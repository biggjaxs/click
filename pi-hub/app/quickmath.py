"""Instant math: "what's 1 + 1", "25% of 80", "12 times 7", "square root of 81". No AI needed."""
from __future__ import annotations

import ast
import math
import operator
import re

_WORDS = [(r"\bplus\b|\badded to\b", "+"), (r"\bminus\b|\btake away\b", "-"), (r"\btimes\b|\bmultiplied by\b|[x×]", "*"),
          (r"\bdivided by\b|\bover\b|÷", "/"), (r"\bto the power of\b|\^", "**"), (r"\bsquared\b", "**2"),
          (r"\bcubed\b", "**3"), (r"\bmod(?:ulo)?\b", "%")]
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos,
        ast.FloorDiv: operator.floordiv}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(b) > 100 or abs(a) > 1e6):
            raise ValueError("too big")
        return _OPS[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sqrt" and len(node.args) == 1:
        return math.sqrt(_eval(node.args[0]))
    raise ValueError("not math")


def _fmt(v: float) -> str:
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        v = int(v)
    if isinstance(v, float):
        return f"{v:,.6f}".rstrip("0").rstrip(".")
    return f"{v:,}" if abs(v) >= 10000 else str(v)


def solve(text: str) -> str | None:
    """'what's 1 + 1?' -> '1 + 1 = 2', or None if it isn't plain math."""
    t = re.sub(r"\s*=\s*$", "", text.lower().strip().rstrip("?.! ")).strip()
    t = re.sub(r"^(?:hey\s+|ok\s+)?(?:what(?:'s| is)|whats|calculate|compute|how much is|solve|tell me|what does|"
               r"can you (?:calculate|tell me|do))\s+", "", t)
    t = re.sub(r"\s+(?:equal|equals|make)$", "", t).strip()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(?:%|percent)\s+of\s+(\d+(?:\.\d+)?)$", t)
    if m:
        v = float(m.group(1)) * float(m.group(2)) / 100
        return f"{m.group(1)}% of {m.group(2)} = {_fmt(v)}"
    m = re.match(r"^(?:the\s+)?square root of\s+(\d+(?:\.\d+)?)$", t)
    if m:
        return f"√{m.group(1)} = {_fmt(math.sqrt(float(m.group(1))))}"
    expr = t.replace(",", "")
    for pat, rep in _WORDS:
        expr = re.sub(pat, rep, expr)
    if not re.fullmatch(r"[\d\s.+\-*/%()]+", expr) or not re.search(r"\d\s*[-+*/%]", expr):
        return None
    try:
        v = _eval(ast.parse(expr, mode="eval"))
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError, TypeError):
        return None
    shown = re.sub(r"\s+", " ", expr.replace("**", "^").replace("*", "×").replace("/", "÷")).strip()
    return f"{shown} = {_fmt(v)}"
