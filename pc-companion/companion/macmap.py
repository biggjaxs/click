"""Clicky's built-in map of a Mac: where things live, even when it can't "see" the screen.

Every Mac lays out the same way: the Apple menu top-left, status icons (Wi-Fi, battery, Control
Center, clock) top-right, the Dock with Finder first and Trash last, the red/yellow/green buttons at
the top-left of every window. This module works out WHERE those are on this Mac from things that
need no permission at all: the screen size, the menu bar and Dock size (NSScreen visibleFrame), and
the Dock's own settings (which apps are pinned, icon size, which side of the screen).

The screen finder (screen.py) uses the precise Accessibility answer when it has one, and this map
when it doesn't, so "where is Wi-Fi / the Trash / Bluetooth / System Settings" always gets an answer
and Clicky always points somewhere sensible, with a sentence explaining what to do.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .platforms import Element, Rect


@dataclass
class MacGeometry:
    """What Clicky knows about this Mac's layout (points, top-left origin, main screen)."""
    screen: Rect                       # the whole main screen
    menu_bar_h: int = 24               # 24, or about 37 on Macs with a notch
    dock_side: str = "bottom"          # bottom / left / right
    dock_thickness: int = 0            # 0..4 when the Dock hides itself
    tilesize: int = 48
    dock_apps: list[str] = field(default_factory=list)    # pinned apps in order (Finder not included)
    dock_middle: list[str] = field(default_factory=list)  # running/recent apps that aren't pinned
    dock_others: list[str] = field(default_factory=list)  # folders on the right (Downloads...)
    window: Rect | None = None         # the front window, for its close/minimise buttons
    window_app: str = ""


# ---- what a Mac looks like: name -> (words people use, where it is, what to say) --------------------------
# where: a locator below. say: Clicky's sentence (also shown next to the cursor).
LANDMARKS: dict[str, tuple[list[str], str, str]] = {
    "apple menu": (["apple menu", "apple logo", "apple icon", "apple", "the apple"], "apple",
                   "This is the Apple menu. Click it for System Settings, About This Mac, Restart and Shut Down."),
    "system settings": (["system settings", "settings", "system preferences", "preferences", "mac settings",
                         "computer settings"], "dock:System Settings|apple",
                        "System Settings is in the Apple menu: click the Apple logo, then System Settings. "
                        "(Or just ask me to “open System Settings”.)"),
    "about this mac": (["about this mac", "mac info", "what mac do i have", "macos version"], "apple",
                       "Click the Apple menu, then About This Mac."),
    "shut down": (["shut down", "shutdown", "power off", "turn off", "turn off my mac", "restart", "reboot",
                   "sleep", "log out", "lock screen", "lock"], "apple",
                  "Click the Apple menu: Sleep, Restart, Shut Down, Lock Screen and Log Out are at the bottom."),
    "force quit": (["force quit", "force quit menu", "frozen app"], "apple",
                   "Click the Apple menu, then Force Quit (or press Cmd + Option + Esc)."),
    "app store": (["app store"], "dock:App Store|apple",
                  "Here's the App Store. It's also in the Apple menu."),
    "menu bar": (["menu bar", "top bar", "status bar"], "menubar",
                 "This is the menu bar. The left side has the menus of the app you're using; the right side "
                 "has Wi‑Fi, battery, Control Center and the clock."),
    "wi-fi": (["wi-fi", "wifi", "wi fi", "wireless", "internet", "wifi icon", "wi-fi icon", "internet connection"],
              "wifi", "Wi‑Fi is the fan‑shaped icon in the top‑right of the menu bar. If it isn't there, "
              "it's inside Control Center (the two little switches)."),
    "battery": (["battery", "battery icon", "battery percentage", "charge"], "battery",
                "The battery icon is in the top‑right of the menu bar. Click it for battery details."),
    "control center": (["control center", "control centre", "quick settings", "toggles"], "cc",
                       "This is Control Center (the icon with two little switches). Wi‑Fi, Bluetooth, "
                       "sound, brightness, AirDrop and Focus are all in here."),
    "bluetooth": (["bluetooth", "airpods", "headphones"], "cc",
                  "Bluetooth is in Control Center: click this icon (two little switches), then Bluetooth."),
    "sound": (["sound", "volume", "volume slider", "speaker", "audio"], "cc",
              "Volume is in Control Center: click this icon, then use the Sound slider."),
    "brightness": (["brightness", "display brightness", "screen brightness", "display"], "cc",
                   "Brightness is in Control Center: click this icon, then use the Display slider."),
    "airdrop": (["airdrop", "air drop"], "cc", "AirDrop is in Control Center: click this icon, then AirDrop."),
    "focus": (["focus", "do not disturb", "dnd"], "cc", "Focus (Do Not Disturb) is in Control Center: click this icon."),
    "screen mirroring": (["screen mirroring", "airplay", "mirror my screen"], "cc",
                         "Screen Mirroring is in Control Center: click this icon."),
    "spotlight": (["spotlight", "search", "spotlight search", "search bar", "magnifying glass"], "spotlight",
                  "This is Spotlight: it finds any app or file. Click the magnifying glass or press Cmd + Space."),
    "clock": (["clock", "time", "date", "date and time", "calendar date"], "clock",
              "The date and time are in the top‑right corner. Click them for notifications and widgets."),
    "notification center": (["notification center", "notifications", "notification centre", "widgets"], "clock",
                            "Click the date and time in the top‑right corner to open Notification Center."),
    "siri": (["siri"], "cc", "Ask Siri from Control Center or by holding Cmd + Space."),
    "dock": (["dock", "the dock", "app bar", "taskbar"], "dock",
             "This is the Dock: your apps. Click an icon to open it."),
    "finder": (["finder", "file explorer", "files", "my files", "finder icon", "documents", "documents folder",
                "applications", "applications folder", "my apps folder"], "dock:Finder",
               "This is Finder (the blue smiling face): it opens your files. Documents and Applications are in "
               "its sidebar on the left."),
    "trash": (["trash", "trash can", "bin", "recycle bin", "garbage", "deleted files"], "dock:Trash",
              "The Trash is at the far end of the Dock. Drag things here to delete them."),
    "downloads": (["downloads", "downloads folder", "my downloads", "download folder"], "dock:Downloads",
                  "Your Downloads folder is near the Trash at the end of the Dock."),
    "launchpad": (["launchpad", "all apps", "all my apps", "app list", "apps"], "dock:Launchpad|dock:Apps|spotlight",
                  "This shows all your apps. You can also press Cmd + Space and type an app's name."),
    "close button": (["close button", "red button", "close window", "x button", "the x"], "win:close",
                     "The red button at the top‑left of the window closes it."),
    "minimize button": (["minimize button", "minimise button", "yellow button", "minimize", "minimise"], "win:min",
                        "The yellow button at the top‑left of the window puts it in the Dock."),
    "full screen button": (["full screen button", "green button", "full screen", "fullscreen", "maximize",
                            "maximise", "zoom button"], "win:full",
                           "The green button at the top‑left of the window makes it full screen."),
}

