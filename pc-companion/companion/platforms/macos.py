"""macOS implementation: Quartz for windows, the Accessibility API (pyobjc) for UI elements.

Needs two one-time permissions in System Settings > Privacy & Security:
Screen Recording (to see window titles and take screenshots) and Accessibility (for UI elements,
the ghost cursor's real clicks and the kill-switch hotkey).
"""
from __future__ import annotations

import logging

from .base import Element, Platform, Rect, Window

log = logging.getLogger("click.platform.macos")

try:
    import Quartz
    from AppKit import NSWorkspace
    from ApplicationServices import (
        AXIsProcessTrusted, AXIsProcessTrustedWithOptions, AXUIElementCopyAttributeValue,
        AXUIElementCopyElementAtPosition, AXUIElementCreateApplication, AXUIElementCreateSystemWide,
        AXValueGetValue, kAXTrustedCheckOptionPrompt, kAXValueCGPointType, kAXValueCGSizeType,
    )
except ImportError as exc:  # pragma: no cover - only on macOS
    raise ImportError("pyobjc is required on macOS: pip install pyobjc") from exc


def _attr(element, name):
    err, value = AXUIElementCopyAttributeValue(element, name, None)
    return value if err == 0 else None


def _rect_of(element) -> Rect | None:
    pos, size = _attr(element, "AXPosition"), _attr(element, "AXSize")
    if pos is None or size is None:
        return None
    ok1, p = AXValueGetValue(pos, kAXValueCGPointType, None)
    ok2, s = AXValueGetValue(size, kAXValueCGSizeType, None)
    if not (ok1 and ok2):
        return None
    return Rect(int(p.x), int(p.y), int(s.width), int(s.height))


def _real_display() -> bool:
    """False in the headless self-test, where windows aren't real macOS windows."""
    try:
        from PySide6.QtGui import QGuiApplication

        return QGuiApplication.platformName() == "cocoa"
    except Exception:  # noqa: BLE001
        return False


def _role_name(role: str) -> str:
    return (role or "").removeprefix("AX")


