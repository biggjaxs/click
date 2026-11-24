"""The Click tile: a small floating chat window in the top-right corner of the screen.

- Frameless, rounded, stays on top (pin to turn that off), draggable by its header, resizable.
- Close hides it; bring it back from the menu bar icon or with Cmd+Shift+Space (Ctrl+Shift+Space on Windows).
- Before Click is ready it shows the setup screen (downloads, permissions); then the chat.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizeGrip, QStackedLayout, QVBoxLayout, QWidget,
)

TILE_W, TILE_H, MARGIN = 400, 600, 14


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
        self.setStyleSheet("QPushButton { border: none; border-radius: 7px; background: transparent; }"
                           "QPushButton:hover { background: rgba(127,127,127,0.16); }")

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = QColor("#6d5dfc") if self.checked_look else QColor(160, 160, 170) if _dark() else QColor(113, 113, 122)
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
        self.minimize.clicked.connect(tile.hide)
        self.close_btn = IconButton("close", "Close (Click keeps running in the menu bar)")
        self.close_btn.clicked.connect(tile.hide)
        for b in (self.pin, self.browser, self.minimize, self.close_btn):
            lay.addWidget(b)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.logo.geometry()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.tile.accent))
        p.drawRoundedRect(QRectF(r), 5, 5)
        o = QPointF(r.x() + 6, r.y() + 4)
        path = QPainterPath(o)
        for dx, dy in ((0, 10), (2.6, 7.4), (4.4, 10.8), (6, 10), (4.3, 6.7), (7.6, 6.5)):
            path.lineTo(o + QPointF(dx, dy))
        path.closeSubpath()
        p.setBrush(QColor("white"))
        p.drawPath(path)
        p.end()

    def set_status(self, text: str, color: str = "#a1a1aa"):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color}; font-size: 12px; padding-left: 6px;")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
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
        bg = "#131316" if _dark() else "#ffffff"
        border = "#26262b" if _dark() else "#e4e4e7"
        self.frame.setStyleSheet(f"QWidget#frame {{ background: {bg}; border: 1px solid {border}; border-radius: 14px; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.frame)
        lay = QVBoxLayout(self.frame)
        lay.setContentsMargins(1, 1, 1, 1)
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
        self.web.page().setBackgroundColor(QColor(bg))
        self.stack.addWidget(self.web)
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 2, 2)
        grip_row.addStretch()
        grip_row.addWidget(QSizeGrip(self.frame))
        lay.addLayout(grip_row)
        self.resize(TILE_W, TILE_H)
        self.place_top_right()

    # ---- window behaviour ------------------------------------------------------------------------------
    def place_top_right(self):
        geo = QApplication.primaryScreen().availableGeometry()
        self.move(geo.right() - self.width() - MARGIN, geo.top() + MARGIN)

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.show_tile()

    def show_tile(self):
        self.show()
        self.raise_()
        self.activateWindow()
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
        if url:
            self.url = url
        self.web.setUrl(QUrl(self.url + ("&" if "?" in self.url else "?") + "tile=1"))
        self.stack.setCurrentWidget(self.web)

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
