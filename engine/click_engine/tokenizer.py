"""Turning text into the model's word pieces (tokens) and back: byte-level BPE, as used by Qwen models.

Built from the vocabulary and merge rules stored in the model file. Special markers like <|im_start|> are
recognised whole, never split.
"""
from __future__ import annotations

from functools import lru_cache

import regex

# how Qwen2 splits text into words before merging pieces
_PAT = regex.compile(r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}| ?[^\s\p{L}\p{N}]+[\r\n]*|"
                     r"\s*[\r\n]+|\s+(?!\S)|\s+")


def _bytes_to_unicode() -> dict[int, str]:
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), ord("ÿ") + 1))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, map(chr, cs)))


class Tokenizer:
    def __init__(self, tokens: list[str], merges: list[str], token_types: list[int] | None = None):
        self.tokens = tokens
        self.ids = {t: i for i, t in enumerate(tokens)}
        self.ranks = {tuple(m.split(" ", 1)): i for i, m in enumerate(merges)}
        self.b2u = _bytes_to_unicode()
        self.u2b = {v: k for k, v in self.b2u.items()}
        # special tokens: control markers (type 3) and the chat/tool markers
        types = token_types or [1] * len(tokens)
        self.special = {t: i for i, t in enumerate(tokens) if types[i] in (3, 4) or (t.startswith("<") and t.endswith(">")
                                                                                     and ("|" in t or t in ("<tool_call>", "</tool_call>")))}
        specials = sorted(self.special, key=len, reverse=True)
        self._split = regex.compile("(" + "|".join(regex.escape(s) for s in specials) + ")") if specials else None
        self._bytes_cache: dict[int, bytes] = {}

    @lru_cache(maxsize=65536)  # noqa: B019 - one tokenizer per model, lives as long as the engine
    def _bpe(self, word: str) -> tuple[int, ...]:
        parts = list(word)
        while len(parts) > 1:
            best, best_i = None, -1
            for i in range(len(parts) - 1):
                r = self.ranks.get((parts[i], parts[i + 1]))
                if r is not None and (best is None or r < best):
                    best, best_i = r, i
            if best is None:
                break
            parts[best_i:best_i + 2] = [parts[best_i] + parts[best_i + 1]]
        return tuple(self.ids[p] for p in parts)

    def encode(self, text: str) -> list[int]:
        out: list[int] = []
        chunks = self._split.split(text) if self._split else [text]
        for ch in chunks:
            if not ch:
                continue
            if ch in self.special:
                out.append(self.special[ch])
                continue
            for w in _PAT.findall(ch):
                out.extend(self._bpe("".join(self.b2u[b] for b in w.encode("utf-8"))))
        return out

    def token_bytes(self, tid: int) -> bytes:
        b = self._bytes_cache.get(tid)
        if b is None:
            t = self.tokens[tid]
            if tid in self.special.values():
                b = t.encode("utf-8")
            else:
                b = bytes(self.u2b.get(c, 32) for c in t)
            self._bytes_cache[tid] = b
        return b

    def decode(self, ids) -> str:
        return b"".join(self.token_bytes(i) for i in ids).decode("utf-8", errors="replace")
