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

W = 440        # the panel's width (Bigger makes it wider and taller)
GOO_M = 16     # a slim glow around it (less to cover your screen)


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
    """A small, quiet chat under the orb: the whole conversation, a Stop button, and a Bigger/Smaller toggle,
    so you never need the full chat window. Fades back when you work elsewhere and tucks itself away."""

    open_full = Signal()
    talk = Signal()  # the mic button

    def __init__(self, url: str, accent: str = "#6d5dfc"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.big = False
        self.setFixedWidth(W + 2 * GOO_M)
        self._goo_t = 0.0
        self._goo = QTimer(self)
        self._goo.timeout.connect(self._tick_goo)
        self._goo.start(60)
        self.accent = accent
        self.conversation_id = None
        self.answer = ""
        self.busy = False
        self._bubble = None  # Click's message being written right now
        self.link = _Link(url)
        self.link.event.connect(self._on_event)
        self.stop_fn = lambda: None  # set by the app: stops lessons, tasks, music, speech on this computer
        self.context_fn = lambda: ""  # set by the app: what lesson step you're on, so questions make sense
        self._voice_turn = False
        self._idle = QTimer(self)  # tuck away after a while unused (never while working)
        self._idle.setSingleShot(True)
        self._idle.setInterval(60000)
        self._idle.timeout.connect(self._maybe_tuck)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14 + GOO_M, 10 + GOO_M, 14 + GOO_M, 10 + GOO_M)
        lay.setSpacing(8)
        # header: status on the left; small text buttons on the right
        head = QHBoxLayout()
        head.setSpacing(2)
        self.status = QLabel("Connecting to Click…")
        self.status.setStyleSheet("color: rgba(255,255,255,0.55); font-size: 11.5px;")
        self.new = self._link_btn("New", "Start a new conversation", self._new)
        self.size_btn = self._link_btn("Bigger", "Make this chat bigger or smaller", self.toggle_size)
        self.full = self._link_btn("Full chat", "Open the full chat window", self.open_full.emit)
        head.addWidget(self.status, 1)
        for b_ in (self.new, self.size_btn, self.full):
            head.addWidget(b_)
        # the conversation
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
                                  "QScrollBar:vertical { width: 6px; background: transparent; }"
                                  "QScrollBar::handle:vertical { background: rgba(255,255,255,0.25); border-radius: 3px; }")
        self.thread = QWidget()
        self.msgs = QVBoxLayout(self.thread)
        self.msgs.setContentsMargins(0, 0, 4, 0)
        self.msgs.setSpacing(8)
        self.msgs.addStretch(1)
        self.scroll.setWidget(self.thread)
        self.scroll.hide()
        self.out = QLabel("")  # kept for the "thinking" line and older callers
        self.actions = QHBoxLayout()
        self.actions.setSpacing(6)
        # one-tap ideas (only before the first question)
        self.chips = QWidget()
        chip_row = QHBoxLayout(self.chips)
        chip_row.setContentsMargins(0, 0, 0, 0)
        chip_row.setSpacing(6)
        for label, ask in (("Teach me something", "teach me how to make a new folder"),
                           ("Play music", "play some music"), ("Focus 25 min", "focus for 25 minutes")):
            c = QPushButton(label)
            c.setCursor(Qt.PointingHandCursor)
            c.setStyleSheet("QPushButton { background: rgba(34,211,238,0.10); color: #e0faff; border: 1px solid "
                            "rgba(34,211,238,0.30); border-radius: 11px; padding: 4px 9px; font-size: 11.5px; }"
                            "QPushButton:hover { background: rgba(34,211,238,0.22); }")
            c.clicked.connect(lambda _=False, q=ask: (self.input.setText(q), self._send()))
            c.setMinimumWidth(c.fontMetrics().horizontalAdvance(label) + 26)
            chip_row.addWidget(c)
        chip_row.addStretch()
        # input row: text, mic, and Stop while working
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask or tell Clicky anything…")
        self.input.returnPressed.connect(self._send)
        self._typing = QTimer(self)
        self._typing.setSingleShot(True)
        self._typing.setInterval(350)  # start working on the answer as soon as you pause
        self._typing.timeout.connect(lambda: self.busy or self.link.send(
            {"type": "typing", "text": self.input.text(), "conversation_id": self.conversation_id, "brief": True}))
        self.input.textEdited.connect(self._edited)
        self.input.setStyleSheet(
            "QLineEdit { background: rgba(255,255,255,0.08); color: #fff; border: 1px solid rgba(255,255,255,0.14);"
            f" border-radius: 12px; padding: 9px 12px; font-size: 14px; selection-background-color: {accent}; }}"
            f"QLineEdit:focus {{ border-color: {accent}; }}")
        self.mic = MicButton(accent)
        self.mic.setToolTip("Talk to Clicky (Control + Space)")
        self.mic.clicked.connect(self.talk.emit)
        self.stop = QPushButton("Stop")
        self.stop.setCursor(Qt.PointingHandCursor)
        self.stop.setToolTip("Stop whatever Clicky is doing")
        self.stop.setStyleSheet("QPushButton { background: rgba(248,113,113,0.18); color: #fecaca; border: 1px solid "
                                "rgba(248,113,113,0.55); border-radius: 12px; padding: 8px 12px; font-size: 13px; }"
                                "QPushButton:hover { background: rgba(248,113,113,0.32); }")
        self.stop.clicked.connect(self.stop_all)
        self.stop.hide()
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.input, 1)
        row.addWidget(self.stop)
        row.addWidget(self.mic)
        lay.addLayout(head)
        lay.addWidget(self.scroll)
        lay.addWidget(self.chips)
        lay.addLayout(self.actions)
        lay.addLayout(row)
        self._fit()

    def _link_btn(self, text, tip, fn):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setFlat(True)
        b.setToolTip(tip)
        b.setStyleSheet("QPushButton { color: rgba(255,255,255,0.55); border: none; font-size: 11.5px; padding: 2px 5px; }"
                        "QPushButton:hover { color: #fff; }")
        b.clicked.connect(fn)
        return b

    # ---- size ------------------------------------------------------------------------------------------------
    def _max_thread_h(self) -> int:
        scr = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        return int(scr.height() * 0.62) if self.big else 300

    def _fit(self):
        """Grow with the conversation, up to a limit (Bigger raises it)."""
        self._fit_now()
        QTimer.singleShot(0, self._fit_now)  # again once Qt has laid out the newest message

    def _fit_now(self):
        if not self.scroll.isHidden():
            self.thread.setFixedWidth(self.scroll.viewport().width() or (self.width() - 2 * GOO_M - 28))
            self.thread.layout().activate()
            want = self.thread.layout().heightForWidth(self.thread.width()) if self.thread.layout().hasHeightForWidth() \
                else self.thread.sizeHint().height()
            self.scroll.setFixedHeight(max(70, min(want + 8, self._max_thread_h())))
        self.adjustSize()

    def toggle_size(self):
        self.big = not self.big
        self.size_btn.setText("Smaller" if self.big else "Bigger")
        self.setFixedWidth((W + 140 if self.big else W) + 2 * GOO_M)
        self._fit()
        self._scroll_end()
        if getattr(self, "_bubble_ref", None) is not None:
            self.place_under(self._bubble_ref)

    def _scroll_end(self):
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))

    # ---- messages ---------------------------------------------------------------------------------------------
    def _add_msg(self, text: str, mine: bool) -> QLabel:
        lab = QLabel()
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.PlainText if mine else Qt.MarkdownText)
        lab.setText(text)
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        lab.setOpenExternalLinks(True)
        if mine:
            lab.setStyleSheet(f"background: {self.accent}; color: #fff; border-radius: 12px; padding: 7px 11px;"
                              " font-size: 13.5px;")
            wrap = QHBoxLayout()
            wrap.addStretch(1)
            wrap.addWidget(lab)
            lab.setMaximumWidth(int(self.width() * 0.72))
            self.msgs.insertLayout(self.msgs.count(), wrap)
        else:
            lab.setStyleSheet("color: #f4f4f5; font-size: 14px; padding: 2px 2px;")
            self.msgs.insertWidget(self.msgs.count(), lab)
        while self.msgs.count() > 41:  # keep the last 20 exchanges
            item = self.msgs.takeAt(1)
            w_ = item.widget() or (item.layout().itemAt(1).widget() if item.layout() else None)
            if w_:
                w_.deleteLater()
        self.scroll.show()
        self.chips.hide()
        self._fit()
        self._scroll_end()
        return lab

    # ---- look ------------------------------------------------------------------------------------------------
    def _edited(self, _t: str):
        self._typing.start()
        self._idle.start()

    def _tick_goo(self):
        from .tile import reduce_motion

        if self.isVisible():
            self._goo_t += 0.02 if reduce_motion() else 0.03
            self.update()
            if self.busy and not self.answer and self._bubble is not None:
                n = int(self._goo_t * 2.5) % 4
                self._bubble.setText(f"<span style='color:#7dd3fc'>Thinking{'.' * n}</span>")

    def paintEvent(self, _):
        from PySide6.QtCore import QRectF

        from .tile import paint_goo

        p = QPainter(self)
        paint_goo(p, QRectF(self.rect()), GOO_M, 18, self._goo_t, self.accent, off=4, amp=2.5)
        p.end()

    def changeEvent(self, e):
        from PySide6.QtCore import QEvent

        if e.type() == QEvent.ActivationChange:
            if self.isActiveWindow():
                self.setWindowOpacity(1.0)
                self._idle.stop()
            elif self.isVisible():
                self.setWindowOpacity(0.86)  # you're working elsewhere: step back a little
                self._idle.start()
        super().changeEvent(e)

    def _maybe_tuck(self):
        if self.isVisible() and not self.busy and not self.isActiveWindow() and not self.underMouse() \
                and not getattr(self, "keep_fn", lambda: False)():
            self.hide()

    # ---- showing / hiding ------------------------------------------------------------------------------------
    def place_under(self, bubble):
        self._bubble_ref = bubble
        geo = (bubble.screen() or QApplication.primaryScreen()).availableGeometry()
        b = bubble.frameGeometry()
        w_ = self.width() - 2 * GOO_M
        x = min(max(geo.left() + 8, b.right() - w_ + 2), geo.right() - w_ - 8)
        self.move(x - GOO_M, b.bottom() + 2 - GOO_M + 10)

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
        self._anim.setDuration(140)
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
        while self.msgs.count() > 1:
            item = self.msgs.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                for i in range(item.layout().count()):
                    w_ = item.layout().itemAt(i).widget()
                    if w_:
                        w_.deleteLater()
        self.scroll.hide()
        self.chips.show()
        self._clear_actions()
        self._fit()
        self.input.setFocus()

    def stop_all(self):
        """The Stop button: stop the answer AND whatever Clicky is doing on this computer."""
        self.link.send({"type": "stop"})
        try:
            self.stop_fn()
        except Exception:  # noqa: BLE001
            pass
        self.set_status("Stopped")
        self._finish(stopped=True)

    def offer(self, label: str, question: str, on_click=None, send: bool = True):
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
            if on_click:
                on_click()
            lay_ = self.chips.layout()
            for i in range(lay_.count()):
                w_ = lay_.itemAt(i).widget()
                if w_ is not None and w_ is not b:
                    w_.show()
            if send:
                self.send_text(question)

        b.clicked.connect(go)
        lay_ = self.chips.layout()
        for i in range(lay_.count()):  # the offer replaces the usual suggestions (no crowding)
            w_ = lay_.itemAt(i).widget()
            if w_ is not None:
                w_.hide()
        lay_.insertWidget(0, b)
        self._offer_btn = b
        if not self.busy:
            self.chips.show()
        self._fit()

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
        self.status.setText(QFontMetrics(self.status.font()).elidedText(text, Qt.ElideRight, 190))

    def set_listening(self, on: bool, note: str = ""):
        self.mic.set_on(on)
        if on:
            self.input.setPlaceholderText("Listening… just talk")
            self.set_status(note or "Listening…")
        else:
            self.input.setPlaceholderText("Ask or tell Clicky anything…")
            if note:
                self.set_status(note)

    def _send(self):
        text = self.input.text().strip()
        if not text:
            return
        if getattr(self, "command_fn", lambda t: False)(text):  # next / back / stop... handled right here
            self.input.clear()
            return
        if self.busy:
            self.set_status("Still working on the last one. Press Stop, or wait a moment.")
            return
        sent = (self.context_fn() or "") + text  # e.g. "(I'm on step 2 of the lesson ...) where is that?"
        if not self.link.send({"type": "send", "text": sent, "conversation_id": self.conversation_id, "uploads": [],
                               "brief": True}):
            self.set_status("Not connected yet. Try again in a moment.")
            return
        self.busy = True
        self.answer = ""
        self.input.clear()
        self._add_msg(text, mine=True)
        self._bubble = self._add_msg("", mine=False)
        self.stop.show()
        self.mic.hide()
        self.set_status("Thinking…")

    def _finish(self, stopped: bool = False):
        self.busy = False
        self.stop.hide()
        self.mic.show()
        if self._bubble is not None and not self.answer.strip():
            self._bubble.setText("*(Stopped.)*" if stopped else "*(No answer. Open the full chat for details.)*")
        self._bubble = None
        self._fit()

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
        elif kind == "conversation":
            self.conversation_id = ev.get("id")
        elif kind == "token" and self.busy:
            self.answer += ev.get("text", "")
            if self._bubble is not None:
                self._bubble.setText(self.answer)
            self._fit()
            self._scroll_end()
        elif kind == "tool" and ev.get("status") == "running":
            self.set_status(ev.get("caption") or "Working…")
        elif kind == "confirm_request":
            self._clear_actions()
            ask = QLabel(ev.get("title", "Click wants to do something"))
            ask.setWordWrap(True)
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
            self._fit()
        elif kind == "image" and self.busy:
            self.answer += "\n\n*(Made an image: open the full chat to see it.)*"
            if self._bubble is not None:
                self._bubble.setText(self.answer)
        elif kind == "error":
            self.set_status(ev.get("message", "Something went wrong")[:120])
            if self.busy and "Still answering" not in ev.get("message", ""):
                self._finish()
        elif kind == "done" and self.busy:
            ms = ev.get("ms")
            self.set_status("Stopped" if ev.get("stopped") else (f"Answered in {ms / 1000:.1f} s" if ms else "Ready"))
            answer = self.answer
            self._finish(stopped=bool(ev.get("stopped")))
            if answer.strip() and not ev.get("stopped") and (
                    self._voice_turn or getattr(self, "speak_enabled", lambda: False)()):
                from .voice import speak as say_it

                say_it(answer)  # you asked out loud: Clicky answers out loud
            self._voice_turn = False


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
