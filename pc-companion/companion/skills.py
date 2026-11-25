"""Skills: real things Click can do on the computer, reliably, with one OK from the user.

A task is a short plan of steps, e.g. "open Notes and type today's date":
    [{"skill": "open_app", "app": "Notes"}, {"skill": "new_note", "text": "Tuesday 25 November 2026, 3:41 PM"}]

The user sees ONE confirmation listing every step; then Click's cursor goes to each place and the
step runs. On macOS the steps use the official app automation (AppleScript), which is far more
reliable than clicking pixels. The first time, macOS asks "Click wants to control Notes/Music":
that's expected, click OK.
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import sys

log = logging.getLogger("click.skills")

APP_ALIASES = {"settings": "System Settings", "system preferences": "System Settings", "music": "Music",
               "apple music": "Music", "notes": "Notes", "safari": "Safari", "mail": "Mail", "messages": "Messages",
               "calendar": "Calendar", "photos": "Photos", "finder": "Finder", "maps": "Maps", "reminders": "Reminders",
               "chrome": "Google Chrome", "google chrome": "Google Chrome", "spotify": "Spotify", "calculator": "Calculator",
               "app store": "App Store", "terminal": "Terminal", "facetime": "FaceTime", "tv": "TV", "podcasts": "Podcasts"}


# System Settings sections, opened directly (macOS 13 and later)
_PANE = "x-apple.systempreferences:"
SETTINGS_URLS = {
    "Wi-Fi": "com.apple.wifi-settings-extension", "Bluetooth": "com.apple.BluetoothSettings",
    "Network": "com.apple.Network-Settings.extension", "Displays": "com.apple.Displays-Settings.extension",
    "Sound": "com.apple.Sound-Settings.extension", "Battery": "com.apple.Battery-Settings.extension",
    "Wallpaper": "com.apple.Wallpaper-Settings.extension",
    "Privacy & Security": "com.apple.settings.PrivacySecurity.extension",
    "Screen Recording": "com.apple.preference.security?Privacy_ScreenCapture",
    "Accessibility": "com.apple.preference.security?Privacy_Accessibility",
    "Notifications": "com.apple.Notifications-Settings.extension",
    "Software Update": "com.apple.Software-Update-Settings.extension",
    "Keyboard": "com.apple.Keyboard-Settings.extension", "Trackpad": "com.apple.Trackpad-Settings.extension",
    "Mouse": "com.apple.Mouse-Settings.extension", "Appearance": "com.apple.Appearance-Settings.extension",
    "Desktop & Dock": "com.apple.Desktop-Settings.extension", "Users & Groups": "com.apple.Users-Groups-Settings.extension",
    "Lock Screen": "com.apple.Lock-Screen-Settings.extension", "Touch ID & Password": "com.apple.Touch-ID-Settings.extension",
    "Storage": "com.apple.settings.Storage", "Date & Time": "com.apple.Date-Time-Settings.extension",
    "Focus": "com.apple.Focus-Settings.extension", "Screen Time": "com.apple.Screen-Time-Settings.extension",
    "Printers & Scanners": "com.apple.Print-Scanner-Settings.extension", "General": "com.apple.systempreferences.GeneralSettings",
    "Control Center": "com.apple.ControlCenter-Settings.extension", "Siri": "com.apple.Siri-Settings.extension",
    "Apple Account": "com.apple.systempreferences.AppleIDSettings",
}


def installed_apps() -> dict[str, str]:
    """Every app on this Mac: lower-case name -> path (Applications folders, System apps, Utilities)."""
    from pathlib import Path

    out: dict[str, str] = {}
    for base in ("/Applications", "/System/Applications", "/System/Applications/Utilities",
                 "/Applications/Utilities", str(Path.home() / "Applications")):
        try:
            for p in Path(base).glob("*.app"):
                out.setdefault(p.stem.lower(), str(p))
        except OSError:
            continue
    return out


def best_app(name: str, apps: dict[str, str]) -> str | None:
    """Fuzzy match like Spotlight/Raycast: 'spotfy' -> Spotify, 'msg' -> Messages, 'chrome' -> Google Chrome."""
    import difflib

    n = name.lower().strip()
    n = n.removesuffix(" app").removesuffix(" application").removeprefix("the ").strip()
    if not n:
        return None
    if n in apps:
        return apps[n]
    starts = [a for a in apps if a.startswith(n)]
    if starts:
        return apps[min(starts, key=len)]
    words = [a for a in apps if n in a.split() or a.endswith(" " + n)]
    if words:
        return apps[min(words, key=len)]

    def subseq(q, a):
        it = iter(a)
        return all(ch in it for ch in q)

    if len(n) >= 3:
        sub = [a for a in apps if a[0] == n[0] and subseq(n, a)]
        if sub:
            return apps[min(sub, key=len)]
    close = difflib.get_close_matches(n, list(apps), n=1, cutoff=0.72)
    return apps[close[0]] if close else None


SKILL_NAMES = ("open_app", "play_music", "new_note", "type_text", "open_settings", "quit_app", "music_control",
               "open_url", "open_file", "append_note", "send_message", "email_draft", "reminder", "volume",
               "dark_mode", "wifi", "screenshot", "keys", "lock_screen", "empty_trash")
HIGH_RISK = {"send_message", "empty_trash"}  # these always ask first, even with "Always allow"


class SkillError(RuntimeError):
    pass


def app_name(name: str) -> str:
    n = name.strip().strip(".").lower()
    return APP_ALIASES.get(n, " ".join(w.capitalize() if w.islower() else w for w in name.strip().split()))


def _as_text(s: str) -> str:
    """Make text safe to put inside an AppleScript string."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _osascript(script: str, timeout: int = 30) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        err = r.stderr.strip()
        if "-1743" in err or "Not authorized" in err:
            raise SkillError("macOS hasn't allowed Click to control that app yet. Open System Settings > "
                             "Privacy & Security > Automation and turn on the app under Click, then try again.")
        raise SkillError(err or "The app didn't respond.")
    return r.stdout.strip()


