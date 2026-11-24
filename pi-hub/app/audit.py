"""Audit log: every screenshot, file read, memory write, input action, web search and PC request."""
from __future__ import annotations

import time

from .db import Database


class AuditLog:
    def __init__(self, db: Database):
        self.db = db
        self.listeners: list = []  # callables(entry) used to push live updates to the UI

    def log(self, category: str, action: str, detail: str = "") -> dict:
        detail = detail[:2000]
        at = time.time()
        entry_id = self.db.execute(
            "INSERT INTO audit_log(at, category, action, detail) VALUES (?, ?, ?, ?)",
            (at, category, action, detail),
        )
        entry = {"id": entry_id, "at": at, "category": category, "action": action, "detail": detail}
        for listener in list(self.listeners):
            try:
                listener(entry)
            except Exception:
                pass
        return entry

    def recent(self, limit: int = 200, category: str | None = None) -> list[dict]:
        if category:
            return self.db.query(
                "SELECT * FROM audit_log WHERE category = ? ORDER BY id DESC LIMIT ?", (category, limit)
            )
        return self.db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))

    def export(self) -> list[dict]:
        return self.db.query("SELECT * FROM audit_log ORDER BY id")
