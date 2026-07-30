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

# The Windows console defaults to cp1252 and dies on the arrows/em-dashes in
# this module's help text (same guard as imagery/species_portraits.py).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vtt.mapgen import ARCHETYPES, generate_map          # noqa: E402
from vtt.art import render_battlemap, canvas_size        # noqa: E402

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
    ap.add_argument("--lora", action="append", default=[], metavar="NAME[:STRENGTH]",
                    help="LoRA to apply for this run, e.g. --lora battlemap.safetensors:0.9 "
                         "(repeatable, applied in order). Overrides the config, so a "
                         "candidate can be tried without editing anything.")
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
    if a.lora:
        loras = [_parse_lora(x) for x in a.lora]
        # Force this run's stack regardless of config, for both the "map" kind
        # and the house default, so nothing quietly falls back.
        cfg = store._cfg()
        cfg.loras = loras
        cfg.loras_by_kind = dict(getattr(cfg, "loras_by_kind", None) or {},
                                 map=loras)
        store._config = cfg
        print("LoRA stack: " + ", ".join(f"{l['name']}@{l['model']}" for l in loras))

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
        gw, gh = canvas_size(gen.width, gen.height)
        note = _grid_note(raw, gen.width, gw)
        print(f"ok  {len(raw)//1024:>4} KB   {note}")
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


def _parse_lora(spec: str) -> dict:
    name, _, strength = spec.partition(":")
    v = float(strength) if strength else 0.8
    return {"name": name, "model": v, "clip": v}


def _grid_note(raw: bytes, squares: int, canvas_w: int) -> str:
    """Hint at whether a grid the model DREW lines up with the engine's.

    This matters the moment anyone wants to measure distance off the picture.
    The engine's pitch is `canvas_w / squares` — a fractional, board-dependent
    number (51.20 px at 20x20, 60.80 vs 59.73 on a 20x15, where the two axes
    don't even agree). A diffusion model draws a grid at whatever spacing looks
    right, so it can match by luck and drift out over twenty cells.

    Reports the dominant vertical pitch near the engine's own, with how far off
    it is and how strongly it stands out. It is an OBSERVATION, not a gate and
    not a verdict: separating "ruled grid" from "row of flagstones" by spectrum
    alone did not survive real map art. Read it alongside the sheet.
    """
    try:
        import io
        import numpy as np
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("L")
        col = np.asarray(im, dtype=float).mean(axis=0)
        n = len(col)
        if n < 64 or not col.any():
            return ""
        # Frequency domain, not autocorrelation: an autocorrelation decays with
        # lag, so its global max is always the shortest period and every render
        # "has a 12px grid". A windowed FFT finds a real repeating pitch and
        # lets us ask how much it stands out from everything else.
        col = col - col.mean()
        mag = np.abs(np.fft.rfft(col * np.hanning(n)))
        periods = np.full(len(mag), np.inf)
        periods[1:] = n / np.arange(1, len(mag))
        expect = im.width / float(squares)
        # Only look NEAR the engine's own pitch. The question is never "is there
        # some repeating pattern" — map art is full of those, a cave pool or a
        # row of walls reads as a 170px period — it is "does a drawn grid match
        # MY grid". A period nowhere near `expect` answers that with "no".
        band = np.where((periods >= expect * 0.6) & (periods <= expect * 1.7))[0]
        if band.size == 0:
            return ""
        k = band[int(np.argmax(mag[band]))]
        # Ruled lines are a strong single tone against the local neighbourhood;
        # texture is broadband. Compare to the whole plausible-grid spectrum.
        wide = np.where((periods >= 8) & (periods <= 400))[0]
        prominence = float(mag[k]) / (float(np.median(mag[wide])) or 1.0)
        period = float(periods[k])
        err = abs(period - expect) / expect * 100.0
        # Deliberately a HINT, not a verdict. Deciding "is that a ruled grid or
        # just a row of flagstones" from the spectrum alone did not survive
        # contact with real map art — every threshold I tried either missed
        # faint grids or called a cave pool one. The number is still the thing
        # worth knowing, so it is reported with its confidence and you confirm
        # on the sheet. Prominence below ~8 is usually just texture.
        tag = "ALIGNED" if err <= 4 else f"off {err:.0f}%"
        return (f"strongest pitch {period:>5.0f}px vs engine {expect:>5.1f}px "
                f"[{tag}, prom {prominence:.0f}x]")
    except Exception:
        return ""


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
