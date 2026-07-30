"""Render EVERY battlemap archetype, so a map LoRA can be judged on all of them.

A map LoRA that nails a dungeon corridor and then draws a tavern in isometric,
or puts a horizon on open water, is worse than none — the grid the engine
enforces stops matching what the players see. One good sample proves nothing;
this renders the whole catalogue through the real ``vtt.art.render_battlemap``
path and lays the results out as one sheet you can scan in a few seconds.

Run it once with no LoRA for a baseline, then again with one configured, and
compare the two sheets:

    # baseline
    ./.venv/Scripts/python.exe scripts/map_lora_probe.py --tag baseline

    # ...set loras_by_kind = {"map": [...]} in game_config, then
    ./.venv/Scripts/python.exe scripts/map_lora_probe.py --tag battlemap-lora

MUST run under the WINDOWS interpreter — ComfyUI is a Windows process and WSL
cannot reach it (see CLAUDE.md → Environment).

What to look for, per tile:
  * dead-flat overhead — no isometric drift, no horizon, no vanishing point
  * no figures, no tokens, no drawn grid lines (the engine draws those)
  * terrain that matches the archetype (a sewer should not read as a tavern)
  * full-bleed to the edges — a vignette or frame breaks the board
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vtt.mapgen import ARCHETYPES, generate_map          # noqa: E402
from vtt.art import render_battlemap                     # noqa: E402

OUT_ROOT = Path(__file__).resolve().parent.parent / "map-probe"

# A representative biome/lighting per archetype, so each is judged in the
# conditions it actually appears in rather than all under noon daylight.
CONTEXT = {
    "cave": ("underground", "dark"), "crypt": ("underground", "dark"),
    "sewer": ("underground", "dark"), "dungeon-room": ("underground", "dim"),
    "dungeon-complex": ("underground", "dim"), "ruins": ("overgrown", "dim"),
    "tavern": ("interior", "dim"), "forest": ("woodland", "dim"),
    "swamp": ("wetland", "dim"), "reef": ("undersea", "dim"),
    "open-water": ("open sea", "bright"), "sky-islands": ("open sky", "bright"),
    "skyship": ("open sky", "bright"), "ship": ("open sea", "bright"),
    "street": ("town", "bright"), "camp": ("wilderness", "dim"),
    "bridge": ("river gorge", "bright"), "arena": ("town", "bright"),
    "mountain-pass": ("alpine", "bright"), "clearing": ("woodland", "bright"),
    "open": ("grassland", "bright"),
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="run",
                    help="names the output folder, e.g. 'baseline' / 'lora-0.9'")
    ap.add_argument("--only", help="comma-separated archetypes (default: all)")
    ap.add_argument("--seed", type=int, default=20260730,
                    help="layout seed — the SAME seed across runs makes the "
                         "sheets directly comparable")
    ap.add_argument("--squares", type=int, default=20, help="board edge in squares")
    a = ap.parse_args(argv)

    names = ([s.strip() for s in a.only.split(",")] if a.only
             else sorted(ARCHETYPES))
    unknown = [n for n in names if n not in ARCHETYPES]
    if unknown:
        print(f"unknown archetype(s): {unknown}\nknown: {sorted(ARCHETYPES)}")
        return 2

    out = OUT_ROOT / a.tag
    out.mkdir(parents=True, exist_ok=True)
    from imagery import ImageStore
    store = ImageStore()

    print(f"rendering {len(names)} archetype(s) -> {out}\n")
    done, offline = [], []
    for i, name in enumerate(names, 1):
        biome, lighting = CONTEXT.get(name, (None, None))
        gen = generate_map(name, width=a.squares, height=a.squares,
                           seed=a.seed, lighting=lighting)
        print(f"[{i:>2}/{len(names)}] {name:<16} ", end="", flush=True)
        art = render_battlemap(gen, store=store, biome=biome, lighting=lighting,
                               force_new=True)
        if art.offline or not art.image_id:
            print("OFFLINE / no image")
            offline.append(name)
            continue
        raw = store.get_image_bytes(art.image_id)
        if not raw:
            print("no bytes")
            offline.append(name)
            continue
        (out / f"{name}.webp").write_bytes(raw)
        print(f"ok  {len(raw)//1024} KB")
        done.append(name)

    if offline:
        print(f"\n{len(offline)} did not render: {offline}")
    if not done:
        print("\nnothing rendered — is ComfyUI up, and are you on the Windows "
              "interpreter? (see CLAUDE.md -> Environment)")
        return 1

    _contact_sheet(out, done)
    print(f"\n{len(done)} rendered. Sheet: {out / '_sheet.png'}")
    return 0


def _contact_sheet(out: Path, names: list) -> None:
    """One labelled grid of every archetype, for a single-glance comparison."""
    from PIL import Image, ImageDraw
    cell, cols = 300, 6
    rows = (len(names) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 18)), (18, 20, 30))
    dr = ImageDraw.Draw(sheet)
    for i, n in enumerate(names):
        im = Image.open(out / f"{n}.webp").convert("RGB")
        # Letterbox rather than crop: a battlemap's edges are exactly where the
        # isometric drift and vignetting show up, so they must stay visible.
        im.thumbnail((cell, cell), Image.LANCZOS)
        x, y = (i % cols) * cell, (i // cols) * (cell + 18)
        sheet.paste(im, (x + (cell - im.width) // 2, y + (cell - im.height) // 2))
        dr.text((x + 3, y + cell + 3), n, fill=(230, 200, 130))
    sheet.save(out / "_sheet.png")


if __name__ == "__main__":
    sys.exit(main())
