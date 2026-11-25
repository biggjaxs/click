"""Typing and key presses on macOS that are safe from any thread.

Why this exists: on new macOS versions, keyboard libraries that look up the keyboard layout
(TSMGetInputSourceProperty) from a background thread make macOS kill the whole app instantly. Click's
actions run in a background thread, so it must never do that. Here we post keyboard events directly
with Quartz (CGEvent): text is sent as Unicode characters (no layout lookup), long text is pasted,
and shortcuts use fixed key codes.
"""
from __future__ import annotations

import subprocess
import time

# US/ANSI key codes (the physical key positions macOS uses for shortcuts)
KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13,
    "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
    "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38,
    "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51, "escape": 53, "esc": 53,
    "forwarddelete": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109,
    "f11": 103, "f12": 111,
}
MODS = {"cmd": 1 << 20, "command": 1 << 20, "ctrl": 1 << 18, "control": 1 << 18, "alt": 1 << 19, "option": 1 << 19,
        "opt": 1 << 19, "shift": 1 << 17, "fn": 1 << 23}


def _post(code: int, down: bool, flags: int = 0, text: str | None = None) -> None:
    import Quartz

    ev = Quartz.CGEventCreateKeyboardEvent(None, code, down)
    if flags:
        Quartz.CGEventSetFlags(ev, flags)
    if text is not None:
        Quartz.CGEventKeyboardSetUnicodeString(ev, len(text.encode("utf-16-le")) // 2, text)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def type_unicode(text: str, should_stop=lambda: False) -> int:
    """Type text character by character as Unicode (works for any keyboard layout and emoji)."""
    n = 0
    for line_i, line in enumerate(text.split("\n")):
        if line_i:
            _post(36, True)
            _post(36, False)
            time.sleep(0.01)
        for i in range(0, len(line), 12):
            if should_stop():
                return n
            chunk = line[i:i + 12]
            _post(0, True, text=chunk)
            _post(0, False, text=chunk)
            n += len(chunk)
            time.sleep(0.012)
    return n


def press(keys: str) -> None:
    """'cmd+v', 'cmd+shift+t', 'return', 'esc'..."""
    parts = [p.strip().lower() for p in keys.replace(" ", "").split("+") if p.strip()]
    flags = 0
    key = None
    for p in parts:
        p = p.strip("<>")
        if p in MODS:
            flags |= MODS[p]
        else:
            key = p
    if key is None or key not in KEYCODES:
        raise ValueError(f"Unknown key '{key}'")
    code = KEYCODES[key]
    _post(code, True, flags)
    time.sleep(0.03)
    _post(code, False, flags)


def _clipboard() -> str:
    try:
        return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=3).stdout
    except Exception:  # noqa: BLE001
        return ""


def _set_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text, text=True, timeout=5)


def paste_text(text: str) -> None:
    """Put text in the front app in one go (fast and reliable for paragraphs), then put the user's own
    clipboard back."""
    old = _clipboard()
    _set_clipboard(text)
    time.sleep(0.12)
    press("cmd+v")
    time.sleep(0.6)
    try:
        _set_clipboard(old)
    except Exception:  # noqa: BLE001
        pass


def put_text(text: str, should_stop=lambda: False) -> int:
    """Short text is typed (you see it appear); anything long is pasted."""
    if len(text) > 60:
        paste_text(text)
        return len(text)
    return type_unicode(text, should_stop)
