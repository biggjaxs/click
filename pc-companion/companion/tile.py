"""The Click tile: a small floating chat window in the top-right corner of the screen.

- Frameless, rounded, stays on top (pin to turn that off), draggable by its header, resizable.
- Close hides it; bring it back from the menu bar icon or with Cmd+Shift+Space (Ctrl+Shift+Space on Windows).
- Before Click is ready it shows the setup screen (downloads, permissions); then the chat.
"""
from __future__ import annotations

import math
import sys

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizeGrip, QStackedLayout, QVBoxLayout, QWidget,
)

TILE_W, TILE_H, MARGIN = 500, 700, 0
GLOW = 52      # room around the flat chat panel for the flowing goo (and its droplets)
GOO_OFF = 14   # how far the goo reaches past the panel at rest
GOO_AMP = 8    # how much it ripples (calm)
PANEL_R = 30   # the flat panel's corner radius


def _dark() -> bool:
    return QApplication.palette().window().color().lightness() < 128


class IconButton(QPushButton):
    """Small round header button drawn with simple strokes (no emoji, no image files)."""

    def __init__(self, kind: str, tip: str):
        super().__init__()
        self.kind = kind
        self.checked_look = False
        self.setToolTip(tip)
        self.setFixedSize(QSize(28, 28))
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { border: none; border-radius: 8px; background: transparent; }"
                           "QPushButton:hover { background: rgba(255,255,255,0.10); }")

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = QColor("#8b7dff") if self.checked_look else QColor(170, 170, 182)
        p.setPen(QPen(col, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        c = QPointF(14, 14)
        if self.kind == "close":
            p.drawLine(c + QPointF(-4, -4), c + QPointF(4, 4))
            p.drawLine(c + QPointF(4, -4), c + QPointF(-4, 4))
        elif self.kind == "min":
            p.drawLine(c + QPointF(-5, 0), c + QPointF(5, 0))
        elif self.kind == "pin":
            path = QPainterPath(c + QPointF(-3, -6))
            path.lineTo(c + QPointF(3, -6))
            path.lineTo(c + QPointF(2, -1))
            path.lineTo(c + QPointF(5, 2))
            path.lineTo(c + QPointF(-5, 2))
            path.lineTo(c + QPointF(-2, -1))
            path.closeSubpath()
            p.drawPath(path)
            p.drawLine(c + QPointF(0, 2), c + QPointF(0, 7))
        elif self.kind == "open":  # open in browser: square with arrow
            p.drawRoundedRect(QRectF(8, 10, 10, 10), 2, 2)
            p.drawLine(QPointF(14, 14), QPointF(20, 8))
            p.drawLine(QPointF(16, 8), QPointF(20, 8))
            p.drawLine(QPointF(20, 8), QPointF(20, 12))
        p.end()


class Header(QWidget):
    """Drag handle with the logo, name, status dot and window buttons."""

    def __init__(self, tile: "Tile"):
        super().__init__()
        self.tile = tile
        self.setFixedHeight(40)
        self._drag: QPoint | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(2)
        self.logo = QLabel()
        self.logo.setFixedSize(18, 18)
        self.name = QLabel("Click")
        self.name.setStyleSheet("font-weight: 600; font-size: 13px;")
        self.status = QLabel("")
        self.status.setStyleSheet("color: #a1a1aa; font-size: 12px; padding-left: 6px;")
        lay.addWidget(self.logo)
        lay.addSpacing(6)
        lay.addWidget(self.name)
        lay.addWidget(self.status)
        lay.addStretch()
        self.pin = IconButton("pin", "Keep on top")
        self.pin.checked_look = True
        self.pin.clicked.connect(tile.toggle_pin)
        self.browser = IconButton("open", "Open in browser")
        self.browser.clicked.connect(tile.open_in_browser)
        self.minimize = IconButton("min", "Hide (Cmd/Ctrl+Shift+Space brings it back)")
        self.minimize.clicked.connect(tile.hide_tile)
        self._spin = QTimer(self)
        self._spin.timeout.connect(lambda: self.update(self.logo.geometry().adjusted(-4, -4, 4, 4)))
        self._spin.start(50)
        self.close_btn = IconButton("close", "Close (Click keeps running in the menu bar)")
        self.close_btn.clicked.connect(tile.hide_tile)
        for b in (self.pin, self.browser, self.minimize, self.close_btn):
            lay.addWidget(b)

    def paintEvent(self, e):
        import math
        import time

        from PySide6.QtGui import QRadialGradient

        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.logo.geometry())
        c = r.center()
        t = time.time()
        rad = r.width() * 0.46
        path = QPainterPath()
        for i in range(49):
            a = 2 * math.pi * i / 48
            w = 1 + math.sin(3 * a + t * 1.4) * 0.06 + math.sin(5 * a - t * 2.0) * 0.04
            pt = QPointF(c.x() + rad * w * math.cos(a), c.y() + rad * w * math.sin(a))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        g = QRadialGradient(QPointF(c.x() + rad * 0.4 * math.cos(t), c.y() + rad * 0.4 * math.sin(t)), rad * 1.7)
        g.setColorAt(0, QColor("#22d3ee"))
        g.setColorAt(0.6, QColor(self.tile.accent))
        g.setColorAt(1, QColor(self.tile.accent).darker(160))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawPath(path)
        p.end()

    def set_status(self, text: str, color: str = "#a1a1aa"):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color}; font-size: 12px; padding-left: 6px;")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            wh = self.tile.windowHandle()
            if wh is not None and wh.startSystemMove():  # let macOS move the window: perfectly smooth
                self._drag = None
                return
            self._drag = e.globalPosition().toPoint() - self.tile.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.tile.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.tile.moved.emit()


