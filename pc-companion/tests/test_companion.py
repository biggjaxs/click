import pytest

from companion.files import FileAccessError, FileService
from companion.safety import is_blocked, looks_like_secret, risky_keys


class S(dict):
    pass


def test_blocklist():
    bl = ["1Password", "bank", "paypal", "amex"]
    assert is_blocked("Vault - 1Password", "1Password", bl)
    assert is_blocked("Chase Bank - Accounts", "chrome", bl)
    assert is_blocked("PayPal: Summary", "firefox", bl)
    assert not is_blocked("Riverbank Park photos", "Photos", bl)  # short words match whole words only
    assert not is_blocked("Notes", "Notes", bl)


def test_secrets_and_keys():
    assert looks_like_secret("4111 1111 1111 1111")
    assert not looks_like_secret("hello world 12345")
    assert risky_keys("alt+f4") and risky_keys("Enter") and not risky_keys("ctrl+s")


def test_files_only_inside_approved_folders(tmp_path):
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "notes.txt").write_text("buy milk")
    (docs / "sub").mkdir()
    (docs / "sub" / "invoice-march.csv").write_text("item,price\nmilk,2")
    secret = tmp_path / "secret.txt"
    secret.write_text("nope")
    fs = FileService(S(approved_folders=[str(docs)], permissions={"file_write": True}))
    assert fs.read_file(str(docs / "notes.txt"))["text"] == "buy milk"
    assert fs.read_file("notes.txt")["text"] == "buy milk"            # relative to an approved folder
    assert fs.read_file("Documents/notes.txt")["text"] == "buy milk"  # or with its name
    with pytest.raises(FileAccessError):
        fs.read_file(str(secret))
    with pytest.raises(FileAccessError):
        fs.read_file(str(docs / ".." / "secret.txt"))                 # no escaping with ..
    (docs / "link.txt").symlink_to(secret)
    with pytest.raises(FileAccessError):
        fs.read_file(str(docs / "link.txt"))                          # no escaping with symlinks
    names = [r["name"] for r in fs.search_files("invoice")["results"]]
    assert names == ["invoice-march.csv"]
    hits = fs.search_files("milk", by="content")["results"]
    assert {h["name"] for h in hits} == {"notes.txt", "invoice-march.csv"}
    res = fs.rename_file(str(docs / "notes.txt"), "shopping.txt")
    assert (docs / "shopping.txt").exists() and res["status"] == "renamed"
    with pytest.raises(FileAccessError):
        fs.rename_file(str(docs / "shopping.txt"), "../escape.txt")
    fs.settings["permissions"]["file_write"] = False
    with pytest.raises(FileAccessError):
        fs.move_file(str(docs / "shopping.txt"), str(docs / "sub"))


def test_screen_matching_windows_and_mac_dpi():
    from companion.gui import match_screens

    mss = [{"left": 0, "top": 0, "width": 2880, "height": 1620},      # 4K-ish laptop at 150%
           {"left": 2880, "top": 0, "width": 1920, "height": 1080}]   # second monitor at 100%
    # Windows Qt6: native top-left, logical size
    qt = [(0, 0, 1920, 1080, 1.5), (2880, 0, 1920, 1080, 1.0)]
    m = match_screens(qt, mss)
    assert m[0][1]["left"] == 0 and abs(m[0][2] - 1.5) < 1e-6
    assert m[1][1]["left"] == 2880 and abs(m[1][2] - 1.0) < 1e-6
    # macOS: everything in points, Retina doesn't change coordinates
    mac_mss = [{"left": 0, "top": 0, "width": 1512, "height": 982}, {"left": -1920, "top": 0, "width": 1920, "height": 1080}]
    mac_qt = [(0, 0, 1512, 982, 2.0), (-1920, 0, 1920, 1080, 1.0)]
    m2 = match_screens(mac_qt, mac_mss)
    assert m2[0][1]["left"] == 0 and m2[0][2] == 1.0
    assert m2[1][1]["left"] == -1920 and m2[1][2] == 1.0


def test_parse_keys():
    pytest.importorskip("pynput")
    try:
        from companion.actions import parse_keys

        keys = parse_keys("ctrl+shift+s")
    except Exception as exc:  # pynput needs a display backend on Linux
        pytest.skip(f"pynput unavailable here: {exc}")
    assert len(keys) == 3


