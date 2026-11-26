"""Clicky's companion brain: always quietly watching what's in front of you, learning how you use your computer,
and stepping in when it can actually help.

What it notices (every few seconds): only the app in front and its window title (never keystrokes, never
screenshots - screen memory is a separate opt-in). That's enough to learn which apps and projects you use and
when, and to judge moments where help is welcome:
  * a long video or an article you've been on for a while -> "Want a quick summary?"
  * 90 minutes of non-stop work -> "Stretch break?"
  * battery low, a meeting in 10 minutes, disk nearly full
  * drifting to distractions for 20+ minutes -> "Want a focus timer?"
  * the first time you sit down in the morning -> "Want your briefing?"
How it stays polite: one offer at a time, at least 8 minutes apart, nothing but focus reminders during focus
time, and it learns from you: offers you ignore or turn down get rarer and then stop. A minute-by-minute
summary (app + title) goes to Click's encrypted memory so the chat knows you better over time.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
import threading
import time

log = logging.getLogger("click.brain")

BROWSERS = ("Safari", "Google Chrome", "Arc", "Brave Browser", "Microsoft Edge", "Firefox", "Opera", "Chromium", "Vivaldi")
NEVER_SUMMARIZE = re.compile(
    r"tiktok|instagram|facebook|\bx\b|twitter|reddit|snapchat|threads|messages|imessage|whatsapp|messenger|slack|"
    r"discord|telegram|mail|gmail|outlook|inbox|google search|bing|duckduckgo|search results|login|log in|sign in|"
    r"checkout|cart|bank|password|\bshorts\b|#shorts|new tab|start page|favorites", re.I)
READING = re.compile(
    r"wikipedia|medium|substack|news|times|post|guardian|bbc|cnn|npr|reuters|bloomberg|forbes|verge|wired|"
    r"atlantic|vox|axios|blog|article|docs|documentation|guide|tutorial|how to|review|explained|history of|"
    r"magazine|journal|research|paper|arxiv|stack overflow|learn", re.I)


def judge_content(app: str, title: str) -> str | None:
    """Is this worth offering a summary for? 'video', 'article' or None.
    Long YouTube videos and real articles: yes. Shorts, social feeds, messages, email, search, shopping: no."""
    if app not in BROWSERS or not title:
        return None
    t = title.strip()
    if NEVER_SUMMARIZE.search(t):
        return None
    if re.search(r"\s[-–|]\s*YouTube$", t) and len(re.sub(r"\s[-–|]\s*YouTube$", "", t).split()) >= 3:
        return "video"
    if re.search(r"vimeo|ted\.com|\bted\b|lecture|course|webinar", t, re.I):
        return "video"
    words = len(re.split(r"\s+", re.split(r"\s[-–|•]\s", t)[0]))
    if words >= 5 and (READING.search(t) or re.search(r"\s[-–|•]\s\S", t)):
        return "article"
    return None


def battery() -> tuple[int, bool] | None:
    """(percent, charging) on a laptop, or None."""
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r"(\d+)%;\s*(\w+)", out)
    if not m:
        return None
    return int(m.group(1)), m.group(2).lower() in ("charging", "charged", "finishing")


def idle_seconds() -> float:
    """How long since the last mouse/keyboard input (no permission needed)."""
    try:
        import Quartz

        return float(Quartz.CGEventSourceSecondsSinceLastEventType(Quartz.kCGEventSourceStateCombinedSessionState,
                                                                    Quartz.kCGAnyInputEventType))
    except Exception:  # noqa: BLE001
        return 0.0


def free_disk_gb() -> float:
    import shutil

    try:
        return shutil.disk_usage("/").free / 1e9
    except OSError:
        return 999.0


class Brain:
    GAP = 15 * 60  # at least this long between offers (it should never feel like a bother)

    def __init__(self, app, tick: float = 4.0):
        self.app = app
        self.tick = tick
        self._stop = threading.Event()
        self.front = ("", "")
        self.since = time.time()          # on this app+title since
        self.minute: dict[tuple[str, str], float] = {}
        self.minute_start = time.time()
        self.active_since = time.time()   # continuous use without a 5-minute break
        self.distracted: list[tuple[float, float]] = []  # (when, seconds) on distractions
        self.offered_titles: set[str] = set()
        self.last_offer = 0.0
        self.cooldown: dict[str, float] = {}
        self.pending: dict | None = None
        self.day_seen = ""
        self.last_calendar = 0.0
        self.last_inventory = 0.0

    # ---- learning from you -----------------------------------------------------------------------------------
    def _stats(self) -> dict:
        return dict(self.app.settings.get("suggest_stats") or {})

    def muted(self, kind: str) -> bool:
        st = self._stats().get(kind, {})
        return st.get("no", 0) >= 3 and st.get("yes", 0) == 0 or st.get("muted", False)

    def feedback(self, kind: str, accepted: bool, mute: bool = False) -> None:
        stats = self._stats()
        st = dict(stats.get(kind, {}))
        st["yes" if accepted else "no"] = st.get("yes" if accepted else "no", 0) + 1
        if mute:
            st["muted"] = True
        stats[kind] = st
        self.app.settings.set("suggest_stats", stats)

    def accepted(self, offer: dict) -> None:
        offer["answered"] = True
        self.feedback(offer["kind"], True)
        if self.pending is offer:
            self.pending = None

    def answer_pending(self, text: str) -> str | None:
        """'no thanks' / 'not now' / 'stop suggesting that' right after an offer."""
        p = self.pending
        if not p or time.time() - p["at"] > 180:
            return None
        t = text.lower().strip(" .!")
        if re.fullmatch(r"(?:no|nope|nah|no thanks|no thank you|not now|maybe later|later|skip|i'?m good|all good)", t):
            self.feedback(p["kind"], False)
            p["answered"] = True
            self.pending = None
            return "No problem."
        if re.search(r"(?:stop|don'?t|never)\s+(?:suggesting|asking|offering|reminding)|stop\s+asking", t):
            self.feedback(p["kind"], False, mute=True)
            p["answered"] = True
            self.pending = None
            return "Got it, I won't suggest that again. (You can turn suggestions off in the menu bar menu.)"
        return None

    # ---- the loop ----------------------------------------------------------------------------------------------
    def start(self) -> None:
        threading.Thread(target=self._loop, name="click-brain", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        time.sleep(20)
        while not self._stop.is_set():
            try:
                self.step(time.time())
            except Exception:  # noqa: BLE001 - a helper; never break anything
                log.debug("brain step failed", exc_info=True)
            self._stop.wait(self.tick)

    def step(self, now: float) -> dict | None:
        """One look. Returns the offer it made (for tests), if any."""
        app = self.app
        if not getattr(self, "_began", False):  # the clocks start when the brain first looks, not at launch
            self._began = True
            self.active_since = self.since = self.minute_start = now
        if app.paused or not app.settings.get("companion", True):
            return None
        w = app.platform.active_window()
        name, title = (w.app or "", w.title or "") if w else ("", "")
        if name in ("Click", "Python", "loginwindow", "ScreenSaverEngine"):
            return None
        from .safety import is_blocked

        if is_blocked(title, name, app.settings["blocklist"]):
            title = ""  # learn the app, never a blocked window's title
        idle = idle_seconds()
        if (name, title) != self.front:
            self.front, self.since = (name, title), now
        if idle < 120:
            key = (name, title[:120])
            self.minute[key] = self.minute.get(key, 0.0) + self.tick
        if idle > 300:
            self.active_since = now  # a real break resets the clock
        from .focus import distracting

        if distracting(name, title) and idle < 120:
            self.distracted.append((now, self.tick))
        self.distracted = [(t, s) for t, s in self.distracted if now - t < 1800]
        if now - self.minute_start >= 60:
            self._send_minute(now)
        if now - self.last_inventory > 6 * 3600:
            self.last_inventory = now
            threading.Thread(target=self._send_inventory, daemon=True).start()
        if idle > 120:
            return None  # they're away: no offers
        return self._maybe_offer(now, name, title, w)

    def _send_minute(self, now: float) -> None:
        items = sorted(self.minute.items(), key=lambda kv: -kv[1])[:6]
        self.minute, self.minute_start = {}, now
        if items:
            self.app.server.send_event_threadsafe("system", "activity", {
                "at": now, "items": [{"app": a, "title": t, "seconds": round(s)} for (a, t), s in items]})

    def _send_inventory(self) -> None:
        try:
            from .skills import installed_apps

            apps = sorted({p.rsplit("/", 1)[-1].removesuffix(".app") for p in installed_apps().values()})
            self.app.server.send_event_threadsafe("system", "inventory", {"apps": apps[:400]})
        except Exception:  # noqa: BLE001
            pass

    # ---- deciding when to help ----------------------------------------------------------------------------------
    def _maybe_offer(self, now: float, name: str, title: str, w) -> dict | None:
        level = self.app.settings.get("suggestions", "normal")
        if level == "off" or now - self.last_offer < (self.GAP * (2 if level == "low" else 1)):
            return None
        focus = getattr(self.app, "focus", None)
        if focus is not None and focus.active:
            return None  # focus time: the focus guard does the talking
        dwell = now - self.since
        hour = time.localtime(now).tm_hour
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        cands = []
        kind = judge_content(name, title)
        if kind and title not in self.offered_titles and dwell >= (90 if kind == "video" else 150):
            what = "video" if kind == "video" else "article"
            cands.append(("summary", f"Summarize this {what}",
                          f"Summarize what I'm {'watching' if kind == 'video' else 'reading'}: {title[:120]}",
                          f"Want a quick summary of this {what}?"))
        if now - self.active_since > 90 * 60:
            cands.append(("break", "Take a 5-minute break", "remind me to take a break in 5 minutes",
                          "You've been going for 90 minutes. Stretch break?"))
        b = battery() if sys.platform == "darwin" else None
        if b and b[0] <= 15 and not b[1]:
            cands.append(("battery", "Got it", "ok", f"Battery's at {b[0]}%. Plug in soon?"))
        drift = sum(s for _, s in self.distracted)
        if drift > 20 * 60 and 8 <= hour < 22:
            cands.append(("focus", "Focus for 25 minutes", "help me focus for 25 minutes",
                          "Lots of scrolling lately. Want a 25-minute focus timer?"))
        if today != self.day_seen and 5 <= hour < 12:
            self.day_seen = today
            cands.append(("briefing", "Good morning briefing", "good morning", "Good morning! Want your briefing?"))
        if free_disk_gb() < 10:
            cands.append(("disk", "Show me how to free up space", "show me how to free up disk space on my mac",
                          "Your disk is nearly full. Want me to show you how to free up space?"))
        if sys.platform == "darwin" and now - self.last_calendar > 300:
            self.last_calendar = now
            ev = self._meeting_soon()
            if ev:
                cands.append(("meeting", "Thanks", "ok", f"“{ev}” starts in about 10 minutes."))
        for kind_, label, question, say in cands:
            if self.muted(kind_) or now - self.cooldown.get(kind_, 0) < self._cool(kind_):
                continue
            self.cooldown[kind_] = now
            self.last_offer = now
            if kind_ == "summary":
                self.offered_titles.add(title)
            if self.pending and not self.pending.get("answered"):
                self.feedback(self.pending["kind"], False)  # the last offer was ignored: a little less of those
            offer = {"kind": kind_, "label": label, "question": question, "say": say, "at": now,
                     "rect": w.rect.as_list() if w else None, "info": kind_ in ("battery", "meeting")}
            self.pending = offer
            self.app.bridge.companionOffer.emit(offer)
            return offer
        return None

    @staticmethod
    def _cool(kind: str) -> float:
        return {"summary": 600, "break": 3600, "battery": 1800, "focus": 3600, "briefing": 20 * 3600,
                "disk": 24 * 3600, "meeting": 900}.get(kind, 1800)

    def _meeting_soon(self) -> str | None:
        """A calendar event starting in 5-15 minutes (only if Calendar is already running)."""
        script = """
if application "Calendar" is not running then return ""
set s to (current date) + 5 * minutes
set e to (current date) + 15 * minutes
tell application "Calendar"
    repeat with c in calendars
        try
            set evs to (every event of c whose start date >= s and start date <= e)
            if evs is not {} then return summary of item 1 of evs
        end try
    end repeat
end tell
return \"\""""
        try:
            r = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True, timeout=15)
            return r.stdout.strip() or None
        except Exception:  # noqa: BLE001
            return None
