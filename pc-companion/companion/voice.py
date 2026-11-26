"""Talking with Clicky, all on this computer.

- Clicky speaks with the Mac's own offline voice (`say`).
- You speak: the microphone is recorded to a short file (sounddevice), and whisper.cpp (an offline speech-to-text engine
  bundled inside Click) turns it into text. Nothing is sent anywhere. The speech model (about 140 MB) is
  downloaded once, the first time you use voice.
Recording stops by itself when you stop talking (or after 20 seconds).
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger("click.voice")

MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin"
MODEL_NAME = "ggml-base.en.bin"
MODEL_MIN_BYTES = 100_000_000

_say: subprocess.Popen | None = None
_say_lock = threading.Lock()


# ---- Clicky speaking --------------------------------------------------------------------------------------------
def speak(text: str, wait: bool = False, rate: int = 190) -> None:
    """Say something out loud (replacing whatever Clicky was saying)."""
    global _say
    import re

    plain = re.sub(r"[*_`#>\[\]()]", "", text or "").strip()[:900]
    if not plain or sys.platform != "darwin":
        return
    with _say_lock:
        stop_speaking()
        try:
            _say = subprocess.Popen(["say", "-r", str(rate), plain], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001
            return
    if wait:
        try:
            _say.wait(timeout=60)
        except Exception:  # noqa: BLE001
            pass


def stop_speaking() -> None:
    global _say
    if _say and _say.poll() is None:
        try:
            _say.terminate()
        except Exception:  # noqa: BLE001
            pass
    _say = None


def speaking() -> bool:
    return bool(_say and _say.poll() is None)


# ---- the speech engine ----------------------------------------------------------------------------------------
def engine_path() -> Path | None:
    """whisper-cli bundled inside Click.app (or on PATH while developing)."""
    cands = []
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        cands += [base / "bin" / "whisper-cli", Path(sys.executable).parent.parent / "Resources" / "bin" / "whisper-cli"]
    cands += [Path(p) / "whisper-cli" for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    for c in cands:
        if c.exists() and os.access(c, os.X_OK):
            return c
    return None


class Voice:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.model = self.folder / MODEL_NAME
        self.recording = False
        self._recorder = None

    def model_ready(self) -> bool:
        return self.model.exists() and self.model.stat().st_size > MODEL_MIN_BYTES

    def available(self) -> tuple[bool, str]:
        if sys.platform != "darwin":
            return False, "Voice is Mac-only for now."
        if not engine_path():
            return False, "The speech engine isn't included in this build."
        return True, ""

    def download_model(self, progress=lambda pct: None) -> None:
        """Get the speech model once (about 140 MB)."""
        import httpx

        tmp = self.model.with_suffix(".part")
        with httpx.stream("GET", MODEL_URL, follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            got = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    got += len(chunk)
                    if total:
                        progress(int(got * 100 / total))
        if tmp.stat().st_size < MODEL_MIN_BYTES:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("the speech model download was incomplete")
        tmp.replace(self.model)

    # ---- listening -----------------------------------------------------------------------------------------
    def listen(self, max_seconds: float = 20.0, silence: float = 1.3, on_level=lambda lvl: None) -> str:
        """Record from the microphone until the user stops talking, then turn it into text (all local)."""
        wav = Path(tempfile.mkstemp(suffix=".wav")[1])
        try:
            self._record(wav, max_seconds, silence, on_level)
            return self.transcribe(wav)
        finally:
            wav.unlink(missing_ok=True)

    def stop(self) -> None:
        self.recording = False

    def _record(self, wav: Path, max_seconds: float, silence: float, on_level) -> None:
        """Record 16 kHz mono from the microphone until they stop talking (sounddevice / PortAudio, local)."""
        import queue
        import wave

        import numpy as np
        import sounddevice as sd

        rate, chunks, q = 16000, [], queue.Queue()

        def callback(indata, frames, t, status):
            q.put(indata.copy())

        self.recording = True
        start = last_voice = time.time()
        heard = False
        try:
            with sd.InputStream(samplerate=rate, channels=1, dtype="int16", blocksize=1600, callback=callback):
                while self.recording and time.time() - start < max_seconds:
                    try:
                        block = q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    chunks.append(block)
                    rms = float(np.sqrt(np.mean(block.astype(np.float32) ** 2))) / 32768.0
                    db = 20 * np.log10(max(rms, 1e-6))  # about -60 (quiet) .. 0 (loud)
                    on_level(max(0.0, min(1.0, (db + 55) / 45)))
                    if db > -38:
                        heard, last_voice = True, time.time()
                    elif heard and time.time() - last_voice > silence:
                        break  # they stopped talking
                    elif not heard and time.time() - start > 7:
                        break  # nothing said
        except sd.PortAudioError as exc:
            raise RuntimeError("couldn't use the microphone (is Click allowed? System Settings > Privacy & Security "
                               f"> Microphone): {exc}") from exc
        finally:
            self.recording = False
        audio = np.concatenate(chunks) if chunks else np.zeros((0, 1), dtype="int16")
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(audio.tobytes())

    def transcribe(self, wav: Path) -> str:
        exe = engine_path()
        if not exe:
            raise RuntimeError("the speech engine isn't included in this build")
        if not self.model_ready():
            raise RuntimeError("the speech model isn't downloaded yet")
        threads = str(max(2, (os.cpu_count() or 4) // 2))
        r = subprocess.run([str(exe), "-m", str(self.model), "-f", str(wav), "-nt", "-np", "-l", "en", "-t", threads],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"speech engine error: {r.stderr.strip()[-200:]}")
        return clean_transcript(r.stdout)


def clean_transcript(out: str) -> str:
    """whisper prints lines like '[BLANK_AUDIO]' or ' Hello there.'; keep just the words."""
    import re

    text = " ".join(line.strip() for line in out.splitlines() if line.strip())
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text)  # [BLANK_AUDIO], (music), ...
    return " ".join(text.split()).strip()


def lesson_command(text: str) -> str | None:
    """'next' / 'repeat' / 'back' / 'stop' if that's what they said during a lesson, else None (a question)."""
    import re

    t = re.sub(r"[^a-z ]", "", text.lower()).strip()
    t = re.sub(r"^(?:ok(?:ay)?|alright|um+|uh+|clicky|hey clicky)\s+", "", t).strip()
    if re.fullmatch(r"(?:next|next step|skip|skip it|done|got it|i did it|continue|go on|keep going|move on|"
                    r"ok next|whats next|im done|finished)(?: please)?", t):
        return "next"
    if re.fullmatch(r"(?:repeat|repeat that|again|say that again|say it again|what|come again|one more time|"
                    r"show me again|what did you say)(?: please)?", t):
        return "repeat"
    if re.fullmatch(r"(?:back|go back|previous|previous step|last step|undo)(?: please)?", t):
        return "back"
    if re.fullmatch(r"(?:stop|cancel|quit|end|stop the lesson|thats enough|never mind|nevermind|exit)(?: please)?", t):
        return "stop"
    return None
