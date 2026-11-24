"""Pi Hub configuration: reads config/click.yaml, then environment variables override it."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

APP_DIR = Path(__file__).resolve().parent
HUB_DIR = APP_DIR.parent
REPO_DIR = HUB_DIR.parent
CONFIG_DIR = HUB_DIR / "config"

# Make the shared protocol package importable.
_shared = REPO_DIR / "shared"
if str(_shared) not in sys.path:
    sys.path.insert(0, str(_shared))


@dataclass
class Config:
    data_dir: Path = Path("/var/lib/click")
    assistant_name: str = "Click"
    fallback_model: str = "gemma3:1b"
    embed_model: str = "all-minilm"
    ollama_url: str = "http://127.0.0.1:11434"
    history_token_budget: int = 3000
    memory_token_budget: int = 700
    max_tool_rounds: int = 6
    request_timeout_s: int = 120
    image_timeout_s: int = 900
    companion_port: int = 47321
    manual_pc_host: str = ""
    db_key: str | None = None  # hex key for SQLCipher, loaded from systemd credential
    allow_unencrypted: bool = False
    system_prompt_path: Path = CONFIG_DIR / "system_prompt.md"
    extra: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "click.db"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def tls_dir(self) -> Path:
        return self.data_dir / "tls"

    @property
    def screens_dir(self) -> Path:
        return self.data_dir / "screens"


def _read_db_key() -> str | None:
    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if cred_dir and (Path(cred_dir) / "dbkey").exists():
        return (Path(cred_dir) / "dbkey").read_text().strip()
    return os.environ.get("CLICK_DB_KEY") or None


def load_config() -> Config:
    cfg = Config()
    yaml_path = CONFIG_DIR / "click.yaml"
    if yaml_path.exists():
        data = yaml.safe_load(yaml_path.read_text()) or {}
        for key, value in data.items():
            if hasattr(cfg, key) and value is not None:
                current = getattr(cfg, key)
                setattr(cfg, key, Path(value) if isinstance(current, Path) else value)
            else:
                cfg.extra[key] = value
    env_map = {
        "CLICK_DATA_DIR": ("data_dir", Path),
        "CLICK_FALLBACK_MODEL": ("fallback_model", str),
        "CLICK_EMBED_MODEL": ("embed_model", str),
        "CLICK_OLLAMA_URL": ("ollama_url", str),
        "CLICK_PC_HOST": ("manual_pc_host", str),
    }
    for env, (attr, typ) in env_map.items():
        if os.environ.get(env):
            setattr(cfg, attr, typ(os.environ[env]))
    cfg.allow_unencrypted = os.environ.get("CLICK_ALLOW_UNENCRYPTED") == "1"
    cfg.db_key = _read_db_key()
    for d in (cfg.data_dir, cfg.images_dir, cfg.uploads_dir, cfg.tls_dir, cfg.screens_dir):
        d.mkdir(parents=True, exist_ok=True)
        try:
            d.chmod(0o700)
        except PermissionError:
            pass
    # The personality file lives with your data (the code folder is read-only for the service).
    # It starts as a copy of config/system_prompt.md and is edited in Settings > Personality.
    personal = cfg.data_dir / "system_prompt.md"
    if not personal.exists() and cfg.system_prompt_path.exists():
        personal.write_text(cfg.system_prompt_path.read_text())
    cfg.system_prompt_path = personal
    return cfg
