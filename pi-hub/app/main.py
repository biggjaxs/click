"""Click Pi Hub: FastAPI app serving the chat UI, REST API and WebSocket."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .audit import AuditLog
from .config import APP_DIR, load_config
from .db import Database
from .extract import extract_text, is_image
from .images import ImageService
from .llm import LocalOllama, PcBackend
from .memory import CATEGORIES, Memory
from .orchestrator import Orchestrator, TurnState
from .pc_link import Identity, Pairing, PcError, PcLink
from .permissions import ConfirmationBroker, Permissions

logging.basicConfig(level=os.environ.get("CLICK_LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("click")

DEFAULT_SETTINGS = {
    "assistant_name": "Click",
    "theme": "system",            # system | light | dark
    "accent_color": "#6d5dfc",    # violet
    "chat_style": "bubbles",      # bubbles | minimal
    "font_size": "medium",        # small | medium | large
    "cursor_color": "#6d5dfc",
    "cursor_style": "arrow",      # arrow | dot | ring
    "cursor_label": "Clicky",
    "screenshot_history": False,
    "watch_interval_s": 30,
}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class Services:
    """Everything the app needs, created once at startup."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.db = Database(cfg.db_path, cfg.db_key, cfg.allow_unencrypted)
        self.audit = AuditLog(self.db)
        self.permissions = Permissions(self.db)
        self.broker = ConfirmationBroker()
        self.identity = Identity(cfg.tls_dir)
        self.pairing = Pairing(self.db, self.identity, self.audit)
        self.link = PcLink(cfg, self.db, self.audit, self.identity)
        self.pi_backend = LocalOllama(cfg.ollama_url, cfg.fallback_model, cfg.embed_model, cfg.request_timeout_s)
        self.pc_backend = PcBackend(self.link)
        self.memory = Memory(self.db, self.audit, self.pi_backend.embed)
        self.images = ImageService(cfg, self.db, self.audit, self.link, self.permissions)
        self.orchestrator = Orchestrator(self)
        self.sockets: set[WebSocket] = set()
        self.socket_locks: dict[WebSocket, asyncio.Lock] = {}
        self.broadcast = self._broadcast
        self.images.broadcast = self._broadcast

    def settings(self) -> dict:
        saved = self.db.get_setting("ui_settings", {})
        return {**DEFAULT_SETTINGS, **saved}

    async def send_to(self, ws: WebSocket, event: dict) -> None:
        lock = self.socket_locks.setdefault(ws, asyncio.Lock())
        async with lock:
            try:
                await ws.send_text(json.dumps(event))
            except Exception:  # noqa: BLE001 - socket closed
                pass

    async def _broadcast(self, event: dict) -> None:
        for ws in list(self.sockets):
            await self.send_to(ws, event)

    def status(self) -> dict:
        st = self.link.status()
        s = self.settings()
        return {
            "type": "status",
            "pc": st,
            "answering_model": (st["capabilities"].get("models", {}).get("chat") if st["connected"]
                                else self.cfg.fallback_model),
            "answering_source": "pc" if st["connected"] else "pi",
            "watch_mode": self.permissions.enabled("watch_mode"),
            "paused": self.permissions.paused,
            "memory_paused": not self.permissions.enabled("memory_saving"),
            "encrypted": self.db.encrypted,
            "settings": s,
        }

    async def narrate(self, text: str) -> None:
        """Show what Click is doing as a caption next to its cursor on the computer."""
        try:
            await self.link.send_event("cursor", "narrate", {"text": text})
        except Exception:  # noqa: BLE001 - narration is best-effort
            pass

    def save_screenshot(self, image_b64: str) -> None:
        if self.settings().get("screenshot_history"):
            name = f"{int(time.time())}-{uuid.uuid4().hex[:6]}.webp"
            (self.cfg.screens_dir / name).write_bytes(base64.b64decode(image_b64))

    async def push_settings_to_pc(self) -> None:
        s = self.settings()
        try:
            await self.link.send_event("system", "settings", {
                "cursor_color": s["cursor_color"], "cursor_style": s["cursor_style"],
                "cursor_label": s["cursor_label"] or s["assistant_name"],
                "watch_mode": self.permissions.enabled("watch_mode"), "paused": self.permissions.paused,
            })
        except Exception:  # noqa: BLE001
            pass