_FILLER = re.compile(r"^(?:the|my|your|a|an)\s+|\s+(?:icon|button|app|menu|thing|on my mac|on the mac|on my screen|"
                     r"on the screen|on my computer|on my laptop|located|at)$")


def _clean(q: str) -> str:
    q = q.lower().replace("‑", "-").strip(" ?.!\"'")
    for _ in range(3):
        q = _FILLER.sub("", q).strip()
    return q


def match(query: str) -> str | None:
    """The landmark a question is about ("where's my wifi icon" -> "wi-fi"), or None. Exact words only,
    so ordinary things ("my budget folder") are never hijacked."""
    raw = query.lower().replace("‑", "-").strip(" ?.!\"'")
    q = _clean(query)
    for key, (words, _, _) in LANDMARKS.items():
        if q in words or raw in words or q == key:
            return key
    return None


# ---- locators -----------------------------------------------------------------------------------------------
def _dock_slots(g: MacGeometry) -> list[tuple[str, Rect]]:
    """Where each Dock icon is, worked out from the Dock's settings (no permission needed)."""
    names = ["Finder"] + [a for a in g.dock_apps if a != "Finder"]
    middle = [a for a in g.dock_middle if a not in names]
    seq: list[str | None] = names + ([None] + middle if middle else []) + [None] + g.dock_others + ["Trash"]
    pitch = g.tilesize * 1.1
    sep = g.tilesize * 0.45
    total = sum(sep if n is None else pitch for n in seq) + g.tilesize * 0.3
    thick = max(g.dock_thickness, int(g.tilesize * 1.3))
    s = g.screen
    out = []
    if g.dock_side in ("left", "right"):
        pos = s.y + (s.h - total) / 2 + g.tilesize * 0.15
        x = s.x if g.dock_side == "left" else s.x + s.w - thick
        for n in seq:
            if n is None:
                pos += sep
                continue
            out.append((n, Rect(int(x), int(pos), int(thick), int(pitch))))
            pos += pitch
    else:
        pos = s.x + (s.w - total) / 2 + g.tilesize * 0.15
        y = s.y + s.h - thick
        for n in seq:
            if n is None:
                pos += sep
                continue
            out.append((n, Rect(int(pos), int(y), int(pitch), int(thick))))
            pos += pitch
    return out


def dock_slot(g: MacGeometry, name: str) -> Rect | None:
    n = name.lower()
    for label, r in _dock_slots(g):
        if label.lower() == n:
            return r
    for label, r in _dock_slots(g):
        if n in label.lower() or label.lower() in n:
            return r
    return None


def _right(g: MacGeometry, from_right: int, w: int) -> Rect:
    """An icon in the menu bar, counted from the right edge (clock, Control Center, Spotlight, Wi-Fi...)."""
    s = g.screen
    return Rect(s.x + s.w - from_right, s.y, w, g.menu_bar_h)


