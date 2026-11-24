"""Blocklist matching and sensitive-text checks used before every screen capture and action."""
from __future__ import annotations

import re

_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def is_blocked(title: str, app: str, blocklist: list[str]) -> bool:
    hay = f"{title} {app}".lower()
    for entry in blocklist:
        e = entry.strip().lower()
        if not e:
            continue
        if len(e) <= 4:  # short words must match whole words (e.g. "amex", "bank")
            if re.search(rf"\b{re.escape(e)}\b", hay):
                return True
        elif e in hay:
            return True
    return False


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def looks_like_secret(text: str) -> bool:
    """Card numbers (checked with the Luhn checksum). Password/code fields are blocked separately."""
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return True
    return False


def risky_keys(keys: str) -> bool:
    k = keys.lower().replace(" ", "")
    return any(x in k for x in ("alt+f4", "cmd+q", "ctrl+alt+del", "ctrl+alt+delete", "cmd+opt+esc", "shift+delete",
                                "ctrl+w", "cmd+w", "cmd+delete", "cmd+backspace", "enter", "return"))
