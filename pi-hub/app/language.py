"""Clicky's language understanding: turn everyday English into a short plan of steps.

How it works (the same idea big assistants use, done locally and instantly):
1. Rewrite: drop polite wrappers ("can you…", "could you please…", "for me", "have Clicky…").
2. Resolve references from the conversation so far: "it", "that", "there", "again", "the same song".
3. Split one request into single steps: "open Notes, make a new note and write X" -> 3 clauses.
4. Read each clause (open / close / write / type / play / pause / search / settings …) and merge them
   into a plan the Mac side can run. Writing that needs the AI ("a paragraph on why global warming is
   bad") becomes a "compose" step the chat part fills in before running.
Anything these rules don't understand can go to the AI planner (llm_plan_prompt), whose answer is
checked against the same list of allowed steps.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from .intents import _clean_song, _expand, settings_pane, strip_clicky

ALLOWED = {"open_app", "quit_app", "open_settings", "play_music", "music_control", "new_note", "type_text",
           "open_url"}


@dataclass
class DialogState:
    """What the conversation is about right now, so "it", "that" and "again" make sense."""
    last_app: str = ""
    last_song: str = ""
    last_text: str = ""
    last_plan: list = field(default_factory=list)
    last_summary: str = ""


# ---- 1. rewrite -----------------------------------------------------------------------------------------------
_POLITE_LEAD = re.compile(
    r"^(?:(?:hey|hi|ok|okay|yo|so|um+|uh+|alright|right|now|and|also|then)\b[,!\s]*|"
    r"(?:can|could|would|will|won't|wont)\s+you\s+(?:please\s+)?(?:go ahead and\s+|just\s+)?|"
    r"(?:please|pls|plz|kindly|just|quickly|go ahead and|let's|lets)\s+|"
    r"i(?:'d| would)? (?:like|love|want|need) (?:you )?to\s+|i want you to\s+|i need you to\s+|"
    r"(?:help me|try to|try and|be able to|i'd like to|i wanna|i want to|i need to)\s+|"
    r"(?:click|clicky)[,!:\s]+)+", re.I)
_POLITE_TAIL = re.compile(r"(?:[,\s]+(?:for me|please|pls|thanks|thank you|right now|now|real quick|quickly|"
                          r"if you can|if you could|on my (?:mac|computer|laptop|screen)|okay|ok))+[\s?.!]*$", re.I)


def rewrite(text: str) -> str:
    t = strip_clicky(text.strip())
    t = re.sub(r"\s+", " ", t)
    for _ in range(3):
        t = _POLITE_LEAD.sub("", t).strip()
        t = _POLITE_TAIL.sub("", t).strip()
    return t.strip(" ,?.!")


# ---- 2. references ("it", "that", "again") ---------------------------------------------------------------------
_AGAIN = re.compile(r"^(?:do (?:it|that|this) again|again|repeat(?: that| it)?|one more time|same again|"
                    r"do the same(?: thing)?(?: again)?)$", re.I)


def resolve(t: str, st: DialogState) -> str:
    if st.last_app:
        t = re.sub(r"\b(open|close|quit|exit|launch|reopen|switch to|go (?:back )?to|bring up|show)\s+"
                   r"(?:it|that|that app|the app|this app|that one)\b", rf"\1 {st.last_app}", t, flags=re.I)
        t = re.sub(r"\b(in|into|on)\s+(?:it|there|that|that app|the app)\b", rf"\1 {st.last_app}", t, flags=re.I)
    if st.last_song:
        t = re.sub(r"\bplay\s+(?:it|that|that song|the same song|the song)(?:\s+again)?\b", f"play {st.last_song}",
                   t, flags=re.I)
    return t


# ---- 3. split into clauses -------------------------------------------------------------------------------------------
VERBS = (r"open|launch|start|run|close|quit|exit|play|pause|resume|stop|skip|next|previous|write|type|put|add|"
         r"enter|jot|make|create|new|search|google|look up|go to|take me to|show|bring up|turn|change|set|"
         r"switch to|visit|find")
_SPLIT = re.compile(rf"\s*(?:,\s*(?:and\s+)?(?:then\s+)?|;\s*|\.\s+|\s+and\s+then\s+|\s+then\s+|\s+and\s+(?:also\s+)?"
                    rf"|\s+after that\s+|\s+afterwards\s+)(?=(?:{VERBS})\b)", re.I)
_CONTENT_VERB = re.compile(r"^(?:write|type|put|add|enter|jot)\b", re.I)


def split(t: str) -> list[str]:
    """'open notes, make a new note and write a poem about cats and dogs' ->
    ['open notes', 'make a new note', 'write a poem about cats and dogs']. Once a clause is writing
    something, the rest belongs to it unless a clear 'then' starts a new step."""
    parts, rest = [], t
    while rest:
        if _CONTENT_VERB.match(rest):
            m = re.search(rf"\s*(?:,\s*then\s+|\s+and\s+then\s+|\s+then\s+|;\s*)(?=(?:{VERBS})\b)", rest, re.I)
        else:
            m = _SPLIT.search(rest)
        if not m:
            parts.append(rest)
            break
        parts.append(rest[:m.start()])
        rest = rest[m.end():]
    return [p.strip(" ,.") for p in parts if p.strip(" ,.")]


# ---- 4. read each clause --------------------------------------------------------------------------------------------
_KINDS = (r"paragraphs?|poem|haiku|story|short story|essay|summary|list|to-?do list|shopping list|email|letter|"
          r"note|joke|sentences?|outline|speech|message|tweet|post|description|explanation|reminder|plan|recipe|"
          r"bullet points?|few lines|couple (?:of )?(?:lines|sentences)|song|rap|limerick")
_COMPOSE = re.compile(rf"^(?:a|an|one|two|three|four|five|some|a few|a short|a long|a quick|short|long|\d+)?\s*"
                      rf"(?:[\w-]+\s+){{0,2}}(?:{_KINDS})\b.*", re.I)
_TOPIC_ONLY = re.compile(r"^(?:something|stuff|info|information|facts|a bit|some words)\s+(?:about|on)\s+.+", re.I)

SITES = {"youtube": "https://www.youtube.com", "google": "https://www.google.com", "gmail": "https://mail.google.com",
         "netflix": "https://www.netflix.com", "amazon": "https://www.amazon.com", "facebook": "https://www.facebook.com",
         "instagram": "https://www.instagram.com", "twitter": "https://x.com", "x": "https://x.com",
         "reddit": "https://www.reddit.com", "tiktok": "https://www.tiktok.com", "wikipedia": "https://www.wikipedia.org",
         "chatgpt": "https://chatgpt.com", "github": "https://github.com", "spotify web": "https://open.spotify.com",
         "linkedin": "https://www.linkedin.com", "espn": "https://www.espn.com", "weather": "https://weather.com"}

_NOTE_WORDS = r"(?:a\s+|the\s+|my\s+)?(?:new\s+|blank\s+|empty\s+)?(?:apple\s+)?notes?(?:\s+app)?"


def _content(x: str, now) -> dict:
    """What to write: literal text ("hello", "today's date") or something the AI composes."""
    x = x.strip().strip('"“”').strip()
    x = re.sub(r"^(?:in|down|out|that says|saying|which says|with|the words?|the text)\s+", "", x, flags=re.I)
    if _COMPOSE.match(x) or _TOPIC_ONLY.match(x) or re.match(r"^(?:why|how|what|about)\b", x, re.I):
        return {"compose": x}
    return {"text": _expand(x, now)}


def _clause(c: str, ctx: dict, st: DialogState, now) -> list[dict] | None:
    """One step (or None if this clause isn't something Clicky can do)."""
    c = c.strip()
    low = c.lower()
    # music controls
    if re.match(r"^(?:pause|stop)(?:\s+(?:the\s+)?(?:music|song|playing|it))?$", low):
        return [{"skill": "music_control", "action": "pause"}]
    if re.match(r"^(?:resume|unpause|keep playing|continue(?: playing)?)(?:\s+(?:the\s+)?(?:music|song))?$", low):
        return [{"skill": "music_control", "action": "play"}]
    if re.match(r"^(?:skip|next)(?:\s+(?:this\s+)?(?:song|track))?$|^(?:play\s+)?(?:the\s+)?next (?:song|track)$", low):
        return [{"skill": "music_control", "action": "next"}]
    if re.match(r"^(?:previous|go back|last)(?:\s+(?:song|track))$|^play the (?:previous|last) (?:song|track)$", low):
        return [{"skill": "music_control", "action": "previous"}]
    # play a song
    m = re.match(r"^play\s+(?P<q>.+?)(?:\s+(?:on|in|from|with)\s+(?:apple\s+)?music(?:\s+app)?)?$", c, re.I)
    if m and not re.match(r"^(?:some|a|music|something)\b", m.group("q"), re.I):
        q = _clean_song(m.group("q"))
        if q:
            return [{"skill": "open_app", "app": "Music"}, {"skill": "play_music", "query": q}]
    if re.match(r"^play\s+(?:some\s+)?music$", low):
        return [{"skill": "open_app", "app": "Music"}, {"skill": "music_control", "action": "play"}]
    # new note (optionally with its contents)
    m = re.match(rf"^(?:make|create|start|add|open|write|take|do)\s+{_NOTE_WORDS}(?:\s*(?:,|:)?\s*(?:saying|that says|"
                 rf"with|about|of|on|for|called|named)?\s+(?P<x>.+))?$", c, re.I)
    if m and re.search(r"\bnotes?\b", low) and (m.group("x") or not re.match(r"^open\s+notes?(?:\s+app)?$", low)):
        step = {"skill": "new_note"}
        if m.group("x"):
            step.update(_content(m.group("x"), now))
        ctx["note"] = step
        return [step]
    # write / type something
    m = re.match(r"^(?P<verb>write|type|put|add|enter|jot down|jot|note down)\s+(?:in\s+|down\s+|out\s+)?(?P<x>.+?)"
                 rf"(?:\s+(?:in|into)\s+(?P<where>{_NOTE_WORDS}|(?:the\s+)?[A-Za-z][\w.'-]*(?:\s+[A-Za-z][\w.'-]*)?"
                 r"(?:\s+app)?))?$", c, re.I)
    if m and m.group("where") and re.search(r"\b(?:style|way|form|voice|tone|words|english|spanish|french|"
                                            r"detail|order|sentences?|paragraphs?|points?|total)\b", m.group("where"), re.I):
        m = re.match(r"^(?P<verb>write|type|put|add|enter|jot down|jot|note down)\s+(?:in\s+|down\s+|out\s+)?"
                     r"(?P<x>.+)$(?P<where>)", c, re.I)
    if m:
        x, where = m.group("x"), re.sub(r"\s+app$", "", (m.group("where") or "").strip(), flags=re.I)
        body = _content(x, now)
        if where and re.search(r"\bnotes?\b", where, re.I):
            if ctx.get("note") is not None and not ({"text", "compose"} & set(ctx["note"])):
                ctx["note"].update(body)
                return []
            step = {"skill": "new_note", **body}
            ctx["note"] = step
            return [{"skill": "open_app", "app": "Notes"}, step]
        if ctx.get("note") is not None and not ({"text", "compose"} & set(ctx["note"])):
            ctx["note"].update(body)  # "make a new note and write X" -> X goes in that note
            return []
        app = where or ctx.get("app") or ""
        if app.lower() in ("notes", "apple notes"):
            step = {"skill": "new_note", **body}
            ctx["note"] = step
            return [step]
        if not app and (m.group("verb").lower() not in ("type", "enter") or "compose" in body):
            return None  # "write a poem about cats" with nowhere to put it: just answer in the chat
        steps = [{"skill": "open_app", "app": app}] if where else []
        return steps + [{"skill": "type_text", **body}]
    # close / quit
    m = re.match(r"^(?:close|quit|exit|shut down|kill)\s+(?:the\s+|my\s+)?(?P<app>[\w .'-]+?)(?:\s+app)?$", c, re.I)
    if m and m.group("app").lower() not in ("window", "this window", "tab", "this tab", "it", "that"):
        return [{"skill": "quit_app", "app": nice_app(m.group("app"))}]
    # search the web
    m = re.match(r"^(?:search(?:\s+(?:google|the web|online|the internet))?(?:\s+for)?|google|look up|look for)\s+"
                 r"(?P<q>.+?)(?:\s+(?:on|in)\s+(?:google|the web|safari|chrome|the internet))?$", c, re.I)
    if m and not re.search(r"\b(?:file|folder|document|my (?:mac|computer))\b", low):
        return [{"skill": "open_url", "url": "https://www.google.com/search?q=" + _q(m.group("q"))}]
    m = re.match(r"^(?:search|look up)\s+(?P<q>.+?)\s+on\s+youtube$|^(?:find|watch)\s+(?P<q2>.+?)\s+on\s+youtube$", c, re.I)
    if m:
        return [{"skill": "open_url", "url": "https://www.youtube.com/results?search_query=" +
                 _q(m.group("q") or m.group("q2"))}]
    # settings
    m = (re.match(r"^(?:open|show|go to|take me to|bring up)\s+(?:the\s+|my\s+)?(?P<p>[\w &\-‑]+?)\s+"
                  r"(?:settings|preferences|prefs|options)$", c, re.I)
         or re.match(r"^(?:change|turn (?:on|off)|connect to|adjust|fix)\s+(?:the\s+|my\s+)?(?P<p>wi-?fi|bluetooth|"
                     r"brightness|wallpaper|volume|sound|display|dark mode|screen resolution)(?:\s+.*)?$", c, re.I))
    if m:
        pane = settings_pane(m.group("p"))
        if pane:
            return [{"skill": "open_settings", "pane": pane}]
    # websites
    m = re.match(r"^(?:open|go to|take me to|visit|launch|bring up|pull up)\s+(?:the\s+)?(?P<s>[\w.\-]+\.(?:com|org|net|"
                 r"io|edu|gov|co|ai|app|dev|tv|us)(?:/\S*)?|youtube|gmail|netflix|amazon|facebook|instagram|twitter|"
                 r"reddit|tiktok|wikipedia|chatgpt|github|linkedin|espn)(?:\s+(?:website|site|page|\.com))?$", c, re.I)
    if m:
        s = m.group("s").lower()
        return [{"skill": "open_url", "url": SITES.get(s) or ("https://" + s if "://" not in s else s)}]
    # open an app
    m = re.match(r"^(?:open|launch|start|run|go to|switch to|bring up|pull up|show me|show|fire up|load)\s+(?:up\s+)?"
                 r"(?:the\s+|my\s+)?(?P<app>[\w .'&-]+?)(?:\s+app(?:lication)?)?$", c, re.I)
    if m:
        app = m.group("app").strip()
        if app.lower() in ("it", "that", "this") or len(app.split()) > 4 or \
                re.search(r"\b(folder|file|document|tab|page|link|window|where|how|what)\b", app, re.I):
            return None
        app = nice_app(app)
        ctx["app"] = app
        return [{"skill": "open_app", "app": app}]
    return None


_APP_NAMES = {"settings": "System Settings", "system settings": "System Settings", "system preferences": "System Settings",
              "notes": "Notes", "apple notes": "Notes", "music": "Music", "apple music": "Music", "safari": "Safari",
              "chrome": "Google Chrome", "google chrome": "Google Chrome", "textedit": "TextEdit", "text edit": "TextEdit",
              "facetime": "FaceTime", "app store": "App Store", "iphone mirroring": "iPhone Mirroring",
              "vscode": "Visual Studio Code", "vs code": "Visual Studio Code", "word": "Microsoft Word",
              "excel": "Microsoft Excel", "powerpoint": "Microsoft PowerPoint", "teams": "Microsoft Teams"}


def nice_app(name: str) -> str:
    n = name.strip().strip(".")
    return _APP_NAMES.get(n.lower(), " ".join(w[:1].upper() + w[1:] if w.islower() else w for w in n.split()))


def _q(s: str) -> str:
    import urllib.parse

    return urllib.parse.quote_plus(s.strip().strip('"“”'))


def describe(step: dict) -> str:
    k = step["skill"]
    if k == "open_app":
        return f"open {step['app']}"
    if k == "quit_app":
        return f"close {step['app']}"
    if k == "open_settings":
        return f"open {step['pane']} settings"
    if k == "play_music":
        return f"play “{step['query']}”"
    if k == "music_control":
        return {"pause": "pause the music", "play": "play music", "next": "skip to the next song",
                "previous": "go back a song"}.get(step.get("action"), "control the music")
    if k == "new_note":
        what = step.get("compose") or step.get("text")
        return "make a note" + (f" with {what[:50]}" if step.get("compose") else f": “{what[:40]}”" if what else "")
    if k == "type_text":
        what = step.get("compose") or step.get("text") or ""
        return f"write {what[:50]}" if step.get("compose") else f"type “{what[:40]}”"
    if k == "open_url":
        u = step["url"]
        return "search Google" if "google.com/search" in u else f"open {u.split('//')[-1].split('/')[0]}"
    return k


def summary_of(plan: list[dict]) -> str:
    parts = [describe(s) for s in plan]
    if len(parts) > 1 and parts[0].startswith("open ") and plan[1]["skill"] in ("new_note", "play_music"):
        parts = parts[1:] if plan[0].get("app") in ("Notes", "Music") else parts
    text = ", then ".join(parts)
    return text[:1].upper() + text[1:]


def plan(text: str, st: DialogState | None = None, now: dt.datetime | None = None) -> dict | None:
    """Everyday English -> {"plan": [...], "summary": "..."} or None (then it's a normal chat question)."""
    st = st or DialogState()
    t = rewrite(text)
    if not t:
        return None
    if _AGAIN.match(t) and st.last_plan:
        return {"plan": [dict(s) for s in st.last_plan], "summary": st.last_summary or "Do that again"}
    t = resolve(t, st)
    clauses = split(t)
    ctx: dict = {}
    steps: list[dict] = []
    for c in clauses:
        got = _clause(c, ctx, st, now)
        if got is None:
            return None  # one part isn't understood: let the AI planner or the chat handle it
        steps += got
    # tidy: drop "open Notes" right before a new note (the note step opens Notes itself)
    out: list[dict] = []
    for s in steps:
        if s["skill"] == "open_app":
            s["app"] = nice_app(s["app"])
        if out and out[-1] == s:
            continue
        out.append(s)
    for s in out:
        if s["skill"] == "new_note" and not ({"text", "compose"} & set(s)):
            s["text"] = ""
    if not out or len(out) > 8:
        return None
    result = {"plan": out, "summary": summary_of(out)}
    if len(out) == 1 and out[0]["skill"] == "open_app":
        result["open_only"] = True
    return result


def remember(st: DialogState, plan_steps: list[dict], summary: str) -> None:
    """After a task runs: remember what it was about, for "it", "that" and "again" next time."""
    st.last_plan, st.last_summary = [dict(s) for s in plan_steps], summary
    for s in plan_steps:
        if s["skill"] == "open_app":
            st.last_app = s["app"]
        elif s["skill"] == "new_note":
            st.last_app = "Notes"
        elif s["skill"] == "play_music":
            st.last_song, st.last_app = s["query"], "Music"
        elif s["skill"] == "open_settings":
            st.last_app = "System Settings"
        if s.get("text"):
            st.last_text = s["text"]


# ---- the AI planner, for requests the rules above don't cover -------------------------------------------------------
PLAN_SCHEMA = {
    "type": "object",
    "properties": {"steps": {"type": "array", "items": {"type": "object", "properties": {
        "skill": {"type": "string", "enum": sorted(ALLOWED) + ["none"]},
        "app": {"type": "string"}, "text": {"type": "string"}, "compose": {"type": "string"},
        "query": {"type": "string"}, "pane": {"type": "string"}, "url": {"type": "string"},
        "action": {"type": "string"}}, "required": ["skill"]}}},
    "required": ["steps"]}

PLANNER_PROMPT = """You turn a request for a Mac into steps. Skills:
open_app(app) quit_app(app) open_settings(pane) play_music(query) music_control(action: play|pause|next|previous)
new_note(text OR compose) type_text(text OR compose) open_url(url)
Use "compose" when the AI must write it (e.g. "a paragraph on why recycling matters"), "text" for exact words.
If the request is a question or chat, not something to do on the Mac, return {"steps":[{"skill":"none"}]}.
Examples:
"fire up safari and search for pizza near me" -> {"steps":[{"skill":"open_app","app":"Safari"},{"skill":"open_url","url":"https://www.google.com/search?q=pizza+near+me"}]}
"jot down eggs milk bread in a note" -> {"steps":[{"skill":"new_note","text":"eggs, milk, bread"}]}
"get notes going and do a short poem about the ocean" -> {"steps":[{"skill":"new_note","compose":"a short poem about the ocean"}]}
"shut spotify" -> {"steps":[{"skill":"quit_app","app":"Spotify"}]}
"what is the capital of france" -> {"steps":[{"skill":"none"}]}
Context: the last app used was "{last_app}". Answer with JSON only."""


def looks_like_command(text: str) -> bool:
    """Worth asking the AI planner? (Only for things that sound like doing, not asking.)"""
    t = rewrite(text).lower()
    if not t or t.endswith("?") or re.match(r"^(?:what|why|how|who|when|which|is|are|do|does|did|tell me|explain|"
                                            r"should|would|could i|can i)\b", t):
        return False
    from .intents import point_target

    if re.search(r"\bwhere\b|\bpoint\b", t) or point_target(text):
        return False  # "show me where X is": Clicky points; no planning needed
    if re.match(r"^(?:open|show|find|go to)\s", t) and not re.search(r"\b(?:and|then)\b|,", t):
        return False  # a single "open X" that isn't an app: Clicky looks for it on screen instead
    return bool(re.match(rf"^(?:{VERBS}|get|fire up|pull up|load|shut|bring|do)\b", t))


def validate_llm_plan(data: dict) -> list[dict] | None:
    steps = []
    for s in (data or {}).get("steps") or []:
        k = s.get("skill")
        if k == "none":
            return None
        if k not in ALLOWED:
            continue
        need = {"open_app": "app", "quit_app": "app", "open_settings": "pane", "play_music": "query",
                "music_control": "action", "open_url": "url"}.get(k)
        if need and not str(s.get(need) or "").strip():
            continue
        if k == "open_url" and not str(s["url"]).startswith(("http://", "https://")):
            continue
        if k == "open_settings":
            pane = settings_pane(s["pane"])
            if not pane:
                continue
            s["pane"] = pane
        clean = {"skill": k, **{f: str(s[f])[:2000] for f in ("app", "text", "compose", "query", "pane", "url", "action")
                                if s.get(f)}}
        if k in ("new_note", "type_text") and not ({"text", "compose"} & set(clean)):
            clean["text"] = ""
        steps.append(clean)
    return steps[:8] or None
