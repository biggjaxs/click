"""OS abstraction: windows, accessibility tree and focus checks.

All coordinates are "screen coordinates" as used by mss and the OS input APIs:
physical pixels on Windows (the process is DPI-aware), points on macOS.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    def as_list(self) -> list[int]:
        return [self.x, self.y, self.w, self.h]


@dataclass
class Window:
    handle: int
    title: str
    app: str
    rect: Rect
    pid: int = 0
    focused: bool = False

    def public(self) -> dict:
        d = asdict(self)
        d["rect"] = self.rect.as_list()
        d.pop("handle", None)
        return d


@dataclass
class Element:
    name: str
    role: str
    rect: Rect
    value: str = ""
    is_password: bool = False
    enabled: bool = True
    window_title: str = ""
    extra: dict = field(default_factory=dict)


class Platform:
    name = "generic"

    def list_windows(self) -> list[Window]:
        """Visible top-level windows, top of the stack first."""
        return []

    def active_window(self) -> Window | None:
        wins = self.list_windows()
        return next((w for w in wins if w.focused), wins[0] if wins else None)

    def window_at(self, x: float, y: float) -> Window | None:
        for w in self.list_windows():
            if w.rect.contains(x, y):
                return w
        return None

    def ui_elements(self, window: Window, max_elements: int = 300) -> list[Element]:
        return []

    def element_at(self, x: float, y: float) -> Element | None:
        return None

    def desktop_items(self) -> list[Element]:
        """Icons on the desktop (files, folders, drives)."""
        return []

    def dock_items(self) -> list[Element]:
        """App icons in the Dock / taskbar."""
        return []

    def menu_bar_items(self) -> list[Element]:
        """Items in the system menu bar / tray (Wi-Fi, battery, the app's File/Edit menus...)."""
        return []

    def focused_is_sensitive(self) -> bool:
        """True if the keyboard focus is in a password / secure field."""
        return False

    def configure_overlay(self, widget, click_through: bool = True) -> None:
        """Make a Click window float above all apps (and full-screen apps); optionally let clicks pass through."""

    def exclude_from_capture(self, widget) -> None:
        """Hide Click's own overlay windows from screenshots."""

    def check_permissions(self) -> list[str]:
        """Return human-readable problems (e.g. missing macOS permissions)."""
        return []

    def request_permissions(self) -> None:
        pass
