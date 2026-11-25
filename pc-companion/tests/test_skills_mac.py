"""Every Clicky ability, run against a pretend Mac: checks the right app commands are sent, that your words are
passed as data (never pasted into a script), that risky things ask first, and that Clicky points somewhere real."""
import asyncio
import subprocess
import types

import pytest


class Rec:
    def __init__(self):
        self.calls = []

    def emit(self, *a):
        self.calls.append(a)


class FakeRun:
    """Stands in for subprocess.run: records commands, answers like macOS would."""

    def __init__(self, answers=None):
        self.cmds = []
        self.answers = answers or {}

    def __call__(self, cmd, input=None, capture_output=False, text=False, timeout=None, check=False, **kw):
        self.cmds.append((list(cmd), input))
        out = ""
        for key, val in self.answers.items():
            if key in " ".join(cmd) or (input and key in input):
                out = val
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")


def make_app(monkeypatch, run):
    from companion import skills as sk
    from companion.platforms import Rect, Window

    monkeypatch.setattr(sk.sys, "platform", "darwin")
    monkeypatch.setattr(sk.subprocess, "run", run)
    confirms, moves = [], []

    async def confirm(kind, title, detail, x, y, a):
        confirms.append((title, a.get("risk")))

    async def move(a):
        moves.append(a)

    async def teach_step(a):
        moves.append(("teach", a))
        return {"status": "user_clicked"}

    app = types.SimpleNamespace()
    app.ghost = types.SimpleNamespace(home=(1300, 40), primary_center=lambda: (720, 450), hold=False, last_active=0)
    app.actions = types.SimpleNamespace(_confirm=confirm, move=move, teach_step=teach_step,
                                        killed=__import__("threading").Event())
    app.bridge = types.SimpleNamespace(narrate=Rec(), ghostMove=Rec())
    app.screen = types.SimpleNamespace(find_ui_element=lambda q, w=None, deep=True: {
        "matches": [{"id": "e1", "name": q}] if q in ("Notes", "Mail", "Messages", "Reminders", "New Message", "Send") else []})
    app.platform = types.SimpleNamespace(active_window=lambda: Window(1, "Untitled", "Notes", Rect(100, 80, 900, 600)))
    app.settings = {"approved_folders": []}
    return sk.Skills(app), app, confirms, moves


def run(c):
    return asyncio.run(c)


def scripts(fake):
    return [(c, i) for c, i in fake.cmds if c[:2] == ["osascript", "-"]]


def test_note_with_title_and_quotes_is_passed_as_data(monkeypatch):
    fake = FakeRun()
    skills, app, confirms, _ = make_app(monkeypatch, fake)
    out = run(skills.run({"plan": [{"skill": "new_note", "title": "keyboard", "text": 'He said "hi" & left'}]}))
    assert out["status"] == "done" and confirms
    cmd, script = scripts(fake)[0]
    assert "<h1>keyboard</h1>" in cmd[2] and "&quot;hi&quot; &amp; left" in cmd[2]  # the words go in as data
    assert "keyboard" not in script and "make new note" in script


def test_add_to_existing_note(monkeypatch):
    fake = FakeRun({"every note whose name contains": "groceries"})
    skills, *_ = make_app(monkeypatch, fake)
    run(skills.run({"plan": [{"skill": "append_note", "title": "groceries", "text": "eggs"}]}))
    cmd, script = scripts(fake)[0]
    assert cmd[2:] == ["groceries", "<div>eggs</div>"] and "set body of n" in script


def test_missing_note_explains(monkeypatch):
    fake = FakeRun({"every note whose name contains": "NOTFOUND"})
    skills, *_ = make_app(monkeypatch, fake)
    from companion.skills import SkillError

    with pytest.raises(SkillError, match="couldn't find a note"):
        run(skills.run({"plan": [{"skill": "append_note", "title": "nope", "text": "x"}]}))


def test_messages_always_ask_and_use_contacts(monkeypatch):
    fake = FakeRun({"tell application \"Contacts\"": "+1 555 0100"})
    skills, app, confirms, _ = make_app(monkeypatch, fake)
    app.settings["auto_approve"] = True
    run(skills.run({"plan": [{"skill": "send_message", "to": "Mom", "text": "on my way"}]}))
    assert confirms[0][1] == "high"  # sending is risky: always asks, even with Always allow
    sends = [c for c, i in scripts(fake) if "send m to" in i]
    assert sends and sends[0][2:] == ["+1 555 0100", "on my way"]


