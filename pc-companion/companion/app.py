"""Click Companion: the background app on your computer (Windows or Mac).

Start it with:  python -m companion
"""
from __future__ import annotations

import asyncio
import logging
import socket
import sys

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
        self.bridge.toggleTile.connect(lambda: self.tile and self.tile.toggle())
        self.tray = Tray(self)
        self.selftest = "--selftest" in sys.argv
        self._start_hotkeys()
        if not self.selftest:
            self.platform.request_permissions()
        self.server.start()
        self.tile = None
        self.hub = None
        self._setup_thread = None
        QTimer.singleShot(0, self._start_click)

    # ---- startup: the box part (on this computer or a Pi), then the tile ------------------------------------
    @property
    def mode(self) -> str:
        if self.settings.get("mode"):
            return self.settings["mode"]
        # Earlier versions were always paired with a Pi; new installs run everything on this computer.
        return "box" if self.settings["pi_cert_pem"] and not self.settings.get("standalone_paired") else "standalone"

    def _start_click(self) -> None:
        from .config import config_dir
        from .tile import Tile

        if self.mode == "standalone":
            from .standalone import EmbeddedHub

            self.hub = EmbeddedHub(self.settings, self.models["chat"])
            self.hub.start()
            cert = self.hub.ensure_paired(self.server.identity.cert, self.settings["pc_id"], self.settings["pc_name"],
                                          self.settings["port"])
            if cert:
                self.settings.set("pi_cert_pem", cert)
                self.server.restart_tls()
            url = self.hub.url
        else:
            url = f"http://{self.settings['pi_host']}"
        self.tile = Tile(url, str(config_dir() / "web"), self.settings["cursor"]["color"])
        self.tile.moved.connect(self._tile_moved)
        if self.selftest:
            QTimer.singleShot(500, self._selftest_check)
            return
        self.tile.show_tile()
        if self.settings.get("setup_done") and self._models_present():
            self.tile.show_chat()
            self.tile.header.set_status("Ready", "#16a34a")
        else:
            self._run_setup()

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
            view.cont.setText("Start using Click")
            view.cont.show()
            self._on_continue(self._finish_setup)
            if not rows or all(r[2] for r in rows):
                self._finish_setup()

        view.cont.hide()
        models = list(dict.fromkeys([self.models["chat"], self.models["vision"]]))
        self._setup_thread = run_setup(view, self.settings["ollama_url"], models, done)

    def _on_continue(self, fn) -> None:
        view = self.tile.setup
        if getattr(self, "_continue_fn", None):
            view.continue_clicked.disconnect(self._continue_fn)
        self._continue_fn = fn
        view.continue_clicked.connect(fn)

    def _finish_setup(self) -> None:
        self.tile.show_chat()
        self.tile.header.set_status("Ready", "#16a34a")
        QTimer.singleShot(1200, self.ghost.demo)  # show the cursor once so you know what to look for

    def _tile_moved(self) -> None:
        """The cursor appears from the tile, so its movement is easy to follow."""
        if not self.tile:
            return
        pos = self.tile.bottom_left_global()
        for o in self.ghost.overlays:
            g = o.smap.screen.geometry()
            if g.contains(pos):
                lx, ly = pos.x() - g.x(), pos.y() - g.y()
                self.ghost.set_home(o.smap.left + lx * o.smap.scale, o.smap.top + ly * o.smap.scale)
                return

    def _selftest_check(self, tries: int = 0) -> None:
        """Used by the automatic Mac build to prove the app starts: hub up + link connected."""
        import httpx

        try:
            st = httpx.get(f"{self.tile.url}/api/status", timeout=2).json()
            if st["pc"]["connected"]:
                print("SELFTEST OK", st["pc"]["pc_name"], flush=True)
                self.qt.exit(0)
                return
        except Exception as exc:  # noqa: BLE001
            log.info("selftest waiting: %s", exc)
        if tries > 60:
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
        elif (service, action) == ("cursor", "narrate"):
            self.bridge.narrate.emit(str(payload.get("text", ""))[:160])
        elif (service, action) == ("system", "pause"):
            self._set_paused(bool(payload.get("paused")), from_pi=True)

    async def capabilities(self) -> dict:
        return {
            "protocol": 1, "pc_name": self.settings["pc_name"], **self.hardware,
            "models": {"chat": self.models["chat"], "vision": self.models["vision"]},
            "vision_in_chat": self.inference.vision_in_chat,
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

    def kill(self) -> None:
        """Ctrl+Alt+Esc: stop all watching and actions instantly and release control."""
        self._set_paused(True, from_pi=True)
        self.server.send_event_threadsafe("system", "kill", {"reason": "hotkey"})
        self.notify("Stopped. Everything is paused. Resume from the menu bar icon or the chat.")

    def _start_hotkeys(self) -> None:
        """Ctrl+Alt+Esc stops everything; Cmd+Shift+Space (Ctrl+Shift+Space on Windows) shows/hides the tile."""
        try:
            from pynput import keyboard

            toggle = "<cmd>+<shift>+<space>" if sys.platform == "darwin" else "<ctrl>+<shift>+<space>"
            self._hotkey = keyboard.GlobalHotKeys({self.settings["kill_hotkey"]: self.kill,
                                                   toggle: self.bridge.toggleTile.emit})
            self._hotkey.daemon = True
            self._hotkey.start()
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
        return self.screen.find_ui_element(p.get("query", ""), p.get("window"))

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

    async def inference_describe(self, p):
        return await self.inference.describe(p)

    def run(self) -> int:
        return self.qt.exec()


def _hide_dock_icon() -> None:
    if sys.platform == "darwin":
        try:
            from AppKit import NSApplication

            NSApplication.sharedApplication().setActivationPolicy_(1)  # accessory: menu bar only
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(config.config_dir() / "companion.log", encoding="utf-8")])
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:  # noqa: BLE001
        pass
    app = App()
    return app.run()
