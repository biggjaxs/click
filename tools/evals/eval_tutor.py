import asyncio, json, sys, time
import httpx
sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..', '..', 'pi-hub'))
from app.tutor import Lesson

MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5:1.5b"

async def chat(msgs, tools=None, fmt=None, options=None):
    body = {"model": MODEL, "messages": msgs, "stream": True, "format": fmt, "options": dict(options or {}, num_thread=4),
            "keep_alive": "1h"}
    async with httpx.AsyncClient(timeout=300) as c:
        async with c.stream("POST", "http://127.0.0.1:11434/api/chat", json=body) as r:
            async for line in r.aiter_lines():
                if line:
                    d = json.loads(line)
                    t = d.get("message", {}).get("content", "")
                    if t:
                        yield {"type": "token", "text": t}

def ctl(names, role="Button", area="top"):
    out = []
    for n in names:
        if isinstance(n, tuple):
            out.append({"name": n[0], "role": n[1], "area": n[2]})
        else:
            out.append({"name": n, "role": role, "area": area})
    return out

MENUBAR = lambda *m: [{"name": x, "role": "MenuBarItem", "area": "menu bar"} for x in m]
CASES = [
 ("add a cube in blender", [], "Blender", MENUBAR() + ctl(["File","Edit","Render","Window","Help","Layout","Modeling","Sculpting","UV Editing","Texture Paint","Shading","Animation","Object Mode","View","Select","Add","Object","Global","Scene Collection","Collection","Camera","Cube","Light","Properties","Timeline","Playback","Keying","Frame"], "Text"), {"Add"}),
 ("add a cube in blender", [("Click Add at the top.", True)], "Blender", ctl(["Mesh","Curve","Surface","Metaball","Text","Volume","Grease Pencil","Armature","Lattice","Empty","Image","Light","Light Probe","Camera","Speaker","Force Field","Collection Instance"], "Text", "middle"), {"Mesh"}),
 ("add a cube in blender", [("Click Add at the top.", True), ("Choose Mesh.", True)], "Blender", ctl(["Plane","Cube","Circle","UV Sphere","Ico Sphere","Cylinder","Cone","Torus","Grid","Monkey"], "Text", "middle"), {"Cube"}),
 ("send an email", [], "Mail", MENUBAR("Mail","File","Edit","View","Mailbox","Message","Format","Window","Help") + ctl([("Get Mail","Button","top"),("New Message","Button","top"),("Archive","Button","top"),("Delete","Button","top"),("Junk","Button","top"),("Reply","Button","top"),("Reply All","Button","top"),("Forward","Button","top"),("Flag","MenuButton","top"),("Search","SearchField","top-right"),("Inbox","Row","left side"),("Sent","Row","left side"),("Drafts","Row","left side"),("Trash","Row","left side")]), {"New Message", "File"}),
 ("make a new folder", [], "Finder", MENUBAR("Finder","File","Edit","View","Go","Window","Help") + ctl([("Back","Button","top-left"),("Forward","Button","top-left"),("View","PopUpButton","top"),("Share","Button","top-right"),("Search","SearchField","top-right"),("Recents","Row","left side"),("Applications","Row","left side"),("Desktop","Row","left side"),("Documents","Row","left side"),("Downloads","Row","left side")]), {"File"}),
 ("make a new folder", [("Click File in the menu bar.", True)], "Finder", MENUBAR("Finder","File","Edit","View","Go","Window","Help") + ctl([("New Finder Window","MenuItem","menu bar"),("New Folder","MenuItem","menu bar"),("New Folder with Selection","MenuItem","menu bar"),("New Smart Folder","MenuItem","menu bar"),("New Tab","MenuItem","menu bar"),("Open","MenuItem","menu bar"),("Close Window","MenuItem","menu bar"),("Get Info","MenuItem","menu bar"),("Rename","MenuItem","menu bar")]), {"New Folder"}),
 ("crop a photo in preview", [], "Preview", MENUBAR("Preview","File","Edit","View","Go","Tools","Window","Help") + ctl([("Sidebar","MenuButton","top-left"),("Zoom Out","Button","top"),("Zoom In","Button","top"),("Share","Button","top-right"),("Show Markup Toolbar","Button","top-right"),("Rotate","Button","top-right"),("Search","SearchField","top-right")]), {"Show Markup Toolbar","Tools"}),
 ("turn on dark mode", [], "System Settings", ctl([("Search","SearchField","top-left"),("Wi-Fi","Row","left side"),("Bluetooth","Row","left side"),("Network","Row","left side"),("Notifications","Row","left side"),("Sound","Row","left side"),("Focus","Row","left side"),("General","Row","left side"),("Appearance","Row","left side"),("Accessibility","Row","left side"),("Control Center","Row","left side"),("Desktop & Dock","Row","left side"),("Displays","Row","left side"),("Wallpaper","Row","left side"),("Battery","Row","left side")]), {"Appearance"}),
 ("bookmark this page in safari", [], "Safari", MENUBAR("Safari","File","Edit","View","History","Bookmarks","Window","Help") + ctl([("Back","Button","top-left"),("Forward","Button","top-left"),("Sidebar","Button","top-left"),("Address and search","TextField","top"),("Share","Button","top-right"),("New Tab","Button","top-right"),("Tab Overview","Button","top-right")]), {"Bookmarks","Share"}),
 ("make a checklist in notes", [], "Notes", MENUBAR("Notes","File","Edit","Format","View","Window","Help") + ctl([("Sidebar","Button","top-left"),("Delete","Button","top"),("Compose","Button","top"),("Format","Button","top"),("Checklist","Button","top"),("Table","Button","top"),("Attachments","Button","top"),("Share","Button","top-right"),("Search","SearchField","top-right"),("Shopping list","Row","left side")]), {"Checklist", "Format"}),
 ("make a chart in excel", [], "Microsoft Excel", MENUBAR("Excel","File","Edit","View","Insert","Format","Tools","Data","Window","Help") + ctl([("Home","RadioButton","top"),("Insert","RadioButton","top"),("Draw","RadioButton","top"),("Page Layout","RadioButton","top"),("Formulas","RadioButton","top"),("Data","RadioButton","top"),("Review","RadioButton","top"),("View","RadioButton","top"),("Paste","Button","top-left"),("Bold","Button","top-left"),("Sum","Button","top-right"),("Sort & Filter","Button","top-right")]), {"Insert"}),
]

async def main():
    from menus import add_items
    add_items(CASES)
    ok = 0
    times = []
    for goal, hist, app, controls, want in CASES:
        for i, c in enumerate(controls):
            c["id"] = f"e{i}"
        snap = {"app": app, "title": "", "window": [0, 25, 1400, 850], "controls": controls, "source": "accessibility"}
        L = Lesson("x", goal, None, None, chat, None)
        L.history = [{"say": s, "done": d} for s, d in hist]
        t = time.time()
        ch = await L.ask_ai(snap)
        dt = time.time() - t
        times.append(dt)
        got = (ch or {}).get("control", {}).get("name") or (ch or {}).get("area")
        good = got in want
        ok += good
        print(f"{'OK ' if good else 'BAD'} {dt:5.1f}s  {goal!r:36} step {len(hist)+1}: -> {got!r:22} | {(ch or {}).get('say')}")
    print(f"\n{ok}/{len(CASES)} correct, median {sorted(times)[len(times)//2]:.1f}s, first {times[0]:.1f}s (incl. load)")

asyncio.run(main())
