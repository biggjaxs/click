"""Focus guard: "help me focus for 45 minutes".

Every few seconds it notes which app you're in (nothing is recorded but the app name and how long). If you
drift to something distracting, Clicky gently nudges you (at most every 2 minutes). At the end you get a
short summary of where the time went. Say "stop focus" to end early.
"""
from __future__ import annotations

import re
import threading
import time

DISTRACTING = re.compile(
    # social media
    r"tiktok|instagram|facebook|messenger\.com|twitter|\bx\.com|\b(?:on|\|) x\b|reddit|snapchat|threads|bluesky|tumblr|"
    r"pinterest|linkedin|discord|telegram|whatsapp web|bereal|9gag|imgur|"
    # video and streaming
    r"youtube|netflix|hulu|disney\+|prime video|max\b|hbo|twitch|kick\.com|crunchyroll|peacock|paramount\+|"
    # games
    r"steam|epic games|roblox|minecraft|fortnite|valorant|league of legends|battle\.net|overwatch|call of duty|"
    r"genshin|chess\.com|lichess|candy crush|"
    # time sinks
    r"buzzfeed|espn|bleacher report|amazon\.com.*(?:deal|cart)|ebay|shein|temu", re.I)
NOT_DISTRACTING = re.compile(r"podcast|audiobook|audible|\bbooks\b|lecture|course|tutorial|lofi|study music|"
                             r"focus music|white noise|rain sounds", re.I)


def distracting(app: str, title: str) -> bool:
    """Social media, video, games... but never podcasts, audiobooks or study music."""
    text = f"{app} {title}"
    if app in ("Podcasts", "Audible", "Books", "Spotify", "Music") or NOT_DISTRACTING.search(text):
        return False
    return bool(DISTRACTING.search(text))


class Focus:
    def __init__(self, app):
        self.app = app
        self.until = 0.0
        self.started = 0.0
        self.time_in: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread = None
        self.last_nudge = 0.0
        self.was_bad = False

    @property
    def active(self) -> bool:
        return time.time() < self.until

    def minutes_left(self) -> int:
        return max(0, int((self.until - time.time() + 59) // 60))

    def start(self, minutes: int) -> str:
        self.stop(quiet=True)
        minutes = max(5, min(240, int(minutes or 25)))
        self.started, self.until = time.time(), time.time() + minutes * 60
        self.time_in, self.last_nudge, self.was_bad = {}, 0.0, False
        self._stop = threading.Event()
        self.app.ghost.flags["focus_until"] = self.until
        self._thread = threading.Thread(target=self._loop, name="click-focus", daemon=True)
        self._thread.start()
        return f"Focus time started: {minutes} minutes. I'll nudge you if you wander off."

    def extend(self, minutes: int) -> str:
        if not self.active:
            return self.start(minutes)
        self.until += max(1, int(minutes)) * 60
        self.app.ghost.flags["focus_until"] = self.until
        return f"Added {int(minutes)} minutes: {self.minutes_left()} minutes of focus left."

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
        drift = sum(sec for name, sec in self.time_in.items() if distracting(name.split(" - ")[0], name))
        tail = " No distractions, great job!" if drift < 60 else f" ({int(drift // 60)} min on distractions.)"
        return f"Focus done: {int(total // 60)} minutes. Time went to {parts}.{tail}"

    def _loop(self) -> None:
        last = time.time()
        while not self._stop.is_set() and self.active:
            self._stop.wait(3)
            now = time.time()
            try:
                w = self.app.platform.active_window()
            except Exception:  # noqa: BLE001
                w = None
            self.observe(w, now - last, now)
            last = now
        if not self._stop.is_set():  # the time ran out (not stopped by hand)
            self.stop()

    def observe(self, w, seconds: float, now: float) -> str | None:
        """One look at the app in front: count the time, and nudge if it's a distraction.
        Returns "switched" (just went to a distraction), "still" (still on it) or None."""
        name = (w.app or "?") if w else "?"
        title = w.title if w else ""
        bad = distracting(name, title)
        label = f"{name} - {title.split(' - ')[0][:40]}" if (bad and DISTRACTING.search(title)) else name
        self.time_in[label] = self.time_in.get(label, 0.0) + seconds
        switched_in = bad and not self.was_bad  # just opened (or came back to) a distraction
        self.was_bad = bad
        if not bad or self.app.paused or not (switched_in or now - self.last_nudge > 45):
            return None
        self.last_nudge = now
        x, y = (w.rect.x + min(w.rect.w / 2, 260), w.rect.y + 18) if w else self.app.ghost.primary_center()
        what = (title.split(" - ")[0][:30] or name) if title else name
        self.app.bridge.ghostMove.emit(float(x), float(y), f"Focus time: {self.minutes_left()} min left. {what} can wait!")
        if switched_in:
            self.app.notify(f"Focus time: {self.minutes_left()} minutes left. Back to it?")
            if self.app.settings.get("focus_voice", True):
                from .voice import speak

                speak(f"Focus time. {self.minutes_left()} minutes left. Back to it!")
        return "switched" if switched_in else "still"
