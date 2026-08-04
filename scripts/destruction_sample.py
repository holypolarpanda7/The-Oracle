"""Render a before/after pair: a battlemap, then the same board after damage.

A demonstration, not a test — it needs a GPU, so it is a separate deliberate
run like ``map_composite_check.py``.

    ./.venv/Scripts/python.exe scripts/destruction_sample.py

MUST run under the WINDOWS interpreter — ComfyUI is a Windows process and WSL
cannot reach it (see CLAUDE.md -> Environment).

Writes ``map-probe/destruction/``. What it proves visually:
  * the base battlemap is painted ONCE and survives the damage — the same
    picture is under both frames, because the art is pinned to the layout as
    generated rather than to the live grid;
  * a broken pillar and a smashed door are wreckage sprites dropped on their
    own squares, drawn at one-square size and shared by kind;
  * the cover the pillar granted is gone in the "after" board's own numbers.
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
OUT = ROOT / "map-probe" / "destruction"


def main() -> int:
    import os
    OUT.mkdir(parents=True, exist_ok=True)
    scratch = ROOT / "destruction-sample.db"
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

    sc = v.open_scene("sample:table", kind="combat", archetype="dungeon-room",
                      name="The Vault Antechamber", width=18, height=12,
                      seed=20260803, render_art=False)

    # Put the furniture somewhere it makes SENSE. A door belongs in a wall, not
    # standing in open floor — the first version of this sample painted one at
    # 11,6 in the middle of the room and it looked exactly as odd as it was.
    g = v.grid_of(v.get_scene(sc.id))

    def wall_gap():
        """A wall square with open floor on both sides — a doorway."""
        for y in range(1, g.height - 1):
            for x in range(1, g.width - 1):
                if g.get(x, y) != "#":
                    continue
                if g.passable(x - 1, y) and g.passable(x + 1, y):
                    return x, y
                if g.passable(x, y - 1) and g.passable(x, y + 1):
                    return x, y
        return None

    def open_floor(n, near=None):
        """n plain squares, optionally clustered near a point."""
        spots = [(x, y) for y in range(1, g.height - 1)
                 for x in range(1, g.width - 1)
                 if g.get(x, y) == "." and g.passable(x, y)]
        if near:
            spots.sort(key=lambda p: abs(p[0] - near[0]) + abs(p[1] - near[1]))
        return spots[:n]

    door_sq = wall_gap()
    pillars = open_floor(2, near=(g.width // 2, g.height // 2))
    crates = open_floor(4)[2:4]
    for sq in pillars:
        v.set_terrain(sc.id, [sq], "O")
    for sq in crates:
        v.set_terrain(sc.id, [sq], "o")
    if door_sq:
        v.set_terrain(sc.id, [door_sq], "+")
    print(f"  door in a wall at {door_sq}; pillars {pillars}; crates {crates}")
    breakables = [*pillars, *crates] + ([door_sq] if door_sq else [])

    # Stand them either side of a pillar so the cover claim is about the pillar.
    px, py = pillars[0]
    kara = v.add_token(sc.id, name="Kara", x=px - 1, y=py, team="party", speed_ft=30)
    ogre = v.add_token(sc.id, name="Ogre", x=px + 1, y=py, team="foe", speed_ft=30)
    v.add_token(sc.id, name="Bram", x=px - 1, y=min(g.height - 2, py + 2),
                team="party", speed_ft=25)

    print("painting the battlemap, conditioned on the floorplan...")
    v.render_art(sc.id, extra="cracked flagstones, old dust, iron sconces",
                 conditions="underground")
    row = v.get_scene(sc.id)
    print(f"  art_status={row.art_status} image_id={row.background_image_id}")
    print("drawing the objects that stand in it...")
    print(f"  {v.render_objects(sc.id, conditions='underground')} object sprite(s)")

    def lookup(image_id):
        return store.get_image_bytes(image_id)

    before_cover = v.cover_for(sc.id, "Kara", "Ogre")
    (OUT / "1-before.png").write_bytes(
        render_board_png(v.state(sc.id), image_lookup=lookup, cell=54))
    print(f"  before: Ogre's cover from Kara = {before_cover}")

    print("\nbreaking things...")
    for sq in breakables:
        r = v.damage_object(sc.id, sq[0], sq[1], 500, damage_type="bludgeoning")
        print(f"  {sq}: {r.get('detail') or r.get('reason')}")

    print("\ndrawing wreckage sprites (small, and shared by kind)...")
    made = v.render_debris(sc.id, conditions="underground")
    print(f"  {made} sprite(s) drawn; "
          f"debris now: {[(d['x'], d['y'], d['image_id']) for d in v.debris_for(sc.id)]}")

    after_cover = v.cover_for(sc.id, "Kara", "Ogre")
    (OUT / "2-after.png").write_bytes(
        render_board_png(v.state(sc.id), image_lookup=lookup, cell=54))
    print(f"\n  after: Ogre's cover from Kara = {after_cover}")
    print(f"  (the base battlemap image is still {v.get_scene(sc.id).background_image_id} "
          f"— it was never re-rendered)")

    print(f"\nwrote {OUT / '1-before.png'} and {OUT / '2-after.png'}")
    try:
        eng.dispose()
        scratch.unlink(missing_ok=True)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
