# Click Engine

Click's own AI engine, written from scratch: it runs open-weights language models (Qwen2 / Qwen2.5 family) on the
Mac's processor. No llama.cpp, no Ollama: the math core (`csrc/clickengine.cpp`), the model-file reader
(`click_engine/gguf.py`), the tokenizer, the chat format, tool calls, JSON-shaped answers and the server are all
Click's own code. It reads models from standard open GGUF files (8-bit Q8_0).

- `csrc/clickengine.cpp`: the transformer (RMSNorm, rotary positions, grouped attention, SwiGLU), 8-bit weights x
  8-bit activations with AVX2, a spin-waiting thread pool, prompts processed in chunks of 256, a kept KV cache.
- `click_engine/model.py`: loads a model, reuses the shared beginning of prompts (only new words are processed).
- `click_engine/chat.py`: Qwen chat format, `<tool_call>` parsing, and `JsonShape` (only valid JSON can be written).
- `click_engine/server.py`: an Ollama-compatible HTTP API (`/api/chat`, `/api/generate`, `/api/tags`, `/api/ps`,
  `/api/pull`) so Click can switch engines without losing features.

Build the core and try it (Intel / AVX2):

    clang++ -O3 -mavx2 -mfma -mf16c -std=c++17 -shared -fPIC -o click_engine/libclickengine.dylib csrc/clickengine.cpp
    python -m click_engine.server --port 11435 --models ~/models    # with qwen2.5-1.5b-instruct-q8_0.gguf in ~/models
    python -m pytest -q tests

Status (v1.27, measured on a 4-core AVX2 machine, 1.5B model): correct answers, tool calls and JSON; follow-up
questions start in ~0.3 s thanks to the kept memory; ~11 words/s writing (Ollama ~18) and ~55 tokens/s reading a
fresh long prompt (Ollama ~180). See TODO.md for the plan to beat Ollama.
