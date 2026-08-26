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

import math
from typing import Callable, Iterable, Sequence

from .skins import solid


def _ring(r: float, cx: float = 0.5, cz: float = 0.5, wob: float = 0.0,
          n: int = 8) -> tuple[tuple[float, float], ...]:
    """A rounded plan, wobbled so it is not a wheel. Mirrors isocam's ``_ring``.

    Here for the same reason it is there: a growing thing drawn as a box comes
    back painted as a box. The first outdoor scenery written for this module
    WAS boxes — a bush was three stacked ones — and every meadow came back
    strewn with purple crates, which is the crate-and-dice lesson arriving in
    the one place small enough to think it did not matter.
    """
    out = []
    for i in range(n):
        a = i * 2 * math.pi / n + math.pi / n
        k = r * (1.0 + wob * math.cos(3 * a))
        out.append((cx + math.cos(a) * k, cz + math.sin(a) * k))
    return tuple(out)

#: Nothing decorative may stand this tall or taller, in feet.
#:
#: The lowest cover height the rules quote is 3 ft (a low wall, an overturned
#: table). Staying strictly under it means no decoration can ever read as
#: something to crouch behind.
MAX_DECOR_HEIGHT_FT = 2.0

#: The kinds of PLACE a board can be, for the purpose of what is lying about.
#:
#: A rug on a mountainside and a brazier in a meadow are the same failure, and
#: it was on every outdoor board: the kinds came from one pool, so the painter
#: was handed a hillside strewn with sacks and rugs and duly painted crates on
#: the grass.
#:
#: Four rather than indoor/outdoor, because two of them are neither. A CAVE has
#: no furniture and no vegetation, and does have bones, roots and stones. A
#: STREET is open to the sky and paved: nothing grows out of it and nobody lays
#: a rug on it — given the indoor pool it came back strewn with them.
BUILT, PAVED, WILD, UNDER = "built", "paved", "wild", "under"

