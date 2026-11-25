"""Run Click entirely on this computer (no Raspberry Pi needed).

The "box" part (chat app, memory, brain) normally lives on the Pi. In standalone mode it runs inside
this app on 127.0.0.1 only, and pairs itself with this computer's Companion automatically.
Memory is encrypted with a key kept in a private file in the app's settings folder.
"""
from __future__ import annotations

import logging
import os
import secrets
import sys
import threading
import time
from pathlib import Path

import httpx

log = logging.getLogger("click.standalone")

PORT = 47390


def hub_dir() -> Path:
    if getattr(sys, "frozen", False):  # inside the built Click.app
        return Path(sys._MEIPASS) / "pi-hub"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2] / "pi-hub"


def _memory_key(config_dir: Path) -> str:
    """64-hex-character key for the encrypted memory database, in a file only you can read.

    (The macOS Keychain was tried first, but it can freeze waiting for an unlock prompt.)
    """
    path = config_dir / "memory.key"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path.read_text().strip()


class EmbeddedHub:
    def __init__(self, settings, chat_model: str):
        self.settings = settings
        self.chat_model = chat_model
        self.data_dir = settings.dir / "Hub"
        self.server = None
        self.thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{PORT}"

    def start(self) -> None:
        hub = hub_dir()
        for path in (hub, hub.parent / "shared"):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        os.environ["CLICK_DATA_DIR"] = str(self.data_dir)
        os.environ["CLICK_OLLAMA_URL"] = self.settings["ollama_url"]
        os.environ["CLICK_PC_HOST"] = "127.0.0.1"
        # Same machine: if the link ever drops, the "backup" is simply the same model.
        os.environ["CLICK_FALLBACK_MODEL"] = self.chat_model
        try:
            import sqlcipher3  # noqa: F401

            os.environ["CLICK_DB_KEY"] = _memory_key(self.settings.dir)
        except ImportError:
            log.warning("SQLCipher not available; memory is stored unencrypted (development build)")
            os.environ["CLICK_ALLOW_UNENCRYPTED"] = "1"
        from app.main import app  # noqa: E402 - the hub package, found via sys.path above

        self._app = app
        self._serve()

    def _serve(self) -> None:
        import uvicorn

        # log_config=None: hub errors go to Click's own log file instead of a hidden console
        config = uvicorn.Config(self._app, host="127.0.0.1", port=PORT, log_level="info", log_config=None,
                                proxy_headers=False)
        self.server = uvicorn.Server(config)

        def run():
            try:
                self.server.run()
            except BaseException:  # noqa: BLE001
                log.exception("Click's chat part stopped")

        self.thread = threading.Thread(target=run, name="click-hub", daemon=True)
        self.thread.start()
        for _ in range(600):  # slow Macs (first launch is scanned by macOS) can take a while
            if self.server.started or not self.thread.is_alive():
                break
            time.sleep(0.05)
        log.info("Embedded Click hub %s at %s", "running" if self.server.started else "NOT running", self.url)

    def alive(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def restart(self) -> None:
        log.warning("Restarting Click's chat part")
        self._serve()

    def ensure_paired(self, pc_cert: str, pc_id: str, pc_name: str, port: int) -> str | None:
        """Pair the embedded hub with this computer's Companion. Returns the hub certificate to trust."""
        try:
            status = httpx.get(f"{self.url}/api/status", timeout=5).json()
            if status["pc"]["paired"] and self.settings.get("standalone_paired"):
                return None
            code = httpx.post(f"{self.url}/api/pair/start", timeout=5).json()["code"]
            r = httpx.post(f"{self.url}/api/pair/complete", timeout=5, json={
                "code": code, "pc_id": pc_id, "name": pc_name, "port": port, "cert_pem": pc_cert})
            r.raise_for_status()
            self.settings.set("standalone_paired", True)
            return r.json()["pi_cert_pem"]
        except Exception as exc:  # noqa: BLE001
            log.error("Automatic pairing failed: %s", exc)
            return None

    def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
