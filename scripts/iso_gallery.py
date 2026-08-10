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

#: (archetype, biome, lighting, what the room is, style)
#:
#: ``style`` is only meaningful where the archetype offers a real choice — a
#: skyship is timber, brass-and-steam or grown, and all three deserve looking at
#: because they are three different vessels rather than three tints of one.
SCENES: list[tuple[str, str, str, str, str]] = [
    ("dungeon-room", "damp underdark", "dim", "a ruined mill cellar", ""),
    ("dungeon-complex", "old crypt stone", "dark", "a warren of burial vaults", ""),
    ("cave", "wet limestone cavern", "dark", "a dripping cave mouth", ""),
    ("crypt", "ancient catacomb", "dark", "a tomb of forgotten kings", ""),
    ("sewer", "brick undercity", "dim", "a flooded sewer junction", ""),
    ("forest", "old pine woodland", "bright", "a clearing among great pines", ""),
    ("swamp", "brackish wetland", "dim", "a sunken boardwalk over black water", ""),
    ("ruins", "overgrown ruins", "bright", "a toppled temple courtyard", ""),
    ("street", "cobbled town", "bright", "a narrow street between tall stone houses", ""),
    ("tavern", "timber interior", "dim", "the taproom of a country inn", ""),
    ("camp", "open woodland", "dim", "a bandit camp among the trees", ""),
    ("bridge", "river crossing", "bright", "a stone bridge over a gorge", ""),
    ("mountain-pass", "frozen scree", "bright", "a snowbound pass", ""),
    ("arena", "sand and stone", "bright", "a fighting pit", ""),
    ("ship", "weathered deck timber", "bright", "the deck of a caravel", ""),
    ("reef", "shallow coral water", "bright", "a coral shelf under clear water", ""),
    ("sky-islands", "open sky", "bright", "floating stones in open air", ""),
    ("skyship", "airship deck", "bright", "the deck of a flying vessel",
     "timber"),
    ("skyship-steampunk", "airship deck", "bright",
     "the deck of a brass-and-steam flying vessel", "steampunk"),
    ("skyship-organic", "airship deck", "bright",
     "the deck of a grown, living flying vessel", "organic"),
]

#: Gallery names that are a STYLE of another archetype, not one of their own.
STYLE_OF = {"skyship-steampunk": "skyship", "skyship-organic": "skyship"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma-separated archetypes to render")
    ap.add_argument("--strength", type=float, default=None,
                    help="override the depth ControlNet strength")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--size", default="24x18", help="board size, WxH squares")
    ap.add_argument("--sheet", action="store_true",
                    help="composite everything drawn into one contact sheet")
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
    for i, (label, biome, light, name, style) in enumerate(scenes, 1):
        arch = STYLE_OF.get(label, label)
        print(f"[{i}/{len(scenes)}] {label:20} {name} ...", end="", flush=True)
        gen = generate_map(arch, width=width, height=height, seed=a.seed,
                           style=style)
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
        (OUT / f"{label}.png").write_bytes(store.get_image_bytes(art.image_id))
        if a.depth:
            (OUT / f"{label}-depth.png").write_bytes(isocam.depth_image(
                gen.grid.rows, height_ft=tile_height_ft, square_ft=5,
                structure=STRUCTURE_CODES))
        ok += 1
        print(" ok")

    dt = time.time() - t0
    print(f"\n{ok} painted, {skipped} left as geometry, in {dt:.0f}s "
          f"({dt / max(1, ok):.0f}s each) -> {OUT.relative_to(ROOT)}")
    if a.sheet:
        _sheet()
    return 0


def _sheet() -> None:
    """One image of every environment, so the range can be judged together.

    Composited over a dark ground on purpose: the paintings are stored with
    their surround cut away, and on white the transparent corners would read as
    part of the picture.
    """
    from PIL import Image, ImageDraw

    shots = sorted(f for f in OUT.glob("*.png")
                   if not f.name.endswith("-depth.png") and f.name != "gallery.png")
    if not shots:
        print("nothing to composite")
        return
    cols = 3
    cell_w, cap = 460, 20
    rows_n = (len(shots) + cols - 1) // cols
    thumbs = []
    for f in shots:
        im = Image.open(f).convert("RGBA")
        h = max(1, round(cell_w * im.height / im.width))
        thumbs.append((f.stem, im.resize((cell_w, h), Image.LANCZOS)))
    cell_h = max(t.height for _n, t in thumbs)
    pad = 10
    sheet = Image.new("RGB",
                      (cols * (cell_w + pad) + pad,
                       rows_n * (cell_h + pad + cap) + pad), (12, 16, 26))
    d = ImageDraw.Draw(sheet)
    for i, (name, im) in enumerate(thumbs):
        x = pad + (i % cols) * (cell_w + pad)
        y = pad + (i // cols) * (cell_h + pad + cap)
        sheet.paste(im, (x, y), im)
        d.text((x + 2, y + cell_h + 4), name, fill=(198, 208, 232))
    path = OUT / "gallery.png"
    sheet.save(path)
    print(f"contact sheet -> {path.relative_to(ROOT)} ({len(thumbs)} environments)")


if __name__ == "__main__":
    sys.exit(main())
