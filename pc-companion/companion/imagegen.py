"""Image generation on the PC's GPU with Hugging Face diffusers.

- Two modes: "draft" (fast, few steps) and "quality" (slower, better), models picked by hardware tier.
- Image-to-image, seeds for follow-ups, progress updates and cancel.
- A local safety filter (prompt check + an image classifier) that is ON by default.

Models download from Hugging Face the first time they're used (several GB). Run
`python -m companion.imagegen --download` once (the installer does this) to fetch them up front.
"""
from __future__ import annotations

import base64
import io
import logging
import re
import threading
from dataclasses import dataclass

log = logging.getLogger("click.imagegen")

_BLOCKED = re.compile(r"\b(nude|nudity|naked|nsfw|porn\w*|sexual|sex|explicit|hentai|topless|genitals?|gore|gory|"
                      r"beheading|dismember\w*)\b", re.I)
SAFETY_MODEL = "Falconsai/nsfw_image_detection"

MODEL_DEFAULTS = {
    # repo: (default steps, guidance, native size)
    "stabilityai/sd-turbo": (2, 0.0, 512),
    "stabilityai/sdxl-turbo": (4, 0.0, 512),
    "stabilityai/stable-diffusion-xl-base-1.0": (30, 7.0, 1024),
    "black-forest-labs/FLUX.1-schnell": (4, 0.0, 1024),
}


class ImageGenError(RuntimeError):
    pass


class Cancelled(Exception):
    pass


@dataclass
class Job:
    job_id: str
    cancelled: bool = False