class MacPlatform(Platform):
    name = "macos"

    def list_windows(self) -> list[Window]:
        opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        info = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        front_pid = front.processIdentifier() if front else -1
        import os

        own = os.getpid()
        out, seen_front = [], False
        for w in info:
            if w.get("kCGWindowLayer", 1) != 0:
                continue
            pid = int(w.get("kCGWindowOwnerPID", 0))
            if pid == own:
                continue
            b = w.get("kCGWindowBounds", {})
            rect = Rect(int(b.get("X", 0)), int(b.get("Y", 0)), int(b.get("Width", 0)), int(b.get("Height", 0)))
            if rect.w < 40 or rect.h < 30:
                continue
            app = w.get("kCGWindowOwnerName", "") or ""
            title = w.get("kCGWindowName", "") or app
            focused = pid == front_pid and not seen_front
            seen_front = seen_front or focused
            out.append(Window(int(w.get("kCGWindowNumber", 0)), title, app, rect, pid, focused))
        return out

    def _ax_window(self, window: Window):
        app = AXUIElementCreateApplication(window.pid)
        wins = _attr(app, "AXWindows") or []
        for w in wins:
            if (_attr(w, "AXTitle") or "") == window.title:
                return w
        return _attr(app, "AXFocusedWindow") or (wins[0] if wins else None)

    def ui_elements(self, window: Window, max_elements: int = 300) -> list[Element]:
        root = self._ax_window(window)
        if root is None:
            return []
        out: list[Element] = []
        stack = [(root, 0)]
        while stack and len(out) < max_elements:
            el, depth = stack.pop()
            children = _attr(el, "AXChildren") or []
            if depth < 25:
                stack.extend((c, depth + 1) for c in reversed(list(children)))
            if el is root:
                continue
            role = _attr(el, "AXRole") or ""
            name = (_attr(el, "AXTitle") or _attr(el, "AXDescription") or _attr(el, "AXHelp") or "")
            if not name and role in ("AXTextField", "AXTextArea"):
                name = _attr(el, "AXPlaceholderValue") or ""
            if not name and role not in ("AXButton", "AXTextField", "AXCheckBox", "AXPopUpButton", "AXMenuItem"):
                continue
            rect = _rect_of(el)
            if not rect or rect.w <= 1 or rect.h <= 1:
                continue
            subrole = _attr(el, "AXSubrole") or ""
            out.append(Element(name=str(name)[:120], role=_role_name(role), rect=rect,
                               is_password=subrole == "AXSecureTextField", enabled=bool(_attr(el, "AXEnabled")),
                               window_title=window.title))
        return out

    def menu_bar_items(self) -> list[Element]:
        """Menu bar icons (Wi-Fi, battery, sound, Control Center...) and the front app's menus."""
        ws = NSWorkspace.sharedWorkspace()
        pids = [a.processIdentifier() for a in ws.runningApplications()
                if (a.bundleIdentifier() or "") in ("com.apple.controlcenter", "com.apple.systemuiserver",
                                                    "com.apple.Spotlight", "com.apple.TextInputMenuAgent")]
        front = ws.frontmostApplication()
        if front:
            pids.append(front.processIdentifier())
        out: list[Element] = []
        for pid in pids:
            app = AXUIElementCreateApplication(pid)
            for attr in ("AXExtrasMenuBar", "AXMenuBar"):
                bar = _attr(app, attr)
                for el in (_attr(bar, "AXChildren") or []) if bar is not None else []:
                    name = _attr(el, "AXTitle") or _attr(el, "AXDescription") or _attr(el, "AXIdentifier") or ""
                    rect = _rect_of(el)
                    if name and rect and rect.w > 2:
                        out.append(Element(name=str(name)[:80], role="MenuBarItem", rect=rect, window_title="Menu bar"))
        return out

    def element_at(self, x: float, y: float) -> Element | None:
        err, el = AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), float(x), float(y), None)
        if err != 0 or el is None:
            return None
        rect = _rect_of(el) or Rect(int(x), int(y), 1, 1)
        name = _attr(el, "AXTitle") or _attr(el, "AXDescription") or _attr(el, "AXValue") or ""
        return Element(name=str(name)[:120], role=_role_name(_attr(el, "AXRole") or ""), rect=rect,
                       is_password=(_attr(el, "AXSubrole") or "") == "AXSecureTextField")

    def focused_is_sensitive(self) -> bool:
        try:
            el = _attr(AXUIElementCreateSystemWide(), "AXFocusedUIElement")
            if el is None:
                return False
            if (_attr(el, "AXSubrole") or "") == "AXSecureTextField":
                return True
            name = str(_attr(el, "AXTitle") or _attr(el, "AXDescription") or "").lower()
            return any(k in name for k in ("password", "passcode", "one-time", "verification code", "pin"))
        except Exception:  # noqa: BLE001 - if we can't tell, be safe
            return True

    def configure_overlay(self, widget, click_through: bool = True) -> None:
        """Qt's "stay on top" isn't enough on macOS: other apps, full-screen apps and other Spaces
        would hide the cursor. Raise it to the status-bar level and show it on every Space."""
        if not _real_display():
            return
        try:
            import objc

            win = objc.objc_object(c_void_p=int(widget.winId())).window()
            # Cursor overlay at pop-up-menu level; the clickable orb one level above it, so the two
            # never fight over which is on top (that caused a flicker when pausing).
            win.setLevel_(101 if click_through else 102)
            # canJoinAllSpaces | stationary | ignoresCycle | fullScreenAuxiliary
            win.setCollectionBehavior_((1 << 0) | (1 << 4) | (1 << 6) | (1 << 8))
            win.setIgnoresMouseEvents_(bool(click_through))  # the orb must stay clickable
            win.setHasShadow_(False)
            win.orderFrontRegardless()
        except Exception:  # noqa: BLE001
            log.warning("could not raise the overlay window")

    def exclude_from_capture(self, widget) -> None:
        if not _real_display():
            return
        try:
            import objc

            view = objc.objc_object(c_void_p=int(widget.winId()))
            view.window().setSharingType_(0)  # NSWindowSharingNone
        except Exception:  # noqa: BLE001
            log.info("could not exclude overlay from capture")

    def check_permissions(self) -> list[str]:
        problems = []
        if not Quartz.CGPreflightScreenCaptureAccess():
            problems.append("Screen Recording is not allowed yet (System Settings > Privacy & Security > Screen Recording).")
        if not AXIsProcessTrusted():
            problems.append("Accessibility is not allowed yet (System Settings > Privacy & Security > Accessibility).")
        return problems

    def request_permissions(self) -> None:
        try:
            Quartz.CGRequestScreenCaptureAccess()
            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
        except Exception:  # noqa: BLE001
            pass
