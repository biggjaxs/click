"""Screen memory (off unless you turn it on): once a minute, a short note of what's in front of you (app,
window title, a little of the visible text) goes to Click's encrypted memory on this computer, so you can ask
"what was that website about flights I looked at Tuesday?". Blocked apps (banks, password managers...) and
anything while paused are skipped. Notes older than 30 days are deleted."""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("click.screenmem")


class ScreenMemory:
    def __init__(self, app, every: float = 60.0):
        self.app = app
        self.every = every
        self.last = ("", "", 0.0)
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, name="click-screenmem", daemon=True).start()

    def note(self) -> dict | None:
        """One note of what's in front, or None if it shouldn't (or needn't) be noted."""
        app = self.app
        if app.paused or not app.settings.get("screen_memory", False):
            return None
        w = app.platform.active_window()
        if not w or w.app in ("Click", "Python", "loginwindow", "ScreenSaverEngine"):
            return None
        from .safety import is_blocked

        if is_blocked(w.title, w.app, app.settings["blocklist"]):
            return None
        same = (w.app, w.title) == self.last[:2]
        if same and time.time() - self.last[2] < 300:
            return None  # nothing changed: don't keep re-noting the same page
        text = ""
        try:
            from .ocr import lines_to_text, recognize

            img, _rect, _blocked, _ = app.screen.capture_image("active_window", None)
            text = lines_to_text(recognize(img, fast=True))[:1500]
        except Exception:  # noqa: BLE001 - title alone is still useful
            pass
        self.last = (w.app, w.title, time.time())
        return {"app": w.app, "title": w.title, "text": text, "at": time.time()}

    def _loop(self) -> None:
        time.sleep(30)
        while not self._stop.is_set():
            try:
                n = self.note()
                if n:
                    self.app.server.send_event_threadsafe("system", "seen", n)
            except Exception:  # noqa: BLE001
                log.debug("screen memory note failed", exc_info=True)
            self._stop.wait(self.every)