def describe(step: dict) -> str:
    k = step.get("skill")
    if k == "open_app":
        return f"Open {app_name(step['app'])}"
    if k == "play_music":
        return f"Play “{step['query']}” in Music"
    if k == "new_note":
        t = step.get("title")
        return "Make a note" + (f" titled “{t[:40]}”" if t else "") + (f": “{step['text'][:50]}”" if step.get("text") else "")
    if k == "type_text":
        return f"Type “{step['text'][:60]}”"
    if k == "open_settings":
        return f"Open {step['pane']} in System Settings"
    if k == "quit_app":
        return f"Close {app_name(step['app'])}"
    if k == "music_control":
        return {"pause": "Pause the music", "play": "Play music", "next": "Next song",
                "previous": "Previous song"}.get(step.get("action"), "Control the music")
    if k == "open_url":
        u = step.get("url", "")
        return "Search Google" if "google.com/search" in u else f"Open {u.split('//')[-1].split('/')[0]}"
    if k == "open_file":
        return f"Open the file “{step.get('query', '')}”"
    if k == "append_note":
        return f"Add to your note “{step.get('title', '')}”: “{step.get('text', '')[:40]}”"
    if k == "send_message":
        return f"Send a message to {step.get('to', '?')}: “{step.get('text', '')[:60]}”"
    if k == "email_draft":
        return f"Write an email to {step.get('to') or 'someone'}" + (f" about {step['subject']}" if step.get("subject") else "")
    if k == "reminder":
        return f"Remind you to {step.get('text', '')}" + (f" ({step['when_text']})" if step.get("when_text") else "")
    if k == "volume":
        return {"up": "Turn the volume up", "down": "Turn the volume down", "mute": "Mute", "unmute": "Unmute"}.get(
            step.get("action"), f"Set the volume to {step.get('level')}%")
    if k == "dark_mode":
        return "Turn on dark mode" if step.get("on") else "Turn on light mode"
    if k == "wifi":
        return "Turn Wi-Fi on" if step.get("on") else "Turn Wi-Fi off"
    if k == "screenshot":
        return "Take a screenshot"
    if k == "keys":
        return step.get("what") or f"Press {step.get('keys')}"
    if k == "lock_screen":
        return "Lock the screen"
    if k == "empty_trash":
        return "Empty the Trash (can't be undone)"
    return k or "?"


