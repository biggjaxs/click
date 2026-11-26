"""Conversations in the model's own format, tool calls, and answers forced into an exact shape (JSON).

- render(): turns Click's messages (system / user / assistant / tool, plus tool definitions) into the text the
  Qwen models were trained on.
- split_tool_calls(): finds <tool_call>{...}</tool_call> in what the model wrote and returns them like Ollama.
- JsonShape: when Click asks for a JSON answer matching a schema (lessons, plans, commands), every word the
  model is allowed to write must keep the answer valid. So the answer always parses, with only the allowed
  fields and choices: the model can't invent a button name that isn't in the list.
"""
from __future__ import annotations

import json
import re

DEFAULT_SYSTEM = "You are Qwen, created by Alibaba Cloud. You are a helpful assistant."


def _content(m: dict) -> str:
    c = m.get("content") or ""
    if isinstance(c, list):  # [{"type": "text", "text": ...}, ...]
        c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
    return str(c)


def render(messages: list[dict], tools: list[dict] | None = None) -> str:
    out = []
    first_system = messages and messages[0].get("role") == "system"
    if tools:
        sys_text = _content(messages[0]) if first_system else DEFAULT_SYSTEM
        out.append("<|im_start|>system\n" + sys_text + "\n\n# Tools\n\nYou may call one or more functions to assist "
                   "with the user query.\n\nYou are provided with function signatures within <tools></tools> XML "
                   "tags:\n<tools>")
        for t in tools:
            out.append("\n" + json.dumps(t, ensure_ascii=False))
        out.append("\n</tools>\n\nFor each function call, return a json object with function name and arguments "
                   "within <tool_call></tool_call> XML tags:\n<tool_call>\n{\"name\": <function-name>, "
                   "\"arguments\": <args-json-object>}\n</tool_call><|im_end|>\n")
    elif first_system:
        out.append("<|im_start|>system\n" + _content(messages[0]) + "<|im_end|>\n")
    else:
        out.append("<|im_start|>system\n" + DEFAULT_SYSTEM + "<|im_end|>\n")
    for i, m in enumerate(messages):
        role = m.get("role")
        if i == 0 and role == "system":
            continue
        if role in ("user", "system") or (role == "assistant" and not m.get("tool_calls")):
            out.append(f"<|im_start|>{role}\n{_content(m)}<|im_end|>\n")
        elif role == "assistant":
            out.append("<|im_start|>assistant")
            if _content(m):
                out.append("\n" + _content(m))
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", tc)
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        pass
                out.append('\n<tool_call>\n{"name": "' + str(fn.get("name", "")) + '", "arguments": '
                           + json.dumps(args, ensure_ascii=False) + "}\n</tool_call>")
            out.append("<|im_end|>\n")
        elif role == "tool":
            if i == 0 or messages[i - 1].get("role") != "tool":
                out.append("<|im_start|>user")
            out.append("\n<tool_response>\n" + _content(m) + "\n</tool_response>")
            if i == len(messages) - 1 or messages[i + 1].get("role") != "tool":
                out.append("<|im_end|>\n")
    out.append("<|im_start|>assistant\n")
    return "".join(out)


_TOOL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def split_tool_calls(text: str) -> tuple[str, list[dict]]:
    calls = []
    for m in _TOOL_RE.finditer(text):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        args = d.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        calls.append({"function": {"name": d.get("name", ""), "arguments": args if isinstance(args, dict) else {}}})
    return _TOOL_RE.sub("", text).strip(), calls


# ---------------------------------------------------------------------------------------------------------------
class Incomplete(Exception):
    """The text so far is a valid beginning; it just isn't finished."""


class Invalid(Exception):
    pass


_WS = " \t\n\r"
MAX_WS = 4        # a little whitespace between parts is fine; endless whitespace isn't
MAX_STRING = 4000


