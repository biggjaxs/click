"""End-to-end: real Pi link <-> real PC Companion server over mutual TLS (no GUI, fake AI)."""
import asyncio
import base64
import io
import socket
import ssl
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pc-companion"))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeSettings(dict):
    def __init__(self, tmp, port):
        super().__init__(pc_id="pc123", pc_name="TestPC", port=port, pi_cert_pem=None,
                         permissions={"screen": True, "cursor": True, "actions": True, "files": True,
                                      "file_write": True, "image": True})
        self._tls = tmp / "pctls"
        self._tls.mkdir()

    @property
    def tls_dir(self):
        return self._tls

    def set(self, k, v):
        self[k] = v


class FakeInference:
    async def chat(self, payload, emit):
        for t in ["Hel", "lo ", "from PC"]:
            await emit({"token": t})
        return {"model": "big-pc-model", "tool_calls": []}

    async def describe(self, payload):
        return {"text": "a screenshot"}


class FakeImageGen:
    def generate(self, payload, progress):
        from PIL import Image

        for i in range(1, 4):
            progress({"step": i, "total": 3, "status": "generating"})
            time.sleep(0.01)
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), "purple").save(buf, "PNG")
        return {"png_b64": base64.b64encode(buf.getvalue()).decode(), "seed": payload["seed"],
                "model": "fake-diffusion", "width": 8, "height": 8, "steps": 3}

    def cancel(self, job_id):
        pass


class FakeApp:
    def __init__(self, settings):
        self.settings = settings
        self.paused = False
        self.inference = FakeInference()
        self.imagegen = FakeImageGen()
        self.events = []

    async def capabilities(self):
        return {"models": {"chat": "big-pc-model", "vision": "eyes"}, "image": {"enabled": True, "model": "fake"},
                "os": "TestOS"}

    def on_pi_connection(self, c):
        self.events.append(("conn", c))

    def on_pi_event(self, service, action, payload):
        self.events.append((service, action, payload))

    def notify(self, t):
        pass

    def screen_list_windows(self, p):
        return {"windows": [{"title": "Notes"}]}


def _wait(cond, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_pair_connect_chat_image_and_reject_strangers(services, tmp_path):
    from companion.server import CompanionServer

    port = free_port()
    fs = FakeSettings(tmp_path, port)
    app = FakeApp(fs)
    server = CompanionServer(app)
    server.start()
    services.cfg.manual_pc_host = "127.0.0.1"

    # 1) pairing: code from the Pi, certificate swap
    code = services.pairing.start()["code"]
    with pytest.raises(PermissionError):
        services.pairing.complete("000000" if code != "000000" else "111111", "pc123", "TestPC", "127.0.0.1", port,
                                  server.identity.cert)
    res = services.pairing.complete(code, "pc123", "TestPC", "127.0.0.1", port, server.identity.cert)
    fs.set("pi_cert_pem", res["pi_cert_pem"])
    server.restart_tls()
    time.sleep(0.5)

    async def scenario():
        services.link.start()
        for _ in range(200):
            if services.link.connected:
                break
            await asyncio.sleep(0.05)
        assert services.link.connected, services.link.last_error
        assert services.link.capabilities["models"]["chat"] == "big-pc-model"

        # 2) streamed chat through the orchestrator uses the PC
        events = []

        async def send(ev):
            events.append(ev)

        from app.orchestrator import TurnState

        out = await services.orchestrator.handle(send=send, text="tell me something nice?", conversation_id=None, upload_ids=[],
                                                 incognito_key=None, state=TurnState())
        assert out["answer"] == "Hello from PC" and out["source"] == "pc" and out["model"] == "big-pc-model"
        assert [e["text"] for e in events if e["type"] == "token"] == ["Hel", "lo ", "from PC"]

        # 3) image generation streams progress and lands in the gallery
        events.clear()
        image = await services.images._generate(services.images._params({"prompt": "a purple square", "seed": 42}),
                                                None, send)
        steps = [e.get("step") for e in events if e["type"] == "image_progress" and e.get("step")]
        assert steps == [1, 2, 3]
        assert image["params"]["seed"] == 42 and services.images.path(image["id"]).exists()

        # 4) a plain request/response and an event to the PC
        wins = await services.link.request("screen", "list_windows", {})
        assert wins["windows"][0]["title"] == "Notes"
        await services.link.send_event("system", "settings", {"cursor_color": "#ff0000"})
        await asyncio.sleep(0.2)
        assert any(e[:2] == ("system", "settings") for e in app.events)

        # 5) local permission re-check on the PC
        fs["permissions"]["screen"] = False
        from app.pc_link import PcError

        with pytest.raises(PcError, match="turned off"):
            await services.link.request("screen", "list_windows", {})
        await services.link.stop()

    asyncio.run(scenario())

    # 6) a device with a different (unpaired) certificate is rejected by the PC
    from click_protocol.tls import client_context, generate_identity

    cert, key = generate_identity("stranger")
    ctx = client_context(cert, key, server.identity.cert)

    async def stranger():
        import websockets

        async with websockets.connect(f"wss://127.0.0.1:{port}/click", ssl=ctx, open_timeout=5) as ws:
            await ws.send("{}")
            await ws.recv()

    with pytest.raises((ssl.SSLError, ConnectionError, OSError, Exception)):
        asyncio.run(stranger())

    # 7) the Pi refuses to talk to a PC whose certificate isn't the paired one
    other_cert, _ = generate_identity("fake-pc")
    ctx2 = client_context(services.identity.cert, services.identity.key, other_cert)

    async def wrong_pc():
        import websockets

        async with websockets.connect(f"wss://127.0.0.1:{port}/click", ssl=ctx2, open_timeout=5):
            pass

    with pytest.raises(Exception):
        asyncio.run(wrong_pc())
