# Click roadmap and design notes

Ideas and answers from planning conversations, kept for future versions.
Status: **Built** = in the app now, **Next** = planned soon, **Later** = good idea, not scheduled.

---

## 1. Use the computer's power without overloading it or leaving things behind

**Answer: partly possible.**

- **Built:** with a Click box (Pi), chats, memories and settings live only on the box. The computer
  holds them just for the length of one answer.
- **Unavoidable:** to think, the computer must load the AI model (2–6 GB) into its own RAM and use
  its processor. That is the "power" being borrowed.
- **Later: nothing left on the computer.** The model file could be stored on the box and copied to
  the computer each time it's plugged in, then deleted when unplugged. Cost: a 1–2 minute wait
  each time you plug in (USB on the Pi 5 copies roughly 3 GB in about 1–2 minutes).
- **Never overload.**
  - **Built:** a model that leaves room for the computer's own work (chosen by RAM tier).
  - **Built:** the model is freed from RAM after 10 idle minutes.
  - **Built:** Click never uses all processor cores (it leaves 2 free).
  - **Next:** a "Click is using X GB" indicator.

## 2. Start working while the user is still typing

**Answer: yes, a big speed-up.** It can't write the answer before the question is finished, but it
can do the slow preparation:

- **Next:** while you type, send the text so far so the model "reads" it in advance (prompt
  prefill), and grab the screen and recall memories in parallel. When you press Enter, only the
  last few words are left to process, so answers start almost immediately.
- **Later:** a "speculative" draft answer that is thrown away if the question changes. Uses more
  power, so it's optional.

## 3. Battery, Bluetooth and one cable

- **Battery: yes.** A Pi 5 runs from a USB-C power bank (5 V / 3–5 A) or a UPS/battery HAT. A
  10,000 mAh bank gives a few hours.
- **Bluetooth: no, for the main link.** It's far too slow for screenshots and answers. Use **Wi-Fi**
  for wireless (same home network, or the Pi makes its own hotspot). Bluetooth could still be used
  for quick pairing or a remote button.
- **One cable: probably yes.**
  - The Pi 5's USB-C port can take power and carry data (USB "gadget" mode: the Pi appears to the
    laptop as a network adapter).
  - No video is needed, because Click shows in the tile on the computer's screen.
  - **To test:** the laptop's USB-C port must supply enough power (15 W+), and the Pi 5 may
    limit its own USB peripherals on lower power.

## 4. How much RAM does the Pi need?

- With the computer doing the thinking, the box only runs the chat app, memory and search.
  **2 GB is enough.**
- **4 GB+** adds the backup model that answers when the computer is off. 8 GB allows a better
  backup model.
- **Decision:** choose 2 GB to save money if the box never needs to work alone.

## 5. Combine the computer's RAM with the Pi's RAM?