# ---- login (UI password = the passphrase chosen at install) ---------------------------------------
def _ui_hash() -> str | None:
    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if cred_dir and (Path(cred_dir) / "uihash").exists():
        return (Path(cred_dir) / "uihash").read_text().strip()
    return os.environ.get("CLICK_UI_HASH") or None


def check_password(password: str, stored: str) -> bool:
    # format: scrypt$<salt hex>$<hash hex>
    try:
        _, salt_hex, hash_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


services: Services | None = None
UI_HASH = _ui_hash()
OPEN_PATHS = {"/login", "/api/login", "/api/pair/complete", "/manifest.json", "/favicon.svg"}


def _valid_session(token: str | None) -> bool:
    if not UI_HASH:
        return True
    if not token or not services:
        return False
    sessions = services.db.get_setting("ui_sessions", {})
    expires = sessions.get(hashlib.sha256(token.encode()).hexdigest())
    return bool(expires and expires > time.time())


# ---- background loops ---------------------------------------------------------------------------------
async def watch_loop(s: Services) -> None:
    """Watch mode: low-frequency snapshots to keep context. The PC shows an indicator while it's on."""
    while True:
        interval = max(10, int(s.settings().get("watch_interval_s", 30)))
        await asyncio.sleep(interval)
        if not (s.permissions.enabled("watch_mode") and s.permissions.enabled("screen_reading") and s.link.connected):
            continue
        try:
            res = await s.link.request("screen", "capture", {"target": "active_window", "watch": True}, timeout=30)
            desc = await s.link.request("inference", "describe", {
                "image_b64": res["image_b64"], "mime": "image/webp",
                "prompt": "In 2-4 short sentences: which app is open and what is the user doing? "
                          "Include exact text of any visible error."}, timeout=120)
            s.orchestrator.screen_context = {"text": desc.get("text", ""), "at": time.time()}
            s.audit.log("screen", "watch_snapshot", desc.get("text", "")[:300])
            s.save_screenshot(res["image_b64"])
        except PcError as exc:
            log.info("watch snapshot failed: %s", exc)
        except Exception:  # noqa: BLE001
            log.exception("watch loop error")