class SetupView(QWidget):
    """First-run setup: shows each step with a progress bar, then permission checks."""

    continue_clicked = Signal()

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 22)
        lay.setSpacing(10)
        self.title = QLabel("Setting up Click")
        self.title.setStyleSheet("font-size: 17px; font-weight: 600;")
        self.sub = QLabel("This happens once. Click downloads its AI model so everything runs privately on "
                          "this computer.")
        self.sub.setWordWrap(True)
        self.sub.setStyleSheet("color: #8a8a94;")
        lay.addWidget(self.title)
        lay.addWidget(self.sub)
        lay.addSpacing(8)
        self.steps = QVBoxLayout()
        self.steps.setSpacing(8)
        lay.addLayout(self.steps)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(5)
        self.bar.setStyleSheet("QProgressBar { background: rgba(127,127,127,0.18); border: none; border-radius: 2px; }"
                               "QProgressBar::chunk { background: #6d5dfc; border-radius: 2px; }")
        lay.addWidget(self.bar)
        self.detail = QLabel("")
        self.detail.setStyleSheet("color: #8a8a94; font-size: 12px;")
        self.detail.setWordWrap(True)
        lay.addWidget(self.detail)
        self.perm_box = QVBoxLayout()
        self.perm_box.setSpacing(6)
        lay.addLayout(self.perm_box)
        lay.addStretch()
        self.cont = QPushButton("Start using Click")
        self.cont.setCursor(Qt.PointingHandCursor)
        self.cont.setStyleSheet("QPushButton { background: #6d5dfc; color: white; border: none; border-radius: 9px;"
                                " padding: 9px 14px; font-weight: 600; } QPushButton:disabled { background: #b9b2fd; }")
        self.cont.clicked.connect(self.continue_clicked.emit)
        self.cont.hide()
        lay.addWidget(self.cont)
        self._rows: dict[str, QLabel] = {}

    def set_step(self, key: str, text: str, state: str):
        """state: todo | active | done | error"""
        mark = {"todo": "<span style='color:#a1a1aa'>○</span>", "active": "<span style='color:#6d5dfc'>●</span>",
                "done": "<span style='color:#16a34a'>✓</span>", "error": "<span style='color:#dc2626'>!</span>"}[state]
        if key not in self._rows:
            lbl = QLabel()
            lbl.setTextFormat(Qt.RichText)
            self._rows[key] = lbl
            self.steps.addWidget(lbl)
        weight = "600" if state == "active" else "400"
        self._rows[key].setText(f"{mark}&nbsp;&nbsp;<span style='font-weight:{weight}'>{text}</span>")

    def set_progress(self, frac: float | None, detail: str = ""):
        if frac is None:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(frac * 1000))
        self.detail.setText(detail)

    def add_recheck(self, fn):
        if getattr(self, "_recheck", None):
            return
        self._recheck = QPushButton("Check again")
        self._recheck.setCursor(Qt.PointingHandCursor)
        self._recheck.clicked.connect(fn)
        self.layout().insertWidget(self.layout().count() - 1, self._recheck)

    def show_permissions(self, rows: list[tuple[str, str, bool, object]]):
        """rows: (name, why, granted, open_settings_callable)"""
        while self.perm_box.count():
            item = self.perm_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not rows:
            return
        head = QLabel("Allow Click on this Mac")
        head.setStyleSheet("font-weight: 600; margin-top: 8px;")
        self.perm_box.addWidget(head)
        for name, why, granted, opener in rows:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            txt = QLabel(f"<b>{name}</b><br><span style='color:#8a8a94;font-size:12px'>{why}</span>")
            txt.setWordWrap(True)
            h.addWidget(txt, 1)
            if granted:
                ok = QLabel("<span style='color:#16a34a'>Allowed</span>")
                h.addWidget(ok)
            else:
                b = QPushButton("Open Settings")
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(opener)
                h.addWidget(b)
            self.perm_box.addWidget(row)


