"""Focus guard: "help me focus for 45 minutes".

Every few seconds it notes which app you're in (nothing is recorded but the app name and how long). If you
drift to something distracting, Clicky gently nudges you (at most every 2 minutes). At the end you get a
short summary of where the time went. Say "stop focus" to end early.
"""
from __future__ import annotations

import re
import threading
import time

DISTRACTING = re.compile(r"youtube|tiktok|instagram|facebook|twitter|\bx\.com|reddit|netflix|hulu|disney\+|twitch|"
                         r"prime video|steam|epic games|roblox|minecraft|fortnite|snapchat|pinterest|tumblr|9gag|"
                         r"buzzfeed|espn|news\b", re.I)


class Focus:
    def __init__(self, app):
        self.app = app
        self.until = 0.0
        self.started = 0.0
        self.time_in: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread = None
        self.last_nudge = 0.0

    @property
    def active(self) -> bool:
        return time.time() < self.until

    def minutes_left(self) -> int:
        return max(0, int((self.until - time.time() + 59) // 60))

    def start(self, minutes: int) -> str:
        self.stop(quiet=True)
        minutes = max(5, min(240, int(minutes or 25)))
        self.started, self.until = time.time(), time.time() + minutes * 60
        self.time_in, self.last_nudge = {}, 0.0
        self._stop = threading.Event()
        self.app.ghost.flags["focus_until"] = self.until
        self._thread = threading.Thread(target=self._loop, name="click-focus", daemon=True)
        self._thread.start()
        return f"Focus time started: {minutes} minutes. I'll nudge you if you wander off."

    def stop(self, quiet: bool = False) -> str:
        was = self.active or bool(self.time_in)
        self.until = 0.0
        self._stop.set()
        self.app.ghost.flags["focus_until"] = 0.0
        summary = self.summary()
        if was and not quiet:
            self.app.notify(summary)
            self.app.bridge.narrate.emit(summary)
        return summary

    def summary(self) -> str:
        total = sum(self.time_in.values())
        if total < 30:
            return "Focus ended."
        top = sorted(self.time_in.items(), key=lambda kv: -kv[1])[:4]
        parts = ", ".join(f"{name} {int(sec // 60)} min" for name, sec in top if sec >= 30)
        drift = sum(sec for name, sec in self.time_in.items() if DISTRACTING.search(name))
        tail = " No distractions, great job!" if drift < 60 else f" ({int(drift // 60)} min on distractions.)"
        return f"Focus done: {int(total // 60)} minutes. Time went to {parts}.{tail}"

    def _loop(self) -> None:
        last = time.time()
        while not self._stop.is_set() and self.active:
            self._stop.wait(5)
            now = time.time()
            try:
                w = self.app.platform.active_window()
            except Exception:  # noqa: BLE001
                w = None
            name = (w.app or "?") if w else "?"
            title = w.title if w else ""
            label = f"{name} - {title.split(' - ')[0][:40]}" if DISTRACTING.search(title) else name
            self.time_in[label] = self.time_in.get(label, 0.0) + (now - last)
            last = now
            if (DISTRACTING.search(f"{name} {title}") and now - self.last_nudge > 120 and not self.app.paused):
                self.last_nudge = now
                x, y = (w.rect.x + min(w.rect.w / 2, 260), w.rect.y + 18) if w else self.app.ghost.primary_center()
                self.app.bridge.ghostMove.emit(float(x), float(y),
                                               f"Focus time: {self.minutes_left()} min left. Back to it?")
        if not self._stop.is_set():  # the time ran out (not stopped by hand)
            self.stop()
