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


def _poly(pts) -> str:
    return "[" + ", ".join(f"[{_num(x)}, {_num(z)}]" for x, z in pts) + "]"


def _part(p) -> str:
    """One part, in whichever of the two forms it is.

    A box is six numbers; a solid is two polygons and two heights. They are
    told apart in the browser exactly as they are here — by whether the first
    element is a number — so there is no tag to keep in step.
    """
    if isinstance(p[0], (int, float)):
        return "[" + ", ".join(_num(v) for v in p) + "]"
    bottom, top, y0, y1 = p
    return f"[{_poly(bottom)}, {_poly(top)}, {_num(y0)}, {_num(y1)}]"


def _arrangements(variants) -> str:
    return ",\n      ".join(
        "[" + ", ".join(_part(p) for p in parts) + "]" for parts in variants)


_PART_TYPE = """/** What things on the board are SHAPED like.
 *
 *  Two forms, told apart by whether the first element is a number — the same
 *  discriminator the Python side uses, so no tag has to be kept in step.
 *
 *  A **box** is `[x0, x1, z0, z1, yFrom, yTo]`: fractions of the square for
 *  x/z, of the thing's standing height for y. Axis-aligned and cheap.
 *
 *  A **solid** is `[bottomPolygon, topPolygon, yFrom, yTo]` — a prismatoid,
 *  two polygons of equal vertex count joined by a quad per edge. It subsumes
 *  the box, which is why gaining it rewrote nothing, and it is what stops
 *  everything reading as a cube: a narrower top is a TAPER (a tent's canvas
 *  drawn in to a ridge, a hull with tumblehome, a hipped roof), an offset top
 *  is a LEAN (the raked legs of a timber watchtower, a ladder), and more than
 *  four vertices is a CUT CORNER, which is what turns a stair-stepped hull
 *  outline into one continuous diagonal. */
export type BoxPart = readonly [number, number, number, number, number, number];
export type PolyPart = readonly (readonly [number, number])[];
export type SolidPart = readonly [PolyPart, PolyPart, number, number];
export type Part = BoxPart | SolidPart;

export function isSolid(part: Part): part is SolidPart {
  return typeof part[0] !== "number";
}
"""


