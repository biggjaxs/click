"""Screen service: screenshots (with blocklisted windows blacked out), OCR, windows and UI elements."""
from __future__ import annotations

import base64
import difflib
import io
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import logging

from PIL import Image, ImageDraw

from .platforms import Element, Platform, Rect, Window
from .safety import is_blocked

MAX_SIDE = 1600


class ScreenError(RuntimeError):
    pass


class ElementCache:
    """Gives UI elements short ids ("e12") so the model can point at them by id."""

    def __init__(self):
        self._lock = threading.Lock()
        self._items: dict[str, Element] = {}
        self._n = 0

    def add(self, el: Element) -> str:
        with self._lock:
            self._n += 1
            eid = f"e{self._n}"
            self._items[eid] = el
            if len(self._items) > 1500:
                for k in list(self._items)[:500]:
                    del self._items[k]
            return eid

    def get(self, eid: str) -> Element | None:
        with self._lock:
            return self._items.get(str(eid))


def monitors() -> list[dict]:
    import mss

    with mss.mss() as sct:
        return [dict(m) for m in sct.monitors]  # [0] is the whole virtual screen


def _grab(rect: Rect) -> Image.Image:
    try:
        import mss

        with mss.mss() as sct:
            shot = sct.grab({"left": rect.x, "top": rect.y, "width": rect.w, "height": rect.h})
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        if sys.platform == "darwin" and img.getbbox() is None:
            raise ScreenError("blank screenshot")  # newer macOS can return an empty image; use screencapture
        return img
    except Exception as exc:  # noqa: BLE001
        if sys.platform == "darwin":  # fallback: macOS built-in screencapture tool
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "shot.png"
                subprocess.run(["screencapture", "-x", "-R", f"{rect.x},{rect.y},{rect.w},{rect.h}", str(out)],
                               check=True, timeout=15)
                return Image.open(out).convert("RGB")
        raise ScreenError(f"Could not take a screenshot: {exc}") from exc


log = logging.getLogger("click.screen")

SYNONYMS = {"system preferences": "system settings", "settings app": "system settings", "wifi": "wi-fi",
            "wi fi": "wi-fi", "control centre": "control center", "trash can": "trash", "bin": "trash",
            "file explorer": "finder", "files": "finder", "app store": "app store", "browser": "safari"}


def _norm(t: str) -> str:
    import re

    t = re.sub(r"[…\.\u2026:]+$", "", (t or "").lower().replace("\u2011", "-").replace("\u2010", "-")).strip()
    t = re.sub(r"\s+", " ", t)
    return SYNONYMS.get(t, t)


def _score(q: str, name: str) -> float:
    if not name:
        return 0.0
    if name == q:
        return 1.0
    if len(name) >= 3 and (q in name or name in q):
        return 0.9 if len(name) - len(q) < 15 else 0.75
    return difflib.SequenceMatcher(None, q, name).ratio()


def _overlaps(a: Rect, b: Rect) -> bool:
    return a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h


def _installed_app(q: str) -> bool:
    import sys as _sys

    if _sys.platform != "darwin":
        return False
    from pathlib import Path as _P

    name = q.title() + ".app"
    return any((_P(d) / name).exists() for d in ("/Applications", "/System/Applications",
                                                   "/System/Applications/Utilities"))


