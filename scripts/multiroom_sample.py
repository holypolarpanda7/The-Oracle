"""Render a multi-room board: doors between rooms, fog of war, live sight.

A demonstration, not a test — it needs a GPU, so it is a separate deliberate
run like ``destruction_sample.py``.

    ./.venv/Scripts/python.exe scripts/multiroom_sample.py

MUST run under the WINDOWS interpreter — ComfyUI is a Windows process and WSL
cannot reach it (see CLAUDE.md -> Environment).

Writes ``map-probe/multiroom/``. What it shows, in three frames of the same
board:

  1. **omniscient** — no fog at all. The whole warren: rooms, corridors, and a
     door standing in every threshold, panels lying along the walls they
     interrupt.
  2. **doors closed** — fog on, the party in one room. Everywhere they have
     never seen is black; the room they are in is clear. A closed door stops
     line of sight dead, so the corridor beyond it is not merely unlit, it is
     unknown — and the foes waiting there are not drawn, because a creature
     standing in the dark is not on the board.
  3. **doors opened** — the same party in the same squares, with the doors
     opened. Sight runs through the doorway and down the corridor; rooms the
     party has already seen but is no longer watching stay on the map under a
     cold veil, because fog is MEMORY and sight is not.

Nothing here is a special case. The door blocks sight because ``blocks_sight``
is true for a closed door and false for an open one, and every renderer reads
the tile.
"""
from __future__ import annotations

import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "map-probe" / "multiroom"


