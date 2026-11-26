"""Click Engine pieces that don't need a model file: chat format, tool calls, JSON-shaped answers, sampling."""
import json

import numpy as np

from click_engine.chat import JsonShape, render, split_tool_calls
from click_engine.server import _choose

LESSON = {"type": "object", "properties": {"say": {"type": "string"}, "target": {"type": "string", "enum": ["File", "Add"]},
                                           "kind": {"type": "string", "enum": ["click", "look"]}},
          "required": ["say", "target", "kind"]}


def test_json_shape_allows_only_valid_answers():
    j = JsonShape(LESSON)
    assert j.check('{"say": "Click Add') == "partial"
    assert j.check('{"say":"x","target":"Zed"') == "invalid"   # not on screen: can't be written
    assert j.check('{"say":"x","target":"Add","kind":"click"}') == "complete"
    assert j.check('{"say":"x","target":"Add"}') == "invalid"  # a required part is missing
    assert j.check('{"say":"x","made_up":1') == "invalid"
    anyj = JsonShape("json")
    assert anyj.check('{"a": [1, 2') == "partial" and anyj.check('[true, null, "x"]') == "complete"
    assert anyj.check('{"a": 1}       x') == "invalid"


def test_chat_format_and_tool_calls():
    tools = [{"type": "function", "function": {"name": "find"}}]
    p = render([{"role": "system", "content": "S"}, {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "find", "arguments": {"q": 1}}}]},
                {"role": "tool", "content": "found"}], tools)
    assert p.startswith("<|im_start|>system\nS\n\n# Tools") and '{"name": "find", "arguments": {"q": 1}}' in p
    assert "<tool_response>\nfound\n</tool_response><|im_end|>" in p and p.endswith("<|im_start|>assistant\n")
    text, calls = split_tool_calls('<tool_call>\n{"name": "find", "arguments": {"query": "wifi"}}\n</tool_call>')
    assert text == "" and calls == [{"function": {"name": "find", "arguments": {"query": "wifi"}}}]


def test_sampling_respects_the_allowed_choices():
    logits = np.array([5.0, 4.0, 3.0, 0.0], dtype=np.float32)
    assert _choose(logits, {"temperature": 0}, []) == 0
    assert _choose(logits, {"temperature": 0}, [], allowed=lambda t: t >= 2) == 2
    picks = {_choose(logits, {"temperature": 1.0, "top_k": 2, "top_p": 1.0}, []) for _ in range(50)}
    assert picks <= {0, 1}