async def consolidation_loop(s: Services) -> None:
    while True:
        await asyncio.sleep(3600)
        last = s.db.get_setting("last_consolidation", 0)
        if time.time() - last > 7 * 86400 and s.permissions.enabled("memory_saving"):
            try:
                backend = s.pc_backend if s.link.connected else s.pi_backend
                await s.memory.consolidate(backend)
            except Exception:  # noqa: BLE001
                log.exception("consolidation failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global services
    cfg = load_config()
    services = Services(cfg)
    s = services

    def on_status():
        asyncio.create_task(s._broadcast(s.status()))
        if s.link.connected:
            asyncio.create_task(s.push_settings_to_pc())
            asyncio.create_task(s.images.process_queue())

    def on_kill(payload):
        s.permissions.set_paused(True)
        s.broker.deny_all()
        s.audit.log("permission", "kill_switch", f"Stopped from the computer ({payload.get('reason', 'hotkey')})")
        asyncio.create_task(s._broadcast(s.status()))
        asyncio.create_task(s._broadcast({"type": "notice", "text": "Stopped: the kill switch was pressed on your computer. "
                                                                     "Everything is paused."}))

    def on_pause(payload):
        s.permissions.set_paused(bool(payload.get("paused")))
        s.audit.log("permission", "pause" if payload.get("paused") else "resume", "from the computer's tray icon")
        asyncio.create_task(s._broadcast(s.status()))

    s.link.status_listeners.append(on_status)
    def on_capabilities(payload):
        s.link.capabilities = payload
        asyncio.create_task(s._broadcast(s.status()))

    s.link.event_handlers["system.kill"] = on_kill
    s.link.event_handlers["system.pause"] = on_pause
    s.link.event_handlers["system.capabilities"] = on_capabilities
    s.pairing.on_paired = s.link.reconnect_now
    s.audit.listeners.append(lambda e: asyncio.get_event_loop().create_task(s._broadcast({"type": "activity", "entry": e})))
    s.link.start()
    loops = [asyncio.create_task(watch_loop(s)), asyncio.create_task(consolidation_loop(s))]
    log.info("Click Pi Hub started (encrypted memory: %s)", s.db.encrypted)
    yield
    for t in loops:
        t.cancel()
    await s.link.stop()
    s.db.close()


app = FastAPI(title="Click", lifespan=lifespan, docs_url=None, redoc_url=None)
STATIC = APP_DIR / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def auth_and_headers(request: Request, call_next):
    path = request.url.path
    # Static files are just the app's code (no private data), so they load without login.
    if (UI_HASH and path not in OPEN_PATHS and not path.startswith("/static/")
            and not _valid_session(request.cookies.get("click_session"))):
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Please log in."}, status_code=401)
        return Response(status_code=302, headers={"Location": "/login"})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
    return response


def S() -> Services:
    if services is None:
        raise HTTPException(503, "Starting up")
    return services


# ---- pages -----------------------------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC / "index.html").read_text()


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return (STATIC / "login.html").read_text()


@app.get("/favicon.svg")
async def favicon():
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")


@app.get("/manifest.json")
async def manifest():
    return JSONResponse({"name": "Click", "short_name": "Click", "start_url": "/", "display": "standalone",
                         "background_color": "#111114", "theme_color": "#7c3aed",
                         "icons": [{"src": "/favicon.svg", "sizes": "any", "type": "image/svg+xml"}]})


class LoginBody(BaseModel):
    password: str


_login_failures: dict[str, list[float]] = {}


@app.post("/api/login")
async def login(body: LoginBody, request: Request):
    if not UI_HASH:
        return {"ok": True}
    ip = request.client.host if request.client else "?"
    recent = [t for t in _login_failures.get(ip, []) if time.time() - t < 300]
    if len(recent) >= 8:
        raise HTTPException(429, "Too many attempts. Wait 5 minutes.")
    if not check_password(body.password, UI_HASH):
        recent.append(time.time())
        _login_failures[ip] = recent
        await asyncio.sleep(1)
        raise HTTPException(401, "Wrong password.")
    token = secrets.token_urlsafe(32)
    sessions = {k: v for k, v in S().db.get_setting("ui_sessions", {}).items() if v > time.time()}
    sessions[hashlib.sha256(token.encode()).hexdigest()] = time.time() + 30 * 86400
    S().db.set_setting("ui_sessions", sessions)
    S().audit.log("permission", "login", f"from {ip}")
    resp = JSONResponse({"ok": True})
    resp.set_cookie("click_session", token, max_age=30 * 86400, httponly=True, samesite="strict")
    return resp


@app.post("/api/logout")
async def logout(request: Request):
    token = request.cookies.get("click_session")
    if token:
        sessions = S().db.get_setting("ui_sessions", {})
        sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
        S().db.set_setting("ui_sessions", sessions)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("click_session")
    return resp


# ---- status / settings -----------------------------------------------------------------------------------
@app.get("/api/status")
async def status():
    return S().status()


@app.get("/api/settings")
async def get_settings():
    return S().settings()


@app.put("/api/settings")
async def put_settings(body: dict):
    s = S()
    current = s.db.get_setting("ui_settings", {})
    for k, v in body.items():
        if k in DEFAULT_SETTINGS:
            current[k] = v
    s.db.set_setting("ui_settings", current)
    if "assistant_name" in body:
        s.db.set_setting("assistant_name", str(body["assistant_name"])[:40] or "Click")
    await s.push_settings_to_pc()
    await s._broadcast(s.status())
    return s.settings()


@app.get("/api/system-prompt")
async def get_system_prompt():
    return {"text": S().cfg.system_prompt_path.read_text()}


@app.put("/api/system-prompt")
async def put_system_prompt(body: dict):
    text = str(body.get("text", ""))
    if not text.strip():
        raise HTTPException(400, "The system prompt can't be empty.")
    S().cfg.system_prompt_path.write_text(text)
    S().audit.log("permission", "system_prompt_edited", f"{len(text)} characters")
    return {"ok": True}


# ---- conversations ----------------------------------------------------------------------------------------
@app.get("/api/conversations")
async def list_conversations(q: str | None = None):
    db = S().db
    if q:
        like = f"%{q}%"
        return db.query(
            "SELECT DISTINCT c.* FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id "
            "WHERE c.title LIKE ? OR m.content LIKE ? ORDER BY c.updated_at DESC LIMIT 200", (like, like))
    return db.query("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT 300")


@app.get("/api/conversations/{cid}/messages")
async def conversation_messages(cid: int):
    rows = S().db.query("SELECT id, role, content, meta, created_at FROM messages WHERE conversation_id = ? "
                        "AND role IN ('user','assistant') ORDER BY id", (cid,))
    out = []
    for r in rows:
        meta = json.loads(r["meta"] or "{}")
        meta.pop("context", None)
        images = [S().images.get(i) for i in meta.get("images", [])]
        uploads = [S().db.one("SELECT id, filename, mime FROM uploads WHERE id = ?", (u,)) for u in meta.get("uploads", [])]
        out.append({**r, "meta": meta, "images": [i for i in images if i], "uploads": [u for u in uploads if u]})
    return out


@app.patch("/api/conversations/{cid}")
async def rename_conversation(cid: int, body: dict):
    title = str(body.get("title", "")).strip()[:100]
    if not title:
        raise HTTPException(400, "Title required")
    S().db.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, cid))
    return {"ok": True}