def main() -> int:
    import os
    OUT.mkdir(parents=True, exist_ok=True)
    scratch = ROOT / "multiroom-sample.db"
    if scratch.exists():
        scratch.unlink()
    os.environ["DATABASE_URL"] = f"sqlite:///{scratch.as_posix()}"

    from sqlmodel import SQLModel
    from imagery import ImageStore
    from vtt import VttEngine
    from vtt.scene import _default_engine
    from vtt.render_image import render_board_png

    eng = _default_engine()
    SQLModel.metadata.create_all(eng)
    store = ImageStore()
    v = VttEngine(engine=eng, image_store=store)

    # NOTE the name and the extra below. "The Sunken Warren", asked for with
    # "standing water", came back with a lake painted across a room the grid
    # says is dry flagstone — the rules were right and the picture was
    # inviting a swim. The art is a texture and the grid is the truth, but
    # that is a reason not to ASK for terrain the layout doesn't have, not a
    # licence to. Describe the light and the materials; leave the ground to
    # the tiles.
    sc = v.open_scene("sample:table", kind="combat", archetype="dungeon-complex",
                      name="The Kethran Warren", width=26, height=18,
                      # A seed whose warren has a door that is genuinely the
                      # only way between two halves of it — otherwise "the door
                      # blocks sight" is a claim about a door the party could
                      # walk around, and the frames prove nothing.
                      seed=20260841, render_art=False,
                      # Torchlight: a 30-ft reach rather than the generator's
                      # 60. Not for atmosphere — with a long enough sight
                      # radius the party can still see the room they left, and
                      # the whole point of the last frame is what happens when
                      # they can't.
                      lighting="dark")
    row = v.get_scene(sc.id)
    g = v.grid_of(row)
    print("layout:")
    for y, r in enumerate(g.to_rows()):
        print(f"  {y:2d} {r}")
    doors = [(d["x"], d["y"], d.get("state", "closed")) for d in (row.doors or [])]
    print(f"  doors: {doors}")
    if not doors:
        print("  (this seed grew no doors — pick another)")
        return 1

    # Find a door that is genuinely the ONLY way between two parts of the
    # warren — otherwise "the door blocks sight" is a claim about a door the
    # party could simply walk around, and the frames prove nothing.
    from vtt.terrain import aperture_axis

    def region_from(start):
        seen, stack = {start}, [start]
        while stack:
            x, y = stack.pop()
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if (nx, ny) in seen or not g.in_bounds(nx, ny):
                    continue
                if not g.passable(nx, ny):
                    continue
                seen.add((nx, ny))
                stack.append((nx, ny))
        return seen

    chosen = None
    for dx, dy, _s in doors:
        # A door's PASSAGE runs perpendicular to the wall it interrupts.
        axis = aperture_axis(g, dx, dy)
        sides = ([(dx, dy - 1), (dx, dy + 1)] if axis == "ew"
                 else [(dx - 1, dy), (dx + 1, dy)])
        if not all(g.in_bounds(*s) and g.passable(*s) for s in sides):
            continue
        a, b = region_from(sides[0]), region_from(sides[1])
        if a.isdisjoint(b):
            chosen = (dx, dy, a, b, sides)
            break
    if chosen is None:
        print("  (no door on this seed separates two regions — pick another)")
        return 1
    dx, dy, near_side, far_side, sides = chosen
    print(f"  the door at {dx},{dy} is the only way between "
          f"{len(near_side)} and {len(far_side)} squares")

    def closest(region, to, n):
        return sorted(region, key=lambda p: max(abs(p[0] - to[0]),
                                                abs(p[1] - to[1])))[:n]

    party = [("Kara", 30), ("Bram", 25), ("Sela", 30)]
    for (name, spd), (x, y) in zip(party, closest(near_side, (dx, dy), 5)[1:]):
        v.add_token(sc.id, name=name, x=x, y=y, team="party", speed_ft=spd)
    for i, (x, y) in enumerate(closest(far_side, (dx, dy), 3), 1):
        v.add_token(sc.id, name=f"Cultist {i}", x=x, y=y, team="foe",
                    speed_ft=30)

    print("\npainting the warren, conditioned on the floorplan...")
    v.render_art(sc.id, extra="dry flagstones, dust and grit, guttering "
                               "torchlight in iron sconces",
                 conditions="underground")
    print(f"  art image_id={v.get_scene(sc.id).background_image_id}")
    print("drawing the objects that stand in it...")
    print(f"  {v.render_objects(sc.id, conditions='underground')} object sprite(s)")

    def lookup(image_id):
        return store.get_image_bytes(image_id)

    def shot(name: str, note: str) -> None:
        st = v.state(sc.id)
        seen = sum(r.count("1") for r in (st.get("fog") or []))
        lit = sum(r.count("1") for r in (st.get("sight") or []))
        # Count what the PICTURE will show, by the renderer's own rule: a foe
        # standing where nobody can see is not drawn.
        sg = st.get("sight")
        drawn = sum(1 for t in st["tokens"] if t["team"] == "foe"
                    and (not sg or sg[t["y"]][t["x"]] == "1"))
        (OUT / name).write_bytes(
            render_board_png(st, image_lookup=lookup, cell=42))
        print(f"  {name}: {note} — remembered {seen} sq, in sight {lit} sq, "
              f"foes on the board {drawn}")

    print("\nframes:")
    shot("1-omniscient.png", "no fog")

    # Fog on: the party knows only where they can see from.
    v._set_fields(sc.id, fog=v._blank_fog(row.width, row.height))
    v.reveal_from_party(sc.id)
    shot("2-doors-closed.png", "fog on, doors shut")

    for x, y, _s in doors:
        v.set_door(sc.id, x, y, "open")
    v.reveal_from_party(sc.id)
    shot("3-doors-open.png", "the same party, doors opened")

    # Walk them through, so the room behind falls out of sight without falling
    # out of memory. This is the frame that shows why one tier isn't enough:
    # the room they just left is drawn under a cold veil — mapped, not watched.
    landing = sorted(far_side, key=lambda p: max(abs(p[0] - dx),
                                                 abs(p[1] - dy)))[-6:]
    for t, sq in zip([t for t in v.tokens(sc.id) if t.team == "party"],
                     landing[::2]):
        v.move_token(t.id, sq[0], sq[1], teleport=True, free=True)
    v.reveal_from_party(sc.id)
    shot("4-through-the-door.png", "the party has walked through")

    print(f"\nwrote {OUT}")
    try:
        eng.dispose()
        scratch.unlink(missing_ok=True)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
