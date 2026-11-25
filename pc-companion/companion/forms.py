"""Fill out forms from what's on your own Mac (your card in Contacts). Never passwords, cards, IDs or codes;
never presses Submit. Shows you what it will fill first, and highlights anything it wasn't sure about."""
from __future__ import annotations

import re

NEVER = re.compile(r"pass(?:word|code|phrase)?|\bpin\b|card|cvv|cvc|security code|expir|ssn|social security|"
                   r"tax ?id|account (?:number|no)|routing|iban|swift|license|passport|one.?time|verification|"
                   r"\bcode\b|otp|secret", re.I)

RULES = [  # (label pattern, profile key) - first match wins
    (r"first\s*name|given\s*name|forename", "first"),
    (r"last\s*name|surname|family\s*name", "last"),
    (r"middle\s*name", None),
    (r"(?:full\s*)?name|your name", "name"),
    (r"e-?mail", "email"),
    (r"phone|mobile|cell|telephone|\btel\b", "phone"),
    (r"address\s*(?:line)?\s*2|apt|apartment|suite|unit", "street2"),
    (r"street|address(?:\s*line\s*1)?|\baddress\b", "street"),
    (r"city|town", "city"),
    (r"state|province|region|county", "state"),
    (r"zip|postal|post\s*code", "zip"),
    (r"country", "country"),
    (r"company|organi[sz]ation|employer|business", "company"),
    (r"job\s*title|position|role", "job"),
    (r"website|url|homepage", "url"),
]


def key_for(label: str) -> str | None:
    if not label or NEVER.search(label):
        return None
    for pat, key in RULES:
        if re.search(pat, label, re.I):
            return key
    return None


def plan_fill(fields: list[dict], me: dict) -> tuple[list[tuple[dict, str]], list[dict]]:
    """Which fields to fill with what, and which ones to point out as 'please check'."""
    fill, unsure = [], []
    for f in fields:
        label = f.get("label", "")
        if NEVER.search(label):
            continue  # never touched, never highlighted
        if f.get("value", "").strip():
            continue  # already filled in: leave it
        key = key_for(label)
        value = (me.get(key) or "").strip() if key else ""
        if value:
            fill.append((f, value))
        else:
            unsure.append(f)
    return fill, unsure


ME_SCRIPT = """
tell application "Contacts"
    set p to my card
    set out to "first=" & (first name of p as text) & linefeed & "last=" & (last name of p as text) & linefeed
    try
        set out to out & "email=" & (value of item 1 of emails of p) & linefeed
    end try
    try
        set out to out & "phone=" & (value of item 1 of phones of p) & linefeed
    end try
    try
        set out to out & "company=" & (organization of p as text) & linefeed
    end try
    try
        set out to out & "job=" & (job title of p as text) & linefeed
    end try
    try
        set a to item 1 of addresses of p
        set out to out & "street=" & (street of a as text) & linefeed & "city=" & (city of a as text) & linefeed
        set out to out & "state=" & (state of a as text) & linefeed & "zip=" & (zip of a as text) & linefeed
        set out to out & "country=" & (country of a as text) & linefeed
    end try
end tell
return out"""


def parse_me(out: str) -> dict:
    me = {}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        v = v.strip()
        if k and v and v != "missing value":
            me[k.strip()] = v
    if me.get("first") or me.get("last"):
        me["name"] = f"{me.get('first', '')} {me.get('last', '')}".strip()
    return me
