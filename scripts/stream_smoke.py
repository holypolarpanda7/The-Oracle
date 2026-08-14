"""A player may watch the DM write. They may not watch it work.

The narration a table sees is not the model's output — hooks are pulled out of
it, dice are rolled and substituted, and the rest is split into typed events.
Streaming a preview means showing text BEFORE any of that has happened, and the
model writes its hooks in the middle of its prose, so the whole problem is
keeping twenty-five families of `[[NAME: …]]` off the screen while the words
around them go up.

`narration/stream.py` is that, and it is provable with no model, no key and no
GPU: the readers take lines and the guard takes strings.

    uv run python scripts/stream_smoke.py
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

from narration.stream import (                                    # noqa: E402
    HookGuard, guarded, iter_ollama_deltas, iter_sse_deltas)

OK, BAD, OFF, DIM = "\033[32m", "\033[31m", "\033[0m", "\033[2m"
_fails = 0


def check(cond: bool, what: str, detail: str = "") -> None:
    global _fails
    print(f"  {OK}OK{OFF}  {what}" if cond else f"  {BAD}FAIL{OFF}  {what}")
    if detail:
        print(f"      {DIM}{detail}{OFF}")
    if not cond:
        _fails += 1


REPLY = (
    "The bar goes over with a crash. [[COMBAT: start | The Gilded Sow | unaware]]"
    "Old Marla is already moving.\n\n"
    "\"Out! All of you!\" [[MUSIC: tavern-brawl]]\n\n"
    "The bravo swings wide. [[ROLL: 1d20+5 | Athletics | DC 13]] "
    "and you feel the table give under your shoulder. "
    "[[VTT: open | combat | the taproom | The Gilded Sow]]"
)
CLEAN = (
    "The bar goes over with a crash. Old Marla is already moving.\n\n"
    "\"Out! All of you!\" \n\n"
    "The bravo swings wide.  and you feel the table give under your shoulder. "
)


def run(chunks) -> str:
    g = HookGuard()
    return "".join(g.feed(c) for c in chunks) + g.close()


print("\n\033[1m1. the hook never reaches the screen\033[0m")
check(run([REPLY]) == CLEAN, "one whole chunk",
      f"{run([REPLY])[:70]!r}")
check("[[" not in run([REPLY]) and "]]" not in run([REPLY]),
      "no bracket of any hook survives")
check("COMBAT" not in run([REPLY]) and "tavern-brawl" not in run([REPLY]),
      "and neither does anything inside one")

print("\n\033[1m2. cut it ANYWHERE\033[0m")
# One character at a time is the worst case a real stream can produce, and the
# case a naive filter fails: the cut lands between the two brackets of `[[`.
check(run(list(REPLY)) == CLEAN, "a character at a time gives the same text")
rng = random.Random(7)
bad = 0
for _ in range(300):
    parts, i = [], 0
    while i < len(REPLY):
        n = rng.randint(1, 9)
        parts.append(REPLY[i:i + n])
        i += n
    if run(parts) != CLEAN:
        bad += 1
check(bad == 0, "300 random choppings all give the same text",
      f"{bad} disagreed")

print("\n\033[1m3. a generation that runs out mid-hook\033[0m")
g = HookGuard()
shown = g.feed("You strike. [[COMBAT: damage | Gruk")
check(shown == "You strike. ", "the prose before it is shown")
check(g.close() == "",
      "and the half-written hook is DROPPED, not flushed",
      "hitting the token limit mid-hook is not rare — it is what a limit does")

g = HookGuard()
check(g.feed("a chest [") == "a chest ", "a trailing '[' is held back…")
check(g.feed("here") == "[here", "…and released once it turns out to be prose")

print("\n\033[1m4. reading the wire\033[0m")
sse = [
    ": keep-alive",
    'data: {"choices":[{"delta":{"role":"assistant"}}]}',
    "",
    'data: {"choices":[{"delta":{"content":"The bar goes "}}]}',
    'data: {"choices":[{"delta":{"content":"over. [[MUS"}}]}',
    'data: {"choices":[{"delta":{"content":"IC: brawl]]Marla moves."}}]}',
    "data: [DONE]",
    'data: {"choices":[{"delta":{"content":"never read"}}]}',
]
got = list(iter_sse_deltas(sse))
check(len(got) == 3, "role frames, comments and blanks yield nothing",
      f"{got}")
check("never read" not in "".join(got), "and [DONE] ends it")
check("".join(guarded(iter_sse_deltas(sse))) == "The bar goes over. Marla moves.",
      "a hook split across THREE frames still never shows")

ndjson = [
    '{"message":{"content":"You hear "}}',
    '{"message":{"content":"a bolt draw back. [[VTT: open | combat]]"}}',
    '{"message":{"content":"Nobody breathes."},"done":true}',
    '{"message":{"content":"past done"}}',
]
check("".join(guarded(iter_ollama_deltas(ndjson)))
      == "You hear a bolt draw back. Nobody breathes.",
      "Ollama's newline-delimited JSON reads the same way")

print("\n\033[1m5. it stays cheap\033[0m")
g = HookGuard()
g.feed("prose [[QUEST: open | " + "x" * 5000)
check(len(g._held) <= 1,
      "a long hook is not accumulated — the guard holds at most one character",
      f"held {len(g._held)}")
check(g.hidden > 5000, "though it counts what it hid", f"hidden {g.hidden}")

print()
if _fails:
    print(f"{BAD}{_fails} FAILED{OFF}")
    sys.exit(1)
print(f"{OK}the words go up as they are written; the machinery does not{OFF}")