class ScreenService:
    def __init__(self, platform: Platform, settings):
        self.platform = platform
        self.settings = settings
        self.own_rects = lambda: []  # set by the app: where Click's own windows are
        self.elements = ElementCache()
        self._ocr = None

    # ---- helpers ----------------------------------------------------------------------------
    def _blocked(self, w: Window) -> bool:
        return is_blocked(w.title, w.app, self.settings["blocklist"])

    def _find_window(self, query: str | None) -> Window:
        wins = self.platform.list_windows()
        if not query:
            w = self.platform.active_window()
            if not w:
                raise ScreenError("No active window found.")
            return w
        q = query.lower()
        for w in wins:
            if q in w.title.lower() or q == w.app.lower():
                return w
        raise ScreenError(f"No open window matches '{query}'.")

    def _target_rect(self, target: str | None, region) -> tuple[Rect, Window | None]:
        mons = monitors()
        if region:
            x, y, w, h = (int(v) for v in region[:4])
            return Rect(x, y, max(1, w), max(1, h)), None
        target = (target or "full").strip()
        if target == "full":
            m = mons[0]
            return Rect(m["left"], m["top"], m["width"], m["height"]), None
        if target.startswith("monitor:"):
            idx = int(target.split(":", 1)[1] or 1)
            if not 1 <= idx < len(mons):
                raise ScreenError(f"There is no monitor {idx}. This computer has {len(mons) - 1}.")
            m = mons[idx]
            return Rect(m["left"], m["top"], m["width"], m["height"]), None
        win = self._find_window(None if target == "active_window" else target.split(":", 1)[-1])
        if self._blocked(win):
            raise ScreenError("That window is on the blocklist (e.g. a password manager or banking page), so Click won't look at it.")
        return win.rect, win

    def capture_image(self, target: str | None = "full", region=None) -> tuple[Image.Image, Rect, list[str], Window | None]:
        rect, win = self._target_rect(target, region)
        img = _grab(rect)
        sx, sy = img.width / rect.w, img.height / rect.h  # Retina: image pixels per screen point
        draw = ImageDraw.Draw(img)
        blocked = []
        for w in self.platform.list_windows():
            if self._blocked(w):
                r = w.rect
                box = [(r.x - rect.x) * sx, (r.y - rect.y) * sy, (r.x + r.w - rect.x) * sx, (r.y + r.h - rect.y) * sy]
                if box[2] > 0 and box[3] > 0 and box[0] < img.width and box[1] < img.height:
                    draw.rectangle(box, fill=(0, 0, 0))
                    blocked.append(w.app or "blocked app")
        return img, rect, blocked, win

    @staticmethod
    def encode(img: Image.Image, fmt: str = "WEBP", max_side: int = MAX_SIDE) -> str:
        img = img.copy()
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, fmt, quality=80)
        return base64.b64encode(buf.getvalue()).decode()

    # ---- tools ------------------------------------------------------------------------------------
    def capture(self, target: str | None = "full", region=None) -> dict:
        img, rect, blocked, win = self.capture_image(target, region)
        return {"image_b64": self.encode(img), "mime": "image/webp",
                "monitor": {"left": rect.x, "top": rect.y, "width": rect.w, "height": rect.h},
                "blocked_windows": sorted(set(blocked)),
                "active_window": win.public() if win else None}

    def ocr(self, region=None) -> dict:
        from .ocr import lines_to_text, recognize

        img, rect, _, _ = self.capture_image("active_window" if not region else None, region)
        return {"text": lines_to_text(recognize(img, fast=False)), "region": rect.as_list()}

    def _monitor_for_active(self) -> tuple[str, Window | None]:
        """The monitor the user is working on (where the active window is)."""
        win = self.platform.active_window()
        if win:
            cx, cy = win.rect.center
            for i, m in enumerate(monitors()[1:], start=1):
                if m["left"] <= cx < m["left"] + m["width"] and m["top"] <= cy < m["top"] + m["height"]:
                    return f"monitor:{i}", win
        return "monitor:1", win

    def context(self, target: str | None = None, image: bool = True, **_) -> dict:
        """Everything needed to answer "what's on my screen?" in one fast step:
        active window, other windows, all visible text (fast OCR) and a small screenshot."""
        import time

        from .ocr import lines_to_text, recognize

        t0 = time.perf_counter()
        if not target or target == "full":
            target, active = self._monitor_for_active()
        else:
            active = self.platform.active_window()
        img, rect, blocked, _ = self.capture_image(target, None)
        lines = recognize(img, fast=True)
        others = []
        for w in self.platform.list_windows()[:12]:
            if active and w.handle == active.handle:
                continue
            others.append("[hidden app]" if self._blocked(w) else f"{w.title} ({w.app})")
        out = {"text": lines_to_text(lines)[:6000], "windows": others, "blocked_windows": sorted(set(blocked)),
               "monitor": {"left": rect.x, "top": rect.y, "width": rect.w, "height": rect.h}}
        if active:
            out["active_window"] = ({"title": "[hidden: blocklisted app]", "app": ""} if self._blocked(active)
                                    else {"title": active.title, "app": active.app})
        try:
            out["layout"] = self._layout(lines, img, rect, active)
        except Exception:  # noqa: BLE001 - a nice extra; never block the answer
            log.exception("layout failed")
        if image:
            out["image_b64"] = self.encode(img, "JPEG", max_side=1024)  # small = fewer image tokens = faster
            out["mime"] = "image/jpeg"
        out["ms"] = int((time.perf_counter() - t0) * 1000)
        return out

    @staticmethod
    def _where(x: float, y: float, r: Rect) -> str:
        """'top-left', 'middle', 'bottom-right'... of the screen."""
        cx = (x - r.x) / max(1, r.w)
        cy = (y - r.y) / max(1, r.h)
        v = "top" if cy < 0.33 else "bottom" if cy > 0.66 else "middle"
        h = "left" if cx < 0.33 else "right" if cx > 0.66 else ""
        return f"{v}-{h}" if h else ("middle" if v == "middle" else v)

    def _layout(self, lines: list, img, rect: Rect, active) -> str:
        """WHERE things are, so the AI can say 'top-right' and Clicky can point: text by screen area, the
        front window's buttons, the Dock."""
        sx, sy = rect.w / img.width, rect.h / img.height
        areas: dict[str, list[str]] = {}
        for ln in lines:
            if ln.get("conf", 1) < 0.4 or not ln["text"].strip():
                continue
            where = self._where(rect.x + (ln["x"] + ln["w"] / 2) * sx, rect.y + (ln["y"] + ln["h"] / 2) * sy, rect)
            areas.setdefault(where, []).append(ln["text"].strip())
        order = ["top-left", "top", "top-right", "middle-left", "middle", "middle-right",
                 "bottom-left", "bottom", "bottom-right"]
        parts = ["Screen areas: " + " | ".join(f"[{a}] " + " · ".join(areas[a][:25]) for a in order if a in areas)]
        if active and not self._blocked(active):
            seen, buttons = set(), []
            for el in self.platform.ui_elements(active)[:150]:
                n = (el.name or "").strip()
                if not n or n.lower() in seen or len(n) > 40:
                    continue
                seen.add(n.lower())
                buttons.append(f"{n} ({el.role.lower()}, {self._where(*el.rect.center, rect)})")
                if len(buttons) >= 45:
                    break
            if buttons:
                parts.append(f"Things you can click in {active.app}: " + "; ".join(buttons))
        try:
            dock = [e.name for e in self.platform.dock_items()][:30]
            if dock:
                parts.append("Dock: " + ", ".join(dock))
        except Exception:  # noqa: BLE001
            pass
        return "\n".join(parts)[:5000]

    def list_windows(self, **_) -> dict:
        out = []
        for w in self.platform.list_windows()[:60]:
            d = w.public()
            if self._blocked(w):
                d["title"], d["blocked"] = "[hidden: blocklisted app]", True
            out.append(d)
        return {"windows": out, "monitors": monitors()[1:]}

    def get_active_window(self, **_) -> dict:
        w = self.platform.active_window()
        if not w:
            return {"window": None}
        if self._blocked(w):
            return {"window": {"title": "[hidden: blocklisted app]", "blocked": True}}
        return {"window": w.public()}

    def _elements_payload(self, els: list[Element], limit: int = 200) -> list[dict]:
        out = []
        for el in els[:limit]:
            eid = self.elements.add(el)
            out.append({"id": eid, "name": el.name, "role": el.role, "rect": el.rect.as_list(),
                        **({"password_field": True} if el.is_password else {}),
                        **({"disabled": True} if not el.enabled else {})})
        return out

    def get_ui_elements(self, window: str | None = None, **_) -> dict:
        win = self._find_window(window)
        if self._blocked(win):
            raise ScreenError("That window is on the blocklist, so Click won't read it.")
        els = self.platform.ui_elements(win)
        return {"window": win.public(), "elements": self._elements_payload(els)}

    def find_ui_element(self, query: str, window: str | None = None, deep: bool = True, **_) -> dict:
        """Find something the user asked about. Looks, in order, at: the Dock (apps), the menu bar
        (Wi-Fi, battery, app menus), buttons in open windows, then visible text on screen.
        Click's own windows (the chat, the orb) are never matched."""
        q = _norm(query)
        own = self.own_rects()
        scored: list[tuple[float, Element]] = []

        def add(el: Element, boost: float = 0.0):
            if any(_overlaps(el.rect, r) for r in own):
                return  # never point at Click's own chat (it contains the user's question!)
            score = _score(q, _norm(el.name))
            if score >= 0.6:
                scored.append((min(1.0, score + boost), el))

        for getter, boost in ((self.platform.dock_items, 0.04), (self.platform.menu_bar_items, 0.02),
                              (self.platform.desktop_items, 0.03)):
            try:
                for el in getter():
                    add(el, boost)
            except Exception:  # noqa: BLE001
                pass
        # Clicky's built-in Mac map (Apple menu, Wi-Fi, Control Center, Dock, Trash...) works without
        # permissions and instantly; when it knows the thing, skip the slow window-by-window search.
        landmark = self._map_hit(query)
        wins = [] if landmark is not None and not window else \
            ([self._find_window(window)] if window else self.platform.list_windows()[:4])
        for i, win in enumerate(wins):
            if self._blocked(win):
                continue
            for el in self.platform.ui_elements(win):
                if el.name:
                    add(el, -0.03 * i)
        source = "accessibility"
        # Clicky's built-in Mac map: knows where the Apple menu, Wi-Fi, Control Center, Dock, Trash... are,
        # even without permissions. A precise Accessibility match still wins.
        best = max((sc for sc, _ in scored), default=0.0)
        if landmark is not None:
            if best < 0.95:
                scored.append((0.97, landmark))
                source = "mac map"
            else:
                top = max(scored, key=lambda s: s[0])[1]
                top.extra.setdefault("note", landmark.extra.get("note", ""))
        if not scored or max(sc for sc, _ in scored) < 0.85:
            extra = self._app_on_screen(query)  # an app in the Dock or an open window, without permissions
            if extra is not None:
                scored.append((0.93, extra))
                source = "dock / windows"
        if (not scored or max(sc for sc, _ in scored) < 0.85) and landmark is None and deep:
            for sc, el in self._find_by_text(q):
                if not any(_overlaps(el.rect, r) for r in own):
                    scored.append((sc, el))
            source = "screen text"
        if not scored or max(sc for sc, _ in scored) < 0.85:
            fallback = self._app_fallback(q)
            if fallback:
                scored.append(fallback)
                source = "app launcher"
        scored.sort(key=lambda s: s[0], reverse=True)
        found = self._elements_payload([el for _, el in scored[:6]])
        for item, (score, el) in zip(found, scored):
            item["match"] = round(score, 2)
            item["where"] = el.window_title
            if el.extra.get("note"):
                item["note"] = el.extra["note"]
        out = {"query": query, "matches": found, "found_by": source,
               "hint": "Use the id with ghost_move/click." if found else "Nothing found on screen."}
        problems = self.platform.check_permissions()
        if problems and not found:
            out["permission_problem"] = " ".join(problems)
        return out

    def _app_on_screen(self, query: str):
        """Find an app by name: its open window (title bar) or its Dock icon."""
        q = _norm(query).removesuffix(" app").strip()
        if not q:
            return None
        try:
            own = self.own_rects()
            for w in self.platform.list_windows()[:20]:
                if _norm(w.app) == q and w.rect.w > 80 and not any(_overlaps(w.rect, r) for r in own):
                    bar = Rect(w.rect.x, w.rect.y, w.rect.w, min(28, w.rect.h))
                    el = Element(name=w.app, role="Window", rect=bar, window_title=w.app)
                    el.extra["note"] = f"Here's your {w.app} window."
                    return el
        except Exception:  # noqa: BLE001
            pass
        geo_fn = getattr(self, "geometry_fn", None)
        try:
            from . import macmap

            g = geo_fn() if geo_fn else (macmap.mac_geometry(None) if sys.platform == "darwin" else None)
            return macmap.dock_app(query, g) if g else None
        except Exception:  # noqa: BLE001
            log.exception("dock lookup failed")
            return None

    def _map_hit(self, query: str):
        """Where Clicky's Mac map says `query` is (or None)."""
        from . import macmap

        if not macmap.match(query):
            return None
        try:
            geo_fn = getattr(self, "geometry_fn", None)
            if geo_fn is None:
                if sys.platform != "darwin":
                    return None
                own = self.own_rects()
                front = next((w for w in self.platform.list_windows()
                              if w.app not in ("Click", "Python") and not any(_overlaps(w.rect, r) for r in own)), None)
                g = macmap.mac_geometry(front)
            else:
                g = geo_fn()
            return macmap.locate(query, g) if g else None
        except Exception:  # noqa: BLE001
            log.exception("mac map failed")
            return None

    def _app_fallback(self, q: str):
        """An app that isn't in the Dock: point at the Apple menu (System Settings) or Spotlight."""
        try:
            items = self.platform.menu_bar_items()
        except Exception:  # noqa: BLE001
            return None
        if q in ("system settings", "settings", "system preferences", "preferences"):
            apple = next((el for el in items if _norm(el.name) in ("apple", "apple menu")), None)
            if apple:
                apple.extra["note"] = "System Settings is in the Apple menu: click it, then System Settings."
                return (0.8, apple)
        if _installed_app(q):
            spot = next((el for el in items if "spotlight" in _norm(el.name)), None)
            if spot:
                spot.extra["note"] = f"{q.title()} isn't in the Dock. Open Spotlight here and type its name."
                return (0.7, spot)
        return None

    def _find_by_text(self, q: str) -> list:
        """Locate visible text on the monitor the user is working on."""
        from .ocr import recognize

        target, _ = self._monitor_for_active()
        try:
            img, rect, _, _ = self.capture_image(target, None)
        except ScreenError:
            return []
        sx, sy = rect.w / img.width, rect.h / img.height
        hits = []
        lines = recognize(img, fast=True)  # Apple's fast reader first (made for real-time use)
        if not any(q in ln["text"].lower() for ln in lines):
            lines = recognize(img, fast=False)  # not seen: read again carefully
        for ln in lines:
            t = ln["text"].lower().strip()
            if not t:
                continue
            score = 1.0 if t == q else 0.92 if q in t and len(t) <= len(q) + 25 else \
                difflib.SequenceMatcher(None, q, t).ratio()
            if score >= 0.6:
                r = Rect(int(rect.x + ln["x"] * sx), int(rect.y + ln["y"] * sy),
                         max(4, int(ln["w"] * sx)), max(4, int(ln["h"] * sy)))
                w = self.platform.window_at(*r.center)
                if w and self._blocked(w):
                    continue
                hits.append((score - 0.02, Element(name=ln["text"][:120], role="Text", rect=r,
                                                   window_title=w.title if w else "")))
        return hits

    def element_rect(self, element_id: str) -> Rect:
        el = self.elements.get(element_id)
        if not el:
            raise ScreenError(f"Unknown element id {element_id}. Call find_ui_element or get_ui_elements again.")
        return el.rect

    def crop(self, rect: Rect) -> str:
        img, _, _, _ = self.capture_image(None, rect.as_list())
        return self.encode(img, "PNG", max_side=1200)
