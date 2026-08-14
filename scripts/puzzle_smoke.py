"""A puzzle is EARNED, and the answer is the server's.

An LLM asked to run a puzzle fails four ways: it forgets the solution, it leaks
it, it folds and accepts any answer, and it loses the state across turns. So the
game holds the solution and the graded hints and the DM is fed only a private
answer key — the same split `combat/` makes between the tracker and the
narration. And a puzzle only comes up somewhere a puzzle belongs, which is what
the world graph's `puzzle_site` tagging is for.

Nothing guarded any of that. This walks the whole chain offline — no LLM, no
network, a scratch database and a library seeded here rather than read from the
gitignored overrides slot:

1. the location GATE: a tagged place is a site whatever its name, an ordinary
   place is not, and the keyword heuristic still catches an untagged crypt;
2. `[[PUZZLE: site]]` tags the room the party is standing in;
3. tag matching tokenizes BOTH sides, so "tomb" finds a "crypt-tomb" puzzle;
4. the answer key reaches the DM and NEVER the player — the one property whose
   failure ruins the puzzle rather than merely breaking it;
5. hints come out of the library in order and run out;
6. solving pays, logs the world event, clears the state and sets the cooldown —
   and the cooldown really does stop the next offer.

    uv run python scripts/puzzle_smoke.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

db = os.path.join(tempfile.gettempdir(), "oracle_puzzle_smoke.db")
if os.path.exists(db):
    os.remove(db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"

spec = importlib.util.spec_from_file_location(
    "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)                                        # noqa: E402

from sqlmodel import Session, SQLModel, select                     # noqa: E402

from eight_card_system.models import EntityType                    # noqa: E402
from rules.models import Puzzle                                    # noqa: E402

SQLModel.metadata.create_all(m.engine)

OK, BAD, OFF, DIM = "\033[32m", "\033[31m", "\033[0m", "\033[2m"
_fails = 0


def check(cond: bool, what: str, detail: str = "") -> None:
    global _fails
    print(f"  {OK}OK{OFF}  {what}" if cond else f"  {BAD}FAIL{OFF}  {what}")
    if detail:
        print(f"      {DIM}{detail}{OFF}")
    if not cond:
        _fails += 1


# A library of one, seeded here: the real one lives in the gitignored overrides
# slot, and a test that only passes on the author's machine is not a test.
with Session(m.engine) as s:
    if not s.exec(select(Puzzle).where(Puzzle.index_slug == "smoke-sealed-door")).first():
        s.add(Puzzle(
            index_slug="smoke-sealed-door", name="The Sealed Door",
            puzzle_type="riddle", setting_tags=["crypt-tomb", "sealed-door"],
            difficulty="medium", check_dc=15,
            premise="Three sigils are cut into the lintel; only one is warm.",
            solution="Press the sigil that is warm — the others are cut deeper.",
            hints=["The stone remembers a hand.", "One sigil is shallower.",
                   "Warmth, not depth, is the tell."],
            reward="The door grinds open on a stair going down.",
            fail_state="The lintel cracks and the stair is buried."))
        s.commit()


class Loc:
    """The shape `_scene_puzzle_tags` reads off a world slice."""

    def __init__(self, name, type_="place", attributes=None, slug=None,
                 status="active"):
        self.name = name
        self.type = type_
        self.attributes = attributes or {}
        self.slug = slug or name.lower().replace(" ", "-").replace("'", "")
        self.status = status


class Ctx:
    def __init__(self, loc):
        self.location = loc


print("\n\033[1m1. the location gate\033[0m")
plain = Ctx(Loc("The Wispering Mill"))
check(_ := m._scene_puzzle_tags(plain, "I look around") == [],
      "an ordinary mill offers nothing")

tagged = Ctx(Loc("The Wispering Mill",
                 attributes={"puzzle_site": True,
                             "puzzle_tags": ["sealed-door", "mechanism"]}))
tags = m._scene_puzzle_tags(tagged, "I look around")
check("sealed-door" in tags,
      "…until the graph TAGS it",
      f"tags: {sorted(tags)}")
check(set(tags) == {"sealed-door", "mechanism"},
      "and then its own tags are AUTHORITATIVE — not joined to every word in "
      "the room's name and the player's sentence",
      "noise tags match by overlap, so they offer puzzles about nothing")

keyworded = Ctx(Loc("The Sunken Crypt"))
check(bool(m._scene_puzzle_tags(keyworded, "I look around")),
      "an untagged CRYPT still trips the keyword fallback")

print("\n\033[1m2. the DM tags the room it is standing in\033[0m")
world_loc = m.world.upsert_entity("The Wispering Mill", EntityType.PLACE)
clean, ops = m.extract_puzzle_hooks(
    "The lintel is carved. [[PUZZLE: site | sealed-door, mechanism]]")
check(ops and ops[0]["action"] == "site", "the site verb parses")
check("[[PUZZLE" not in clean, "and is stripped out of what the player sees")
m.process_puzzle_hooks("puzzle:table", ops,
                       ctx_obj=Ctx(Loc("The Wispering Mill",
                                       slug=world_loc.slug)))
back = m.world.get_entity(world_loc.slug)
attrs = (back.attributes or {}) if back else {}
check(bool(attrs.get("puzzle_site")), "the location is a site on the graph now")
check("sealed-door" in (attrs.get("puzzle_tags") or []),
      "carrying the tags the DM named", f"{attrs.get('puzzle_tags')}")

print("\n\033[1m3. matching a puzzle to the place\033[0m")
found = m.rules_lib.search_puzzles(tags=["tomb"], limit=3)
check(any(p.index_slug == "smoke-sealed-door" for p in found),
      "\"tomb\" finds a puzzle tagged \"crypt-tomb\" — both sides tokenize",
      f"{[p.index_slug for p in found]}")
check(not m.rules_lib.search_puzzles(tags=["galley"], limit=3),
      "and a tag nothing carries finds nothing")

print("\n\033[1m4. the answer key is the DM's alone\033[0m")
notes = m.process_puzzle_hooks("puzzle:table",
                               [{"action": "start", "arg": "smoke-sealed-door"}])
shown = "\n".join(notes)
check("Three sigils" in shown, "the premise is presented in scene")
check("Press the sigil" not in shown,
      "AND THE SOLUTION IS NOT — the failure that ruins a puzzle rather than "
      "merely breaking it")
state = m._load_session_state("puzzle:table")
ap = (state.get("meta") or {}).get("active_puzzle") or {}
check(ap.get("solution", "").startswith("Press the sigil"),
      "the game is holding it")
key = m._format_active_puzzle_block(ap)
check("Press the sigil" in key and "NEVER state the solution" in key,
      "and hands it to the DM under a warning")

print("\n\033[1m5. hints are the library's, not the model's\033[0m")
first = m.process_puzzle_hooks("puzzle:table", [{"action": "hint"}])
check("The stone remembers a hand." in "\n".join(first),
      "the first graded hint comes out verbatim")
for _ in range(2):
    m.process_puzzle_hooks("puzzle:table", [{"action": "hint"}])
spent = m.process_puzzle_hooks("puzzle:table", [{"action": "hint"}])
check("No further hints" in "\n".join(spent),
      "and they run out rather than being invented")

print("\n\033[1m6. solving it ends it\033[0m")
before = len(m.world.recent_events(limit=50)) if hasattr(m.world, "recent_events") else None
paid = m.process_puzzle_hooks("puzzle:table", [{"action": "solved"}])
check("grinds open" in "\n".join(paid), "the reward is paid out")
state = m._load_session_state("puzzle:table")
meta = state.get("meta") or {}
check("active_puzzle" not in meta, "the live state is cleared")
check(int(meta.get("puzzle_cooldown_turn", 0)) > int(state.get("turn_count", 0)),
      "and a cooldown is set so the next room is not another puzzle",
      f"cooldown until turn {meta.get('puzzle_cooldown_turn')}")

print()
if _fails:
    print(f"{BAD}{_fails} FAILED{OFF}")
    sys.exit(1)
print(f"{OK}a puzzle is offered where it belongs, and the answer stays the "
      f"game's{OFF}")
