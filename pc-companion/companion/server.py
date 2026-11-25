"""The PC side of the Pi <-> PC link: a mutual-TLS WebSocket server plus mDNS announcement.

Runs its own asyncio loop in a background thread. The Pi is always the decision maker, but the
Companion re-checks its own local permissions before every screen, file or cursor action.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import threading
from pathlib import Path

import websockets
from click_protocol import (
    MAX_MESSAGE_BYTES, PROTOCOL_VERSION, SERVICE_TYPE, ProtocolError, error_reply, make_message, reply, validate,
)
from click_protocol.tls import generate_identity, server_context

log = logging.getLogger("click.server")


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent; this just picks the LAN interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Identity:
    def __init__(self, tls_dir: Path, name: str):
        self.cert_path, self.key_path = tls_dir / "pc.crt", tls_dir / "pc.key"
        if not self.cert_path.exists() or not self.key_path.exists():
            cert, key = generate_identity(f"click-pc-{name}"[:60])
            self.key_path.write_text(key)
            try:
                self.key_path.chmod(0o600)
            except OSError:
                pass
            self.cert_path.write_text(cert)
        self.cert = self.cert_path.read_text()
        self.key = self.key_path.read_text()


# (service, action) -> (local permission needed, handler name on the App controller, runs blocking code?)
ROUTES = {
    ("screen", "capture"): ("screen", "screen_capture", True),
    ("screen", "context"): ("screen", "screen_context", False),
    ("screen", "ocr"): ("screen", "screen_ocr", True),
    ("screen", "list_windows"): ("screen", "screen_list_windows", True),
    ("screen", "get_active_window"): ("screen", "screen_active_window", True),
    ("screen", "get_ui_elements"): ("screen", "screen_ui_elements", True),
    ("screen", "find_ui_element"): ("screen", "screen_find_element", True),
    ("files", "list_folder"): ("files", "files_list", True),
    ("files", "read_file"): ("files", "files_read", True),
    ("files", "search_files"): ("files", "files_search", True),
    ("files", "rename_file"): ("file_write", "files_rename", True),
    ("files", "move_file"): ("file_write", "files_move", True),
    ("cursor", "move"): ("cursor", "cursor_move", False),
    ("cursor", "highlight"): ("cursor", "cursor_highlight", False),
    ("cursor", "inspect"): ("cursor", "cursor_inspect", False),
    ("cursor", "hide"): (None, "cursor_hide", False),
    ("cursor", "summon"): ("cursor", "cursor_summon", False),
    ("cursor", "teach_step"): ("cursor", "cursor_teach", False),
    ("cursor", "click"): ("actions", "action_click", False),
    ("cursor", "type_text"): ("actions", "action_type", False),
    ("cursor", "press_keys"): ("actions", "action_keys", False),
    ("cursor", "scroll"): ("actions", "action_scroll", False),
    ("inference", "describe"): (None, "inference_describe", False),
    ("skills", "run"): ("actions", "skills_run", False),
}


class CompanionServer:
    def __init__(self, app):
        self.app = app
        self.settings = app.settings
        self.identity = Identity(self.settings.tls_dir, self.settings["pc_name"])
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.ws = None
        self._send_lock: asyncio.Lock | None = None
        self._server = None
        self._zc = None
        self._restart = None

    @property
    def pi_connected(self) -> bool:
        return self.ws is not None

    # ---- lifecycle ---------------------------------------------------------------------------------
    def start(self) -> None:
        self.thread = threading.Thread(target=self._thread_main, name="click-server", daemon=True)
        self.thread.start()

    def _thread_main(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._send_lock = asyncio.Lock()
        self._restart = asyncio.Event()
        self.loop.run_until_complete(self._main())

    async def _main(self) -> None:
        await self._announce()
        while True:
            ctx = server_context(self.identity.cert, self.identity.key, self.settings["pi_cert_pem"])
            try:
                async with websockets.serve(self._handler, "0.0.0.0", self.settings["port"], ssl=ctx,
                                            max_size=MAX_MESSAGE_BYTES, ping_interval=20, ping_timeout=20) as server:
                    self._server = server
                    log.info("Companion listening on port %s (paired: %s)", self.settings["port"],
                             bool(self.settings["pi_cert_pem"]))
                    await self._restart.wait()
                    self._restart.clear()
            except OSError as exc:
                log.error("Could not listen on port %s: %s", self.settings["port"], exc)
                self.app.notify(f"Click Companion can't use port {self.settings['port']}: {exc}")
                await asyncio.sleep(10)

    def restart_tls(self) -> None:
        """Call after pairing/unpairing so the new Pi certificate is used."""
        if self.loop:
            self.loop.call_soon_threadsafe(self._restart.set)
            if self.ws:
                asyncio.run_coroutine_threadsafe(self.ws.close(), self.loop)

    async def _announce(self) -> None:
        try:
            from zeroconf import ServiceInfo
            from zeroconf.asyncio import AsyncZeroconf

            ip = local_ip()
            name = "".join(c for c in self.settings["pc_name"] if c.isalnum() or c in "-_")[:40] or "pc"
            info = ServiceInfo(SERVICE_TYPE, f"{name}-{self.settings['pc_id'][:6]}.{SERVICE_TYPE}",
                               addresses=[socket.inet_aton(ip)], port=self.settings["port"],
                               properties={"id": self.settings["pc_id"], "v": str(PROTOCOL_VERSION)},
                               server=f"{name}.local.")
            self._zc = AsyncZeroconf()
            await self._zc.async_register_service(info)
            log.info("Announced %s at %s:%s", info.name, ip, self.settings["port"])
        except Exception as exc:  # noqa: BLE001 - discovery is optional (the Pi remembers our IP)
            log.warning("mDNS announcement failed: %s", exc)

    # ---- connection ------------------------------------------------------------------------------------
    async def _handler(self, ws) -> None:
        if self.ws is not None:  # only one Pi at a time; the newest wins
            try:
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass
        self.ws = ws
        self.app.on_pi_connection(True)
        tasks: set[asyncio.Task] = set()
        try:
            async for raw in ws:
                try:
                    msg = validate(json.loads(raw))
                except (ValueError, ProtocolError) as exc:
                    log.warning("bad message: %s", exc)
                    continue
                if msg["type"] == "event":
                    self.app.on_pi_event(msg["service"], msg["action"], msg["payload"])
                    continue
                if msg["type"] != "request":
                    continue
                t = asyncio.create_task(self._dispatch(msg))
                tasks.add(t)
                t.add_done_callback(tasks.discard)
        except websockets.ConnectionClosed:
            pass
        finally:
            for t in tasks:
                t.cancel()
            if self.ws is ws:
                self.ws = None
                self.app.on_pi_connection(False)

    async def send(self, msg: dict) -> None:
        ws = self.ws
        if ws is None:
            return
        async with self._send_lock:
            try:
                await ws.send(json.dumps(msg))
            except websockets.ConnectionClosed:
                pass

    def send_event_threadsafe(self, service: str, action: str, payload: dict) -> None:
        if self.loop and self.ws:
            asyncio.run_coroutine_threadsafe(self.send(make_message("event", service, action, payload)), self.loop)

    # ---- dispatch -------------------------------------------------------------------------------------------
    async def _dispatch(self, msg: dict) -> None:
        app = self.app
        key = (msg["service"], msg["action"])
        try:
            if key == ("system", "hello"):
                await self.send(reply(msg, await app.capabilities()))
                return
            if key == ("system", "heartbeat"):
                await self.send(reply(msg, {"ok": True}))
                return
            if key == ("inference", "chat"):
                async def emit(chunk):
                    await self.send(reply(msg, chunk, type="stream_chunk"))
                app.busy = getattr(app, "busy", 0) + 1  # the bubble swirls while Click is thinking
                try:
                    await self.send(reply(msg, await app.inference.chat(msg["payload"], emit)))
                finally:
                    app.busy -= 1
                return
            if key == ("image", "generate"):
                await self._image(msg)
                return
            if key == ("image", "cancel"):
                app.imagegen.cancel(msg["payload"].get("job_id", ""))
                await self.send(reply(msg, {"ok": True}))
                return
            route = ROUTES.get(key)
            if not route:
                await self.send(error_reply(msg, f"Unknown request {key[0]}.{key[1]}", "unknown"))
                return
            perm, handler, blocking = route
            if app.paused and key != ("cursor", "hide"):
                raise PermissionError("Click is paused on this computer.")
            if perm and not self.settings["permissions"].get(perm, True):
                raise PermissionError(f"This computer has '{perm}' turned off in the Click Companion settings.")
            fn = getattr(app, handler)
            payload = msg["payload"]
            result = await asyncio.to_thread(fn, payload) if blocking else await fn(payload)
            await self.send(reply(msg, result))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - every failure becomes a readable error for the chat
            code = "permission" if isinstance(exc, PermissionError) else "error"
            log.info("%s.%s failed: %s", *key, exc)
            await self.send(error_reply(msg, str(exc) or exc.__class__.__name__, code))

    async def _image(self, msg: dict) -> None:
        app = self.app
        if app.paused:
            raise PermissionError("Click is paused on this computer.")
        loop = asyncio.get_running_loop()

        def progress(p: dict):
            asyncio.run_coroutine_threadsafe(self.send(reply(msg, p, type="stream_chunk")), loop)

        result = await asyncio.to_thread(app.imagegen.generate, msg["payload"], progress)
        await self.send(reply(msg, result))
