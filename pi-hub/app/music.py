"""Understanding music requests the way people say them.

    "play Blinding Lights by The Weeknd"   -> song "Blinding Lights", artist "The Weeknd"
    "put on some Drake" / "play Drake"      -> probably an artist (the Mac checks the library to be sure)
    "play songs by Taylor Swift"            -> artist "Taylor Swift"
    "play the album 1989" / "play 1989 album" -> album
    "play my workout playlist"              -> playlist "workout"
    "shuffle Drake"                         -> artist, shuffled

The computer part then matches this against the REAL music library (forgiving spelling) and plays exactly
that: only that artist's songs, that song, that album or that playlist.
"""
from __future__ import annotations

import re

_FILLER = r"(?:a\s+little\s+bit\s+of|a\s+bit\s+of|a\s+little|some\s+of|some|a\s+few|the|any|me|us)"


def parse(q: str) -> dict:
    """The thing to play, as {"query", and one of "song"+"artist" / "artist" / "album" / "playlist", "kind"}."""
    q = re.sub(r"\s+", " ", (q or "").strip().strip('"“”.!?')).strip()
    q = re.sub(r"\s+(?:on|in|from|with)\s+(?:apple\s+)?music(?:\s+app)?$", "", q, flags=re.I)
    q = re.sub(r"\s+(?:please|for me|right now|now|real quick|rn)$", "", q, flags=re.I)
    out: dict = {"query": q}
    shuffle = bool(re.match(r"^(?:shuffle|shuffled)\b", q, re.I))
    q = re.sub(r"^(?:shuffle(?:d)?)\s+", "", q, flags=re.I)

    if re.match(r"^(?:some\s+of\s+)?my\s+(?:favou?rite|liked|loved|top)\s+(?:songs|music|tracks|stuff)$|^(?:songs|music)"
                r"\s+i\s+(?:like|love)$", q, re.I):
        out.update(kind="favorites")
        return _done(out, True)
    if re.match(r"^(?:my\s+)?(?:music|library|songs)(?:\s+library)?$|^anything$|^something$", q, re.I):
        out.update(kind="library")
        return _done(out, True)
    m = re.match(r"^(?:my\s+|the\s+)?(?P<p>.+?)\s+playlist$|^(?:my\s+|the\s+)?playlist\s+(?:called\s+|named\s+)?(?P<p2>.+)$",
                 q, re.I)
    if m:
        out.update(kind="playlist", playlist=(m.group("p") or m.group("p2")).strip())
        return _done(out, shuffle)
    m = re.match(r"^(?:the\s+)?(?:album|record)\s+(?:called\s+|named\s+)?(?P<a>.+?)(?:\s+by\s+(?P<ar>.+))?$|"
                 r"^(?:the\s+)?(?P<a2>.+?)\s+album(?:\s+by\s+(?P<ar2>.+))?$", q, re.I)
    if m:
        out.update(kind="album", album=(m.group("a") or m.group("a2")).strip())
        if m.group("ar") or m.group("ar2"):
            out["artist"] = (m.group("ar") or m.group("ar2")).strip()
        return _done(out, shuffle)
    m = re.match(rf"^(?:{_FILLER}\s+)?(?:songs?|music|tracks|stuff|hits|anything|something)\s+(?:by|from)\s+(?P<ar>.+)$",
                 q, re.I)
    if m:
        out.update(kind="artist", artist=m.group("ar").strip())
        return _done(out, True)
    m = re.match(r"^(?:the\s+)?(?:song|track)\s+(?P<s>.+?)(?:\s+by\s+(?P<ar>.+))?$|^(?P<s2>.+?)\s+by\s+(?P<ar2>.+)$",
                 q, re.I)
    if m:
        song = (m.group("s") or m.group("s2")).strip()
        artist = (m.group("ar") or m.group("ar2") or "").strip()
        out.update(kind="song", song=re.sub(r"\s+(?:song|track)$", "", song, flags=re.I))
        if artist:
            out["artist"] = artist
        return _done(out, shuffle)
    m = re.match(rf"^(?:{_FILLER}\s+)+(?P<x>.+)$", q, re.I)  # "some Drake": most likely an artist (or a genre)
    if m:
        out.update(kind="any", hint="artist", name=m.group("x").strip())
        return _done(out, True)
    out.update(kind="any", name=re.sub(r"^(?:the\s+)?(?:song|track)\s+", "", q, flags=re.I))
    return _done(out, shuffle)


def _done(out: dict, shuffle: bool) -> dict:
    for k in ("song", "artist", "album", "playlist", "name"):
        if k in out:
            out[k] = out[k].strip(" '\"“”")
    if shuffle:
        out["shuffle"] = True
    return out
