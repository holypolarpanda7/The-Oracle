"""Render a real drafted map — painted terrain wash under the coordinate ink.

The offline half of the map path is covered by ``scripts/map_smoke.py``. This
is the other half: it needs a GPU, so it is a separate, deliberately-run check
rather than part of the smoke suite.

    ./.venv/Scripts/python.exe scripts/map_composite_check.py

MUST run under the WINDOWS interpreter — ComfyUI is a Windows process and WSL
cannot reach it (see CLAUDE.md -> Environment).

Writes ``map-probe/composite/`` — the same sheet with and without the wash, so
the two can be compared directly. What to check:

  * every place name is READABLE over the paint (that is what the parchment
    veil and the outlined labels are for)
  * the painted country agrees with the survey line printed below — forest
    where the graph says forest
  * the model wrote nothing. Any lettering in the paint is a second, false map
    showing through the real one
"""
from __future__ import annotations

import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "map-probe" / "composite"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    scratch = ROOT / "map-composite-scratch.db"
    if scratch.exists():
        scratch.unlink()
    import os
    os.environ["DATABASE_URL"] = f"sqlite:///{scratch.as_posix()}"

    from eight_card_system import mapmaker
    from eight_card_system.graph import WorldGraph
    from eight_card_system.seed import place_pc, seed_starter_world

    world = WorldGraph()
    world.create_tables()
    seed_starter_world(world)
    pc = place_pc(world, "Kara", discord_user_id="probe")

    center, places = mapmaker.gather_mappable_places(
        world, pc.slug, radius_mi=mapmaker.PURCHASE_RADIUS_MI, include_rumored=True)
    if center is None or not places:
        print("nothing mappable — the seed did not take")
        return 1
    print(f"{len(places)} sites around {center}:")
    for p in places:
        print(f"   {p['name']:24s} {p['biome'] or '-':9s} rumored={p['rumored']}")

    pts = [{**p, **dict(zip(("x", "y"), mapmaker._project(center, p["coords"])))}
           for p in places]
    reach = max([max(abs(p["x"]), abs(p["y"])) for p in pts] + [5.0])
    survey = mapmaker.survey_terrain(pts, reach, center)
    print(f"\nsurvey: {survey.prompt_look()}\n")

    for tag, paint in (("ink-only", False), ("painted", True)):
        png = mapmaker.render_map(
            places, center, title="Map of Greenfields",
            subtitle="by a map-maker's practiced hand",
            seed="probe:0:greenfields:1", paint_terrain=paint, area="Greenfields")
        (OUT / f"{tag}.png").write_bytes(png)
        print(f"{tag:9s} -> {OUT / f'{tag}.png'}  ({len(png) // 1024} KB)")

    flawed = mapmaker.render_map(
        places, center, title="Map of Greenfields",
        subtitle="drafted by Kara", flawed=True,
        seed="probe:0:greenfields:1", paint_terrain=True, area="Greenfields")
    (OUT / "painted-flawed.png").write_bytes(flawed)
    print(f"{'flawed':9s} -> {OUT / 'painted-flawed.png'}  ({len(flawed) // 1024} KB)")

    # Windows won't unlink a file SQLAlchemy still holds open, and the pooled
    # connections outlive this function. Drop them first, and treat a failure
    # as cosmetic — the scratch DB is gitignored either way.
    try:
        world.engine.dispose()
        from imagery.models import get_engine as _img_engine
        _img_engine().dispose()
    except Exception:
        pass
    try:
        scratch.unlink(missing_ok=True)
    except OSError as e:
        print(f"(left {scratch.name} behind: {e})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
