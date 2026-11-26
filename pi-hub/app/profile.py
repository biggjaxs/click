"""What Click has learned about how you use your computer (from the companion brain's activity notes).

Only app names, window titles and time spent are used - never keystrokes or screenshots. All of it stays in
Click's encrypted database on this computer and is deleted after 60 days.
"""
from __future__ import annotations

import datetime as dt
import re
import time
from collections import defaultdict

BROWSERS = {"Safari", "Google Chrome", "Arc", "Brave Browser", "Microsoft Edge", "Firefox", "Opera", "Chromium", "Vivaldi"}
GENERIC = re.compile(r"^(?:untitled|new tab|start page|favorites|home|inbox|downloads|desktop|documents|recents|"
                     r"finder|settings|system settings|calendar|messages|notes|music|)$", re.I)


def site_of(title: str) -> str:
    parts = re.split(r"\s[-–|•]\s", title)
    return parts[-1].strip()[:40] if len(parts) > 1 else ""


def build(db, days: int = 14) -> dict:
    since = time.time() - days * 86400
    rows = db.query("SELECT day, hour, app, title, seconds FROM activity WHERE created_at >= ?", (since,))
    apps, hours, projects, sites, today_apps = defaultdict(float), defaultdict(float), defaultdict(float), \
        defaultdict(float), defaultdict(float)
    today = dt.date.today().isoformat()
    days_seen = set()
    for r in rows:
        sec = float(r["seconds"] or 0)
        app, title = r["app"] or "?", (r["title"] or "").strip()
        apps[app] += sec
        hours[int(r["hour"])] += sec
        days_seen.add(r["day"])
        if r["day"] == today:
            today_apps[app] += sec
        if app in BROWSERS:
            site = site_of(title)
            if site:
                sites[site] += sec
        elif title and not GENERIC.match(title):
            projects[(title[:60], app)] += sec
    top = lambda d, n: sorted(d.items(), key=lambda kv: -kv[1])[:n]  # noqa: E731
    peak = sorted(h for h, _ in top(hours, 3))
    return {"days": len(days_seen), "apps": top(apps, 6), "peak_hours": peak, "projects": top(projects, 4),
            "sites": top(sites, 5), "today": top(today_apps, 5), "total": sum(apps.values())}


def _hrs(sec: float) -> str:
    return f"{sec / 3600:.1f} h" if sec >= 3600 else f"{max(1, int(sec // 60))} min"


def _hour(h: int) -> str:
    return dt.time(h).strftime("%I %p").lstrip("0")


def line(p: dict) -> str:
    """A short line for the AI's context (kept small so answers stay fast)."""
    if not p or p.get("total", 0) < 1800:
        return ""
    bits = ["Most-used apps: " + ", ".join(f"{a} ({_hrs(s)})" for a, s in p["apps"][:4])]
    if p["peak_hours"]:
        bits.append("busiest around " + ", ".join(_hour(h) for h in p["peak_hours"]))
    if p["projects"]:
        bits.append("recent projects: " + ", ".join(f"\"{t}\" in {a}" for (t, a), _ in p["projects"][:3]))
    if p["sites"]:
        bits.append("favourite sites: " + ", ".join(s for s, _ in p["sites"][:3]))
    return ("What I've learned about how they use their computer (last 2 weeks): " + "; ".join(bits) + ".")[:420]


def answer(p: dict) -> str:
    """For 'what have you learned about me?' / 'what do I use most?'."""
    if not p or p.get("total", 0) < 600:
        return ("I'm still getting to know you: I learn from which apps and windows you use (never your typing or "
                "screen contents) and keep it private on this computer. Ask me again in a day or two!")
    out = [f"Here's what I've learned over the last {p['days']} day{'s' if p['days'] != 1 else ''}:"]
    out.append("- **Your main apps:** " + ", ".join(f"{a} ({_hrs(s)})" for a, s in p["apps"][:5]))
    if p["peak_hours"]:
        out.append("- **You're busiest around** " + ", ".join(_hour(h) for h in p["peak_hours"]))
    if p["projects"]:
        out.append("- **What you've been working on:** " + "; ".join(f"“{t}” in {a}" for (t, a), _ in p["projects"]))
    if p["sites"]:
        out.append("- **Sites you visit most:** " + ", ".join(s for s, _ in p["sites"]))
    if p["today"]:
        out.append("- **Today so far:** " + ", ".join(f"{a} {_hrs(s)}" for a, s in p["today"]))
    out.append("All of this stays private on your computer.")
    return "\n".join(out)


# ---- summaries of what you're watching / reading ----------------------------------------------------------------
def youtube_id(url: str) -> str | None:
    m = re.search(r"(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/|youtube\.com/live/)([\w-]{11})", url or "")
    return m.group(1) if m else None


def is_short_form(url: str, title: str) -> bool:
    return bool(re.search(r"youtube\.com/shorts/|tiktok\.com|instagram\.com/(?:reel|p)/|#shorts", f"{url} {title}", re.I))


def youtube_transcript(vid: str, max_chars: int = 9000) -> str:
    """The video's captions (works with both old and new versions of the library)."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return ""
    try:
        try:
            items = YouTubeTranscriptApi().fetch(vid)  # 1.x
            parts = [getattr(i, "text", None) or i.get("text", "") for i in items]
        except AttributeError:
            parts = [i["text"] for i in YouTubeTranscriptApi.get_transcript(vid)]  # 0.6
    except Exception:  # noqa: BLE001 - no captions, private video...
        return ""
    return " ".join(p.replace("\n", " ") for p in parts)[:max_chars]
