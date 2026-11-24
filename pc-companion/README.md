# PC Companion

A background (tray / menu bar) app for Windows and Mac. It lends Click your computer's power and,
with permission, its screen, files and mouse.

## What it does
- **Inference proxy:** runs the chat and vision models with Ollama (localhost only) and streams
  answers to the Pi.
- **Image generation:** diffusers on an NVIDIA GPU or Apple Silicon, with draft and quality modes,
  image-to-image, progress, cancel and a safety filter.
- **Screen service:**
  - Screenshots, with blocklisted windows blacked out.
  - OCR (RapidOCR, local).
  - Window list and the accessibility tree (Windows UI Automation / macOS Accessibility).
- **Files:** approved folders only, read-only by default.
- **Ghost cursor:** a transparent, click-through overlay per monitor that draws Click's own pointer,
  labels and highlights.
  - Real clicks, typing, keys and scrolling only after a popup next to the target.
  - **Ctrl+Alt+Esc** stops everything.
- **Tray icon:** status, Pause everything, Pair with Click…, Settings (permissions, folders,
  blocklist, models).

## Install
- **Windows:** double-click `scripts/install-windows.bat`.
- **Mac:**

  ```
  bash scripts/install-mac.command
  ```

The installers get Python 3.12 via `uv`, install Ollama, pick models for your hardware
(`scripts/print_models.py --summary` shows the choice), download them, and set the Companion to
start when you log in.

## Run by hand

```
.venv/bin/python -m companion
```

(Windows: `.venv\Scripts\python.exe -m companion`)

Settings and logs live in `%APPDATA%\ClickCompanion` (Windows) or
`~/Library/Application Support/ClickCompanion` (Mac): `settings.json`, `companion.log`, and `tls/`
for the pairing certificates.

## Tests

```
.venv/bin/pip install pytest pytest-asyncio
```

```
.venv/bin/python -m pytest -q
```

The ghost cursor and actions also need the manual checklist in
`../docs/MANUAL_TEST_CHECKLIST.md`.
