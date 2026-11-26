"""Live lessons: Clicky looks at what is REALLY on the screen at every step and points; you do the clicking.

Two building blocks used by the chat part (which decides what each step is):

snapshot()   What's in front right now: the app, its window (where it is and how big), and every button, menu,
             tab and text box in that window with its exact position. Apps that don't describe their buttons
             to macOS (Blender, games, some web apps) are read with on-screen text recognition instead.
             Text boxes are named by their labels, never by what's typed in them. Password managers, banking
             and other blocklisted apps are never read.

show_step()  Clicky glides to the control, the box around it lights up, and THEN the words appear and are
             spoken, so words and pointer always arrive together. Clicky never clicks: it waits for you to
             click the highlighted thing (or to say next / back / repeat / stop, or ask a question).
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
import time

from .platforms import Element, Rect

log = logging.getLogger("click.tutor")

INTERACTIVE = {"Button", "MenuButton", "MenuItem", "MenuBarItem", "PopUpButton", "CheckBox", "RadioButton", "Tab",
               "TextField", "TextArea", "ComboBox", "SearchField", "Link", "Slider", "Cell", "Row", "DockItem",
               "Incrementor", "ColorWell", "DisclosureTriangle", "SegmentedControl"}
OWN_APPS = {"Click", "Python", "python3"}
MAX_CONTROLS = 70


def area_of(r: Rect, win: Rect) -> str:
    """'top-left', 'left side', 'bottom'... of the window: how a person would describe where it is."""
    if win.w <= 0 or win.h <= 0:
        return ""
    cx, cy = (r.x + r.w / 2 - win.x) / win.w, (r.y + r.h / 2 - win.y) / win.h
    if cy < 0:
        return "menu bar"
    v = "top" if cy < 0.18 else "bottom" if cy > 0.82 else ""
    h = "left" if cx < 0.25 else "right" if cx > 0.75 else ""
    if v and h:
        return f"{v}-{h}"
    if v:
        return v
    if h:
        return f"{h} side"
    return "middle"


def _clean(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip()[:60]


def _rank(el: Element, goal_words: set[str]) -> float:
    """Keep the useful controls when there are too many: buttons and menus, and anything matching the goal."""
    n = el.name.lower()
    s = 2.0 if el.role in ("Button", "MenuBarItem", "MenuItem", "Tab", "PopUpButton", "MenuButton") else \
        1.5 if el.role in INTERACTIVE else 1.0 if el.role == "Text" else 0.6
    if goal_words and any(w in n for w in goal_words):
        s += 2.0
    if len(n) > 40:
        s -= 0.8  # long text is usually content, not a control
    return s


def collect(platform, screen, settings, goal: str = "", use_ocr: bool = True) -> dict:
    """What's on screen right now (see module docs). Blocking: call it in a thread."""
    from .safety import is_blocked

    w = platform.active_window()
    if w is None:
        return {"app": "", "title": "", "window": None, "controls": [], "source": "none",
                "note": "No window is open in front."}
    blocked = is_blocked(w.title, w.app, settings["blocklist"])
    out = {"app": w.app, "title": "" if blocked else w.title[:120], "window": w.rect.as_list(), "controls": [],
           "source": "accessibility"}
    if blocked:
        out["note"] = "This app is private (blocklisted), so Clicky doesn't look inside it."
        return out
    goal_words = {t for t in re.findall(r"[a-z]{3,}", (goal or "").lower())
                  if t not in {"how", "the", "and", "for", "with", "you", "your", "can", "show", "teach", "want",
                               "make", "use", "using", "into", "this", "that", "app", "mac"}}
    els: list[Element] = []
    try:
        els = [e for e in platform.ui_elements(w, 500) if e.name and not e.is_password]
    except Exception:  # noqa: BLE001
        log.debug("ui elements failed", exc_info=True)
    # the app's own menus (File, Edit, View...) and any menu that's open right now
    try:
        for e in platform.app_menus():
            els.append(e)
    except Exception:  # noqa: BLE001
        pass
    win = w.rect
    inside = [e for e in els if e.role in ("MenuBarItem", "MenuItem") or (
        e.rect.x + e.rect.w > win.x and e.rect.x < win.x + win.w and e.rect.y + e.rect.h > win.y
        and e.rect.y < win.y + win.h)]
    useful = [e for e in inside if e.role in INTERACTIVE or e.role in ("MenuBarItem", "MenuItem", "Text", "Image",
                                                                         "Group", "StaticText")]
    if use_ocr and len([e for e in useful if e.role != "MenuBarItem"]) < 8:
        # the app doesn't describe its buttons (Blender, games, some web apps): read the text on its window
        try:
            useful += read_window_text(screen, win)
            out["source"] = "text on screen"
        except Exception as exc:  # noqa: BLE001
            log.info("couldn't read the window's text: %s", exc)
    # one entry per (name, place)
    seen, uniq = set(), []
    for e in useful:
        key = (_clean(e.name).lower(), e.rect.x // 8, e.rect.y // 8)
        if not _clean(e.name) or key in seen:
            continue
        seen.add(key)
        uniq.append(e)
    for e in uniq:
        if e.role == "MenuBarItem":
            e.extra.setdefault("rank_boost", 1.0)  # the app's menus always make the list
    uniq.sort(key=lambda e: -(_rank(e, goal_words) + e.extra.get("rank_boost", 0)))
    uniq = uniq[:MAX_CONTROLS]
    uniq.sort(key=lambda e: (e.role != "MenuBarItem", e.rect.y // 20, e.rect.x))  # reading order
    for e in uniq:
        eid = screen.elements.add(e)
        item = {"id": eid, "name": _clean(e.name), "role": e.role, "rect": e.rect.as_list(),
                "area": "menu bar" if e.role == "MenuBarItem" else area_of(e.rect, win)}
        if e.extra.get("items"):
            item["items"] = [_clean(t) for t in e.extra["items"] if _clean(t)][:30]
        out["controls"].append(item)
    return out


def read_window_text(screen, win: Rect) -> list[Element]:
    """On-screen text inside the window, as pointable elements (for apps without accessibility info)."""
    from .ocr import recognize

    img, rect, _, _ = screen.capture_image(None, win.as_list())
    sx, sy = rect.w / max(1, img.width), rect.h / max(1, img.height)
    out = []
    for ln in recognize(img, fast=True):
        t = _clean(ln["text"])
        if not t or ln.get("conf", 1) < 0.35 or len(t) > 50:
            continue
        r = Rect(int(rect.x + ln["x"] * sx), int(rect.y + ln["y"] * sy), max(6, int(ln["w"] * sx)),
                 max(6, int(ln["h"] * sy)))
        out.append(Element(name=t, role="Text", rect=r, window_title=""))
    return out


def area_rect(area: str, win: Rect) -> Rect:
    """A region of the window to point at when the thing has no label ('left side', 'top'...)."""
    a = (area or "").lower()
    w3, h3 = win.w // 3, win.h // 3
    if "left" in a:
        return Rect(win.x, win.y + h3 // 2, max(60, win.w // 5), h3 * 2)
    if "right" in a:
        return Rect(win.x + win.w - max(60, win.w // 5), win.y + h3 // 2, max(60, win.w // 5), h3 * 2)
    if "top" in a:
        return Rect(win.x + w3 // 2, win.y, w3 * 2, max(40, win.h // 8))
    if "bottom" in a:
        return Rect(win.x + w3 // 2, win.y + win.h - max(40, win.h // 8), w3 * 2, max(40, win.h // 8))
    return Rect(win.x + w3, win.y + h3, w3, h3)


class Tutor:
    """Runs on the computer part; the chat part sends one step at a time."""

    def __init__(self, app):
        self.app = app

    # ---- the lesson's life ------------------------------------------------------------------------------
    def begin(self, title: str) -> None:
        import threading

        app = self.app
        lesson = getattr(app, "lesson", None)
        if lesson is None:
            lesson = app.lesson = {}
        lesson.update(active=True, title=title, total="?", step=0, say="", cmd=None, interrupt=threading.Event())
        app.ghost.hold = True  # stay on screen for the whole lesson

    def end(self, finished: bool) -> None:
        app = self.app
        app.lesson["active"] = False
        app.ghost.hold = False
        app.bridge.ghostHighlight.emit(0, 0, 0, 0, "__clear__")
        msg = "That's it! You did it." if finished else "Okay, lesson stopped."
        app.bridge.narrate.emit(msg)
        if app.settings.get("lesson_voice", True):
            from .voice import speak

            speak(msg)
        app.bridge.ghostRest.emit()

    # ---- one step ---------------------------------------------------------------------------------------
    async def show_step(self, p: dict) -> dict:
        """Point at the control (or area, or app) and wait for the user. Never clicks anything.
        Returns {"result": "clicked" | "app_open" | "next" | "back" | "repeat" | "stop" | "timeout"}."""
        app = self.app
        lesson = app.lesson
        step, say = int(p.get("step") or 1), str(p.get("say") or "").strip()
        lesson.update(step=step, say=say, cmd=None, total=p.get("total") or "?")
        lesson["interrupt"].clear()
        rect = None
        if p.get("element_id"):
            try:
                rect = app.screen.element_rect(p["element_id"])
            except Exception:  # noqa: BLE001 - the screen changed: point at the window instead
                rect = None
        if rect is None and p.get("rect"):
            rect = Rect(*[int(v) for v in p["rect"]])
        if rect is None and p.get("area") and p.get("window"):
            rect = area_rect(p["area"], Rect(*[int(v) for v in p["window"]]))
        want_app = p.get("open_app")
        if want_app and rect is None:
            rect = await asyncio.to_thread(self._dock_icon, want_app)
        if rect is not None:
            x, y = rect.x + rect.w / 2, rect.y + rect.h / 2
        else:
            w = await asyncio.to_thread(app.platform.active_window)
            x, y = (w.rect.x + min(w.rect.w / 2, 240), w.rect.y + 16) if w and w.rect.w > 60 else \
                app.ghost.primary_center()
        caption = f"Step {step}: {say}" if say else ""
        # 1) glide there silently, 2) light up the box, 3) THEN show + say the words (they arrive together)
        app.bridge.ghostMove.emit(float(x), float(y), "")
        dist = ((x - app.ghost.x) ** 2 + (y - app.ghost.y) ** 2) ** 0.5 if app.ghost.visible else 600
        await asyncio.sleep(max(0.4, min(0.8, 0.3 + dist / 2400)))
        if rect is not None:
            app.bridge.ghostHighlight.emit(float(rect.x), float(rect.y), float(rect.w), float(rect.h), "__step__")
        app.bridge.ghostMove.emit(float(x), float(y), caption)
        if app.settings.get("lesson_voice", True) and say:
            from .voice import speak

            speak(say)
        return await self._wait(p, rect, want_app, lesson)

    async def _wait(self, p: dict, rect: Rect | None, want_app: str | None, lesson: dict) -> dict:
        """Wait until the user has done the step, then return at once (no fixed pauses):
        - they clicked the highlighted thing, or
        - the screen changed because of what they did (a menu opened, a window appeared or changed, a
          keyboard shortcut...), or
        - for typing steps: they typed something and paused, or
        - they said next / back / repeat / stop, or the time ran out."""
        app = self.app
        timeout = float(p.get("timeout") or 180)
        kind = p.get("kind") or "click"
        if want_app:  # wait until they've opened the app themselves
            end = time.time() + timeout
            while time.time() < end:
                if lesson["interrupt"].is_set():
                    return {"result": lesson.get("cmd") or "next"}
                w = await asyncio.to_thread(app.platform.active_window)
                if w and want_app.lower() in (w.app or "").lower():
                    await asyncio.sleep(0.5)  # let its window finish appearing
                    return {"result": "app_open"}
                await asyncio.sleep(0.25)
            return {"result": "timeout"}
        if kind == "look":
            words = len(str(p.get("say") or "").split())
            timeout = min(timeout, max(4.0, words / 2.6 + 2.5))  # time to read it, then carry on
        res = await asyncio.to_thread(self._watch, rect, timeout, lesson, kind)
        if res in ("clicked", "changed", "typed"):
            return {"result": "clicked" if res == "clicked" else "next", "how": res}
        if res == "timeout" and kind == "look":
            return {"result": "next", "how": "read"}
        return {"result": lesson.get("cmd") or res}

    def _watch(self, rect: Rect | None, timeout: float, lesson: dict, kind: str) -> str:
        killed = self.app.actions.killed
        plat = self.app.platform
        end = time.time() + timeout
        box = Rect(rect.x - 10, rect.y - 10, rect.w + 20, rect.h + 20) if rect else None
        quartz = None
        if sys.platform == "darwin":
            try:
                import Quartz as quartz  # noqa: N813
            except ImportError:
                quartz = None
        try:
            base = plat.screen_signature()
        except Exception:  # noqa: BLE001
            base = None
        was_down = True
        clicked_at = None
        last_len, last_change = (base[1] if base else 0), time.time()
        typed = False
        while time.time() < end:
            if lesson["interrupt"].is_set() or killed.is_set():
                return "stopped" if killed.is_set() else "command"
            now = time.time()
            if quartz is not None:
                st = quartz.kCGEventSourceStateCombinedSessionState
                down = quartz.CGEventSourceButtonState(st, 0) or quartz.CGEventSourceButtonState(st, 1)
                if down and not was_down:
                    loc = quartz.CGEventGetLocation(quartz.CGEventCreate(None))
                    if box is not None and box.contains(loc.x, loc.y) and kind == "click":
                        clicked_at = now  # clicked it: give the app a moment to react, then move on
                was_down = down
            if clicked_at and now - clicked_at > 0.25:
                return "clicked"
            if base is not None and now - getattr(self, "_last_sig", 0) >= 0.25:
                self._last_sig = now
                try:
                    sig = plat.screen_signature()
                except Exception:  # noqa: BLE001
                    sig = None
                if sig is not None:
                    if kind == "type":
                        if sig[1] != last_len:
                            typed = typed or sig[1] > 0
                            last_len, last_change = sig[1], now
                        elif typed and now - last_change > 1.8:
                            return "typed"  # they typed something and paused
                        if sig[0] != base[0] and typed:
                            return "changed"
                    elif sig[0] != base[0]:
                        time.sleep(0.25)  # let it settle (a menu finishing opening)
                        return "changed"
            time.sleep(0.12)
        return "timeout"

    def _dock_icon(self, name: str) -> Rect | None:
        try:
            for el in self.app.platform.dock_items():
                if el.name.lower() == name.lower() or name.lower() in el.name.lower():
                    return el.rect
        except Exception:  # noqa: BLE001
            pass
        return None