def test_hardware_tiers():
    from companion.hardware import pick_models

    class St(dict):
        pass

    st = St(models={"chat": None, "vision": None}, image={"enabled": None, "draft_model": None, "quality_model": None})
    cpu = pick_models({"tier": "cpu", "device": "cpu", "vram_gb": 0}, st)
    assert cpu["chat"] == cpu["vision"] == "qwen3-vl:4b-instruct" and not cpu["image_enabled"]
    big = pick_models({"tier": "gpu_big", "device": "cuda", "vram_gb": 24}, st)
    assert big["image_enabled"] and "FLUX" in big["image_quality"]
    small = pick_models({"tier": "cpu", "device": "cuda", "vram_gb": 6}, st)
    assert small["image_enabled"] and small["image_quality"] == "stabilityai/sd-turbo"
    st["models"]["chat"] = "my-model"
    assert pick_models({"tier": "cpu", "device": "cpu", "vram_gb": 0}, st)["chat"] == "my-model"


def test_runs_on_this_computer_unless_a_box_was_really_paired():
    """Regression: a leftover pairing from older versions made Click look for a Pi that didn't exist."""
    from companion.app import App

    class S(dict):
        pass

    mode = App.mode.fget
    leftover = type("X", (), {"settings": S(pi_cert_pem="CERT", standalone_paired=False)})()
    assert mode(leftover) == "standalone"
    half = type("X", (), {"settings": S(mode="box", pi_cert_pem="CERT")})()
    assert mode(half) == "standalone"
    real_box = type("X", (), {"settings": S(mode="box", box_paired=True, pi_cert_pem="CERT")})()
    assert mode(real_box) == "box"


def test_find_prefers_dock_app_and_never_points_at_clicks_own_chat():
    """Regression: 'show me where system settings is' pointed at Click's own chat (which contained the words)."""
    from companion.platforms import Element, Platform, Rect
    from companion.screen import ScreenService

    chat = Rect(1100, 80, 400, 300)

    class FakeMac(Platform):
        def dock_items(self):
            return [Element("Finder", "DockItem", Rect(400, 940, 50, 50), window_title="Dock"),
                    Element("System Settings", "DockItem", Rect(800, 940, 50, 50), window_title="Dock")]

        def menu_bar_items(self):
            return [Element("Apple", "MenuBarItem", Rect(10, 0, 20, 24), window_title="Menu bar")]

        def list_windows(self):
            return []

    s = ScreenService(FakeMac(), {"blocklist": []})
    s.own_rects = lambda: [chat]
    s.geometry_fn = lambda: None  # this test is about Accessibility results, not the real Mac's layout
    s._find_by_text = lambda q: [(1.0, Element("system settings", "Text", Rect(1150, 120, 120, 16)))]  # in the chat
    res = s.find_ui_element("system settings")
    assert res["matches"][0]["name"] == "System Settings" and res["matches"][0]["where"] == "Dock"
    assert all(m["rect"][0] < 1100 for m in res["matches"])  # nothing inside Click's chat

    # not in the Dock -> the Apple menu, with a note
    class NoDock(FakeMac):
        def dock_items(self):
            return []

    s2 = ScreenService(NoDock(), {"blocklist": []})
    s2.own_rects = lambda: [chat]
    s2.geometry_fn = lambda: None
    s2._find_by_text = lambda q: [(1.0, Element("system settings", "Text", Rect(1150, 120, 120, 16)))]
    res2 = s2.find_ui_element("system settings")
    assert res2["matches"][0]["name"] == "Apple" and "Apple menu" in res2["matches"][0]["note"]


def test_desktop_icons_are_found():
    from companion.platforms import Element, Platform, Rect
    from companion.screen import ScreenService

    class FakeMac(Platform):
        def desktop_items(self):
            return [Element("Budget 2026", "DesktopIcon", Rect(1300, 400, 70, 80), window_title="Desktop"),
                    Element("Screenshots", "DesktopIcon", Rect(1300, 520, 70, 80), window_title="Desktop")]

    s = ScreenService(FakeMac(), {"blocklist": []})
    s._find_by_text = lambda q: []
    res = s.find_ui_element("budget")
    assert res["matches"][0]["name"] == "Budget 2026" and res["matches"][0]["where"] == "Desktop"


def test_skill_descriptions_and_app_names():
    from companion.skills import _as_text, app_name, describe

    assert app_name("system settings") == "System Settings" and app_name("music") == "Music"
    assert app_name("google chrome") == "Google Chrome"
    assert describe({"skill": "play_music", "query": "Levitating"}) == "Play “Levitating” in Music"
    assert _as_text('say "hi"') == 'say \\"hi\\"'


