"""Guided lessons: "show me how to send an email" -> Clicky walks you through it, step by step.

Each step is one of:
    {"app": "Mail", "say": "..."}                 open an app (the only thing Clicky does for you)
    {"url": "x-apple.systempreferences:...", ...} open a settings page
    {"target": "New Message", "say": "..."}       point at a button and wait for YOU to click it
    {"say": "...", "wait_text": 8}                 an instruction with nothing to point at
Common tasks have hand-written lessons (reliable); anything else gets a lesson written by the AI.
"""
from __future__ import annotations

import re

BUILT_IN: list[tuple[str, str, list[dict]]] = [
    (r"(?:send|write|compose)\s+(?:an?\s+)?e-?mail|use\s+(?:apple\s+)?mail", "How to send an email", [
        {"app": "Mail", "say": "First, open Mail."},
        {"target": "New Message", "say": "Click here to start a new email."},
        {"target": "To:", "say": "Type the email address of who it's for."},
        {"target": "Subject:", "say": "Give it a short subject."},
        {"say": "Write your message in the big space underneath.", "wait_text": 10},
        {"target": "Send", "say": "When you're happy with it, click Send."},
    ]),
    (r"(?:send|write)\s+(?:a\s+)?(?:text|message|imessage)|use\s+messages", "How to send a text", [
        {"app": "Messages", "say": "Open Messages."},
        {"target": "Compose", "say": "Click here to start a new message."},
        {"target": "To:", "say": "Type the person's name or number."},
        {"target": "iMessage", "say": "Type your message here, then press Return to send it."},
    ]),
    (r"(?:make|create|write|start|take)\s+(?:a\s+)?(?:new\s+)?note|use\s+notes", "How to make a note", [
        {"app": "Notes", "say": "Open Notes."},
        {"target": "New Note", "say": "Click here to start a new note."},
        {"say": "Type! The first line becomes the note's title. Notes saves automatically.", "wait_text": 9},
    ]),
    (r"(?:take|make|capture)\s+(?:a\s+)?screen\s?shot", "How to take a screenshot", [
        {"say": "Press Shift + Command + 3 together to capture the whole screen.", "wait_text": 7},
        {"say": "Or press Shift + Command + 4 and drag a box to capture part of it.", "wait_text": 8},
        {"say": "Your screenshot appears on your Desktop.", "wait_text": 5},
    ]),
    (r"connect(?:ing)?\s+to\s+(?:the\s+)?(?:wi-?fi|internet|network)|join(?:ing)?\s+(?:a\s+)?(?:wi-?fi|network)", "How to connect to Wi-Fi", [
        {"target": "Wi-Fi", "say": "Click the Wi-Fi icon in the top-right of your screen."},
        {"say": "Pick your network from the list, then type its password if it asks.", "wait_text": 10},
    ]),
    (r"(?:change|set)\s+(?:my\s+|the\s+)?(?:wallpaper|background|desktop picture)", "How to change your wallpaper", [
        {"url": "x-apple.systempreferences:com.apple.Wallpaper-Settings.extension", "say": "This is Wallpaper in System Settings."},
        {"say": "Click any picture you like. It changes right away.", "wait_text": 10},
    ]),
    (r"empty\s+(?:the\s+)?(?:trash|bin)", "How to empty the Trash", [
        {"target": "Trash", "say": "Right-click (or Control-click) the Trash in your Dock."},
        {"say": "Choose Empty Trash, then confirm.", "wait_text": 8},
    ]),
    (r"(?:bookmark|save)\s+(?:a\s+|this\s+)?(?:web\s*)?(?:page|site|website)", "How to bookmark a page", [
        {"app": "Safari", "say": "Open Safari and go to the page you want to keep."},
        {"target": "Bookmarks", "say": "Click Bookmarks in the menu bar."},
        {"target": "Add Bookmark", "say": "Choose Add Bookmark, then click Add."},
    ]),
    (r"(?:close|quit|force\s+quit)\s+(?:an?\s+)?(?:app|program)", "How to close an app", [
        {"say": "Click on the app you want to close so it's in front.", "wait_text": 6},
        {"say": "Press Command + Q to quit it. If it's frozen, press Option + Command + Esc.", "wait_text": 8},
    ]),
    (r"(?:make|create)\s+(?:a\s+)?(?:new\s+)?folder", "How to make a folder", [
        {"app": "Finder", "say": "Open Finder."},
        {"target": "File", "say": "Click File in the menu bar."},
        {"target": "New Folder", "say": "Choose New Folder, then type its name."},
    ]),
    (r"(?:add|set|make)\s+(?:an?\s+)?(?:event|appointment|calendar)", "How to add a calendar event", [
        {"app": "Calendar", "say": "Open Calendar."},
        {"say": "Double-click the day and time of your event.", "wait_text": 9},
        {"say": "Type a name for it, then press Return.", "wait_text": 7},
    ]),
]

