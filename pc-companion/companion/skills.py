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
import subprocess
import sys

log = logging.getLogger("click.skills")

APP_ALIASES = {"settings": "System Settings", "system preferences": "System Settings", "music": "Music",
               "apple music": "Music", "notes": "Notes", "safari": "Safari", "mail": "Mail", "messages": "Messages",
               "calendar": "Calendar", "photos": "Photos", "finder": "Finder", "maps": "Maps", "reminders": "Reminders",
               "chrome": "Google Chrome", "google chrome": "Google Chrome", "spotify": "Spotify", "calculator": "Calculator",
               "app store": "App Store", "terminal": "Terminal", "facetime": "FaceTime", "tv": "TV", "podcasts": "Podcasts"}


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
        return f"Make a note: “{step['text'][:60]}”"
    if k == "type_text":
        return f"Type “{step['text'][:60]}”"
    return k or "?"


class Skills:
    def __init__(self, app):
        self.app = app

    async def run(self, payload: dict) -> dict:
        plan = payload.get("plan") or []
        if not plan or len(plan) > 6:
            raise SkillError("Nothing to do.")
        for step in plan:
            if step.get("skill") not in ("open_app", "play_music", "new_note", "type_text"):
                raise SkillError(f"Unknown step {step.get('skill')}")
        summary = payload.get("summary") or " then ".join(describe(s) for s in plan)
        # one confirmation for the whole task, next to the orb
        hx, hy = self.app.ghost.home or (400, 300)
        await self.app.actions._confirm("task", summary, "\n".join(f"{i + 1}. {describe(s)}" for i, s in enumerate(plan)),
                                        hx, hy, {"risk": "change", "task_id": ""})
        done = []
        for step in plan:
            if self.app.actions.killed.is_set():
                raise SkillError("Stopped by the kill switch.")
            result = await getattr(self, step["skill"])(step)
            done.append({"step": describe(step), "result": result})
            await asyncio.sleep(0.4)
        return {"status": "done", "steps": done}

    # ---- helpers ------------------------------------------------------------------------------------------
    async def _point(self, query: str, label: str, fallback_center: bool = True) -> None:
        """Clicky glides to the thing (its Dock icon, window...). If it can't be found, Clicky still
        appears in the middle of the screen with the label, so you always see what it's doing."""
        try:
            found = await asyncio.to_thread(self.app.screen.find_ui_element, query)
            if found.get("matches"):
                await self.app.actions.move({"element_id": found["matches"][0]["id"], "label": label})
                return
        except Exception:  # noqa: BLE001
            log.exception("finding %s failed", query)
        w = self.app.platform.active_window()
        x, y = (w.rect.center if w else self.app.ghost.primary_center())
        self.app.bridge.ghostMove.emit(float(x), float(y), label)
        await asyncio.sleep(0.8)

    # ---- the skills -------------------------------------------------------------------------------------------
    async def open_app(self, step: dict) -> str:
        name = app_name(step["app"])
        await self._point(name, f"Opening {name}")
        if sys.platform == "darwin":
            r = await asyncio.to_thread(subprocess.run, ["open", "-a", name], capture_output=True, text=True)
            if r.returncode != 0:
                raise SkillError(f"Couldn't find an app called {name} on this Mac.")
        elif sys.platform == "win32":
            await asyncio.to_thread(subprocess.run, ["cmd", "/c", "start", "", name], capture_output=True)
        else:
            raise SkillError("Opening apps isn't supported on this computer yet.")
        await asyncio.sleep(1.5)  # give the app a moment to come to the front
        return f"opened {name}"

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
        if sys.platform != "darwin":
            raise SkillError("Notes is Mac-only.")
        text = step["text"]
        await self._point("Notes", "Writing your note", fallback_center=True)
        body = _as_text(text).replace("\n", "<br>")
        script = f'''
            tell application "Notes"
                activate
                set n to make new note with properties {{body:"{body}"}}
                show n
                return name of n
            end tell'''
        try:
            await asyncio.to_thread(_osascript, script)
        except SkillError as exc:
            if "Automation" in str(exc):
                raise
            # some Notes setups need an explicit folder
            alt = f'''
                tell application "Notes"
                    activate
                    set n to make new note at folder 1 of default account with properties {{body:"{body}"}}
                    show n
                end tell'''
            await asyncio.to_thread(_osascript, alt)
        self.app.bridge.narrate.emit("Done: your note is open in Notes")
        return f"made a note: {text[:80]}"

    async def type_text(self, step: dict) -> str:
        res = await self.app.actions.type_text({"text": step["text"], "risk": "change", "task_id": "skill-task"},
                                               confirmed=True)
        return res.get("status", "typed")