**Answer: not usefully.**
- RAM can't be pooled across devices.
- Models *can* be split across machines (e.g. llama.cpp's RPC mode), but over USB or Wi-Fi it's
  very slow, and 2 GB from a Pi adds little.

**Better plan for 8 GB computers (Next):**
- Use the 4B model with a smaller context window, unload it when idle, and keep the screenshot
  small. Today's small models are good enough for chat, screen reading and pointing.
- Expect it to be slower and a bit less smart than on 16 GB, but usable.
- Add an "8 GB mode" tier and test it on a real 8 GB Mac.

## 6. Can Click's cursor really click?

**Built.** After you press **Allow** on the popup next to the target, Click really clicks, types,
presses keys or scrolls, then puts your mouse back. High-risk actions ask every time.
**Ctrl+Alt+Esc**, or moving your own mouse, stops it.
- **Needs:** the macOS Accessibility permission.
- **To test:** a real click on a real Mac. In the virtual test, the popup and Deny worked.

## 7. Plugged into a laptop, can the box control the screen?

- **Built (recommended):** the box tells the Click app on the laptop what to do, and the app moves
  Click's cursor and clicks. The app can also see the screen, so it knows where to click.
- **Later (alternative):** a Pi can pretend to be a USB mouse and keyboard (HID gadget) with no app
  installed. But it would be blind (it can't see the screen) and harder to keep safe, so this is
  only for special cases.

## 8. Microphone and status light (Later)

- USB or I2S microphone on the Pi, push-to-talk button, local speech-to-text (Whisper) and spoken
  replies (Piper).
- LED or light ring on the Pi's GPIO pins:
  - off = idle
  - breathing = listening
  - spinning = thinking
  - solid = speaking
  - red = paused or stopped

## 9. Security

**Built:**
- Encrypted memory (SQLCipher) and a password on the box's chat. Firewall: home network only.
- Mutual-TLS link that works only with your paired devices.
- Approval for every real action. Kill switch.
- Blocklist (banking, password managers) and blackout of those windows in screenshots.
- Never types or stores passwords or card numbers. Prompt-injection guard. Full activity log.

**Next:**
- Sign and notarize the Mac app (Apple Developer, $99/year). This removes the "Open Anyway"
  step and proves updates come from us.
- Signed updates (see section 10).
- Encrypt the 6-digit pairing exchange, not just the connection afterwards.

**Later:**
- Full-disk encryption on the Pi.
- Optional "type password after every reboot" mode.
- Independent security review.

## 10. Easier downloads and updates (top priority)

See `docs/UPDATES.md` for the chosen approach once decided. Goal: download Click **once**, then
every new version arrives by itself.

---

## Hardware shopping notes
- **Box:** Raspberry Pi 5 (2 GB if the computer always does the thinking; 4 GB for the backup
  model), official 27 W power supply or a good power bank, active cooler. Add an NVMe SSD later
  (SD cards wear out).
- **Computer:** 16 GB RAM is ideal and 8 GB works (see section 5). Mac: macOS 14+, Apple Silicon
  recommended. Images need Apple Silicon or an NVIDIA GPU.

## Built in version 1.2
- **Floating liquid bubble** in the top-right. Click it to open or close the chat. It swirls while
  thinking and turns red while using the mouse.
- **Reliable start:** "Starting Click…" with automatic retries instead of an error page. The chat
  part restarts itself if it stops.
- **Reopening works:** opening Click while it's running brings it back. Start-up errors are shown
  in plain words.
- **Smoother UI motion:** messages glide in and the active step shimmers.
- **Bubble ideas for later:** react to voice volume; change colour by mood; snap to screen edges.

## Next version (1.3) - top items
1. **Built-in engine (no Ollama).** Bundle llama.cpp's server in Click.app (Metal on Apple Silicon,
   CPU on Intel). Download the Qwen3-VL model files (GGUF + vision projector) with our own progress
   bar. Switch `inference.py` to its OpenAI-style API (streaming, tools, images), and use an
   embedding model for memory search. Stop the engine after 10 idle minutes. The Pi box keeps
   Ollama. About 1-2 sessions; tool calling and images may need tuning.
2. **Official app.** Apple Developer Program ($99/year): signed and notarized builds, no "Open
   Anyway", permissions survive updates. Then self-updating (download once).
3. **Own brain ("click-brain").** Fine-tune Qwen3-VL on Click's own examples (voice, tool use,
   cursor explanations), roughly $10-100 on a rented GPU. Training from scratch isn't realistic.
4. **Fully native look (later).** Rebuild the chat in SwiftUI once features settle. Today the
   bubble and windows are native and only the chat inside is web technology (like Slack or the
   ChatGPT/Claude desktop apps).
5. **Name check** before going public (e.g. "Clicky" is an existing analytics product).

## MAIN GOAL: portable box (unplug, move to the next computer, plug in, it works)
**Built:**
- Memory, chats, personality and settings live on the Pi (encrypted).
- The Mac app can use a box (menu bar: "Use a Click box", one-time 6-digit pairing).
- Encrypted, paired-only connection.

**To build** (needs a real Pi to test; about 1-2 sessions):
1. **Many computers per box.** Store a list of paired computers (today only one), and connect to
   whichever is plugged in or online.
2. **USB-C plug-in.** Pi 5 USB gadget mode (network over USB, same cable as power). The Pi gets a
   fixed address on the cable; the app finds it automatically (mDNS / fixed IP). Check the laptop
   port supplies enough power (15 W+).
