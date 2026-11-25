"""Ask Click: the native panel that opens when you click the floating orb.

Type a question, the answer streams in right there. Approvals ("Click wants to read a file")
appear as buttons. "Open full chat" opens the bigger chat window. This panel talks to Click's chat
part directly, so it works even if the full chat window can't load.
"""
from __future__ import annotations

import json
import threading

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

W = 380


class _Link(QObject):
    """WebSocket connection to Click's chat part, on a background thread."""

    event = Signal(dict)

    def __init__(self, url: str):
        super().__init__()
        self.url = url.replace("http://", "ws://").rstrip("/") + "/ws"
        self.ws = None
        self.lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        from websockets.sync.client import connect

        while True:
            try:
                with connect(self.url, open_timeout=5) as ws:
                    self.ws = ws
                    self.event.emit({"type": "link", "ok": True})
                    for raw in ws:
                        self.event.emit(json.loads(raw))
            except Exception:  # noqa: BLE001 - retry quietly; the panel shows "connecting"
                self.ws = None
                self.event.emit({"type": "link", "ok": False})
                threading.Event().wait(2)

    def send(self, msg: dict) -> bool:
        with self.lock:
            if not self.ws:
                return False
            try:
                self.ws.send(json.dumps(msg))
                return True
            except Exception:  # noqa: BLE001
                return False


