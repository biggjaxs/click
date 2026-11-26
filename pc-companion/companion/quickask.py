"""Ask Click: the native panel that opens when you click the floating orb.

Type a question, the answer streams in right there. Approvals ("Click wants to read a file")
appear as buttons. "Open full chat" opens the bigger chat window. This panel talks to Click's chat
part directly, so it works even if the full chat window can't load.
"""
from __future__ import annotations

import json
import threading

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

W = 420        # the flat panel's width
GOO_M = 40     # room around it for the flowing goo (same look as the full chat and the orb)


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
    talk = Signal()  # the mic button

    def __init__(self, url: str, accent: str = "#6d5dfc"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setFixedWidth(W + 2 * GOO_M)
        self._goo_t = 0.0
        self._goo = QTimer(self)
        self._goo.timeout.connect(self._tick_goo)
        self._goo.start(80)  # slow and gentle
        self.accent = accent
        self.conversation_id = None
        self.answer = ""
        self.busy = False
        self.link = _Link(url)
        self.link.event.connect(self._on_event)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18 + GOO_M, 16 + GOO_M, 18 + GOO_M, 14 + GOO_M)
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
        self.out.setStyleSheet("color: #f4f4f5; font-size: 14px; line-height: 140%;")
        self.out.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(self.out)
        self.scroll.setMaximumHeight(380)
        self.scroll.hide()
        self.actions = QHBoxLayout()
        self.actions.setSpacing(6)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask Click anything…")
        self.input.returnPressed.connect(self._send)
        # Read ahead while the user types (pressing Enter then only leaves a few words to process).
        self._typing = QTimer(self)
        self._typing.setSingleShot(True)
        self._typing.setInterval(350)  # start working on the answer as soon as you pause
        self._typing.timeout.connect(lambda: self.busy or self.link.send(
            {"type": "typing", "text": self.input.text(), "conversation_id": self.conversation_id, "brief": True}))
        self.input.textEdited.connect(self._edited)
        self.mic = MicButton(accent)
        self.mic.setToolTip("Talk to Clicky (Control + Space)")
        self.mic.clicked.connect(self.talk.emit)
        self._voice_turn = False
        self.context_fn = lambda: ""  # set by the app: what lesson step you're on, so questions make sense
        self.input.setStyleSheet(
            "QLineEdit { background: rgba(255,255,255,0.08); color: #fff; border: 1px solid rgba(255,255,255,0.14);"
            f" border-radius: 12px; padding: 10px 12px; font-size: 14px; selection-background-color: {accent}; }}"
            f"QLineEdit:focus {{ border-color: {accent}; }}")
        # One-tap ideas: each uses Clicky's instant paths, so it answers in about a second.
        self.chips = QWidget()
        chip_row = QHBoxLayout(self.chips)
        chip_row.setContentsMargins(0, 0, 0, 0)
        chip_row.setSpacing(6)
        for label, ask in (("Where's Wi\u2011Fi?", "where is wifi"), ("How to email", "show me how to send an email"),
                           ("Hi Clicky", "show me clicky")):
            c = QPushButton(label)
            c.setCursor(Qt.PointingHandCursor)
            c.setStyleSheet("QPushButton { background: rgba(34,211,238,0.10); color: #e0faff; border: 1px solid "
                            "rgba(34,211,238,0.35); border-radius: 11px; padding: 4px 9px; font-size: 11.5px; }"
                            "QPushButton:hover { background: rgba(34,211,238,0.22); }")
            c.clicked.connect(lambda _=False, q=ask: (self.input.setText(q), self._send()))
            c.setMinimumWidth(c.fontMetrics().horizontalAdvance(label) + 26)
            chip_row.addWidget(c)
        chip_row.addStretch()
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
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.input, 1)
        row.addWidget(self.mic)
        lay.addLayout(row)
        lay.addWidget(self.chips)
        lay.addWidget(self.scroll)
        lay.addLayout(self.actions)
        lay.addLayout(foot)
        self.adjustSize()

    # ---- look: dark glass card with a soft accent glow ---------------------------------------------------
    def _edited(self, _t: str):
        self._typing.start()
        if not self.busy and getattr(self, "_preview_for", None) and _norm(self.input.text()) != self._preview_for:
            self._preview_for = None  # the preview was for different words
            self.scroll.hide()
            self.adjustSize()

    def _tick_goo(self):
        from .tile import reduce_motion

        if self.isVisible():
            if not reduce_motion():
                self._goo_t += 0.08
            else:
                self._goo_t += 0.02  # still animate the "thinking" dots slowly
            self.update()
            if self.busy and not self.answer:  # "Clicky is thinking..." until the first word arrives
                n = int(self._goo_t * 2.5) % 4
                self.out.setText(f"<span style='color:#7dd3fc'>Clicky is thinking{'.' * n}</span>")

    def paintEvent(self, _):
        from PySide6.QtCore import QRectF

        from .tile import paint_goo

        p = QPainter(self)
        paint_goo(p, QRectF(self.rect()), GOO_M, 20, self._goo_t, self.accent, off=10, amp=9)
        p.end()

    # ---- showing / hiding ------------------------------------------------------------------------------------
    def place_under(self, bubble):
        geo = (bubble.screen() or QApplication.primaryScreen()).availableGeometry()
        b = bubble.frameGeometry()
        x = min(max(geo.left() + 8, b.right() - W + 2), geo.right() - W - 8)
        self.move(x - GOO_M, b.bottom() + 2 - GOO_M + 14)

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
        self.chips.show()
        self.scroll.hide()
        self._clear_actions()
        self.adjustSize()
        self.input.setFocus()

    def offer(self, label: str, question: str):
        """A one-tap offer at the front of the suggestions (e.g. "Help with this pop-up")."""
        old = getattr(self, "_offer_btn", None)
        if old is not None:
            old.deleteLater()
        b = QPushButton(label)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet("QPushButton { background: rgba(250,204,21,0.16); color: #fff7d6; border: 1px solid "
                        "rgba(250,204,21,0.55); border-radius: 11px; padding: 4px 9px; font-size: 11.5px; }"
                        "QPushButton:hover { background: rgba(250,204,21,0.3); }")
        b.setMinimumWidth(b.fontMetrics().horizontalAdvance(label) + 26)

        def go():
            b.deleteLater()
            self._offer_btn = None
            lay = self.chips.layout()
            for i in range(lay.count()):
                w = lay.itemAt(i).widget()
                if w is not None and w is not b:
                    w.show()
            self.send_text(question)

        b.clicked.connect(go)
        lay = self.chips.layout()
        for i in range(lay.count()):  # the offer replaces the usual suggestions (no crowding)
            w = lay.itemAt(i).widget()
            if w is not None:
                w.hide()
        lay.insertWidget(0, b)
        self._offer_btn = b
        if not self.busy:
            self.chips.show()
        self.status.setText("Clicky can explain that pop-up for you.")
        self.adjustSize()

    def send_text(self, text: str, voice: bool = False):
        """Ask something as if typed (used for voice): the answer is spoken back when voice=True."""
        self.input.setText(text)
        self._voice_turn = voice
        self._send()

    def set_status(self, text: str):
        """One tidy line: long messages end with … (the full text shows when you hover)."""
        from PySide6.QtGui import QFontMetrics

        self.status.setWordWrap(False)
        self.status.setToolTip(text)
        self.status.setText(QFontMetrics(self.status.font()).elidedText(text, Qt.ElideRight, 165))

    def set_listening(self, on: bool, note: str = ""):
        self.mic.set_on(on)
        if on:
            self.input.setPlaceholderText("Listening… just talk")
            self.set_status(note or "Listening…")
        else:
            self.input.setPlaceholderText("Ask Click anything…")
            if note:
                self.set_status(note)

    def _send(self):
        text = self.input.text().strip()
        if not text:
            return
        if getattr(self, "command_fn", lambda t: False)(text):  # "next" / "repeat" / "back" / "stop" in a lesson
            self.input.clear()
            return
        if self.busy:
            self.status.setText("Still answering the last question… press Enter again in a moment.")
            return
        sent = (self.context_fn() or "") + text  # e.g. "(I'm on step 2 of the lesson ...) where is that?"
        if not self.link.send({"type": "send", "text": sent, "conversation_id": self.conversation_id, "uploads": [],
                               "brief": True}):
            self.status.setText("Not connected yet. Try again in a moment.")
            return
        self.busy = True
        self.answer = ""
        self.input.clear()
        self.input.setPlaceholderText("Click is thinking…")
        self.out.setText("<span style='color:#7dd3fc'>Clicky is thinking</span>")
        self.chips.hide()
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
                self.set_status("Ready" if ev["ok"] else (f"Problem: {err[:120]}" if err else "Connecting to Click…"))
        elif kind == "preview":
            # the answer, worked out while you type; press Enter and it's there instantly
            if not self.busy and _norm(ev.get("for", "")) == _norm(self.input.text()) and ev.get("text", "").strip():
                self._preview_for = _norm(self.input.text())
                self.out.setText(f"<span style='color:#94a3b8'><i>{_escape(ev['text'])}</i></span>"
                                 + ("" if ev.get("done") else " <span style='color:#7dd3fc'>…</span>"))
                self.chips.hide()
                self.scroll.show()
                self.adjustSize()
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
            elif self._voice_turn or getattr(self, "speak_enabled", lambda: False)():
                from .voice import speak as say_it

                say_it(self.answer)  # you asked out loud: Clicky answers out loud
            self._voice_turn = False
            self.adjustSize()


