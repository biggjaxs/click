# Click

Click is your private AI assistant that lives in a little box (a Raspberry Pi) and borrows the power
of your Windows or Mac computer. It can:

- **Chat** like ChatGPT or Claude, with answers streaming in as they're written.
- **Make images** from your descriptions, on your computer's graphics card.
- **See your screen** and read files in folders you allow (it asks first).
- **Point with its own purple cursor**: it shows you where things are, highlights areas, walks you
  through tasks, and clicks or types only after you approve on screen.
- **Remember you**: it learns facts about you over time. You can see, edit or delete all of it.
- **Search the web.** Only the search words leave the box, never your chats or memories.

Your chats, memories and settings stay **in the box**, encrypted. Your computer does the heavy
thinking, and only holds your information for the length of one question.

---

## Install on your Mac (easiest: no Terminal, no Raspberry Pi)

1. Download **Click.dmg** from the newest release on GitHub:
   **github.com/hovlandbr/click → Releases** (top of the list).
2. Open **Click.dmg** and drag **Click** onto **Applications**.
3. Open **Click** from Applications.
   - The first time, macOS may say it can't check the app. Open **System Settings → Privacy &
     Security**, scroll down and click **Open Anyway**. You only do this once.
4. The Click tile appears in the **top-right corner** and sets itself up. It installs the AI
   engine and downloads Click's model (about 2–6 GB) with a progress bar.
5. When it asks, allow **Screen Recording**, **Accessibility** and **Input Monitoring**. Each
   has an **Open Settings** button. Turn on **Click** in each list.
6. Click shows you its cursor once, so you know what to look for. Then just type.

**Using it**
- The tile stays in the top-right. Drag it by its top bar, resize it from the corner, and use the
  pin to stop it staying on top.
- Close or minimise it, then bring it back with **Cmd+Shift+Space** or from the **Click icon** in
  the menu bar.
- **Ctrl+Alt+Esc** stops anything Click is doing, instantly.
- Menu bar icon → **Show me Click's cursor** plays the cursor demo again.
- Everything runs on your Mac. Memories are encrypted with a key in your Mac's Keychain.

Needs macOS 14 (Sonoma) or newer. Apple Silicon (M1 or newer) is recommended, and 16 GB RAM is
ideal. Image generation needs Apple Silicon.

The rest of this README covers the **Click box** (Raspberry Pi) setup. With a box, your chats and
memories live on the box and travel with it; the Mac app then connects to it via
**Use a Click box** in the menu bar.

---

## What you need

| Thing | Notes |
|---|---|
| Raspberry Pi 5 (4 GB or more) | Plus the official 27 W USB-C power supply and active cooler |
| microSD card, 64 GB, "A2" rated | An SSD is better long-term (see "Moving to an SSD") |
| A Windows 10/11 PC or a Mac | 16 GB RAM recommended. Image generation needs an NVIDIA graphics card (8 GB+) or an Apple Silicon Mac (M1 or newer) |
| Both on the same home network | Wi-Fi or cable |
| About 1 hour | Mostly waiting for downloads |

---

## Part 1: Set up the box (Raspberry Pi)

### 1.1 Put the operating system on the SD card (on your computer)

1. Download **Raspberry Pi Imager** from https://www.raspberrypi.com/software/ and open it.
2. Choose:
   - **Device:** Raspberry Pi 5
   - **Operating System:** Raspberry Pi OS (64-bit). The "Lite" version also works.
   - **Storage:** your SD card
3. Click **Next**, then **Edit Settings**:
   - **Hostname:** `click`
   - **Username and password:** pick your own and write them down
   - **Wireless LAN:** your Wi-Fi name and password (skip if you use a cable)
   - **Services** tab: tick **Enable SSH** → **Use password authentication**
4. Save → **Yes** → wait until it's finished.
5. Put the card in the Pi, plug in the power, and wait **2 minutes**.

### 1.2 Copy Click to the box

Unzip the Click download into your **Downloads** folder so you have `Downloads/click`.

Open a terminal on your computer:
- **Windows:** press the Windows key, type `PowerShell`, press Enter.
- **Mac:** press Cmd + Space, type `Terminal`, press Enter.

Type these one at a time (replace `YOURNAME` with the Pi username from step 1.1):