class QuickAsk(QWidget):
    open_full = Signal()

    def __init__(self, url: str, accent: str = "#6d5dfc"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setFixedWidth(W)
        self.accent = accent
        self.conversation_id = None
        self.answer = ""
        self.busy = False
        self.link = _Link(url)
        self.link.event.connect(self._on_event)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        self.status = QLabel("Connecting to Click…")
        self.status.setStyleSheet("color: rgba(255,255,255,0.55); font-size: 11.5px;")
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
                                  "QScrollBar:vertical { width: 6px; background: transparent; }"
                                  "QScrollBar::handle:vertical { background: rgba(255,255,255,0.25); border-radius: 3px; }")
        self.out = QLabel("")
        self.out.setWordWrap(True)
        self.out.setTextFormat(Qt.MarkdownText)
        self.out.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self.out.setOpenExternalLinks(True)
        self.out.setStyleSheet("color: #f4f4f5; font-size: 13.5px;")
        self.out.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(self.out)
        self.scroll.setMaximumHeight(320)
        self.scroll.hide()
        self.actions = QHBoxLayout()
        self.actions.setSpacing(6)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask Click anything…")
        self.input.returnPressed.connect(self._send)
        self.input.setStyleSheet(
            "QLineEdit { background: rgba(255,255,255,0.08); color: #fff; border: 1px solid rgba(255,255,255,0.14);"
            f" border-radius: 12px; padding: 10px 12px; font-size: 14px; selection-background-color: {accent}; }}"
            f"QLineEdit:focus {{ border-color: {accent}; }}")
        foot = QHBoxLayout()
        self.full = QPushButton("Open full chat")
        self.new = QPushButton("New question")
        for b in (self.full, self.new):
            b.setCursor(Qt.PointingHandCursor)
            b.setFlat(True)
            b.setStyleSheet("QPushButton { color: rgba(255,255,255,0.6); border: none; font-size: 12px; padding: 2px 4px; }"
                            "QPushButton:hover { color: #fff; }")
        self.full.clicked.connect(self.open_full.emit)
        self.new.clicked.connect(self._new)
        foot.addWidget(self.status)
        foot.addStretch()
        foot.addWidget(self.new)
        foot.addWidget(self.full)
        lay.addWidget(self.input)
        lay.addWidget(self.scroll)
        lay.addLayout(self.actions)
        lay.addLayout(foot)
        self.adjustSize()

    # ---- look: dark glass card with a soft accent glow ---------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, 18, 18)
        p.fillPath(path, QColor(20, 20, 26, 238))
        glow = QColor(self.accent)
        glow.setAlpha(90)
        p.setPen(glow)
        p.drawPath(path)
        p.end()

    # ---- showing / hiding ------------------------------------------------------------------------------------
    def place_under(self, bubble):
        geo = (bubble.screen() or QApplication.primaryScreen()).availableGeometry()
        b = bubble.frameGeometry()
        x = min(max(geo.left() + 8, b.right() - W + 2), geo.right() - W - 8)
        self.move(x, b.bottom() + 2)

    def toggle(self, bubble):
        if self.isVisible():
            self.hide()
            return
        self.place_under(bubble)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(160)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.start()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()

    # ---- chatting ------------------------------------------------------------------------------------------------
    def _new(self):
        self.conversation_id = None
        self.answer = ""
        self.out.setText("")
        self.scroll.hide()
        self._clear_actions()
        self.adjustSize()
        self.input.setFocus()

    def _send(self):
        text = self.input.text().strip()
        if not text or self.busy:
            return
        if not self.link.send({"type": "send", "text": text, "conversation_id": self.conversation_id, "uploads": []}):
            self.status.setText("Not connected yet. Try again in a moment.")
            return
        self.busy = True
        self.answer = ""
        self.input.clear()
        self.input.setPlaceholderText("Click is thinking…")
        self.out.setText(f"<span style='color:#a1a1aa'>{_escape(text)}</span>")
        self.scroll.show()
        self.status.setText("Thinking…")
        self.adjustSize()

    def _clear_actions(self):
        while self.actions.count():
            item = self.actions.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _on_event(self, ev: dict):
        kind = ev.get("type")
        if kind == "link":
            if not self.busy:
                err = getattr(self, "hub_error", lambda: "")()
                self.status.setText("Ready" if ev["ok"] else (f"Problem: {err[:120]}" if err else "Connecting to Click…"))
                self.status.setWordWrap(True)
        elif kind == "conversation":
            self.conversation_id = ev.get("id")
        elif kind == "token":
            self.answer += ev.get("text", "")
            self.out.setText(self.answer)
            QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))
            self.adjustSize()
        elif kind == "tool" and ev.get("status") == "running":
            self.status.setText(ev.get("caption") or "Working…")
        elif kind == "confirm_request":
            self._clear_actions()
            ask = QLabel(ev.get("title", "Click wants to do something"))
            ask.setStyleSheet("color: #fff; font-size: 12.5px;")
            self.actions.addWidget(ask, 1)
            labels = {"once": "Allow", "task": "Allow for this chat", "deny": "Deny"}
            for opt in ev.get("options", ["once", "deny"]):
                b = QPushButton(labels.get(opt, opt))
                b.setCursor(Qt.PointingHandCursor)
                bg = self.accent if opt == "once" else "rgba(255,255,255,0.1)"
                b.setStyleSheet(f"QPushButton {{ background: {bg}; color: #fff; border: none; border-radius: 8px;"
                                " padding: 5px 10px; font-size: 12px; }")
                b.clicked.connect(lambda _=False, o=opt, cid=ev["id"]: (self.link.send({"type": "confirm", "id": cid,
                                                                                        "decision": o}),
                                                                         self._clear_actions()))
                self.actions.addWidget(b)
            self.adjustSize()
        elif kind == "image":
            self.answer += "\n\n*(Made an image: open the full chat to see it.)*"
            self.out.setText(self.answer)
        elif kind == "error":
            self.status.setText(ev.get("message", "Something went wrong")[:80])
        elif kind == "done" and self.busy:
            self.busy = False
            self.input.setPlaceholderText("Ask a follow-up…")
            ms = ev.get("ms")
            self.status.setText(f"Answered in {ms / 1000:.1f} s" if ms else "Ready")
            if not self.answer.strip():
                self.out.setText("(No answer. Open the full chat for details.)")
            self.adjustSize()


def _escape(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