@app.delete("/api/conversations/{cid}")
async def delete_conversation(cid: int):
    s = S()
    for m in s.db.query("SELECT id FROM messages WHERE conversation_id = ?", (cid,)):
        s.db.execute("DELETE FROM embeddings WHERE kind = 'message' AND ref_id = ?", (m["id"],))
    s.db.execute("DELETE FROM conversations WHERE id = ?", (cid,))
    s.audit.log("memory", "conversation_deleted", f"#{cid}")
    return {"ok": True}


# ---- uploads ------------------------------------------------------------------------------------------------
@app.post("/api/uploads")
async def upload(file: UploadFile = File(...)):
    s = S()
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 25 MB.")
    filename = Path(file.filename or "file").name[:120]
    mime = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    stored = f"{uuid.uuid4().hex}{Path(filename).suffix.lower()[:10]}"
    (s.cfg.uploads_dir / stored).write_bytes(data)
    text = None if is_image(filename) else extract_text(filename, data)
    if text is None and not is_image(filename):
        text = "(This file type can't be read as text.)"
    uid = s.db.execute("INSERT INTO uploads(filename, stored_name, mime, text_content, created_at) VALUES (?, ?, ?, ?, ?)",
                       (filename, stored, mime, text, time.time()))
    s.audit.log("file", "upload", filename)
    return {"id": uid, "filename": filename, "mime": mime, "is_image": is_image(filename)}


@app.get("/api/uploads/{uid}/file")
async def upload_file(uid: int):
    s = S()
    row = s.db.one("SELECT * FROM uploads WHERE id = ?", (uid,))
    if not row:
        raise HTTPException(404)
    return FileResponse(s.cfg.uploads_dir / row["stored_name"], media_type=row["mime"], filename=row["filename"])


# ---- images -------------------------------------------------------------------------------------------------
@app.get("/api/images")
async def list_images(conversation_id: int | None = None):
    return S().images.list(conversation_id)


@app.get("/api/images/{image_id}/file")
async def image_file(image_id: int, download: bool = False):
    s = S()
    p = s.images.path(image_id)
    if not p or not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/png", filename=f"click-image-{image_id}.png" if download else None)


@app.delete("/api/images/{image_id}")
async def delete_image(image_id: int):
    if not S().images.delete(image_id):
        raise HTTPException(404)
    return {"ok": True}


@app.get("/api/image-jobs")
async def image_jobs():
    return S().images.queued()


