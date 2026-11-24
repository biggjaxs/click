"""Pick the platform implementation for this OS."""
from __future__ import annotations

import sys

from .base import Element, Platform, Rect, Window

__all__ = ["Element", "Platform", "Rect", "Window", "get_platform"]


def get_platform() -> Platform:
    if sys.platform == "win32":
        from .windows import WindowsPlatform

        return WindowsPlatform()
    if sys.platform == "darwin":
        from .macos import MacPlatform

        return MacPlatform()
    # Linux is not supported yet; this keeps the app usable for development and tests.
    return Platform()