class JsonShape:
    """Checks whether text is a valid beginning (or a complete answer) for a JSON schema (or any JSON)."""

    def __init__(self, schema):
        self.schema = schema if isinstance(schema, dict) else {}

    def check(self, text: str) -> str:
        """"complete", "partial" or "invalid"."""
        try:
            i = self._value(text, self._ws(text, 0), self.schema)
            if text[i:].strip(_WS) == "" and len(text) - i <= MAX_WS:
                return "complete"
            return "invalid"
        except Incomplete:
            return "partial"
        except (Invalid, IndexError, RecursionError):
            return "invalid"

    # each parser returns the index after what it read; raises Incomplete at the end of the text
    def _ws(self, s: str, i: int) -> int:
        j = i
        while j < len(s) and s[j] in _WS:
            j += 1
        if j - i > MAX_WS:
            raise Invalid()
        return j

    def _need(self, s: str, i: int) -> str:
        if i >= len(s):
            raise Incomplete()
        return s[i]

    def _value(self, s: str, i: int, sch: dict) -> int:
        t = sch.get("type")
        if "enum" in sch:
            return self._enum(s, i, [json.dumps(v, ensure_ascii=False) for v in sch["enum"]])
        if isinstance(t, list):
            t = t[0] if t else None
        c = self._need(s, i)
        if t == "object" or (t is None and c == "{"):
            return self._object(s, i, sch)
        if t == "array" or (t is None and c == "["):
            return self._array(s, i, sch)
        if t == "string" or (t is None and c == '"'):
            return self._string(s, i)
        if t in ("number", "integer") or (t is None and (c.isdigit() or c == "-")):
            return self._number(s, i, t == "integer")
        if t == "boolean":
            return self._enum(s, i, ["true", "false"])
        if t == "null":
            return self._enum(s, i, ["null"])
        if t is None:
            return self._enum(s, i, ["true", "false", "null"])
        raise Invalid()

    def _enum(self, s: str, i: int, options: list[str]) -> int:
        rest = s[i:]
        for o in options:
            if rest.startswith(o):
                return i + len(o)
        if any(o.startswith(rest) for o in options):
            raise Incomplete()
        raise Invalid()

    def _string(self, s: str, i: int) -> int:
        if self._need(s, i) != '"':
            raise Invalid()
        j = i + 1
        while True:
            c = self._need(s, j)
            if c == "\\":
                self._need(s, j + 1)
                if s[j + 1] == "u":
                    if len(s) < j + 6:
                        raise Incomplete()
                    j += 6
                else:
                    if s[j + 1] not in '"\\/bfnrt':
                        raise Invalid()
                    j += 2
                continue
            if c == '"':
                return j + 1
            if c in "\n\r" or ord(c) < 0x20:
                raise Invalid()
            j += 1
            if j - i > MAX_STRING:
                raise Invalid()

    def _number(self, s: str, i: int, integer: bool) -> int:
        m = re.match(r"-?(?:0|[1-9]\d*)" + ("" if integer else r"(?:\.\d+)?(?:[eE][+-]?\d+)?"), s[i:])
        if not m:
            if s[i:] in ("-", "") or re.fullmatch(r"-?\d*\.?\d*[eE]?[+-]?", s[i:]):
                raise Incomplete()
            raise Invalid()
        end = i + m.end()
        if end == len(s):
            raise Incomplete()  # a number could still go on: it ends at the next , } or ]
        if len(m.group(0)) > 24:
            raise Invalid()
        return end

    def _object(self, s: str, i: int, sch: dict) -> int:
        if self._need(s, i) != "{":
            raise Invalid()
        props = sch.get("properties")
        required = set(sch.get("required") or [])
        seen: set = set()
        i = self._ws(s, i + 1)
        if self._need(s, i) == "}":
            if required:
                raise Invalid()
            return i + 1
        while True:
            # the key: one of the known properties (any string when the schema doesn't list them)
            if props:
                keys = [json.dumps(k, ensure_ascii=False) for k in props if k not in seen]
                if not keys:
                    raise Invalid()
                j = self._enum(s, i, keys)
                key = json.loads(s[i:j])
            else:
                j = self._string(s, i)
                key = json.loads(s[i:j])
            seen.add(key)
            j = self._ws(s, j)
            if self._need(s, j) != ":":
                raise Invalid()
            j = self._ws(s, j + 1)
            j = self._value(s, j, (props or {}).get(key, {}))
            j = self._ws(s, j)
            c = self._need(s, j)
            if c == "}":
                if not required <= seen:
                    raise Invalid()
                return j + 1
            if c != ",":
                raise Invalid()
            i = self._ws(s, j + 1)

    def _array(self, s: str, i: int, sch: dict) -> int:
        if self._need(s, i) != "[":
            raise Invalid()
        items = sch.get("items") or {}
        i = self._ws(s, i + 1)
        if self._need(s, i) == "]":
            return i + 1
        n = 0
        while True:
            i = self._value(s, i, items)
            n += 1
            i = self._ws(s, i)
            c = self._need(s, i)
            if c == "]":
                return i + 1
            if c != "," or n > 60:
                raise Invalid()
            i = self._ws(s, i + 1)
