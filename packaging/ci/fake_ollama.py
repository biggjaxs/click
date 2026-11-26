"""A stand-in for Ollama (the AI engine) so the build check can start Click for real without downloading models."""
import json, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
MODELS = ["qwen2.5:1.5b", "qwen3-vl:2b-instruct", "nomic-embed-text", "qwen3-vl:4b-instruct"]
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, obj):
        b = json.dumps(obj).encode(); self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path.startswith("/api/tags"): return self._json({"models": [{"name": m, "model": m} for m in MODELS]})
        if self.path.startswith("/api/ps"): return self._json({"models": []})
        if self.path.startswith("/api/version"): return self._json({"version": "0.13.0"})
        self._json({})
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); body = json.loads(self.rfile.read(n) or b"{}")
        if self.path.startswith("/api/embed"):
            inp = body.get("input") or body.get("prompt") or ""
            k = len(inp) if isinstance(inp, list) else 1
            return self._json({"embeddings": [[0.1]*768]*k, "embedding": [0.1]*768})
        if self.path.startswith("/api/generate"): return self._json({"response": "", "done": True})
        if self.path.startswith("/api/chat"):
            msgs = body.get("messages", []); last = msgs[-1]["content"] if msgs else ""
            fmt = body.get("format")
            if fmt and "say" in json.dumps(fmt):
                text = json.dumps({"steps": [{"app": "Blender", "say": "Open Blender."},
                                             {"target": "Add", "say": "In the 3D Viewport, open the Add menu."},
                                             {"target": "Mesh", "say": "Choose Mesh, then Cube."}]})
            elif fmt:
                text = json.dumps({"steps": [{"skill": "none"}]})
            else:
                text = "Paris is the capital of France." if "france" in last.lower() else "Here is a short answer."
            self.send_response(200); self.send_header("Content-Type","application/x-ndjson"); self.end_headers()
            for w in text.split(" "):
                self.wfile.write((json.dumps({"message": {"role": "assistant", "content": w + " "}, "done": False}) + "\n").encode()); self.wfile.flush(); time.sleep(0.02)
            self.wfile.write((json.dumps({"message": {"role": "assistant", "content": ""}, "done": True, "model": body.get("model")}) + "\n").encode())
            return
        self._json({})
ThreadingHTTPServer(("127.0.0.1", 11577), H).serve_forever()
