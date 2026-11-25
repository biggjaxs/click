"""Everything you see on the computer: Click's ghost cursor, highlights, indicators, confirmation
popups, the tray icon and the settings window. Runs on the Qt (GUI) thread.

The ghost cursor is a transparent, always-on-top, click-through window per monitor that draws its
own pointer. It never takes over or blocks your real mouse.
"""
from __future__ import annotations

import math
import sys
import time
from concurrent.futures import Future
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QMenu, QMessageBox, QPushButton, QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget,
)


# --------------------------------------------------------------------------------------------------
# Coordinate mapping: screen coordinates (mss / OS input) -> Qt logical coordinates per monitor
# --------------------------------------------------------------------------------------------------
@dataclass
class ScreenMap:
    screen: object          # QScreen
    left: int               # monitor rect in screen coordinates
    top: int
    width: int
    height: int
    scale: float            # screen-coordinate pixels per Qt logical pixel

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x < self.left + self.width and self.top <= y < self.top + self.height

    def to_local(self, x: float, y: float) -> QPointF:
        return QPointF((x - self.left) / self.scale, (y - self.top) / self.scale)


def match_screens(qgeoms: list[tuple[int, int, int, int, float]], mons: list[dict]) -> list[tuple[int, dict, float]]:
    """Pair each Qt screen (x, y, w, h, devicePixelRatio) with the mss monitor it shows.

    Works for macOS (both in points), Windows Qt6 (native top-left, logical size) and plain DPI scaling.
    Returns [(qt_index, monitor, scale)].
    """
    out = []
    for i, (x, y, w, h, dpr) in enumerate(qgeoms):
        cands = [(x, y, w, h), (x, y, round(w * dpr), round(h * dpr)),
                 (round(x * dpr), round(y * dpr), round(w * dpr), round(h * dpr))]

        def dist(m):
            return min(abs(m["left"] - c[0]) + abs(m["top"] - c[1]) + abs(m["width"] - c[2]) + abs(m["height"] - c[3])
                       for c in cands)

        best = min(mons, key=dist) if mons else {"left": x, "top": y, "width": w, "height": h}
        out.append((i, best, best["width"] / w if w else 1.0))
    return out


