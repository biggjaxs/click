"""Playing exactly what was asked for, from the Music library on this Mac.

The chat part sends what the person meant ("song Blinding Lights by The Weeknd", "artist Drake", "album 1989",
"playlist workout", or just "drake"). Here it's matched against the real library, forgiving spelling and
"the"/"&"/featuring differences, and then:
  - a song plays that exact song,
  - an artist plays ONLY that artist's songs (shuffled), as a queue called "Clicky Mix",
  - an album plays that album in order, a playlist plays that playlist, a genre plays that genre,
  - not in the library: Apple's public music search finds it and it's opened in Apple Music.
"""
from __future__ import annotations

import difflib
import logging
import re
import time

log = logging.getLogger("click.music")

GENRES = {"jazz", "rock", "pop", "hip hop", "hip-hop", "rap", "r&b", "country", "classical", "lofi", "lo-fi", "chill",
          "electronic", "edm", "house", "techno", "indie", "metal", "punk", "blues", "soul", "reggae", "latin",
          "k-pop", "kpop", "alternative", "folk", "gospel", "christian", "dance", "soundtrack", "ambient"}

_index: dict = {"at": 0.0}

INDEX_SCRIPT = r'''
tell application "Music"
    set L to library playlist 1
    set ns to name of every track of L
    set ars to artist of every track of L
    set als to album of every track of L
    set gs to genre of every track of L
    set ps to name of every user playlist
end tell
set AppleScript's text item delimiters to (ASCII character 31)
set out to (ns as text) & (ASCII character 30) & (ars as text) & (ASCII character 30) & (als as text) & ¬
    (ASCII character 30) & (gs as text) & (ASCII character 30) & (ps as text)
set AppleScript's text item delimiters to ""
return out
'''

PLAY_SCRIPT = r'''
on run argv
    set how to item 1 of argv
    set v to item 2 of argv
    set ar to item 3 of argv
    set shuf to (item 4 of argv is "1")
    tell application "Music"
        if how is "song" then
            if ar is "" then
                set t to first track of library playlist 1 whose name is v
            else
                set t to first track of library playlist 1 whose name is v and artist is ar
            end if
            play t
            return (name of t) & " by " & (artist of t)
        end if
        if how is "playlist" then
            set shuffle enabled to shuf
            play user playlist v
            return v
        end if
        if how is "library" then
            set shuffle enabled to true
            play library playlist 1
            return "your library"
        end if
        if not (exists user playlist "Clicky Mix") then make new user playlist with properties {name:"Clicky Mix"}
        set p to user playlist "Clicky Mix"
        delete every track of p
        if how is "artist" then
            set ts to (every track of library playlist 1 whose artist contains v)
        else if how is "album" then
            if ar is "" then
                set ts to (every track of library playlist 1 whose album is v)
            else
                set ts to (every track of library playlist 1 whose album is v and artist contains ar)
            end if
        else if how is "genre" then
            set ts to (every track of library playlist 1 whose genre contains v)
        else if how is "favorites" then
            try
                set ts to (every track of library playlist 1 whose favorited is true)
            on error
                set ts to (every track of library playlist 1 whose loved is true)
            end try
        else
            return "NOTFOUND"
        end if
        if (count of ts) is 0 then return "NOTFOUND"
        duplicate ts to p
        set shuffle enabled to shuf
        play p
        return (count of ts) as text
    end tell
end run
'''


def norm(t: str) -> str:
    t = (t or "").lower().replace("&", " and ").replace("’", "'")
    t = re.sub(r"\((?:feat|ft|with)[^)]*\)|\[[^\]]*\]", " ", t)  # (feat. X), [Remastered]
    t = re.sub(r"\s+-\s+(?:remaster(?:ed)?|live|single version|radio edit).*$", " ", t)
    t = re.sub(r"'", "", t)  # gods plan == God's Plan
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"^the\s+", "", t.strip())
    return re.sub(r"\s+", " ", t).strip()


def score(q: str, name: str) -> float:
    a, b = norm(q), norm(name)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if b.startswith(a + " ") or a.startswith(b + " "):
        return 0.9 if min(len(a), len(b)) >= 4 else 0.7
    toks = b.split(" and ") + re.split(r",\s*|\s+x\s+|\s+feat\s+|\s+ft\s+", b)  # "drake and future", "a, b"
    if any(norm(t) == a for t in toks if t):
        return 0.93
    return difflib.SequenceMatcher(None, a, b).ratio()


def _best(q: str, names, cutoff: float):
    best, s_best = None, 0.0
    for n in names:
        s = score(q, n)
        if s > s_best:
            best, s_best = n, s
    return (best, s_best) if s_best >= cutoff else (None, s_best)


