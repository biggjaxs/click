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
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except Exception as exc:  # noqa: BLE001
        if sys.platform == "darwin":  # fallback: macOS built-in screencapture tool
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "shot.png"
                subprocess.run(["screencapture", "-x", "-R", f"{rect.x},{rect.y},{rect.w},{rect.h}", str(out)],
                               check=True, timeout=15)
                return Image.open(out).convert("RGB")
        raise ScreenError(f"Could not take a screenshot: {exc}") from exc


class ScreenService:
    def __init__(self, platform: Platform, settings):
        self.platform = platform
        self.settings = settings
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
        if image:
            out["image_b64"] = self.encode(img, "JPEG", max_side=1024)  # small = fewer image tokens = faster
            out["mime"] = "image/jpeg"
        out["ms"] = int((time.perf_counter() - t0) * 1000)
        return out

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

    def find_ui_element(self, query: str, window: str | None = None, **_) -> dict:
        """Find a button/menu/icon by name: accessibility tree first, then visible text (OCR)."""
        wins = [self._find_window(window)] if window else [w for w in self.platform.list_windows()[:6]]
        q = query.lower().strip()
        scored = []
        for win in wins:
            if self._blocked(win):
                continue
            for el in self.platform.ui_elements(win):
                name = el.name.lower()
                if not name:
                    continue
                score = 1.0 if name == q else 0.9 if q in name else difflib.SequenceMatcher(None, q, name).ratio()
                if score >= 0.55:
                    scored.append((score - 0.05 * wins.index(win), el))
        source = "accessibility"
        if not scored or scored and max(sc for sc, _ in scored) < 0.85:
            scored += self._find_by_text(q)
            source = "screen text" if not any(sc >= 0.85 for sc, _ in scored[:1]) else source
        scored.sort(key=lambda s: s[0], reverse=True)
        found = self._elements_payload([el for _, el in scored[:8]])
        for item, (score, el) in zip(found, scored):
            item["match"] = round(score, 2)
            item["window"] = el.window_title
        out = {"query": query, "matches": found, "found_by": source,
               "hint": "Use the id with ghost_move/click." if found else "Nothing found. Try get_screen or another name."}
        problems = self.platform.check_permissions()
        if problems and not found:
            out["permission_problem"] = " ".join(problems)
            out["hint"] = ("Click can't read this computer's buttons until the user allows it: " + " ".join(problems)
                           + " Tell the user exactly this.")
        return out

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
        for ln in recognize(img, fast=False):
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
