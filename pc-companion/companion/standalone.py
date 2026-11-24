"""Run Click entirely on this computer (no Raspberry Pi needed).

The "box" part (chat app, memory, brain) normally lives on the Pi. In standalone mode it runs inside
this app on 127.0.0.1 only, and pairs itself with this computer's Companion automatically.
Memory is encrypted with a key kept in the macOS Keychain (or a private file on other systems).
"""
from __future__ import annotations

import logging
import os
import secrets
import subprocess
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
    """64-hex-character key for the encrypted memory database."""
    if sys.platform == "darwin":
        service, account = "Click memory key", "click"
        r = subprocess.run(["security", "find-generic-password", "-s", service, "-a", account, "-w"],
                           capture_output=True, text=True)
        if r.returncode == 0 and len(r.stdout.strip()) == 64:
            return r.stdout.strip()
        key = secrets.token_hex(32)
        subprocess.run(["security", "add-generic-password", "-s", service, "-a", account, "-w", key, "-U"],
                       capture_output=True, check=False)
        return key
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
        import uvicorn

        from app.main import app  # noqa: E402 - the hub package, found via sys.path above

        config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", proxy_headers=False)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="click-hub", daemon=True)
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)
        log.info("Embedded Click hub running at %s", self.url)

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
