import asyncio, json, sys, time
sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..', '..', 'pi-hub')); sys.path.insert(0, "" + __import__('os').path.dirname(__import__('os').path.abspath(__file__)) + "")
import llmchat
from app import language
llmchat.MODEL = sys.argv[1]
APPS = ["Safari", "Notes", "Music", "Mail", "Messages", "Calendar", "Reminders", "Spotify", "Google Chrome", "Discord",
        "Microsoft Word", "Photos", "Finder", "System Settings", "Blender", "Zoom", "Slack", "Preview", "TextEdit"]
# (request, expected skills in order, optional check on fields)
CASES = [
 ("ugh my screen is way too bright at night can u fix that", ["dark_mode"], None),
 ("i need to remember to call mom tmrw at 5", ["reminder"], None),
 ("throw on some kendrick", ["play_music"], lambda s: "kendrick" in s[0].get("query", "").lower()),
 ("can you get discord up for me", ["open_app"], lambda s: s[0].get("app") == "Discord"),
 ("jot down that the wifi password is on the fridge", ["new_note"], None),
 ("kill spotify its being weird", ["quit_app"], lambda s: s[0].get("app") == "Spotify"),
 ("its too loud", ["volume"], lambda s: s[0].get("action") == "down"),
 ("i wanna write a paragraph in word about climate change", ["open_app"], lambda s: s[0].get("app") == "Microsoft Word"),
 ("shoot sarah a text saying im running late", ["send_message"], lambda s: "sarah" in s[0].get("to", "").lower()),
 ("look up how tall the eiffel tower is", None, None),
 ("i cant focus, block distractions for like half an hour", ["focus"], lambda s: s[0].get("minutes") in (30,)),
 ("take a pic of my screen", ["screenshot"], None),
 ("can you make it so my mac doesnt use dark mode anymore", ["dark_mode"], lambda s: s[0].get("on") is False),
 ("pull up my calendar and also put on some lofi", ["open_app", "play_music"], None),
 ("whats 15 percent of 80", None, None),
 ("tell me a joke", None, None),
 ("why is the sky blue", None, None),
 ("i'm bored", None, None),
]
async def main():
    ok, ts = 0, []
    sysmsg = language.PLANNER_PROMPT.replace("{last_app}", "none").replace("{live}", "nothing running") + "\nApps on this Mac: " + ", ".join(APPS)
    for text, want, check in CASES:
        rule = language.plan(text, language.DialogState()) if hasattr(language, "plan") else None
        out = []
        t = time.time()
        async for ev in llmchat.chat([{"role": "system", "content": sysmsg}, {"role": "user", "content": language.rewrite(text)}], None,
                                     fmt=language.PLAN_SCHEMA, options={"num_predict": 200, "temperature": 0}):
            out.append(ev["text"])
        dt = time.time() - t; ts.append(dt)
        try:
            steps = language.validate_llm_plan(json.loads("".join(out)))
        except Exception:
            steps = None
        got = [s["skill"] for s in steps] if steps else None
        good = (got is None and want is None) or (got is not None and want is not None and
               (got == want or (len(want) > 1 and got[0] in want and len(got) == 1) or got[:len(want)] == want) and (check is None or check(steps)))
        if want and len(want) == 2 and want[0] == "search_web":  # either is fine
            good = got is not None and got[0] in want
        ok += good
        print(f"{'OK ' if good else 'BAD'} {dt:5.1f}s {text[:52]:52} -> {steps}")
    print(f"{ok}/{len(CASES)} [{sys.argv[1]}] median {sorted(ts)[len(ts)//2]:.1f}s")
asyncio.run(main())
