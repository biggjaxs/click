import asyncio, sys, time
sys.argv = sys.argv[:1]
sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..', '..', 'pi-hub')); sys.path.insert(0, "" + __import__('os').path.dirname(__import__('os').path.abspath(__file__)) + "")
from llmchat import chat
from app.tutor import Lesson
T = lambda *n: [{"name": x, "role": "Text"} for x in n]
from menus import M
MBAR = lambda app, open_=None: [{"name": k, "role": "MenuBarItem", "items": v} for k, v in M[app].items()]
SCREENS2 = {
 "desk": [],
 "safari": MBAR("Safari") + [{"name": n, "role": "Button"} for n in ("Back", "Forward", "Sidebar", "Share", "New Tab")],
 "file": MBAR("Safari") + [{"name": n, "role": "MenuItem"} for n in M["Safari"]["File"]],
 "private": MBAR("Safari") + [{"name": n, "role": "Button"} for n in ("Back", "Forward", "Share", "New Tab")] + [{"name": "Private Browsing", "role": "Text"}],
}
AFTER2 = {"open:Safari": "safari", "safari:File": "file", "file:New Private Window": "private"}
SCREENS = {
 "desk": [],
 "viewport": T("File", "Edit", "Render", "Window", "Help", "Layout", "Modeling", "Object Mode", "View", "Select", "Add", "Object", "Scene Collection", "Camera", "Light"),
 "add": T("Mesh", "Curve", "Surface", "Metaball", "Text", "Volume", "Armature", "Empty", "Image", "Light", "Camera"),
 "mesh": T("Plane", "Cube", "Circle", "UV Sphere", "Ico Sphere", "Cylinder", "Cone", "Torus", "Monkey"),
 "done": T("File", "Edit", "Render", "Window", "Help", "Object Mode", "View", "Select", "Add", "Object", "Scene Collection", "Camera", "Cube", "Light", "Add Cube", "Size", "Location"),
}
AFTER = {"open:Blender": "viewport", "viewport:Add": "add", "add:Mesh": "mesh", "mesh:Cube": "done"}
class Link:
    def __init__(s): s.cur, s.front, s.log, s.t0 = "desk", "Finder", [], time.time()
    async def request(s, svc, act, p, timeout=0):
        if (svc, act) == ("screen", "snapshot"):
            return {"app": s.front, "title": "", "window": [0, 25, 1400, 850], "source": "text on screen",
                    "controls": [dict(c, id=f"{s.cur}:{c['name']}", rect=[1, 1, 9, 9], area="top") for c in SCREENS[s.cur]]}
        if p.get("op") != "step":
            return {"ok": True}
        s.log.append(f"{time.time()-s.t0:5.1f}s  step {p['step']}: {p['say']!r} -> {p.get('element_id') or p.get('open_app') or p.get('area')}")
        if p.get("open_app"):
            s.front, s.cur = p["open_app"], AFTER["open:" + p["open_app"]]; return {"result": "app_open"}
        name = (p.get("element_id") or "").split(":", 1)[-1]
        nxt = AFTER.get(f"{s.cur}:{name}")
        if nxt:
            s.cur = nxt; return {"result": "clicked"}
        return {"result": "next"}
async def send(ev): pass
async def main():
    global SCREENS, AFTER
    if len(sys.argv) > 1 or True:
        pass
    import os
    if os.environ.get("SC") == "safari":
        SCREENS, AFTER = SCREENS2, AFTER2
        goal = "open a private window in safari"
    else:
        goal = "add a cube in blender"
    link = Link()
    import llmchat as _l
    async def smart(m, t=None, **kw):
        _l.MODEL = "qwen2.5:7b"
        async for ev in _l.chat(m, t, **kw):
            yield ev
    def fast(m, t=None, **kw):
        _l.MODEL = "qwen2.5:1.5b"
        return _l.chat(m, t, **kw)
    L = Lesson("How to " + goal, goal, None, link, fast, send, installed=["Blender", "Safari"], smart_chat=smart)
    out = await L.run()
    print("\n".join(link.log)); print(out.splitlines()[-1]); print("AI times (ms):", L.llm_ms)
asyncio.run(main())
