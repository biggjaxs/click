"""Click Engine on this computer: Click's own AI engine (see engine/ in the repo), as a background helper.

Off by default for now (beta): Menu bar > "Use Click Engine (beta)". When it's on, Click starts the engine
quietly, downloads its model once (about 1.9 GB), and sends quick answers to it; the smart model and pictures
still use Ollama until Click Engine can run them. If anything goes wrong, Click quietly uses Ollama again.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

log = logging.getLogger("click.engine")
PORT = 11435
ENGINE_MODELS = {"qwen2.5:1.5b", "qwen2.5:0.5b", "qwen2.5:3b"}  # what Click Engine can run today


def command(port: int, models_dir: Path) -> list[str]:
    tail = ["--port", str(port), "--models", str(models_dir), "--parent", str(os.getpid())]
    if getattr(sys, "frozen", False):  # inside Click.app: the same program, started in engine mode
        return [sys.executable, "--engine-server", *tail]
    return [sys.executable, "-m", "click_engine.server", *tail]


class EngineManager:
    def __init__(self, settings, config_dir: Path):
        self.settings = settings
        self.models_dir = Path(config_dir) / "models"
        self.proc: subprocess.Popen | None = None
        self.ready_models: set[str] = set()
        self.url = f"http://127.0.0.1:{PORT}"
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("click_engine", False))

    def serves(self, model: str) -> bool:
        """True if this request should go to Click Engine (on, running, and it has this model)."""
        m = (model or "").removesuffix(":latest")
        return self.enabled and m in self.ready_models and self.proc is not None and self.proc.poll() is None

    def start(self, status=lambda text: None) -> None:
        """Start the engine (and get its model the first time). Runs in the background."""
        threading.Thread(target=self._start, args=(status,), name="click-engine", daemon=True).start()

    def _start(self, status) -> None:
        with self._lock:
            if self.proc is None or self.proc.poll() is not None:
                self.models_dir.mkdir(parents=True, exist_ok=True)
                env = dict(os.environ)
                if not getattr(sys, "frozen", False):
                    repo = Path(__file__).resolve().parents[2] / "engine"
                    env["PYTHONPATH"] = str(repo) + os.pathsep + env.get("PYTHONPATH", "")
                log.info("starting Click Engine")
                self.proc = subprocess.Popen(command(PORT, self.models_dir), env=env, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL, start_new_session=True)
            for _ in range(100):
                try:
                    httpx.get(self.url + "/api/version", timeout=1)
                    break
                except Exception:  # noqa: BLE001
                    time.sleep(0.2)
            else:
                log.warning("Click Engine didn't start")
                return
        model = "qwen2.5:1.5b"
        if model not in self.models():
            status("Getting Click Engine's model (about 1.9 GB, once)…")
            try:
                with httpx.stream("POST", self.url + "/api/pull", json={"model": model},
                                  timeout=httpx.Timeout(3600, connect=5)) as r:
                    last = -10
                    for line in r.iter_lines():
                        import json as _j

                        d = _j.loads(line or "{}")
                        if d.get("total") and d.get("completed"):
                            pct = int(d["completed"] * 100 / d["total"])
                            if pct >= last + 10:
                                last = pct
                                status(f"Getting Click Engine's model… {pct}%")
                        if d.get("error"):
                            status(f"Click Engine: {d['error']}")
                            return
            except Exception as exc:  # noqa: BLE001
                status(f"Click Engine couldn't get its model: {exc}")
                return
        self.ready_models = self.models() & ENGINE_MODELS
        try:  # load it now so the first answer is quick
            httpx.post(self.url + "/api/generate", json={"model": model}, timeout=120)
        except Exception:  # noqa: BLE001
            pass
        if self.ready_models:
            status("Click Engine is on: quick answers now run on Click's own engine.")

    def models(self) -> set[str]:
        try:
            return {m["name"] for m in httpx.get(self.url + "/api/tags", timeout=3).json().get("models", [])}
        except Exception:  # noqa: BLE001
            return set()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None
        self.ready_models = set()


def run_server_from_argv(argv: list[str]) -> int:
    """`Click --engine-server ...`: run Click Engine in this process (used inside Click.app)."""
    from click_engine.server import main as serve_main

    args = [a for a in argv[1:] if a != "--engine-server"]
    serve_main(args)
    return 0
