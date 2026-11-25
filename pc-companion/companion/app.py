"""Click Companion: the background app on your computer (Windows or Mac).

Start it with:  python -m companion
"""
from __future__ import annotations

import asyncio
import logging
import socket
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer

from . import config  # noqa: F401 - sets up the path to the shared protocol package
from .config import Settings

log = logging.getLogger("click")


class App:
    def __init__(self):
        from PySide6.QtWidgets import QApplication

        from .actions import Actions
        from .files import FileService
        from .gui import Bridge, ConfirmPopup, GhostController, Tray
        from .hardware import detect, pick_models
        from .imagegen import ImageGenerator
        from .inference import Inference
        from .platforms import get_platform
        from .screen import ScreenService
        from .server import CompanionServer

        self.qt = QApplication.instance() or QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setApplicationName("Click")
        if "--selftest" not in sys.argv:
            _move_to_applications()   # opened from the download / disk image: offer to install properly
            _finish_install()         # stamp this version with this Mac's own signature (keeps permissions)
            _close_other_copies()     # an older Click still running from somewhere else: close it
        if _already_running():
            sys.exit(0)  # the running Click was asked to show itself
        _hide_dock_icon()
        self.settings = Settings()
        self.platform = get_platform()
        self.hardware = detect()
        self.models = pick_models(self.hardware, self.settings)
        log.info("Hardware: %s -> models %s", self.hardware, self.models)
        self.paused = False
        self.bridge = Bridge()
        self.ghost = GhostController(self.bridge, self.settings, self.platform)
        self.screen = ScreenService(self.platform, self.settings)
        self.files = FileService(self.settings)
        self.inference = Inference(self.settings, self.models)
        self.imagegen = ImageGenerator(self.models, self.settings)
        self.actions = Actions(self.bridge, self.ghost, self.screen, self.platform, self.settings)
        from .skills import Skills

        self.skills = Skills(self)
        self.server = CompanionServer(self)
        self._popups = []

        def show_confirm(req):
            popup = ConfirmPopup(req, self.ghost)
            self._popups.append(popup)
            popup.destroyed.connect(lambda *_: self._popups.remove(popup) if popup in self._popups else None)
            popup.show()
            popup.raise_()
            popup.activateWindow()

        self.bridge.confirmRequest.connect(show_confirm)
        self.bridge.notify.connect(self._show_message)
        self.bridge.toggleTile.connect(lambda: self.toggle_ask() if getattr(self, "ask", None) else self.toggle_tile())
        self.bridge.ghostSummon.connect(self.ghost.summon)
        self.tray = Tray(self)
        self.selftest = "--selftest" in sys.argv
        self._start_hotkeys()
        if not self.selftest:
            self.platform.request_permissions()
        self.server.start()
        self.tile = None
        self.ask = None
        self.bubble = None
        self.hub = None
        self.busy = 0
        self._setup_thread = None
        _listen_for_reopen(self)
        self.qt.applicationStateChanged.connect(self._activated)
        QTimer.singleShot(0, self._start_click)

    # ---- startup: the box part (on this computer or a Pi), then the tile ------------------------------------
    @property
    def mode(self) -> str:
        # Runs on this computer unless a real Click box (Pi) was paired via "Use a Click box".
        return "box" if self.settings.get("mode") == "box" and self.settings.get("box_paired") else "standalone"

    def _start_click(self) -> None:
        try:
            self._start_click_inner()
        except Exception as exc:  # noqa: BLE001 - show the problem instead of silently not opening
            log.exception("Click failed to start")
            if self.tile:
                self.tile.setup.title.setText("Click couldn't start")
                self.tile.setup.sub.setText(f"{exc}\n\nDetails: ~/Library/Application Support/ClickCompanion/companion.log")
                self.tile.show_setup()
                self.tile.show_tile()
            if self.bubble:
                self.bubble.set_state("error")

    def _start_click_inner(self) -> None:
        from .bubble import LiquidBubble
        from .config import config_dir
        from .tile import Tile

        self.bubble = LiquidBubble(self.settings["cursor"]["color"])
        self.bubble.clicked.connect(self.toggle_ask)
        self.bubble.moved.connect(self._tile_moved)
        self.bubble.set_state("starting")
        url = f"http://{self.settings['pi_host']}"
        self.tile = Tile(url, str(config_dir() / "web"), self.settings["cursor"]["color"])
        self.tile.place_under(self.bubble)
        self._tile_moved()
        if self.mode == "standalone":
            from .standalone import EmbeddedHub

            self.hub = EmbeddedHub(self.settings, self.models["chat"])
            self.hub.start()
            cert = self.hub.ensure_paired(self.server.identity.cert, self.settings["pc_id"], self.settings["pc_name"],
                                          self.settings["port"])
            if cert:
                self.settings.set("pi_cert_pem", cert)
                self.server.restart_tls()
            self.tile.url = self.hub.url
            self.tile.hub = self.hub
        from .quickask import QuickAsk

        self.ask = QuickAsk(self.tile.url, self.settings["cursor"]["color"])
        self.ask.hub_error = lambda: self.hub.last_error if self.hub else ""
        self.ask.speak_enabled = lambda: bool(self.settings.get("speak_answers", False))
        self.screen.own_rects = self._own_rects
        if not self.selftest:
            for w in (self.ask, self.tile):
                w.winId()
                self.platform.exclude_from_capture(w)
        self.ask.open_full.connect(lambda: (self.ask.hide(), self.toggle_tile() if not self.tile.isVisible() else None))
        self._started = time.time()
        self._watchdog = QTimer()
        self._watchdog.timeout.connect(self._watch)
        self._watchdog.start(1000)
        if self.selftest:
            QTimer.singleShot(500, self._selftest_check)
            return
        self._warm()
        from . import __version__

        # The welcome shows the first time and once after each update (menu bar > Show the welcome again
        # brings it back any time). Otherwise Click opens straight to the orb and Ask box.
        if self.settings.get("welcome_version") != __version__ or not self.settings.get("setup_done") \
                or not self._models_present():
            self._onboard()
        else:
            self._normal_launch()

    def _own_rects(self):
        """Where Click's own windows are (in screen coordinates), so 'find' never points at them."""
        from .platforms import Rect

        out = []
        for w in (self.ask, self.tile, self.bubble, getattr(self, "onboarding", None)):
            if w is None or not w.isVisible():
                continue
            g = w.frameGeometry()
            for o in self.ghost.overlays:
                sg = o.smap.screen.geometry()
                if sg.intersects(g):
                    sc = o.smap.scale
                    out.append(Rect(int(o.smap.left + (g.x() - sg.x()) * sc), int(o.smap.top + (g.y() - sg.y()) * sc),
                                    int(g.width() * sc), int(g.height() * sc)))
                    break
        return out

    def _show_orb(self) -> None:
        if not self.selftest and not self.bubble.isVisible():
            self.bubble.show()
            self.platform.configure_overlay(self.bubble, click_through=False)  # float above every app, still clickable
            self._tile_moved()

    def _normal_launch(self) -> None:
        """Start: the orb fades in, Clicky waves hello, Ask Click opens, and any missing permission is
        stated plainly (so Clicky never fails silently)."""
        self._show_orb()
        self.tile.url and self.tile.show_chat()  # load the full chat in the background, ready when wanted
        QTimer.singleShot(700, lambda: self.ask and not self.ask.isVisible() and self.ask.toggle(self.bubble))
        QTimer.singleShot(1400, self.ghost.hello)
        QTimer.singleShot(2500, self._report_permissions)

    def _report_permissions(self) -> None:
        problems = self.platform.check_permissions()
        if problems and self.ask:
            self.ask.status.setWordWrap(True)
            self.ask.status.setText("Clicky needs permission: " + " ".join(problems))
            self.notify("Clicky needs permission to see and point: " + " ".join(problems))

    def check_clicky(self) -> None:
        """Menu bar > Check Clicky: test everything Clicky needs and show the results."""
        from PySide6.QtWidgets import QMessageBox

        lines = []

        def ok(cond, good, bad):
            lines.append(("\u2705 " if cond else "\u274c ") + (good if cond else bad))

        problems = self.platform.check_permissions()
        ok(not problems, "Permissions: Screen Recording and Accessibility are on", "Permissions: " + " ".join(problems))
        for label, getter in (("Dock icons", self.platform.dock_items), ("Menu bar items", self.platform.menu_bar_items),
                              ("Desktop icons", self.platform.desktop_items)):
            try:
                n = len(getter())
                ok(n > 0 or label == "Desktop icons", f"{label}: sees {n}", f"{label}: sees none (Accessibility?)")
            except Exception as exc:  # noqa: BLE001
                ok(False, "", f"{label}: error {exc}")
        try:
            img, _, _, _ = self.screen.capture_image("monitor:1", None)
            ok(img.getbbox() is not None, f"Screenshots work ({img.width}x{img.height})", "Screenshots come back blank")
        except Exception as exc:  # noqa: BLE001
            ok(False, "", f"Screenshots fail: {exc}")
        ok(self._models_present(), f"AI engine ready ({self.models['chat']})", "AI engine or model missing (Ollama)")
        ok(bool(self.hub and self.hub.alive()) or self.mode == "box", "Chat part running",
           "Chat part not running: " + (self.hub.last_error if self.hub else ""))
        ok(self.server.pi_connected, "Clicky is connected to the chat", "Clicky isn't connected to the chat")
        ok(not self.paused, "Not paused", "Everything is paused (menu bar > Resume)")
        prev = config.config_dir() / "crash-previous.log"
        if prev.exists():
            ok(False, "", f"Click closed unexpectedly before. Details: {prev}")
        self.ghost.summon("This is Clicky. If you can see me, the cursor works!", True)
        QTimer.singleShot(3500, self.ghost.rest)
        box = QMessageBox()
        box.setWindowTitle("Check Clicky")
        box.setText("\n".join(lines))
        box.setInformativeText("Take a screenshot of this and send it if something has a \u274c.")
        box.exec()

    def _warm(self) -> None:
        """Load the AI model into memory now, so the first question doesn't wait for it."""
        if self.server.loop:
            asyncio.run_coroutine_threadsafe(self.inference.warm_up(), self.server.loop)

    def _onboard(self) -> None:
        """First launch: a short guided welcome; setup runs in the background meanwhile."""
        from .onboarding import Onboarding
        from .setup import mac_permission_rows, run_setup
        from .tile import SetupView

        view = SetupView()
        self.onboarding = Onboarding(self.settings["cursor"]["color"], view,
                                     lambda: mac_permission_rows(self.platform))
        self.onboarding.show_cursor.connect(self.ghost.demo)

        def setup_done(ok: bool, message: str):
            if ok:
                self.settings.set("setup_done", True)
                view.title.setText("Ready")
                view.sub.setText("Click's brain is downloaded and loaded.")
            else:
                view.title.setText("Setup needs a hand")
                view.sub.setText(message)
                view.cont.setText("Try again")
                view.cont.show()
                view.continue_clicked.connect(lambda: (view.cont.hide(), start()))
            self.onboarding.set_setup_done(ok)

        def start():
            models = list(dict.fromkeys([self.models["chat"], self.models["vision"]]))
            self._setup_thread = run_setup(view, self.settings["ollama_url"], models, setup_done)

        def finished():
            from . import __version__

            self.settings.set("onboarded", True)
            self.settings.set("welcome_version", __version__)
            self._normal_launch()

        self.onboarding.finished.connect(finished)
        self.onboarding.show()
        self.onboarding.raise_()
        self.onboarding.activateWindow()
        if self.settings.get("setup_done") and self._models_present():
            setup_done(True, "")  # already set up: nothing to download, Skip works at once
            self._show_orb()
        else:
            start()

    def _after_hub_start(self) -> None:
        cert = self.hub.ensure_paired(self.server.identity.cert, self.settings["pc_id"], self.settings["pc_name"],
                                      self.settings["port"])
        if cert:
            self.settings.set("pi_cert_pem", cert)
            self.server.restart_tls()
        if self.tile.url != self.hub.url:
            self.tile.url = self.hub.url
            self.ask.link.url = self.hub.url.replace("http://", "ws://") + "/ws"
        if self.tile.isVisible():
            self.tile.show_chat()

    def _update_status(self) -> None:
        """Header shows the truth: Ready / Starting / Paused, plus how much memory the AI is using."""
        import httpx

        if self.paused:
            self.tile.header.set_status("Paused", "#a1a1aa")
            return
        if not self.server.pi_connected:
            self.tile.header.set_status("Starting…", "#a1a1aa")
            return
        gb = 0.0
        try:
            ps = httpx.get(f"{self.settings['ollama_url']}/api/ps", timeout=1).json().get("models", [])
            gb = sum(m.get("size", 0) for m in ps) / 1e9
        except Exception:  # noqa: BLE001
            pass
        self.tile.header.set_status(f"Ready · AI using {gb:.1f} GB" if gb else "Ready", "#16a34a")

    def toggle_ask(self) -> None:
        """Clicking the orb opens the Ask panel (or the setup screen until Click is ready)."""
        if not self.settings.get("setup_done") and self.tile:
            self.toggle_tile()
            return
        if self.tile and self.tile.isVisible():
            self.tile.hide_tile()
        self.ask.toggle(self.bubble)

    def toggle_tile(self) -> None:
        if not self.tile:
            return
        if self.tile.isVisible():
            self.tile.hide_tile()
        else:
            self.tile.place_under(self.bubble)
            self.tile.show_tile()
            if self.tile.stack.currentWidget() is not self.tile.web and self.settings.get("setup_done"):
                self.tile.show_chat()

    def _watch(self) -> None:
        """Every second: keep the chat part alive and keep the bubble's mood in sync."""
        if self.hub and not self.hub.alive():
            self.bubble.set_state("error")
            now = time.time()
            if now - getattr(self, "_last_restart", 0) < min(60, 5 * (self.hub.failures + 1)):
                return  # back off: don't hammer a failing start
            self._last_restart = now
            self.hub.restart()
            if self.hub.alive():
                self._after_hub_start()
            return
        self._tick = getattr(self, "_tick", 0) + 1
        if self._tick % 5 == 1 and self.tile:
            self._update_status()
        if self.paused:
            self.bubble.set_state("paused")
        elif self.ghost.flags.get("control"):
            self.bubble.set_state("acting")
        elif self.busy > 0:
            self.bubble.set_state("thinking")
        elif not self.server.pi_connected:
            self.bubble.set_state("starting")
        else:
            self.bubble.set_state("idle")

    def _models_present(self) -> bool:
        import httpx

        try:
            tags = httpx.get(f"{self.settings['ollama_url']}/api/tags", timeout=2).json().get("models", [])
        except Exception:  # noqa: BLE001
            return False
        names = {m["name"] for m in tags} | {m["name"].removesuffix(":latest") for m in tags}
        return self.models["chat"] in names

    def _run_setup(self) -> None:
        from .setup import mac_permission_rows, run_setup

        view = self.tile.setup
        self.tile.show_setup()
        self.tile.header.set_status("Setting up…")

        def done(ok: bool, message: str):
            if not ok:
                view.title.setText("Setup needs a hand")
                view.sub.setText(message)
                view.cont.setText("Try again")
                view.cont.show()
                self._on_continue(self._run_setup)
                return
            self.settings.set("setup_done", True)
            view.title.setText("Click is ready")
            view.sub.setText("Everything runs privately on this computer.")
            rows = mac_permission_rows(self.platform)
            view.show_permissions(rows)
            if rows and not all(r[2] for r in rows):
                view.sub.setText("Turn on Click in each list below. If you just turned one on, press Check again; "
                                 "if it still shows as off, quit Click from the menu bar and open it again "
                                 "(macOS only notices some permissions after a restart).")
                view.add_recheck(lambda: view.show_permissions(mac_permission_rows(self.platform)))
            view.cont.setText("Start using Click")
            view.cont.show()
            self._on_continue(self._finish_setup)
            if not rows or all(r[2] for r in rows):
                self._finish_setup()

        view.cont.hide()
        models = list(dict.fromkeys([self.models["chat"], self.models["vision"]]))
        self._setup_thread = run_setup(view, self.settings["ollama_url"], models, done)

    def _activated(self, state) -> None:
        """Opening Click from Applications/Spotlight while it's running brings it back."""
        from PySide6.QtCore import Qt as _Qt

        if state == _Qt.ApplicationActive and self.bubble and not self.selftest and self.settings.get("onboarded"):
            self._show_orb()
            self.bubble.raise_()
            # Clicking Click in Applications/Dock/Spotlight while it runs brings Ask Click back.
            if self.settings.get("onboarded") and self.ask and not self.ask.isVisible() \
                    and not (self.tile and self.tile.isVisible()) and time.time() - getattr(self, "_started", 0) > 3:
                self.ask.toggle(self.bubble)

    def _on_continue(self, fn) -> None:
        view = self.tile.setup
        if getattr(self, "_continue_fn", None):
            view.continue_clicked.disconnect(self._continue_fn)
        self._continue_fn = fn
        view.continue_clicked.connect(fn)

    def _finish_setup(self) -> None:
        self.settings.set("setup_done", True)
        self.tile.show_chat()
        QTimer.singleShot(1200, self.ghost.demo)  # show the cursor once so you know what to look for

    def _tile_moved(self) -> None:
        """The cursor appears from the tile, so its movement is easy to follow."""
        if not self.bubble:
            return
        g0 = self.bubble.frameGeometry()
        from PySide6.QtCore import QPoint

        pos = QPoint(g0.left() - 26, g0.center().y() - 6)  # dock: just left of the orb
        for o in self.ghost.overlays:
            g = o.smap.screen.geometry()
            if g.contains(pos):
                lx, ly = pos.x() - g.x(), pos.y() - g.y()
                self.ghost.set_home(o.smap.left + lx * o.smap.scale, o.smap.top + ly * o.smap.scale)
                return

    def _selftest_ui(self) -> None:
        """Load the real chat page in the real chat window; report whether it displays."""
        def done(ok):
            print("SELFTEST UI", "OK" if ok else "FAILED", flush=True)
            self.qt.exit(0 if ok else 3)

        self.tile.web.loadFinished.connect(done)
        self.tile._want_chat = False
        self.tile.web.setUrl(__import__("PySide6.QtCore", fromlist=["QUrl"]).QUrl(self.tile.url + "/?tile=1"))
        QTimer.singleShot(60000, lambda: done(False))

    def _selftest_check(self, tries: int = 0) -> None:
        """Used by the automatic Mac build to prove the app starts: hub up + link connected."""
        import httpx

        try:
            st = httpx.get(f"{self.tile.url}/api/status", timeout=2).json()
            if st["pc"]["connected"]:
                print("SELFTEST OK", st["pc"]["pc_name"], flush=True)
                if "--ui" in sys.argv:
                    self._selftest_ui()
                    return
                self.qt.exit(0)
                return
        except Exception as exc:  # noqa: BLE001
            log.info("selftest waiting: %s", exc)
        if tries > 150:
            print("SELFTEST FAILED", flush=True)
            self.qt.exit(1)
            return
        QTimer.singleShot(500, lambda: self._selftest_check(tries + 1))

    # ---- status & events -------------------------------------------------------------------------------
    def _show_message(self, text: str):
        from PySide6.QtWidgets import QSystemTrayIcon

        self.tray.icon.showMessage("Click", text, QSystemTrayIcon.Information, 6000)

    def notify(self, text: str) -> None:
        self.bridge.notify.emit(text)

    def on_pi_connection(self, connected: bool) -> None:
        if self.mode == "box":
            self.notify("Connected to your Click box." if connected else "The Click box disconnected.")
        if not connected:
            self.bridge.indicator.emit("watch", False)

    def on_pi_event(self, service: str, action: str, payload: dict) -> None:
        if (service, action) == ("system", "settings"):
            cursor = self.settings["cursor"]
            for src, dst in (("cursor_color", "color"), ("cursor_style", "style"), ("cursor_label", "label")):
                if payload.get(src):
                    cursor[dst] = payload[src]
            self.settings.set("cursor", cursor)
            self.bridge.styleChanged.emit()
            self.bridge.indicator.emit("watch", bool(payload.get("watch_mode")) and not payload.get("paused"))
            self._set_paused(bool(payload.get("paused")), from_pi=True)
        elif (service, action) == ("cursor", "rest"):
            self.bridge.ghostRest.emit()
        elif (service, action) == ("cursor", "narrate"):
            self.bridge.narrate.emit(str(payload.get("text", ""))[:160])
        elif (service, action) == ("system", "pause"):
            self._set_paused(bool(payload.get("paused")), from_pi=True)

    async def capabilities(self) -> dict:
        return {
            "protocol": 1, "pc_name": self.settings["pc_name"], **self.hardware,
            "models": {"chat": self.models["chat"], "vision": self.models["vision"]},
            "vision_in_chat": self.inference.vision_in_chat,
            # On processor-only computers a screenshot costs many seconds to read; the OCR text is enough.
            "cpu_only": self.hardware.get("device") == "cpu",
            "installed_models": await self.inference.installed_models(),
            "loaded_models": await self.inference.loaded_models(),
            "image": self.imagegen.capability(),
            "services": dict(self.settings["permissions"]),
            "approved_folders": self.settings["approved_folders"],
        }

    # ---- pause / kill switch --------------------------------------------------------------------------------
    def _set_paused(self, paused: bool, from_pi: bool = False) -> None:
        self.paused = paused
        self.bridge.indicator.emit("paused", paused)
        if paused:
            self.bridge.ghostHide.emit()
            self.bridge.indicator.emit("watch", False)
            self.actions.killed.set()
            for job in list(self.imagegen._jobs):
                self.imagegen.cancel(job)
        else:
            self.actions.killed.clear()
        if not from_pi:
            self.server.send_event_threadsafe("system", "pause", {"paused": paused})

    def toggle_pause(self) -> None:
        self._set_paused(not self.paused)
        if self.bubble:
            self.bubble.set_state("paused" if self.paused else "idle")

    def kill(self) -> None:
        """Ctrl+Alt+Esc: stop all watching and actions instantly and release control."""
        self._set_paused(True, from_pi=True)
        self.server.send_event_threadsafe("system", "kill", {"reason": "hotkey"})
        self.notify("Stopped. Everything is paused. Resume from the menu bar icon or the chat.")

    def _start_hotkeys(self) -> None:
        """Ctrl+Alt+Esc stops everything; Cmd+Shift+Space (Ctrl+Shift+Space on Windows) shows/hides the tile."""
        if sys.platform == "darwin":
            self._start_mac_hotkeys()
            return
        try:
            from pynput import keyboard

            toggle = "<cmd>+<shift>+<space>" if sys.platform == "darwin" else "<ctrl>+<shift>+<space>"
            self._hotkey = keyboard.GlobalHotKeys({self.settings["kill_hotkey"]: self.kill,
                                                   toggle: self.bridge.toggleTile.emit})
            self._hotkey.daemon = True
            self._hotkey.start()
        except Exception as exc:  # noqa: BLE001
            log.warning("hotkeys unavailable: %s", exc)

    def _start_mac_hotkeys(self) -> None:
        """macOS: watch for the shortcuts with AppKit on the MAIN thread. (A keyboard listener on a background
        thread makes new macOS versions kill the app; that was the "Click just closes sometimes" bug.)"""
        try:
            from AppKit import NSEvent

            ctrl, opt, cmd, shift = 1 << 18, 1 << 19, 1 << 20, 1 << 17
            mask_keydown = 1 << 10

            def check(event):
                try:
                    code, flags = event.keyCode(), event.modifierFlags()
                    if code == 53 and flags & ctrl and flags & opt:
                        self.kill()
                    elif code == 49 and flags & cmd and flags & shift:
                        self.bridge.toggleTile.emit()
                except Exception:  # noqa: BLE001
                    log.exception("hotkey handler")

            def local(event):
                check(event)
                return event

            self._hotkey = (NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask_keydown, check),
                            NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask_keydown, local))
        except Exception as exc:  # noqa: BLE001
            log.warning("hotkeys unavailable: %s", exc)

    # ---- pairing / settings --------------------------------------------------------------------------------------
    def pair(self) -> None:
        import httpx
        from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox

        code, ok = QInputDialog.getText(
            None, "Pair with Click",
            "On your Click box's chat (http://click.local) open Settings, Your computer, Pair a computer.\n"
            "Type the 6-digit code shown there:",
            QLineEdit.Normal, "")
        if not ok or not code.strip():
            return
        host = self.settings["pi_host"]
        body = {"code": code.strip(), "pc_id": self.settings["pc_id"], "name": self.settings["pc_name"],
                "port": self.settings["port"], "cert_pem": self.server.identity.cert}
        for _attempt in range(2):
            try:
                r = httpx.post(f"http://{host}/api/pair/complete", json=body, timeout=10)
                if r.status_code != 200:
                    detail = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
                    QMessageBox.warning(None, "Pairing failed", str(detail))
                    return
                data = r.json()
                self.settings.set("pi_cert_pem", data["pi_cert_pem"])
                self.settings.set("pi_host", host)
                self.settings.set("mode", "box")
                self.settings.set("box_paired", True)
                self.settings.set("standalone_paired", False)
                self.server.restart_tls()
                QMessageBox.information(None, "Paired!", "This computer is now paired with your Click box. "
                                                         "Quit and reopen Click to switch over.")
                return
            except (httpx.ConnectError, httpx.ConnectTimeout, socket.gaierror):
                host, ok = QInputDialog.getText(
                    None, "Can't find the Click box",
                    f"Couldn't reach '{host}'. Type the Click box's IP address\n"
                    "(shown in the Click chat under Settings → Your computer):", QLineEdit.Normal, "")
                if not ok or not host.strip():
                    return
                host = host.strip()
        QMessageBox.warning(None, "Pairing failed", "Couldn't reach the Click box. Check it is on the same network.")

    def unpair(self) -> None:
        self.settings.set("pi_cert_pem", None)
        self.server.restart_tls()

    def open_settings(self) -> None:
        from .gui import SettingsDialog

        dlg = SettingsDialog(self)
        dlg.exec()

    def settings_changed(self) -> None:
        from .hardware import pick_models

        self.models.update(pick_models(self.hardware, self.settings))
        if self.server.loop and self.server.ws:
            from click_protocol import make_message

            async def push():
                await self.server.send(make_message("event", "system", "capabilities", await self.capabilities()))
            asyncio.run_coroutine_threadsafe(push(), self.server.loop)

    def use_this_computer(self) -> None:
        """Switch back to running everything on this computer (no Pi)."""
        self.settings.set("mode", "standalone")
        self.settings.set("standalone_paired", False)
        self.notify("Quit and reopen Click to switch to this-computer mode.")

    def quit(self) -> None:
        self.bridge.ghostHide.emit()
        if self.hub:
            self.hub.stop()
        self.qt.quit()

    # ---- request handlers (called by the server; "blocking" ones run in a worker thread) ---------------------
    def screen_capture(self, p: dict) -> dict:
        if not p.get("watch"):
            self.bridge.indicator.emit("capture", True)
            self.tray.alert()
        return self.screen.capture(p.get("target"), p.get("region"))

    async def screen_context(self, p: dict) -> dict:
        """Fast "what's on screen": windows + text + small screenshot, optionally a close visual look."""
        self.bridge.indicator.emit("capture", True)
        self.tray.alert()
        res = await asyncio.to_thread(self.screen.context, p.get("target"), True)
        if p.get("describe"):
            q = p.get("question") or "the user's question"
            d = await self.inference.describe({"image_b64": res["image_b64"],
                                               "prompt": f"Describe this screen to help with: {q}. Give exact text of any errors. "
                                                         "Black boxes are hidden private apps; don't guess what's in them."})
            res["description"] = d.get("text", "")
        if not p.get("image"):
            res.pop("image_b64", None)
        return res

    def screen_ocr(self, p: dict) -> dict:
        self.bridge.indicator.emit("capture", True)
        return self.screen.ocr(p.get("region"))

    def screen_list_windows(self, p: dict) -> dict:
        return self.screen.list_windows()

    def screen_active_window(self, p: dict) -> dict:
        return self.screen.get_active_window()

    def screen_ui_elements(self, p: dict) -> dict:
        return self.screen.get_ui_elements(p.get("window"))

    def screen_find_element(self, p: dict) -> dict:
        return self.screen.find_ui_element(p.get("query", ""), p.get("window"), deep=p.get("deep", True))

    def files_list(self, p: dict) -> dict:
        return self.files.list_folder(p.get("path", ""))

    def files_read(self, p: dict) -> dict:
        return self.files.read_file(p.get("path", ""))

    def files_search(self, p: dict) -> dict:
        return self.files.search_files(p.get("query", ""), p.get("by", "name"), p.get("folder"))

    def files_rename(self, p: dict) -> dict:
        return self.files.rename_file(p.get("path", ""), p.get("new_name", ""))

    def files_move(self, p: dict) -> dict:
        return self.files.move_file(p.get("path", ""), p.get("destination_folder", ""))

    async def cursor_move(self, p):
        return await self.actions.move(p)

    async def cursor_highlight(self, p):
        return await self.actions.highlight(p)

    async def cursor_inspect(self, p):
        return await self.actions.inspect(p)

    async def cursor_summon(self, p):
        self.bridge.ghostSummon.emit(str(p.get("label") or "Hi! I'm Clicky."), bool(p.get("center", True)))
        await asyncio.sleep(0.8)
        return {"status": "visible"}

    async def cursor_hide(self, p):
        return await self.actions.hide(p)

    async def cursor_teach(self, p):
        return await self.actions.teach_step(p)

    async def action_click(self, p):
        return await self.actions.click(p)

    async def action_type(self, p):
        return await self.actions.type_text(p)

    async def action_keys(self, p):
        return await self.actions.press_keys(p)

    async def action_scroll(self, p):
        return await self.actions.scroll(p)

    async def skills_guide(self, p):
        try:
            return await self.skills.guide(p)
        finally:
            self.bridge.ghostRest.emit()

    async def skills_run(self, p):
        try:
            return await self.skills.run(p)
        finally:
            self.bridge.ghostRest.emit()

    async def inference_describe(self, p):
        return await self.inference.describe(p)

    def run(self) -> int:
        return self.qt.exec()


