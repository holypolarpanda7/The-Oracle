"""Paint one isometric board per environment, so the look can be judged across
the whole range rather than from the one dungeon that happened to work.

    ./.venv/Scripts/python.exe scripts/iso_gallery.py
    ./.venv/Scripts/python.exe scripts/iso_gallery.py --only cave,reef --strength 0.45

MUST run under the WINDOWS interpreter — ComfyUI is a Windows process and WSL
cannot reach it (see CLAUDE.md -> Environment).

Writes to ``material-probe/iso-gallery/`` (gitignored), one PNG per
environment plus its depth map, because the two are only worth looking at
together: a painting that has drifted off its geometry looks perfectly fine on
its own and wrong the moment you see what it was conditioned on.

The archetypes here are the ones with genuinely different problems — enclosed
stone, open woodland, water, sky, interiors, and the two fought off the ground.
An environment that comes back reading as a different KIND of place than its
tiles say is the failure to look for.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "material-probe" / "iso-gallery"

#: (archetype, biome, lighting, what the room is)
SCENES: list[tuple[str, str, str, str]] = [
    ("dungeon-room", "damp underdark", "dim", "a ruined mill cellar"),
    ("dungeon-complex", "old crypt stone", "dark", "a warren of burial vaults"),
    ("cave", "wet limestone cavern", "dark", "a dripping cave mouth"),
    ("crypt", "ancient catacomb", "dark", "a tomb of forgotten kings"),
    ("sewer", "brick undercity", "dim", "a flooded sewer junction"),
    ("forest", "old pine woodland", "bright", "a clearing among great pines"),
    ("swamp", "brackish wetland", "dim", "a sunken boardwalk over black water"),
    ("ruins", "overgrown ruins", "bright", "a toppled temple courtyard"),
    ("street", "cobbled town", "bright", "a narrow street between tall stone houses"),
    ("tavern", "timber interior", "dim", "the taproom of a country inn"),
    ("camp", "open woodland", "dim", "a bandit camp among the trees"),
    ("bridge", "river crossing", "bright", "a stone bridge over a gorge"),
    ("mountain-pass", "frozen scree", "bright", "a snowbound pass"),
    ("arena", "sand and stone", "bright", "a fighting pit"),
    ("ship", "weathered deck timber", "bright", "the deck of a caravel"),
    ("reef", "shallow coral water", "bright", "a coral shelf under clear water"),
    ("sky-islands", "open sky", "bright", "floating stones in open air"),
    ("skyship", "airship deck", "bright", "the deck of a flying vessel"),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma-separated archetypes to render")
    ap.add_argument("--strength", type=float, default=None,
                    help="override the depth ControlNet strength")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--size", default="24x18", help="board size, WxH squares")
    ap.add_argument("--depth", action="store_true",
                    help="also write each board's depth map beside it")
    a = ap.parse_args(argv)

    from game_config import get_config
    from imagery import ImageStore
    from vtt import isocam
    from vtt.art import STRUCTURE_CODES, render_iso_board, worth_painting
    from vtt.mapgen import generate_map
    from vtt.terrain import tile_height_ft

    img = get_config().imagery
    cn = getattr(img, "isoboard_controlnet", "") or ""
    strength = a.strength if a.strength is not None else float(
        getattr(img, "isoboard_controlnet_strength", 0.55))
    if not cn:
        print("no isoboard_controlnet configured — nothing to render.\n"
              "Set ImageryConfig.isoboard_controlnet to the depth model "
              "in ComfyUI/models/controlnet/.")
        return 1

    w, _, h = a.size.partition("x")
    width, height = int(w), int(h or w)
    wanted = {s.strip() for s in a.only.split(",")} if a.only else None
    scenes = [s for s in SCENES if not wanted or s[0] in wanted]

    OUT.mkdir(parents=True, exist_ok=True)
    store = ImageStore()
    print(f"{len(scenes)} environments at {width}x{height}, "
          f"depth strength {strength}\n")

    t0 = time.time()
    ok = skipped = 0
    for i, (arch, biome, light, name) in enumerate(scenes, 1):
        print(f"[{i}/{len(scenes)}] {arch:16} {name} ...", end="", flush=True)
        gen = generate_map(arch, width=width, height=height, seed=a.seed)
        if not worth_painting(gen.grid):
            # Not a failure: an open board keeps its geometry on purpose.
            print(" skipped (too flat to condition — geometry only)")
            skipped += 1
            continue
        art = render_iso_board(gen, store=store, name=name, biome=biome,
                               lighting=light, controlnet=cn,
                               controlnet_strength=strength)
        if not art.image_id:
            print(" FAILED")
            continue
        (OUT / f"{arch}.png").write_bytes(store.get_image_bytes(art.image_id))
        if a.depth:
            (OUT / f"{arch}-depth.png").write_bytes(isocam.depth_image(
                gen.grid.rows, height_ft=tile_height_ft, square_ft=5,
                structure=STRUCTURE_CODES))
        ok += 1
        print(" ok")

    dt = time.time() - t0
    print(f"\n{ok} painted, {skipped} left as geometry, in {dt:.0f}s "
          f"({dt / max(1, ok):.0f}s each) -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