_ASK = re.compile(r"^(?:can you\s+|could you\s+|please\s+)?(?:show\s+me\s+how\s+(?:to|do\s+i|i\s+(?:can|would))\s+|"
                  r"teach\s+me\s+(?:how\s+(?:to|do\s+i)\s+)?|walk\s+me\s+through\s+(?:how\s+to\s+)?|"
                  r"show\s+me\s+the\s+steps\s+to\s+)(?P<task>.+?)[?.!]*$|"
                  r"^how\s+(?:do|can|would|should)\s+i\s+(?P<task2>.+?)[?.!]*$", re.I)
_COMPUTER = re.compile(r"\b(mac|computer|laptop|app|apps|email|e-mail|mail|note|notes|text|message|imessage|wi-?fi|"
                       r"settings?|screenshot|folder|file|files|trash|bookmark|safari|chrome|finder|calendar|wallpaper|"
                       r"download|install|password|printer|print|bluetooth|airdrop|dock|desktop|zoom|facetime|music|"
                       r"photos?|keyboard|shortcut|window|tab|icon|menu|update|battery|volume|brightness|"
                       r"blender|photoshop|lightroom|illustrator|premiere|after effects|final cut|imovie|garageband|"
                       r"logic|davinci|resolve|obs|excel|word|powerpoint|outlook|keynote|pages|numbers|xcode|"
                       r"vs ?code|visual studio|figma|canva|slack|discord|teams|spotify|steam|minecraft|firefox|"
                       r"gmail|google (?:docs|sheets|slides|drive)|notion|terminal|preview|quicktime|textedit)\b",
                       re.I)


def find(text: str) -> tuple[str, list[dict]] | None:
    """A lesson for 'show me how to X' / 'how do I X' about the computer, or None."""
    m = _ASK.match(text.strip())
    if not m:
        return None
    task = (m.group("task") or m.group("task2") or "").strip()
    explicit = bool(m.group("task"))
    for pat, title, steps in BUILT_IN:
        if re.search(pat, task, re.I):
            return title, [dict(s) for s in steps]
    if explicit or _COMPUTER.search(task):
        title = f"Here's how: {task}" if re.match(r"^\w+ing\b", task) else f"How to {task}"
        return title, []  # empty: the AI writes this lesson
    return None


LESSON_SCHEMA = {"type": "object", "properties": {"steps": {"type": "array", "items": {"type": "object", "properties": {
    "app": {"type": "string"}, "target": {"type": "string"}, "say": {"type": "string"}}, "required": ["say"]}}},
    "required": ["steps"]}

LESSON_PROMPT = """You teach someone to do a task on their Mac, one small step at a time (3 to 10 steps).
If reference material from the web is given, follow it closely (it's more accurate than memory) but rewrite
it for a beginner. Use the exact names of menus, buttons and panels as they appear on screen.
Each step: "say" = one short friendly instruction. Add "app" ONLY on the first step if an app must be opened
(its exact Mac name, e.g. "Mail"). Add "target" = the exact visible label of the button or menu to click
(e.g. "New Message", "File", "Send"), or leave it out if there's nothing to click.
Example for "how to add a contact": {"steps":[{"app":"Contacts","say":"Open Contacts."},
{"target":"+","say":"Click the plus button, then New Contact."},{"say":"Type their name and number.","target":""},
{"target":"Done","say":"Click Done to save."}]}
Answer with JSON only."""


def clean_lesson(data: dict) -> list[dict]:
    out = []
    for i, s in enumerate((data or {}).get("steps") or []):
        say = str(s.get("say") or "").strip()
        if not say:
            continue
        step = {"say": say[:160]}
        if s.get("app") and i == 0:
            step["app"] = str(s["app"])[:40]
        if s.get("target"):
            step["target"] = str(s["target"])[:40]
        out.append(step)
    return out[:10]


def search_query(title: str) -> str:
    """'How to add a cube in blender' -> 'how to add a cube in blender step by step mac'"""
    t = re.sub(r"^(?:how to|here's how:)\s*", "", title, flags=re.I)
    return f"how to {t} step by step mac"


def as_text(title: str, steps: list[dict]) -> str:
    lines = [f"**{title}**"] + [f"{i}. {s['say']}" for i, s in enumerate(steps, 1)]
    return "\n".join(lines)
