"""Print the Ollama models this computer should use (one per line). Used by the installers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from companion.config import Settings  # noqa: E402
from companion.hardware import detect, pick_models  # noqa: E402

info = detect()
models = pick_models(info, Settings())
if "--summary" in sys.argv:
    print(f"{info['os']} | {info['ram_gb']} GB RAM | GPU: {info.get('gpu') or 'none'} | tier: {info['tier']}")
    print(f"chat: {models['chat']} | vision: {models['vision']} | images: "
          f"{models['image_quality'] if models['image_enabled'] else 'off (no supported GPU)'}")
elif "--images" in sys.argv:
    print("yes" if models["image_enabled"] else "no")
else:
    print(models["chat"])
    print(models["vision"])
