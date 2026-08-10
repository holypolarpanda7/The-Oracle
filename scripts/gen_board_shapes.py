"""Generate the browser's copy of the board's SHAPE tables from Python.

    uv run python scripts/gen_board_shapes.py            # write the file
    uv run python scripts/gen_board_shapes.py --check    # fail if it is stale

The isometric board is drawn twice: `vtt/isocam.py` rasterizes a depth map that
the painted layer is conditioned on, and `vttScene3d.ts` builds the geometry the
player actually looks at. They have to be the same room — the camera already has
a gate for that (`scripts/iso_alignment_check.py`) — and once objects stopped
being plain boxes, they have to be the same FURNITURE too. A hand-mirrored table
in two languages is a drift waiting to happen, and the failure is invisible: the
picture simply gets conditioned on a sarcophagus the player is not looking at.

So Python owns the numbers and the TypeScript is generated. ``--check`` is wired
into the alignment gate, so a change to one side that never reached the other
fails there rather than in a render three weeks later.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "activity-ui" / "src" / "lib" / "boardShapes.generated.ts"

_HEADER = """/** GENERATED FILE — do not edit.
 *
 *  Written by scripts/gen_board_shapes.py from the Python side, which is
 *  authoritative. `scripts/iso_alignment_check.py` fails if this file is stale.
 *
 *  These are the shapes the isometric board is built from, and they matter on
 *  both sides of the wire: vtt/isocam.py rasterizes them into the depth map the
 *  painted layer is conditioned on, and vttScene3d.ts builds them as the
 *  geometry the player looks at. If the two disagree, the painting lands on
 *  furniture that is not there — and nothing in either program looks wrong.
 */

"""


def _num(v: float) -> str:
    """Round-trippable and tidy: 0.34 rather than 0.33999999999999997."""
    s = f"{float(v):.6f}".rstrip("0").rstrip(".")
    return s or "0"


def render() -> str:
    from vtt.art import STRUCTURE_CODES
    from vtt.isocam import (
        HEIGHT_JITTER, OBJECT_VARIANTS, PILLAR_RADIUS, WALL_THICKNESS,
    )
    from vtt.terrain import STAND_HEIGHT_FT, TILES

    lines = [_HEADER]
    lines.append("/** How thick a wall is DRAWN, as a fraction of its square.\n"
                 " *  The square stays fully solid in the RULES; this only stops the\n"
                 " *  wall's top face swallowing the room at this camera angle. */\n"
                 f"export const WALL_THICKNESS = {_num(WALL_THICKNESS)};\n")
    lines.append("/** Radius of a pillar or tree trunk, as a fraction of its square. */\n"
                 f"export const PILLAR_RADIUS = {_num(PILLAR_RADIUS)};\n")
    lines.append("/** How much a tile's DRAWN height may wander, as a fraction.\n"
                 " *  Never applied where the RULES quote a height — see heightScale. */\n"
                 f"export const HEIGHT_JITTER = {_num(HEIGHT_JITTER)};\n")

    lines.append("/** How tall each tile SCREENS a creature, in feet, per the rules.\n"
                 " *  Non-zero means the height is an ANSWER a player reads off the\n"
                 " *  board, so it must be drawn exactly and never jittered. */\n"
                 "export const COVER_HEIGHT_FT: Record<string, number> = {")
    for code, t in sorted(TILES.items()):
        if t.cover_height_ft:
            lines.append(f'  {_key(code)}: {int(t.cover_height_ft)},')
    lines.append("};\n")

    codes = ", ".join(f'"{c}"' for c in sorted(STRUCTURE_CODES))
    lines.append("/** Codes that are the BUILDING rather than something standing in it. */\n"
                 f"export const STRUCTURE_CODES: ReadonlySet<string> = new Set([{codes}]);\n")

    lines.append("/** How tall each tile stands, in feet. 0 is floor you walk on. */\n"
                 "export const TILE_HEIGHT_FT: Record<string, number> = {")
    for code, ft in sorted(STAND_HEIGHT_FT.items()):
        lines.append(f'  {_key(code)}: {int(ft)},')
    lines.append("};\n")

    lines.append("/** What each object is SHAPED like: one or more ARRANGEMENTS, each a\n"
                 " *  list of parts [x0, x1, z0, z1, yFrom, yTo] — fractions of the\n"
                 " *  square for x/z, of the tile's standing height for y. Which one a\n"
                 " *  square uses comes from variantOf, so the same square always picks\n"
                 " *  the same arrangement on both sides of the wire. */\n"
                 "export const OBJECT_VARIANTS: Record<string, readonly (readonly (readonly [\n"
                 "  number, number, number, number, number, number])[])[]> = {")
    for code, variants in sorted(OBJECT_VARIANTS.items()):
        vs = []
        for parts in variants:
            body = ", ".join("[" + ", ".join(_num(v) for v in p) + "]" for p in parts)
            vs.append(f"[{body}]")
        joined = ",\n    ".join(vs)
        lines.append(f"  {_key(code)}: [\n    {joined}],")
    lines.append("};\n")
    return "\n".join(lines)


def _key(code: str) -> str:
    """A JS object key for a tile code. Most are punctuation, so quote them."""
    return code if code.isalpha() else f'"{code}"'


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the committed file is out of date")
    a = ap.parse_args(argv)

    want = render()
    have = OUT.read_text(encoding="utf8") if OUT.exists() else ""
    if a.check:
        if want == have:
            print(f"{OUT.relative_to(ROOT)} is current.")
            return 0
        print(f"STALE: {OUT.relative_to(ROOT)} does not match the Python shapes.\n"
              "The depth map and the geometry would disagree. Regenerate with:\n"
              "  uv run python scripts/gen_board_shapes.py")
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(want, encoding="utf8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
