"""A loaded model: the C++ core (clickengine) plus the tokenizer, with a memory of what it has already read.

The memory (KV cache) is kept between requests: if a new prompt starts the same way as the last one (the
same instructions and conversation so far), only the new part is processed. That's what makes follow-up
questions fast, and it's how Click "reads ahead" while you type: the beginning of your message is already
processed before you press Enter.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
from pathlib import Path

import numpy as np

from .gguf import GGUF, F32, Q8_0
from .tokenizer import Tokenizer


def _lib_path() -> Path:
    here = Path(__file__).resolve().parent
    name = "libclickengine.dylib" if sys.platform == "darwin" else "libclickengine.so"
    for p in (here / name, here.parent / "build" / name,
              Path(getattr(sys, "_MEIPASS", here)) / "click_engine" / name):
        if p.exists():
            return p
    raise FileNotFoundError(f"Click Engine core ({name}) isn't built")


_lib = None


def lib():
    global _lib
    if _lib is None:
        L = ctypes.CDLL(str(_lib_path()))
        L.ce_create.restype = ctypes.c_void_p
        L.ce_create.argtypes = [ctypes.c_char_p] + [ctypes.c_int] * 6 + [ctypes.c_float, ctypes.c_float,
                                                                           ctypes.c_int, ctypes.c_int]
        L.ce_tensor.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int]
        L.ce_eval.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.c_int, ctypes.c_int,
                              ctypes.POINTER(ctypes.c_float)]
        L.ce_free.argtypes = [ctypes.c_void_p]
        _lib = L
    return _lib


_LAYER_KINDS = {"attn_norm.weight": 10, "ffn_norm.weight": 11, "attn_q.bias": 12, "attn_k.bias": 13,
                "attn_v.bias": 14, "attn_q.weight": 20, "attn_k.weight": 21, "attn_v.weight": 22,
                "attn_output.weight": 23, "ffn_gate.weight": 24, "ffn_up.weight": 25, "ffn_down.weight": 26}


def physical_cores() -> int:
    try:
        if sys.platform == "darwin":
            import subprocess

            return int(subprocess.run(["sysctl", "-n", "hw.physicalcpu"], capture_output=True, text=True).stdout)
    except Exception:  # noqa: BLE001
        pass
    return max(1, (os.cpu_count() or 2) // (2 if (os.cpu_count() or 2) > 4 else 1))


class Model:
    def __init__(self, path: str, n_ctx: int = 4096, threads: int | None = None):
        g = GGUF(path)
        if g.arch() not in ("qwen2",):
            raise ValueError(f"Click Engine can't run {g.arch()} models yet")
        self.name = Path(path).name
        self.n_ctx = n_ctx
        self.n_vocab = len(g.meta["tokenizer.ggml.tokens"])
        E = g.get("embedding_length")
        self.ptr = lib().ce_create(path.encode(), g.get("block_count"), E, g.get("feed_forward_length"),
                                   g.get("attention.head_count"), g.get("attention.head_count_kv"), self.n_vocab,
                                   float(g.get("rope.freq_base", 10000.0)),
                                   float(g.get("attention.layer_norm_rms_epsilon", 1e-6)), n_ctx,
                                   threads or physical_cores())
        if not self.ptr:
            raise RuntimeError("couldn't open the model file")
        for name, t in g.tensors.items():
            if t.type not in (F32, Q8_0):
                raise ValueError(f"{name} uses a storage type Click Engine can't run yet")
            cols = t.shape[0]
            rows = t.shape[1] if len(t.shape) > 1 else 1
            if name == "token_embd.weight":
                kind, layer = 0, -1
            elif name == "output.weight":
                kind, layer = 1, -1
            elif name == "output_norm.weight":
                kind, layer = 2, -1
            elif name.startswith("blk."):
                _, li, rest = name.split(".", 2)
                kind, layer = _LAYER_KINDS[rest], int(li)
            else:
                continue
            if lib().ce_tensor(self.ptr, kind, layer, t.offset, t.type, rows, cols) != 0:
                raise RuntimeError(f"couldn't use {name}")
        if "output.weight" not in g.tensors:  # small models reuse the word table for the output
            t = g.tensors["token_embd.weight"]
            lib().ce_tensor(self.ptr, 1, -1, t.offset, t.type, t.shape[1], t.shape[0])
        self.tok = Tokenizer(g.meta["tokenizer.ggml.tokens"], g.meta["tokenizer.ggml.merges"],
                             g.meta.get("tokenizer.ggml.token_type"))
        self.eos = {g.meta.get("tokenizer.ggml.eos_token_id", -1), self.tok.special.get("<|im_end|>", -1),
                    self.tok.special.get("<|endoftext|>", -1)}
        self.cached: list[int] = []  # tokens whose memory is in the cache, in order
        self.lock = threading.Lock()
        self._logits = np.zeros(self.n_vocab, dtype=np.float32)

    def eval(self, tokens: list[int], pos: int) -> np.ndarray:
        arr = (ctypes.c_int * len(tokens))(*tokens)
        rc = lib().ce_eval(self.ptr, arr, len(tokens), pos,
                           self._logits.ctypes.data_as(ctypes.POINTER(ctypes.c_float)))
        if rc != 0:
            raise RuntimeError("the conversation is too long for the model's memory")
        return self._logits

    def prefill(self, tokens: list[int]) -> np.ndarray | None:
        """Make the cache hold exactly `tokens` (reusing the shared beginning). Returns the scores for what
        comes next, or None if nothing new had to be processed (the caller then re-evaluates the last token)."""
        if len(tokens) > self.n_ctx - 8:
            raise RuntimeError("the conversation is too long for the model's memory")
        same = 0
        for a, b in zip(self.cached, tokens):
            if a != b:
                break
            same += 1
        if same == len(tokens):  # all of it is already there: redo the last token to get its scores
            same -= 1
        new = tokens[same:]
        self.cached = list(tokens[:same])
        logits = self.eval(new, same)
        self.cached = list(tokens)
        return logits

    def step(self, token: int) -> np.ndarray:
        pos = len(self.cached)
        logits = self.eval([token], pos)
        self.cached.append(token)
        return logits

    def close(self):
        if self.ptr:
            lib().ce_free(self.ptr)
            self.ptr = None
