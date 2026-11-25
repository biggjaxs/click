"""Encrypted SQLite database (SQLCipher) with a small schema-migration system.

All memory, chats, settings and the audit log live here. On the Pi the key comes from
/etc/click/db.key (derived from the passphrase chosen at install) via a systemd credential.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Iterable

try:  # SQLCipher (encrypted). On the Pi this is built by the installer.
    from sqlcipher3 import dbapi2 as sqlite

    ENCRYPTION_AVAILABLE = True
except ImportError:  # pragma: no cover - only used for local development
    import sqlite3 as sqlite

    ENCRYPTION_AVAILABLE = False


class DatabaseLockedError(RuntimeError):
    pass


SCHEMA = [
    # v1
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL DEFAULT 'New chat',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        last_episode_msg_id INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY,
        conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        meta TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
    CREATE TABLE IF NOT EXISTS facts (
        id INTEGER PRIMARY KEY,
        text TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'other',
        confidence REAL NOT NULL DEFAULT 0.7,
        source_type TEXT NOT NULL DEFAULT 'chat',
        source_ref TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        superseded_by INTEGER REFERENCES facts(id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS episodes (
        id INTEGER PRIMARY KEY,
        conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
        summary TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS embeddings (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,          -- fact | episode | message | file
        ref_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        vector BLOB NOT NULL,
        created_at REAL NOT NULL,
        UNIQUE(kind, ref_id)
    );
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY,
        at REAL NOT NULL,
        category TEXT NOT NULL,      -- screen | file | memory | input | web | image | pc | permission
        action TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS images (
        id INTEGER PRIMARY KEY,
        conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
        filename TEXT NOT NULL,
        prompt TEXT NOT NULL,
        negative_prompt TEXT NOT NULL DEFAULT '',
        params TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS image_jobs (
        id INTEGER PRIMARY KEY,
        conversation_id INTEGER,
        params TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',   -- queued | running | done | failed | cancelled
        error TEXT,
        image_id INTEGER,
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS uploads (
        id INTEGER PRIMARY KEY,
        filename TEXT NOT NULL,
        stored_name TEXT NOT NULL,
        mime TEXT NOT NULL,
        text_content TEXT,
        created_at REAL NOT NULL
    );
    """,
    # v2: screen memory ("what was that website I looked at Tuesday?"), only when the user turns it on
    """
    CREATE TABLE IF NOT EXISTS screen_history (
        id INTEGER PRIMARY KEY,
        app TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        text TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL
    );
    """,
]


class Database:
    """Thread-safe wrapper. FastAPI runs handlers in threads and the event loop."""

    def __init__(self, path: Path, key: str | None, allow_unencrypted: bool = False):
        self.path = path
        self.encrypted = ENCRYPTION_AVAILABLE and bool(key)
        if not self.encrypted and not allow_unencrypted:
            raise DatabaseLockedError(
                "Memory encryption is not set up (no key or SQLCipher missing). "
                "Run scripts/install-pi.sh again."
            )
        self._lock = threading.RLock()
        self.conn = sqlite.connect(str(path), check_same_thread=False, isolation_level=None)
        if self.encrypted:
            if not all(c in "0123456789abcdefABCDEF" for c in key) or len(key) != 64:
                raise DatabaseLockedError("Database key must be 64 hex characters.")
            self.conn.execute(f"PRAGMA key = \"x'{key}'\"")
        try:
            self.conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except Exception as exc:  # wrong key gives "file is not a database"
            raise DatabaseLockedError("Could not unlock the memory database (wrong key?).") from exc
        self.conn.row_factory = sqlite.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def _migrate(self) -> None:
        with self._lock:
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            for i, script in enumerate(SCHEMA[version:], start=version + 1):
                self.conn.executescript(script)
                self.conn.execute(f"PRAGMA user_version = {i}")

    # -- helpers --------------------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            cur = self.conn.execute(sql, tuple(params))
            return cur.lastrowid

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # -- settings ---------------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def close(self) -> None:
        with self._lock:
            self.conn.close()


def now() -> float:
    return time.time()