def render() -> str:
    from vtt.art import STRUCTURE_CODES
    from vtt.isocam import (
        HEIGHT_JITTER, HOLE_CODES, OBJECT_VARIANTS,
        SKIRT_FT, SKIRT_INSET, WALL_THICKNESS,
    )
    from vtt.decor import DECOR_KINDS, MAX_DECOR_HEIGHT_FT
    from vtt.terrain import (GROUND_RIPPLE_FT, SMOOTH_STEP_FT,
                             SOFT_GROUND, STAND_HEIGHT_FT, TILES)

    lines = [_HEADER, _PART_TYPE]
    lines.append("/** How thick a wall is DRAWN, as a fraction of its square.\n"
                 " *  The square stays fully solid in the RULES; this only stops the\n"
                 " *  wall's top face swallowing the room at this camera angle. */\n"
                 f"export const WALL_THICKNESS = {_num(WALL_THICKNESS)};\n")
    lines.append("/** How much a tile's DRAWN height may wander, as a fraction.\n"
                 " *  Never applied where the RULES quote a height — see heightScale. */\n"
                 f"export const HEIGHT_JITTER = {_num(HEIGHT_JITTER)};\n")

    lines.append("/** Ground whose SURFACE may slope between squares.\n"
                 " *  Elevation is stored per square as whole feet, so a hillside is\n"
                 " *  drawn as terraces unless the corners are averaged. Natural ground\n"
                 " *  only — a floor, a road, a bridge and a deck are LAID, and laid\n"
                 " *  things are flat. Mirrors SOFT_GROUND in vtt/terrain.py. */\n"
                 "export const SOFT_GROUND: ReadonlySet<string> = new Set(["
                 + ", ".join(f'"{c}"' if c != '"' else "'\"'"
                             for c in sorted(SOFT_GROUND)) + "]);\n")
    lines.append("/** The largest difference two squares may have and still be joined\n"
                 " *  by a slope, in feet. One STEP: a LEDGE is the height the rules\n"
                 " *  make you decide about, and ramping one draws a lie. */\n"
                 f"export const SMOOTH_STEP_FT = {_num(SMOOTH_STEP_FT)};\n")
    lines.append("/** How far natural ground WANDERS between one corner and the\n"
                 " *  next, in feet. Drawing only — no rule reads it, and the\n"
                 " *  occlusion march ignores it. Mirrors vtt/terrain.py. */\n"
                 f"export const GROUND_RIPPLE_FT = {_num(GROUND_RIPPLE_FT)};\n")
    lines.append("/** How tall each tile SCREENS a creature, in feet, per the rules.\n"
                 " *  Non-zero means the height is an ANSWER a player reads off the\n"
                 " *  board, so it must be drawn exactly and never jittered. */\n"
                 "export const COVER_HEIGHT_FT: Record<string, number> = {")
    for code, t in sorted(TILES.items()):
        if t.cover_height_ft:
            lines.append(f'  {_key(code)}: {int(t.cover_height_ft)},')
    lines.append("};\n")

    lines.append("/** How thick a platform is, in feet, where its floor meets a hole.\n"
                 " *  Without it an island is a paper cut-out hanging in nothing. */\n"
                 f"export const SKIRT_FT = {_num(SKIRT_FT)};\n")
    lines.append("/** How far the BOTTOM of a skirt pulls in from its own edge.\n"
                 " *  A vertical drop is a slab and a ring of slabs is a box; pulled\n"
                 " *  in, each side is a trapezoid instead. Perpendicular to the\n"
                 " *  edge, never toward the square's centre, so two squares along\n"
                 " *  one straight run stay coplanar. */\n"
                 f"export const SKIRT_INSET = {_num(SKIRT_INSET)};\n")

    holes = ", ".join(f'"{c}"' for c in sorted(HOLE_CODES))
    lines.append("/** Codes that are a HOLE, not ground — nothing is drawn on them.\n"
                 " *  Open sky is air and a chasm is the absence of floor; drawing\n"
                 " *  either as a surface invents ground the rules say you fall\n"
                 " *  through. */\n"
                 f"export const HOLE_CODES: ReadonlySet<string> = new Set([{holes}]);\n")

    codes = ", ".join(f'"{c}"' for c in sorted(STRUCTURE_CODES))
    lines.append("/** Codes that are the BUILDING rather than something standing in it. */\n"
                 f"export const STRUCTURE_CODES: ReadonlySet<string> = new Set([{codes}]);\n")

    lines.append("/** How tall each tile stands, in feet. 0 is floor you walk on. */\n"
                 "export const TILE_HEIGHT_FT: Record<string, number> = {")
    for code, ft in sorted(STAND_HEIGHT_FT.items()):
        lines.append(f'  {_key(code)}: {int(ft)},')
    lines.append("};\n")

    lines.append("/** What each object is SHAPED like: one or more ARRANGEMENTS, each a\n"
                 " *  list of parts (see Part). Which one a square uses comes from\n"
                 " *  variantOf, so the same square always picks the same arrangement\n"
                 " *  on both sides of the wire. */\n"
                 "export const OBJECT_VARIANTS: "
                 "Record<string, readonly (readonly Part[])[]> = {")
    for code, variants in sorted(OBJECT_VARIANTS.items()):
        lines.append(f"  {_key(code)}: [\n      {_arrangements(variants)}],")
    lines.append("};\n")

    lines.append("/** Scenery: drawn by every view, honoured by none of the rules.\n"
                 " *  kind -> [heightFt, parts]. Capped below the lowest cover height\n"
                 " *  in the tile table, so nothing decorative can ever be mistaken for\n"
                 " *  something to crouch behind — see vtt/decor.py. WHICH kinds a\n"
                 " *  board may carry is decided on the server (a rug belongs in a\n"
                 " *  room, a bush does not) and arrives in state(). */\n"
                 f"export const MAX_DECOR_HEIGHT_FT = {_num(MAX_DECOR_HEIGHT_FT)};\n"
                 "export const DECOR_KINDS: "
                 "Record<string, readonly [number, readonly Part[]]> = {")
    for kind, spec in sorted(DECOR_KINDS.items()):
        ft, parts = spec[0], spec[1]
        body = ", ".join(_part(p) for p in parts)
        lines.append(f"  {kind}: [{_num(ft)}, [{body}]],")
    lines.append("};\n")

    lines.append(
        "/** Scenery tints. Muted on purpose: decoration that draws the eye\n"
        " *  competes with what a player has to read — cover, hazards,\n"
        " *  creatures. Generated, because the browser had its own table and\n"
        " *  the server painted every kind one brown, so the colour the\n"
        " *  painter was conditioned on was not the colour on the board. */\n"
        "export const DECOR_TINT: Record<string, string> = {")
    for kind, spec in sorted(DECOR_KINDS.items()):
        lines.append(f'  {kind}: "{spec[3]}",')
    lines.append("};\n")

    # --- skins ------------------------------------------------------------
    from vtt.skins import SKINS

    lines.append(
        "/** What a square is MADE OF, as opposed to what it DOES.\n"
        " *\n"
        " *  A tile code answers the rules — cover, movement, sight. A skin\n"
        " *  answers the eye, and nothing else: no rule reads one. It may hand\n"
        " *  over its own silhouette (which wins over everything, including the\n"
        " *  wall-face model, so a mountainside is drawn as rock mass rather\n"
        " *  than masonry panels) and its own drawn height — but never on a\n"
        " *  tile whose height the rules quote. See vtt/skins.py. */\n"
        "export interface SkinShape {\n"
        "  readonly substance: string;\n"
        "  /** Feet. 0 means keep the tile's own standing height. */\n"
        "  readonly heightFt: number;\n"
        "  /** Line up along the run instead of taking a quarter-turn. */\n"
        "  readonly directional: boolean;\n"
        "  /** Turn so the part's authored +z side faces whichever way this\n"
        "   *  square is NOT enclosed. Beats `directional` where both are set.\n"
        "   *  What a tent needed: a wall that does not know which side the\n"
        "   *  weather is on can only lean the same amount both ways. */\n"
        "  readonly outward: boolean;\n"
        "  /** Pick the arrangement from a COARSE hash, so neighbours agree.\n"
        "   *  For a MASS (rock, coral) rather than a set of objects. */\n"
        "  readonly smooth: boolean;\n"
        "  /** May this SURFACE slope between squares? The tile code cannot\n"
        "   *  answer it — `.` is scree on a mountain pass and cobbles on a\n"
        "   *  street — so the skin carries it and SOFT_GROUND is the\n"
        "   *  fallback for a square wearing no skin at all. */\n"
        "  readonly soft: boolean;\n"
        "  /** Feet. Non-zero means this FLOOR carries its own side wherever\n"
        "   *  it meets something that is not the same BODY — how a ship gets\n"
        "   *  a hull, since deep water is not a hole. 0 = the board rule. */\n"
        "  readonly skirtFt: number;\n"
        "  /** How far that side's bottom pulls in, as a fraction. */\n"
        "  readonly skirtInset: number;\n"
        "  /** Squares of one non-empty body are one THING, and no side is\n"
        "   *  drawn between them: a deck, the rail round it, the mast through\n"
        "   *  it and the cabin on it are four skins and one hull. */\n"
        "  readonly body: string;\n"
        "  /** Draw at exactly this height, with no per-instance jitter.\n"
        "   *  Anything BUILT says so: a jittered post is a platform standing\n"
        "   *  clear of its own legs. */\n"
        "  readonly exact: boolean;\n"
        "  readonly variants: readonly (readonly Part[])[] | null;\n"
        "}\n"
        "export const SKINS: Record<string, SkinShape> = {")
    for name, sk in sorted(SKINS.items()):
        variants = (f"[\n      {_arrangements(sk.variants)}]"
                    if sk.variants else "null")
        lines.append(
            f'  {_skin_key(name)}: {{ substance: "{sk.substance}", '
            f"heightFt: {_num(sk.height_ft)}, "
            f"directional: {'true' if sk.directional else 'false'}, "
            f"outward: {'true' if sk.outward else 'false'},\n"
            f"    smooth: {'true' if sk.smooth else 'false'}, "
            f"soft: {'true' if getattr(sk, 'soft', False) else 'false'}, "
            f'    skirtFt: {_num(sk.skirt_ft)}, '
            f"skirtInset: {_num(sk.skirt_inset)}, "
            f'body: "{sk.body}", '
            f"exact: {'true' if sk.exact else 'false'},\n"
            f"    variants: {variants} }},")
    lines.append("};\n")
    return "\n".join(lines)


def _skin_key(name: str) -> str:
    """Skin names are kebab-case, so most of them need quoting as JS keys."""
    return name if name.isidentifier() else f'"{name}"'


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
