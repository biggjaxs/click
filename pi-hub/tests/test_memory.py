import asyncio
import json

import pytest

from app.memory import looks_sensitive


def run(c):
    return asyncio.run(c)


def test_sensitive_filter():
    assert looks_sensitive("my card is 4111 1111 1111 1111")
    assert looks_sensitive("my password is hunter2")
    assert looks_sensitive("SSN 123-45-6789")
    assert not looks_sensitive("I like short answers")
    assert not looks_sensitive("my order number is 1234 5678 9012 3456")  # fails Luhn -> not a card


def test_add_duplicate_and_blocked(memory):
    r1 = run(memory.add_fact("User prefers short answers", "preferences", 0.9))
    assert r1["status"] == "saved"
    r2 = run(memory.add_fact("User prefers short answers", "preferences", 0.8))
    assert r2["status"] == "duplicate" and r2["id"] == r1["id"]
    r3 = run(memory.add_fact("password is abc123", "other"))
    assert r3["status"] == "blocked"
    r4 = run(memory.add_fact("User has asthma", "health"))   # health is off by default
    assert r4["status"] == "blocked"
    r5 = run(memory.add_fact("User has asthma", "health", explicit=True))  # "remember that..." overrides
    assert r5["status"] == "saved"


def test_update_keeps_history(memory):
    old = run(memory.add_fact("User works at Acme", "work"))
    new = run(memory.add_fact("User works at Globex", "work", replaces=old["id"]))
    assert new["status"] == "updated"
    active = [f["text"] for f in memory.active_facts()]
    assert "User works at Globex" in active and "User works at Acme" not in active
    assert [h["text"] for h in memory.fact_history(new["id"])] == ["User works at Acme"]


def test_forget_deletes_fact_and_history(memory):
    old = run(memory.add_fact("User's dog is called Rex", "people"))
    new = run(memory.add_fact("User's dog is called Max", "people", replaces=old["id"]))
    removed = run(memory.forget("dog"))
    assert removed
    assert memory.list_facts(include_history=True) == []


def test_recall_core_and_relevant(memory):
    run(memory.add_fact("User's name is Jaxon", "identity", 1.0))
    run(memory.add_fact("User is building a Raspberry Pi assistant called Click", "projects"))
    run(memory.add_fact("User likes pizza", "preferences"))
    res = run(memory.recall("how is my raspberry pi assistant project going", 500))
    assert "User's name is Jaxon" in res["core"]
    assert any("Raspberry Pi" in r["text"] for r in res["relevant"])


def test_extraction_with_fake_model(memory):
    from hub_fakes import FakeBackend

    reply = json.dumps({"facts": [
        {"fact": "User runs a lawn care business", "category": "work", "confidence": 0.9, "replaces": None},
        {"fact": "User's card is 4111111111111111", "category": "money", "confidence": 0.9},
        {"fact": "maybe likes jazz", "category": "preferences", "confidence": 0.3},
    ]})
    backend = FakeBackend([[{"type": "token", "text": reply}]])
    saved = run(memory.extract_from_exchange(backend, "I run a lawn care business", "Nice!", 1))
    assert [s["text"] for s in saved] == ["User runs a lawn care business"]


def test_export_and_wipe(memory):
    run(memory.add_fact("User likes tea", "preferences"))
    data = memory.export_all()
    assert data["facts"][0]["text"] == "User likes tea"
    memory.wipe_all()
    assert memory.active_facts() == []


def test_database_is_encrypted(tmp_path):
    from app.db import Database, DatabaseLockedError

    d = Database(tmp_path / "x.db", "ef" * 32)
    d.execute("INSERT INTO settings(key, value) VALUES ('secret', '\"top secret words\"')")
    d.close()
    raw = (tmp_path / "x.db").read_bytes() + (tmp_path / "x.db-wal").read_bytes() if (tmp_path / "x.db-wal").exists() else (tmp_path / "x.db").read_bytes()
    assert b"top secret words" not in raw
    with pytest.raises(DatabaseLockedError):
        Database(tmp_path / "x.db", "00" * 32)