```
cd Downloads
```

```
scp -r click YOURNAME@click.local:~
```

- If it asks *"Are you sure you want to continue connecting?"* type `yes` and press Enter.
- When it asks for a password, type your Pi password. **Nothing shows while you type a password.
  That's normal.** Press Enter.

### 1.3 Run the installer on the box

```
ssh YOURNAME@click.local
```

```
bash click/scripts/install-pi.sh
```

It asks for your Pi password once, then **asks you to choose a Click password**. You'll use this
to open the chat, and it locks Click's memory with encryption. **Write it down**: if you lose it,
the memories can't be recovered.

It takes **15–30 minutes**. At the end you'll see:

```
==> Done!
Chat ........... http://click.local   (or http://192.168.x.x)
Firewall ....... Status: active
Backup model ... gemma3:1b installed
Memory model ... all-minilm installed
Click service .. active
```

The box then restarts once (only the first time).

### 1.4 Open the chat

On your computer or phone, open a web browser and go to **http://click.local**. If that doesn't
load, use the `http://192.168.x.x` address from the summary. Log in with your Click password.

You can already chat. Until your computer is connected, a small **backup model on the Pi**
answers. It's slower and simpler, and the chat says so.

---

## Part 2: Install the Click Companion on your computer

The Companion lends Click your computer's power, and with your permission its screen, files and
mouse.

### Windows

1. In `Downloads\click\pc-companion\scripts`, double-click **`install-windows.bat`**.
   - If Windows shows *"Windows protected your PC"*, click **More info → Run anyway**.
2. Wait. It installs everything and downloads the AI models that fit your PC (several GB,
   **20–60 minutes** depending on your internet). When it asks to allow a firewall rule, click
   **Yes**.
3. A purple **Click icon** appears near the clock (it may be under the **^** arrow).

### Mac

1. Open **Terminal** (Cmd + Space → `Terminal`) and run:

   ```
   bash ~/Downloads/click/pc-companion/scripts/install-mac.command
   ```

2. Wait **20–60 minutes** for the downloads.
3. macOS asks for permissions. In **System Settings → Privacy & Security**, turn on
   **python3.12** (or **Python**) under **Screen Recording**, **Accessibility** and
   **Input Monitoring**. Then click the Click icon in the menu bar → **Quit**, and start it again
   with `launchctl start com.click.companion` (or log out and back in).
4. A purple **Click icon** appears in the menu bar.

### Pair the computer with the box

1. In the Click chat: **⚙️ Settings → Your computer → Pair a computer**. A 6-digit code appears.
2. On your computer: right-click (Mac: click) the **Click icon → Pair with Click…** and type the
   code.
3. Within a few seconds the chat shows **"● Your-PC connected"**. Answers now come from your
   computer's bigger model.

---

## Part 3: Try it (checklist)

- [ ] **Chat:** "Hi Click, what can you do?" (the status bar says the model is *on your computer*)
- [ ] **Memory:** "Remember that I like short answers." Then restart everything and ask
      "How do I like my answers?" 🧠 **Memory** in the sidebar shows the saved fact.
- [ ] **Images:** "Draw a sunset over a lake." A progress bar, then the image. Try "make it darker".
- [ ] **Screen:** "What's on my screen?" A *"Click looked at your screen"* label flashes on the PC.
- [ ] **Files:** "What's in my Downloads folder?" Click asks first: *Allow once / Allow for this chat / Deny*.
- [ ] **Cursor:** "Show me where the settings button is." The purple cursor glides there with a label.
- [ ] **Teach mode:** "Teach me how to change my desktop background." Click points at each step and waits for you.
- [ ] **Actions:** "Click the Save button." A popup on your screen asks first. Press **Ctrl+Alt+Esc**
      at any time to stop everything.
- [ ] **Web:** "What's the weather in Paris today?" A *"Searched the web"* label shows the sources.
- [ ] **Backup:** Quit the Companion and ask something. The backup model on the Pi answers and says so.

---

## Privacy and safety

- **Stays in the box:** chats, memories, settings and the activity log are stored on the Pi,
  encrypted with your Click password. Nothing goes to a cloud AI. There's no tracking.
