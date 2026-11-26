import json, sys
import httpx
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

