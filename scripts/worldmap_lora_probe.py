"""Sweep the WORLDMAP LoRA over real surveys, at several strengths, one seed.

The ``worldmap`` kind paints the terrain wash under a drafted parchment map
(``eight_card_system/mapmaker.py``). Its job is unlike every other kind's: not
a photographed place, not a 5-ft battlemap floor, but DRAWN COUNTRY at miles
per inch — and the LoRA carrying that (sxz-wowmap) was trained caption-free, so
it has no trigger word and strength is the only dial there is.

Which makes this probe the only way to set it. It renders the same handful of
surveys — taken from the real biome vocabulary the cartographer rolls — across
a strength ladder from ONE seed, and pixel-compares every frame against the
0.00 baseline.

    ./.venv/Scripts/python.exe scripts/worldmap_lora_probe.py
    ./.venv/Scripts/python.exe scripts/worldmap_lora_probe.py --strengths 0,0.6,0.8

MUST run under the WINDOWS interpreter — ComfyUI is a Windows process and WSL
cannot reach it (see CLAUDE.md -> Environment).

**A 0.00 diff column means the LoRA did nothing.** Not "it was subtle" — it
never loaded. Wrong filename, wrong base architecture, or it never reached the
sampler. This project has shipped a silent no-op LoRA before; the pixel column
is the only thing that catches it, because the render still succeeds and the
file hash still changes.

What to look for on the sheet, beyond "did it fire":
  * drawn map, not photographed ground — stylised relief, not satellite imagery
  * NO writing. Every label, route, compass and scale bar is inked afterwards
    from real coordinates; anything the model writes is a lie under the truth
  * the directional brief honoured — forest east should be forest on the right
  * flat overhead, full bleed, no rolled-scroll edges or torn-parchment framing
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageChops, ImageDraw, ImageStat        # noqa: E402

from eight_card_system.mapmaker import _MAP_STYLE, TerrainSurvey  # noqa: E402
from game_config import get_config                             # noqa: E402
from imagery.comfy_client import client_from_config            # noqa: E402
from imagery.prompt_build import build_prompt                  # noqa: E402

OUT_ROOT = Path(__file__).resolve().parent.parent / "map-probe"
LORA = "sxz-wowmap-civit-sdxl.safetensors"

# Surveys built the way ``survey_terrain`` builds them, so the probe exercises
# the real prompt shape rather than a hand-written sentence. Each is a
# plausible sheet: a mixed frontier, a single-biome expanse, and a hard border.
SURVEYS = {
    "frontier": {
        "northwest": "farmland", "north": "farmland", "northeast": "farmland",
        "west": "hills", "centre": "river", "east": "forest",
        "southwest": "river", "south": "river", "southeast": "forest",
    },
    "deep-forest": {k: "forest" for k, _, _ in (
        ("northwest", 0, 0), ("north", 0, 0), ("northeast", 0, 0),
        ("west", 0, 0), ("centre", 0, 0), ("east", 0, 0),
        ("southwest", 0, 0), ("south", 0, 0), ("southeast", 0, 0))},
    "coast-and-peaks": {
        "northwest": "mountains", "north": "mountains", "northeast": "mountains",
        "west": "hills", "centre": "hills", "east": "coast",
        "southwest": "swamp", "south": "coast", "southeast": "coast",
    },
}


def _survey(sectors: dict) -> TerrainSurvey:
    from collections import Counter
    counts = Counter(sectors.values())
    total = float(sum(counts.values())) or 1.0
    return TerrainSurvey(sectors=dict(sectors),
                         shares={b: n / total for b, n in counts.items()},
                         climate="temperate", signature="probe")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="worldmap")
    ap.add_argument("--strengths", default="0,0.5,0.75,1.0",
                    help="comma-separated LoRA strengths; 0 is the baseline")
    ap.add_argument("--seed", type=int, default=20260801)
    ap.add_argument("--size", type=int, default=768)
    ap.add_argument("--only", help="comma-separated survey names")
    a = ap.parse_args(argv)

    strengths = [float(s) for s in a.strengths.split(",") if s.strip()]
    names = ([s.strip() for s in a.only.split(",")] if a.only else list(SURVEYS))
    unknown = [n for n in names if n not in SURVEYS]
    if unknown:
        print(f"unknown survey(s): {unknown}\nknown: {list(SURVEYS)}")
        return 2

    out = OUT_ROOT / a.tag
    out.mkdir(parents=True, exist_ok=True)
    cfg = get_config().imagery

    frames: dict[tuple[str, float], Image.Image] = {}
    for name in names:
        survey = _survey(SURVEYS[name])
        bp = build_prompt("worldmap", f"the lands of {name}",
                          look=survey.prompt_look(), context=survey.climate,
                          style_prompt=_MAP_STYLE,
                          negative_prompt=cfg.negative_prompt)
        print(f"\n=== {name} ===\n{bp.positive[:300]}...")
        for s in strengths:
            client = client_from_config(cfg)
            # Set the stack on the CLIENT: client_from_config reads cfg.loras
            # (the house style), and this kind overrides it outright.
            client.loras = ([] if s <= 0 else
                            [{"name": LORA, "model": s, "clip": s}])
            print(f"  strength {s:.2f} ...", end="", flush=True)
            try:
                png = client.generate(bp.positive, negative=bp.negative,
                                      seed=a.seed, width=a.size, height=a.size)
            except Exception as e:
                print(f" FAILED: {e}")
                continue
            img = Image.open(io.BytesIO(png)).convert("RGB")
            img.save(out / f"{name}_{s:.2f}.png")
            frames[(name, s)] = img
            print(" ok")

    if not frames:
        print("\nnothing rendered — is ComfyUI up, and are you on the Windows "
              "interpreter? (see CLAUDE.md -> Environment)")
        return 1

    print("\n\nmean pixel diff vs the 0.00 baseline "
          "(0.00 anywhere = the LoRA never loaded):")
    header = "  survey            " + "".join(f"{s:>9.2f}" for s in strengths)
    print(header)
    for name in names:
        base = frames.get((name, strengths[0]))
        if base is None:
            continue
        row = f"  {name:<18}"
        for s in strengths:
            img = frames.get((name, s))
            if img is None:
                row += f"{'--':>9}"
                continue
            diff = ImageStat.Stat(ImageChops.difference(base, img)).mean[0]
            row += f"{diff:>9.2f}"
        print(row)

    _sheet(out, names, strengths, frames)
    print(f"\nSheet: {out / '_sheet.png'}")
    return 0


def _sheet(out: Path, names, strengths, frames) -> None:
    """Rows = survey, columns = strength, so the ladder reads left to right."""
    cell, lab = 300, 20
    sheet = Image.new("RGB", (len(strengths) * cell,
                              len(names) * (cell + lab) + lab), (18, 20, 30))
    dr = ImageDraw.Draw(sheet)
    for ci, s in enumerate(strengths):
        dr.text((ci * cell + 4, 4), f"@{s:.2f}", fill=(230, 200, 130))
    for ri, name in enumerate(names):
        y = lab + ri * (cell + lab)
        for ci, s in enumerate(strengths):
            img = frames.get((name, s))
            if img is None:
                continue
            im = img.copy()
            im.thumbnail((cell, cell), Image.LANCZOS)
            sheet.paste(im, (ci * cell + (cell - im.width) // 2, y))
        dr.text((4, y + cell + 4), name, fill=(230, 200, 130))
    sheet.save(out / "_sheet.png")


if __name__ == "__main__":
    sys.exit(main())