3. **Automatic switching.** Box plugged in: use the box's brain link and memory. Unplugged: local
   mode or "Plug in your box" (decision pending).
4. **Installer on the box.** The box also appears as a small USB drive with Click.dmg/.exe, so a new
   computer installs Click from the box in ~30 seconds.
5. **Leave nothing behind.** On unplug, clear chat caches and session data on that computer.
   Optionally delete the model file too (re-copied from the box next time).
- **Not avoidable:** each computer needs the Click app once (it's what sees the screen and moves
  the cursor). The HID mouse/keyboard alternative would be blind.

### Portable box: the exact plug-in experience we're aiming for
1. Plug the box into any computer (one USB-C cable: power + data).
2. **First time on that computer only:** a drive called "Click" appears; double-click "Install
   Click". This is the only manual step; macOS and Windows block anything from auto-installing
   from USB (anti-virus rule, no way around it).
3. The orb appears at once (top-right) with a progress ring while it sets up in the background.
   The engine and the right-sized brain copy **from the box's storage over the cable** (no
   internet; ~1-2 min for a 2-3 GB brain at USB 2 speeds). Memories, chats, settings and images
   already live on the box, so nothing else needs to move.
4. The computer asks once for Screen Recording + Accessibility (OS rule: each computer approves
   once). Click guides you.
5. Your same Click is ready: same memory, same personality.
- **Next times on that computer:** plug in and the orb appears automatically (Click auto-starts
  when it detects the box).
- **Unplug:** the orb shows "Box unplugged"; personal data is cleared from that computer.

**Box storage (1 TB NVMe SSD on a Pi 5 M.2 HAT, ~$60-90):**
- memories, chats, images
- Click installers for Mac (Intel + Apple Silicon) and Windows
- several brain sizes (the box picks per computer: small for 8 GB laptops, bigger for desktops)
- engine binaries and image models

To build this: the built-in engine (so everything can come from the box), USB gadget mode with a
storage drive plus a network link, an "install from box" bootstrap, a model-serving endpoint on the
Pi, auto-start on box detection, and wipe-on-unplug.

## Built in version 1.5
- **Fixed:** "chat part isn't answering". A leftover setting made Click look for a Raspberry Pi box
  instead of running on the Mac. Now it only uses a box after a real Pi pairing (regression test
  added).
- Opening Click opens the chat. Reopening it brings the chat back.
- A cursor tutorial plays on every start. Click's cursor rests (docked) beside the orb when idle.
- Orb: calm grey "paused" state. It sits a layer above the cursor overlay, so it no longer flickers.
- The header shows the real status and how much memory the AI is using (roadmap item).

## Built in version 1.6
- **Speed (processor-only Macs):**
  - Only the tools a question needs are sent (plain questions send none).
  - Shorter history and memory.
  - No hidden memory-extraction call after each answer.
  - Answers default to 1-3 sentences and are capped.
  - The model stays loaded for 30 minutes and warms up at launch.
  - **Read-ahead while typing:** the conversation is pre-read, so Enter only adds the last words.
- **Pointing that always works:** "show me / where is / point to / find X" is handled directly. Click
  finds X (accessibility, menu bar, or on-screen text), moves its cursor there, then explains.
  It no longer depends on the small model deciding to use a tool.
- **Menu bar items** (Wi-Fi, battery, sound, app menus) can be found and pointed at.
- **First-launch welcome:** Meet Click, the cursor demo, permissions, setup with progress, then
  ready. Later launches are quiet: the orb, the docked cursor and Ask Click.
- **The full chat is dark glass** like the orb, with a live mini-orb in its header.

## Built in version 1.7
- **Pointing finds the real thing.** Find checks the Dock (apps) first, then the menu bar, window
  buttons, then on-screen text. It never matches Click's own windows, which are also kept out of
  Click's screenshots. (Fixes "it pointed at my question in the chat".) An app not in the Dock gets
  the Apple menu (System Settings) or Spotlight, with an explanation.
- **"Open X"** finds X, points at it, and clicks it after your OK.
- After every answer the cursor glides back to the orb and fades away.
- The orb stays calm while thinking (a soft glow, no spinning).
- The full chat is liquid glass: drifting light, a flowing violet/cyan edge, and an orb send button.
