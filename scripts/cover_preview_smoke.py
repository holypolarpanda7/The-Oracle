"""Where you STAND is a decision, and the board never said what it was worth.

Cover has been computed exactly and applied correctly since the board went 3D —
the PHB corner rule, heights folded in, a rogue prone behind a crate genuinely
concealed — and the only place a player ever saw the word was on a foe's own
line, after the fact. Choosing a square is precisely when it matters: a square
behind a pillar is worth two squares of movement, and nothing on the board said
so. A player reported it as "cover is not obvious".

So `path_preview` now carries what standing THERE would be worth, per ENEMY —
because cover is a relationship and not a property of a square. The crate that
screens you from the archer on your left does nothing about the one on your
right, and a single number for the square would be a comfortable lie.

Opens from a DM's own sentence, as a board test should. Offline: a scratch copy
of the database, no GPU, no LLM.

    uv run python scripts/cover_preview_smoke.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
_fails: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {GREEN}✓{OFF} {label}" if ok else f"  {RED}✗{OFF} {label}"
          + (f" {DIM}— {detail}{OFF}" if detail else ""))
    if not ok:
        _fails.append(label)


db = Path(tempfile.gettempdir()) / "oracle_cover_preview.db"
for suffix in ("", "-wal", "-shm"):
    Path(str(db) + suffix).unlink(missing_ok=True)
live = ROOT / "oracle-dm-backend" / "oracle.db"
if live.is_file():
    shutil.copy(live, db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"
os.environ["ORACLE_IMAGERY_ENABLED"] = "0"

from vtt.scene import VttEngine                       # noqa: E402
from vtt.terrain import Grid                          # noqa: E402

eng = VttEngine()

# The DM's sentence. A pillared hall, so there is something to stand behind.
print(f"\n{BOLD}1. a hall with pillars in it{OFF}")
scene = eng.open_scene("cover:smoke", place_hint="a pillared hall, torchlit",
                       name="The Long Gallery", width=14, height=10)
row = eng.get_scene(scene.id)

# Lay the room by hand so the geometry is a STATED fact rather than whatever a
# seed produced: a foe west, the mover east, and one pillar exactly between.
rows = ["#" * 14] + ["#" + "." * 12 + "#" for _ in range(8)] + ["#" * 14]
rows[5] = rows[5][:7] + "O" + rows[5][8:]
from sqlmodel import Session                        # noqa: E402
from vtt.models import TacticalMap                   # noqa: E402
with Session(eng.engine) as s:
    r = s.get(TacticalMap, scene.id)
    r.terrain = rows
    r.levels = []
    s.add(r)
    s.commit()

foe = eng.add_token(scene.id, "Goblin Archer", x=2, y=5, kind="monster",
                    team="foe")
foe2 = eng.add_token(scene.id, "Goblin Skulker", x=7, y=1, kind="monster",
                     team="foe")
me = eng.add_token(scene.id, "Kara", x=11, y=5, kind="pc", team="party",
                   speed_ft=30)
check("the board is out with a pillar between the two of them",
      eng.grid_of(eng.get_scene(scene.id)).get(7, 5) == "O")


print(f"\n{BOLD}2. what standing there would be worth{OFF}")
behind = eng.path_preview(me.id, 8, 5)          # directly behind the pillar
open_sq = eng.path_preview(me.id, 8, 8)         # out in the open, same distance

check("the preview still says what the move costs",
      behind.get("ok") and behind.get("cost_ft", 0) > 0,
      f"{behind.get('cost_ft')} ft")
check("...and now says what it is worth", "cover" in behind,
      str(list(behind.get("cover", {}).keys())))

by_name = {r["name"]: r["cover"] for r in behind["cover"]["from"]}
check("standing behind the pillar is cover from the archer it is between you",
      by_name.get("Goblin Archer") in ("half", "three-quarters", "total"),
      by_name.get("Goblin Archer"))

open_by = {r["name"]: r["cover"] for r in open_sq["cover"]["from"]}
check("...and stepping into the open is not",
      open_by.get("Goblin Archer") == "none", open_by.get("Goblin Archer"))

check("cover is reported per FOE, because it is a relationship",
      len(behind["cover"]["from"]) == 2,
      ", ".join(f"{r['name']}: {r['cover']}" for r in behind["cover"]["from"]))
check("...so the pillar that screens you from one says nothing about the other",
      by_name.get("Goblin Skulker") != by_name.get("Goblin Archer"),
      f"archer {by_name.get('Goblin Archer')}, skulker {by_name.get('Goblin Skulker')}")
check("best and worst are there for a caller with one line to spend",
      behind["cover"]["best"] != behind["cover"]["worst"]
      or behind["cover"]["best"] in ("none", "half", "three-quarters", "total"),
      f"{behind['cover']['best']} / {behind['cover']['worst']}")
check("...and best really is the best of them",
      behind["cover"]["best"] == max(
          (r["cover"] for r in behind["cover"]["from"]),
          key=lambda c: {"none": 0, "half": 1, "three-quarters": 2,
                         "total": 3}[c]))


print(f"\n{BOLD}3. what it must not claim{OFF}")
# The mover's own body must not be counted as something to hide behind.
same = eng.path_preview(me.id, 11, 5)
check("a creature is not its own cover",
      same.get("cover", {}).get("from") is None
      or all(r["cover"] != "total" for r in same["cover"]["from"]),
      str(same.get("cover", {}).get("from")))

# With nobody to take cover FROM there is nothing to say, and saying "none"
# would read as "this square is exposed" on a board with no enemies on it.
for t in (foe, foe2):
    eng.remove_token(t.id)
alone = eng.path_preview(me.id, 8, 5)
check("with no enemies at all, the preview says nothing about cover",
      not alone.get("cover"), str(alone.get("cover")))

for suffix in ("", "-wal", "-shm"):
    Path(str(db) + suffix).unlink(missing_ok=True)

print()
if _fails:
    print(f"{RED}{len(_fails)} check(s) failed:{OFF} " + "; ".join(_fails))
    raise SystemExit(1)
print(f"{GREEN}the board says what a square is worth before you stand on it{OFF}")
