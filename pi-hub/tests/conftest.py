import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hub_fakes import FakeBackend, fake_embed  # noqa: E402,F401

HUB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HUB))
sys.path.insert(0, str(HUB.parent / "shared"))


@pytest.fixture
def db(tmp_path):
    from app.db import Database

    d = Database(tmp_path / "t.db", "ab" * 32)
    yield d
    d.close()


@pytest.fixture
def memory(db):
    from app.audit import AuditLog
    from app.memory import Memory

    return Memory(db, AuditLog(db), fake_embed)


@pytest.fixture
def services(tmp_path, monkeypatch):
    monkeypatch.setenv("CLICK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CLICK_DB_KEY", "cd" * 32)
    from app.config import load_config
    from app.main import Services

    s = Services(load_config())
    s.memory.embedder = fake_embed
    s.orchestrator.pre_read = False  # tested on its own (it adds a background AI call after each answer)
    yield s
    s.db.close()
