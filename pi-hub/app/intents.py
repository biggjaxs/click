"""Understand common "do this on my computer" requests without relying on a small AI model.

parse_task("open notes and type today's date and time") ->
    {"plan": [{"skill": "open_app", "app": "notes"}, {"skill": "new_note", "text": "Tuesday, November 25, ..."}],
     "summary": "Open Notes and write today's date and time"}

point_target("where is the budget folder on my desktop?") -> "budget"
"""
from __future__ import annotations

import datetime as dt
import re

_LEAD = r"^(?:hey click,?\s+|ok(?:ay)?,?\s+|can you\s+|could you\s+|would you\s+|will you\s+|please\s+|i want you to\s+|i need you to\s+)*"
_APP = r"(?:the\s+)?(?P<app>[\w .'-]+?)(?:\s+app(?:lication)?)?"


def _expand(text: str, now: dt.datetime | None = None) -> str:
    """Replace "today's date", "the time", etc. with the real values."""
    now = now or dt.datetime.now()
    date = now.strftime("%A, %B ") + str(now.day) + now.strftime(", %Y")
    time = now.strftime("%I:%M %p").lstrip("0")
    rules = [
        (r"\b(?:today'?s|the(?: current)?|current)\s+date\s+and\s+(?:the\s+)?time\b", f"{date}, {time}"),
        (r"\b(?:today'?s|the(?: current)?|current)\s+time\s+and\s+(?:the\s+)?date\b", f"{date}, {time}"),
        (r"\bdate\s+and\s+time\b", f"{date}, {time}"),
        (r"\b(?:today'?s|the(?: current)?|current)\s+date\b", date),
        (r"\b(?:the(?: current)?|current)\s+time\b", time),
        (r"\btoday'?s\s+day\b", now.strftime("%A")),
    ]
    out = text
    for pat, rep in rules:
        out = re.sub(pat, rep, out, flags=re.I)
    return out.strip().strip('"“”').strip()


def _clean_song(q: str) -> str:
    q = re.sub(r"^(?:the\s+)?(?:song|track)\s+", "", q.strip(), flags=re.I)
    q = re.sub(r"\s+(?:song|track)$", "", q, flags=re.I)
    q = re.split(r"\s+by\s+", q, maxsplit=1, flags=re.I)[0]  # Music's search matches song names
    return q.strip().strip('"“”.!?').strip()


