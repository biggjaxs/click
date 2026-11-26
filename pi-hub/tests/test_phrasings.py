"""Lots of real ways people ask for things: each must turn into the right action (or stay a chat question)."""
import pytest

from app.language import plan

CASES = [
    ("can you open up spotify", "open_app"), ("launch calculator", "open_app"), ("pull up youtube", "open_url"),
    ("go to amazon.com", "open_url"), ("quit music", "quit_app"), ("play some taylor swift", "play_music"),
    ("play the next song", "music_control"), ("skip this song", "music_control"), ("pause", "music_control"),
    ("turn it up", "volume"), ("make it louder", "volume"), ("set the volume to 50%", "volume"),
    ("turn off the wifi", "wifi"), ("switch to dark mode", "dark_mode"), ("make a note that says buy milk", "new_note"),
    ("make a note about my vacation ideas", "new_note"), ("jot down call dentist", "new_note"),
    ("add bread to my shopping list note", "append_note"), ("text john I'll be late", "send_message"),
    ("send mom a text saying hi", "send_message"), ("email sarah and tell her the meeting moved to 3", "email_draft"),
    ("remind me at 5 to call mom", "reminder"), ("set a reminder for tomorrow to pay rent", "reminder"),
    ("open my downloads", "open_file"), ("show me my documents folder", "open_file"),
    ("search for flights to miami", "open_url"), ("open the settings for bluetooth", "open_settings"),
    ("turn on bluetooth", "open_settings"), ("take a screenshot", "screenshot"), ("lock my screen", "lock_screen"),
    ("empty the trash", "empty_trash"), ("open a new tab", "keys"), ("undo that", "keys"), ("select all", "keys"),
    ("save this", "keys"), ("close this tab", "keys"), ("press command shift t", "keys"),
    ("hey click could you please open safari for me thanks", "open_app"), ("I want to listen to Drake", "play_music"),
    ("make me a note titled keyboard", "new_note"), ("opne notes", "open_app"),
    # messy, everyday typing
    ("yo open spotify real quick", "open_app"), ("can u open notes", "open_app"), ("pls play some drake", "play_music"),
    ("turn the volume down a bit", "volume"), ("lower the volume", "volume"), ("raise the volume", "volume"),
    ("can you mute my mac", "volume"), ("take a ss", "screenshot"), ("text my mom im on my way", "send_message"),
    ("set a reminder to call grandma at 6", "reminder"), ("remind me in 10 min to check the oven", "reminder"),
    ("start focusing", "focus"), ("lock in for an hour", "focus"), ("i wanna watch youtube", "open_url"),
    ("go back a song", "music_control"), ("turn it down", "volume"), ("louder pls", "volume"),
    ("could ya close safari", "quit_app"), ("open up my downloads folder", "open_file"),
    ("make a new note called ideas", "new_note"), ("put on some music", "music_control"),
    ("shut off the wifi", "wifi"), ("dark mode pls", "dark_mode"), ("new tab", "keys"),
    ("can you help me focus", "focus"), ("open chrome and go to youtube", "open_url"), ("stop the music", "music_control"),
]
QUESTIONS = ["what is the capital of france", "why is the sky blue", "write me a poem about cats",
             "how do I make pancakes", "who won the super bowl", "tell me a joke", "explain black holes"]


@pytest.mark.parametrize("text,skill", CASES)
def test_phrasing(text, skill):
    p = plan(text)
    assert p, f"not understood: {text}"
    assert p["plan"][-1]["skill"] == skill, f"{text} -> {p['plan']}"


@pytest.mark.parametrize("text", QUESTIONS)
def test_questions_are_answered_not_acted_on(text):
    assert plan(text) is None


@pytest.mark.parametrize("text,minutes", [("help me focus for 45 minutes", 45), ("focus mode for an hour", 60),
                                          ("i need to focus", 25), ("i need to study for 2 hours", 120)])
def test_focus(text, minutes):
    assert plan(text)["plan"] == [{"skill": "focus", "minutes": minutes}]
