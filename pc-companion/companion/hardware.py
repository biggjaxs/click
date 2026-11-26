"""Detect the computer's hardware and pick models that fit it (build spec: "Works with what I have").

Model names change fast. These defaults were current in late 2026; change them in Settings or in
settings.json ("models" / "image") without touching code.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys

TIERS = {
    # One model that both chats (with tools) AND sees images, so Ollama never swaps models (that swap
    # used to cost many seconds per screen question). "instruct" variants answer without slow "thinking".
    # tier: chat/vision model, image draft model, image quality model, image enabled by default
    "cpu_small": {"chat": "qwen3-vl:2b-instruct", "vision": "qwen3-vl:2b-instruct", "draft": "stabilityai/sd-turbo", "quality": "stabilityai/sd-turbo", "image": False},
    "cpu": {"chat": "qwen3-vl:4b-instruct", "vision": "qwen3-vl:4b-instruct", "draft": "stabilityai/sd-turbo", "quality": "stabilityai/sd-turbo", "image": False},
    "gpu_mid": {"chat": "qwen3-vl:8b-instruct", "vision": "qwen3-vl:8b-instruct", "draft": "stabilityai/sdxl-turbo", "quality": "stabilityai/stable-diffusion-xl-base-1.0", "image": True},
    "gpu_big": {"chat": "qwen3-vl:8b-instruct", "vision": "qwen3-vl:8b-instruct", "draft": "stabilityai/sdxl-turbo", "quality": "black-forest-labs/FLUX.1-schnell", "image": True},
    # Intel Macs: Ollama runs on the processor only. Chat uses a small TEXT model (much faster on Intel
    # processors than a vision model); the vision model is only used when Click must look at a picture.
    "intel_mac": {"chat": "qwen2.5:1.5b", "vision": "qwen3-vl:2b-instruct", "draft": "stabilityai/sd-turbo", "quality": "stabilityai/sd-turbo", "image": False},
    "apple_small": {"chat": "qwen3-vl:2b-instruct", "vision": "qwen3-vl:2b-instruct", "draft": "stabilityai/sd-turbo", "quality": "stabilityai/sd-turbo", "image": True},
    "apple_mid": {"chat": "qwen3-vl:4b-instruct", "vision": "qwen3-vl:4b-instruct", "draft": "stabilityai/sdxl-turbo", "quality": "stabilityai/sdxl-turbo", "image": True},
    "apple_big": {"chat": "qwen3-vl:8b-instruct", "vision": "qwen3-vl:8b-instruct", "draft": "stabilityai/sdxl-turbo", "quality": "stabilityai/stable-diffusion-xl-base-1.0", "image": True},
}


def _ram_gb() -> float:
    try:
        import psutil

        return round(psutil.virtual_memory().total / 1024**3, 1)
    except Exception:  # noqa: BLE001
        return 0.0


def _nvidia() -> tuple[str, float] | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10, check=True).stdout.strip().splitlines()
        name, mem = out[0].rsplit(",", 1)
        return name.strip(), round(float(mem) / 1024, 1)
    except Exception:  # noqa: BLE001
        return None


def _other_gpu_name() -> str:
    if sys.platform == "win32":
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "(Get-CimInstance Win32_VideoController).Name"],
                                 capture_output=True, text=True, timeout=15).stdout
            return ", ".join(line.strip() for line in out.splitlines() if line.strip())
        except Exception:  # noqa: BLE001
            return ""
    return ""


def detect() -> dict:
    ram = _ram_gb()
    info = {"os": platform.system(), "os_version": platform.release(), "cpu": platform.processor() or platform.machine(),
            "ram_gb": ram, "gpu": None, "vram_gb": 0.0, "device": "cpu"}
    if sys.platform == "darwin" and platform.machine() == "arm64":
        info.update(gpu="Apple Silicon (unified memory)", vram_gb=ram, device="mps")
        info["tier"] = "apple_big" if ram >= 30 else "apple_mid" if ram >= 14 else "apple_small"  # "16 GB" reports ~15.x
        return info
    if sys.platform == "darwin":  # Intel Mac
        info["gpu"] = "Intel Mac (AI runs on the processor)"
        info["tier"] = "intel_mac"
        return info
    nv = _nvidia()
    if nv:
        info.update(gpu=nv[0], vram_gb=nv[1], device="cuda")
        info["tier"] = "gpu_big" if nv[1] >= 15 else "gpu_mid" if nv[1] >= 7.5 else ("cpu" if ram >= 14 else "cpu_small")
        if nv[1] < 7.5:
            info["note"] = "GPU has less than 8 GB, so images use the small fast model."
        return info
    other = _other_gpu_name()
    if other:
        info["gpu"] = other + " (not used: only NVIDIA GPUs are supported for now)"
    info["tier"] = "cpu" if ram >= 14 else "cpu_small"
    return info


def pick_models(info: dict, settings) -> dict:
    tier = TIERS[info["tier"]]
    models = settings["models"]
    image = settings["image"]
    enabled = image["enabled"] if image["enabled"] is not None else tier["image"]
    # small-GPU NVIDIA cards (<8 GB) can still do the light sd-turbo model
    if info["device"] == "cuda" and info["vram_gb"] < 7.5:
        enabled = image["enabled"] if image["enabled"] is not None else True
        tier = {**tier, "draft": "stabilityai/sd-turbo", "quality": "stabilityai/sd-turbo"}
    chat = models.get("chat") or tier["chat"]
    # Smart mode: a bigger model for conversation, explanations and planning lessons. On Intel Macs with
    # plenty of memory that's a 7B text model (much better at understanding how people talk); the fast small
    # model still does quick commands and each lesson step. Elsewhere the chat model is already capable.
    smart = models.get("smart") or (tier.get("smart") or chat)
    if info.get("tier") == "intel_mac" and not models.get("smart"):
        smart = "qwen2.5:7b" if (info.get("ram_gb") or 0) >= 15 else chat
    return {
        "chat": chat,
        "smart": smart,
        "vision": models.get("vision") or tier["vision"],
        "image_enabled": bool(enabled),
        "image_draft": image.get("draft_model") or tier["draft"],
        "image_quality": image.get("quality_model") or tier["quality"],
        "image_device": info["device"],
    }
