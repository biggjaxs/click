import asyncio, sys, time
sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..', '..', 'pi-hub')); sys.path.insert(0, "" + __import__('os').path.dirname(__import__('os').path.abspath(__file__)) + "")
import llmchat
from obscure import APPS, CASES
from app.tutor import Lesson

def fast(msgs, tools=None, **kw):
    llmchat.MODEL = "qwen2.5:1.5b"; return llmchat.chat(msgs, tools, **kw)
async def smart(msgs, tools=None, **kw):
    llmchat.MODEL = SMART
    async for ev in llmchat.chat(msgs, tools, **kw):
        yield ev

class Mac:
    def __init__(s, app):
        s.app, s.menu, s.sub, s.done, s.log, s.t0, s.clicked = app, None, None, None, [], time.time(), []
    def controls(s):
        menus, buttons = APPS[s.app]
        out = [{"name": k, "role": "MenuBarItem", "items": v, "id": f"bar:{k}", "area": "menu bar"} for k, v in menus.items()]
        out += [{"name": b, "role": "Button", "id": f"btn:{b}", "area": "top"} for b in buttons]
        if s.menu:
            seen = []
            for it in menus[s.menu]:
                top = it.split(" > ")[0]
                if top not in seen:
                    seen.append(top); out.append({"name": top, "role": "MenuItem", "id": f"item:{top}"})
            if s.sub:
                for it in menus[s.menu]:
                    if it.startswith(s.sub + " > "):
                        leaf = it.split(" > ")[1]; out.append({"name": leaf, "role": "MenuItem", "id": f"item:{leaf}"})
        return out
    async def request(s, svc, act, p, timeout=0):
        if (svc, act) == ("screen", "snapshot"):
            return {"app": s.app, "title": "", "window": [0, 25, 1400, 850], "controls": s.controls(), "source": "accessibility"}
        if p.get("op") != "step":
            return {"ok": True}
        eid = p.get("element_id") or ""
        s.log.append(f"{time.time()-s.t0:5.1f}s  {p['say']!r} -> {eid or p.get('area') or '(nothing)'}")
        kind, _, name = eid.partition(":")
        menus = APPS[s.app][0]
        if kind == "bar":
            s.menu, s.sub = name, None; return {"result": "clicked"}
        if kind == "item":
            if s.menu and any(it.startswith(name + " > ") for it in menus[s.menu]) and s.sub is None:
                s.sub = name; return {"result": "next"}
            s.done, s.menu, s.sub = name, None, None; s.clicked.append(name); return {"result": "clicked"}
        if kind == "btn":
            s.done = name; s.clicked.append(name); return {"result": "clicked"}
        return {"result": "next"}
async def send(ev): pass
SMART = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5:7b"
async def main():
    ok = 0
    for app, goal, want in CASES:
        mac = Mac(app)
        L = Lesson("How to " + goal, goal, None, mac, fast, send, installed=[app], smart_chat=smart)
        t = time.time()
        await L.run()
        good = any(want.lower() in c.lower() for c in mac.clicked)
        ok += good
        print(f"{'OK ' if good else 'BAD'} {app:9} {goal:38} did {' / '.join(mac.clicked)[:40]!r:42} plan {getattr(L,'plan_ms',0)/1000:4.1f}s, steps {len(mac.log)}, step-AI calls {len(L.llm_ms)}")
        if not good:
            print("      " + "\n      ".join(mac.log[:6]))
    print(f"{ok}/{len(CASES)} lessons reached the right command [{SMART} plans]")
asyncio.run(main())
