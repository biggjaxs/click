"""Chat backends.

- LocalOllama: the small backup model running on the Pi (used when the PC is off).
- PcBackend:   the big models on the PC, reached through the encrypted PC link.

Both yield the same events:
    {"type": "token", "text": "..."}
    {"type": "tool_calls", "calls": [{"name": "...", "arguments": {...}}]}
    {"type": "done", "model": "...", "source": "pc" | "pi"}
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx


class BackendUnavailable(RuntimeError):
    pass


def _normalize_tool_calls(raw: list | None) -> list[dict]:
    calls = []
    for call in raw or []:
        fn = call.get("function", call)
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"_raw": args}
        calls.append({"name": fn.get("name", ""), "arguments": args or {}})
    return calls


class LocalOllama:
    source = "pi"

    def __init__(self, url: str, model: str, embed_model: str, timeout: float = 120):
        self.url = url.rstrip("/")
        self.model = model
        self.embed_model = embed_model
        self.timeout = timeout

    async def available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                r = await client.get(f"{self.url}/api/version")
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def chat_stream(
        self, messages: list[dict], tools: list[dict] | None = None, *, role: str = "chat",
        options: dict | None = None, fmt: str | None = None,
    ) -> AsyncIterator[dict]:
        body = {"model": self.model, "messages": messages, "stream": True,
                "options": {"num_ctx": 4096, **(options or {})}}
        if tools:
            body["tools"] = tools
        if fmt:
            body["format"] = fmt
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=5)) as client:
                async with client.stream("POST", f"{self.url}/api/chat", json=body) as resp:
                    if resp.status_code != 200:
                        text = (await resp.aread()).decode(errors="replace")[:300]
                        raise BackendUnavailable(f"Backup model error {resp.status_code}: {text}")
                    calls: list[dict] = []
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        chunk = json.loads(line)
                        msg = chunk.get("message", {})
                        if msg.get("content"):
                            yield {"type": "token", "text": msg["content"]}
                        if msg.get("tool_calls"):
                            calls.extend(_normalize_tool_calls(msg["tool_calls"]))
                        if chunk.get("done"):
                            break
                    if calls:
                        yield {"type": "tool_calls", "calls": calls}
        except httpx.HTTPError as exc:
            raise BackendUnavailable(f"Backup model is not reachable: {exc}") from exc
        yield {"type": "done", "model": self.model, "source": self.source}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{self.url}/api/embed", json={"model": self.embed_model, "input": texts})
            r.raise_for_status()
            return r.json()["embeddings"]


class PcBackend:
    source = "pc"

    def __init__(self, link):
        self.link = link

    async def available(self) -> bool:
        return self.link.connected

    @property
    def model(self) -> str:
        return self.link.capabilities.get("models", {}).get("chat", "PC model")

    async def chat_stream(
        self, messages: list[dict], tools: list[dict] | None = None, *, role: str = "chat",
        options: dict | None = None, fmt: str | None = None,
    ) -> AsyncIterator[dict]:
        if not self.link.connected:
            raise BackendUnavailable("PC is offline")
        payload = {"messages": messages, "tools": tools or [], "role": role,
                   "options": options or {}, "format": fmt}
        model = self.model
        async for chunk in self.link.stream("inference", "chat", payload):
            if chunk.get("token"):
                yield {"type": "token", "text": chunk["token"]}
            if chunk.get("tool_calls"):
                yield {"type": "tool_calls", "calls": _normalize_tool_calls(chunk["tool_calls"])}
            if chunk.get("model"):
                model = chunk["model"]
        yield {"type": "done", "model": model, "source": self.source}


async def complete(backend, messages: list[dict], *, fmt: str | None = None, role: str = "chat") -> str:
    """Non-streaming helper: collect the whole answer as text."""
    out = []
    async for ev in backend.chat_stream(messages, None, role=role, fmt=fmt, options={"temperature": 0.2}):
        if ev["type"] == "token":
            out.append(ev["text"])
    return "".join(out)