def parse_task(text: str, now: dt.datetime | None = None) -> dict | None:
    t = re.sub(_LEAD, "", text.strip(), flags=re.I).rstrip(" .!?")
    if not t:
        return None
    # --- Notes ---------------------------------------------------------------------------------
    m = (re.match(r"^(?:open|launch|go to|start)\s+(?:the\s+)?notes?(?:\s+app)?(?:,|\s+and)?\s*(?:then\s+)?"
                  r"(?:type|write|put|add|enter|jot down|note down|make a note(?:\s+(?:saying|that says|with|of))?)"
                  r"(?:\s+(?:in|down))?\s+(?P<x>.+)$", t, re.I)
         or re.match(r"^(?:make|create|write|start|add|take)\s+(?:a\s+)?(?:new\s+)?note(?:\s+(?:saying|that says|with|of|about|:))?\s+(?P<x>.+)$", t, re.I)
         or re.match(r"^(?:write|type|put|jot)\s+(?P<x>.+?)\s+(?:in|into|on)\s+(?:a\s+)?(?:new\s+)?notes?(?:\s+app)?$", t, re.I))
    if m:
        body = _expand(m.group("x"), now)
        return {"plan": [{"skill": "open_app", "app": "Notes"}, {"skill": "new_note", "text": body}],
                "summary": f"Open Notes and write “{body[:50]}”"}
    # --- Music -----------------------------------------------------------------------------------------
    m = (re.match(r"^(?:open|launch|go to|start)\s+(?:the\s+)?(?:apple\s+)?music(?:\s+app)?(?:,|\s+and)?\s*(?:then\s+)?"
                  r"play\s+(?P<q>.+)$", t, re.I)
         or re.match(r"^play\s+(?P<q>.+?)\s+(?:on|in|from|with)\s+(?:apple\s+)?music(?:\s+app)?$", t, re.I)
         or re.match(r"^play\s+(?:the\s+)?(?:song|track)\s+(?P<q>.+)$", t, re.I)
         or re.match(r"^play\s+(?P<q>(?!some\b|music\b|a\b)\S.+)$", t, re.I))
    if m:
        q = _clean_song(m.group("q"))
        if q:
            return {"plan": [{"skill": "open_app", "app": "Music"}, {"skill": "play_music", "query": q}],
                    "summary": f"Open Music and play “{q}”"}
    # --- Open an app, then type into it -----------------------------------------------------------------
    m = re.match(rf"^(?:open|launch|start)\s+{_APP}(?:,|\s+and)\s*(?:then\s+)?(?:type|write|enter)\s+(?P<x>.+)$", t, re.I)
    if m:
        body = _expand(m.group("x"), now)
        return {"plan": [{"skill": "open_app", "app": m.group("app")}, {"skill": "type_text", "text": body}],
                "summary": f"Open {m.group('app').strip().title()} and type “{body[:50]}”"}
    # --- Just open an app ----------------------------------------------------------------------------------------
    m = re.match(rf"^(?:open|launch|start|run)\s+{_APP}(?:\s+for me)?$", t, re.I)
    if m and len(m.group("app").split()) <= 4 and not re.search(r"\b(folder|file|document|tab|page|website|link)\b",
                                                                  m.group("app"), re.I):
        app = m.group("app").strip()
        return {"plan": [{"skill": "open_app", "app": app}], "summary": f"Open {app.title()}", "open_only": True}
    # --- Type into whatever app is in front --------------------------------------------------------------------------
    m = re.match(r"^(?:type|write|enter)\s+(?P<x>.+?)(?:\s+(?:here|for me|in this|in there))?$", t, re.I)
    if m and not re.match(r"^(?:a|an|me)\s", m.group("x"), re.I):
        body = _expand(m.group("x"), now)
        return {"plan": [{"skill": "type_text", "text": body}], "summary": f"Type “{body[:50]}”"}
    return None


# ---- "where is X?" -------------------------------------------------------------------------------------------
_POINT = re.compile(
    _LEAD + r"(?:show me\s+|tell me\s+|point out\s+|help me find\s+)?"
    r"(?:where(?:'s|\s+is|\s+are|\s+can i find|\s+do i find|\s+would i find)?|point(?:\s+(?:at|to))?|find|locate|show me|"
    r"how do i (?:find|get to))\s+(?:the\s+|my\s+|a\s+|an\s+)?(?P<t>.+?)\s*[?.!]*$", re.I)
_TRAIL = [r"\s+(?:is|are|located|at|for me)$",
          r"\s+(?:on|in)\s+(?:my\s+|the\s+)?(?:home\s*screen|desktop|screen|computer|mac|macbook|laptop|dock|menu\s*bar|"
          r"top bar|toolbar|taskbar)$",
          r"\s+(?:button|icon|option|tab|link|shortcut)$"]
SCREENISH = re.compile(r"\b(screen|desktop|dock|menu ?bar|button|icon|folder|file|app|window|settings|on my "
                       r"(?:computer|mac|laptop)|wi-?fi|battery|trash|bluetooth|volume|sound)\b", re.I)


def point_target(text: str) -> str | None:
    m = _POINT.match(text.strip())
    if not m:
        return None
    t = m.group("t").strip(" ?.!'\"")
    changed = True
    while changed and t:
        changed = False
        for pat in _TRAIL:
            new = re.sub(pat, "", t, flags=re.I).strip()
            if new != t and new:
                t, changed = new, True
    t = re.sub(r"\s+(?:folder|file)$", "", t, flags=re.I).strip() or t
    return t if 0 < len(t) <= 60 else None
