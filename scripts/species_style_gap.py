"""Rank species by how far their MALE and FEMALE portraits drifted apart in STYLE.

One house style means one game. The failure this catches is not a bad portrait
— it is two portraits of the same people that do not belong to the same
picture: the goliath men came back inked and cel-shaded while the goliath women
came back smooth 3D renders, and nothing in the pipeline noticed, because every
individual render "succeeded".

Reading the descriptors for "soft"/"smooth" finds only what you thought to look
for. This measures the RENDERS, and it found offenders the word search missed
(the shifter lineages, whose female clause says "lithe" — an adjective with no
edges, which is the same bug wearing a different word).

    uv run python scripts/species_style_gap.py
    uv run python scripts/species_style_gap.py --top 15

Two proxies for "is this drawn or rendered", both cheap and both directional:

  ink   share of pixels sitting in a strong local gradient — hard outlines and
        cel-shaded boundaries. A smooth render has gradients everywhere and
        hard edges almost nowhere, so this is the primary signal.
  dark  share of near-black pixels, i.e. the ink itself. NOISIER: it also
        counts a dark background, so read it as support for `ink`, never on
        its own. (goliath-hill scores -21 on dark purely from a dark
        backdrop while its ink gap is small.)

A positive gap means the MALE is more drawn than the female, which is the
direction this bug has always run — male clauses get concrete structure ("jutting
jaw", "heavy ridged brow", "stubble") while female clauses get mood adjectives
("lithe", "graceful", "serene"), and mood has no edges for a style to bite on.
The fix is never to masculinise her; it is to name structure instead of mood.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ART = Path(__file__).resolve().parent.parent / "activity-ui/public/assets/species"


def score(path: Path) -> tuple[float, float]:
    """(ink, dark) for one portrait, both as percentages of the frame."""
    import numpy as np
    from PIL import Image
    im = Image.open(path).convert("L").resize((384, 494), Image.LANCZOS)
    a = np.asarray(im, dtype=float)
    gx = np.abs(np.diff(a, axis=1))[:-1, :]
    gy = np.abs(np.diff(a, axis=0))[:, :-1]
    g = np.hypot(gx, gy)
    return float((g > 40).mean() * 100), float((a < 55).mean() * 100)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=0,
                    help="show only the N widest gaps (default: all)")
    ap.add_argument("--threshold", type=float, default=5.0,
                    help="flag a pair whose ink gap exceeds this (default 5.0)")
    a = ap.parse_args(argv)

    if not ART.is_dir():
        print(f"no species art at {ART}")
        return 1

    pairs: dict[str, dict] = defaultdict(dict)
    for p in ART.glob("*.webp"):
        base, _, sex = p.stem.rpartition("-")
        if sex in ("m", "f"):
            pairs[base][sex] = score(p)

    rows = []
    for base, v in pairs.items():
        if "m" in v and "f" in v:
            (im_, dm), (if_, df) = v["m"], v["f"]
            rows.append((base, im_, if_, im_ - if_, dm, df, dm - df))
    if not rows:
        print("no male/female pairs found")
        return 1

    rows.sort(key=lambda r: -r[3])
    shown = rows[:a.top] if a.top else rows
    print(f"{'species':<26}{'ink m':>7}{'ink f':>7}{'d ink':>7}"
          f"{'drk m':>7}{'drk f':>7}{'d drk':>7}")
    print("-" * 68)
    flagged = 0
    for base, im_, if_, di, dm, df, dd in shown:
        flag = ""
        if di > a.threshold:
            flag = "  <<< male far more drawn"
            flagged += 1
        print(f"{base:<26}{im_:7.2f}{if_:7.2f}{di:7.2f}"
              f"{dm:7.2f}{df:7.2f}{dd:7.2f}{flag}")
    print(f"\n{flagged} pair(s) over the {a.threshold:.1f} ink-gap threshold, "
          f"of {len(rows)} measured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