def _locate_one(where: str, g: MacGeometry) -> tuple[Rect, str] | None:
    s = g.screen
    if where == "apple":
        return Rect(s.x + 8, s.y, 30, g.menu_bar_h), "Menu bar"
    if where == "menubar":
        return Rect(s.x, s.y, s.w, g.menu_bar_h), "Menu bar"
    if where == "clock":
        return _right(g, 150, 140), "Menu bar"
    if where == "cc":
        return _right(g, 190, 32), "Menu bar"
    if where == "spotlight":
        return _right(g, 226, 32), "Menu bar"
    if where == "wifi":
        return _right(g, 262, 32), "Menu bar"
    if where == "battery":
        return _right(g, 310, 44), "Menu bar"
    if where == "dock":
        slots = _dock_slots(g)
        if not slots:
            return None
        xs = [r.x for _, r in slots] + [r.x + r.w for _, r in slots]
        ys = [r.y for _, r in slots] + [r.y + r.h for _, r in slots]
        return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)), "Dock"
    if where.startswith("dock:"):
        r = dock_slot(g, where[5:])
        return (r, "Dock") if r else None
    if where.startswith("win:"):
        if not g.window:
            return None
        dx = {"close": 20, "min": 40, "full": 60}[where[4:]]
        return Rect(g.window.x + dx - 7, g.window.y + 9, 14, 14), g.window_app or "Window"
    return None


def dock_app(query: str, g: MacGeometry) -> Element | None:
    """Any app in the Dock (pinned or running), found from the Dock's own settings: no permission needed."""
    q = _clean(query).removesuffix(" app").strip()
    if not q:
        return None
    for label, r in _dock_slots(g):
        lab = label.lower()
        if lab == q or (len(q) >= 4 and (lab.startswith(q) or q in lab.split())):
            el = Element(name=label, role="DockItem", rect=r, window_title="Dock")
            el.extra["note"] = f"Here's {label} in your Dock."
            el.extra["from_map"] = True
            return el
    return None


def locate(query: str, g: MacGeometry) -> Element | None:
    """Clicky's best idea of where `query` is on this Mac, as an Element with a note, or None."""
    key = match(query)
    if not key:
        return None
    _, where, say = LANDMARKS[key]
    for option in where.split("|"):
        hit = _locate_one(option, g)
        if hit:
            rect, area = hit
            el = Element(name=key.title() if key != "wi-fi" else "Wi‑Fi", role="Landmark", rect=rect,
                         window_title=area)
            el.extra["note"] = say
            el.extra["from_map"] = True
            if g.dock_thickness <= 4 and area == "Dock":
                el.extra["note"] = say + " (Your Dock is hidden: move your mouse to the edge of the screen to show it.)"
            return el
    return None


# ---- reading this Mac's layout (macOS only; no permissions needed) ------------------------------------------
def mac_geometry(active_window=None) -> MacGeometry | None:
    try:
        from AppKit import NSApplicationActivationPolicyRegular, NSScreen, NSWorkspace
        from Foundation import CFPreferencesAppSynchronize, CFPreferencesCopyAppValue
    except Exception:  # noqa: BLE001 - not a Mac
        return None
    screens = NSScreen.screens()
    if not screens:
        return None
    main = screens[0]  # the screen with the menu bar
    f, v = main.frame(), main.visibleFrame()
    full_h = f.size.height
    screen = Rect(int(f.origin.x), 0, int(f.size.width), int(full_h))
    vis_top = full_h - (v.origin.y + v.size.height)
    bottom = v.origin.y - f.origin.y
    left = v.origin.x - f.origin.x
    right = (f.origin.x + f.size.width) - (v.origin.x + v.size.width)
    CFPreferencesAppSynchronize("com.apple.dock")

    def pref(k, default=None):
        val = CFPreferencesCopyAppValue(k, "com.apple.dock")
        return default if val is None else val

    side = str(pref("orientation", "bottom"))
    thickness = {"bottom": bottom, "left": left, "right": right}.get(side, bottom)

    def labels(key):
        out = []
        for item in pref(key, []) or []:
            try:
                lab = item["tile-data"]["file-label"]
                if lab:
                    out.append(str(lab))
            except Exception:  # noqa: BLE001
                continue
        return out

    pinned = labels("persistent-apps")
    middle: list[str] = []
    try:
        for a in NSWorkspace.sharedWorkspace().runningApplications():
            if a.activationPolicy() == NSApplicationActivationPolicyRegular:
                n = str(a.localizedName() or "")
                if n and n not in pinned and n not in ("Finder", "Click") and n not in middle:
                    middle.append(n)
    except Exception:  # noqa: BLE001
        pass
    if bool(pref("show-recents", True)):
        for n in labels("recent-apps")[:3]:
            if n not in pinned and n not in middle:
                middle.append(n)
    g = MacGeometry(screen=screen, menu_bar_h=int(max(22, min(44, vis_top or 24))), dock_side=side,
                    dock_thickness=int(thickness), tilesize=int(pref("tilesize", 48) or 48), dock_apps=pinned,
                    dock_middle=middle, dock_others=labels("persistent-others"))
    if active_window is not None:
        g.window, g.window_app = active_window.rect, active_window.app
    return g
