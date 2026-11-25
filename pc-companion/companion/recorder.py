"""Teach Clicky once: record what you click, save it as a skill, replay it as a lesson or do it for you.

While recording, Clicky notes each app you switch to and each button/menu you click (by its NAME, so it still
works if windows move). It does NOT record what you type (privacy): where you typed into a box, the replay
asks you to type there. Skills are saved as small files in Click's settings folder and can be exported to
share or imported from someone else.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "skill"


class Library:
    def __init__(self, folder: Path):
        self.folder = Path(folder) / "skills"
        self.folder.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, steps: list[dict], kind: str = "recorded") -> Path:
        p = self.folder / f"{slug(name)}.json"
        p.write_text(json.dumps({"name": name, "kind": kind, "created": time.time(), "steps": steps}, indent=2))
        return p

    def all(self) -> list[dict]:
        out = []
        for p in sorted(self.folder.glob("*.json")):
            try:
                d = json.loads(p.read_text())
                if d.get("name") and isinstance(d.get("steps"), list):
                    out.append(d)
            except (OSError, ValueError):
                continue
        return out

    def find(self, query: str) -> dict | None:
        """Best skill for 'export report' / 'how to export the report'."""
        import difflib

        q = re.sub(r"^(?:how\s+(?:to|do\s+i)\s+|the\s+|my\s+)", "", query.lower().strip(" ?.!")).strip()
        best, score = None, 0.0
        for d in self.all():
            n = d["name"].lower()
            s = 1.0 if n == q else 0.92 if (n in q or q in n) else difflib.SequenceMatcher(None, q, n).ratio()
            if s > score:
                best, score = d, s
        return best if score >= 0.75 else None

    def delete(self, name: str) -> bool:
        d = self.find(name)
        if not d:
            return False
        (self.folder / f"{slug(d['name'])}.json").unlink(missing_ok=True)
        return True

    def export(self, dest: Path) -> Path:
        dest.write_text(json.dumps({"click_skills": 1, "skills": self.all()}, indent=2))
        return dest

    def import_file(self, src: Path) -> int:
        data = json.loads(Path(src).read_text())
        n = 0
        for d in data.get("skills", []):
            steps = [clean_step(s) for s in d.get("steps", []) if isinstance(s, dict)]
            steps = [s for s in steps if s]
            if d.get("name") and steps:
                self.save(str(d["name"])[:60], steps[:30], d.get("kind", "imported"))
                n += 1
        return n


def clean_step(s: dict) -> dict | None:
    """Imported skills are just data: keep only known, harmless fields."""
    out = {k: str(s[k])[:160] for k in ("app", "target", "say", "role") if s.get(k)}
    if s.get("type_here"):
        out["type_here"] = True
    return out if ("app" in out or "target" in out or "say" in out) else None


class Recorder:
    def __init__(self, app):
        self.app = app
        self.name = ""
        self.steps: list[dict] = []
        self.recording = False
        self._stop = threading.Event()

    def start(self, name: str) -> str:
        self.name, self.steps, self.recording = name.strip() or "my skill", [], True
        self._stop = threading.Event()
        self.app.ghost.flags["recording"] = True
        threading.Thread(target=self._loop, name="click-recorder", daemon=True).start()
        return (f"Recording “{self.name}”. Do it the way you normally would; I'll note each app and button. "
                "Say or type “stop recording” when you're done.")

    def stop(self) -> dict:
        self._stop.set()
        self.recording = False
        self.app.ghost.flags["recording"] = False
        steps = simplify(self.steps)
        if not steps:
            return {"name": self.name, "steps": [], "saved": False}
        lib = Library(self.app.settings.dir)
        lib.save(self.name, steps)
        return {"name": self.name, "steps": steps, "saved": True}

    def _loop(self) -> None:
        import Quartz

        plat = self.app.platform
        last_app, was_down = None, False
        while not self._stop.is_set():
            time.sleep(0.05)
            try:
                w = plat.active_window()
                if w and w.app not in ("Click", "Python") and w.app != last_app:
                    last_app = w.app
                    self.steps.append({"app": w.app, "say": f"Open {w.app}."})
                down = Quartz.CGEventSourceButtonState(Quartz.kCGEventSourceStateCombinedSessionState, 0)
                if down and not was_down:
                    loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
                    if not self._on_click_window(loc.x, loc.y):
                        el = plat.element_at(loc.x, loc.y)
                        if el and el.name and not el.is_password:
                            step = {"target": el.name[:60], "role": el.role,
                                    "say": f"Click “{el.name[:60]}”."}
                            if el.role.lower() in ("textfield", "text field", "textarea", "combobox", "searchfield"):
                                step["say"] = f"Click the “{el.name[:40]}” box and type what you need."
                                step["type_here"] = True
                            self.steps.append(step)
                was_down = down
            except Exception:  # noqa: BLE001
                time.sleep(0.3)

    def _on_click_window(self, x: float, y: float) -> bool:
        try:
            from .platforms import Rect

            return any(Rect(r.x, r.y, r.w, r.h).contains(x, y) for r in self.app._own_rects())
        except Exception:  # noqa: BLE001
            return False


def simplify(steps: list[dict]) -> list[dict]:
    """Drop repeats (double clicks, re-clicking the same thing) and a trailing app switch to nothing."""
    out: list[dict] = []
    for s in steps:
        if out and s == out[-1]:
            continue
        if "app" in s and out and "app" in out[-1]:
            out[-1] = s  # switched apps twice in a row: keep the last
            continue
        out.append(s)
    while out and "app" in out[-1]:
        out.pop()
    return out[:30]