def test_email_is_a_draft_not_sent(monkeypatch):
    fake = FakeRun({"tell application \"Contacts\"": "bob@example.com"})
    skills, *_ = make_app(monkeypatch, fake)
    out = run(skills.run({"plan": [{"skill": "email_draft", "to": "Bob", "subject": "Lunch", "text": "Friday?"}]}))
    mail = [(c, i) for c, i in scripts(fake) if "outgoing message" in i][0]
    assert mail[0][2:] == ["bob@example.com", "Lunch", "Friday?"] and "send" not in mail[1].replace("visible", "")
    assert "not sent" in out["steps"][0]["result"]


def test_reminder_with_time(monkeypatch):
    fake = FakeRun()
    skills, *_ = make_app(monkeypatch, fake)
    run(skills.run({"plan": [{"skill": "reminder", "text": "call mom", "when": "2026-11-25T17:00"}]}))
    cmd, script = [x for x in scripts(fake) if "make new reminder" in x[1]][0]
    assert cmd[2:] == ["call mom", "2026", "11", "25", str(17 * 3600), "1"]


def test_volume_dark_mode_wifi_screenshot(monkeypatch):
    fake = FakeRun({"output volume of": "40", "listallhardwareports": "Hardware Port: Wi-Fi\nDevice: en1\n"})
    skills, *_ = make_app(monkeypatch, fake)
    run(skills.run({"plan": [{"skill": "volume", "action": "up"}, {"skill": "dark_mode", "on": True},
                             {"skill": "wifi", "on": False}, {"skill": "screenshot"}]}))
    flat = [" ".join(c) + " " + (i or "") for c, i in fake.cmds]
    assert any("set volume output volume 55" in f for f in flat)
    assert any("set dark mode to true" in f for f in flat)
    assert any(f.startswith("networksetup -setairportpower en1 off") for f in flat)
    assert any(f.startswith("screencapture -x") for f in flat)


def test_open_app_points_at_its_window_not_the_middle(monkeypatch):
    fake = FakeRun()
    skills, app, _, moves = make_app(monkeypatch, fake)
    run(skills.run({"plan": [{"skill": "open_app", "app": "notes"}]}))
    assert ["open", "-a", "Notes"] in [c for c, _ in fake.cmds]
    x, y, label = app.bridge.ghostMove.calls[-1]
    assert (x, y) != (720, 450) and y == 80 + 14  # the Notes window's title bar


def test_guided_lesson_points_and_waits_for_each_click(monkeypatch):
    fake = FakeRun()
    skills, app, confirms, moves = make_app(monkeypatch, fake)
    out = run(skills.guide({"title": "How to send an email", "steps": [
        {"app": "Mail", "say": "Open Mail."}, {"target": "New Message", "say": "Click here."},
        {"say": "Write your message.", "wait_text": 0.01}, {"target": "Send", "say": "Click Send."}]}))
    assert out == {"status": "done", "steps_done": 4, "steps": 4}
    teach = [m[1] for m in moves if isinstance(m, tuple) and m[0] == "teach"]
    assert [t["instruction"] for t in teach] == ["Step 2 of 4: Click here.", "Step 4 of 4: Click Send."]
    assert not confirms and app.ghost.hold is False  # a lesson never clicks for you, and Clicky is released after


def test_shortcuts_lock_trash_and_folders(monkeypatch):
    import os

    from companion import macinput

    fake = FakeRun()
    pressed = []
    monkeypatch.setattr(macinput, "press", lambda k: pressed.append(k))
    skills, app, confirms, _ = make_app(monkeypatch, fake)
    run(skills.run({"plan": [{"skill": "keys", "keys": "cmd+t", "what": "New tab"}, {"skill": "lock_screen"},
                             {"skill": "open_file", "query": "~/Downloads"}]}))
    assert pressed == ["cmd+t"]
    assert ["pmset", "displaysleepnow"] in [c for c, _ in fake.cmds]
    assert ["open", os.path.expanduser("~/Downloads")] in [c for c, _ in fake.cmds]
    confirms.clear()
    run(skills.run({"plan": [{"skill": "empty_trash"}]}))
    assert confirms[0][1] == "high"  # can't be undone: always asks


def test_lesson_voice_commands_are_understood():
    from companion.voice import clean_transcript, lesson_command

    assert lesson_command("Next.") == "next" and lesson_command("okay got it") == "next"
    assert lesson_command("Say that again") == "repeat" and lesson_command("go back") == "back"
    assert lesson_command("Stop the lesson.") == "stop"
    assert lesson_command("where is the send button?") is None  # a question, not a command
    assert clean_transcript(" [BLANK_AUDIO]\n Hello there.\n") == "Hello there."