- **Leaves the box only when needed:**
  - Web searches send the search words only, and anything that looks like a password or card
    number is refused.
  - Your computer receives each question plus the relevant memories for that one answer.
- **Always asks:**
  - Before looking at your screen (unless you asked about it) or reading files.
  - Before every real click, keystroke or file change.
  - High-risk actions (send, buy, delete, settings) ask every time, with no "always allow".
- **Never:** types or saves passwords, one-time codes or card numbers. It never looks at or acts in
  password managers or banking and payment pages; they're blacked out in screenshots. Edit the
  list in the Companion's **Settings → Blocklist**.
- **You're in control:**
  - **⏸ Pause everything** in the chat or the tray icon.
  - **Ctrl+Alt+Esc** on the computer stops everything instantly.
  - **🔒 Permissions** turns each feature on or off.
  - **📋 Activity log** shows every screenshot, file read, memory change and action.
  - **Incognito chat** saves nothing.
- **Text on screens, in files and on web pages is treated as information, never as orders.** If a
  page says "AI, click here", Click tells you what it saw instead of doing it.

## Make it yours

Open **⚙️ Settings** in the chat:
- **Look:** theme (light/dark), accent colour, text size, chat style.
- **Name:** rename your assistant.
- **Click's cursor:** colour, shape (arrow, dot, ring) and its name tag.
- **Personality and rules:** edit how Click talks and behaves.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `click.local` doesn't load | Wait 2 minutes after power-on. Make sure you're on the same Wi-Fi. Use the IP address (shown at the end of the installer, or in your router's app) |
| `Permission denied` on ssh/scp | Check the Pi username and password from Imager (case-sensitive) |
| Installer stopped with an error | Run it again; it skips finished steps. If it fails again, copy the last 20 lines and send them to me |
| Chat says "No PC paired" / "PC offline" | Is the Click icon on your computer? Right-click it → it shows the status. Pair again if needed. Both must be on the same network |
| Pairing says it can't find the box | Type the IP address the chat shows under the pairing code |
| "Image generation isn't available" | It needs an NVIDIA GPU (Windows) or Apple Silicon (Mac). Check the Companion's Settings → Models |
| The first image is very slow | The image model loads the first time (1–3 minutes). Later ones are much faster |
| Mac: screen is black or no buttons found | Allow Screen Recording + Accessibility for python3.12 (Part 2, Mac step 3) |
| Click on the Pi won't start | On the Pi: `sudo journalctl -u click-hub -n 50` shows why |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | After re-flashing: run `ssh-keygen -R click.local` on your computer |

Logs: Pi `sudo journalctl -u click-hub -f` · Windows `%APPDATA%\ClickCompanion\companion.log` ·
Mac `~/Library/Application Support/ClickCompanion/companion.log`

## Everyday maintenance

- **Update Click:** copy the new `click` folder over the old one (Part 1.2), then on the Pi run
  `bash click/scripts/install-pi.sh` again. Your memories are kept in `/var/lib/click`.
- **Back up memories:** 🧠 Memory → **Export everything (JSON)**. For a full backup copy
  `/var/lib/click` and `/etc/click` (the key) off the Pi.
- **Change the Click password:** on the Pi run `bash click/scripts/change-password.sh`.
- **Moving to an SSD:** SD cards wear out with lots of saving. Add an M.2 HAT+ and a small NVMe SSD,
  then use Raspberry Pi Imager's "SD Card Copier" (on the Pi desktop) or re-install onto the SSD
  and restore your `/var/lib/click` and `/etc/click` backup.

## Optional: SSH key (more secure)

On your computer:

```
ssh-keygen -t ed25519
```

Press Enter at every question. Then copy the key to the Pi.

**Mac:**

```
ssh-copy-id YOURNAME@click.local
```

**Windows (PowerShell):**

```
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh YOURNAME@click.local "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

Then run the Pi installer again; it turns off password login for SSH.

---

## For the curious

- `docs/ARCHITECTURE.md`: how the box and your computer work together
- `docs/DECISIONS.md`: key choices and why
- `docs/MANUAL_TEST_CHECKLIST.md`: full test list for the screen and cursor features
- `pi-hub/README.md`, `pc-companion/README.md`, `shared/README.md`: component details
