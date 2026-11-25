"""Rescue mode: when an error or a confusing pop-up appears, Clicky offers to help.

A light check every few seconds looks at the window in front. If it's a pop-up that looks like trouble
("couldn't", "failed", "not responding", "are you sure you want to delete"...), Clicky appears beside it and
offers to explain it and point at the right button. It offers once per pop-up, never reads password dialogs,
respects the blocklist and pause, and can be turned off in the menu bar menu.
"""
from __future__ import annotations

import logging
import re
import threading
import time

log = logging.getLogger("click.rescue")

TROUBLE = re.compile(
    r"\b(error|failed|failure|couldn['’]?t|can['’]?t|cannot|unable|not responding|quit unexpectedly|unexpectedly|"
    r"problem|denied|not allowed|not permitted|damaged|not supported|unsupported|warning|invalid|unavailable|"
    r"incompatible|expired|crash\w*|try again|isn['’]?t|wasn['’]?t|required|insufficient|not enough|disk is full|"
    r"are you sure|permanently|can['’]?t be undone|cannot be undone|erase|malware|unidentified developer|"
    r"verify|blocked|lost|disconnected|offline|out of date|update)\b", re.I)


def needs_help(d: dict) -> bool:
    return bool(d and TROUBLE.search(f"{d.get('title', '')} {d.get('text', '')}"))


def question_for(d: dict) -> str:
    """What the Ask box sends when you accept help (the chat side recognises this exact shape)."""
    msg = " ".join(f"{d.get('title', '')} {d.get('text', '')}".split())[:700].replace('"', "'")
    return (f'Help me with this pop-up from {d.get("app") or "an app"}: "{msg}". '
            f'Its buttons: {" | ".join(d.get("buttons") or [])}. What does it mean and which button should I press?')


class Watcher:
    def __init__(self, app, interval: float = 4.0):
        self.app = app
        self.interval = interval
        self.seen: set[str] = set()
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, name="click-rescue", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def check_once(self) -> dict | None:
        app = self.app
        if app.paused or not app.settings.get("rescue_mode", True):
            return None
        d = app.platform.front_dialog()
        if not d or not needs_help(d) or d.get("app") in ("Click", "Python"):
            return None
        from .safety import is_blocked

        if is_blocked(d.get("title", ""), d.get("app", ""), app.settings["blocklist"]):
            return None
        key = f"{d.get('app')}|{d.get('title')}|{d.get('text', '')[:200]}"
        if key in self.seen:
            return None
        self.seen.add(key)
        return d

    def _loop(self) -> None:
        time.sleep(15)  # let Click finish starting
        while not self._stop.is_set():
            try:
                d = self.check_once()
                if d:
                    self.app.bridge.rescueOffer.emit(d)
            except Exception:  # noqa: BLE001 - a helper; never let it break anything
                log.debug("rescue check failed", exc_info=True)
            self._stop.wait(self.interval)
