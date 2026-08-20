"""A gallery may have a step on it.

Height is the cheapest asymmetry a fight can have — it costs movement to take,
a fall to leave in a hurry, and it changes who can see whom without changing a
rule. The ground floor grew a whole vocabulary for it (`_raise`, `_terrace`,
`_mound`, `_plateaus`) and every UPPER storey was a table top by construction:
elevation was stored in one flat map belonging to the ground, so a gallery
could not have a step, a rooftop could not have a ridge, and a hold could not
have a platform. The vocabulary stopped at the stairs.

Elevation is now stored the way terrain, fog, sight and light already are —
level 0 on the row, upper floors inside ``levels`` — because it is the same
KIND of fact about what a storey looks like, and splitting it any other way
puts two answers in two places.

The distinction this test exists to hold: a level's ``base_ft`` is where its
FLOOR sits, and its elevation is what stands ON that floor. Adding them is
``token_height_ft``'s job and only its job — do it twice and a one-foot step on
a fifteen-foot gallery is sixteen feet in the air.

Offline: a scratch copy of the database, no GPU, no LLM.

    uv run python scripts/storey_height_smoke.py
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


db = Path(tempfile.gettempdir()) / "oracle_storey_height.db"
for suffix in ("", "-wal", "-shm"):
    Path(str(db) + suffix).unlink(missing_ok=True)
live = ROOT / "oracle-dm-backend" / "oracle.db"
if live.is_file():
    shutil.copy(live, db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"
os.environ["ORACLE_IMAGERY_ENABLED"] = "0"

from vtt.mapgen import generate_map                  # noqa: E402
from vtt.scene import VttEngine                      # noqa: E402


# ---------------------------------------------------------------------------
print(f"\n{BOLD}1. the generators can build height on a storey{OFF}")

# A roof is not a plane. Raised by RING in from the eaves rather than by "is it
# interior", which is the difference between a hipped roof and a mesa with a rim.
roofed = generate_map("street", width=30, height=22, seed=7)
roofs = [l for l in roofed.levels if l.get("name") == "Rooftops"]
check("a street builds roofs over it", bool(roofs))
elev = (roofs[0].get("elevation") or {}) if roofs else {}
check("...and they are not twenty feet of table top", bool(elev),
      f"{len(elev)} raised square(s)")
flat = sum(1 for r in roofs[0]["terrain"] for c in r if c != " ") - len(elev) \
    if roofs else 0
check("...but they are not all one plateau either — eaves and a ridge",
      0 < len(elev) < len(elev) + flat,
      f"{len(elev)} raised, {flat} at the eaves")

tav = generate_map("tavern", width=30, height=22, seed=3)
gal = [l for l in tav.levels if l.get("name") == "Gallery"]
check("a taproom's gallery has a landing at the far end",
      bool(gal) and bool(gal[0].get("elevation")),
      f"{len(gal[0].get('elevation') or {})} square(s)" if gal else "no gallery")

check("every storey carries the field, even the empty ones",
      all("elevation" in l for l in roofed.levels + tav.levels))


# ---------------------------------------------------------------------------
print(f"\n{BOLD}2. it survives into the world, per floor{OFF}")

eng = VttEngine()
scene = eng.open_scene("storey:smoke", archetype="tavern", width=30, height=22,
                       name="The Wispering Mill", seed=3)
row = eng.get_scene(scene.id)
lvl = next((i for i, l in enumerate(eng.levels_of(row))
            if l.get("name") == "Gallery"), 0)
check("the board came out with a gallery on it", lvl > 0, f"level {lvl}")

ground = eng.elevation_of(row, 0)
upper = eng.elevation_of(row, lvl)
check("the gallery's own height map came through", bool(upper),
      f"{len(upper)} raised square(s)")
check("...and it is NOT the ground floor's", upper != ground,
      f"ground {len(ground)}, gallery {len(upper)}")

# The distinction the whole design turns on.
step_sq = tuple(int(v) for v in next(iter(upper)).split(","))
step_ft = int(upper[f"{step_sq[0]},{step_sq[1]}"])
base_ft = eng.level_base_ft(row, lvl)
check("a level's base_ft is where its FLOOR sits", base_ft > 0, f"{base_ft} ft")
check("...and its elevation is what stands ON that floor, not above the ground",
      step_ft < base_ft, f"a {step_ft} ft step on a {base_ft} ft gallery")
check("_height_at answers per storey",
      eng._height_at(row, step_sq, lvl) == step_ft
      and eng._height_at(row, step_sq, 0) == int(ground.get(f"{step_sq[0]},{step_sq[1]}", 0) or 0),
      f"gallery {eng._height_at(row, step_sq, lvl)}, ground {eng._height_at(row, step_sq, 0)}")


# ---------------------------------------------------------------------------
print(f"\n{BOLD}3. a creature standing on it is really up there{OFF}")

def put(name: str, sq, level: int):
    """A token standing on a named storey, at a named square.

    Three steps and all three are the rules working: `add_token` places on the
    GROUND and will relocate off an impassable square (which is why asking for
    a gallery square here lands somebody a yard away); `update_token` moves the
    storey but REFUSES x/y on purpose, so nothing can sidestep the movement
    rules by editing a position; and the teleport is the sanctioned way to put
    a creature exactly where a test needs it.
    """
    t = eng.add_token(scene.id, name, kind="pc", team="party")
    if level:
        eng.update_token(t.id, level=level)
    eng.move_token(t.id, sq[0], sq[1], teleport=True)
    return t


on_step = put("Kara", step_sq, lvl)
# A square of the gallery that is NOT the landing.
plain = next((tuple(int(v) for v in k.split(","))
              for k in [f"{x},{y}"
                        for y in range(row.height) for x in range(row.width)]
              if eng.grid_of(row, lvl).get(*(int(v) for v in k.split(","))) not in (" ",)
              and k not in upper), None)
check("there is a plain square of gallery to compare against", plain is not None,
      str(plain))
if plain:
    on_walk = put("Pip", plain, lvl)
    row = eng.get_scene(scene.id)
    h_step = eng.token_height_ft(row, eng.get_token(on_step.id))
    h_walk = eng.token_height_ft(row, eng.get_token(on_walk.id))
    check("the one on the landing is the step higher, and no more",
          h_step - h_walk == step_ft, f"{h_step} ft vs {h_walk} ft")
    check("...and both of them are up on the gallery",
          h_walk == base_ft, f"{h_walk} ft on a {base_ft} ft gallery")

on_floor = put("Brother Aldous", step_sq, 0)
row = eng.get_scene(scene.id)
check("somebody on the ground under it is not carried up by it",
      eng.token_height_ft(row, eng.get_token(on_floor.id))
      == int(ground.get(f"{step_sq[0]},{step_sq[1]}", 0) or 0),
      f"{eng.token_height_ft(row, eng.get_token(on_floor.id))} ft")


# ---------------------------------------------------------------------------
print(f"\n{BOLD}4. what the two sides are told{OFF}")

st = eng.state(scene.id)
check("state() ships the ground floor's flat, where it has always been",
      st.get("elevation") == ground)
check("...and every storey's own inside levels[]",
      st["levels"][lvl].get("elevation") == upper,
      f"{len(st['levels'][lvl].get('elevation') or {})} square(s)")
check("...with the ground repeated there too, like terrain",
      st["levels"][0].get("elevation") == ground)

board = eng.render(scene.id)
check("the DM board names the storey its high ground is on",
      "Gallery:" in board and "ground height" in board,
      next((l.strip() for l in board.splitlines() if "Gallery:" in l), "")[:80])

# Setting it by hand goes to the right floor and nowhere else.
before = dict(eng.elevation_of(eng.get_scene(scene.id), 0))
eng.set_elevation(scene.id, [(2, 2)], 10, level=lvl)
row = eng.get_scene(scene.id)
check("set_elevation writes the storey it was told to",
      eng._height_at(row, (2, 2), lvl) == 10)
check("...and leaves the ground alone", eng.elevation_of(row, 0) == before)
eng.set_elevation(scene.id, [(2, 2)], 7)
row = eng.get_scene(scene.id)
check("...and with no level named it still means the ground, as it always did",
      eng._height_at(row, (2, 2), 0) == 7 and eng._height_at(row, (2, 2), lvl) == 10)

for suffix in ("", "-wal", "-shm"):
    Path(str(db) + suffix).unlink(missing_ok=True)

print()
if _fails:
    print(f"{RED}{len(_fails)} check(s) failed:{OFF} " + "; ".join(_fails))
    raise SystemExit(1)
print(f"{GREEN}every storey may have ground of its own{OFF}")