_INSTANCE = "app.click.assistant.instance"


def _bundle_path():
    """This Click.app's location (macOS packaged app only), else None."""
    from pathlib import Path

    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    for parent in exe.parents:
        if parent.suffix == ".app":
            return parent
    return None


def _close_other_copies() -> None:
    """If another Click is running from a DIFFERENT place (an old copy), close it so this one takes over.
    Otherwise opening the new version would just bring back the old one."""
    import os
    import signal
    import subprocess
    import time as _t

    me = _bundle_path()
    if not me:
        return
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return
    victims = []
    for line in out.splitlines():
        pid_s, _, cmd = line.strip().partition(" ")
        if "Click.app/Contents/MacOS/Click" in cmd and pid_s.isdigit() and int(pid_s) != os.getpid():
            if not cmd.startswith(str(me) + "/"):
                victims.append(int(pid_s))
    for pid in victims:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    if victims:
        logging.getLogger("click").info("closed older Click copies: %s", victims)
        _t.sleep(1.5)
        for pid in victims:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def _sign_copy(app) -> bool:
    """Stamp an installed copy with this Mac's own signature (see selfsign.py). Never blocks Click."""
    try:
        from .selfsign import Signer

        Signer(config.config_dir()).sign(app)
        _reset_old_permissions()
        return True
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("click").warning("couldn't sign Click on this Mac: %s", exc)
        return False


