"""Click's floating liquid bubble: always in the top-right corner, click it to open or close the chat.

It's a soft, glowing blob whose edge ripples like liquid. Slow and calm when idle, faster and
brighter while Click is thinking, and red-tinted while Click is using your mouse.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPoint, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QWidget

SIZE = 76          # window size (the blob itself is smaller, the rest is glow)
MARGIN = 18


class LiquidBubble(QWidget):
    clicked = Signal()
    moved = Signal()

    def __init__(self, accent: str = "#6d5dfc"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setFixedSize(SIZE, SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click: open or close the chat (Cmd/Ctrl+Shift+Space)")
        self.accent = QColor(accent)
        self.state = "idle"        # idle | thinking | acting | starting | error
        self._t0 = time.time()
        self._press: QPoint | None = None
        self._dragged = False
        self._energy = 0.0         # smoothly follows the state, so changes never jump
        geo = QApplication.primaryScreen().availableGeometry()
        self.move(geo.right() - SIZE - MARGIN + 8, geo.top() + MARGIN - 8)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(33)       # ~30 fps is plenty for a smooth ripple and stays light on the CPU

    def set_state(self, state: str):
        self.state = state

    # ---- drawing ---------------------------------------------------------------------------------------
    def _blob(self, c: QPointF, r: float, t: float, energy: float) -> QPainterPath:
        path = QPainterPath()
        n = 72
        for i in range(n + 1):
            a = 2 * math.pi * i / n
            wobble = (math.sin(3 * a + t * 1.3) * 0.045 + math.sin(5 * a - t * 1.9) * 0.03
                      + math.sin(2 * a + t * 0.7) * 0.035) * (1 + 1.6 * energy)
            rr = r * (1 + wobble)
            pt = QPointF(c.x() + rr * math.cos(a), c.y() + rr * math.sin(a))
            if i == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        path.closeSubpath()
        return path

    def paintEvent(self, _):
        target = {"idle": 0.0, "starting": 0.5, "thinking": 1.0, "acting": 1.0, "error": 0.2}.get(self.state, 0.0)
        self._energy += (target - self._energy) * 0.08
        e = self._energy
        t = (time.time() - self._t0) * (1 + 1.8 * e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(SIZE / 2, SIZE / 2)
        base = QColor("#ef4444") if self.state in ("acting", "error") else self.accent
        second = QColor("#22d3ee") if self.state not in ("acting", "error") else QColor("#f97316")
        breathe = 1 + 0.04 * math.sin(t * 1.6)
        r = SIZE * 0.30 * breathe
        # outer glow
        glow = QRadialGradient(c, SIZE / 2)
        g = QColor(base)
        g.setAlphaF(0.30 + 0.25 * e)
        glow.setColorAt(0.45, g)
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, SIZE / 2, SIZE / 2)
        # liquid body: a moving gradient between the accent and a cool cyan
        hx = c.x() + r * 0.45 * math.cos(t * 0.9)
        hy = c.y() + r * 0.45 * math.sin(t * 1.1)
        body = QRadialGradient(QPointF(hx, hy), r * 1.6)
        light = QColor(second)
        light.setAlphaF(0.95)
        body.setColorAt(0.0, light)
        body.setColorAt(0.55, base)
        dark = QColor(base).darker(160)
        body.setColorAt(1.0, dark)
        p.setBrush(body)
        p.drawPath(self._blob(c, r, t, e))
        # glossy highlight, like light on water
        shine = QRadialGradient(QPointF(c.x() - r * 0.35, c.y() - r * 0.45), r * 0.7)
        shine.setColorAt(0.0, QColor(255, 255, 255, 150))
        shine.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(shine)
        p.drawPath(self._blob(c, r * 0.92, t + 0.6, e))
        # thinking: an orbiting spark
        if e > 0.05:
            a = t * 2.4
            spark = QPointF(c.x() + r * 1.18 * math.cos(a), c.y() + r * 1.18 * math.sin(a))
            s = QColor(255, 255, 255, int(220 * min(1.0, e)))
            p.setBrush(s)
            p.drawEllipse(spark, 2.6, 2.6)
        if self.state == "error":
            p.setPen(QPen(QColor("white"), 2.4, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(c.x(), c.y() - 7), QPointF(c.x(), c.y() + 2))
            p.drawPoint(QPointF(c.x(), c.y() + 7))
        p.end()

    # ---- click to open/close, drag to move ---------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._dragged = False

    def mouseMoveEvent(self, e):
        if self._press is not None:
            new = e.globalPosition().toPoint() - self._press
            if (new - self.pos()).manhattanLength() > 3:
                self._dragged = True
                self.move(new)
                self.moved.emit()

    def mouseReleaseEvent(self, e):
        if self._press is not None and not self._dragged:
            self.clicked.emit()
        self._press = None
