"""First-run setup, with progress, so nobody needs the Terminal:

1. Make sure Ollama (the engine that runs AI models) is installed and running; install it if not.
2. Download Click's AI model and its small memory model, showing progress.
3. Load the model so the first question is fast.
4. (Mac) Check the permissions Click needs and offer buttons to open the right settings.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from PySide6.QtCore import QObject, QThread, Signal, Slot

log = logging.getLogger("click.setup")
EMBED_MODEL = "all-minilm"
MIN_OLLAMA = (0, 12, 7)  # qwen3-vl needs at least this version


class SetupWorker(QObject):
    step = Signal(str, str, str)          # key, text, state
    progress = Signal(object, str)        # fraction or None, detail
    finished = Signal(bool, str)          # ok, message

    def __init__(self, ollama_url: str, models: list[str]):
        super().__init__()
        self.url = ollama_url.rstrip("/")
        self.models = models

    # ---- helpers ------------------------------------------------------------------------------------------
    def _up(self) -> str | None:
        try:
            return httpx.get(f"{self.url}/api/version", timeout=2).json().get("version")
        except Exception:  # noqa: BLE001
            return None

    def _wait_up(self, seconds: int = 60) -> str | None:
        for _ in range(seconds * 2):
            v = self._up()
            if v:
                return v
            time.sleep(0.5)
        return None

    def _download(self, url: str, dest: Path, label: str) -> None:
        with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            done = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        self.progress.emit(done / total, f"{label}: {done / 1e6:.0f} of {total / 1e6:.0f} MB")

    def _install_ollama(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        if sys.platform == "darwin":
            zip_path = tmp / "Ollama-darwin.zip"
            self._download("https://ollama.com/download/Ollama-darwin.zip", zip_path, "Downloading Ollama")
            self.progress.emit(None, "Installing Ollama…")
            target = Path("/Applications")
            if not target.exists() or not os.access(target, os.W_OK):
                target = Path.home() / "Applications"
                target.mkdir(exist_ok=True)
            subprocess.run(["ditto", "-x", "-k", str(zip_path), str(target)], check=True)
            subprocess.run(["open", "-ga", str(target / "Ollama.app")], check=False)
        elif sys.platform == "win32":
            exe = tmp / "OllamaSetup.exe"
            self._download("https://ollama.com/download/OllamaSetup.exe", exe, "Downloading Ollama")
            self.progress.emit(None, "Installing Ollama…")
            subprocess.run([str(exe), "/SILENT"], check=True)
        else:
            raise RuntimeError("Install Ollama from https://ollama.com, then reopen Click.")

    def _start_ollama(self) -> None:
        if sys.platform == "darwin":
            for app in (Path("/Applications/Ollama.app"), Path.home() / "Applications" / "Ollama.app"):
                if app.exists():
                    subprocess.run(["open", "-ga", str(app)], check=False)
                    return
        exe = shutil.which("ollama")
        if exe:
            subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _installed(self) -> set[str]:
        try:
            tags = httpx.get(f"{self.url}/api/tags", timeout=5).json().get("models", [])
            names = set()
            for m in tags:
                names.add(m["name"])
                names.add(m["name"].removesuffix(":latest"))
            return names
        except Exception:  # noqa: BLE001
            return set()

    def _pull(self, model: str, label: str) -> None:
        with httpx.stream("POST", f"{self.url}/api/pull", json={"model": model, "stream": True}, timeout=None) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.strip():
                    continue
                ev = json.loads(line)
                if ev.get("error"):
                    raise RuntimeError(ev["error"])
                total, done = ev.get("total"), ev.get("completed")
                if total and done:
                    self.progress.emit(done / total, f"{label}: {done / 1e9:.2f} of {total / 1e9:.2f} GB")
                else:
                    self.progress.emit(None, f"{label}: {ev.get('status', '')}")

    # ---- the run ----------------------------------------------------------------------------------------------
    def run(self) -> None:
        try:
            self.step.emit("engine", "AI engine (Ollama)", "active")
            version = self._up()
            if not version:
                self._start_ollama()
                version = self._wait_up(15)
            if not version:
                self._install_ollama()
                version = self._wait_up(90)
            if not version:
                raise RuntimeError("Ollama didn't start. Open the Ollama app once, then reopen Click.")
            parts = tuple(int(x) for x in version.split("-")[0].split(".")[:3] if x.isdigit())
            if parts and parts < MIN_OLLAMA:
                raise RuntimeError(f"Ollama {version} is too old. Open Ollama and let it update, then reopen Click.")
            self.step.emit("engine", "AI engine (Ollama)", "done")
            installed = self._installed()
            for i, model in enumerate(self.models + [EMBED_MODEL]):
                key = f"model{i}"
                label = "Memory model" if model == EMBED_MODEL else f"Click's brain ({model})"
                if model in installed:
                    self.step.emit(key, label, "done")
                    continue
                self.step.emit(key, label, "active")
                self._pull(model, "Downloading " + ("memory model" if model == EMBED_MODEL else "Click's brain"))
                self.step.emit(key, label, "done")
            self.step.emit("warm", "Getting ready", "active")
            self.progress.emit(None, "Loading the model into memory…")
            try:
                httpx.post(f"{self.url}/api/generate", json={"model": self.models[0], "keep_alive": "60m"}, timeout=300)
            except Exception:  # noqa: BLE001
                pass
            self.step.emit("warm", "Getting ready", "done")
            self.progress.emit(1.0, "")
            self.finished.emit(True, "")
        except Exception as exc:  # noqa: BLE001
            log.exception("setup failed")
            self.progress.emit(0.0, "")
            self.finished.emit(False, str(exc))


class _Relay(QObject):
    """Lives on the GUI thread, so the 'finished' callback runs there (touching windows from the
    download thread crashes the web view)."""

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    @Slot(bool, str)
    def call(self, ok: bool, message: str):
        self.fn(ok, message)


def run_setup(view, ollama_url: str, models: list[str], on_done) -> QThread:
    """Run SetupWorker in a background thread, updating the SetupView."""
    thread = QThread()
    worker = SetupWorker(ollama_url, models)
    worker.moveToThread(thread)
    relay = _Relay(on_done)
    worker.step.connect(view.set_step)
    worker.progress.connect(view.set_progress)
    worker.finished.connect(relay.call)
    worker.finished.connect(thread.quit)
    thread._relay = relay
    thread.started.connect(worker.run)
    thread._worker = worker  # keep a reference
    thread.start()
    return thread


def mac_permission_rows(platform) -> list[tuple[str, str, bool, object]]:
    """Status of the three macOS permissions Click uses, each with a button to open its settings."""
    if sys.platform != "darwin":
        return []

    def opener(anchor):
        return lambda: subprocess.run(["open", f"x-apple.systempreferences:com.apple.preference.security?{anchor}"])

    problems = " ".join(platform.check_permissions())
    return [
        ("Screen Recording", "So Click can see your screen when you ask.",
         "Screen Recording" not in problems, opener("Privacy_ScreenCapture")),
        ("Accessibility", "So Click's cursor can find buttons and click when you approve.",
         "Accessibility" not in problems, opener("Privacy_Accessibility")),
        ("Input Monitoring", "For the Ctrl+Alt+Esc stop key.",
         _input_monitoring_ok(), opener("Privacy_ListenEvent")),
    ]


def _input_monitoring_ok() -> bool:
    try:
        import ctypes

        iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
        iokit.IOHIDCheckAccess.restype = ctypes.c_int
        return iokit.IOHIDCheckAccess(1) == 0  # kIOHIDRequestTypeListenEvent -> granted
    except Exception:  # noqa: BLE001
        return True
