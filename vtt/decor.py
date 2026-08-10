"""Things that are in the room but not in the rules.

Bones, a rug, a fallen sack, a brazier, roots through the flagstones. They are
drawn — in the geometry the player sees and in the depth map the painted layer
is conditioned on — and they are invisible to movement, cover and sight.

**Why this exists.** The board's visual vocabulary is capped by its tile
taxonomy: nine object codes, because every new code costs rules meaning (cover,
height, breakability) and has to propagate to four renderers. That is the right
price for a crate you can hide behind and the wrong price for a rug. Decoration
decouples the two, so a room can be furnished without inventing mechanics.

**The one hard rule: a decoration may never be tall enough to be mistaken for
cover.** Everything here is capped below the lowest ``cover_height_ft`` in the
tile table, and :func:`_check_heights` enforces it at import. A player deciding
whether they can break line of sight is reading the picture, and in a fight
against something stronger than them that decision is most of the fight; a
waist-high decorative crate that the engine will not honour is a lie told at
exactly the wrong moment. If something deserves to be cover, it is a TILE.

Placement is DERIVED, never stored — the same reasoning as
:meth:`VttEngine.objects_for` reading the grid each time. A board's decoration
is a pure function of its layout and its seed, so it cannot drift from the room
it decorates, survives a re-generation, and costs no column.
"""
from __future__ import annotations

from typing import Callable, Iterable, Sequence

#: Nothing decorative may stand this tall or taller, in feet.
#:
#: The lowest cover height the rules quote is 3 ft (a low wall, an overturned
#: table). Staying strictly under it means no decoration can ever read as
#: something to crouch behind.
MAX_DECOR_HEIGHT_FT = 2.0

#: kind -> (height in feet, parts). Parts are ``(x0, x1, z0, z1, y0, y1)`` in
#: fractions — of the square for x/z, of the kind's own height for y — the same
#: shape language as OBJECT_VARIANTS.
DECOR_KINDS: dict[str, tuple[float, tuple[tuple[float, float, float, float, float, float], ...]]] = {
    # Flat on the floor. Reads entirely as texture, which is the point.
    "rug": (0.08, ((0.10, 0.90, 0.18, 0.82, 0.0, 1.0),)),
    "bones": (0.5, ((0.30, 0.70, 0.42, 0.52, 0.0, 0.5),
                    (0.38, 0.48, 0.30, 0.70, 0.0, 0.4),
                    (0.55, 0.72, 0.55, 0.68, 0.0, 0.7))),
    "shards": (0.4, ((0.32, 0.52, 0.36, 0.56, 0.0, 1.0),
                     (0.54, 0.68, 0.52, 0.66, 0.0, 0.6))),
    "roots": (0.6, ((0.05, 0.95, 0.40, 0.52, 0.0, 0.5),
                    (0.36, 0.48, 0.05, 0.95, 0.0, 0.7))),
    "sack": (1.6, ((0.30, 0.66, 0.32, 0.68, 0.0, 0.78),
                   (0.34, 0.60, 0.36, 0.62, 0.78, 1.0))),
    # A pedestal under a bowl. The tallest thing here, and still under the cap.
    "brazier": (1.9, ((0.42, 0.58, 0.42, 0.58, 0.0, 0.62),
                      (0.32, 0.68, 0.32, 0.68, 0.62, 1.0))),
}

#: Kinds that want a wall at their back, and how strongly.
_AGAINST_WALL = ("brazier", "sack")

#: Roughly what fraction of eligible floor gets something on it. Sparse on
#: purpose: decoration reads as detail when it is occasional and as clutter
#: when it is everywhere, and every piece is geometry the depth map carries.
DENSITY = 0.07


def _check_heights() -> None:
    tall = {k: h for k, (h, _p) in DECOR_KINDS.items() if h >= MAX_DECOR_HEIGHT_FT}
    if tall:
        raise ValueError(
            f"decoration may never reach cover height: {tall} "
            f"(cap {MAX_DECOR_HEIGHT_FT} ft). If it deserves to be cover, "
            "it is a tile, not a decoration.")


_check_heights()


def _hash(x: int, z: int, seed: int, salt: int) -> int:
    """Stable per-square hash. Same masking discipline as isocam's."""
    return ((x * 73856093) ^ (z * 19349663) ^ (seed * 83492791) ^ salt) & 0xFFFFFFFF


def decor_for(rows: Sequence[str], *, seed: int = 0,
              standing: Callable[[str], bool] | None = None,
              density: float = DENSITY) -> list[dict]:
    """Which squares carry decoration, and what. Deterministic for a layout.

    ``standing(code)`` says whether a code already has something on it (a wall,
    a crate, a pillar); those squares are skipped, as are hazards and anything
    off the floor. Returns ``[{"x", "y", "kind"}]``.
    """
    from .terrain import tile

    stands = standing or (lambda c: False)
    out: list[dict] = []
    kinds = sorted(DECOR_KINDS)
    for z, row in enumerate(rows):
        for x, code in enumerate(row):
            if code == " " or stands(code):
                continue
            t = tile(code)
            # Only ordinary standable ground. Not water, not lava, not a chasm:
            # a rug floating on deep water is exactly the kind of invented
            # scenery the board is careful never to show.
            if t.move_cost_ft is None or t.hazard or t.traversable_swimming:
                continue
            h = _hash(x, z, seed, 0x5bf0)
            if (h % 1000) / 1000.0 >= density:
                continue
            # Something with its back to a wall goes against one; everything
            # else lands where it lands.
            near_wall = any(
                0 <= z + dz < len(rows) and 0 <= x + dx < len(rows[z + dz])
                and stands(rows[z + dz][x + dx])
                for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)))
            pool = [k for k in kinds if near_wall or k not in _AGAINST_WALL]
            out.append({"x": x, "y": z,
                        "kind": pool[(h >> 12) % len(pool)]})
    return out


def describe(decor: Iterable[dict]) -> str:
    """One line for the DM board — what is lying about, and that it is scenery.

    Told rather than left to be discovered, because the alternative is a player
    asking about a brazier the DM cannot see and being told it isn't there. It
    is also the honest version of a rule the project already has for painted
    scenery: narrate it as harmless. Here it is deliberate, so it can be named.
    """
    counts: dict[str, int] = {}
    for d in decor:
        counts[d.get("kind", "?")] = counts.get(d.get("kind", "?"), 0) + 1
    if not counts:
        return ""
    bits = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
    return ("scenery (no mechanical effect — narrate freely, it grants no cover "
            f"and costs no movement): {bits}")
