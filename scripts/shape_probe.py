"""Look at the board's GEOMETRY, with no GPU and no browser.

`vtt/isocam.py` rasterizes the board's shapes for the depth map a painting is
conditioned on, and the same rasterizer will draw them shaded — which makes it
the cheapest honest way to judge a silhouette. A contact sheet of archetypes
here answers "does this read as a thing or as a stack of blocks" in seconds,
where the browser needs a build and a server and the painter needs a card.

    uv run python scripts/shape_probe.py
    uv run python scripts/shape_probe.py --only street,camp --seed 7

Writes to `style-probe/review/shapes/` (gitignored). Judge a shape here, then
confirm the LOOK in the browser — this rasterizer and `vttScene3d.ts` are held
to the same table by `iso_alignment_check.py`, so what you see here is what
the player is standing in.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vtt.art import conditioning_kwargs               # noqa: E402
from vtt.boardpalette import colour_of                # noqa: E402
from vtt.isocam import depth_image                    # noqa: E402
from vtt.mapgen import ARCHETYPES, generate_map       # noqa: E402

#: The archetypes with genuinely different shape problems: built stone, timber,
#: a street of roofs, a camp of tents, open rock, water, a ship.
DEFAULT = ("dungeon-room", "street", "camp", "tavern", "mountain-pass",
           "ruins", "reef", "ship", "forest", "cave")


def one(arch: str, seed: int, size: tuple[int, int], out: Path,
        px: int) -> Path:
    w, h = size
    gen = generate_map(arch, width=w, height=h, seed=seed)
    # Exactly what the PAINTER is conditioned on, assembled by the one function
    # that assembles it — skins, elevation, hulls, set pieces and all. Building
    # the kwargs by hand here would make this a probe of a board nobody renders.
    kw = conditioning_kwargs(gen)
    rows = kw.pop("rows")
    kw.pop("square_ft", None)
    # COLOURED, because a depth ramp is what the painter reads and not what a
    # person can judge a silhouette from: everything at the same distance comes
    # out the same grey, so a roof and the wall under it are one shape.
    png = depth_image(rows, px_per_square=px, _flat=False,
                      _colour_of=colour_of, **kw)
    path = out / f"{arch}.png"
    path.write_bytes(png)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", default="", help="comma-separated archetypes")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--size", default="24x18")
    ap.add_argument("--px", type=int, default=54, help="pixels per square")
    ap.add_argument("--out", default="style-probe/review/shapes")
    a = ap.parse_args(argv)

    want = [s.strip() for s in a.only.split(",") if s.strip()] or list(DEFAULT)
    bad = [s for s in want if s not in ARCHETYPES]
    if bad:
        print(f"no such archetype: {', '.join(bad)}")
        return 1
    w, h = (int(v) for v in a.size.lower().split("x"))
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    for arch in want:
        p = one(arch, a.seed, (w, h), out, a.px)
        print(f"  {arch:16s} -> {p.relative_to(ROOT)}")
    print(f"\n{len(want)} board(s) in {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
