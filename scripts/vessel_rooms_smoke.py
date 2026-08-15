"""A ship has ROOMS, and a bastion's rooms are the ones its owner paid for.

Hull SHAPE stopped being one shape when `vtt/vessels.py` went in — a cutter and
a galleon are different outlines now, and `vtt.selftest.test_vessels` guards
that. This is the other half of the same complaint: from the inside every
vessel was still one vessel, because `_rig_ship` built exactly one deckhouse
aft whatever it was rigging, and a board had no way to say what any room was
CALLED.

Two things follow, and this walks both from a DM's own words:

* how many compartments a hull carries is the CLASS's business (a cutter is a
  hull, a mast and open deck; a cruiser is cabins along her length);
* what they are called can only come from the caller, and for a bastion that
  flies the answer is the facilities somebody bought in the builder.

The hull rations them either way. A trader is nine squares in the beam and
holds exactly one deckhouse, so the rooms that will not fit on the weather deck
go BELOW — which is where a ship puts them anyway, and cost no new idea: a
bulkhead is a wall and a doorway is a doorway.

    uv run python scripts/vessel_rooms_smoke.py
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

db = os.path.join(tempfile.gettempdir(), "oracle_vesselrooms_smoke.db")
if os.path.exists(db):
    os.remove(db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"

spec = importlib.util.spec_from_file_location(
    "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)                                     # noqa: E402

from sqlmodel import Session, SQLModel                          # noqa: E402

from bastion.models import Bastion, FacilityInstance            # noqa: E402
from vtt import mapgen                                          # noqa: E402
from vtt.mapgen import archetype_for, generate_map              # noqa: E402

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


#: Where every one of these starts: the DM says a sentence.
NARRATION = ("You come up the companionway onto the deck of the Gilded Sow, "
             "your own skyship, her envelope taut overhead")

print("\n\033[1m1. from the DM's words to a vessel board\033[0m")
arch = archetype_for(NARRATION)
check(arch == "skyship", "the narration lands on the skyship archetype", arch)

print("\n\033[1m2. how many rooms is the HULL's business\033[0m")
counts: dict[str, int] = {}
for seed in range(1, 25):
    gen = generate_map("skyship", width=34, height=22, seed=seed)
    counts[str(seed)] = len(gen.rooms)
check(max(counts.values()) >= 2,
      "a big enough vessel carries more than one compartment",
      f"most seen: {max(counts.values())} over 24 boards")
check(min(counts.values()) == 0,
      "…and a small one carries none",
      "a cutter is a hull, a mast and an open deck; building a cabin into one "
      "leaves nowhere to fight")
tiny = generate_map("skyship", width=18, height=12, seed=4)
deck = sum(r.count("b") for r in tiny.grid.to_rows())
check(deck > 0, "and a small board is still a DECK, never a meadow", f"{deck} squares")

print("\n\033[1m3. what they are CALLED can only come from the caller\033[0m")
FAC = ["the armoury", "the arcane study", "the barracks", "the smithy"]
gen = generate_map("skyship", width=34, height=22, seed=9, rooms=FAC)
got = [r["name"] for r in gen.rooms]
check(got and got == FAC[:len(got)],
      "the asked-for rooms are used, in order and unrenamed",
      f"{got}")
check(len(got) >= 3, "and most of them fit somewhere", f"{len(got)} of {len(FAC)}")
below = [r for r in gen.rooms if int(r.get("level") or 0) > 0]
check(bool(below),
      "the ones the beam has no room for go BELOW DECKS",
      f"{[r['name'] for r in below]} — a trader is nine squares across and "
      f"holds one deckhouse")

hold = gen.levels[-1]["terrain"]
walls = sum(row.count("#") for row in hold)
doors = sum(row.count("/") for row in hold)
check(walls > 0 and doors > 0,
      "divided by real BULKHEADS with real doorways — no new tile, no new rule",
      f"{walls} wall squares, {doors} doorways")

# A hold of sealed boxes is worse than an undivided one: the companionway lands
# in whichever the dice picked and the rest of the ship is unreachable.
rows = [list(r) for r in hold]
start = next(((x, y) for y, r in enumerate(rows) for x, c in enumerate(r)
              if c == "."), None)
seen = {start}
stack = [start]
while stack:
    x, y = stack.pop()
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = x + dx, y + dy
        if (0 <= ny < len(rows) and 0 <= nx < len(rows[ny])
                and (nx, ny) not in seen and rows[ny][nx] in "./"):
            seen.add((nx, ny))
            stack.append((nx, ny))
floor = {(x, y) for y, r in enumerate(rows) for x, c in enumerate(r) if c == "."}
check(floor <= seen, "and you can walk the whole hold",
      f"{len(floor - seen)} squares cut off")

print("\n\033[1m4. a board opened over a real bastion asks nobody\033[0m")
PLACE = "the-gilded-sow"
with Session(m.engine) as s:
    b = Bastion(character_id=1, name="The Gilded Sow", place_slug=PLACE,
                kind="airship", vehicle_kind="airship")
    s.add(b)
    s.commit()
    s.refresh(b)
    for slug in ("armory", "barrack", "arcane-study"):
        s.add(FacilityInstance(bastion_id=b.id, facility_slug=slug))
    s.commit()

rooms = m._bastion_rooms(PLACE)
check(len(rooms) == 3,
      "the backend reads the facilities off the bastion that IS this place",
      f"{rooms}")
check(not m._bastion_rooms("some-tavern"),
      "and a place that is nobody's bastion contributes nothing")

scene = m.vtt_engine.open_scene(
    "vessel:table", kind="combat", archetype="skyship",
    name="The Gilded Sow", place_slug=PLACE, width=34, height=22, seed=9,
    rooms=rooms, render_art=False)
stored = (scene.notes or {}).get("rooms") or []
check(bool(stored), "the board keeps them", f"{[r['name'] for r in stored]}")
check(all(r["name"] in rooms for r in stored),
      "and every one is a facility the owner actually built")

state = m.vtt_engine.state(scene.id)
check(state.get("rooms") == stored, "the players' view is shipped them too")

board = m.vtt_engine.render(scene.id)
check("rooms:" in board and stored[0]["name"] in board,
      "and the DM is TOLD which room is which",
      "the grid shows walls and a doorway and cannot say which side of them "
      "the armoury is on")

from vtt.render_image import render_board_png                 # noqa: E402
png = render_board_png(state, level=0)
check(png[:4] == b"\x89PNG" and len(png) > 4000,
      "and the Discord board DRAWS the name on the room",
      f"{len(png)} bytes — one chip at the room's middle, and only for the "
      f"floor being drawn: a hold's compartments named over the weather deck "
      f"would label a place nobody can see")

print("\n\033[1m5. an ordinary board grows no rooms\033[0m")
plain = generate_map("dungeon-room", width=24, height=18, seed=3, rooms=FAC)
check(not plain.rooms,
      "a generator that does not BUILD rooms ignores the request",
      "asking for an armoury does not entitle you to one")

print()
if _fails:
    print(f"{BAD}{_fails} FAILED{OFF}")
    sys.exit(1)
print(f"{OK}a bastion that flies is her owner's rooms, not a generic deckhouse{OFF}")
