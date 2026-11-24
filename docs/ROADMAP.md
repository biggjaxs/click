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
- **Next: never overload.**
  - Pick a model that leaves room for the computer's own work (built: chosen by RAM tier).
  - Unload the model after N minutes idle (setting; the next question then waits 5–20 s).
  - Cap how many processor cores Click may use.
  - Show a "Click is using X GB" indicator.

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
