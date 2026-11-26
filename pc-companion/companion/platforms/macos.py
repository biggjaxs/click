"""macOS implementation: Quartz for windows, the Accessibility API (pyobjc) for UI elements.

Needs two one-time permissions in System Settings > Privacy & Security:
Screen Recording (to see window titles and take screenshots) and Accessibility (for UI elements,
the ghost cursor's real clicks and the kill-switch hotkey).
"""
from __future__ import annotations

import logging
import os
import threading

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


try:
    from ApplicationServices import AXUIElementGetPid
except ImportError:  # pragma: no cover
    AXUIElementGetPid = None

_OWN_PID = os.getpid()


def _is_own(element) -> bool:
    """True if this accessibility element belongs to Click itself."""
    if AXUIElementGetPid is None or element is None:
        return False
    try:
        err, pid = AXUIElementGetPid(element, None)
    except Exception:  # noqa: BLE001
        return False
    return err == 0 and pid == _OWN_PID


def _attr(element, name):
    # Asking macOS about Click's OWN windows from a background thread makes macOS close Click instantly
    # (AppKit answers those questions on our main thread). Helpers that run in the background
    # (the pop-up helper, the screen index...) therefore never look at Click's own windows.
    if threading.current_thread() is not threading.main_thread() and _is_own(element):
        return None
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

    def desktop_items(self) -> list[Element]:
        """Icons on the desktop. They belong to Finder's desktop, not to any window."""
        finder = [a for a in NSWorkspace.sharedWorkspace().runningApplications()
                  if (a.bundleIdentifier() or "") == "com.apple.finder"]
        if not finder:
            return []
        app = AXUIElementCreateApplication(finder[0].processIdentifier())
        out: list[Element] = []
        for area in _attr(app, "AXChildren") or []:
            if (_attr(area, "AXRole") or "") != "AXScrollArea":
                continue  # Finder windows are AXWindow; the desktop is a scroll area
            stack = [(area, 0)]
            while stack and len(out) < 500:
                el, depth = stack.pop()
                for c in _attr(el, "AXChildren") or []:
                    if depth < 5:
                        stack.append((c, depth + 1))
                if el is area:
                    continue
                title = _attr(el, "AXTitle") or _attr(el, "AXDescription") or _attr(el, "AXFilename") or ""
                rect = _rect_of(el)
                if title and rect and rect.w > 4 and rect.h > 4:
                    out.append(Element(name=str(title)[:120], role="DesktopIcon", rect=rect, window_title="Desktop"))
        return out

    def dock_items(self) -> list[Element]:
        """App icons in the Dock, so "show me System Settings" can point at its icon."""
        dock = [a for a in NSWorkspace.sharedWorkspace().runningApplications()
                if (a.bundleIdentifier() or "") == "com.apple.dock"]
        if not dock:
            return []
        out: list[Element] = []
        app = AXUIElementCreateApplication(dock[0].processIdentifier())
        for lst in _attr(app, "AXChildren") or []:
            for el in _attr(lst, "AXChildren") or []:
                title = _attr(el, "AXTitle") or ""
                rect = _rect_of(el)
                if title and rect:
                    out.append(Element(name=str(title), role="DockItem", rect=rect, window_title="Dock"))
        return out

    def app_menus(self) -> list[Element]:
        """The front app's own menus (File, Edit, View...) and the items of a menu that's open right now
        (from the menu bar, or a right-click menu), so a lesson can point at 'New Folder' inside 'File'."""
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is None or front.processIdentifier() == _OWN_PID:
            return []
        app = AXUIElementCreateApplication(front.processIdentifier())
        out: list[Element] = []

        def menu_items(menu, depth=0):
            for it in _attr(menu, "AXChildren") or []:
                name = _attr(it, "AXTitle") or ""
                rect = _rect_of(it)
                if name and rect and rect.w > 2 and rect.h > 2:
                    out.append(Element(name=str(name)[:80], role="MenuItem", rect=rect, window_title="Menu"))
                for sub in _attr(it, "AXChildren") or []:  # an open submenu
                    if depth < 2 and (_attr(sub, "AXRole") or "") == "AXMenu" and _rect_of(sub):
                        menu_items(sub, depth + 1)

        bar = _attr(app, "AXMenuBar")
        for i, item in enumerate(_attr(bar, "AXChildren") or [] if bar is not None else []):
            name = _attr(item, "AXTitle") or ""
            rect = _rect_of(item)
            if i == 0 or not name or not rect:  # skip the Apple menu
                continue
            el = Element(name=str(name)[:80], role="MenuBarItem", rect=rect, window_title="Menu bar")
            # what's inside this menu (readable even while it's closed): lets a lesson know that
            # "New Private Window" is under File before anything is opened
            inside = []
            for menu in _attr(item, "AXChildren") or []:
                for it in (_attr(menu, "AXChildren") or [])[:40]:
                    t = _attr(it, "AXTitle") or ""
                    if t:
                        inside.append(str(t)[:50])
                        for sub in _attr(it, "AXChildren") or []:  # one level of submenus: New > Playlist
                            for st in (_attr(sub, "AXChildren") or [])[:12]:
                                u = _attr(st, "AXTitle") or ""
                                if u:
                                    inside.append(f"{str(t)[:30]} > {str(u)[:30]}")
            el.extra["items"] = inside
            out.append(el)
            if _attr(item, "AXSelected"):  # this menu is open: its items are on screen
                for menu in _attr(item, "AXChildren") or []:
                    menu_items(menu)
        for child in _attr(app, "AXChildren") or []:  # a right-click menu
            if (_attr(child, "AXRole") or "") == "AXMenu":
                menu_items(child)
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

    def front_dialog(self) -> dict | None:
        """The pop-up in front (an alert, an error sheet, a "not responding" box...), or None.
        Returns its app, title, message text, button names and where it is. Never looks inside dialogs that
        have password fields."""
        ws = NSWorkspace.sharedWorkspace()
        front = ws.frontmostApplication()
        if front is None or front.processIdentifier() == _OWN_PID:
            return None  # Click itself is in front: nothing to rescue
        app = AXUIElementCreateApplication(front.processIdentifier())
        win = _attr(app, "AXFocusedWindow")
        if win is None:
            return None
        sub = str(_attr(win, "AXSubrole") or "")
        sheet = next((c for c in (_attr(win, "AXChildren") or []) if _attr(c, "AXRole") == "AXSheet"), None)
        box = sheet if sheet is not None else win
        texts, buttons, secure = [], [], False

        def walk(el, depth=0):
            nonlocal secure
            if depth > 4 or len(texts) > 30:
                return
            for c in _attr(el, "AXChildren") or []:
                role = str(_attr(c, "AXRole") or "")
                if (_attr(c, "AXSubrole") or "") == "AXSecureTextField":
                    secure = True
                if role == "AXStaticText":
                    v = str(_attr(c, "AXValue") or "").strip()
                    if v:
                        texts.append(v[:300])
                elif role == "AXButton":
                    t = str(_attr(c, "AXTitle") or _attr(c, "AXDescription") or "").strip()
                    if t and t not in buttons:
                        buttons.append(t[:40])
                walk(c, depth + 1)

        walk(box)
        rect = _rect_of(box)
        is_dialog = sheet is not None or sub in ("AXDialog", "AXSystemDialog")
        small = rect is not None and rect.w < 760 and rect.h < 520
        if secure or not texts or not buttons or not (is_dialog or small):
            return None
        title = str(_attr(win, "AXTitle") or "")
        return {"app": str(front.localizedName() or ""), "title": title, "text": " ".join(texts)[:900],
                "buttons": buttons[:6], "rect": rect.as_list() if rect else None}

    def form_fields(self) -> list[dict]:
        """Text boxes in the window in front, with the label people see next to each one. Password fields
        are left out entirely."""
        ws = NSWorkspace.sharedWorkspace()
        front = ws.frontmostApplication()
        if front is None:
            return []
        win = _attr(AXUIElementCreateApplication(front.processIdentifier()), "AXFocusedWindow")
        if win is None:
            return []
        fields, last_text = [], [""]

        def label_of(el) -> str:
            t = _attr(el, "AXTitleUIElement")
            for v in ((_attr(t, "AXValue") if t is not None else None), _attr(el, "AXDescription"),
                      _attr(el, "AXPlaceholderValue"), _attr(el, "AXTitle"), _attr(el, "AXHelp")):
                if v and str(v).strip():
                    return str(v).strip()
            return last_text[0]

        def walk(el, depth=0):
            if depth > 14 or len(fields) >= 40:
                return
            for c in _attr(el, "AXChildren") or []:
                role = str(_attr(c, "AXRole") or "")
                sub = str(_attr(c, "AXSubrole") or "")
                if role == "AXStaticText":
                    v = str(_attr(c, "AXValue") or "").strip()
                    if v:
                        last_text[0] = v[:80]
                elif role in ("AXTextField", "AXTextArea", "AXComboBox") and sub != "AXSecureTextField":
                    rect = _rect_of(c)
                    if rect and rect.w > 20:
                        fields.append({"ax": c, "label": label_of(c)[:80], "value": str(_attr(c, "AXValue") or ""),
                                       "rect": rect})
                walk(c, depth + 1)

        walk(win)
        return fields

    def set_field(self, field: dict, value: str) -> bool:
        """Put text straight into a text box (no typing); True if it took."""
        from ApplicationServices import AXUIElementSetAttributeValue

        el = field["ax"]
        AXUIElementSetAttributeValue(el, "AXFocused", True)
        err = AXUIElementSetAttributeValue(el, "AXValue", value)
        return err == 0 and str(_attr(el, "AXValue") or "") == value

    def element_at(self, x: float, y: float) -> Element | None:
        err, el = AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), float(x), float(y), None)
        if err != 0 or el is None:
            return None
        rect = _rect_of(el) or Rect(int(x), int(y), 1, 1)
        role = str(_attr(el, "AXRole") or "")
        if role in ("AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"):
            # a text box: name it by its label, never by what's typed in it
            t = _attr(el, "AXTitleUIElement")
            name = ((_attr(t, "AXValue") if t is not None else None) or _attr(el, "AXDescription")
                    or _attr(el, "AXPlaceholderValue") or _attr(el, "AXTitle") or "")
        else:
            name = _attr(el, "AXTitle") or _attr(el, "AXDescription") or _attr(el, "AXValue") or ""
        return Element(name=str(name)[:120], role=_role_name(role), rect=rect,
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
            try:
                win.setHidesOnDeactivate_(False)  # never vanish when you click into another app
            except Exception:  # noqa: BLE001
                pass
            win.orderFrontRegardless()
        except Exception:  # noqa: BLE001
            log.warning("could not raise the overlay window")

    def raise_overlay(self, widget) -> None:
        """Bring Clicky's layer back to the very front (other apps, full-screen apps and display sleep
        can push it back)."""
        if not _real_display():
            return
        try:
            import objc

            win = objc.objc_object(c_void_p=int(widget.winId())).window()
            if not win.isVisible():
                widget.show()
            win.orderFrontRegardless()
        except Exception:  # noqa: BLE001
            pass

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