def _pop() -> None:
    """A soft, quiet "pop" when Clicky lands on something (macOS system sound; silent elsewhere)."""
    if sys.platform != "darwin":
        return
    try:
        import subprocess

        subprocess.Popen(["afplay", "-v", "0.25", "/System/Library/Sounds/Pop.aiff"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass


def _ease(t: float) -> float:
    return 4 * t * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 3) / 2


# --------------------------------------------------------------------------------------------------
# Bridge: thread-safe signals from the network thread to the GUI thread
# --------------------------------------------------------------------------------------------------
@dataclass
class ConfirmRequest:
    title: str
    detail: str
    x: float
    y: float
    high_risk: bool
    future: Future = field(default_factory=Future)


class Bridge(QObject):
    ghostMove = Signal(float, float, str)
    ghostHighlight = Signal(float, float, float, float, str)
    ghostHide = Signal()
    confirmRequest = Signal(object)
    indicator = Signal(str, bool)       # capture | watch | control | paused
    narrate = Signal(str)               # caption explaining what Click is doing right now
    toggleTile = Signal()
    ghostRest = Signal()
    ghostSummon = Signal(str, bool)
    styleChanged = Signal()
    statusChanged = Signal()
    notify = Signal(str)


GLASS = QColor(22, 22, 26, 235)   # dark translucent card used for captions and status
SCALE = 1.5                       # Click's pointer is 1.5x a normal cursor so it's easy to spot


# --------------------------------------------------------------------------------------------------
# The overlay
# --------------------------------------------------------------------------------------------------
class Overlay(QWidget):
    def __init__(self, controller: "GhostController", smap: ScreenMap):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.controller = controller
        self.smap = smap
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setGeometry(smap.screen.geometry())
        self.setWindowTitle("Click overlay")

    def _font(self, size: float, bold: bool = False) -> QFont:
        f = QFont()
        f.setPointSizeF(size)
        f.setWeight(QFont.DemiBold if bold else QFont.Normal)
        return f

    def paintEvent(self, _):
        c = self.controller
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        color = QColor(c.style["color"])
        now = time.time()
        primary = self.smap.screen == QApplication.primaryScreen()
        # highlights
        for h in c.highlights:
            if now > h["until"]:
                continue
            tl = self.smap.to_local(h["x"], h["y"])
            br = self.smap.to_local(h["x"] + h["w"], h["y"] + h["h"])
            rect = QRectF(tl, br).adjusted(-6, -6, 6, 6)
            fill = QColor(color)
            fill.setAlpha(28)
            p.setBrush(fill)
            p.setPen(QPen(color, 2.5))
            p.drawRoundedRect(rect, 10, 10)
            if h["label"]:
                self._card(p, rect.bottomLeft() + QPointF(0, 10), h["label"], color)
        # status (top centre of the primary screen)
        if primary:
            y = 14
            for text, dot in c.active_pills():
                self._status(p, text, QColor(dot), y)
                y += 40
        # the pointer
        here = c.visible and self.smap.contains(c.x, c.y)
        if here:
            pt = self.smap.to_local(c.x, c.y)
            since = now - c.arrived_at
            if 0 <= since < 0.9:  # arrival ripple: "I'm here"
                r = 10 + since * 60
                ring = QColor(color)
                ring.setAlphaF(max(0.0, 0.7 * (1 - since / 0.9)))
                p.setPen(QPen(ring, 3))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(pt, r, r)
            if c.docked and not c._anim:
                p.setOpacity(0.55)
                self._pointer(p, pt, color, c.style["style"])
                p.setOpacity(1.0)
                p.end()
                return
            self._pointer(p, pt, color, c.style["style"])
            name = c.style.get("label") or ""
            if name:
                self._tag(p, pt + QPointF(26 * SCALE, 26 * SCALE), name, color)
            text = c.bubble or c.caption_text()
            if text:
                self._card(p, pt + QPointF(26 * SCALE, 52 * SCALE), text, color)
        elif primary and c.caption_text() and not c.any_pointer_visible():
            # nothing to point at yet: show the caption bottom-centre so you always see what Click is doing
            self._card(p, None, c.caption_text(), color)
        p.end()

    def _pointer(self, p: QPainter, pt: QPointF, color: QColor, style: str):
        glow = QColor(color)
        for i in range(8, 0, -1):
            glow.setAlpha(14)
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawEllipse(pt + QPointF(8, 12), 8 + i * 3.5, 8 + i * 3.5)
        if style == "dot":
            p.setPen(QPen(QColor("white"), 3))
            p.setBrush(color)
            p.drawEllipse(pt, 13, 13)
            return
        if style == "ring":
            p.setPen(QPen(color, 6))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(pt, 17, 17)
            return
        path = QPainterPath(pt)
        for dx, dy in ((0, 26), (7, 19.5), (11.5, 29), (16, 27), (11.5, 17.5), (20.5, 17)):
            path.lineTo(pt + QPointF(dx * SCALE, dy * SCALE))
        path.closeSubpath()
        shadow = QPainterPath(path)
        shadow.translate(1.5, 2.5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawPath(shadow)
        p.setPen(QPen(QColor("white"), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(color)
        p.drawPath(path)

    def _tag(self, p: QPainter, at: QPointF, text: str, color: QColor):
        f = self._font(10, True)
        p.setFont(f)
        fm = QFontMetrics(f)
        r = QRectF(at, at + QPointF(fm.horizontalAdvance(text) + 18, fm.height() + 8))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(QColor("white"))
        p.drawText(r, Qt.AlignCenter, text)

    def _card(self, p: QPainter, at: QPointF | None, text: str, color: QColor):
        """Dark caption card with an accent bar. at=None -> bottom centre of this screen."""
        f = self._font(12)
        p.setFont(f)
        fm = QFontMetrics(f)
        br = fm.boundingRect(0, 0, 340, 1000, Qt.TextWordWrap, text)
        w, h = br.width() + 34, br.height() + 20
        if at is None:
            r = QRectF((self.width() - w) / 2, self.height() - h - 90, w, h)
        else:
            r = QRectF(at.x(), at.y(), w, h)
            if r.right() > self.width() - 10:
                r.moveRight(self.width() - 10)
            if r.bottom() > self.height() - 10:
                r.moveBottom(at.y() - 70)
        p.setPen(Qt.NoPen)
        p.setBrush(GLASS)
        p.drawRoundedRect(r, 12, 12)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(r.x() + 10, r.y() + 10, 3, r.height() - 20), 1.5, 1.5)
        p.setPen(QColor(245, 245, 247))
        p.drawText(r.adjusted(22, 10, -12, -10), Qt.TextWordWrap, text)

    def _status(self, p: QPainter, text: str, dot: QColor, y: int):
        f = self._font(11, True)
        p.setFont(f)
        fm = QFontMetrics(f)
        w = fm.horizontalAdvance(text) + 46
        r = QRectF((self.width() - w) / 2, y, w, 32)
        p.setPen(Qt.NoPen)
        p.setBrush(GLASS)
        p.drawRoundedRect(r, 16, 16)
        p.setBrush(dot)
        p.drawEllipse(QPointF(r.x() + 18, r.center().y()), 4.5, 4.5)
        p.setPen(QColor(245, 245, 247))
        p.drawText(r.adjusted(30, 0, -14, 0), Qt.AlignVCenter | Qt.AlignLeft, text)


class GhostController(QObject):
    """Owns Click's cursor position (in screen coordinates) and animates it across monitors."""

    def __init__(self, bridge: Bridge, settings, platform):
        super().__init__()
        self.bridge, self.settings, self.platform = bridge, settings, platform
        self.x, self.y = 0.0, 0.0
        self.home: tuple[float, float] | None = None   # the cursor's dock, right next to the orb
        self.visible = False
        self.docked = False
        self.bubble = ""
        self.caption = ""
        self.caption_until = 0.0
        self.arrived_at = 0.0
        self.highlights: list[dict] = []
        self.flags = {"capture": 0.0, "watch": False, "control": False, "paused": False}
        self._anim = None
        self._ticks = 0
        self.overlays: list[Overlay] = []
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)
        bridge.ghostMove.connect(self.move_to)
        bridge.ghostHighlight.connect(self.highlight)
        bridge.ghostHide.connect(self.hide)
        bridge.ghostRest.connect(lambda: QTimer.singleShot(4000, self.rest))
        bridge.indicator.connect(self.set_indicator)
        bridge.narrate.connect(self.narrate)
        bridge.styleChanged.connect(self.repaint_all)
        app = QApplication.instance()
        app.screenAdded.connect(lambda *_: self.rebuild())
        app.screenRemoved.connect(lambda *_: self.rebuild())
        self.rebuild()

    @property
    def style(self) -> dict:
        return self.settings["cursor"]

    def rebuild(self):
        from .screen import monitors

        for o in self.overlays:
            o.close()
        self.overlays = []
        screens = QApplication.screens()
        geoms = [(s.geometry().x(), s.geometry().y(), s.geometry().width(), s.geometry().height(), s.devicePixelRatio())
                 for s in screens]
        try:
            mons = monitors()[1:]
        except Exception:  # noqa: BLE001
            mons = []
        for idx, mon, scale in match_screens(geoms, mons):
            smap = ScreenMap(screens[idx], mon["left"], mon["top"], mon["width"], mon["height"], scale)
            o = Overlay(self, smap)
            o.show()
            self.platform.configure_overlay(o)       # above every window, on every Space, click-through
            self.platform.exclude_from_capture(o)
            self.overlays.append(o)
        if mons and self.home is None:
            m = mons[0]
            self.home = (m["left"] + m["width"] - 260, m["top"] + 120)

    def any_pointer_visible(self) -> bool:
        return self.visible and any(o.smap.contains(self.x, self.y) for o in self.overlays)

    def caption_text(self) -> str:
        return self.caption if time.time() < self.caption_until else ""

    def active_pills(self) -> list[tuple[str, str]]:
        pills = []
        if self.flags["paused"]:
            pills.append(("Click is paused", "#94a3b8"))
        if self.flags["control"]:
            pills.append(("Click is using your mouse  ·  Ctrl+Alt+Esc to stop", "#ef4444"))
        if self.flags["watch"]:
            pills.append(("Click is watching your screen", "#ef4444"))
        if time.time() < self.flags["capture"]:
            pills.append(("Click looked at your screen", self.style["color"]))
        return pills

    # ---- slots -------------------------------------------------------------------------------------
    def set_home(self, x: float, y: float):
        self.home = (x, y)
        pass  # the cursor appears from here when Click points at something

    def dock(self):
        """Park the cursor beside the orb, faded, so it's always on screen but out of the way."""
        if not self.home:
            return
        if self.visible and not self.docked:
            self.move_to(*self.home, "")
        else:
            self.x, self.y = self.home
            self.visible = True
        self.docked = True
        self.bubble = ""

    def move_to(self, x: float, y: float, label: str):
        self.last_active = time.time()
        self.docked = False
        self._vanish_after_anim = False
        if self.visible:
            start = (self.x, self.y)
        else:  # appear at the chat tile and travel, so the movement is easy to follow
            start = self.home or (x - 200, y - 140)
        dist = math.hypot(x - start[0], y - start[1])
        duration = max(0.55, min(1.1, 0.35 + dist / 1800))
        self.x, self.y = start
        self._anim = {"from": start, "to": (x, y), "t0": time.time(), "dur": duration}
        self.visible = True
        self.bubble = label or ""
        if label:
            self.caption = ""

    def highlight(self, x: float, y: float, w: float, h: float, label: str):
        self.highlights = [hl for hl in self.highlights if hl["until"] > time.time()][-4:]
        self.highlights.append({"x": x, "y": y, "w": w, "h": h, "label": label, "until": time.time() + 8})

    def narrate(self, text: str):
        self.last_active = time.time()
        self.caption = text
        self.caption_until = time.time() + 6

    def hide(self):
        """Done pointing: glide back to the orb and disappear."""
        self.rest()

    def rest(self):
        self.bubble = ""
        self.caption = ""
        self.highlights = []
        if not self.visible:
            return
        if self.home and math.hypot(self.x - self.home[0], self.y - self.home[1]) < 40:
            self.visible = False  # already home: just disappear
            self.docked = False
            self._anim = None
        elif self.home:
            self.move_to(*self.home, "")
            self._vanish_after_anim = True
        else:
            self.visible = False

    def set_indicator(self, name: str, on: bool):
        if name == "capture":
            self.flags["capture"] = time.time() + 2.5 if on else 0
        else:
            self.flags[name] = on

    def primary_center(self) -> tuple[float, float]:
        m = next((o.smap for o in self.overlays if o.smap.screen == QApplication.primaryScreen()),
                 self.overlays[0].smap if self.overlays else None)
        return (m.left + m.width / 2, m.top + m.height / 2) if m else (400.0, 300.0)

    def summon(self, label: str, center: bool = True):
        """Clicky appears: glides out from the orb to the middle of the screen (or just beside the orb)."""
        if center:
            x, y = self.primary_center()
        else:
            hx, hy = self.home or self.primary_center()
            x, y = hx - 140, hy + 110
        self.move_to(x, y, label)

    def hello(self):
        """A short 'hi' every time Click starts, so you always know Clicky is there."""
        if not self.overlays:
            return
        hx, hy = self.home or self.primary_center()
        QTimer.singleShot(0, lambda: self.move_to(hx - 220, hy + 160, "Hi, I'm Clicky! Ask me where things are."))
        QTimer.singleShot(3200, self.rest)

    def demo(self):
        """"Show me Click's cursor": a short tour so you can check it works, without the AI."""
        mons = [o.smap for o in self.overlays]
        if not mons:
            return
        m = next((o.smap for o in self.overlays if o.smap.screen == QApplication.primaryScreen()), mons[0])
        cx, cy = m.left + m.width / 2, m.top + m.height / 2
        steps = [
            (0, lambda: self.move_to(cx, cy, "Hi! This is Click's cursor. It never takes over your own mouse.")),
            (2600, lambda: self.highlight(cx - 180, cy + 90, 360, 120, "I can highlight areas")),
            (2700, lambda: self.move_to(cx + 150, cy + 150, "...and point at buttons, explaining what they do")),
            (5600, lambda: self.move_to(m.left + 120, m.top + 30, "I'll always tell you what I'm doing")),
            (8600, self.hide),
        ]
        for delay, fn in steps:
            QTimer.singleShot(delay, fn)

    def repaint_all(self):
        for o in self.overlays:
            o.update()

    def _tick(self):
        # 60 fps while moving / rippling / showing a fresh caption; otherwise 4 times a second.
        self._ticks += 1
        lively = self._anim or (time.time() - self.arrived_at < 1.0)
        if not lively and self._ticks % 15:
            return
        if self._anim:
            a = self._anim
            t = min(1.0, (time.time() - a["t0"]) / a["dur"])
            e = _ease(t)
            # fly along a gentle curve (a lifted arc), like a hand moving, not a straight robotic line
            (x0, y0), (x1, y1) = a["from"], a["to"]
            dist = math.hypot(x1 - x0, y1 - y0)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 - min(160.0, dist * 0.22)
            self.x = (1 - e) ** 2 * x0 + 2 * (1 - e) * e * cx + e ** 2 * x1
            self.y = (1 - e) ** 2 * y0 + 2 * (1 - e) * e * cy + e ** 2 * y1
            if t >= 1:
                self._anim = None
                self.arrived_at = time.time()
                if not getattr(self, "_vanish_after_anim", False) and dist > 30:
                    _pop()
                if getattr(self, "_vanish_after_anim", False):
                    self._vanish_after_anim = False
                    self.visible = False
                    self.docked = False
        elif self.visible and time.time() - getattr(self, "last_active", 0) > self.IDLE_HIDE_S \
                and not self._waiting_for_ok():
            self.rest()  # safety net: Clicky never stays parked on screen doing nothing
        self.repaint_all()

    IDLE_HIDE_S = 7.0

    def _waiting_for_ok(self) -> bool:
        return any(isinstance(w, ConfirmPopup) and w.isVisible() for w in QApplication.topLevelWidgets())


# --------------------------------------------------------------------------------------------------
# Confirmation popup near the target
# --------------------------------------------------------------------------------------------------
class ConfirmPopup(QWidget):
    TIMEOUT_S = 60

    def __init__(self, req: ConfirmRequest, controller: GhostController):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.req = req
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setAttribute(Qt.WA_TranslucentBackground)  # rounded card, no square corners
        color = "#dc2626" if req.high_risk else controller.style["color"]
        self.setStyleSheet(f"""
            QWidget#card {{ background: white; border: 2px solid {color}; border-radius: 12px; }}
            QLabel {{ color: #16161d; }} QLabel#title {{ font-weight: 700; font-size: 14px; }}
            QLabel#risk {{ color: white; background: {color}; border-radius: 8px; padding: 2px 8px; font-weight: 700; }}
            QPushButton {{ padding: 6px 12px; border-radius: 8px; border: 1px solid #d4d4dc; background: #f4f4f7; color: #16161d; }}
            QPushButton#allow {{ background: {color}; color: white; border: none; }}
        """)
        card = QWidget(self)
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        name = controller.style.get("label") or "Clicky"
        title = QLabel(f"{name} wants to: {req.title}")
        title.setObjectName("title")
        title.setWordWrap(True)
        lay.addWidget(title)
        if req.high_risk:
            risk = QLabel("High-risk action · asks every time")
            risk.setObjectName("risk")
            lay.addWidget(risk)
        if req.detail:
            d = QLabel(req.detail)
            d.setWordWrap(True)
            lay.addWidget(d)
        self.countdown = QLabel()
        lay.addWidget(self.countdown)
        row = QHBoxLayout()
        allow = QPushButton("Allow once")
        allow.setObjectName("allow")
        allow.clicked.connect(lambda: self.finish("once"))
        row.addWidget(allow)
        if not req.high_risk:
            task = QPushButton("Allow for this task")
            task.clicked.connect(lambda: self.finish("task"))
            row.addWidget(task)
        deny = QPushButton("Deny")
        deny.clicked.connect(lambda: self.finish("deny"))
        row.addWidget(deny)
        lay.addLayout(row)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        self.setFixedWidth(380)
        self.adjustSize()
        self._place(controller)
        self.t0 = time.time()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(250)
        self._tick()

    def _place(self, controller: GhostController):
        for o in controller.overlays:
            if o.smap.contains(self.req.x, self.req.y):
                local = o.smap.to_local(self.req.x, self.req.y)
                g = o.smap.screen.geometry()
                x = g.x() + local.x() + 30
                y = g.y() + local.y() + 105  # below the cursor and its label bubble
                x = min(max(g.x() + 8, x), g.x() + g.width() - self.width() - 8)
                if y + self.height() > g.y() + g.height() - 8:
                    y = g.y() + local.y() - self.height() - 30
                self.move(int(x), int(max(g.y() + 8, y)))
                return
        geo = QApplication.primaryScreen().availableGeometry()
        self.move(geo.center().x() - self.width() // 2, geo.center().y() - self.height() // 2)

    def _tick(self):
        left = self.TIMEOUT_S - int(time.time() - self.t0)
        self.countdown.setText(f"<small>Denies automatically in {left} s · Esc = Deny</small>")
        if left <= 0:
            self.finish("deny")

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.finish("deny")

    def finish(self, decision: str):
        if not self.req.future.done():
            self.req.future.set_result(decision)
        self.close()


# --------------------------------------------------------------------------------------------------
# Tray icon and settings
# --------------------------------------------------------------------------------------------------
def make_icon(color: str, alert: bool = False) -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#dc2626" if alert else color))
    p.drawRoundedRect(2, 2, 60, 60, 16, 16)
    path = QPainterPath(QPointF(22, 13))
    for x, y in ((22, 45), (30.5, 37), (36, 49), (41, 46.5), (35.5, 35), (46, 34.5)):
        path.lineTo(x, y)
    path.closeSubpath()
    p.setBrush(QColor("white"))
    p.drawPath(path)
    p.end()
    return QIcon(pm)


class SettingsDialog(QDialog):
    def __init__(self, app_ctl):
        super().__init__()
        self.app_ctl = app_ctl
        s = app_ctl.settings
        self.setWindowTitle("Click Companion settings")
        self.resize(560, 480)
        tabs = QTabWidget()

        # General
        gen = QWidget()
        form = QFormLayout(gen)
        info = app_ctl.hardware
        models = app_ctl.models
        form.addRow("This computer:", QLabel(f"{info['os']} · {info['ram_gb']} GB RAM · {info.get('gpu') or 'no GPU'}"))
        form.addRow("Chat model:", QLabel(models["chat"]))
        form.addRow("Vision model:", QLabel(models["vision"]))
        form.addRow("Images:", QLabel(models["image_quality"] if models["image_enabled"] else "off"))
        paired = bool(s["pi_cert_pem"])
        form.addRow("Click box:", QLabel(("Paired · " + s["pi_host"]) if paired else "Not paired yet"))
        self.pi_host = QLineEdit(s["pi_host"])
        form.addRow("Click box address:", self.pi_host)
        btns = QHBoxLayout()
        pair = QPushButton("Pair with Click…")
        pair.clicked.connect(lambda: (self.accept(), app_ctl.pair()))
        btns.addWidget(pair)
        if paired:
            unpair = QPushButton("Unpair")
            unpair.clicked.connect(self._unpair)
            btns.addWidget(unpair)
        form.addRow(btns)
        form.addRow(QLabel("<small>Kill switch: <b>Ctrl+Alt+Esc</b> stops everything instantly.</small>"))
        tabs.addTab(gen, "General")

        # Permissions
        perm = QWidget()
        pl = QVBoxLayout(perm)
        pl.addWidget(QLabel("This computer refuses these even if the Click box asks:"))
        labels = {"screen": "Let Click look at the screen", "cursor": "Show Click's own cursor",
                  "actions": "Let Click click and type (asks every time)", "files": "Let Click read approved folders",
                  "file_write": "Let Click rename/move files (asks every time)", "image": "Let Click make images on this GPU"}
        self.perm_boxes = {}
        for k, text in labels.items():
            cb = QCheckBox(text)
            cb.setChecked(bool(s["permissions"].get(k, True)))
            self.perm_boxes[k] = cb
            pl.addWidget(cb)
        pl.addStretch()
        tabs.addTab(perm, "Permissions")

        # Folders
        fol = QWidget()
        fl = QVBoxLayout(fol)
        fl.addWidget(QLabel("Click can only read files inside these folders (and asks before each look):"))
        self.folders = QListWidget()
        self.folders.addItems(s["approved_folders"])
        fl.addWidget(self.folders)
        fb = QHBoxLayout()
        add = QPushButton("Add folder…")
        add.clicked.connect(self._add_folder)
        rem = QPushButton("Remove")
        rem.clicked.connect(lambda: [self.folders.takeItem(self.folders.row(i)) for i in self.folders.selectedItems()])
        fb.addWidget(add)
        fb.addWidget(rem)
        fl.addLayout(fb)
        tabs.addTab(fol, "Folders")

        # Blocklist
        bl = QWidget()
        bll = QVBoxLayout(bl)
        bll.addWidget(QLabel("Click never looks at or acts in windows matching these words\n"
                             "(they're blacked out in screenshots):"))
        self.block = QListWidget()
        self.block.addItems(s["blocklist"])
        bll.addWidget(self.block)
        bb = QHBoxLayout()
        badd = QPushButton("Add…")
        badd.clicked.connect(self._add_block)
        brem = QPushButton("Remove")
        brem.clicked.connect(lambda: [self.block.takeItem(self.block.row(i)) for i in self.block.selectedItems()])
        bb.addWidget(badd)
        bb.addWidget(brem)
        bll.addLayout(bb)
        tabs.addTab(bl, "Blocklist")

        # Models
        mo = QWidget()
        mf = QFormLayout(mo)
        self.chat_model = QLineEdit(s["models"].get("chat") or "")
        self.chat_model.setPlaceholderText(f"automatic ({models['chat']})")
        self.vision_model = QLineEdit(s["models"].get("vision") or "")
        self.vision_model.setPlaceholderText(f"automatic ({models['vision']})")
        self.img_enabled = QCheckBox("Make images on this computer")
        self.img_enabled.setChecked(models["image_enabled"])
        self.draft_model = QLineEdit(s["image"].get("draft_model") or "")
        self.draft_model.setPlaceholderText(f"automatic ({models['image_draft']})")
        self.quality_model = QLineEdit(s["image"].get("quality_model") or "")
        self.quality_model.setPlaceholderText(f"automatic ({models['image_quality']})")
        self.safety = QCheckBox("Image safety filter (recommended)")
        self.safety.setChecked(s["image"].get("safety_filter", True))
        mf.addRow("Chat model (Ollama):", self.chat_model)
        mf.addRow("Vision model (Ollama):", self.vision_model)
        mf.addRow(self.img_enabled)
        mf.addRow("Fast image model:", self.draft_model)
        mf.addRow("Quality image model:", self.quality_model)
        mf.addRow(self.safety)
        mf.addRow(QLabel("<small>Changes to models take effect after restarting the Companion.</small>"))
        tabs.addTab(mo, "Models")

        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(box)

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder Click may read")
        if d:
            self.folders.addItem(d)

    def _add_block(self):
        text, ok = QInputDialog.getText(self, "Add to blocklist", "App name or word in the window title:")
        if ok and text.strip():
            self.block.addItem(text.strip())

    def _unpair(self):
        if QMessageBox.question(self, "Unpair", "Unpair from the Click box?") == QMessageBox.Yes:
            self.app_ctl.unpair()
            self.accept()

    def _save(self):
        s = self.app_ctl.settings
        s.set("pi_host", self.pi_host.text().strip() or "click.local")
        s.set("permissions", {k: cb.isChecked() for k, cb in self.perm_boxes.items()})
        s.set("approved_folders", [self.folders.item(i).text() for i in range(self.folders.count())])
        s.set("blocklist", [self.block.item(i).text() for i in range(self.block.count())])
        s.update("models", chat=self.chat_model.text().strip() or None, vision=self.vision_model.text().strip() or None)
        s.update("image", enabled=self.img_enabled.isChecked(), draft_model=self.draft_model.text().strip() or None,
                 quality_model=self.quality_model.text().strip() or None, safety_filter=self.safety.isChecked())
        self.app_ctl.settings_changed()
        self.accept()


class Tray(QObject):
    def __init__(self, app_ctl):
        super().__init__()
        self.app_ctl = app_ctl
        color = app_ctl.settings["cursor"]["color"]
        self.icon = QSystemTrayIcon(make_icon(color))
        self.icon.setToolTip("Click")
        self.icon.activated.connect(self._activated)
        menu = QMenu()
        from . import __version__

        self.version_action = QAction(f"Click version {__version__}")
        self.version_action.setEnabled(False)
        menu.addAction(self.version_action)
        self.status_action = QAction("Starting…")
        self.status_action.setEnabled(False)
        menu.addAction(self.status_action)
        menu.addSeparator()
        show = QAction("Show Click")
        show.triggered.connect(lambda: app_ctl.tile and app_ctl.tile.show_tile())
        menu.addAction(show)
        demo = QAction("Show me Clicky (tutorial)")
        demo.triggered.connect(app_ctl.ghost.demo)
        menu.addAction(demo)
        check = QAction("Check Clicky…")
        check.triggered.connect(app_ctl.check_clicky)
        menu.addAction(check)
        self.speak_action = QAction("Read answers aloud")
        self.speak_action.setCheckable(True)
        self.speak_action.setChecked(bool(app_ctl.settings.get("speak_answers", False)))
        self.speak_action.toggled.connect(lambda on: app_ctl.settings.set("speak_answers", bool(on)))
        menu.addAction(self.speak_action)
        welcome = QAction("Show the welcome again")
        welcome.triggered.connect(lambda: (app_ctl.settings.set("welcome_version", ""), app_ctl._onboard()))
        menu.addAction(welcome)
        menu.addSeparator()
        self.pause_action = QAction("Pause everything")
        self.pause_action.triggered.connect(app_ctl.toggle_pause)
        menu.addAction(self.pause_action)
        pair = QAction("Use a Click box (Raspberry Pi)…")
        pair.triggered.connect(app_ctl.pair)
        menu.addAction(pair)
        local = QAction("Run Click on this computer")
        local.triggered.connect(app_ctl.use_this_computer)
        menu.addAction(local)
        settings = QAction("Settings…")
        settings.triggered.connect(app_ctl.open_settings)
        menu.addAction(settings)
        menu.addSeparator()
        quit_action = QAction("Quit Click")
        quit_action.triggered.connect(app_ctl.quit)
        menu.addAction(quit_action)
        self.menu = menu
        self.icon.setContextMenu(menu)
        self.icon.show()
        self._alert_until = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)

    def _activated(self, reason):
        if reason == QSystemTrayIcon.Trigger and sys.platform != "darwin" and self.app_ctl.tile:
            self.app_ctl.tile.toggle()

    def alert(self, seconds: float = 2.5):
        self._alert_until = time.time() + seconds

    def refresh(self):
        ctl = self.app_ctl
        flags = ctl.ghost.flags
        alert = time.time() < self._alert_until or flags["control"] or flags["watch"]
        self.icon.setIcon(make_icon(ctl.settings["cursor"]["color"], alert))
        if ctl.paused:
            status = "Paused"
        elif ctl.server.pi_connected:
            status = "Running on this computer" if ctl.mode == "standalone" else "Connected to your Click box"
        elif ctl.mode == "box":
            status = "Waiting for your Click box…"
        else:
            status = "Starting…"
        self.status_action.setText(status)
        self.pause_action.setText("Resume everything" if ctl.paused else "Pause everything")
        self.icon.setToolTip(f"Click: {status}")