def _mac():
    from companion.macmap import MacGeometry
    from companion.platforms import Rect

    return MacGeometry(screen=Rect(0, 0, 1440, 900), menu_bar_h=24, dock_side="bottom", dock_thickness=70,
                       tilesize=48, dock_apps=["Safari", "Messages", "Music", "System Settings"],
                       dock_middle=["Notes"], dock_others=["Downloads"], window=Rect(200, 100, 800, 600),
                       window_app="Safari")


def test_mac_map_knows_where_things_are():
    from companion.macmap import locate, match

    g = _mac()
    assert match("my budget folder") is None and match("the weather") is None  # never hijacks normal things
    apple = locate("apple menu", g)
    assert apple.rect.x < 50 and apple.rect.y == 0
    wifi = locate("where's my wifi icon".replace("where's ", ""), g)
    assert wifi.rect.x > 1100 and wifi.rect.y == 0 and "Wi" in wifi.extra["note"]
    assert locate("bluetooth", g).extra["note"].startswith("Bluetooth is in Control Center")
    finder, trash = locate("finder", g), locate("the trash", g)
    assert finder.rect.y > 800 and trash.rect.y > 800 and finder.rect.x < trash.rect.x
    music = locate("system settings", g)  # pinned in the Dock: points there, not at the Apple menu
    assert music.window_title == "Dock"
    assert locate("close button", g).rect.x < 230
    g.dock_apps = []
    assert locate("system settings", g).window_title == "Menu bar"  # not in the Dock: the Apple menu


def test_find_uses_mac_map_without_permissions(monkeypatch):
    from companion.screen import ScreenService

    class P:  # a Mac where Accessibility shows nothing (permission off)
        def __getattr__(self, name):
            return lambda *a, **k: []

        def check_permissions(self):
            return ["Accessibility is off."]

    svc = ScreenService(P(), {"blocklist": []})
    svc.geometry_fn = _mac
    monkeypatch.setattr(svc, "_find_by_text", lambda q: [])
    out = svc.find_ui_element("trash")
    assert out["matches"] and out["found_by"] == "mac map"


def test_fuzzy_app_names_like_spotlight():
    from companion.skills import best_app

    apps = {n.lower(): f"/Applications/{n}.app" for n in
            ("Messages", "Spotify", "Google Chrome", "System Settings", "Notes", "Numbers", "Safari", "Music")}
    assert best_app("spotfy", apps).endswith("Spotify.app")
    assert best_app("msg", apps).endswith("Messages.app")
    assert best_app("chrome", apps).endswith("Google Chrome.app")
    assert best_app("the spotify app", apps).endswith("Spotify.app")
    assert best_app("settings", apps).endswith("System Settings.app")
    assert best_app("zzzz", apps) is None


def test_mac_typing_keys_are_known():
    from companion.macinput import KEYCODES, MODS

    for k in ("v", "n", "return", "esc", "space", "tab", "left"):
        assert k in KEYCODES
    assert MODS["cmd"] == 1 << 20 and MODS["shift"] == 1 << 17


def test_screen_layout_says_where_things_are():
    from PIL import Image

    from companion.platforms import Element, Platform, Rect, Window
    from companion.screen import ScreenService

    class P(Platform):
        def ui_elements(self, w):
            return [Element("New Message", "Button", Rect(20, 60, 80, 30)), Element("Send", "Button", Rect(1300, 60, 60, 30))]

        def dock_items(self):
            return [Element("Mail", "DockItem", Rect(600, 860, 50, 50))]

    svc = ScreenService(P(), {"blocklist": []})
    lines = [{"text": "Inbox", "x": 10, "y": 10, "w": 60, "h": 20, "conf": 0.9},
             {"text": "Hello Sam", "x": 700, "y": 450, "w": 100, "h": 20, "conf": 0.9}]
    out = svc._layout(lines, Image.new("RGB", (1440, 900)), Rect(0, 0, 1440, 900),
                      Window(1, "Inbox", "Mail", Rect(0, 0, 1440, 900)))
    assert "[top-left] Inbox" in out and "[middle] Hello Sam" in out
    assert "New Message (button, top-left)" in out and "Send (button, top-right)" in out and "Dock: Mail" in out