def _reset_old_permissions() -> None:
    """Once: forget permissions granted to builds with the old shared signature, so that signature
    (which is now public) can never be used to get them. Click asks again with its new signature."""
    marker = config.config_dir() / "permissions-reset-v2"
    if marker.exists():
        return
    import subprocess

    for service in ("ScreenCapture", "Accessibility"):
        subprocess.run(["tccutil", "reset", service, "app.click.assistant"], capture_output=True, timeout=15)
    marker.write_text("done")


def _finish_install() -> None:
    """Running from Applications but not yet stamped with this Mac's signature (a fresh download):
    make a stamped copy, swap it in after Click quits, and reopen. Takes a few seconds, once per update."""
    import os
    import subprocess

    me = _bundle_path()
    if not me or not str(me).startswith("/Applications/"):
        return
    from .selfsign import Signer

    if Signer.signed_by_us(me):
        return
    from . import __version__

    tried = config.config_dir() / "install-attempt"
    if tried.exists() and tried.read_text().strip() == __version__:
        return  # already tried for this version (e.g. the password box was cancelled): never nag or loop
    tried.write_text(__version__)
    staging = me.with_name(".Click-installing.app")
    try:
        subprocess.run(["rm", "-rf", str(staging)], timeout=60)
        subprocess.run(["ditto", str(me), str(staging)], check=True, timeout=300)
        if not _sign_copy(staging):
            subprocess.run(["rm", "-rf", str(staging)], timeout=60)
            return
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("click").warning("finishing install failed: %s", exc)
        return
    swap = (f'while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.3; done; '
            f'rm -rf "{me}" && mv "{staging}" "{me}" && open "{me}"')
    subprocess.Popen(["/bin/sh", "-c", swap], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sys.exit(0)


def _move_to_applications() -> None:
    """Opened from the disk image, Downloads or anywhere outside Applications: offer to move Click into
    Applications (replacing any old copy) and reopen from there. macOS's "Quit & Reopen" after granting
    permissions reopens the copy in Applications, so an old copy there would come back otherwise."""
    import shutil
    import subprocess

    me = _bundle_path()
    if not me or str(me).startswith("/Applications/"):
        return
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox()
    box.setWindowTitle("Move Click to Applications?")
    box.setText("Click works best from your Applications folder.\n\nMove it there now? "
                "Any older Click in Applications will be replaced.")
    move = box.addButton("Move to Applications", QMessageBox.AcceptRole)
    box.addButton("Not now", QMessageBox.RejectRole)
    box.exec()
    if box.clickedButton() is not move:
        return
    dest = "/Applications/Click.app"
    try:
        subprocess.run(["pkill", "-f", dest + "/Contents/MacOS/Click"], timeout=5)
        if shutil.os.path.exists(dest):
            shutil.rmtree(dest)
        subprocess.run(["ditto", str(me), dest], check=True, timeout=300)
        _sign_copy(Path(dest))
        subprocess.run(["open", "-n", dest], timeout=30)
    except Exception as exc:  # noqa: BLE001
        QMessageBox.warning(None, "Couldn't move Click",
                            f"Please drag Click into Applications yourself.\n\n({exc})")
        return
    if str(me).startswith("/Volumes/"):  # opened from the disk image: eject it
        subprocess.Popen(["/bin/sh", "-c", f'sleep 3; hdiutil detach "{me.parent}" -quiet'],
                         start_new_session=True)
    sys.exit(0)


def _already_running() -> bool:
    """If Click is already running, ask it to show its chat and return True."""
    from PySide6.QtNetwork import QLocalSocket

    sock = QLocalSocket()
    sock.connectToServer(_INSTANCE)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return True
    return False


def _listen_for_reopen(app) -> None:
    from PySide6.QtNetwork import QLocalServer

    QLocalServer.removeServer(_INSTANCE)  # clears a stale one left by a crash
    server = QLocalServer()
    server.listen(_INSTANCE)

    def on_conn():
        conn = server.nextPendingConnection()
        if conn:
            conn.close()
        if app.bubble:
            app.bubble.show()
        if app.tile and not app.tile.isVisible():
            app.toggle_tile()

    server.newConnection.connect(on_conn)
    app._instance_server = server


def _hide_dock_icon() -> None:
    if sys.platform == "darwin":
        try:
            from AppKit import NSApplication

            NSApplication.sharedApplication().setActivationPolicy_(1)  # accessory: menu bar only
        except Exception:  # noqa: BLE001
            pass


def _webengine_env() -> None:
    import os

    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    # The chat page is served from this computer; never send it through a system proxy or VPN.
    flags += " --no-proxy-server"
    if getattr(sys, "frozen", False):
        # Chromium's sandbox helper often fails inside a packaged (unnotarized) app and the page never
        # loads. The page is Click's own local chat, so it's safe to run without it.
        flags += " --no-sandbox"
        os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = flags.strip()


def _crash_log() -> None:
    """If Click ever closes unexpectedly, the reason is written to crash.log (Check Clicky shows it)."""
    import faulthandler

    try:
        path = config.config_dir() / "crash.log"
        if path.exists() and path.stat().st_size > 0:
            old = path.with_name("crash-previous.log")
            path.replace(old)
            logging.getLogger("click").warning("Click closed unexpectedly last time; details in %s", old)
        global _crash_file
        _crash_file = open(path, "w")  # noqa: SIM115 - kept open for the life of the app
        faulthandler.enable(_crash_file, all_threads=True)
        import signal

        if hasattr(signal, "SIGTRAP"):  # macOS "dispatch assert" crashes arrive as SIGTRAP
            faulthandler.register(signal.SIGTRAP, _crash_file, all_threads=True, chain=True)
    except Exception:  # noqa: BLE001
        pass


_crash_file = None


def main() -> int:
    if "--selftest-sign" in sys.argv:  # build check: can this Mac stamp Click with its own signature?
        from .selfsign import selftest

        return selftest(_bundle_path())
    _webengine_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(config.config_dir() / "companion.log", encoding="utf-8")])
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:  # noqa: BLE001
        pass
    _crash_log()
    app = App()
    code = app.run()
    try:  # a normal quit: no crash to report
        if _crash_file:
            _crash_file.close()
            (config.config_dir() / "crash.log").unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return code