def _rr_point(r: QRectF, rad: float, u: float, panel_r: float = PANEL_R):
    """A point (and the outward direction) at fraction u around a rounded rectangle grown by
    rad - panel_r around r."""
    grow = rad - panel_r
    x0, y0, x1, y1 = r.left() - grow, r.top() - grow, r.right() + grow, r.bottom() + grow
    w, h = x1 - x0 - 2 * rad, y1 - y0 - 2 * rad
    arc = math.pi * rad / 2
    segs = [("line", w), ("arc", arc), ("line", h), ("arc", arc), ("line", w), ("arc", arc), ("line", h), ("arc", arc)]
    total = sum(L for _, L in segs)
    d = (u % 1.0) * total
    corners = [(x1 - rad, y0 + rad), (x1 - rad, y1 - rad), (x0 + rad, y1 - rad), (x0 + rad, y0 + rad)]
    for i, (kind, L) in enumerate(segs):
        if d <= L or i == len(segs) - 1:
            f = d / L if L else 0
            side = i // 2
            if kind == "line":
                if side == 0:
                    return QPointF(x0 + rad + f * w, y0), QPointF(0, -1)
                if side == 1:
                    return QPointF(x1, y0 + rad + f * h), QPointF(1, 0)
                if side == 2:
                    return QPointF(x1 - rad - f * w, y1), QPointF(0, 1)
                return QPointF(x0, y1 - rad - f * h), QPointF(-1, 0)
            cx, cy = corners[side]
            a = -math.pi / 2 + side * math.pi / 2 + f * math.pi / 2
            n = QPointF(math.cos(a), math.sin(a))
            return QPointF(cx + rad * n.x(), cy + rad * n.y()), n
        d -= L
    return QPointF(x0, y0), QPointF(0, -1)


def goo_path(panel: QRectF, panel_r: float, t: float, n: int = 140, off: float = GOO_OFF,
             amp: float = GOO_AMP) -> QPainterPath:
    """A wavy, flowing outline around the panel: always outside it, never still, never fast."""
    pts = []
    for i in range(n):
        u = i / n
        pt, nrm = _rr_point(panel, panel_r + off, u, panel_r)
        a = 2 * math.pi * u
        wob = amp * (0.5 * math.sin(4 * a + t * 0.8) + 0.3 * math.sin(7 * a - t * 1.1)
                         + 0.2 * math.sin(11 * a + t * 1.6) + 0.15 * math.sin(2 * a - t * 0.5))
        wob = max(-off + 4, wob)  # the goo never cuts into the chat
        pts.append(QPointF(pt.x() + nrm.x() * wob, pt.y() + nrm.y() * wob))
    path = QPainterPath()
    mid = lambda a, b: QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2)  # noqa: E731
    path.moveTo(mid(pts[-1], pts[0]))
    for i in range(n):
        path.quadTo(pts[i], mid(pts[i], pts[(i + 1) % n]))
    path.closeSubpath()
    return path


