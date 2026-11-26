"""Build check: start Click the way a person does (not the quick self-test) and make sure the chat really works.

It starts Click on top of the data left by earlier runs (like an update), waits until the companion brain
and every background helper are running, and keeps checking that:
  - the chat part (hub) answers,
  - the hub is connected to the computer part (otherwise the header says "Starting..." and nothing answers),
  - a question gets an answer.
Prints a timeline and the end of Click's log, and exits 1 if anything is wrong.

    python realstart.py --app dist/Click.app/Contents/MacOS/Click --home "$HOME/realstart" --version 1.23.1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx


def settings_dir(home: Path) -> Path:
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "ClickCompanion"
    return home / ".config" / "ClickCompanion"


async def ask(url: str, text: str, wait: float = 40) -> str:
    import websockets

    got = []
    async with websockets.connect(url.replace("http://", "ws://") + "/ws", open_timeout=10) as w:
        await w.send(json.dumps({"type": "send", "text": text, "brief": True}))
        end = time.time() + wait
        while time.time() < end:
            try:
                m = json.loads(await asyncio.wait_for(w.recv(), max(0.1, end - time.time())))
            except asyncio.TimeoutError:
                break
            if m.get("type") == "token":
                got.append(m.get("text", ""))
            elif m.get("type") in ("done", "error"):
                if m.get("type") == "error":
                    got.append(f"[error: {m}]")
                break
    return "".join(got)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True, help="command to start Click (split on spaces)")
    ap.add_argument("--home", required=True)
    ap.add_argument("--version", default="")
    ap.add_argument("--seconds", type=int, default=110)
    ap.add_argument("--ollama", default="http://127.0.0.1:11577")
    a = ap.parse_args()

    home = Path(a.home)
    sd = settings_dir(home)
    sd.mkdir(parents=True, exist_ok=True)
    sp = sd / "settings.json"
    s = json.loads(sp.read_text()) if sp.exists() else {}
    s.update({"setup_done": True, "onboarded": True, "ollama_url": a.ollama, "companion": True})
    if a.version:
        s["welcome_version"] = a.version  # straight to the normal start (the welcome is shown on first runs)
    sp.write_text(json.dumps(s))
    logf = sd / "companion.log"
    start_size = logf.stat().st_size if logf.exists() else 0

    env = dict(os.environ, HOME=str(home), CLICK_CI_REALSTART="1")
    out = open(home / "realstart.out", "ab")
    p = subprocess.Popen(a.app.split(), env=env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    t0 = time.time()
    ok, url, problems, asked = True, None, [], set()

    def log_text() -> str:
        try:
            return logf.read_bytes()[start_size:].decode(errors="replace")
        except OSError:
            return ""

    try:
        while time.time() - t0 < a.seconds:
            time.sleep(5)
            t = int(time.time() - t0)
            if p.poll() is not None:
                problems.append(f"Click quit by itself (exit code {p.returncode}) after {t} s")
                break
            m = re.findall(r"Embedded Click hub (?:running|NOT running) at (http://[\d.:]+)", log_text())
            url = m[-1] if m else url
            if not url:
                print(f"{t:4d}s  hub not started yet")
                continue
            try:
                st = httpx.get(url + "/api/status", timeout=5).json()
                line = f"hub OK, connected to computer part: {st['pc']['connected']}"
            except Exception as exc:  # noqa: BLE001
                st, line = None, f"HUB NOT ANSWERING ({exc.__class__.__name__}: {exc})"
            print(f"{t:4d}s  {url}  {line}", flush=True)
            for when in (30, a.seconds - 15):
                if t >= when and when not in asked and st:
                    asked.add(when)
                    try:
                        reply = asyncio.run(ask(url, "what is the capital of france"))
                    except Exception as exc:  # noqa: BLE001
                        reply = f"[{exc.__class__.__name__}: {exc}]"
                    print(f"{t:4d}s  asked a question -> {reply.strip()[:120]!r}", flush=True)
                    if "Paris" not in reply:
                        problems.append(f"no answer at {t} s: {reply[:200]!r}")
        if not url:
            problems.append("the chat part never started")
        else:
            try:
                st = httpx.get(url + "/api/status", timeout=5).json()
                if not st["pc"]["connected"]:
                    problems.append("at the end the chat part was NOT connected to the computer part")
                page = httpx.get(url + "/?tile=1", timeout=5)
                if page.status_code != 200:
                    problems.append(f"chat page returned {page.status_code}")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"at the end the chat part didn't answer: {exc}")
    finally:
        try:
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(timeout=10)
        except Exception:  # noqa: BLE001
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except Exception:  # noqa: BLE001
                pass
    if sys.platform == "darwin":
        left = subprocess.run(["pgrep", "-f", "Click.app/Contents/MacOS/Click"], capture_output=True, text=True).stdout.split()
        if left:
            problems.append(f"other Click copies still running after the test: {left}")
    print("---- end of Click's log ----")
    lines = [ln for ln in log_text().splitlines() if "zeroconf" not in ln and "No route to host" not in ln
             and "uvicorn.access" not in ln and "httpx: HTTP Request" not in ln]
    print("\n".join(lines[-150:]))
    if problems:
        for name in ("crash.log", "crash-previous.log"):
            f = sd / name
            if f.exists() and f.stat().st_size:
                print(f"---- {name} ----")
                print(f.read_text(errors="replace")[-12000:])
        for folder in (Path.home() / "Library/Logs/DiagnosticReports", home / "Library/Logs/DiagnosticReports"):
            reports = sorted(folder.glob("Click*"), key=lambda q: q.stat().st_mtime)[-1:] if folder.exists() else []
            for r in reports:
                if r.stat().st_mtime < t0:
                    continue
                txt = r.read_text(errors="replace")
                print(f"---- macOS crash report {r.name} ----")
                try:  # .ips: a JSON header line, then JSON with the crashed thread's frames
                    body = json.loads(txt.split("\n", 1)[1])
                    print("exception:", body.get("exception"), "termination:", body.get("termination"))
                    print("asi:", body.get("asi"))
                    imgs = body.get("usedImages", [])
                    for th in body.get("threads", []):
                        if th.get("triggered"):
                            for fr in th.get("frames", [])[:40]:
                                img = imgs[fr.get("imageIndex", 0)].get("name", "?") if imgs else "?"
                                print(f"   {img:28s} {fr.get('symbol', '?')}")
                except Exception:  # noqa: BLE001
                    print(txt[:6000])
        ok = False
        for pr in problems:
            print("PROBLEM:", pr)
    print("REALSTART OK" if ok else "REALSTART FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