#: kind -> (height in feet, parts, where it belongs, colour). Parts are
#: ``(x0, x1, z0, z1, y0, y1)`` in fractions — of the square for x/z, of the
#: kind's own height for y — the same shape language as OBJECT_VARIANTS.
#:
#: The COLOUR is here because it was in two places and they disagreed: the
#: browser carried a per-kind tint table and the server painted every
#: decoration one flat brown, which is the colour the terrain image handed the
#: painter. So a green shrub reached the model as a brown lump. Same rule as
#: everywhere else on this board — Python owns it, the browser's copy is
#: generated.
DECOR_KINDS: dict[str, tuple[float, tuple[tuple[float, float, float, float, float, float], ...], tuple[str, ...], str]] = {
    # --- built: a room, a deck, a street ---------------------------------
    # Flat on the floor. Reads entirely as texture, which is the point.
    "rug": (0.08, ((0.10, 0.90, 0.18, 0.82, 0.0, 1.0),),
            (BUILT,), "#5a3238"),
    "sack": (1.6, ((0.30, 0.66, 0.32, 0.68, 0.0, 0.78),
                   (0.34, 0.60, 0.36, 0.62, 0.78, 1.0)),
             (BUILT, PAVED), "#7a6a4a"),
    # A pedestal under a bowl. The tallest thing here, and still under the cap.
    "brazier": (1.9, ((0.42, 0.58, 0.42, 0.58, 0.0, 0.62),
                      (0.32, 0.68, 0.32, 0.68, 0.62, 1.0)),
                (BUILT, PAVED), "#6a5b46"),
    "shards": (0.4, ((0.32, 0.52, 0.36, 0.56, 0.0, 1.0),
                     (0.54, 0.68, 0.52, 0.66, 0.0, 0.6)),
               (BUILT, PAVED, UNDER), "#8a7a63"),
    # --- anywhere the dead and the growing things reach -------------------
    # A skull and two long bones. NOT built out of boxes: at this size a box
    # with a paler box on top is a crate, and a meadow of them is a meadow of
    # crates — which is exactly what came back. It also leaves the WILD pool
    # here: bones belong where something died indoors or underground, and
    # twelve of them scattered over a hillside is a charnel field nobody asked
    # for.
    "bones": (0.5, (solid(_ring(0.11, 0.60, 0.58, 0.12),
                          _ring(0.08, 0.60, 0.58, 0.10), 0.0, 1.0),
                    solid(((0.16, 0.40), (0.62, 0.34), (0.62, 0.42),
                           (0.16, 0.48)),
                          ((0.20, 0.40), (0.58, 0.35), (0.58, 0.41),
                           (0.20, 0.46)), 0.0, 0.5),
                    solid(((0.22, 0.62), (0.56, 0.56), (0.56, 0.64),
                           (0.22, 0.70)),
                          ((0.26, 0.62), (0.52, 0.57), (0.52, 0.63),
                           (0.26, 0.68)), 0.0, 0.44)),
              (BUILT, PAVED, UNDER), "#c9c2ac"),
    # Roots through the floor: two tapering tendrils, not two planks.
    "roots": (0.6, (solid(((0.05, 0.40), (0.95, 0.46), (0.95, 0.56),
                           (0.05, 0.54)),
                          ((0.20, 0.42), (0.80, 0.47), (0.80, 0.53),
                           (0.20, 0.50)), 0.0, 0.7),
                    solid(((0.38, 0.05), (0.50, 0.05), (0.46, 0.95),
                           (0.36, 0.95)),
                          ((0.40, 0.20), (0.48, 0.20), (0.45, 0.80),
                           (0.38, 0.80)), 0.0, 0.9)),
              (UNDER, WILD), "#4a5a34"),
    # --- wild: what is actually lying about outdoors ----------------------
    # None of these existed, which is why every meadow was furnished like a
    # tavern. Each is under the cap and none of them is cover: a tuft of grass
    # and a fallen branch are things you walk over.
    # A tuft of coarse grass: fat at the ground, drawn to a point.
    "tussock": (0.9, (solid(_ring(0.20, 0.48, 0.52, 0.18),
                            _ring(0.05, 0.52, 0.46), 0.0, 1.0),
                      solid(_ring(0.13, 0.62, 0.44, 0.20),
                            _ring(0.03, 0.66, 0.40), 0.0, 0.74)),
                (WILD,), "#6c7a3c"),
    # A low shrub: a rounded mass, widest below the middle.
    "bush": (1.8, (solid(_ring(0.20, 0.48, 0.52, 0.10),
                         _ring(0.34, 0.48, 0.52, 0.16), 0.0, 0.44),
                   solid(_ring(0.34, 0.48, 0.52, 0.16),
                         _ring(0.10, 0.52, 0.48), 0.44, 1.0)),
             (WILD,), "#3f5a2e"),
    # A fallen branch: long and thin and lying where it dropped. A stick is one
    # of the few things that really is a box, so it stays one — tapered, and
    # with two smaller ones off it.
    "deadfall": (0.6, (solid(((0.06, 0.46), (0.94, 0.40), (0.94, 0.52),
                              (0.06, 0.58)),
                             ((0.10, 0.46), (0.90, 0.42), (0.90, 0.50),
                              (0.10, 0.54)), 0.0, 0.9),
                       (0.28, 0.46, 0.22, 0.48, 0.0, 0.5),
                       (0.60, 0.76, 0.52, 0.80, 0.0, 0.6)),
                 (WILD,), "#6b5636"),
    # Loose stones — the small ones. A rock big enough to hide behind is a
    # TILE, and a boulder big enough to be a landmark is a set piece.
    "stones": (0.7, (solid(_ring(0.18, 0.40, 0.42, 0.20),
                           _ring(0.09, 0.42, 0.44, 0.14), 0.0, 1.0),
                     solid(_ring(0.13, 0.64, 0.56, 0.22),
                           _ring(0.06, 0.62, 0.58, 0.16), 0.0, 0.62),
                     solid(_ring(0.10, 0.48, 0.70, 0.18),
                           _ring(0.05, 0.50, 0.68), 0.0, 0.44)),
               (WILD, PAVED, UNDER), "#8d8a82"),
    # A cut stump: a bole with the roots flaring out at the ground.
    "stump": (1.4, (solid(_ring(0.30, 0.5, 0.5, 0.22),
                          _ring(0.19, 0.5, 0.5, 0.10), 0.0, 1.0),
                    solid(_ring(0.40, 0.5, 0.5, 0.30),
                          _ring(0.28, 0.5, 0.5, 0.18), 0.0, 0.22)),
              (WILD,), "#6a5433"),
}

