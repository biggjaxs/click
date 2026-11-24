# Decisions

A running log of key choices and why. Newest at the bottom.

## Setup answers (Nov 2026)

| Question | Answer |
|---|---|
| Pi | Raspberry Pi 5, 4 GB RAM, microSD card (SSD later) |
| PC | Windows and Mac (Linux later). Specs detected automatically at install |
| Network | Same home network as the PC (Wi-Fi or Ethernet) |
| Folders | Documents, Downloads, Desktop, Pictures, but only after asking permission |
| Name / look | **Click**. Colour and look are customisable |
| Online | Web search allowed. No cloud AI models. Memory never leaves the box |
| Terminal level | Beginner: every command spelled out |

## D1. Project name is "Click", not "PiMind"
The build prompt says to use PiMind unless given another name. The assistant is named Click, so
the code, hostname (`click.local`), service (`click-hub`) and mDNS type (`_click._tcp`) all use it.

## D2. Pi 5 with 4 GB instead of 8 GB
Pi prices rose several times in 2026 because of memory shortages. The 8 GB model costs about
$175–200, over the $150 budget. 4 GB is enough because the PC does the heavy thinking. The cost
is that the backup model must be small (about 1B parameters).

## D3. microSD card for now, NVMe SSD later
The build prompt asks for an SSD because SD cards wear out under database writes. To stay in
budget we start on a good A2 card. Data lives in `/var/lib/click` and `/etc/click`, so moving to an
SSD is a copy (README, "Moving to an SSD").

## D4. Models (check again when updating; names change fast)
- **Pi backup:** `gemma3:1b` (Gemma Terms of Use). **Pi memory embeddings:** `all-minilm`
  (Apache-2.0, about 46 MB).
- **PC chat:** `qwen3:4b` / `8b` / `14b` by tier (Apache-2.0); they support tool calling.
  "Thinking" is off for speed and switches on automatically if a model needs it.
- **PC vision:** `qwen2.5vl:3b` / `7b` (Apache-2.0).
- **Images:**
  - Small GPU / CPU: `sd-turbo`.
  - 8–12 GB VRAM or Apple Silicon: `sdxl-turbo` (draft) + SDXL base 1.0 (quality).
  - 16 GB+: FLUX.1-schnell (quality, Apache-2.0).
  - Licences: sd-turbo and sdxl-turbo use the Stability AI Community License (free under $1M
    yearly revenue); SDXL base uses CreativeML OpenRAIL++-M.
- Every model can be changed in the Companion's **Settings → Models** without code changes.

## D5. Web search: on, but only search words leave the box
"No cloud" is kept: no cloud AI, no telemetry, and chats, memories and files stay on the Pi. Web
search sends only the search words to DuckDuckGo, through the `ddgs` library, with no account.
- Queries that look like a password, card or ID number are refused.
- `fetch_web_page` only opens public addresses, never your home network.
- Results are marked as untrusted data, and the chat shows a "Searched the web" label with sources.
- Search is a Permissions toggle (on by default, as requested).

## D6. Customisable look
Chat settings: theme (system/light/dark), accent colour, text size, chat style and name. Cursor
settings: colour, shape (arrow, dot, ring) and name tag. Settings live on the Pi and are pushed to
the PC on connect, so the look follows the box.

## D7. Windows and Mac from the start
Each OS-specific part has a `windows` and a `macos` implementation behind one interface
(`companion/platforms`). This covers window lists, the accessibility tree, password-field
detection and hiding the overlay from screenshots. Linux has a stub so the code runs for
development; Linux support is future work.

## D8. Pi security defaults
- Firewall (ufw): only home-network addresses can reach the chat (80) and SSH (22). Ollama on the
  Pi is localhost-only.
- SSH password login is turned off only once an SSH key exists, so a beginner can't lock
  themselves out.
- Raspberry Pi Imager forces a custom username and password, so there's no default login.
- Automatic security updates are on.
- The service runs as the normal user with a read-only system and home. It can write only to
  `/var/lib/click` and gets only the right to use port 80.

## D9. Encryption at rest: SQLCipher, key derived from your password
The installer derives a 256-bit key from the Click password with scrypt and stores it in
`/etc/click/db.key` (root-only). systemd passes it to the service as a private credential.
**Trade-off:** the key sits on the same SD card, so a thief with the whole card could unlock it if
they also got root-level access to the files. We chose this so Click restarts by itself after a
power cut. Stronger option (future): type the password in the browser after every reboot.
Generated images and uploads are files in `/var/lib/click` (owner-only permissions), not inside
the encrypted database.

## D10. Chat login
The chat needs the Click password (the same one), so other people on your Wi-Fi can't read your
memories. Sessions last 30 days, failed logins are rate-limited, and the cookie is HttpOnly and
SameSite=Strict. The pairing endpoint stays open, because it's protected by the 6-digit code.

## D11. One encrypted channel for everything
The build prompt suggests HTTPS for large uploads. We send screenshots and images base64-encoded
over the same mutual-TLS WebSocket (limit 32 MB). It's simpler to secure and the files are small
(screenshots are downscaled WebP). This can be revisited if images grow.

## D12. Vector search with numpy instead of sqlite-vec
sqlite-vec can't load into SQLCipher reliably on the Pi. Embeddings are stored as blobs in the
encrypted database, and search is a numpy dot product, which is fast enough for tens of thousands
of items. If embeddings are unavailable, recall falls back to keyword matching.

