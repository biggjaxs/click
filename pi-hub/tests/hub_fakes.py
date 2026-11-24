"""Test doubles shared by the Pi Hub tests."""
import hashlib
import re

import numpy as np


async def fake_embed(texts):
    """Deterministic bag-of-words embedding so tests don't need Ollama."""
    out = []
    for t in texts:
        v = np.zeros(256, dtype=np.float32)
        for w in re.findall(r"[a-z0-9]+", t.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1
        out.append(v.tolist())
    return out


class FakeBackend:
    """Scripted chat backend: each call pops the next list of events."""
    source = "pc"
    model = "fake-model"

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = []

    async def chat_stream(self, messages, tools=None, **kw):
        self.calls.append({"messages": messages, "tools": tools, **kw})
        script = self.scripts.pop(0) if self.scripts else [{"type": "token", "text": "ok"}]
        for ev in script:
            yield ev
        yield {"type": "done", "model": self.model, "source": self.source}
