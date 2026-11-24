"""The Pi's side of the encrypted Pi <-> PC link.

- Finds the paired PC on the network with mDNS (falls back to the last known / manual address).
- Connects with mutual TLS: the Pi only trusts the PC certificate it got at pairing, and the PC
  only accepts the Pi certificate it got at pairing.
- One WebSocket carries requests, streamed chunks and events. Heartbeat every 5 seconds.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from pathlib import Path
from typing import AsyncIterator, Callable

import websockets
from click_protocol import (
    DEFAULT_COMPANION_PORT, HEARTBEAT_SECONDS, HEARTBEAT_TIMEOUT_SECONDS, MAX_MESSAGE_BYTES,
    SERVICE_TYPE, ProtocolError, make_message, validate,
)
from click_protocol.tls import client_context, fingerprint, generate_identity

from .audit import AuditLog
from .db import Database

log = logging.getLogger("click.pclink")


def local_addresses() -> list[str]:
    """The Pi's LAN IP address(es), shown while pairing in case click.local can't be resolved."""
    import socket

    out = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent
        out.append(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()
    return [a for a in out if not a.startswith("127.")]


class PcError(RuntimeError):
    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


class Identity:
    """The Pi's own TLS certificate, created once and kept in the data folder."""

    def __init__(self, tls_dir: Path):
        self.cert_path = tls_dir / "pi.crt"
        self.key_path = tls_dir / "pi.key"
        if not self.cert_path.exists() or not self.key_path.exists():
            cert, key = generate_identity("click-pi")
            self.key_path.write_text(key)
            self.key_path.chmod(0o600)
            self.cert_path.write_text(cert)
        self.cert = self.cert_path.read_text()
        self.key = self.key_path.read_text()


class Pairing:
    """6-digit pairing codes shown in the chat UI and typed into the PC tray app."""

    CODE_TTL = 600
    MAX_ATTEMPTS = 5

    def __init__(self, db: Database, identity: Identity, audit: AuditLog):
        self.db, self.identity, self.audit = db, identity, audit
        self.code: str | None = None
        self.expires = 0.0
        self.attempts = 0
        self.on_paired: Callable[[], None] | None = None

    def start(self) -> dict:
        self.code = f"{secrets.randbelow(1_000_000):06d}"
        self.expires = time.time() + self.CODE_TTL
        self.attempts = 0
        return {"code": self.code, "expires_in": self.CODE_TTL, "addresses": local_addresses()}

    def complete(self, code: str, pc_id: str, name: str, host: str, port: int, cert_pem: str) -> dict:
        if not self.code or time.time() > self.expires:
            raise PermissionError("No pairing in progress. Click 'Pair a computer' in Settings first.")
        self.attempts += 1
        if self.attempts > self.MAX_ATTEMPTS:
            self.code = None
            raise PermissionError("Too many wrong codes. Start pairing again.")
        if not secrets.compare_digest(str(code).strip(), self.code):
            raise PermissionError("Wrong code.")
        fingerprint(cert_pem)  # validates the certificate parses
        self.code = None
        self.db.set_setting("pc_pairing", {
            "pc_id": pc_id, "name": name[:80], "host": host, "port": int(port or DEFAULT_COMPANION_PORT),
            "cert_pem": cert_pem, "paired_at": time.time(),
        })
        self.audit.log("pc", "paired", f"Paired with {name} ({host}), fingerprint {fingerprint(cert_pem)[:16]}")
        if self.on_paired:
            self.on_paired()
        return {"pi_cert_pem": self.identity.cert, "pi_name": "Click"}

    def unpair(self) -> None:
        self.db.set_setting("pc_pairing", None)
        self.audit.log("pc", "unpaired", "")


