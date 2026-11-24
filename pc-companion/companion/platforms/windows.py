"""Windows implementation: Win32 for windows, UI Automation (pywinauto) for the accessibility tree."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging

from .base import Element, Platform, Rect, Window

log = logging.getLogger("click.platform.windows")

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
WDA_EXCLUDEFROMCAPTURE = 0x11
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080

# Make sure coordinates are physical pixels on every monitor (Qt also does this).
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001 - already set, or older Windows
    try:
        user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def _process_name(pid: int) -> str:
    try:
        import psutil

        return psutil.Process(pid).name().removesuffix(".exe")
    except Exception:  # noqa: BLE001
        return ""


def _rect(hwnd: int) -> Rect | None:
    r = wt.RECT()
    if dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        if not user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r)):
            return None
    return Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)


def _cloaked(hwnd: int) -> bool:
    val = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val))
    return val.value != 0


class WindowsPlatform(Platform):
    name = "windows"

    def list_windows(self) -> list[Window]:
        out: list[Window] = []
        fg = user32.GetForegroundWindow()
        own_pid = ctypes.windll.kernel32.GetCurrentProcessId()

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def cb(hwnd, _):
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or _cloaked(hwnd):
                return True
            if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == own_pid:
                return True
            rect = _rect(hwnd)
            if not rect or rect.w < 40 or rect.h < 30:
                return True
            out.append(Window(int(hwnd), buf.value, _process_name(pid.value), rect, pid.value, hwnd == fg))
            return True

        user32.EnumWindows(cb, 0)
        return out  # EnumWindows walks top of the Z-order first

    def active_window(self) -> Window | None:
        fg = user32.GetForegroundWindow()
        for w in self.list_windows():
            if w.handle == fg:
                return w
        return super().active_window()

    def ui_elements(self, window: Window, max_elements: int = 300) -> list[Element]:
        from pywinauto.uia_element_info import UIAElementInfo

        root = UIAElementInfo(window.handle)
        out: list[Element] = []
        stack = [(root, 0)]
        while stack and len(out) < max_elements:
            info, depth = stack.pop()
            try:
                children = info.children()
            except Exception:  # noqa: BLE001
                children = []
            for child in reversed(children):
                if depth < 25:
                    stack.append((child, depth + 1))
            if info is root:
                continue
            try:
                r = info.rectangle
                if r.width() <= 1 or r.height() <= 1 or not info.visible:
                    continue
                name = (info.name or "").strip()
                role = info.control_type or ""
                if not name and role not in ("Edit", "Button", "CheckBox", "ComboBox", "MenuItem", "TabItem"):
                    continue
                is_pw = bool(getattr(info.element, "CurrentIsPassword", False))
                out.append(Element(name=name[:120], role=role, rect=Rect(r.left, r.top, r.width(), r.height()),
                                   is_password=is_pw, enabled=bool(info.enabled), window_title=window.title))
            except Exception:  # noqa: BLE001 - elements can disappear while we walk
                continue
        return out

    def element_at(self, x: float, y: float) -> Element | None:
        from pywinauto.uia_element_info import UIAElementInfo

        try:
            info = UIAElementInfo.from_point(int(x), int(y))
            r = info.rectangle
            return Element(name=(info.name or "")[:120], role=info.control_type or "",
                           rect=Rect(r.left, r.top, r.width(), r.height()),
                           is_password=bool(getattr(info.element, "CurrentIsPassword", False)))
        except Exception:  # noqa: BLE001
            return None

    def focused_is_sensitive(self) -> bool:
        try:
            from pywinauto.uia_defines import IUIA

            el = IUIA().iuia.GetFocusedElement()
            if el is None:
                return False
            if el.CurrentIsPassword:
                return True
            name = (el.CurrentName or "").lower()
            return any(k in name for k in ("password", "passcode", "one-time", "otp", "verification code", "pin"))
        except Exception:  # noqa: BLE001 - if we can't tell, be safe
            return True

    def configure_overlay(self, widget) -> None:
        HWND_TOPMOST, SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = -1, 0x2, 0x1, 0x10
        try:
            user32.SetWindowPos(wt.HWND(int(widget.winId())), wt.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception:  # noqa: BLE001
            pass

    def exclude_from_capture(self, widget) -> None:
        try:
            user32.SetWindowDisplayAffinity(wt.HWND(int(widget.winId())), WDA_EXCLUDEFROMCAPTURE)
        except Exception:  # noqa: BLE001 - Windows 10 before 2004
            log.info("could not exclude overlay from capture")