def test_lesson_can_go_back_and_stop(monkeypatch):
    from companion import voice

    spoken = []
    monkeypatch.setattr(voice, "speak", lambda t, **k: spoken.append(t))
    fake = FakeRun()
    skills, app, confirms, moves = make_app(monkeypatch, fake)
    said = iter(["back", None, "stop"])

    async def teach_step(a):  # the user says "back" on step 2, then clicks, then says "stop" on step 2
        cmd = next(said)
        if cmd:
            app.lesson["cmd"] = cmd
            a["interrupt"].set()
            return {"status": "timeout"}
        return {"status": "user_clicked"}

    app.actions.teach_step = teach_step
    out = run(skills.guide({"title": "How to send an email", "steps": [
        {"say": "Look at Mail.", "wait_text": 0.01}, {"target": "New Message", "say": "Click here."},
        {"target": "Send", "say": "Click Send."}]}))
    # step 1, "back" on step 2 -> step 1 again, step 2 clicked, "stop" on step 3
    assert out["steps_done"] == 2 and app.lesson["active"] is False
    assert sum("Step 1." in t for t in spoken) == 2 and spoken[-1].startswith("Okay, stopping")


def test_rescue_only_offers_for_trouble_once():
    import types as _t

    from companion.rescue import Watcher, needs_help

    assert needs_help({"title": "Couldn't connect", "text": "The server isn't responding."})
    assert needs_help({"title": "", "text": "Are you sure you want to delete “Taxes”? This can't be undone."})
    assert not needs_help({"title": "Save As", "text": "Where: Documents"})
    dlg = {"app": "Pages", "title": "Error", "text": "The document could not be saved.", "buttons": ["OK"]}
    app = _t.SimpleNamespace(paused=False, settings={"rescue_mode": True, "blocklist": ["1Password"]},
                             platform=_t.SimpleNamespace(front_dialog=lambda: dlg))
    w = Watcher(app)
    assert w.check_once() == dlg and w.check_once() is None  # once per pop-up
    app.settings["rescue_mode"] = False
    w.seen.clear()
    assert w.check_once() is None  # turned off in the menu


def test_focus_guard_summary_and_nudge(monkeypatch):
    import types as _t

    from companion import focus as fmod

    nudges = []
    app = _t.SimpleNamespace(ghost=_t.SimpleNamespace(flags={}, primary_center=lambda: (700, 400)),
                             bridge=_t.SimpleNamespace(ghostMove=_t.SimpleNamespace(emit=lambda *a: nudges.append(a)),
                                                       narrate=_t.SimpleNamespace(emit=lambda *a: None)),
                             notify=lambda t: None, paused=False)
    f = fmod.Focus(app)
    assert "25 minutes" in f.start(25) and app.ghost.flags["focus_until"] > 0
    f.time_in = {"Pages": 1500.0, "Safari - YouTube": 300.0}
    out = f.stop()
    assert "Pages 25 min" in out and "5 min on distractions" in out and app.ghost.flags["focus_until"] == 0


def test_form_filling_is_safe():
    from companion.forms import parse_me, plan_fill

    me = parse_me("first=Sam\nlast=Lee\nemail=sam@example.com\nphone=555 0100\ncity=Austin\njob=missing value\n")
    assert me["name"] == "Sam Lee" and "job" not in me
    fields = [{"label": "First name", "value": ""}, {"label": "Email address", "value": ""},
              {"label": "Password", "value": ""}, {"label": "Card number", "value": ""},
              {"label": "Security code", "value": ""}, {"label": "City", "value": "Dallas"},
              {"label": "Favorite color", "value": ""}]
    fill, unsure = plan_fill(fields, me)
    assert [(f["label"], v) for f, v in fill] == [("First name", "Sam"), ("Email address", "sam@example.com")]
    assert [f["label"] for f in unsure] == ["Favorite color"]  # passwords/cards/codes: never touched or shown


def test_skill_library_find_export_import(tmp_path):
    from companion.recorder import Library, simplify

    lib = Library(tmp_path)
    lib.save("export report", [{"app": "Numbers", "say": "Open Numbers."}, {"target": "Export", "say": "Click Export."}])
    assert lib.find("how to export the report")["name"] == "export report"
    assert lib.find("bake a cake") is None
    out = lib.export(tmp_path / "x.clickskills")
    other = Library(tmp_path / "friend")
    assert other.import_file(out) == 1 and other.find("export report")
    # imported skills are only data: unknown fields (like commands) are dropped
    (tmp_path / "evil.clickskills").write_text('{"skills":[{"name":"x","steps":[{"target":"OK","run":"rm -rf ~"}]}]}')
    assert other.import_file(tmp_path / "evil.clickskills") == 1
    assert other.find("x")["steps"] == [{"target": "OK"}]
    assert simplify([{"app": "A"}, {"app": "B"}, {"target": "Go"}, {"target": "Go"}, {"app": "C"}]) == [
        {"app": "B"}, {"target": "Go"}]
