"""Clicky's language understanding: polite phrasing, several steps at once, 'it' / 'that' / 'again'."""
from app.language import DialogState, looks_like_command, plan, remember, validate_llm_plan


def steps(text, st=None):
    p = plan(text, st)
    return p["plan"] if p else None


def test_no_trigger_word_or_politeness_needed():
    for t in ("open safari", "can you open safari for me", "could you please open safari", "have clicky open safari",
              "hey, open safari please", "I want you to open safari"):
        assert steps(t) == [{"skill": "open_app", "app": "Safari"}], t


def test_many_steps_in_one_message():
    assert steps("open notes, create a new note and put in a paragraph on why global warming is bad") == [
        {"skill": "open_app", "app": "Notes"},
        {"skill": "new_note", "compose": "a paragraph on why global warming is bad"}]
    assert steps("open calculator and then open safari") == [
        {"skill": "open_app", "app": "Calculator"}, {"skill": "open_app", "app": "Safari"}]
    assert steps("open safari and search for pizza near me")[1]["url"].endswith("q=pizza+near+me")
    # the words inside what you write aren't split into steps
    assert steps("open notes and type eggs and milk")[-1] == {"skill": "new_note", "text": "eggs and milk"}


def test_it_that_and_again():
    st = DialogState()
    p = plan("open spotify", st)
    remember(st, p["plan"], p["summary"])
    assert steps("close it", st) == [{"skill": "quit_app", "app": "Spotify"}]
    p = plan("play Levitating", st)
    remember(st, p["plan"], p["summary"])
    assert steps("play it again", st)[-1] == {"skill": "play_music", "query": "Levitating"}
    assert steps("do it again", st) == p["plan"]
    remember(st, [{"skill": "open_app", "app": "Notes"}], "Open Notes")
    assert steps("type hello in it", st)[-1] == {"skill": "new_note", "text": "hello"}


def test_questions_stay_questions():
    for t in ("what is the weather", "write a poem about cats", "where is wifi", "why is the sky blue?"):
        assert steps(t) is None, t
    assert not looks_like_command("show me where the wifi is")
    assert looks_like_command("fire up safari and look for flights")


def test_ai_plans_are_checked():
    assert validate_llm_plan({"steps": [{"skill": "none"}]}) is None
    assert validate_llm_plan({"steps": [{"skill": "rm -rf", "app": "x"}, {"skill": "open_app", "app": "Safari"}]}) == [
        {"skill": "open_app", "app": "Safari"}]
    assert validate_llm_plan({"steps": [{"skill": "open_url", "url": "file:///etc/passwd"}]}) is None


def test_titles_and_values_never_include_command_words():
    assert steps("open notes and make me a note titled keyboard")[-1] == {"skill": "new_note", "title": "keyboard", "text": ""}
    assert steps("make a note called groceries with apples, bread and milk") == [
        {"skill": "new_note", "title": "groceries", "text": "apples, bread and milk"}]
    assert steps("create a new note titled 'Ideas' that says build a robot") == [
        {"skill": "new_note", "title": "Ideas", "text": "build a robot"}]
    assert steps("make a note titled keyboard and write a paragraph about mechanical keyboards in it") == [
        {"skill": "new_note", "title": "keyboard", "compose": "a paragraph about mechanical keyboards"}]
    assert steps("make a quick note: dentist tuesday") == [{"skill": "new_note", "text": "dentist tuesday"}]


def test_indirect_requests_and_typos():
    assert steps("I want to listen to Levitating")[-1] == {"skill": "play_music", "query": "Levitating"}
    assert steps("put on some Drake")[-1] == {"skill": "play_music", "query": "Drake"}
    assert steps("I need to write down eggs and milk") == [{"skill": "new_note", "text": "eggs and milk"}]
    assert steps("let me check my email") == [{"skill": "open_app", "app": "Mail"}]
    assert steps("opne safari") == [{"skill": "open_app", "app": "Safari"}]
    assert steps("open notse and wirte hello there")[-1] == {"skill": "new_note", "text": "hello there"}
    # your own words are never "corrected"
    assert steps("make a note saying opne the windos")[-1]["text"] == "opne the windos"


def test_new_abilities():
    import datetime as dt

    now = dt.datetime(2026, 11, 25, 14, 0)
    assert steps("text mom saying I'm on my way") == [{"skill": "send_message", "to": "mom", "text": "I'm on my way"}]
    assert steps("add eggs to my groceries note") == [{"skill": "append_note", "title": "groceries", "text": "eggs"}]
    assert steps("write an email to sara@x.com saying thanks for lunch")[0]["skill"] == "email_draft"
    r = plan("remind me to call mom at 5pm", now=now)["plan"][0]
    assert r["text"] == "call mom" and r["when"] == "2026-11-25T17:00"
    assert steps("turn the volume down")[0] == {"skill": "volume", "action": "down"}
    assert steps("turn on dark mode") == [{"skill": "dark_mode", "on": True}]
    assert steps("open my resume") == [{"skill": "open_file", "query": "resume"}]
    assert steps("take a screenshot") == [{"skill": "screenshot"}]


def test_lessons():
    from app.guides import find

    title, st = find("show me how to send an email")
    assert st[0]["app"] == "Mail" and any(s.get("target") == "Send" for s in st)
    assert find("how do I make pancakes") is None  # a cooking question, not a computer lesson
    assert find("walk me through connecting to wifi")[1]  # built-in
    assert find("show me how to add a contact") == ("How to add a contact", [])  # the AI writes this one
