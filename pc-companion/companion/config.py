"""Companion settings, stored as JSON in the user's app-data folder.

Windows: %APPDATA%\\ClickCompanion   macOS: ~/Library/Application Support/ClickCompanion
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_shared = ROOT.parent / "shared"
if _shared.exists() and str(_shared) not in sys.path:
    sys.path.insert(0, str(_shared))
_bundled = ROOT / "shared"  # when the companion folder is copied on its own
if _bundled.exists() and str(_bundled) not in sys.path:
    sys.path.insert(0, str(_bundled))


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "ClickCompanion"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _default_folders() -> list[str]:
    home = Path.home()
    return [str(home / n) for n in ("Documents", "Downloads", "Desktop", "Pictures") if (home / n).exists()]


DEFAULT_BLOCKLIST = [
    # password managers
    "1Password", "Bitwarden", "LastPass", "KeePass", "KeePassXC", "Dashlane", "Keeper", "Keychain Access",
    "Passwords", "NordPass", "Proton Pass", "RoboForm", "Enpass",
    # banking and payments (matched against window titles, which include browser tab titles)
    "bank", "banking", "credit union", "paypal", "venmo", "cash app", "zelle", "stripe dashboard",
    "chase", "wells fargo", "citibank", "capital one", "american express", "amex", "barclays", "hsbc",
    "checkout", "payment", "billing", "wallet", "coinbase", "binance", "kraken", "robinhood", "fidelity",
    "schwab", "vanguard", "turbotax", "irs.gov",
]

DEFAULTS = {
    "pc_id": None,
    "pc_name": socket.gethostname()[:60],
    "port": 47321,
    "pi_host": "click.local",
    "pi_cert_pem": None,
    "permissions": {  # local re-check: the PC refuses even if the Pi asks
        "screen": True, "cursor": True, "actions": True, "files": True, "file_write": True, "image": True,
    },
    "approved_folders": None,
    "blocklist": DEFAULT_BLOCKLIST,
    "kill_hotkey": "<ctrl>+<alt>+<esc>",
    "models": {"chat": None, "vision": None},   # None = pick automatically from the hardware tier
    "image": {"enabled": None, "draft_model": None, "quality_model": None, "safety_filter": True},
    "cursor": {"color": "#6d5dfc", "style": "arrow", "label": "Clicky"},
    "ollama_url": "http://127.0.0.1:11434",
    "think": False,
}


class Settings:
    def __init__(self, path: Path | None = None):
        self.dir = config_dir()
        self.path = path or self.dir / "settings.json"
        self._lock = threading.RLock()
        data = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
            except json.JSONDecodeError:
                data = {}
        self.data = {**DEFAULTS, **data}
        for key in ("permissions", "image", "cursor", "models"):
            self.data[key] = {**DEFAULTS[key], **(data.get(key) or {})}
        if not self.data["pc_id"]:
            self.data["pc_id"] = uuid.uuid4().hex
        if self.data["approved_folders"] is None:
            self.data["approved_folders"] = _default_folders()
        self.save()

    def __getitem__(self, key):
        with self._lock:
            return self.data[key]

    def get(self, key, default=None):
        with self._lock:
            return self.data.get(key, default)

    def set(self, key, value) -> None:
        with self._lock:
            self.data[key] = value
            self.save()

    def update(self, key, **values) -> None:
        with self._lock:
            self.data[key] = {**self.data.get(key, {}), **values}
            self.save()

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2))
            os.replace(tmp, self.path)
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    @property
    def tls_dir(self) -> Path:
        d = self.dir / "tls"
        d.mkdir(exist_ok=True)
        return d
