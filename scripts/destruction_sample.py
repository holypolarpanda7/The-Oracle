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

    # A room with something worth breaking: two pillars flanking a door.
    v.set_terrain(sc.id, [(6, 5), (6, 7)], "O")
    v.set_terrain(sc.id, [(11, 6)], "+")
    v.set_terrain(sc.id, [(4, 3), (4, 9)], "o")

    kara = v.add_token(sc.id, name="Kara", x=3, y=6, team="party", speed_ft=30)
    ogre = v.add_token(sc.id, name="Ogre", x=8, y=6, team="foe", speed_ft=30,
                       size="large")
    v.add_token(sc.id, name="Bram", x=3, y=8, team="party", speed_ft=25)

    print("painting the battlemap (this is the expensive one)...")
    v.render_art(sc.id, extra="cracked flagstones, old dust, iron sconces",
                 conditions="underground")
    row = v.get_scene(sc.id)
    print(f"  art_status={row.art_status} image_id={row.background_image_id}")

    def lookup(image_id):
        return store.get_image_bytes(image_id)

    before_cover = v.cover_for(sc.id, "Kara", "Ogre")
    (OUT / "1-before.png").write_bytes(
        render_board_png(v.state(sc.id), image_lookup=lookup, cell=54))
    print(f"  before: Ogre's cover from Kara = {before_cover}")

    print("\nbreaking things...")
    for sq, dmg in (((6, 5), 500), ((6, 7), 500), ((11, 6), 500),
                    ((4, 3), 500)):
        r = v.damage_object(sc.id, sq[0], sq[1], dmg, damage_type="bludgeoning")
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