## D13. Image generation with diffusers, not ComfyUI
The build prompt prefers ComfyUI. We chose diffusers for a one-click install on Windows and Mac
with no second app to manage. It still has two modes (draft and quality), image-to-image, seeds,
progress, cancel and a safety filter (prompt check plus the Falconsai NSFW classifier, Apache-2.0,
on by default). `imagegen.py` has a single `generate()` entry point, so a ComfyUI backend can be
added behind it later. AMD GPUs aren't used for images yet (NVIDIA CUDA and Apple MPS only).

## D14. The confirmation rules
- **Look, point, highlight, inspect, teach:** allowed whenever the cursor feature is on.
- **Screen tools:**
  - Run without asking when your message is clearly about the screen ("what's on my screen?",
    "where is…").
  - Otherwise Click asks in the chat (Allow once / for this chat / Deny).
  - Watch mode counts as standing permission and shows an indicator.
- **File reads:** always ask in the chat, with an "allow for this chat" option.
- **File changes:** a separate permission (off by default), and Click asks every time.
- **Clicks, typing, keys, scroll:** a popup next to the target on the PC (Allow once / for this
  task / Deny), which denies after 60 s.
- **High-risk actions:** words like send, buy, pay, delete or settings, or keys like Enter, Alt+F4,
  Cmd+Q. These ask every time with no "for this task" option.
- **Kill switch:** Ctrl+Alt+Esc stops everything. Moving your real mouse during an action also
  stops it.

## D15. Companion packaging
The installers use `uv` to get Python 3.12 and install into `pc-companion/.venv`, with a startup
shortcut (Windows) or LaunchAgent (Mac). A single PyInstaller .exe/.app is future work. **Mac
note:** permissions are granted to `python3.12`, not to an app named "Click", until we ship a
signed .app.

## D16. Closing the browser doesn't stop an answer
Only the Stop button cancels. If the tab closes or a phone sleeps, the answer finishes and is
saved for when you come back.

## D17. Things deliberately left for later
Voice (push-to-talk with Whisper and spoken replies with Piper), wake-on-LAN, background indexing
of whole folders (files are indexed when read), a true second pointer on Linux (X11 MPX), ComfyUI
workflows, AMD/Intel GPU image generation, and a signed one-file installer.

## Version 1.1 (after the first test)

## D18. One model that sees and chats (speed)
`qwen3-vl` (2b/4b/8b, "instruct") handles chat, tool calls AND images, so Ollama never swaps
between a chat model and a vision model. That swap used to cost many seconds per screen question.
Models stay loaded for 60 minutes (`keep_alive`) and are warmed up at start.

## D19. Faster screen questions
- When a message is about the screen, the Pi grabs the screen BEFORE asking the model, in one
  PC request (`screen.context`). That request returns the active window, other windows, all
  visible text (OCR) and a 1024 px screenshot, and saves a whole model round trip.
- OCR on Macs uses Apple's Vision framework (on-device, Neural Engine). Windows uses RapidOCR.
  A full screen read took about 1 s with RapidOCR on a plain cloud CPU in testing.
- The system prompt never changes between turns; time, memories and screen go in a context block
  on the latest message, so the model reuses its cached work.
- Tool descriptions are shorter (a smaller prompt is faster).
- Every answer shows how long it took, so speed can be measured on real hardware.

## D20. Cursor you can see, and that explains itself
- On macOS the overlay is raised to pop-up-menu level and set to show on every Space and next to
  full-screen apps. Qt's "stay on top" alone wasn't enough.
- The cursor is 1.5x normal size, with a glow and an arrival ripple. It always appears from the
  chat tile and travels to its target.
- Every tool step sends a caption ("Looking for 'Wi-Fi'…") that shows next to the cursor.
- `find_ui_element` falls back to on-screen text (OCR boxes) when the accessibility tree has
  nothing, so pointing works in web pages and apps with poor accessibility. Missing macOS
  permissions are reported in plain words.
- The menu bar item "Show me Click's cursor" runs a demo, which also plays once after setup.

## D21. The tile and standalone mode
- The chat is a floating, frameless window (Qt WebEngine showing the same chat app in a compact
  layout).
- With no Pi, the "box" part runs inside the app on 127.0.0.1 and pairs itself automatically.
  Memory is encrypted with SQLCipher. The key is in a private file (owner-only) in the app's settings folder; the Keychain was dropped because it can freeze waiting for an unlock prompt.
- With a Pi, the tile shows the box's chat instead ("Use a Click box" in the menu bar).
- WebEngine needs macOS 14+.

## D22. Click.dmg built by GitHub
GitHub's Mac runners build Click.app with PyInstaller on every push to `main`. The build runs the
tests and a start-up self-test of the finished app (hub up and linked), then publishes Click.dmg
as a Release. The app is ad-hoc signed, not notarized (that needs a paid Apple Developer account,
$99/year), so the first open needs "Open Anyway" once. First-run setup installs Ollama and
downloads models with a progress bar, so no Terminal is needed. Windows packaging is future work;
the Windows installer script still works.

## D23. Design
Neutral palette, one accent colour (#6d5dfc), simple line icons drawn for Click, and no emojis.
Tool activity shows as quiet step lines with timings instead of coloured chips. Status lives in
one small pill.
