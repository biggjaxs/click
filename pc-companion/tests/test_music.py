"""Playing exactly what was asked for, matched to the real library."""
from companion import music

LIB = {"tracks": [
    ("Blinding Lights", "The Weeknd", "After Hours", "R&B/Soul"),
    ("Save Your Tears", "The Weeknd", "After Hours", "R&B/Soul"),
    ("Blinding Lights", "Some Cover Band", "Covers", "Pop"),
    ("God's Plan", "Drake", "Scorpion", "Hip-Hop/Rap"),
    ("Jimmy Cooks (feat. 21 Savage)", "Drake & 21 Savage", "Honestly, Nevermind", "Hip-Hop/Rap"),
    ("Drake Song Tribute", "Fan Club", "Tributes", "Pop"),
    ("Shake It Off", "Taylor Swift", "1989", "Pop"),
    ("Blank Space", "Taylor Swift", "1989", "Pop"),
    ("Take Five", "Dave Brubeck Quartet", "Time Out", "Jazz"),
    ("Hotel California", "Eagles", "Hotel California", "Rock"),
], "playlists": ["Workout Mix", "Chill Vibes", "Road Trip"]}


def pick(**req):
    return music.choose(req, LIB)


def test_an_artist_plays_only_that_artist():
    assert pick(kind="any", name="drake", hint="artist") == {"how": "artist", "value": "Drake"}
    assert pick(kind="any", name="drak") == {"how": "artist", "value": "Drake"}  # typo
    assert pick(kind="artist", artist="taylor swft")["value"] == "Taylor Swift"
    assert pick(kind="any", name="the weeknd")["value"] == "The Weeknd"
    assert pick(kind="artist", artist="eagles")["value"] == "Eagles"  # "the eagles" vs "Eagles"


def test_a_song_by_an_artist_is_that_exact_version():
    p = pick(kind="song", song="blinding lights", artist="the weeknd")
    assert p == {"how": "song", "value": "Blinding Lights", "artist": "The Weeknd"}
    assert pick(kind="song", song="jimmy cooks", artist="drake")["value"].startswith("Jimmy Cooks")
    assert pick(kind="any", name="gods plan")["how"] == "song"
    assert pick(kind="song", song="blinding lights", artist="dua lipa")["how"] == "catalog"  # not this one


def test_albums_playlists_genres_and_not_in_library():
    assert pick(kind="album", album="1989") == {"how": "album", "value": "1989", "artist": ""}
    assert pick(kind="playlist", playlist="workout") == {"how": "playlist", "value": "Workout Mix"}
    assert pick(kind="any", name="chill vibes") == {"how": "playlist", "value": "Chill Vibes"}
    assert pick(kind="any", name="jazz")["how"] == "genre"
    out = pick(kind="any", name="bad bunny", hint="artist")
    assert out["how"] == "catalog" and out["entity"] == "musicArtist"
    assert pick(kind="favorites") == {"how": "favorites"}


def test_reading_the_library():
    raw = "\x1e".join(["A\x1fB", "X\x1fY", "Al1\x1fAl2", "Pop\x1fRock", "Mix\x1fClicky Mix"])
    idx = music.parse_index(raw)
    assert idx["tracks"] == [("A", "X", "Al1", "Pop"), ("B", "Y", "Al2", "Rock")]
    assert idx["playlists"] == ["Mix"]  # Click's own queue isn't offered back