class MicButton(QPushButton):
    """A round microphone button that glows while Clicky is listening."""

    def __init__(self, accent: str):
        super().__init__()
        self.accent = accent
        self.on = False
        self.level = 0.0
        self.setFixedSize(38, 38)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("QPushButton { border: none; background: transparent; }")

    def set_on(self, on: bool):
        self.on = on
        self.level = 0.0
        self.update()

    def set_level(self, lvl: float):
        self.level = lvl
        self.update()

    def paintEvent(self, _):
        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtGui import QColor, QPen

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(19, 19)
        bg = QColor(self.accent if self.on else "#2a2a33")
        if self.on:
            glow = QColor(self.accent)
            glow.setAlpha(int(60 + 120 * self.level))
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawEllipse(c, 18, 18)
        p.setPen(Qt.NoPen)
        p.setBrush(bg if self.on else QColor(255, 255, 255, 26))
        p.drawEllipse(c, 15, 15)
        pen = QPen(QColor("white"), 1.8)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(15.5, 10, 7, 12), 3.5, 3.5)          # the mic
        p.drawArc(QRectF(12.5, 14, 13, 11), 200 * 16, 140 * 16)        # its stand
        p.drawLine(QPointF(19, 25), QPointF(19, 28))
        p.end()


_say = None


def speak(text: str) -> None:
    """Read an answer aloud with the Mac's own offline voice (Settings > Accessibility > Spoken Content)."""
    global _say
    import re
    import subprocess
    import sys

    if sys.platform != "darwin":
        return
    plain = re.sub(r"[*_`#>\[\]()]", "", text)[:600]
    try:
        if _say and _say.poll() is None:
            _say.terminate()
        _say = subprocess.Popen(["say", plain], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass


def _norm(t: str) -> str:
    import re

    return re.sub(r"[\s?.!]+$", "", " ".join((t or "").lower().split()))


def _escape(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
