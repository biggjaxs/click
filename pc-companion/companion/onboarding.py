"""First-launch welcome: a short guided introduction, like a regular app.

1. Meet Click        (animated orb)
2. Your own cursor   (live demo)
3. Permissions       (Mac only; buttons open the right settings)
4. Getting ready     (downloads with progress; runs in the background from the first page)
5. You're ready      (how to use it) -> the orb and chat appear
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QRadialGradient
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QStackedLayout, QVBoxLayout, QWidget,
)

TEXT = "color: #f4f4f5;"
SUB = "color: rgba(244,244,245,0.62); font-size: 13.5px;"


class Orb(QWidget):
    """A large version of the liquid orb, for the welcome page."""

    def __init__(self, size: int, accent: str):
        super().__init__()
        self.setFixedSize(size, size)
        self.accent = QColor(accent)
        self.t0 = time.time()
        t = QTimer(self)
        t.timeout.connect(self.update)
        t.start(33)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self.width()
        t = time.time() - self.t0
        c = QPointF(s / 2, s / 2)
        r = s * 0.30 * (1 + 0.035 * math.sin(t * 1.4))
        glow = QRadialGradient(c, s / 2)
        g = QColor(self.accent)
        g.setAlphaF(0.35)
        glow.setColorAt(0.45, g)
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, s / 2, s / 2)
        path = QPainterPath()
        for i in range(97):
            a = 2 * math.pi * i / 96
            w = 1 + math.sin(3 * a + t * 1.2) * 0.05 + math.sin(5 * a - t * 1.7) * 0.03
            pt = QPointF(c.x() + r * w * math.cos(a), c.y() + r * w * math.sin(a))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        body = QRadialGradient(QPointF(c.x() + r * 0.4 * math.cos(t), c.y() + r * 0.4 * math.sin(t * 1.1)), r * 1.6)
        body.setColorAt(0, QColor("#22d3ee"))
        body.setColorAt(0.55, self.accent)
        body.setColorAt(1, self.accent.darker(170))
        p.setBrush(body)
        p.drawPath(path)
        shine = QRadialGradient(QPointF(c.x() - r * 0.35, c.y() - r * 0.45), r * 0.7)
        shine.setColorAt(0, QColor(255, 255, 255, 140))
        shine.setColorAt(1, QColor(255, 255, 255, 0))
        p.setBrush(shine)
        p.drawEllipse(c + QPointF(-r * 0.2, -r * 0.25), r * 0.6, r * 0.5)
        p.end()


def _button(text: str, primary: bool, accent: str) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setMinimumHeight(38)
    if primary:
        b.setStyleSheet(f"QPushButton {{ background: {accent}; color: white; border: none; border-radius: 10px;"
                        " padding: 0 22px; font-weight: 600; font-size: 14px; }"
                        "QPushButton:disabled { background: rgba(255,255,255,0.12); color: rgba(255,255,255,0.4); }")
    else:
        b.setStyleSheet("QPushButton { background: rgba(255,255,255,0.08); color: #f4f4f5; border: none;"
                        " border-radius: 10px; padding: 0 18px; font-size: 14px; }"
                        "QPushButton:hover { background: rgba(255,255,255,0.14); }")
    return b


class Onboarding(QWidget):
    finished = Signal()
    show_cursor = Signal()

    def __init__(self, accent: str, setup_view, permission_rows_fn):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Welcome to Click")
        self.accent = accent
        self.permission_rows_fn = permission_rows_fn
        self.setup_done = False
        self.setFixedSize(540, 520)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(34, 30, 34, 26)
        self.stack = QStackedLayout()
        outer.addLayout(self.stack, 1)
        nav = QHBoxLayout()
        self.dots = QLabel()
        nav.addWidget(self.dots)
        nav.addStretch()
        self.back = _button("Back", False, accent)
        self.next = _button("Get started", True, accent)
        self.back.clicked.connect(lambda: self._go(self.stack.currentIndex() - 1))
        self.next.clicked.connect(self._next)
        nav.addWidget(self.back)
        nav.addWidget(self.next)
        outer.addLayout(nav)

        # 1 welcome
        p1 = self._page("Meet Click", "Your private AI that lives right here on your computer. It can see your "
                        "screen when you ask, point things out with its own cursor, and remember what matters to you.")
        p1.layout().insertWidget(0, Orb(170, accent), 0, Qt.AlignHCenter)
        # 2 cursor
        p2 = self._page("Click has its own cursor", "When you ask where something is, Click glides its purple "
                        "cursor over and explains it. It never takes over your mouse, and it always asks before "
                        "it clicks anything. Press Control + Option + Esc any time to stop everything.")
        demo = _button("Show me", False, accent)
        demo.clicked.connect(self.show_cursor.emit)
        p2.layout().addWidget(demo, 0, Qt.AlignLeft)
        # 3 permissions
        self.p3 = self._page("A few permissions", "So Click can see your screen and point at things. Turn on "
                             "Click in each list, then come back here.")
        self.perm_box = QVBoxLayout()
        self.p3.layout().addLayout(self.perm_box)
        recheck = _button("Check again", False, accent)
        recheck.clicked.connect(self._fill_permissions)
        self.p3.layout().addWidget(recheck, 0, Qt.AlignLeft)
        # 4 setup (the setup view does the real work; it started when the welcome opened)
        p4 = QWidget()
        l4 = QVBoxLayout(p4)
        l4.setContentsMargins(0, 0, 0, 0)
        setup_view.setStyleSheet("QLabel { color: #f4f4f5; }")
        l4.addWidget(setup_view)
        # 5 ready
        p5 = self._page("You're all set", "")
        tips = QLabel("<p style='line-height:150%'>"
                      "<b>Ask anything:</b> click the orb in the top-right corner.<br>"
                      "<b>Open or hide Click:</b> Cmd + Shift + Space.<br>"
                      "<b>Show me where…:</b> Click points with its own cursor.<br>"
                      "<b>Stop everything:</b> Control + Option + Esc.</p>")
        tips.setStyleSheet(TEXT + "font-size: 14px;")
        tips.setWordWrap(True)
        p5.layout().addWidget(tips)
        for page in (p1, p2, self.p3, p4, p5):
            self.stack.addWidget(page)
        self.pages = 5
        self._go(0)
        geo = QApplication.primaryScreen().availableGeometry()
        self.move(geo.center().x() - self.width() // 2, geo.center().y() - self.height() // 2)

    def _page(self, title: str, body: str) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(12)
        t = QLabel(title)
        t.setStyleSheet(TEXT + "font-size: 24px; font-weight: 700;")
        lay.addWidget(t)
        if body:
            b = QLabel(body)
            b.setWordWrap(True)
            b.setStyleSheet(SUB)
            lay.addWidget(b)
        lay.addStretch()
        w.setLayout(lay)
        return w

    def _fill_permissions(self):
        while self.perm_box.count():
            item = self.perm_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        rows = self.permission_rows_fn()
        if not rows:
            lbl = QLabel("Nothing to do on this computer.")
            lbl.setStyleSheet(SUB)
            self.perm_box.addWidget(lbl)
        for name, why, granted, opener in rows:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 4, 0, 4)
            txt = QLabel(f"<b>{name}</b><br><span style='color:rgba(244,244,245,0.6)'>{why}</span>")
            txt.setStyleSheet(TEXT)
            txt.setWordWrap(True)
            h.addWidget(txt, 1)
            if granted:
                ok = QLabel("Allowed")
                ok.setStyleSheet("color: #4ade80; font-weight: 600;")
                h.addWidget(ok)
            else:
                b = _button("Open Settings", False, self.accent)
                b.clicked.connect(opener)
                h.addWidget(b)
            self.perm_box.addWidget(row)

    def set_setup_done(self, ok: bool):
        self.setup_done = ok
        self._update_buttons()

    def _go(self, i: int):
        i = max(0, min(self.pages - 1, i))
        self.stack.setCurrentIndex(i)
        if i == 2:
            self._fill_permissions()
        self.dots.setText("  ".join(f"<span style='color:{self.accent if k == i else 'rgba(255,255,255,0.25)'};"
                                    "font-size:18px'>●</span>" for k in range(self.pages)))
        self._update_buttons()

    def _update_buttons(self):
        i = self.stack.currentIndex()
        self.back.setVisible(i > 0)
        labels = {0: "Get started", 3: "Continue", 4: "Start using Click"}
        self.next.setText(labels.get(i, "Next"))
        self.next.setEnabled(not (i == 3 and not self.setup_done))

    def _next(self):
        i = self.stack.currentIndex()
        if i == self.pages - 1:
            self.close()
            self.finished.emit()
        else:
            self._go(i + 1)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(1, 1, -1, -1), 24, 24)
        p.fillPath(path, QColor(17, 17, 22, 246))
        edge = QColor(self.accent)
        edge.setAlpha(110)
        p.setPen(edge)
        p.drawPath(path)
        p.end()

    def mousePressEvent(self, e):  # drag anywhere to move
        self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if getattr(self, "_drag", None) is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None
