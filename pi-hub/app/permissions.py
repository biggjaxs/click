"""Permission toggles, risk levels and user confirmations.

Rules (from the build spec):
- Read-only actions (look, point, highlight) are allowed when their feature is on.
- Actions that change things (click, type, file write) need confirmation.
- High-risk actions (send, buy, delete, system settings) need confirmation EVERY time,
  with no "allow for this task" option.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass

from .db import Database

# name -> (default, label, description)
PERMISSIONS: dict[str, tuple[bool, str, str]] = {
    "memory_saving": (True, "Save memories", "Remember facts about you from chats."),
    "web_search": (True, "Web search", "Search the web. Only the search words leave the box."),
    "image_generation": (True, "Image generation", "Create images on your computer's GPU."),
    "screen_reading": (True, "Screen reading", "Look at your screen when you ask (asks first)."),
    "watch_mode": (False, "Watch mode", "Take a snapshot every 30 s to keep context. Shows an indicator."),
    "file_access": (True, "File access", "Read files in approved folders (asks first)."),
    "file_write": (False, "File changes", "Rename or move files. Asks every time."),
    "cursor": (True, "Ghost cursor", "Click's own pointer can point and highlight."),
    "input_actions": (True, "Mouse & keyboard actions", "Real clicks and typing. Asks before each one."),
}

# Which tool needs which permission.
TOOL_PERMISSION = {
    "web_search": "web_search",
    "fetch_web_page": "web_search",
    "generate_image": "image_generation",
    "get_screen": "screen_reading",
    "read_screen_text": "screen_reading",
    "list_windows": "screen_reading",
    "get_active_window": "screen_reading",
    "get_ui_elements": "screen_reading",
    "find_ui_element": "screen_reading",
    "list_folder": "file_access",
    "read_file": "file_access",
    "search_files": "file_access",
    "rename_file": "file_write",
    "move_file": "file_write",
    "ghost_move": "cursor",
    "ghost_highlight": "cursor",
    "ghost_inspect": "cursor",
    "ghost_hide": "cursor",
    "teach_step": "cursor",
    "click": "input_actions",
    "type_text": "input_actions",
    "press_keys": "input_actions",
    "scroll": "input_actions",
    "remember": "memory_saving",
}

# Tools that look at private things and must ask in the chat first (unless allowed for this chat).
ASK_IN_CHAT = {"get_screen", "read_screen_text", "list_windows", "get_active_window",
               "get_ui_elements", "find_ui_element", "list_folder", "read_file", "search_files"}
# Tools that change files: ask in chat every time, no "allow for this chat".
ASK_EVERY_TIME = {"rename_file", "move_file"}
# Real input actions: confirmed on the PC with a popup near the target.
CONFIRM_ON_PC = {"click", "type_text", "press_keys", "scroll"}

HIGH_RISK_WORDS = re.compile(
    r"\b(send|post|publish|buy|purchase|pay|order|checkout|check out|delete|remove|erase|"
    r"uninstall|install|format|transfer|submit|confirm|sign|accept|reset|shutdown|shut down|restart|"
    r"settings|registry|terminal|admin)\b",
    re.I,
)


def risk_level(tool: str, args: dict, target_label: str = "") -> str:
    """Return 'read', 'change' or 'high'."""
    if tool in CONFIRM_ON_PC or tool in ASK_EVERY_TIME:
        text = " ".join(str(v) for v in args.values()) + " " + (target_label or "")
        if tool == "press_keys" and re.search(r"\benter\b|\breturn\b|delete", str(args.get("keys", "")), re.I):
            return "high"
        if HIGH_RISK_WORDS.search(text):
            return "high"
        return "change"
    return "read"


class Permissions:
    def __init__(self, db: Database):
        self.db = db

    def all(self) -> dict[str, dict]:
        saved = self.db.get_setting("permissions", {})
        return {
            name: {"enabled": bool(saved.get(name, default)), "label": label, "description": desc}
            for name, (default, label, desc) in PERMISSIONS.items()
        }

    def enabled(self, name: str) -> bool:
        if self.paused:  # "Pause everything" turns every capability off until resumed
            return False
        saved = self.db.get_setting("permissions", {})
        return bool(saved.get(name, PERMISSIONS[name][0]))

    def set(self, name: str, value: bool) -> None:
        if name not in PERMISSIONS:
            raise KeyError(name)
        saved = self.db.get_setting("permissions", {})
        saved[name] = bool(value)
        self.db.set_setting("permissions", saved)

    @property
    def paused(self) -> bool:
        return bool(self.db.get_setting("paused", False))

    def set_paused(self, value: bool) -> None:
        self.db.set_setting("paused", bool(value))

    def tool_allowed(self, tool: str) -> bool:
        perm = TOOL_PERMISSION.get(tool)
        return True if perm is None else self.enabled(perm)


@dataclass
class PendingConfirm:
    id: str
    future: asyncio.Future


class ConfirmationBroker:
    """Asks the user in the chat UI and waits for Allow once / Allow for this chat / Deny."""

    def __init__(self, timeout_s: float = 120):
        self.timeout_s = timeout_s
        self.pending: dict[str, asyncio.Future] = {}

    async def ask(self, send, title: str, detail: str, allow_task: bool) -> str:
        cid = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        self.pending[cid] = fut
        options = ["once", "task", "deny"] if allow_task else ["once", "deny"]
        await send({"type": "confirm_request", "id": cid, "title": title, "detail": detail, "options": options})
        try:
            decision = await asyncio.wait_for(fut, self.timeout_s)
        except asyncio.TimeoutError:
            decision = "deny"
        finally:
            self.pending.pop(cid, None)
        await send({"type": "confirm_resolved", "id": cid, "decision": decision})
        if decision == "task" and not allow_task:
            decision = "once"
        return decision

    def resolve(self, cid: str, decision: str) -> bool:
        fut = self.pending.get(cid)
        if fut and not fut.done():
            fut.set_result(decision if decision in ("once", "task", "deny") else "deny")
            return True
        return False

    def deny_all(self) -> None:
        for fut in self.pending.values():
            if not fut.done():
                fut.set_result("deny")
