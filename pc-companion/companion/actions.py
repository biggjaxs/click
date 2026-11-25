"""Ghost cursor tools and real mouse/keyboard actions, with confirmations and the kill switch.

- Look-only tools (move, point, highlight, inspect, teach) need only the cursor permission.
- Real actions (click, type, keys, scroll) show a popup near the target first. High-risk actions
  always ask, with no "allow for this task" option.
- If you move your real mouse during an action, or press the kill switch (Ctrl+Alt+Esc), Click
  stops immediately and lets go.
"""
from __future__ import annotations

import asyncio
import logging
import math
import threading
import sys
import time

from .gui import ConfirmRequest
from .platforms import Rect
from .safety import is_blocked, looks_like_secret, risky_keys

log = logging.getLogger("click.actions")



def _wait_for_click_mac(area: Rect, timeout: float, killed) -> bool:
    """True when the user clicks inside `area` (with a little margin) before `timeout` seconds."""
    import Quartz

    box = Rect(area.x - 12, area.y - 12, area.w + 24, area.h + 24)
    end = time.time() + timeout
    was_down = False
    while time.time() < end and not killed.is_set():
        down = Quartz.CGEventSourceButtonState(Quartz.kCGEventSourceStateCombinedSessionState, 0)
        if down and not was_down:
            loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
            if box.contains(loc.x, loc.y):
                return True
        was_down = down
        time.sleep(0.05)
    return False

class ActionError(RuntimeError):
    pass


class Stopped(ActionError):
    pass


def parse_keys(keys: str):
    from pynput.keyboard import Key, KeyCode

    names = {"ctrl": Key.ctrl, "control": Key.ctrl, "alt": Key.alt, "option": Key.alt, "opt": Key.alt,
             "shift": Key.shift, "cmd": Key.cmd, "command": Key.cmd, "win": Key.cmd, "super": Key.cmd,
             "enter": Key.enter, "return": Key.enter, "tab": Key.tab, "esc": Key.esc, "escape": Key.esc,
             "space": Key.space, "backspace": Key.backspace, "delete": Key.delete, "del": Key.delete,
             "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right, "home": Key.home, "end": Key.end,
             "pageup": Key.page_up, "pagedown": Key.page_down}
    names.update({f"f{i}": getattr(Key, f"f{i}") for i in range(1, 13)})
    out = []
    for part in keys.lower().replace(" ", "").split("+"):
        if part in names:
            out.append(names[part])
        elif len(part) == 1:
            out.append(KeyCode.from_char(part))
        else:
            raise ActionError(f"Unknown key '{part}'.")
    return out