_rm = {"at": 0.0, "on": False}


def reduce_motion() -> bool:
    """The Mac's Accessibility > Display > Reduce motion setting: when on, the goo holds still."""
    import time

    if time.time() - _rm["at"] > 30:
        _rm["at"] = time.time()
        try:
            from AppKit import NSWorkspace

            ws = NSWorkspace.sharedWorkspace()
            _rm["on"] = bool(ws.respondsToSelector_("accessibilityDisplayShouldReduceMotion")
                             and ws.accessibilityDisplayShouldReduceMotion())
        except Exception:  # noqa: BLE001 - not a Mac
            _rm["on"] = False
    return _rm["on"]


def paint_goo(p: QPainter, area: QRectF, margin: float, panel_r: float, t: float, accent_hex: str,
              off: float = GOO_OFF, amp: float = GOO_AMP, droplets: bool = False) -> None:
    """A floating ball of goo: a flat, calm panel inside (easy to read), wrapped in slowly flowing
    liquid in the orb's colours (cyan to blue), with little droplets drifting by.
    Used by the full chat and the Ask panel, so both look the same."""
    p.setRenderHint(QPainter.Antialiasing)
    accent = QColor(accent_hex)
    cyan = QColor("#22d3ee")
    panel = area.adjusted(margin, margin, -margin, -margin)
    goo = goo_path(panel, panel_r, t, off=off, amp=amp)
    # 1. soft glow in space around the goo
    for width, alpha in ((22, 18), (12, 30)):
        g = QColor(cyan)
        g.setAlpha(alpha)
        p.setPen(QPen(g, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(goo)
    # 2. the goo itself, coloured like the orb, with its light slowly drifting
    c = panel.center()
    drift = QPointF(c.x() + panel.width() * 0.25 * math.cos(t * 0.35), c.y() + panel.height() * 0.25 * math.sin(t * 0.3))
    body = QRadialGradient(drift, max(panel.width(), panel.height()) * 0.75)
    body.setColorAt(0.0, cyan)
    body.setColorAt(0.55, accent)
    body.setColorAt(1.0, accent.darker(170))
    p.setPen(Qt.NoPen)
    p.setBrush(body)
    p.drawPath(goo)
    # 3. droplets floating beside it (goo in space)
    if droplets:
        for k, (u0, speed, r0) in enumerate(((0.12, 0.010, 7.5), (0.61, -0.008, 5.5))):
            u = (u0 + t * speed) % 1.0
            pt, nrm = _rr_point(panel, panel_r + off, u, panel_r)
            dist = amp + 9 + 5 * math.sin(t * 0.6 + k * 2)
            dc = QPointF(pt.x() + nrm.x() * dist, pt.y() + nrm.y() * dist)
            dg = QRadialGradient(dc + QPointF(-r0 * 0.3, -r0 * 0.3), r0 * 1.4)
            dg.setColorAt(0, cyan)
            dg.setColorAt(1, accent)
            p.setBrush(dg)
            p.drawEllipse(dc, r0 * (1 + 0.08 * math.sin(t * 1.3 + k)), r0 * (1 - 0.08 * math.sin(t * 1.3 + k)))
    # 4. shine on the goo, like light on the orb
    inner = QPainterPath()
    inner.addRoundedRect(panel, panel_r, panel_r)
    p.save()
    p.setClipPath(goo.subtracted(inner))
    shine = QRadialGradient(QPointF(panel.x() + panel.width() * 0.25, panel.y()), panel.width() * 0.55)
    shine.setColorAt(0, QColor(255, 255, 255, 150))
    shine.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillRect(area, shine)
    p.restore()
    # 5. the flat panel inside (2D, calm, easy to read)
    p.fillPath(inner, QColor(13, 13, 19, 250))
    edge = QColor(cyan)
    edge.setAlpha(70)
    p.setPen(QPen(edge, 1.2))
    p.setBrush(Qt.NoBrush)
    p.drawPath(inner)


class Tile(QWidget):
    moved = Signal()

    def __init__(self, url: str, profile_dir: str, accent: str = "#6d5dfc"):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
        from PySide6.QtWebEngineWidgets import QWebEngineView

        self.url = url
        self.accent = accent
        self.pinned = True
        self.setWindowTitle("Click")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(320, 360)
        self.frame = QWidget(self)
        self.frame.setObjectName("frame")
        # The tile paints its own liquid glass (see paintEvent); the frame and the page are see-through.
        self.frame.setStyleSheet("QWidget#frame { background: transparent; } QLabel { color: #f4f4f5; }")
        self._glow_t = 0.0
        self._glow = QTimer(self)
        self._glow.timeout.connect(self._tick_glow)
        self._glow.start(50)  # small, slow steps = smooth, calm motion
        outer = QVBoxLayout(self)
        outer.setContentsMargins(GLOW, GLOW, GLOW, GLOW)
        outer.addWidget(self.frame)
        lay = QVBoxLayout(self.frame)
        lay.setContentsMargins(9, 9, 9, 9)  # keep the content inside the thick glass rim
        lay.setSpacing(0)
        self.header = Header(self)
        lay.addWidget(self.header)
        self.stack = QStackedLayout()
        lay.addLayout(self.stack, 1)
        self.setup = SetupView()
        self.stack.addWidget(self.setup)

        profile = QWebEngineProfile("click", self)
        profile.setPersistentStoragePath(profile_dir)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        profile.downloadRequested.connect(self._download)

        class Page(QWebEnginePage):
            def acceptNavigationRequest(page, qurl, nav_type, is_main):  # noqa: N805
                # Links to other websites open in your normal browser, not inside the tile.
                if is_main and qurl.host() not in (QUrl(self.url).host(), "") and qurl.scheme() in ("http", "https"):
                    QDesktopServices.openUrl(qurl)
                    return False
                return super().acceptNavigationRequest(qurl, nav_type, is_main)

        self.web = QWebEngineView()
        self.web.setPage(Page(profile, self.web))
        self.web.page().setBackgroundColor(Qt.transparent)
        self.web.setAttribute(Qt.WA_TranslucentBackground)
        self.web.setStyleSheet("background: transparent;")
        self.web.loadFinished.connect(self._loaded)
        self.web.page().renderProcessTerminated.connect(
            lambda status, code: __import__("logging").getLogger("click.tile").error(
                "chat window renderer stopped: %s (exit %s)", status, code))
        self.stack.addWidget(self.web)
        self._tries = 0
        self._want_chat = False
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 2, 2)
        grip_row.addStretch()
        grip_row.addWidget(QSizeGrip(self.frame))
        lay.addLayout(grip_row)
        self.resize(TILE_W, TILE_H)
        self.place_top_right()

    def moveEvent(self, e):
        super().moveEvent(e)
        # tell the orb where the chat went, once the window stops moving
        if not hasattr(self, "_moved_timer"):
            self._moved_timer = QTimer(self)
            self._moved_timer.setSingleShot(True)
            self._moved_timer.timeout.connect(self.moved.emit)
        self._moved_timer.start(150)

    # ---- liquid glass look ------------------------------------------------------------------------------
    def _tick_glow(self):
        if self.isVisible() and not reduce_motion():
            self._glow_t += 0.035
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        paint_goo(p, QRectF(self.rect()), GLOW, PANEL_R, self._glow_t, self.accent)
        p.end()

    # ---- window behaviour ------------------------------------------------------------------------------
    def place_top_right(self):
        geo = QApplication.primaryScreen().availableGeometry()
        self.move(geo.right() - self.width() - MARGIN, geo.top() + MARGIN)

    def toggle(self):
        if self.isVisible():
            self.hide_tile()
        else:
            self.show_tile()

    def _fade(self, start: float, end: float, after=None):
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(180)
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        if after:
            self._anim.finished.connect(after)
        self._anim.start()

    def hide_tile(self):
        self._fade(1.0, 0.0, lambda: (self.hide(), self.setWindowOpacity(1.0)))

    def place_under(self, bubble):
        """Open just below the bubble, kept on screen."""
        geo = (bubble.screen() or QApplication.primaryScreen()).availableGeometry()
        b = bubble.frameGeometry()
        x = min(max(geo.left() + 8, b.right() - self.width() + 4), geo.right() - self.width() - 8)
        y = b.bottom() + 4
        if y + self.height() > geo.bottom() - 8:
            y = max(geo.top() + 8, b.top() - self.height() - 4)
        self.move(x, y)

    def show_tile(self):
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        self._fade(0.0, 1.0)
        if sys.platform == "darwin":
            _mac_round_and_float(self, self.pinned)

    def toggle_pin(self):
        self.pinned = not self.pinned
        self.header.pin.checked_look = self.pinned
        self.header.pin.update()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.pinned)
        self.show_tile()

    def open_in_browser(self):
        QDesktopServices.openUrl(QUrl(self.url))

    def show_chat(self, url: str | None = None):
        """Load the chat. Until it answers, show "Starting Click…" and keep retrying (never an error page)."""
        if url:
            self.url = url
        self._want_chat = True
        self._tries = 0
        if self.stack.currentWidget() is not self.web:
            self.setup.title.setText("Starting Click…")
            self.setup.sub.setText("Getting everything ready. This takes a few seconds on the first start.")
            self.setup.set_progress(None, "")
            self.setup.cont.hide()
        self._load()

    def _load(self):
        self.web.setUrl(QUrl(self.url + ("&" if "?" in self.url else "?") + "tile=1"))

    def _loaded(self, ok: bool):
        if not self._want_chat:
            return
        if ok:
            self.stack.setCurrentWidget(self.web)
            return
        self._tries += 1
        self.stack.setCurrentWidget(self.setup)
        if self._tries in (8, 20, 40):  # say plainly what's wrong, but keep trying
            self.setup.title.setText("The full chat window can't load yet")
            err = getattr(getattr(self, "hub", None), "last_error", "")
            self.setup.sub.setText(_diagnose(self.url) + (f"\n\nError: {err}" if err else "") + "\n\nYou can still chat: click the orb in the top-right "
                                   "to open Ask Click. Details: ~/Library/Application Support/ClickCompanion/companion.log")
        QTimer.singleShot(1500, self._load)

    def show_setup(self):
        self.stack.setCurrentWidget(self.setup)

    def _download(self, item):
        from pathlib import Path

        folder = Path.home() / "Downloads"
        item.setDownloadDirectory(str(folder))
        item.accept()

    def bottom_left_global(self) -> QPoint:
        g = self.frameGeometry()
        return QPoint(g.left() + 30, g.top() + 60)

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self.moved.emit)


def _diagnose(url: str) -> str:
    import logging

    import httpx

    try:
        httpx.get(url + "/api/status", timeout=3).raise_for_status()
        msg = "Click's chat part is running, but this window couldn't display it (a web view problem)."
    except Exception as exc:  # noqa: BLE001
        msg = f"Click's chat part isn't answering ({exc.__class__.__name__}). It restarts itself automatically."
    logging.getLogger("click.tile").warning("chat window not loading: %s", msg)
    return msg


def _mac_round_and_float(widget, pinned: bool) -> None:
    """Keep the tile above other apps (and on every Space) on macOS."""
    if QApplication.platformName() != "cocoa":
        return
    try:
        import objc

        win = objc.objc_object(c_void_p=int(widget.winId())).window()
        win.setLevel_(3 if pinned else 0)                 # floating / normal
        win.setCollectionBehavior_((1 << 0) | (1 << 8))   # all Spaces, next to full-screen apps
        win.setHasShadow_(True)
    except Exception:  # noqa: BLE001
        pass
