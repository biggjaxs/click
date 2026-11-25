# PyInstaller recipe for Click.app (macOS). Built by .github/workflows/build-mac.yml:
#   pyinstaller --noconfirm packaging/Click.spec
# The app contains everything: the chat tile, the "box" part (hub) and the computer part (companion).
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
HUB = ROOT / "pi-hub"
COMP = ROOT / "pc-companion"
SHARED = ROOT / "shared"

datas = [
    (str(HUB / "app" / "static"), "app/static"),
    (str(HUB / "config"), "config"),
    (str(Path(SPECPATH) / "entitlements.plist"), "."),  # used when each Mac stamps Click with its own signature
]
binaries = []
_whisper = Path(SPECPATH) / "bin" / "whisper-cli"  # offline speech engine, built by the workflow
if _whisper.exists():
    binaries.append((str(_whisper), "bin"))
hiddenimports = (
    ["app.main", "click_protocol", "click_protocol.tls"]
    + collect_submodules("app")
    + collect_submodules("companion")
    + collect_submodules("uvicorn")
    + collect_submodules("websockets")
    + collect_submodules("zeroconf")
    + ["sqlcipher3", "sqlcipher3.dbapi2", "multipart", "python_multipart"]
)
# Packages that load parts of themselves dynamically or read their own metadata at runtime.
for pkg in ("ddgs", "pypdf", "docx", "openpyxl", "Vision", "Quartz", "ApplicationServices", "AppKit", "pynput"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # noqa: BLE001
        print("skip", pkg, exc)

# Image generation (optional: only bundled if installed in the build environment).
try:
    import diffusers  # noqa: F401

    for pkg in ("diffusers", "transformers", "accelerate", "safetensors", "huggingface_hub", "tokenizers"):
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    for pkg in ("torch", "transformers", "diffusers", "accelerate", "huggingface_hub", "tokenizers", "safetensors",
                "numpy", "tqdm", "regex", "requests", "packaging", "filelock", "pyyaml"):
        try:
            datas += copy_metadata(pkg)
        except Exception:  # noqa: BLE001
            pass
except ImportError:
    print("diffusers not installed: building without image generation")

a = Analysis(
    [str(COMP / "click_app.py")],
    pathex=[str(COMP), str(HUB), str(SHARED)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "jupyter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="Click", debug=False, strip=False, upx=False,
    console=False, argv_emulation=False, target_arch=None,
    codesign_identity=None,  # signed here only temporarily; each Mac stamps Click with its own key (selfsign.py)
    entitlements_file=str(Path(SPECPATH) / "entitlements.plist"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Click")
import re as _re

_VERSION = _re.search(r'__version__ = "([^"]+)"',
                      (Path(SPECPATH).parent / "pc-companion" / "companion" / "__init__.py").read_text()).group(1)
app = BUNDLE(
    coll,
    name="Click.app",
    icon=str(Path(SPECPATH) / "Click.icns"),
    bundle_identifier="app.click.assistant",
    info_plist={
        "CFBundleName": "Click",
        "CFBundleDisplayName": "Click",
        # the real version, so macOS always knows which copy of Click is the newest
        "CFBundleShortVersionString": _VERSION,
        "CFBundleVersion": f"{_VERSION}.{os.environ.get('GITHUB_RUN_NUMBER', '0')}",
        "LSUIElement": True,                       # lives in the menu bar, no Dock icon
        "LSMinimumSystemVersion": "14.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,   # follows light/dark mode
        "NSAppleEventsUsageDescription": "Click opens apps and makes notes, reminders and emails when you ask.",
        "NSMicrophoneUsageDescription": "So you can talk to Clicky. Your voice is turned into text on this Mac "
                                        "and is never sent anywhere.",
        "NSLocalNetworkUsageDescription": "Click connects to your Click box (Raspberry Pi) on your home network.",
        "NSBonjourServices": ["_click._tcp"],
    },
)
