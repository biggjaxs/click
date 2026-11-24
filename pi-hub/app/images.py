"""Image generation jobs (run on the PC's GPU), the gallery on the Pi, and the offline queue."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import re
import time
import uuid
from pathlib import Path

from .pc_link import PcError

log = logging.getLogger("click.images")

# Local safety filter for prompts (the PC also runs an image classifier on the output).
_BLOCKED_PROMPT = re.compile(
    r"\b(nude|nudity|naked|nsfw|porn\w*|sexual|sex|explicit|hentai|topless|genitals?|gore|gory|"
    r"beheading|dismember\w*|child abuse|cp)\b", re.I)


def check_prompt(prompt: str) -> None:
    if _BLOCKED_PROMPT.search(prompt):
        raise ValueError("That image request was blocked by the safety filter (explicit or graphic content).")


class ImageService:
    def __init__(self, cfg, db, audit, link, permissions):
        self.cfg, self.db, self.audit, self.link, self.permissions = cfg, db, audit, link, permissions
        self.running: dict[str, dict] = {}   # job key -> {"cancelled": bool}
        self.broadcast = None                # set by main: async fn(event) to all browsers

    # ---- gallery --------------------------------------------------------------------------
    def list(self, conversation_id: int | None = None, limit: int = 200) -> list[dict]:
        if conversation_id:
            rows = self.db.query("SELECT * FROM images WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                                 (conversation_id, limit))
        else:
            rows = self.db.query("SELECT * FROM images ORDER BY id DESC LIMIT ?", (limit,))
        return [self._public(r) for r in rows]

    def get(self, image_id: int) -> dict | None:
        row = self.db.one("SELECT * FROM images WHERE id = ?", (image_id,))
        return self._public(row) if row else None

    def _public(self, row: dict) -> dict:
        params = json.loads(row["params"] or "{}")
        return {"id": row["id"], "conversation_id": row["conversation_id"], "url": f"/api/images/{row['id']}/file",
                "prompt": row["prompt"], "negative_prompt": row["negative_prompt"], "params": params,
                "created_at": row["created_at"]}

    def path(self, image_id: int) -> Path | None:
        row = self.db.one("SELECT filename FROM images WHERE id = ?", (image_id,))
        return self.cfg.images_dir / row["filename"] if row else None

    def delete(self, image_id: int) -> bool:
        p = self.path(image_id)
        if not p:
            return False
        p.unlink(missing_ok=True)
        self.db.execute("DELETE FROM images WHERE id = ?", (image_id,))
        return True

    def last_for_conversation(self, conversation_id: int | None) -> dict | None:
        if not conversation_id:
            return None
        row = self.db.one("SELECT * FROM images WHERE conversation_id = ? ORDER BY id DESC LIMIT 1", (conversation_id,))
        return self._public(row) if row else None

    def _source_b64(self, ref: str | None) -> str | None:
        if not ref:
            return None
        m = re.fullmatch(r"(image|upload):(\d+)", ref.strip())
        if not m:
            raise ValueError("source_image must look like 'image:12' or 'upload:3'.")
        kind, rid = m.group(1), int(m.group(2))
        if kind == "image":
            p = self.path(rid)
        else:
            row = self.db.one("SELECT stored_name, mime FROM uploads WHERE id = ?", (rid,))
            if row and not row["mime"].startswith("image/"):
                raise ValueError("That upload is not an image.")
            p = self.cfg.uploads_dir / row["stored_name"] if row else None
        if not p or not p.exists():
            raise ValueError(f"Source image {ref} not found.")
        return base64.b64encode(p.read_bytes()).decode()

    # ---- generation -------------------------------------------------------------------------
    def _params(self, a: dict) -> dict:
        prompt = (a.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("Empty image prompt.")
        check_prompt(prompt)
        style = (a.get("style") or "").strip()
        quality = a.get("quality") if a.get("quality") in ("draft", "quality") else "quality"
        seed = a.get("seed")
        seed = int(seed) if isinstance(seed, (int, float, str)) and str(seed).lstrip("-").isdigit() else random.randint(0, 2**31 - 1)
        return {
            "prompt": f"{prompt}, {style} style" if style and style.lower() not in prompt.lower() else prompt,
            "negative_prompt": (a.get("negative_prompt") or "").strip(),
            "width": int(a["width"]) if a.get("width") else None,
            "height": int(a["height"]) if a.get("height") else None,
            "steps": int(a["steps"]) if a.get("steps") else None,
            "seed": seed,
            "quality": quality,
            "source_image": a.get("source_image"),
            "strength": float(a.get("strength") or 0.6),
        }

    async def generate_tool(self, ctx, a: dict) -> str:
        params = self._params(a)
        if not self.link.connected:
            if a.get("queue_if_offline"):
                job_id = self.db.execute(
                    "INSERT INTO image_jobs(conversation_id, params, status, created_at) VALUES (?, ?, 'queued', ?)",
                    (ctx.conversation_id, json.dumps(params), time.time()))
                self.audit.log("image", "queued", params["prompt"][:200])
                await ctx.send({"type": "notice", "text": f"Image queued (job {job_id}). It will be made when your computer is back."})
                return f"Queued as job {job_id}. It will appear in the gallery when the PC is back online."
            return ("The PC is offline, and images can only be made on its GPU. Tell the user plainly and ask "
                    "whether they want to queue the image until the PC is back (then call again with queue_if_offline=true).")
        caps = self.link.capabilities.get("image", {})
        if not caps.get("enabled", False):
            return ("Image generation isn't available on this PC (" + caps.get("reason", "no usable GPU") +
                    "). Tell the user plainly; they can turn it on in the PC Companion settings.")
        image = await self._generate(params, ctx.conversation_id, ctx.send)
        ctx.shown_images.append(image["id"])
        return (f"Image created and shown to the user (image:{image['id']}, seed {image['params']['seed']}, "
                f"model {image['params'].get('model')}). Don't describe it at length; one short sentence is enough.")

    async def _generate(self, params: dict, conversation_id: int | None, send) -> dict:
        job_key = uuid.uuid4().hex[:10]
        self.running[job_key] = {"cancelled": False}
        payload = {k: v for k, v in params.items() if k != "source_image"}
        payload["job_id"] = job_key
        payload["init_image_b64"] = self._source_b64(params.get("source_image"))
        self.audit.log("image", "generate", f"{params['quality']}: {params['prompt'][:200]}")
        await send({"type": "image_progress", "job_id": job_key, "step": 0, "total": 0, "status": "starting",
                    "prompt": params["prompt"]})
        final = None
        try:
            async for chunk in self.link.stream("image", "generate", payload, timeout=self.cfg.image_timeout_s):
                if "step" in chunk:
                    await send({"type": "image_progress", "job_id": job_key, "step": chunk["step"],
                                "total": chunk.get("total", 0), "status": chunk.get("status", "generating")})
                if chunk.get("png_b64"):
                    final = chunk
        except PcError as exc:
            await send({"type": "image_progress", "job_id": job_key, "status": "failed", "error": str(exc)})
            raise
        finally:
            self.running.pop(job_key, None)
        if not final:
            raise PcError("No image came back from the PC.")
        if final.get("cancelled"):
            await send({"type": "image_progress", "job_id": job_key, "status": "cancelled"})
            raise PcError("Image generation was cancelled.", "cancelled")
        image = self._save(final, params, conversation_id)
        await send({"type": "image_progress", "job_id": job_key, "status": "done"})
        await send({"type": "image", "image": image})
        return image

    def _save(self, final: dict, params: dict, conversation_id: int | None) -> dict:
        filename = f"{int(time.time())}-{uuid.uuid4().hex[:8]}.png"
        (self.cfg.images_dir / filename).write_bytes(base64.b64decode(final["png_b64"]))
        stored = {**{k: v for k, v in params.items() if k not in ("prompt", "negative_prompt")},
                  "seed": final.get("seed", params["seed"]), "model": final.get("model"),
                  "width": final.get("width"), "height": final.get("height"), "steps": final.get("steps")}
        image_id = self.db.execute(
            "INSERT INTO images(conversation_id, filename, prompt, negative_prompt, params, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, filename, params["prompt"], params["negative_prompt"], json.dumps(stored), time.time()))
        return self.get(image_id)

    async def regenerate(self, image_id: int, send) -> dict:
        img = self.get(image_id)
        if not img:
            raise ValueError("Image not found.")
        if not self.permissions.enabled("image_generation"):
            raise ValueError("Image generation is turned off in Permissions.")
        if not self.link.connected:
            raise PcError("Your computer is offline.", "offline")
        p = img["params"]
        params = self._params({"prompt": img["prompt"], "negative_prompt": img["negative_prompt"],
                               "width": p.get("width"), "height": p.get("height"),
                               "quality": p.get("quality"), "source_image": p.get("source_image"),
                               "strength": p.get("strength")})  # new random seed
        return await self._generate(params, img["conversation_id"], send)

    async def cancel(self, job_key: str) -> None:
        if job_key in self.running:
            self.running[job_key]["cancelled"] = True
            try:
                await self.link.request("image", "cancel", {"job_id": job_key}, timeout=10)
            except PcError:
                pass

    async def process_queue(self) -> None:
        """Called when the PC connects: make any images that were queued while it was off."""
        jobs = self.db.query("SELECT * FROM image_jobs WHERE status = 'queued' ORDER BY id")
        for job in jobs:
            if not self.link.connected:
                return
            self.db.execute("UPDATE image_jobs SET status = 'running' WHERE id = ?", (job["id"],))
            params = json.loads(job["params"])

            async def send(ev):
                if self.broadcast:
                    await self.broadcast({**ev, "queued_job": job["id"]})
            try:
                image = await self._generate(params, job["conversation_id"], send)
                self.db.execute("UPDATE image_jobs SET status = 'done', image_id = ? WHERE id = ?", (image["id"], job["id"]))
                if job["conversation_id"]:
                    self.db.execute(
                        "INSERT INTO messages(conversation_id, role, content, meta, created_at) VALUES (?, 'assistant', ?, ?, ?)",
                        (job["conversation_id"], "Here's the image you queued while your computer was off.",
                         json.dumps({"images": [image["id"]], "source": "pc"}), time.time()))
            except Exception as exc:  # noqa: BLE001
                self.db.execute("UPDATE image_jobs SET status = 'failed', error = ? WHERE id = ?", (str(exc), job["id"]))
            await asyncio.sleep(0)

    def queued(self) -> list[dict]:
        return self.db.query("SELECT id, conversation_id, params, status, error, created_at FROM image_jobs "
                             "WHERE status IN ('queued','running','failed') ORDER BY id DESC LIMIT 50")
