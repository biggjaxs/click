"""Live lessons on the computer side: reading what's on screen, and pointing (never clicking)."""
import asyncio
import threading

from companion.platforms import Element, Platform, Rect, Window
from companion.screen import ElementCache
from companion import tutor


class FakePlatform(Platform):
    def __init__(self, els, app="Mail"):
        self.els, self.app = els, app

    def active_window(self):
        return Window(1, "Inbox", self.app, Rect(100, 100, 1000, 700), 42, True)

    def ui_elements(self, window, max_elements=300):
        return self.els

    def app_menus(self):
        return [Element("File", "MenuBarItem", Rect(60, 0, 30, 22))]

    def dock_items(self):
        return [Element("Mail", "DockItem", Rect(500, 860, 50, 50))]


class FakeScreen:
    def __init__(self, ocr=None):
        self.elements = ElementCache()
        self.ocr = ocr or []

    def capture_image(self, target, region):
        from PIL import Image

        return Image.new("RGB", (region[2], region[3])), Rect(*region), [], None

    def element_rect(self, eid):
        return self.elements.get(eid).rect


SETTINGS = {"blocklist": ["1password"], "lesson_voice": False}


def test_snapshot_lists_the_real_buttons_with_where_they_are():
    els = [Element("New Message", "Button", Rect(400, 110, 80, 24)),
           Element("Password", "TextField", Rect(400, 300, 200, 24), is_password=True),
           Element("Far away", "Button", Rect(3000, 3000, 50, 20))] + \
          [Element(f"Mailbox {i}", "Row", Rect(110, 200 + 30 * i, 150, 24)) for i in range(10)]
    snap = tutor.collect(FakePlatform(els), FakeScreen(), SETTINGS, "send an email")
    names = [c["name"] for c in snap["controls"]]
    assert "New Message" in names and "File" in names
    assert "Password" not in names and "Far away" not in names  # no passwords, nothing outside the window
    assert snap["window"] == [100, 100, 1000, 700] and snap["source"] == "accessibility"
    nm = next(c for c in snap["controls"] if c["name"] == "New Message")
    assert nm["area"] == "top" and nm["rect"] == [400, 110, 80, 24]
    assert next(c for c in snap["controls"] if c["name"] == "Mailbox 5")["area"] == "left side"


def test_apps_without_button_info_are_read_from_the_screen(monkeypatch):
    from companion import ocr

    monkeypatch.setattr(ocr, "recognize", lambda img, fast=True: [
        {"text": "Add", "x": 200, "y": 10, "w": 30, "h": 14, "conf": 0.9},
        {"text": "Object", "x": 240, "y": 10, "w": 50, "h": 14, "conf": 0.9}])
    snap = tutor.collect(FakePlatform([], app="Blender"), FakeScreen(), SETTINGS, "add a cube")
    add = next(c for c in snap["controls"] if c["name"] == "Add")
    assert snap["source"] == "text on screen" and add["rect"][:2] == [300, 110]  # placed in screen coordinates


def test_blocklisted_apps_are_never_read():
    snap = tutor.collect(FakePlatform([Element("Vault", "Button", Rect(200, 200, 50, 20))], app="1Password"),
                         FakeScreen(), SETTINGS)
    assert snap["controls"] == [] and "private" in snap["note"]


class Sig:
    def __init__(self, log, name):
        self.log, self.name = log, name

    def emit(self, *a):
        self.log.append((self.name,) + a)


class FakeApp:
    def __init__(self, platform, screen):
        self.log = []
        self.platform, self.screen, self.settings = platform, screen, SETTINGS
        self.bridge = type("B", (), {k: Sig(self.log, k) for k in ("ghostMove", "ghostHighlight", "narrate",
                                                                     "ghostRest")})()
        self.ghost = type("G", (), {"x": 0.0, "y": 0.0, "visible": True, "hold": False,
                                    "primary_center": lambda self: (500, 400)})()
        self.actions = type("A", (), {"killed": threading.Event()})()
        self.lesson = {}