def choose(req: dict, idx: dict) -> dict:
    """What to play from the library: {"how": song|artist|album|playlist|genre|favorites|library|catalog, ...}."""
    kind = req.get("kind") or "any"
    tracks = idx.get("tracks") or []
    artists = sorted({t[1] for t in tracks if t[1]})
    if kind in ("favorites", "library"):
        return {"how": kind}
    if kind == "playlist":
        p, _ = _best(req.get("playlist", ""), idx.get("playlists") or [], 0.7)
        return {"how": "playlist", "value": p} if p else {"how": "catalog", "term": req.get("playlist", "")}
    if kind == "album":
        pool = [t for t in tracks if not req.get("artist") or score(req["artist"], t[1]) >= 0.75]
        a, _ = _best(req.get("album", ""), {t[2] for t in pool}, 0.8)
        if a:
            return {"how": "album", "value": a, "artist": next((t[1] for t in pool if t[2] == a), "") if
                    req.get("artist") else ""}
        return {"how": "catalog", "term": f"{req.get('album', '')} {req.get('artist', '')}".strip(), "entity": "album"}
    if kind == "artist":
        a, _ = _best(req.get("artist", ""), artists, 0.78)
        return {"how": "artist", "value": a} if a else {"how": "catalog", "term": req.get("artist", ""),
                                                         "entity": "musicArtist", "want": req.get("artist", "")}
    if kind == "song":
        best, s_best = None, 0.0
        for t in tracks:
            s = score(req.get("song", ""), t[0])
            if s < 0.8:
                continue
            if req.get("artist"):
                sa = score(req["artist"], t[1])
                if sa < 0.7:
                    continue  # same name, different artist: not the one they asked for
                s = s * 0.6 + sa * 0.4
            if s > s_best:
                best, s_best = t, s
        if best:
            return {"how": "song", "value": best[0], "artist": best[1]}
        return {"how": "catalog", "term": f"{req.get('song', '')} {req.get('artist', '')}".strip(), "entity": "song",
                "want": req.get("song", ""), "by": req.get("artist", "")}
    # just a name: is it an artist, a song, an album, a playlist or a genre?
    q = req.get("name") or req.get("query") or ""
    cands = []
    a, sa = _best(q, artists, 0.82)
    if a:
        cands.append((sa + (0.06 if req.get("hint") == "artist" else 0.02), {"how": "artist", "value": a}))
    s, ss = _best(q, {t[0] for t in tracks}, 0.85)
    if s:
        art = next(t[1] for t in tracks if t[0] == s)
        cands.append((ss, {"how": "song", "value": s, "artist": art}))
    al, sal = _best(q, {t[2] for t in tracks if t[2]}, 0.88)
    if al:
        cands.append((sal - 0.01, {"how": "album", "value": al, "artist": ""}))
    p, sp = _best(q, idx.get("playlists") or [], 0.85)
    if p:
        cands.append((sp + 0.01, {"how": "playlist", "value": p}))
    if norm(q) in GENRES:
        g, sg = _best(q, {t[3] for t in tracks if len(t) > 3 and t[3]}, 0.7)
        cands.append((0.99, {"how": "genre", "value": g or q}))
    if cands:
        return max(cands, key=lambda c: c[0])[1]
    return {"how": "catalog", "term": q, "entity": "musicArtist" if req.get("hint") == "artist" else "song", "want": q}


def parse_index(raw: str) -> dict:
    parts = raw.split("\x1e")
    while len(parts) < 5:
        parts.append("")
    cols = [p.split("\x1f") if p else [] for p in parts]
    n = len(cols[0])
    tracks = [(cols[0][i], cols[1][i] if i < len(cols[1]) else "", cols[2][i] if i < len(cols[2]) else "",
               cols[3][i] if i < len(cols[3]) else "") for i in range(n)]
    return {"tracks": tracks, "playlists": [p for p in cols[4] if p and p != "Clicky Mix"], "at": time.time()}


def catalog_search(term: str, entity: str = "song", want: str = "", by: str = "") -> dict | None:
    """Apple's public music search (only the words asked for are sent; no account needed). Picks the result
    that best matches the song (or album/artist) name AND the artist, not just the first one."""
    import httpx

    ent = {"song": "musicTrack"}.get(entity, entity)
    try:
        r = httpx.get("https://itunes.apple.com/search", params={"term": term, "media": "music", "entity": ent,
                                                                 "limit": 10}, timeout=8)
        results = r.json().get("results") or []
    except Exception:  # noqa: BLE001
        return None
    if not results:
        return None

    alt = (r"remix|karaoke|instrumental|cover|tribute|bossa nova|lullaby|piano|acoustic|8-bit|made famous|"
           r"in the style|originally performed|workout|cover version")

    def fit(i_res):
        i, res = i_res
        name = res.get("trackName") or res.get("collectionName") or res.get("artistName") or ""
        s = score(want, name) if want else 0.5
        if by:
            s += score(by, res.get("artistName", "")) * 0.8
        if re.search(alt, f"{name} {res.get('collectionName', '')} {res.get('artistName', '')}", re.I) \
                and not re.search(alt, f"{want} {term}", re.I):
            s -= 0.35  # the original, unless they asked for a remix or a cover
        return s - 0.015 * i  # Apple's own ranking breaks ties (the famous one is usually first)

    res = max(enumerate(results), key=fit)[1]
    url = res.get("trackViewUrl") or res.get("collectionViewUrl") or res.get("artistLinkUrl") or ""
    name = res.get("trackName") or res.get("collectionName") or res.get("artistName") or term
    artist = res.get("artistName") if (res.get("trackName") or res.get("collectionName")) else ""
    return {"url": re.sub(r"[?&]uo=\d+", "", url).replace("https://", "music://", 1) if url else "", "name": name, "by": artist}