class PcLink:
    def __init__(self, cfg, db: Database, audit: AuditLog, identity: Identity):
        self.cfg, self.db, self.audit, self.identity = cfg, db, audit, identity
        self.ws = None
        self.connected = False
        self.capabilities: dict = {}
        self.last_seen = 0.0
        self.last_error = ""
        self._queues: dict[str, asyncio.Queue] = {}
        self._discovered: dict[str, tuple[str, int]] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._zc = None
        self.status_listeners: list[Callable[[], None]] = []
        self.event_handlers: dict[str, Callable[[dict], None]] = {}

    # -- lifecycle ---------------------------------------------------------------
    def start(self) -> None:
        self._task = asyncio.create_task(self._run())
        asyncio.create_task(self._start_discovery())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self.ws:
            await self.ws.close()
        if self._zc:
            await self._zc.async_close()

    def reconnect_now(self) -> None:
        self._wake.set()

    @property
    def pairing(self) -> dict | None:
        return self.db.get_setting("pc_pairing")

    def status(self) -> dict:
        p = self.pairing
        return {
            "paired": bool(p),
            "pc_name": p["name"] if p else None,
            "connected": self.connected,
            "capabilities": self.capabilities if self.connected else {},
            "last_error": "" if self.connected else self.last_error,
        }

    def _notify(self) -> None:
        for fn in list(self.status_listeners):
            try:
                fn()
            except Exception:
                log.exception("status listener failed")

    # -- discovery -------------------------------------------------------------
    async def _start_discovery(self) -> None:
        try:
            from zeroconf import ServiceStateChange
            from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf
        except ImportError:
            return
        self._zc = AsyncZeroconf()

        def on_change(zeroconf, service_type, name, state_change):
            if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
                asyncio.ensure_future(resolve(name))

        async def resolve(name):
            info = AsyncServiceInfo(SERVICE_TYPE, name)
            if await info.async_request(self._zc.zeroconf, 3000):
                props = {k.decode(): (v or b"").decode() for k, v in (info.properties or {}).items()}
                addrs = info.parsed_addresses()
                if props.get("id") and addrs:
                    self._discovered[props["id"]] = (addrs[0], info.port)
                    p = self.pairing
                    if p and p["pc_id"] == props["id"] and not self.connected:
                        self._wake.set()

        AsyncServiceBrowser(self._zc.zeroconf, SERVICE_TYPE, handlers=[on_change])

    def _target(self) -> tuple[str, int] | None:
        p = self.pairing
        if not p:
            return None
        if self.cfg.manual_pc_host:
            return self.cfg.manual_pc_host, p.get("port", DEFAULT_COMPANION_PORT)
        return self._discovered.get(p["pc_id"]) or (p["host"], p["port"])

    # -- connection loop ---------------------------------------------------------
    async def _run(self) -> None:
        backoff = 2
        while True:
            target = self._target()
            if target:
                try:
                    await self._connect_and_serve(*target)
                    backoff = 2
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - any failure means "offline"
                    self.last_error = str(exc)[:200]
                    log.info("PC link down: %s", exc)
                finally:
                    if self.connected:
                        self.audit.log("pc", "disconnected", self.last_error)
                    self._set_offline()
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 10)

    async def _connect_and_serve(self, host: str, port: int) -> None:
        p = self.pairing
        ctx = client_context(self.identity.cert, self.identity.key, p["cert_pem"])
        async with websockets.connect(
            f"wss://{host}:{port}/click", ssl=ctx, max_size=MAX_MESSAGE_BYTES,
            open_timeout=5, ping_interval=None,
        ) as ws:
            self.ws = ws
            hello = await self._request_raw("system", "hello", {"pi": "click"}, timeout=10, ws=ws, read_inline=True)
            self.capabilities = hello
            self.connected = True
            self.last_seen = time.time()
            self.last_error = ""
            if p.get("host") != host:
                p["host"] = host
                self.db.set_setting("pc_pairing", p)
            self.audit.log("pc", "connected", f"{p['name']} at {host}")
            self._notify()
            hb = asyncio.create_task(self._heartbeat())
            try:
                async for raw in ws:
                    self._dispatch(raw)
            finally:
                hb.cancel()

    def _set_offline(self) -> None:
        was = self.connected
        self.connected = False
        self.ws = None
        for q in self._queues.values():
            q.put_nowait({"type": "error", "payload": {"code": "offline", "message": "PC went offline"}})
        if was:
            self._notify()

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                await self.request("system", "heartbeat", {}, timeout=HEARTBEAT_TIMEOUT_SECONDS)
                self.last_seen = time.time()
            except Exception:
                if self.ws:
                    await self.ws.close()
                return

    def _dispatch(self, raw: str) -> None:
        try:
            msg = validate(json.loads(raw))
        except (ValueError, ProtocolError):
            log.warning("bad message from PC")
            return
        if msg["type"] == "event":
            handler = self.event_handlers.get(f"{msg['service']}.{msg['action']}")
            if handler:
                handler(msg["payload"])
            return
        q = self._queues.get(msg["id"])
        if q:
            q.put_nowait(msg)

    # -- requests ----------------------------------------------------------------
    async def _request_raw(self, service, action, payload, *, timeout, ws, read_inline=False):
        msg = make_message("request", service, action, payload)
        await ws.send(json.dumps(msg))
        if read_inline:  # used for the hello before the reader loop starts
            while True:
                reply = validate(json.loads(await asyncio.wait_for(ws.recv(), timeout)))
                if reply["id"] == msg["id"]:
                    if reply["type"] == "error":
                        raise PcError(reply["payload"].get("message", "error"))
                    return reply["payload"]
        return msg

    async def request(self, service: str, action: str, payload: dict, *, timeout: float = 60,
                      requires_confirmation: bool = False) -> dict:
        final = {}
        async for chunk in self._stream(service, action, payload, timeout, requires_confirmation, final_only=True):
            final = chunk
        return final

    async def stream(self, service: str, action: str, payload: dict, *, timeout: float = 120) -> AsyncIterator[dict]:
        async for chunk in self._stream(service, action, payload, timeout, False, final_only=False):
            yield chunk

    async def _stream(self, service, action, payload, timeout, requires_confirmation, final_only):
        if not self.connected or not self.ws:
            raise PcError("Your computer is offline.", "offline")
        msg = make_message("request", service, action, payload, requires_confirmation=requires_confirmation)
        q: asyncio.Queue = asyncio.Queue()
        self._queues[msg["id"]] = q
        if service not in ("system",):
            self.audit.log("pc", f"{service}.{action}", _summary(payload))
        try:
            await self.ws.send(json.dumps(msg))
            while True:
                reply = await asyncio.wait_for(q.get(), timeout)
                if reply["type"] == "error":
                    p = reply["payload"]
                    raise PcError(p.get("message", "PC error"), p.get("code", "error"))
                if reply["type"] == "stream_chunk":
                    if not final_only:
                        yield reply["payload"]
                    continue
                yield reply["payload"]
                return
        except asyncio.TimeoutError as exc:
            raise PcError(f"Your computer did not answer in {int(timeout)} s ({service}.{action}).", "timeout") from exc
        finally:
            self._queues.pop(msg["id"], None)

    async def send_event(self, service: str, action: str, payload: dict) -> None:
        if self.connected and self.ws:
            await self.ws.send(json.dumps(make_message("event", service, action, payload)))


def _summary(payload: dict) -> str:
    parts = []
    for k, v in payload.items():
        if k in ("messages", "image", "images", "init_image", "tools"):
            parts.append(f"{k}=<{len(v) if hasattr(v, '__len__') else '?'}>")
        else:
            parts.append(f"{k}={str(v)[:80]}")
    return ", ".join(parts)[:400]