class Skills:
    def __init__(self, app):
        self.app = app

    async def run(self, payload: dict) -> dict:
        plan = payload.get("plan") or []
        if not plan or len(plan) > 8:
            raise SkillError("Nothing to do.")
        for step in plan:
            if step.get("skill") not in SKILL_NAMES:
                raise SkillError(f"Unknown step {step.get('skill')}")
        summary = payload.get("summary") or " then ".join(describe(s) for s in plan)
        # one confirmation for the whole task, next to the orb (sending things always asks)
        hx, hy = self.app.ghost.home or (400, 300)
        risk = "high" if any(s.get("skill") in HIGH_RISK for s in plan) else "change"
        await self.app.actions._confirm("task", summary, "\n".join(f"{i + 1}. {describe(s)}" for i, s in enumerate(plan)),
                                        hx, hy, {"risk": risk, "task_id": ""})
        done = []
        for step in plan:
            if self.app.actions.killed.is_set():
                raise SkillError("Stopped by the kill switch.")
            result = await getattr(self, step["skill"])(step)
            done.append({"step": describe(step), "result": result})
            await asyncio.sleep(0.4)
        return {"status": "done", "steps": done}

    # ---- guided lessons: "show me how to send an email" ---------------------------------------------------------
    async def guide(self, payload: dict) -> dict:
        """Walk the user through a task: Clicky goes to each button with the step written beside it and waits
        for them to click it. Nothing is clicked FOR them (except opening the app), so it's a real lesson."""
        steps = (payload.get("steps") or [])[:10]
        title = payload.get("title") or "Here's how"
        if not steps:
            raise SkillError("No steps to show.")
        ghost = self.app.ghost
        ghost.hold = True  # stay on screen during the lesson (no auto-hide)
        done = 0
        try:
            self.app.bridge.narrate.emit(title)
            for i, st in enumerate(steps, 1):
                if self.app.actions.killed.is_set():
                    break
                say = f"Step {i} of {len(steps)}: {st.get('say', '')}"
                if st.get("app"):
                    name = app_name(st["app"])
                    await self._point(name, say, fallback_center=False)
                    if sys.platform == "darwin":
                        await asyncio.to_thread(subprocess.run, ["open", "-a", name], capture_output=True)
                    elif sys.platform == "win32":
                        await asyncio.to_thread(subprocess.run, ["cmd", "/c", "start", "", name], capture_output=True)
                    await asyncio.sleep(1.5)
                    await self._point_at_front_window(say, name)
                    await asyncio.sleep(2.2)
                    done += 1
                    continue
                if st.get("url"):
                    opener = ["open", st["url"]] if sys.platform == "darwin" else ["cmd", "/c", "start", "", st["url"]]
                    await asyncio.to_thread(subprocess.run, opener, capture_output=True)
                    await asyncio.sleep(1.5)
                    await self._point_at_front_window(say)
                    await asyncio.sleep(2.2)
                    done += 1
                    continue
                el = None
                if st.get("target"):
                    for _ in range(3):  # the window may still be drawing: look a few times
                        found = await asyncio.to_thread(self.app.screen.find_ui_element, st["target"], None, True)
                        if found.get("matches"):
                            el = found["matches"][0]
                            break
                        await asyncio.sleep(0.8)
                if el:
                    res = await self.app.actions.teach_step({"element_id": el["id"], "instruction": say,
                                                             "timeout": st.get("wait", 60)})
                    if res.get("status") == "stopped":
                        break
                else:  # can't see it: explain beside the front window and give time to do it
                    await self._point_at_front_window(say)
                    await asyncio.sleep(float(st.get("wait_text", 7)))
                done += 1
                ghost.last_active = __import__("time").time()
            self.app.bridge.narrate.emit("That's it! You did it.")
        finally:
            ghost.hold = False
        return {"status": "done", "steps_done": done, "steps": len(steps)}

    # ---- helpers ------------------------------------------------------------------------------------------
    async def _point(self, query: str, label: str, fallback_center: bool = True) -> bool:
        """Clicky glides to the thing (its Dock icon, its window, a menu bar icon...). Returns False if it
        couldn't be found (then Clicky waits by the front window with the label, never in the middle of
        nowhere)."""
        try:
            found = await asyncio.to_thread(self.app.screen.find_ui_element, query, None, False)
            if found.get("matches"):
                await self.app.actions.move({"element_id": found["matches"][0]["id"], "label": label})
                return True
        except Exception:  # noqa: BLE001
            log.exception("finding %s failed", query)
        if fallback_center:
            await self._point_at_front_window(label)
        return False

    async def _point_at_front_window(self, label: str, app: str | None = None) -> None:
        """Point at the title bar of the front window (of `app` if given): where the user is looking."""
        w = None
        for _ in range(6):
            w = await asyncio.to_thread(self.app.platform.active_window)
            if w and (not app or app.lower() in (w.app or "").lower()):
                break
            await asyncio.sleep(0.25)
        if w and w.rect.w > 60:
            x, y = w.rect.x + min(w.rect.w / 2, 220), w.rect.y + 14
        else:
            x, y = self.app.ghost.primary_center()
        self.app.bridge.ghostMove.emit(float(x), float(y), label)
        await asyncio.sleep(0.7)

    # ---- the skills -------------------------------------------------------------------------------------------
    async def open_app(self, step: dict) -> str:
        name = app_name(step["app"])
        pointed = await self._point(name, f"Opening {name}", fallback_center=False)
        if sys.platform == "darwin":
            r = await asyncio.to_thread(subprocess.run, ["open", "-a", name], capture_output=True, text=True)
            if r.returncode != 0:
                path = best_app(step["app"], await asyncio.to_thread(installed_apps))  # a near-miss name
                if not path:
                    f = await asyncio.to_thread(self._find_file, step["app"])
                    if f:  # "open my resume": it's a file, not an app
                        return await self.open_file({"query": step["app"], "path": f})
                    raise SkillError(f"Couldn't find an app called {name} on this Mac.")
                await asyncio.to_thread(subprocess.run, ["open", path], capture_output=True)
                name = path.rsplit("/", 1)[-1].removesuffix(".app")
        elif sys.platform == "win32":
            await asyncio.to_thread(subprocess.run, ["cmd", "/c", "start", "", name], capture_output=True)
        else:
            raise SkillError("Opening apps isn't supported on this computer yet.")
        await asyncio.sleep(1.2)  # give the app a moment to come to the front
        # now point at the app's window, so you can see where it opened
        await self._point_at_front_window(f"{name} is open" if pointed else f"Here's {name}", name)
        return f"opened {name}"

    async def open_settings(self, step: dict) -> str:
        pane = step.get("pane") or ""
        if sys.platform != "darwin":
            raise SkillError("Opening Settings sections is Mac-only for now.")
        await self._point("System Settings", f"Opening {pane} settings")
        url = SETTINGS_URLS.get(pane)
        cmd = ["open", _PANE + url] if url else ["open", "-a", "System Settings"]
        r = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True)
        if r.returncode != 0 and url:
            await asyncio.to_thread(subprocess.run, ["open", "-a", "System Settings"], capture_output=True)
            self.app.bridge.narrate.emit(f"Opened System Settings: pick {pane} in the list on the left")
            return f"opened System Settings (pick {pane} on the left)"
        await asyncio.sleep(1.2)
        self.app.bridge.narrate.emit(f"Here's {pane} in System Settings")
        return f"opened {pane} settings"

    async def quit_app(self, step: dict) -> str:
        name = app_name(step["app"])
        if sys.platform != "darwin":
            raise SkillError("Closing apps is Mac-only for now.")
        await self._point(name, f"Closing {name}")
        r = await asyncio.to_thread(subprocess.run, ["osascript", "-e", f'tell application "{_as_text(name)}" to quit'],
                                    capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            path = best_app(step["app"], await asyncio.to_thread(installed_apps))
            if not path:
                raise SkillError(f"Couldn't find an app called {name}.")
            name = path.rsplit("/", 1)[-1].removesuffix(".app")
            await asyncio.to_thread(subprocess.run, ["osascript", "-e", f'tell application "{_as_text(name)}" to quit'],
                                    capture_output=True, text=True, timeout=20)
        return f"closed {name}"

    async def music_control(self, step: dict) -> str:
        if sys.platform != "darwin":
            raise SkillError("Music controls are Mac-only for now.")
        cmd = {"pause": "pause", "play": "play", "next": "next track", "previous": "previous track"}.get(step.get("action"))
        if not cmd:
            raise SkillError("Unknown music control.")
        await asyncio.to_thread(_osascript, f'tell application "Music" to {cmd}')
        self.app.bridge.narrate.emit(describe(step))
        return describe(step).lower()

    async def open_url(self, step: dict) -> str:
        url = step.get("url", "")
        if not url.startswith(("http://", "https://")):
            raise SkillError("That doesn't look like a web address.")
        w = self.app.ghost.primary_center()
        self.app.bridge.ghostMove.emit(float(w[0]), float(w[1]), describe(step))
        opener = ["open", url] if sys.platform == "darwin" else ["cmd", "/c", "start", "", url]
        await asyncio.to_thread(subprocess.run, opener, capture_output=True)
        await asyncio.sleep(1.0)
        return describe(step).lower()

    # ---- files ------------------------------------------------------------------------------------------------
    def _find_file(self, query: str) -> str | None:
        """Find a file by (part of) its name in your folders, newest first, like Spotlight."""
        import os
        from pathlib import Path

        q = query.strip().strip('"“”').removeprefix("my ").removeprefix("the ").strip()
        q = re.sub(r"\s+(?:file|document|doc|pdf|spreadsheet|presentation|folder)$", "", q, flags=re.I).strip()
        if len(q) < 2:
            return None
        folders = [f for f in (self.app.settings.get("approved_folders") or []) if os.path.isdir(f)]
        icloud = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
        if icloud.is_dir():
            folders.append(str(icloud))
        hits: list[str] = []
        if sys.platform == "darwin":
            for folder in folders:
                r = subprocess.run(["mdfind", "-onlyin", folder, "-name", q], capture_output=True, text=True, timeout=10)
                hits += [h for h in r.stdout.splitlines() if h and "/." not in h and "/Library/" not in h]
        else:
            for folder in folders:
                for root, dirs, files in os.walk(folder):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    hits += [os.path.join(root, f) for f in files + dirs if q.lower() in f.lower()]
                    if len(hits) > 200:
                        break
        if not hits:
            return None
        ql = q.lower()

        def rank(p):
            stem = Path(p).stem.lower()
            exact = 0 if stem == ql else 1 if stem.startswith(ql) else 2
            try:
                age = -os.path.getmtime(p)
            except OSError:
                age = 0
            return (exact, age)

        return sorted(set(hits), key=rank)[0]

    async def open_file(self, step: dict) -> str:
        import os

        q = step.get("query", "")
        path = step.get("path") or (os.path.expanduser(q) if q.startswith("~") else None) \
            or await asyncio.to_thread(self._find_file, q)
        if not path:
            raise SkillError(f"I couldn't find a file called “{step.get('query', '')}” in your Documents, Desktop, "
                             "Downloads or Pictures.")
        from pathlib import Path

        name = Path(path).name
        opener = ["open", path] if sys.platform == "darwin" else ["cmd", "/c", "start", "", path]
        await asyncio.to_thread(subprocess.run, opener, capture_output=True)
        await asyncio.sleep(1.2)
        await self._point_at_front_window(f"Opened {name}")
        return f"opened {path}"

    # ---- notes, messages, email, reminders ---------------------------------------------------------------------
    async def _script(self, script: str, *args: str, timeout: int = 40) -> str:
        """Run AppleScript with values passed as data (never pasted into the script)."""
        r = await asyncio.to_thread(subprocess.run, ["osascript", "-", *args], input=script, capture_output=True,
                                    text=True, timeout=timeout)
        if r.returncode != 0:
            err = r.stderr.strip()
            if "-1743" in err or "Not authorized" in err:
                raise SkillError("macOS hasn't let Click control that app yet. Open System Settings > Privacy & "
                                 "Security > Automation, turn it on under Click, then try again.")
            raise SkillError(err.split("execution error:")[-1].strip()[:200] or "The app didn't respond.")
        return r.stdout.strip()

    async def append_note(self, step: dict) -> str:
        if sys.platform != "darwin":
            raise SkillError("Notes is Mac-only.")
        import html

        title, text = step.get("title", "").strip(), step.get("text", "").strip()
        await self._point("Notes", f"Adding to “{title}”")
        add = "".join(f"<div>{html.escape(p)}</div>" for p in text.split("\n") if p.strip())
        res = await self._script("""
on run argv
    set t to item 1 of argv
    set addition to item 2 of argv
    tell application "Notes"
        activate
        set found to (every note whose name contains t)
        if found is {} then return "NOTFOUND"
        set n to item 1 of found
        set body of n to (body of n) & addition
        show n
        return name of n
    end tell
end run""", title, add)
        if res == "NOTFOUND":
            raise SkillError(f"I couldn't find a note called “{title}”. Say “make a note titled {title}” to start one.")
        self.app.bridge.narrate.emit(f"Added to your note “{res}”")
        return f"added to the note {res}"

    async def _contact(self, who: str, kind: str) -> str:
        """A phone number or email for a name in Contacts (or the value itself if it already is one)."""
        who = who.strip()
        if re.fullmatch(r"[+\d][\d\s().-]{5,}", who) or "@" in who:
            return who
        prop = "phones" if kind == "phone" else "emails"
        res = await self._script(f"""
on run argv
    set q to item 1 of argv
    tell application "Contacts"
        set ppl to (every person whose name contains q)
        if ppl is {{}} then return ""
        set p to item 1 of ppl
        if (count of {prop} of p) is 0 then return ""
        return value of item 1 of {prop} of p
    end tell
end run""", who)
        if not res:
            raise SkillError(f"I couldn't find {who} in your Contacts" + (" with a phone number." if kind == "phone"
                                                                          else " with an email address."))
        return res

    async def send_message(self, step: dict) -> str:
        if sys.platform != "darwin":
            raise SkillError("Messages is Mac-only.")
        to, text = step.get("to", ""), step.get("text", "")
        if not to or not text:
            raise SkillError("Who should I message, and what should it say?")
        await self._point("Messages", f"Messaging {to}")
        handle = await self._contact(to, "phone") if "@" not in to else to
        await self._script("""
on run argv
    set h to item 1 of argv
    set m to item 2 of argv
    tell application "Messages"
        set svc to 1st account whose service type = iMessage
        try
            send m to participant h of svc
        on error
            send m to buddy h of svc
        end try
    end tell
end run""", handle, text)
        self.app.bridge.narrate.emit(f"Sent to {to}")
        return f"sent a message to {to}"

    async def email_draft(self, step: dict) -> str:
        """Opens a ready-to-send email in Mail. YOU press Send (Click never sends email by itself)."""
        if sys.platform != "darwin":
            raise SkillError("Mail is Mac-only for now.")
        to = step.get("to", "")
        addr = await self._contact(to, "email") if to else ""
        await self._point("Mail", "Writing your email")
        await self._script("""
on run argv
    set a to item 1 of argv
    set s to item 2 of argv
    set c to item 3 of argv
    tell application "Mail"
        set m to make new outgoing message with properties {subject:s, content:c, visible:true}
        if a is not "" then
            tell m to make new to recipient at end of to recipients with properties {address:a}
        end if
        activate
    end tell
end run""", addr, step.get("subject", ""), step.get("text", ""))
        await self._point_at_front_window("Check it, then press Send", "Mail")
        return f"wrote an email draft to {to or 'nobody yet'} (not sent)"

    async def reminder(self, step: dict) -> str:
        if sys.platform != "darwin":
            raise SkillError("Reminders is Mac-only for now.")
        import datetime as dt

        when = step.get("when")
        parts = ["", "", "", "", ""]
        if when:
            d = dt.datetime.fromisoformat(when)
            parts = [str(d.year), str(d.month), str(d.day), str(d.hour * 3600 + d.minute * 60), "1"]
        await self._point("Reminders", "Adding your reminder")
        await self._script("""
on run argv
    set t to item 1 of argv
    tell application "Reminders"
        if item 6 of argv is "1" then
            set d to current date
            set day of d to 1
            set year of d to (item 2 of argv as integer)
            set month of d to (item 3 of argv as integer)
            set day of d to (item 4 of argv as integer)
            set time of d to (item 5 of argv as integer)
            make new reminder with properties {name:t, due date:d, remind me date:d}
        else
            make new reminder with properties {name:t}
        end if
    end tell
end run""", step.get("text", ""), *parts)
        self.app.bridge.narrate.emit(describe(step))
        return describe(step).lower()

    # ---- settings you can change directly -------------------------------------------------------------------------
    async def volume(self, step: dict) -> str:
        if sys.platform != "darwin":
            raise SkillError("Volume control is Mac-only for now.")
        a = step.get("action")
        if a == "mute":
            await self._script("set volume output muted true")
        elif a == "unmute":
            await self._script("set volume output muted false")
        else:
            cur = int(await self._script("output volume of (get volume settings)") or 50)
            level = {"up": min(100, cur + 15), "down": max(0, cur - 15)}.get(a, int(step.get("level") or cur))
            await self._script(f"set volume output volume {max(0, min(100, level))} --100%")
            await self._script("set volume output muted false")
        self.app.bridge.narrate.emit(describe(step))
        return describe(step).lower()

    async def dark_mode(self, step: dict) -> str:
        if sys.platform != "darwin":
            raise SkillError("Dark mode is Mac-only here.")
        on = "true" if step.get("on") else "false"
        await self._script(f'tell application "System Events" to tell appearance preferences to set dark mode to {on}')
        self.app.bridge.narrate.emit(describe(step))
        return describe(step).lower()

    async def wifi(self, step: dict) -> str:
        if sys.platform != "darwin":
            raise SkillError("Wi-Fi control is Mac-only here.")
        ports = (await asyncio.to_thread(subprocess.run, ["networksetup", "-listallhardwareports"], capture_output=True,
                                         text=True)).stdout
        m = re.search(r"Hardware Port: (?:Wi-Fi|AirPort)\s*\nDevice: (\w+)", ports)
        dev = m.group(1) if m else "en0"
        r = await asyncio.to_thread(subprocess.run, ["networksetup", "-setairportpower", dev, "on" if step.get("on") else "off"],
                                    capture_output=True, text=True)
        if r.returncode != 0:
            raise SkillError(r.stderr.strip() or "Couldn't change Wi-Fi.")
        self.app.bridge.narrate.emit(describe(step))
        return describe(step).lower()

    async def screenshot(self, step: dict) -> str:
        if sys.platform != "darwin":
            raise SkillError("Screenshots are Mac-only here.")
        import datetime as dt
        from pathlib import Path

        path = Path.home() / "Desktop" / f"Screenshot {dt.datetime.now():%Y-%m-%d at %H.%M.%S}.png"
        await asyncio.to_thread(subprocess.run, ["screencapture", "-x", str(path)])
        self.app.bridge.narrate.emit("Screenshot saved to your Desktop")
        return f"saved a screenshot to {path}"

    async def keys(self, step: dict) -> str:
        """Everyday shortcuts in the app you're using: new tab, undo, copy, paste, save..."""
        if sys.platform != "darwin":
            raise SkillError("Shortcuts are Mac-only here.")
        from . import macinput

        await asyncio.sleep(0.3)
        await asyncio.to_thread(macinput.press, step["keys"])
        self.app.bridge.narrate.emit(describe(step))
        return describe(step).lower()

    async def lock_screen(self, step: dict) -> str:
        if sys.platform != "darwin":
            raise SkillError("Locking is Mac-only here.")
        await asyncio.to_thread(subprocess.run, ["pmset", "displaysleepnow"], capture_output=True)
        return "locked the screen"

    async def empty_trash(self, step: dict) -> str:
        if sys.platform != "darwin":
            raise SkillError("Mac-only.")
        await self._point("Trash", "Emptying the Trash")
        await self._script('tell application "Finder" to empty trash')
        return "emptied the Trash"

    async def play_music(self, step: dict) -> str:
        if sys.platform != "darwin":
            raise SkillError("Playing music is Mac-only for now.")
        q = _as_text(step["query"])
        await self._point("Music", f"Finding “{step['query']}”", fallback_center=True)
        script = f'''
            tell application "Music"
                activate
                set found to (search library playlist 1 for "{q}" only songs)
                if found is {{}} then return "NOTFOUND"
                play item 1 of found
                return (name of item 1 of found) & " by " & (artist of item 1 of found)
            end tell'''
        res = await asyncio.to_thread(_osascript, script)
        if res == "NOTFOUND":
            import urllib.parse

            url = "music://music.apple.com/search?term=" + urllib.parse.quote(step["query"])
            await asyncio.to_thread(subprocess.run, ["open", url])
            self.app.bridge.narrate.emit(f"“{step['query']}” isn't in your library. I opened Apple Music search.")
            return "not in the library; opened Apple Music search for it"
        self.app.bridge.narrate.emit(f"Playing {res}")
        return f"playing {res}"

    async def new_note(self, step: dict) -> str:
        """A new note in Apple Notes with an optional title and text. The words are handed to Notes as
        data (never pasted into the script), so any quotes, symbols or long paragraphs work. If Notes
        won't take it, Clicky makes a new note and pastes the text in instead."""
        if sys.platform != "darwin":
            raise SkillError("Notes is Mac-only.")
        import html

        title = (step.get("title") or "").strip()
        text = (step.get("text") or "").strip()
        await self._point("Notes", "Writing your note", fallback_center=True)
        parts = []
        if title:
            parts.append(f"<div><h1>{html.escape(title)}</h1></div>")
        for para in [p.strip() for p in text.split("\n") if p.strip()]:
            parts.append(f"<div>{html.escape(para)}</div>")
            parts.append("<div><br></div>")
        body = "".join(parts) or "<div><br></div>"
        script = """
on run argv
    set b to item 1 of argv
    tell application "Notes"
        activate
        try
            set n to make new note at folder 1 of default account with properties {body:b}
        on error
            set n to make new note with properties {body:b}
        end try
        show n
        return id of n
    end tell
end run"""
        try:
            r = await asyncio.to_thread(subprocess.run, ["osascript", "-", body], input=script,
                                        capture_output=True, text=True, timeout=40)
            if r.returncode != 0:
                err = r.stderr.strip()
                if "-1743" in err or "Not authorized" in err:
                    raise SkillError("macOS hasn't let Click control Notes yet. Open System Settings > Privacy & "
                                     "Security > Automation, turn on Notes under Click, then try again.")
                raise SkillError(err or "Notes didn't respond.")
        except (SkillError, subprocess.TimeoutExpired) as exc:
            if "Automation" in str(exc):
                raise
            log.warning("Notes scripting failed (%s); making the note by hand", exc)
            await self._note_by_hand(title, text)
        self.app.bridge.narrate.emit("Done: your note is open in Notes")
        return f"made a note{f' titled {title}' if title else ''}" + (f": {text[:80]}" if text else "")

    async def _note_by_hand(self, title: str, text: str) -> None:
        """Fallback: open Notes, press Cmd+N, paste the words (like a person would)."""
        from . import macinput

        await asyncio.to_thread(subprocess.run, ["open", "-a", "Notes"], capture_output=True)
        await asyncio.sleep(1.5)
        await asyncio.to_thread(macinput.press, "cmd+n")
        await asyncio.sleep(0.8)
        words = (title + "\n\n" if title else "") + text
        if words.strip():
            await asyncio.to_thread(macinput.paste_text, words)

    async def type_text(self, step: dict) -> str:
        res = await self.app.actions.type_text({"text": step["text"], "risk": "change", "task_id": "skill-task"},
                                               confirmed=True)
        return res.get("status", "typed")