# ---- memory ---------------------------------------------------------------------------------------------------
@app.get("/api/memory/facts")
async def facts(q: str | None = None):
    return S().memory.list_facts(q)


@app.post("/api/memory/facts")
async def add_fact(body: dict):
    res = await S().memory.add_fact(str(body.get("text", "")), body.get("category", "other"), 1.0, "manual",
                                    None, explicit=True)
    if res["status"] == "blocked":
        raise HTTPException(400, f"Not saved: {res['reason']}")
    return res


@app.patch("/api/memory/facts/{fid}")
async def edit_fact(fid: int, body: dict):
    try:
        res = await S().memory.edit_fact(fid, str(body.get("text", "")), body.get("category"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not res:
        raise HTTPException(404)
    return res


@app.delete("/api/memory/facts/{fid}")
async def delete_fact(fid: int):
    if not S().memory.delete_fact(fid):
        raise HTTPException(404)
    return {"ok": True}


@app.get("/api/memory/facts/{fid}/history")
async def fact_history(fid: int):
    return S().memory.fact_history(fid)


@app.get("/api/memory/episodes")
async def episodes():
    return S().db.query("SELECT * FROM episodes ORDER BY id DESC LIMIT 300")


@app.delete("/api/memory/episodes/{eid}")
async def delete_episode(eid: int):
    s = S()
    s.db.execute("DELETE FROM embeddings WHERE kind = 'episode' AND ref_id = ?", (eid,))
    s.db.execute("DELETE FROM episodes WHERE id = ?", (eid,))
    s.audit.log("memory", "episode_deleted", f"#{eid}")
    return {"ok": True}


@app.get("/api/memory/settings")
async def memory_settings():
    return {"categories": CATEGORIES, "blocked": sorted(S().memory.blocked_categories)}


@app.put("/api/memory/settings")
async def put_memory_settings(body: dict):
    S().memory.set_blocked_categories(list(body.get("blocked", [])))
    return {"categories": CATEGORIES, "blocked": sorted(S().memory.blocked_categories)}


@app.get("/api/memory/export")
async def export_memory():
    s = S()
    s.audit.log("memory", "exported", "")
    data = json.dumps(s.memory.export_all(), indent=2, default=str)
    return Response(data, media_type="application/json",
                    headers={"Content-Disposition": "attachment; filename=click-memory.json"})


@app.post("/api/memory/wipe")
async def wipe_memory(body: dict):
    if body.get("confirm") != "WIPE":
        raise HTTPException(400, "Type WIPE to confirm.")
    S().memory.wipe_all()
    return {"ok": True}


@app.post("/api/memory/consolidate")
async def consolidate():
    s = S()
    return await s.memory.consolidate(s.pc_backend if s.link.connected else s.pi_backend)


# ---- permissions / activity -------------------------------------------------------------------------------------
@app.get("/api/permissions")
async def get_permissions():
    s = S()
    return {"permissions": s.permissions.all(), "paused": s.permissions.paused}


@app.put("/api/permissions")
async def put_permissions(body: dict):
    s = S()
    for name, value in body.items():
        try:
            s.permissions.set(name, bool(value))
            s.audit.log("permission", f"{name}:{'on' if value else 'off'}", "changed in the Permissions panel")
        except KeyError:
            raise HTTPException(400, f"Unknown permission {name}") from None
    await s.push_settings_to_pc()
    await s._broadcast(s.status())
    return {"permissions": s.permissions.all(), "paused": s.permissions.paused}


@app.post("/api/pause")
async def pause(body: dict):
    s = S()
    paused = bool(body.get("paused", True))
    s.permissions.set_paused(paused)
    if paused:
        s.broker.deny_all()
    s.audit.log("permission", "pause" if paused else "resume", "from the chat")
    await s.link.send_event("system", "pause", {"paused": paused})
    await s.push_settings_to_pc()
    await s._broadcast(s.status())
    return {"paused": paused}


@app.get("/api/activity")
async def activity(category: str | None = None, limit: int = 300):
    return S().audit.recent(min(limit, 2000), category)


@app.get("/api/activity/export")
async def activity_export():
    data = json.dumps(S().audit.export(), indent=2)
    return Response(data, media_type="application/json",
                    headers={"Content-Disposition": "attachment; filename=click-activity-log.json"})


# ---- pairing ------------------------------------------------------------------------------------------------------
@app.post("/api/pair/start")
async def pair_start():
    return S().pairing.start()


class PairComplete(BaseModel):
    code: str
    pc_id: str
    name: str
    port: int
    cert_pem: str


@app.post("/api/pair/complete")
async def pair_complete(body: PairComplete, request: Request):
    s = S()
    host = request.client.host if request.client else ""
    try:
        res = s.pairing.complete(body.code, body.pc_id, body.name, host, body.port, body.cert_pem)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, f"Invalid certificate: {exc}") from exc
    await s._broadcast({"type": "paired", "pc_name": body.name})
    return res


@app.post("/api/pair/unpair")
async def unpair():
    s = S()
    s.pairing.unpair()
    if s.link.ws:
        await s.link.ws.close()
    await s._broadcast(s.status())
    return {"ok": True}


# ---- chat WebSocket ------------------------------------------------------------------------------------------------
@app.websocket("/ws")
async def chat_ws(ws: WebSocket):
    if UI_HASH and not _valid_session(ws.cookies.get("click_session")):
        await ws.close(code=4401)
        return
    await ws.accept()
    s = S()
    s.sockets.add(ws)
    state: TurnState | None = None
    turn_task: asyncio.Task | None = None
    incognito_key = f"incog-{uuid.uuid4().hex}"

    async def send(event: dict):
        await s.send_to(ws, event)

    await send(s.status())
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            kind = msg.get("type")
            if kind == "send":
                if turn_task and not turn_task.done():
                    await send({"type": "error", "message": "Still answering. Press Stop first."})
                    continue
                state = TurnState()
                incog = incognito_key if msg.get("incognito") else None

                async def run_turn(m=msg, st=state, ik=incog):
                    try:
                        await s.orchestrator.handle(
                            send=send, text=str(m.get("text", ""))[:20000],
                            conversation_id=m.get("conversation_id"),
                            upload_ids=[int(u) for u in m.get("uploads", [])][:10],
                            incognito_key=ik, state=st)
                    except Exception as exc:  # noqa: BLE001
                        log.exception("turn failed")
                        await send({"type": "error", "message": f"Something went wrong: {exc}"})
                        await send({"type": "done", "source": "", "model": ""})

                turn_task = asyncio.create_task(run_turn())
            elif kind == "typing":
                if not (turn_task and not turn_task.done()):
                    asyncio.create_task(s.orchestrator.prewarm(msg.get("conversation_id"), str(msg.get("text", ""))[:2000]))
            elif kind == "stop":
                if state:
                    state.cancel.set()
                s.broker.deny_all()
            elif kind == "confirm":
                s.broker.resolve(str(msg.get("id")), str(msg.get("decision")))
            elif kind == "cancel_image":
                await s.images.cancel(str(msg.get("job_id")))
            elif kind == "regenerate_image":
                async def regen(image_id=int(msg.get("image_id", 0))):
                    try:
                        await s.images.regenerate(image_id, send)
                    except Exception as exc:  # noqa: BLE001
                        await send({"type": "error", "message": f"Couldn't regenerate: {exc}"})
                asyncio.create_task(regen())
            elif kind == "new_chat" and msg.get("previous_id"):
                await s.orchestrator.summarize_previous(int(msg["previous_id"]))
            elif kind == "end_incognito":
                s.orchestrator.incognito_sessions.pop(incognito_key, None)
                incognito_key = f"incog-{uuid.uuid4().hex}"
    except WebSocketDisconnect:
        pass
    finally:
        s.sockets.discard(ws)
        s.socket_locks.pop(ws, None)
        # A closed tab (or a phone going to sleep) doesn't stop the answer: it finishes and is saved,
        # so it's there when the chat is reopened. Only the Stop button cancels.
        if not (turn_task and not turn_task.done()):
            s.orchestrator.incognito_sessions.pop(incognito_key, None)