#: archetype -> which setting its scenery comes from.
#:
#: Unlisted archetypes are WILD, which is the right default for exactly the
#: reason ``open`` is the fallback layout: a board nobody has said anything
#: about is a patch of ground, and a patch of ground with a rug on it is the
#: failure this table exists to fix.
_SETTINGS: dict[str, str] = {
    "tavern": BUILT, "dungeon-room": BUILT, "dungeon-complex": BUILT,
    "crypt": BUILT,
    # Open to the sky and paved: nobody lays a rug on it, and things do not
    # grow out of it. A deck is the same question answered the same way.
    "street": PAVED, "arena": PAVED, "ship": PAVED, "skyship": PAVED,
    "cave": UNDER, "mine": UNDER, "sewer": UNDER,
}


def setting_for(archetype: str) -> str:
    """Which pool of scenery this kind of board draws from."""
    return _SETTINGS.get((archetype or "").strip().lower(), WILD)


def kinds_for(setting: str) -> list[str]:
    """The scenery a board of this setting may carry, sorted for determinism."""
    if not setting:
        return sorted(DECOR_KINDS)
    return sorted(k for k, v in DECOR_KINDS.items() if setting in v[2])


def colour_of(kind: str) -> str:
    """This kind's tint, as a hex string. One answer for both renderers."""
    got = DECOR_KINDS.get(kind)
    return got[3] if got else "#6a5b46"


#: Kinds that want a wall at their back, and how strongly.
_AGAINST_WALL = ("brazier", "sack")

#: Roughly what fraction of eligible floor gets something on it. Sparse on
#: purpose: decoration reads as detail when it is occasional and as clutter
#: when it is everywhere, and every piece is geometry the depth map carries.
DENSITY = 0.07


def _check_heights() -> None:
    tall = {k: v[0] for k, v in DECOR_KINDS.items()
            if v[0] >= MAX_DECOR_HEIGHT_FT}
    if tall:
        raise ValueError(
            f"decoration may never reach cover height: {tall} "
            f"(cap {MAX_DECOR_HEIGHT_FT} ft). If it deserves to be cover, "
            "it is a tile, not a decoration.")
    homeless = {k: v[2] for k, v in DECOR_KINDS.items()
                if not v[2] or any(s not in (BUILT, PAVED, WILD, UNDER)
                                   for s in v[2])}
    if homeless:
        raise ValueError(
            f"every decoration says where it belongs: {homeless}. A kind with "
            f"no setting is one that turns up in a meadow.")
    for setting in (BUILT, PAVED, WILD, UNDER):
        if not kinds_for(setting):
            raise ValueError(f"nothing to scatter on a {setting!r} board")


_check_heights()


def _hash(x: int, z: int, seed: int, salt: int) -> int:
    """Stable per-square hash. Same masking discipline as isocam's."""
    return ((x * 73856093) ^ (z * 19349663) ^ (seed * 83492791) ^ salt) & 0xFFFFFFFF


def decor_for(rows: Sequence[str], *, seed: int = 0,
              standing: Callable[[str], bool] | None = None,
              density: float = DENSITY,
              archetype: str = "") -> list[dict]:
    """Which squares carry decoration, and what. Deterministic for a layout.

    ``standing(code)`` says whether a code already has something on it (a wall,
    a crate, a pillar); those squares are skipped, as are hazards and anything
    off the floor. Returns ``[{"x", "y", "kind"}]``.

    ``archetype`` decides which pool the kinds come from — see
    :func:`setting_for`. Left empty every kind is eligible, which is what every
    caller written before settings existed got, and is wrong on any outdoor
    board: pass it.
    """
    from .terrain import tile

    stands = standing or (lambda c: False)
    out: list[dict] = []
    kinds = kinds_for(setting_for(archetype) if archetype else "")
    for z, row in enumerate(rows):
        for x, code in enumerate(row):
            if code == " " or stands(code):
                continue
            from .boardshapes import HOLE_CODES
            if code in HOLE_CODES:
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