class ImageGenerator:
    def __init__(self, models: dict, settings):
        self.models = models
        self.settings = settings
        self.device = models.get("image_device", "cpu")
        self._pipes: dict[str, object] = {}
        self._lock = threading.Lock()   # one image at a time
        self._jobs: dict[str, Job] = {}
        self._safety = None

    @property
    def enabled(self) -> bool:
        return bool(self.models.get("image_enabled")) and self.settings["permissions"].get("image", True)

    def capability(self) -> dict:
        if not self.models.get("image_enabled"):
            return {"enabled": False, "reason": "no supported GPU found (turn it on in Settings to use the slow CPU mode)"}
        if not self.settings["permissions"].get("image", True):
            return {"enabled": False, "reason": "turned off in the Click Companion settings"}
        return {"enabled": True, "model": self.models["image_quality"], "draft_model": self.models["image_draft"],
                "device": self.device}

    def cancel(self, job_id: str) -> None:
        if job_id in self._jobs:
            self._jobs[job_id].cancelled = True

    # ---- loading -------------------------------------------------------------------------------
    def _dtype(self):
        import torch

        return torch.float16 if self.device in ("cuda", "mps") else torch.float32

    def _load(self, repo: str):
        if repo in self._pipes:
            return self._pipes[repo]
        import torch
        from diffusers import AutoPipelineForText2Image

        # Keep only one big pipeline in memory at a time.
        for k in list(self._pipes):
            del self._pipes[k]
        if self.device == "cuda":
            torch.cuda.empty_cache()
        kwargs = {"torch_dtype": self._dtype()}
        if "FLUX" in repo:
            kwargs["torch_dtype"] = torch.bfloat16 if self.device == "cuda" else self._dtype()
        elif self.device in ("cuda", "mps"):
            kwargs["variant"] = "fp16"
        try:
            pipe = AutoPipelineForText2Image.from_pretrained(repo, **kwargs)
        except (OSError, ValueError):
            kwargs.pop("variant", None)
            pipe = AutoPipelineForText2Image.from_pretrained(repo, **kwargs)
        if hasattr(pipe, "safety_checker"):
            pipe.safety_checker = None  # we run our own classifier below (works for every model)
        if self.device == "cuda":
            vram = torch.cuda.get_device_properties(0).total_memory / 1024**3
            if "FLUX" in repo or vram < 12:
                pipe.enable_model_cpu_offload()
            else:
                pipe.to("cuda")
        else:
            pipe.to(self.device)
        if hasattr(pipe, "enable_attention_slicing") and self.device != "cuda":
            pipe.enable_attention_slicing()
        self._pipes[repo] = pipe
        return pipe

    def _safety_check(self, image) -> bool:
        """Return True if the image is safe."""
        if not self.settings["image"].get("safety_filter", True):
            return True
        if self._safety is None:
            from transformers import pipeline

            self._safety = pipeline("image-classification", model=SAFETY_MODEL, device="cpu")
        scores = {r["label"].lower(): r["score"] for r in self._safety(image)}
        return scores.get("nsfw", 0.0) < 0.5

    # ---- generation (runs in a worker thread) ---------------------------------------------------------
    def generate(self, p: dict, progress) -> dict:
        if not self.enabled:
            raise ImageGenError("Image generation is turned off on this computer.")
        if self.settings["image"].get("safety_filter", True) and _BLOCKED.search(p.get("prompt", "")):
            raise ImageGenError("Blocked by the safety filter.")
        job = Job(p.get("job_id", "job"))
        self._jobs[job.job_id] = job
        try:
            with self._lock:
                return self._generate(p, job, progress)
        finally:
            self._jobs.pop(job.job_id, None)

    def _generate(self, p: dict, job: Job, progress) -> dict:
        import torch
        from PIL import Image

        repo = self.models["image_draft"] if p.get("quality") == "draft" else self.models["image_quality"]
        steps_default, guidance, native = MODEL_DEFAULTS.get(repo, (25, 7.0, 1024))
        progress({"step": 0, "total": 0, "status": "loading_model"})
        pipe = self._load(repo)
        if job.cancelled:
            return {"cancelled": True}
        width = int(p.get("width") or native)
        height = int(p.get("height") or native)
        width, height = (max(256, min(1536, v)) // 64 * 64 for v in (width, height))
        steps = int(p.get("steps") or steps_default)
        steps = max(1, min(60, steps))
        seed = int(p.get("seed") or 0)
        generator = torch.Generator("cpu").manual_seed(seed)

        def on_step(pipeline, step, timestep, kwargs):
            if job.cancelled:
                pipeline._interrupt = True
            progress({"step": step + 1, "total": steps, "status": "generating"})
            return kwargs

        common = {"prompt": p["prompt"], "num_inference_steps": steps, "generator": generator,
                  "callback_on_step_end": on_step}
        if guidance:
            common["guidance_scale"] = guidance
        if p.get("negative_prompt") and "FLUX" not in repo and guidance:
            common["negative_prompt"] = p["negative_prompt"]
        if p.get("init_image_b64"):
            from diffusers import AutoPipelineForImage2Image

            init = Image.open(io.BytesIO(base64.b64decode(p["init_image_b64"]))).convert("RGB")
            init = init.resize((width, height))
            i2i = AutoPipelineForImage2Image.from_pipe(pipe)
            strength = max(0.1, min(0.95, float(p.get("strength") or 0.6)))
            # img2img runs only `steps * strength` steps; make sure at least one step happens
            if steps * strength < 1:
                common["num_inference_steps"] = int(1 / strength) + 1
            result = i2i(image=init, strength=strength, **common)
        else:
            result = pipe(width=width, height=height, **common)
        if job.cancelled:
            return {"cancelled": True}
        image = result.images[0]
        progress({"step": steps, "total": steps, "status": "checking"})
        if not self._safety_check(image):
            raise ImageGenError("The image was blocked by the safety filter.")
        buf = io.BytesIO()
        image.save(buf, "PNG")
        return {"png_b64": base64.b64encode(buf.getvalue()).decode(), "seed": seed, "model": repo,
                "width": image.width, "height": image.height, "steps": steps}


def download_models() -> None:
    """Fetch the image models for this computer's tier (used by the installer)."""
    from huggingface_hub import snapshot_download

    from .config import Settings
    from .hardware import detect, pick_models

    settings = Settings()
    info = detect()
    models = pick_models(info, settings)
    if not models["image_enabled"]:
        print("Image generation is off for this computer (no supported GPU). Skipping image model download.")
        return
    for repo in dict.fromkeys([models["image_draft"], models["image_quality"], SAFETY_MODEL]):
        print(f"Downloading {repo} (this can take a while the first time)…")
        allow = None
        if "FLUX" in repo:  # skip the 23 GB single-file checkpoint; diffusers only needs the folders
            allow = ["*.json", "*.txt", "*.model", "transformer/*", "text_encoder/*", "text_encoder_2/*",
                     "vae/*", "tokenizer/*", "tokenizer_2/*", "scheduler/*"]
        elif repo.startswith("stabilityai/") and info["device"] in ("cuda", "mps"):
            allow = ["*.json", "*.txt", "*fp16.safetensors", "*.fp16.safetensors", "tokenizer*/*", "*.model"]
        try:
            snapshot_download(repo, allow_patterns=allow)
        except Exception:  # noqa: BLE001 - retry without the filter (some repos have no fp16 files)
            snapshot_download(repo)
    print("Image models ready.")


if __name__ == "__main__":
    import sys

    if "--download" in sys.argv:
        download_models()
