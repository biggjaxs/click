"""Inference proxy: forwards chat and vision requests to Ollama, which listens on localhost only."""
from __future__ import annotations

import base64
import io
import json
import logging
from typing import Awaitable, Callable

import httpx


log = logging.getLogger("click.inference")
KEEP_ALIVE = "24h"  # keep the model loaded all day: reloading it after a break costs many seconds
def _physical_cores() -> int:
    """The AI engine runs fastest with one thread per REAL core. Intel chips report twice as many
    (hyper-threading); using those extra threads makes answers slower, not faster. A 2018 i9 = 6."""
    import os
    import subprocess
    import sys

    if sys.platform == "darwin":
        try:
            return int(subprocess.run(["sysctl", "-n", "hw.physicalcpu"], capture_output=True, text=True,
                                      timeout=3).stdout.strip())
        except Exception:  # noqa: BLE001
            pass
    try:
        import psutil

        n = psutil.cpu_count(logical=False)
        if n:
            return n
    except Exception:  # noqa: BLE001
        pass
    return max(2, (os.cpu_count() or 4) // 2)


THREADS = max(2, _physical_cores())


class InferenceError(RuntimeError):
    pass


def to_jpeg_b64(image_b64: str) -> str:
    """Ollama reliably accepts PNG/JPEG; screenshots arrive as WebP."""
    from PIL import Image

    img = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


class Inference:
    def __init__(self, settings, models: dict):
        self.settings = settings
        self.models = models

    @property
    def url(self) -> str:
        return self.settings["ollama_url"].rstrip("/")

    async def loaded_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"{self.url}/api/ps")
                return [m["name"] for m in r.json().get("models", [])]
        except Exception:  # noqa: BLE001
            return []

    async def installed_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"{self.url}/api/tags")
                return [m["name"] for m in r.json().get("models", [])]
        except Exception:  # noqa: BLE001
            return []

    @property
    def num_ctx(self) -> int:
        """Smaller working memory on processor-only computers = faster answers."""
        return 3072 if self.models.get("image_device") == "cpu" else 8192

    @property
    def vision_in_chat(self) -> bool:
        """True when the chat model can see images itself (no separate vision step needed)."""
        chat = self.models["chat"].lower()
        return chat == self.models["vision"].lower() or "-vl" in chat or "vl:" in chat

    async def warm_up(self) -> None:
        """Load the model into memory right away so the first question is fast."""
        try:
            async with httpx.AsyncClient(timeout=300) as c:
                await c.post(f"{self.url}/api/generate", json={"model": self.models["chat"], "keep_alive": KEEP_ALIVE})
                await self.refresh_installed()
                smart = self.smart_model()
                if smart:  # keep the smart model ready too (both fit easily in memory)
                    await c.post(f"{self.url}/api/generate", json={"model": smart, "keep_alive": KEEP_ALIVE})
        except Exception:  # noqa: BLE001 - Ollama not running yet; the first question will load it
            pass

    def smart_model(self) -> str | None:
        """The smart model, if Smart mode is on and it's downloaded (else None: the fast model answers)."""
        smart = self.models.get("smart")
        if not smart or smart == self.models["chat"] or not self.settings.get("smart_mode", True):
            return None
        return smart if smart in getattr(self, "_installed", set()) or \
            smart.removesuffix(":latest") in getattr(self, "_installed", set()) else None

    async def refresh_installed(self) -> None:
        try:
            self._installed = set(await self.installed_models())
        except Exception:  # noqa: BLE001
            pass

    def download_smart(self, progress=lambda pct: None) -> bool:
        """Get the smart model once (about 4.7 GB), quietly in the background. Blocking: run in a thread."""
        smart = self.models.get("smart")
        if not smart or smart == self.models["chat"]:
            return False
        import httpx as _h

        try:
            with _h.stream("POST", f"{self.url}/api/pull", json={"model": smart, "stream": True},
                           timeout=_h.Timeout(3600, connect=10)) as r:
                last = -1
                for line in r.iter_lines():
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    if d.get("total") and d.get("completed"):
                        pct = int(d["completed"] * 100 / d["total"])
                        if pct != last and d["total"] > 100_000_000:
                            last = pct
                            progress(pct)
                    if d.get("status") == "success":
                        self._installed = set(getattr(self, "_installed", set())) | {smart}
                        return True
        except Exception as exc:  # noqa: BLE001
            log.warning("couldn't download the smart model: %s", exc)
        return False

    async def chat(self, payload: dict, emit: Callable[[dict], Awaitable[None]]) -> dict:
        role = payload.get("role")
        model = self.models["vision"] if role == "vision" else (
            (self.smart_model() or self.models["chat"]) if role == "smart" else self.models["chat"])
        engine = getattr(self, "engine", None)
        base = engine.url if engine is not None and engine.serves(model) else self.url  # Click Engine or Ollama
        body = {"model": model, "messages": payload.get("messages", []), "stream": True, "keep_alive": KEEP_ALIVE,
                "options": {"num_ctx": self.num_ctx, "num_thread": THREADS, **(payload.get("options") or {})}}
        if payload.get("tools"):
            body["tools"] = payload["tools"]
        if payload.get("format"):
            body["format"] = payload["format"]
        if not self.settings.get("think", False):
            body["think"] = False
        calls: list = []
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=5)) as client:
                async with client.stream("POST", f"{base}/api/chat", json=body) as resp:
                    if resp.status_code == 400 and "think" in body:
                        raise _RetryWithoutThink()
                    if resp.status_code != 200:
                        text = (await resp.aread()).decode(errors="replace")[:300]
                        raise InferenceError(f"The AI model on this computer returned an error: {text}")
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        chunk = json.loads(line)
                        msg = chunk.get("message", {})
                        if msg.get("content"):
                            await emit({"token": msg["content"]})
                        if msg.get("tool_calls"):
                            calls.extend(msg["tool_calls"])
                        if chunk.get("done"):
                            break
        except _RetryWithoutThink:
            self.settings.data["think"] = True  # this model doesn't support turning thinking off
            payload = {**payload}
            return await self.chat(payload, emit)
        except httpx.ConnectError as exc:
            raise InferenceError("Ollama isn't running on this computer. Start Ollama and try again.") from exc
        return {"model": model, "tool_calls": calls}

    async def describe(self, payload: dict) -> dict:
        image = to_jpeg_b64(payload["image_b64"])
        body = {"model": self.models["vision"], "stream": False, "keep_alive": KEEP_ALIVE,
                "messages": [{"role": "user", "content": payload.get("prompt") or "Describe this image.", "images": [image]}],
                "options": {"num_ctx": 8192, "temperature": 0.2}}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=5)) as client:
                r = await client.post(f"{self.url}/api/chat", json=body)
        except httpx.ConnectError as exc:
            raise InferenceError("Ollama isn't running on this computer.") from exc
        if r.status_code != 200:
            raise InferenceError(f"The vision model returned an error: {r.text[:300]}")
        return {"text": r.json().get("message", {}).get("content", ""), "model": self.models["vision"]}


class _RetryWithoutThink(Exception):
    pass
