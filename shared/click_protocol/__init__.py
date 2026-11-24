"""Click Pi <-> PC protocol: message envelope, constants and TLS helpers.

Both the Pi Hub and the PC Companion import this package, so the two sides always agree
on the message format. See ../schema/message.schema.json for the JSON schema.
"""
from __future__ import annotations

import datetime as _dt
import uuid as _uuid

PROTOCOL_VERSION = 1
SERVICE_TYPE = "_click._tcp.local."
DEFAULT_COMPANION_PORT = 47321
HEARTBEAT_SECONDS = 5
HEARTBEAT_TIMEOUT_SECONDS = 15
MAX_MESSAGE_BYTES = 32 * 1024 * 1024  # screenshots and generated images travel base64-encoded

MESSAGE_TYPES = {"request", "response", "event", "stream_chunk", "error"}
SERVICES = {"inference", "image", "screen", "cursor", "files", "system"}


class ProtocolError(ValueError):
    """Raised when a message does not follow the Click protocol."""


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def make_message(
    type: str,
    service: str,
    action: str,
    payload: dict | None = None,
    *,
    id: str | None = None,
    requires_confirmation: bool = False,
) -> dict:
    msg = {
        "id": id or str(_uuid.uuid4()),
        "type": type,
        "service": service,
        "action": action,
        "payload": payload or {},
        "requires_confirmation": bool(requires_confirmation),
        "timestamp": now_iso(),
    }
    validate(msg)
    return msg


def validate(msg: object) -> dict:
    if not isinstance(msg, dict):
        raise ProtocolError("message must be a JSON object")
    for key in ("id", "type", "service", "action", "payload", "timestamp"):
        if key not in msg:
            raise ProtocolError(f"missing field: {key}")
    if msg["type"] not in MESSAGE_TYPES:
        raise ProtocolError(f"unknown type: {msg['type']!r}")
    if msg["service"] not in SERVICES:
        raise ProtocolError(f"unknown service: {msg['service']!r}")
    if not isinstance(msg["action"], str) or not msg["action"]:
        raise ProtocolError("action must be a non-empty string")
    if not isinstance(msg["payload"], dict):
        raise ProtocolError("payload must be an object")
    if not isinstance(msg["id"], str) or not msg["id"]:
        raise ProtocolError("id must be a non-empty string")
    return msg


def reply(to: dict, payload: dict | None = None, *, type: str = "response") -> dict:
    """Build a response / stream_chunk / error that answers request `to` (same id)."""
    return make_message(type, to["service"], to["action"], payload, id=to["id"])


def error_reply(to: dict, message: str, code: str = "error") -> dict:
    return reply(to, {"code": code, "message": message}, type="error")
