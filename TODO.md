# Click: what it can't do yet (to fix later)

Written after v1.26 (build-53). Each item says what's wrong, what we know, and an idea for the fix.
The test benches in `tools/evals/` (see the bottom) measure most of these with the real AI models.

## Understanding what people say

- **"Make my screen less bright" / brightness.** Click has no brightness control, so "my screen is way too
  bright at night" was turned into "volume down" in testing. Fix: add a `brightness` skill (up/down/level, and
  Night Shift on/off) on the Mac side, add it to `ALLOWED` and the planner prompt in `pi-hub/app/language.py`,
  plus rules for "too bright / dim the screen / night mode".
- **Some ways of asking to send a text aren't understood**, e.g. "shoot sarah a text saying im running late"
  (the smart model declined it). Fix: rules in `language.py` for "shoot/drop/send/text <name> (a text|a message)
  saying/that ...", plus a test in `pi-hub/tests/test_phrasings.py`.
- **The small model on its own is weak at messy requests** (9/18 vs 16/18 for Smart mode). If Smart mode is off
  or still downloading, understanding drops. Fix later: fine-tune the small model on Click's own requests
  (needs a GPU for a few hours: rented cloud GPU or Google Colab), using the user's real mistakes as examples.
- **Collect the user's real misunderstood phrases** and turn each one into a test (and training data).

## Lessons (Clicky teaching)

- **Never tested on the real Mac yet.** All lesson tests use simulated screens built from real app menus.
  GitHub's Macs can't grant Accessibility / Screen Recording, so real button reading and the "you finished the
  step" detection (window list, typing length) need checking on the user's laptop.
- **Picture-only buttons** (icons with no name or tooltip, e.g. parts of Blender, games) can't be found by
  name. Clicky points at the area of the window instead. Fix idea: run the vision model on a crop of the window
  only when needed (slow on Intel: ~10-30 s), or match icons against a small icon library.
- **Apps that don't share their menus/buttons with macOS** (Blender, some games, some web/Electron apps) are
  taught step by step from on-screen text (OCR) with the fast model: no plan, so long tasks are less reliable.
- **Held-out accuracy of the step-by-step picker is ~10/12.** Misses: Music "make a playlist" (File > New >
  Playlist is two levels deep) and Photos albums (roundabout route).
- **Plans sometimes add extra steps after the task is done** (e.g. GIMP blur then "Layer to Image Size").
  The prompt says not to; a check that ends the lesson when the goal's command was clicked would be safer.
- **Dialogs**: once a dialog opens (Export, Save, Settings), its controls are read like a window, but the plan
  only says "choose in the dialog". Fix: re-plan when a dialog/sheet appears.
- **Typing steps** move on 1.8 s after the user stops typing; a slow typist may get moved on early.
- **Lesson planning takes ~5-10 s** on the i9 (7B model). Could be faster with our own engine (below) or a
  smaller fine-tuned planner.

## Music

- **Never tested against the real Music app** (AppleScript can't run here). Check: library matching, the
  "Clicky Mix" playlist for artists/albums/genres, favorites (`favorited` vs `loved` property).
- **Songs not in the library**: opened in Apple Music; pressing Play for the user is best effort (Accessibility)
  and full songs need an Apple Music subscription.
- **Spotify isn't supported** for "play <artist>" (only opening the app). Spotify's AppleScript can only play a
  known track ID; searching needs their web API (an account/key).

## Speed

- **Smart mode answers are slower to finish** (7B writes ~7-9 words/s on the i9 vs ~25 for the small model).
- **Our own engine** (llama.cpp built into Click instead of Ollama) is not built yet. It would make start-up
  faster and more reliable and allow "speculative decoding" (a tiny model drafts, the big one checks: ~1.5-2x
  faster). Doesn't improve understanding by itself.

## Other

- Summaries of very long pages use the fast model (the smart one would take ~20 s just to read them).
- The Windows build hasn't been worked on in a long time.

## Test benches (tools/evals)

Need Ollama running locally with `qwen2.5:1.5b` (and `qwen2.5:7b` for Smart mode). Run from the repo root:

| Script | What it measures |
|---|---|
| `python tools/evals/eval_tutor.py` | picking the right button on 11 everyday screens (fast model) |
| `python tools/evals/eval_holdout.py` | same, on 12 tasks it was never tuned on |
| `python tools/evals/e2e_obscure.py qwen2.5:7b` | full lessons in Audacity, GIMP, VLC, Inkscape, OBS, HandBrake |
| `python tools/evals/e2e_lesson.py` (and `SC=safari ...`) | full Blender / Safari lessons |
| `python tools/evals/eval_lang.py qwen2.5:7b` | messy requests the rules don't cover (planner) |

Results at v1.26: 11/11, 10/12, 17/17, both full lessons OK, language 16/18 (smart) vs 9/18 (fast).