class Actions:
    def __init__(self, bridge, ghost, screen, platform, settings):
        self.bridge, self.ghost, self.screen, self.platform, self.settings = bridge, ghost, screen, platform, settings
        self.killed = threading.Event()
        self.task_allow: dict[str, set[str]] = {}   # task_id -> allowed action kinds
        self._mouse = None
        self._keyboard = None

    @property
    def mouse(self):
        if self._mouse is None:
            from pynput.mouse import Controller

            self._mouse = Controller()
        return self._mouse

    @property
    def keyboard(self):
        if self._keyboard is None:
            from pynput.keyboard import Controller

            self._keyboard = Controller()
        return self._keyboard

    # ---- targets -------------------------------------------------------------------------------
    def _target(self, a: dict) -> tuple[float, float, Rect | None, str]:
        if a.get("element_id"):
            rect = self.screen.element_rect(a["element_id"])
            el = self.screen.elements.get(a["element_id"])
            cx, cy = rect.center
            return cx, cy, rect, el.name if el else ""
        if a.get("x") is not None and a.get("y") is not None:
            return float(a["x"]), float(a["y"]), None, ""
        if self.ghost.visible:
            return self.ghost.x, self.ghost.y, None, ""
        raise ActionError("No target: give an element_id (from find_ui_element) or x and y.")

    def _check_window(self, x: float, y: float) -> None:
        w = self.platform.window_at(x, y)
        if w and is_blocked(w.title, w.app, self.settings["blocklist"]):
            raise ActionError("That's in a blocklisted app (password manager, banking or payment), so Click won't act there.")

    # ---- look-only tools ---------------------------------------------------------------------------
    async def move(self, a: dict) -> dict:
        x, y, rect, name = self._target(a)
        self.bridge.ghostMove.emit(x, y, a.get("label", ""))
        if rect:
            self.bridge.ghostHighlight.emit(rect.x, rect.y, rect.w, rect.h, "")
        await asyncio.sleep(0.7)
        return {"status": "pointing", "at": [int(x), int(y)], "element": name}

    async def highlight(self, a: dict) -> dict:
        if a.get("element_id"):
            rect = self.screen.element_rect(a["element_id"])
        elif a.get("region"):
            rx, ry, rw, rh = (int(v) for v in a["region"][:4])
            rect = Rect(rx, ry, rw, rh)
        else:
            raise ActionError("Give an element_id or a region [x, y, width, height].")
        self._check_window(*rect.center)
        self.bridge.ghostMove.emit(float(rect.x + rect.w), float(rect.y + rect.h), "")
        self.bridge.ghostHighlight.emit(rect.x, rect.y, rect.w, rect.h, a.get("label", ""))
        out = {"status": "highlighted", "region": rect.as_list()}
        if a.get("inspect"):
            await asyncio.sleep(0.2)
            out["image_b64"] = await asyncio.to_thread(self.screen.crop, rect)
        return out

    async def inspect(self, a: dict) -> dict:
        if not self.ghost.visible:
            return {"status": "Click's cursor is hidden. Move it first with ghost_move."}
        el = await asyncio.to_thread(self.platform.element_at, self.ghost.x, self.ghost.y)
        if not el:
            return {"at": [int(self.ghost.x), int(self.ghost.y)], "element": None}
        w = self.platform.window_at(self.ghost.x, self.ghost.y)
        if w and is_blocked(w.title, w.app, self.settings["blocklist"]):
            return {"element": "[hidden: blocklisted app]"}
        return {"at": [int(self.ghost.x), int(self.ghost.y)],
                "element": {"name": el.name, "role": el.role, "rect": el.rect.as_list()}}

    async def hide(self, a: dict) -> dict:
        self.bridge.ghostHide.emit()
        return {"status": "hidden"}

    async def teach_step(self, a: dict) -> dict:
        """Point at the next thing to click and wait for the user to click it themselves."""
        x, y, rect, name = self._target(a)
        self.bridge.ghostMove.emit(x, y, a.get("instruction", ""))
        if rect:
            self.bridge.ghostHighlight.emit(rect.x, rect.y, rect.w, rect.h, "")
        area = rect or Rect(int(x) - 30, int(y) - 30, 60, 60)
        if sys.platform == "darwin":
            # watch for the click by asking macOS where the mouse is and whether it's pressed (safe from any
            # thread, needs no extra permission)
            got = await asyncio.to_thread(_wait_for_click_mac, area, float(a.get("timeout", 90)), self.killed)
            if got:
                return {"status": "user_clicked", "element": name}
            return {"status": "stopped" if self.killed.is_set() else "timeout",
                    "note": "The user didn't click it in time."}
        clicked = threading.Event()
        try:
            from pynput import mouse

            def on_click(px, py, button, pressed):
                if pressed and Rect(area.x - 10, area.y - 10, area.w + 20, area.h + 20).contains(px, py):
                    clicked.set()
                    return False
                return None

            listener = mouse.Listener(on_click=on_click)
            listener.start()
        except Exception:  # noqa: BLE001 - listener not permitted (macOS input monitoring)
            return {"status": "pointing", "note": "Couldn't watch for your click; say 'next' when done."}
        try:
            for _ in range(900):  # up to 90 s
                if clicked.is_set() or self.killed.is_set():
                    break
                await asyncio.sleep(0.1)
        finally:
            listener.stop()
        if clicked.is_set():
            return {"status": "user_clicked", "element": name}
        return {"status": "stopped" if self.killed.is_set() else "timeout",
                "note": "The user didn't click it within 90 seconds."}

    # ---- real actions --------------------------------------------------------------------------------
    async def _confirm(self, kind: str, title: str, detail: str, x: float, y: float, a: dict) -> None:
        high = a.get("risk") == "high"
        task = a.get("task_id", "")
        if not high and kind in self.task_allow.get(task, set()):
            return
        if not high and self.settings.get("auto_approve", False):
            # "Always allow": Clicky acts without asking for everyday actions (risky ones still ask)
            self.bridge.narrate.emit(title)
            return
        req = ConfirmRequest(title=title, detail=detail, x=x, y=y, high_risk=high)
        self.bridge.confirmRequest.emit(req)
        decision = await asyncio.wrap_future(req.future)
        if decision == "deny":
            raise ActionError("The user said no on their screen. Don't try again unless they ask.")
        if decision == "task" and not high and task:
            self.task_allow.setdefault(task, set()).add(kind)
        if decision == "always" and not high:
            self.settings.set("auto_approve", True)

    def _guard_start(self) -> tuple[float, float]:
        if self.killed.is_set():
            raise Stopped("Stopped by the kill switch. Everything is paused.")
        self.bridge.indicator.emit("control", True)
        return self.mouse.position

    def _user_moved(self, expected: tuple[float, float]) -> bool:
        pos = self.mouse.position
        return math.hypot(pos[0] - expected[0], pos[1] - expected[1]) > 8

    async def click(self, a: dict) -> dict:
        x, y, rect, name = self._target(a)
        self._check_window(x, y)
        self.bridge.ghostMove.emit(x, y, a.get("description") or (f"Click '{name}'" if name else "Click here"))
        await asyncio.sleep(0.7)
        label = a.get("description") or (f"Click '{name}'" if name else f"Click at ({int(x)}, {int(y)})")
        what = ("Double-click" if a.get("double") else "Right-click" if a.get("button") == "right" else "Click")
        await self._confirm("click", label, f"{what} at the purple cursor.", x, y, a)
        await asyncio.sleep(0.25)  # let the popup close before clicking under it
        from pynput.mouse import Button

        home = self._guard_start()
        try:
            self.mouse.position = (x, y)
            time.sleep(0.04)
            if self._user_moved((x, y)) or self.killed.is_set():
                raise Stopped("You moved your mouse, so Click stopped and let go.")
            self.mouse.click(Button.right if a.get("button") == "right" else Button.left, 2 if a.get("double") else 1)
            time.sleep(0.05)
        finally:
            self.mouse.position = home
            self.bridge.indicator.emit("control", False)
        return {"status": "clicked", "at": [int(x), int(y)], "element": name}

    async def type_text(self, a: dict, confirmed: bool = False) -> dict:
        text = str(a.get("text", ""))
        if not text:
            raise ActionError("Nothing to type.")
        if looks_like_secret(text):
            raise ActionError("That looks like a card number. Click never types card numbers or passwords.")
        if await asyncio.to_thread(self.platform.focused_is_sensitive):
            raise ActionError("The focused field is a password or code field. Click never types into those.")
        aw = self.platform.active_window()
        if aw and is_blocked(aw.title, aw.app, self.settings["blocklist"]):
            raise ActionError("The active window is blocklisted, so Click won't type there.")
        preview = text if len(text) <= 160 else text[:157] + "…"
        x, y = (self.ghost.x, self.ghost.y) if self.ghost.visible else (aw.rect.center if aw else (100, 100))
        if not confirmed:  # a skill task was already approved as a whole
            await self._confirm("type", "Type text", preview, x, y, a)
        if await asyncio.to_thread(self.platform.focused_is_sensitive):
            raise ActionError("The focus moved to a password field, so Click stopped.")
        start = self._guard_start()
        if sys.platform == "darwin":  # crash-safe typing from this background thread (see macinput.py)
            from . import macinput

            try:
                done = await asyncio.to_thread(macinput.put_text, text,
                                               lambda: self.killed.is_set() or self._user_moved(start))
                if done < len(text):
                    raise Stopped(f"Stopped after {done} characters because you moved your mouse or pressed stop.")
            finally:
                self.bridge.indicator.emit("control", False)
            return {"status": "typed", "characters": len(text)}
        try:
            for i in range(0, len(text), 8):
                if self.killed.is_set() or self._user_moved(start):
                    raise Stopped(f"Stopped after {i} characters because you moved your mouse or pressed the kill switch.")
                self.keyboard.type(text[i:i + 8])
                time.sleep(0.01)
        finally:
            self.bridge.indicator.emit("control", False)
        return {"status": "typed", "characters": len(text)}

    async def press_keys(self, a: dict) -> dict:
        keys = str(a.get("keys", ""))
        combo = parse_keys(keys)
        if risky_keys(keys):
            a = {**a, "risk": "high"}
        aw = self.platform.active_window()
        if aw and is_blocked(aw.title, aw.app, self.settings["blocklist"]):
            raise ActionError("The active window is blocklisted, so Click won't press keys there.")
        x, y = (self.ghost.x, self.ghost.y) if self.ghost.visible else (aw.rect.center if aw else (100, 100))
        await self._confirm("keys", f"Press {keys}", f"In: {aw.title if aw else 'the active window'}", x, y, a)
        self._guard_start()
        if sys.platform == "darwin":
            from . import macinput

            try:
                await asyncio.to_thread(macinput.press, keys)
            except ValueError as exc:
                raise ActionError(str(exc)) from exc
            finally:
                self.bridge.indicator.emit("control", False)
            return {"status": "pressed", "keys": keys}
        try:
            for k in combo:
                self.keyboard.press(k)
            time.sleep(0.05)
            for k in reversed(combo):
                self.keyboard.release(k)
        finally:
            self.bridge.indicator.emit("control", False)
        return {"status": "pressed", "keys": keys}

    async def scroll(self, a: dict) -> dict:
        direction = a.get("direction", "down")
        amount = max(1, min(30, int(a.get("amount") or 3)))
        x, y = (self.ghost.x, self.ghost.y) if self.ghost.visible else self.mouse.position
        self._check_window(x, y)
        await self._confirm("scroll", f"Scroll {direction}", f"{amount} steps at Click's cursor", x, y, a)
        dx, dy = {"up": (0, amount), "down": (0, -amount), "left": (-amount, 0), "right": (amount, 0)}[direction]
        home = self._guard_start()
        try:
            self.mouse.position = (x, y)
            time.sleep(0.03)
            self.mouse.scroll(dx, dy)
        finally:
            self.mouse.position = home
            self.bridge.indicator.emit("control", False)
        return {"status": "scrolled", "direction": direction, "amount": amount}