def test_clicky_arrives_first_then_the_words_and_never_clicks():
    screen = FakeScreen()
    eid = screen.elements.add(Element("New Message", "Button", Rect(400, 110, 80, 24)))
    app = FakeApp(FakePlatform([]), screen)
    t = tutor.Tutor(app)
    t.begin("How to send an email")

    def wait(rect, timeout, lesson, kind):  # no real mouse here: the user says "next" instead
        threading.Timer(0.2, lambda: (lesson.update(cmd="next"), lesson["interrupt"].set())).start()
        lesson["interrupt"].wait(3)
        return "command"

    t._watch = wait
    res = asyncio.run(t.show_step({"step": 1, "say": "Click New Message.", "element_id": eid, "kind": "click"}))
    kinds = [e[0] for e in app.log]
    assert kinds[:3] == ["ghostMove", "ghostHighlight", "ghostMove"]
    assert app.log[0][3] == "" and "Click New Message" in app.log[2][3]  # silent glide, then the words
    assert app.log[1][1:] == (400.0, 110.0, 80.0, 24.0, "__step__")  # the box is exactly the button
    assert res["result"] == "next"
    assert not hasattr(app, "clicked")


def test_open_app_step_waits_for_you_to_open_it():
    plat = FakePlatform([], app="Finder")
    app = FakeApp(plat, FakeScreen())
    t = tutor.Tutor(app)
    t.begin("x")

    async def go():
        async def later():
            await asyncio.sleep(0.5)
            plat.app = "Mail"  # the user clicked Mail in the Dock
        asyncio.ensure_future(later())
        return await t.show_step({"step": 1, "say": "Open Mail.", "open_app": "Mail", "kind": "look", "timeout": 5})

    res = asyncio.run(go())
    assert res["result"] == "app_open"
    assert app.log[1][0] == "ghostHighlight" and app.log[1][1:3] == (500.0, 860.0)  # pointed at the Dock icon


class SigPlatform(FakePlatform):
    """The screen's fingerprint changes when `changed` is set (a menu opened, the user typed...)."""

    def __init__(self):
        super().__init__([])
        self.sig = (("Mail", (), 0), 0)

    def screen_signature(self):
        return self.sig


def _tutor_with(plat):
    app = FakeApp(plat, FakeScreen())
    t = tutor.Tutor(app)
    t.begin("x")
    return app, t


def test_moves_on_by_itself_when_the_screen_changes():
    plat = SigPlatform()
    app, t = _tutor_with(plat)
    threading.Timer(0.4, lambda: setattr(plat, "sig", (("Mail", (("New Message", 60, 40),), 0), 0))).start()
    t0 = __import__("time").time()
    res = asyncio.run(t._wait({"kind": "click", "timeout": 10}, Rect(1, 1, 5, 5), None, app.lesson))
    assert res["result"] == "next" and res["how"] == "changed"
    assert __import__("time").time() - t0 < 1.5  # right away, not after a fixed pause


def test_typing_step_moves_on_after_you_type_and_pause():
    plat = SigPlatform()
    app, t = _tutor_with(plat)

    def typing():
        import time as _tt
        for n in (3, 8, 14):
            plat.sig = (plat.sig[0], n)  # only how MUCH is in the box is known, never what
            _tt.sleep(0.3)

    threading.Thread(target=typing).start()
    res = asyncio.run(t._wait({"kind": "type", "timeout": 10}, Rect(1, 1, 5, 5), None, app.lesson))
    assert res["result"] == "next" and res["how"] == "typed"


def test_explain_only_steps_carry_on_after_reading_time():
    plat = SigPlatform()
    app, t = _tutor_with(plat)
    t0 = __import__("time").time()
    res = asyncio.run(t._wait({"kind": "look", "say": "Look at the panel.", "timeout": 30}, None, None, app.lesson))
    assert res["result"] == "next" and __import__("time").time() - t0 < 6


def test_smart_mode_uses_the_bigger_model_only_when_it_is_here():
    from companion.hardware import pick_models
    from companion.inference import Inference

    class S(dict):
        def __getitem__(self, k):
            return dict.get(self, k, {})

    info = {"tier": "intel_mac", "device": "cpu", "ram_gb": 32, "vram_gb": 0}
    models = pick_models(info, S(models={}, image={"enabled": None}))
    assert models["chat"] == "qwen2.5:1.5b" and models["smart"] == "qwen2.5:7b"
    small = pick_models({**info, "ram_gb": 8}, S(models={}, image={"enabled": None}))
    assert small["smart"] == small["chat"]  # not enough memory: no big model
    settings = S(ollama_url="http://127.0.0.1:1")
    inf = Inference(settings, models)
    assert inf.smart_model() is None  # not downloaded yet: the fast model answers meanwhile
    inf._installed = {"qwen2.5:7b", "qwen2.5:1.5b"}
    assert inf.smart_model() == "qwen2.5:7b"
    settings["smart_mode"] = False
    assert inf.smart_model() is None  # switched off in the menu bar
