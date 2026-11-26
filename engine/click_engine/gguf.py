"""Reading model files (the open GGUF container format: a header with settings, then the weights).

Only the reading is done here: where each weight table is in the file, its shape and how it's stored.
The weights are then used straight from the file (memory-mapped), so loading is nearly instant.
"""
from __future__ import annotations

import mmap
import struct
from dataclasses import dataclass

# value types in the header
_T = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}
STRING, ARRAY = 8, 9

# how weights are stored (the ones Click Engine can run)
F32, F16, Q4_0, Q8_0, BF16 = 0, 1, 2, 8, 30
TYPE_NAMES = {F32: "F32", F16: "F16", Q4_0: "Q4_0", Q8_0: "Q8_0", BF16: "BF16"}
BLOCK = {Q4_0: (32, 18), Q8_0: (32, 34), F32: (1, 4), F16: (1, 2), BF16: (1, 2)}  # (values, bytes) per block


@dataclass
class Tensor:
    name: str
    shape: tuple  # (columns, rows): GGUF lists the fastest-changing size first
    type: int
    offset: int   # absolute position in the file
    nbytes: int


class GGUF:
    def __init__(self, path: str):
        self.path = path
        self.f = open(path, "rb")
        self.mm = mmap.mmap(self.f.fileno(), 0, access=mmap.ACCESS_READ)
        self.pos = 0
        if self._read(4) != b"GGUF":
            raise ValueError("not a GGUF model file")
        version = self._u32()
        if version < 2:
            raise ValueError(f"model file version {version} is too old")
        n_tensors, n_kv = self._u64(), self._u64()
        self.meta: dict = {}
        for _ in range(n_kv):
            key = self._str()
            self.meta[key] = self._value(self._u32())
        infos = []
        for _ in range(n_tensors):
            name = self._str()
            nd = self._u32()
            shape = tuple(self._u64() for _ in range(nd))
            typ = self._u32()
            off = self._u64()
            infos.append((name, shape, typ, off))
        align = int(self.meta.get("general.alignment", 32))
        start = (self.pos + align - 1) // align * align
        self.tensors: dict[str, Tensor] = {}
        for name, shape, typ, off in infos:
            n = 1
            for s in shape:
                n *= s
            vals, size = BLOCK.get(typ, (0, 0))
            nbytes = n // vals * size if vals else 0
            self.tensors[name] = Tensor(name, shape, typ, start + off, nbytes)

    # ---- low-level reading ----
    def _read(self, n: int) -> bytes:
        b = self.mm[self.pos:self.pos + n]
        self.pos += n
        return b

    def _u32(self) -> int:
        return struct.unpack("<I", self._read(4))[0]

    def _u64(self) -> int:
        return struct.unpack("<Q", self._read(8))[0]

    def _str(self) -> str:
        return self._read(self._u64()).decode("utf-8", errors="replace")

    def _value(self, t: int):
        if t == STRING:
            return self._str()
        if t == ARRAY:
            et, n = self._u32(), self._u64()
            if et == STRING:
                return [self._str() for _ in range(n)]
            fmt = _T[et]
            size = struct.calcsize(fmt)
            data = self._read(size * n)
            return list(struct.unpack(f"<{n}{fmt}", data))
        fmt = _T[t]
        return struct.unpack("<" + fmt, self._read(struct.calcsize(fmt)))[0]

    def arch(self) -> str:
        return self.meta.get("general.architecture", "")

    def get(self, key: str, default=None):
        return self.meta.get(f"{self.arch()}.{key}", self.meta.get(key, default))
