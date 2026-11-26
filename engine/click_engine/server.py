"""Click Engine server: speaks the same small HTTP language as Ollama, so Click can switch engines without
losing anything (streaming answers, tool calls, JSON-shaped answers, warm-up, model downloads).

    python -m click_engine.server --port 11435 --models ~/Library/Application\\ Support/ClickCompanion/models

Endpoints: GET /api/version, /api/tags, /api/ps; POST /api/chat, /api/generate, /api/pull.

Speed features:
- the model's memory of the conversation is reused between requests (only new words are processed);
- a request with num_predict=1 (what Click sends while you're still typing) just reads the text ahead of time,
  so when you press Enter only the last few words are left;
- answers stream word by word as they're written.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

from .chat import JsonShape, render, split_tool_calls

log = logging.getLogger("click.engine")
VERSION = "click-engine-0.1"

# the models Click Engine knows how to get (open-weights Qwen models, 8-bit files)
CATALOG = {
    "qwen2.5:1.5b": ("qwen2.5-1.5b-instruct-q8_0.gguf",
                     "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q8_0.gguf"),
    "qwen2.5:0.5b": ("qwen2.5-0.5b-instruct-q8_0.gguf",
                     "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q8_0.gguf"),
    "qwen2.5:3b": ("qwen2.5-3b-instruct-q8_0.gguf",
                   "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q8_0.gguf"),
}


def canonical(name: str) -> str:
    n = (name or "").strip().lower()
    return n.removesuffix(":latest")


class Engine:
    def __init__(self, models_dir: Path, n_ctx: int = 4096, threads: int | None = None):
        self.dir = Path(models_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.n_ctx, self.threads = n_ctx, threads
        self.loaded: dict = {}
        self.load_lock = threading.Lock()

    def path_for(self, name: str) -> Path | None:
        n = canonical(name)
        if n in CATALOG:
            p = self.dir / CATALOG[n][0]
            return p if p.exists() else None
        p = self.dir / f"{n}.gguf"
        return p if p.exists() else None

    def available(self) -> list[str]:
        return [n for n in CATALOG if self.path_for(n)]

    def model(self, name: str):
        n = canonical(name)
        with self.load_lock:
            if n not in self.loaded:
                p = self.path_for(n)
                if not p:
                    raise KeyError(n)
                from .model import Model

                self.loaded[n] = Model(str(p), n_ctx=self.n_ctx, threads=self.threads)
            return self.loaded[n]


# ---------------------------------------------------------------------------------------------------------------
def _choose(logits: np.ndarray, opts: dict, recent: list[int], allowed=None, rng=np.random.default_rng()) -> int | None:
    """Pick the next token: repeat penalty, then temperature / top-k / top-p. `allowed(tid)` limits the choice
    (JSON shape); the best candidates are checked first so it stays fast."""
    lg = logits.astype(np.float32, copy=True)
    rp = float(opts.get("repeat_penalty", 1.1))
    if rp != 1.0 and recent:
        idx = np.unique(np.array(recent[-int(opts.get("repeat_last_n", 64)):], dtype=np.int64))
        v = lg[idx]
        lg[idx] = np.where(v > 0, v / rp, v * rp)
    temp = float(opts.get("temperature", 0.8))
    top_k = int(opts.get("top_k", 40)) or 40
    top_p = float(opts.get("top_p", 0.9))
    for k in (64, 1024, len(lg)):
        k = min(k, len(lg))
        cand = np.argpartition(-lg, k - 1)[:k] if k < len(lg) else np.arange(len(lg))
        cand = cand[np.argsort(-lg[cand])]
        if allowed is not None:
            ok = []
            enough = 1 if temp <= 0.3 else min(top_k, 6)  # low temperature: the best valid word is enough
            for t in cand:
                if allowed(int(t)):
                    ok.append(int(t))
                    if len(ok) >= enough:
                        break
            if not ok:
                continue
            cand = np.array(ok, dtype=np.int64)
        if temp <= 0 or len(cand) == 1:
            return int(cand[0])
        cand = cand[:top_k]
        p = np.exp((lg[cand] - lg[cand[0]]) / temp)
        p /= p.sum()
        keep = int(np.searchsorted(np.cumsum(p), top_p) + 1)
        cand, p = cand[:keep], p[:keep] / p[:keep].sum()
        return int(rng.choice(cand, p=p))
    return None


class Stream:
    """Turns tokens into text safely (a character can be split across tokens)."""

    def __init__(self, tok):
        self.tok, self.buf = tok, b""

    def push(self, tid: int) -> str:
        self.buf += self.tok.token_bytes(tid)
        for cut in range(len(self.buf), max(-1, len(self.buf) - 4), -1):
            try:
                text = self.buf[:cut].decode("utf-8")
            except UnicodeDecodeError:
                continue
            self.buf = self.buf[cut:]
            return text
        return ""


def generate(m, prompt: str, opts: dict, fmt=None, tools=False, emit=lambda t: True) -> dict:
    """Run one answer. `emit(text)` gets each new piece; it returns False if the listener went away."""
    t0 = time.time()
    tokens = m.tok.encode(prompt)
    n_predict = int(opts.get("num_predict", -1))
    if n_predict < 0 or n_predict > m.n_ctx - len(tokens) - 1:
        n_predict = max(1, m.n_ctx - len(tokens) - 1)
    shape = JsonShape(fmt) if fmt else None
    with m.lock:
        logits = m.prefill(tokens)
        t1 = time.time()
        out_ids: list[int] = []
        text = ""
        st = Stream(m.tok)
        held = ""  # text not shown yet (a tool call being written)
        in_tool = False
        recent = list(tokens[-64:])
        for _ in range(n_predict):
            if shape is not None:
                state = shape.check(text)
                if state == "complete":
                    break

                def allowed(tid, base=text):
                    if tid in m.eos:
                        return shape.check(base) == "complete"
                    if tid in m.tok.special.values():
                        return False
                    try:  # only whole characters in a JSON answer (a piece of a character could break it)
                        piece = m.tok.token_bytes(tid).decode("utf-8")
                    except UnicodeDecodeError:
                        return False
                    return bool(piece) and shape.check(base + piece) != "invalid"

                nxt = _choose(logits, opts, recent, allowed)
                if nxt is None:
                    break
            else:
                nxt = _choose(logits, opts, recent)
            if nxt in m.eos:
                break
            out_ids.append(nxt)
            recent.append(nxt)
            piece = st.push(nxt)
            text += piece
            if tools and (in_tool or "<tool_call>" in text):
                in_tool = True
                held += piece
            elif piece and not emit(piece):
                break  # nobody is listening any more (Stop, or a newer request)
            if len(out_ids) >= n_predict:
                break
            logits = m.step(nxt)
        t2 = time.time()
    content, calls = (split_tool_calls(text) if tools else (text, []))
    if tools and not calls and held:
        emit(held)  # it looked like a tool call but wasn't one: show it after all
    return {"content": content if tools else text, "tool_calls": calls, "prompt_eval_count": len(tokens),
            "eval_count": len(out_ids), "prompt_eval_duration": int((t1 - t0) * 1e9),
            "eval_duration": int((t2 - t1) * 1e9), "total_duration": int((t2 - t0) * 1e9)}


# ---------------------------------------------------------------------------------------------------------------
def download(url: str, dest: Path, progress) -> None:
    import httpx

    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=httpx.Timeout(60, connect=15)) as r:
        if r.status_code not in (200, 206):
            raise RuntimeError(f"download failed ({r.status_code})")
        if r.status_code == 200:
            have = 0
        total = have + int(r.headers.get("content-length") or 0)
        with open(part, "ab" if have else "wb") as f:
            got, last = have, 0.0
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                got += len(chunk)
                if time.time() - last > 0.5:
                    last = time.time()
                    progress(got, total)
    part.replace(dest)


def make_handler(engine: Engine):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *a):
            pass

        def _json(self, code: int, obj) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _line(self, obj) -> bool:
            try:
                self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                return False

        def do_GET(self):
            if self.path.startswith("/api/version"):
                return self._json(200, {"version": VERSION})
            if self.path.startswith("/api/tags"):
                return self._json(200, {"models": [{"name": n, "model": n, "engine": "click"}
                                                   for n in engine.available()]})
            if self.path.startswith("/api/ps"):
                return self._json(200, {"models": [{"name": n, "model": n, "size": 0} for n in engine.loaded]})
            if self.path in ("/", ""):
                return self._json(200, {"status": "Click Engine is running"})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json(400, {"error": "bad request"})
            if self.path.startswith("/api/pull"):
                return self._pull(body)
            if self.path.startswith("/api/chat"):
                return self._chat(body, body.get("messages") or [])
            if self.path.startswith("/api/generate"):
                if not body.get("prompt"):  # warm-up: just load the model
                    try:
                        engine.model(body.get("model", ""))
                    except KeyError:
                        return self._json(404, {"error": f"model '{body.get('model')}' not found"})
                    return self._json(200, {"model": body.get("model"), "done": True, "response": ""})
                return self._chat(body, [{"role": "user", "content": body["prompt"]}], generate_api=True)
            return self._json(404, {"error": "not found"})

        def _chat(self, body: dict, messages: list[dict], generate_api: bool = False):
            name = body.get("model", "")
            if any(m.get("images") for m in messages):
                return self._json(400, {"error": "Click Engine can't look at pictures yet"})
            try:
                m = engine.model(name)
            except KeyError:
                return self._json(404, {"error": f"model '{name}' not found"})
            opts = dict(body.get("options") or {})
            tools = body.get("tools") or []
            prompt = render(messages, tools)
            fmt = body.get("format") or None
            stream = body.get("stream", True)
            if stream:
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()

            def emit(piece: str) -> bool:
                if not stream:
                    return True
                obj = ({"model": name, "response": piece, "done": False} if generate_api else
                       {"model": name, "message": {"role": "assistant", "content": piece}, "done": False})
                return self._line(obj)

            try:
                res = generate(m, prompt, opts, fmt, bool(tools), emit)
            except Exception as exc:  # noqa: BLE001
                log.exception("generation failed")
                if stream:
                    self._line({"error": str(exc), "done": True})
                    return
                return self._json(500, {"error": str(exc)})
            stats = {k: res[k] for k in ("prompt_eval_count", "eval_count", "prompt_eval_duration", "eval_duration",
                                         "total_duration")}
            if generate_api:
                final = {"model": name, "response": "" if stream else res["content"], "done": True, **stats}
            else:
                msg = {"role": "assistant", "content": "" if stream else res["content"]}
                if res["tool_calls"]:
                    msg["tool_calls"] = res["tool_calls"]
                final = {"model": name, "message": msg, "done": True, "done_reason": "stop", **stats}
            if stream:
                self._line(final)
            else:
                self._json(200, final)

        def _pull(self, body: dict):
            name = canonical(body.get("model") or body.get("name") or "")
            if name not in CATALOG:
                return self._json(404, {"error": f"Click Engine doesn't have '{name}'"})
            fname, url = CATALOG[name]
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            dest = engine.dir / fname
            if dest.exists():
                self._line({"status": "success"})
                return
            try:
                self._line({"status": "pulling manifest"})
                download(url, dest, lambda got, total: self._line({"status": "downloading", "total": total,
                                                                   "completed": got}))
                self._line({"status": "success"})
            except Exception as exc:  # noqa: BLE001
                self._line({"error": f"download failed: {exc}"})

    return H


def serve(port: int, models_dir: Path, threads: int | None = None) -> None:
    engine = Engine(models_dir, threads=threads)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(engine))
    srv.daemon_threads = True
    log.info("Click Engine listening on 127.0.0.1:%s (models in %s)", port, models_dir)
    srv.serve_forever()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11435)
    ap.add_argument("--models", default=os.path.expanduser("~/.click/models"))
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--parent", type=int, default=0, help="quit when this process (Click) is gone")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if a.parent:
        def watch():
            while True:
                time.sleep(2)
                try:
                    os.kill(a.parent, 0)
                except OSError:
                    os._exit(0)  # Click quit (or crashed): don't stay behind
        threading.Thread(target=watch, daemon=True).start()
    serve(a.port, Path(a.models), a.threads or None)


if __name__ == "__main__":
    main()
