"""What a square is MADE OF, kept apart from what a square DOES.

**Why this exists.** The board has twenty-nine tile codes and twenty-one
archetypes, and until now a code meant exactly one thing everywhere. A ``#`` was
a wall — the same dressed masonry in a sewer, on a mountainside and around a
tent. So the mountain pass came back looking like a built corridor rather than a
canyon, the reef's columns stood pristine and carved on the seabed, and every
board's walls were the same wall.

The obvious fix — more tile codes — is the one the project has already ruled
out, and rightly: *"every new code costs rules meaning (cover, height,
breakability) and has to propagate to four renderers"*. A cliff and a wall are
not different RULES. They are both total cover, both block sight, both stop
movement. They are different STUFF.

So this is the same split :mod:`eight_card_system.placelore` already makes
between the land a place stands in and the surface it presents, applied one
level down:

* the **tile code** answers the rules — cover, movement, sight, breakability;
* the **skin** answers the eye — what it is made of, what shape it takes, and
  what the painter should be told about it.

Skins are invisible to every rule. Nothing in :mod:`vtt.geometry`,
:mod:`vtt.bridge` or the movement and cover code may read one, and none of it
does. That is the property that makes the vocabulary safe to grow: a new skin
can never change what a square does, so it can never break a fight.

**The one hard rule: a skin may not restate a height the rules quote.** A crate
screens four feet and a low wall three, and a player deciding whether they can
break line of sight is reading those off the board. A skin may reshape them —
a ship's rail is posts and a top rail rather than a slab — but
:func:`_check_heights` refuses at import any skin that changes how TALL they
stand. Walls and rock faces carry no quoted height (they are total cover at any
height), so those are free to vary, which is where all the useful variation is
anyway.

Two ways a square gets its skin, mirroring how the board already handles
elevation and decoration:

* **by archetype** — :data:`ARCH_SKINS` is a pure function of the generator that
  built the board, so it is DERIVED and never stored. Every ``R`` on a reef is
  coral; nothing has to write that down 300 times.
* **by square** — a generator that builds a specific thing (a tent's canvas
  inside a camp that also has a log palisade) records the exception on
  ``GeneratedMap.skins``, sparse and keyed ``"x,y"``, exactly like elevation.
"""
from __future__ import annotations

import math

from dataclasses import dataclass, field
from typing import Optional, Sequence

#: A shape part. There are two forms, and they are told apart by whether the
#: first element is a NUMBER — the same discriminator on both sides of the wire.
#:
#: **Box** — ``(x0, x1, z0, z1, y0, y1)``, the original and still the common
#: case: fractions of the square for x/z, fractions of the thing's standing
#: height for y. Axis-aligned, six numbers, cheap.
#:
#: **Solid** — ``(bottom, top, y0, y1)``, a PRISMATOID: two polygons of equal
#: vertex count, one at each height, joined by a quad per edge. This is the one
#: that stops everything being a cube, and it subsumes the box, so nothing had
#: to be rewritten to get it. What it buys, in the order it was asked for:
#:
#: * a top polygon narrower than the bottom is a **taper** — a tent's canvas
#:   drawn in to a ridge line, a hull with tumblehome, a hipped roof;
#: * a top polygon OFFSET from the bottom is a **lean** — the four slightly
#:   off-vertical poles a timber watchtower stands on, a ladder against a
#:   platform, a guy rope out to its peg;
#: * more than four vertices is a **cut corner**, which is what turns a
#:   stair-stepped hull outline into one continuous diagonal.
#:
#: A degenerate top (two coincident vertices) is a ridge; all-coincident is an
#: apex. Both are legal and neither needs a special case in either rasterizer.
Poly = tuple[tuple[float, float], ...]
Box = tuple[float, float, float, float, float, float]
Solid = tuple[Poly, Poly, float, float]
Part = Box | Solid
Variants = tuple[tuple[Part, ...], ...]


def _signed_area(pts: Poly) -> float:
    a = 0.0
    for i in range(len(pts)):
        x0, z0 = pts[i]
        x1, z1 = pts[(i + 1) % len(pts)]
        a += x0 * z1 - x1 * z0
    return a / 2.0


def solid(bottom: Sequence[tuple[float, float]],
          top: Sequence[tuple[float, float]],
          y0: float, y1: float) -> Solid:
    """A prismatoid part. ``bottom`` and ``top`` must have the same length.

    **The winding is normalized here rather than trusted.** Every face's normal
    is derived from its vertex order, so a ring written the wrong way round
    points its normals INTO the solid: the renderer then culls every face you
    should see and keeps every face you should not. That is not a subtle
    wrongness — the first watchtower roof simply did not appear, and nothing in
    either program looked broken. Since the correct order is a fact about the
    coordinate system and not about the shape, an author should not have to
    carry it, so it is fixed on the way in.
    """
    b, t = tuple(bottom), tuple(top)
    if len(b) != len(t):
        raise ValueError(f"a solid's two polygons must match: {len(b)} vs {len(t)}")
    # Counter-clockwise seen from above — the order the floor's own top face
    # uses, which is negative under this shoelace because z grows southward.
    if _signed_area(b) > 0:
        b, t = tuple(reversed(b)), tuple(reversed(t))
    return (b, t, float(y0), float(y1))


def ring(r: float, cx: float = 0.5, cz: float = 0.5, wob: float = 0.0,
          n: int = 8) -> tuple[tuple[float, float], ...]:
    """A rounded plan of ``n`` points, wobbled so it is not a wheel."""
    out = []
    for i in range(n):
        a = i * 2 * math.pi / n + math.pi / n
        k = r * (1.0 + wob * math.cos(3 * a))
        out.append((cx + math.cos(a) * k, cz + math.sin(a) * k))
    return tuple(out)



def rect(x0: float, x1: float, z0: float, z1: float
          ) -> tuple[tuple[float, float], ...]:
    """A four-point plan. The polygon form of the box everything used to be."""
    return ((x0, z0), (x1, z0), (x1, z1), (x0, z1))


def inset(poly: Sequence[tuple[float, float]], by: float,
           cx: float = 0.5, cz: float = 0.5) -> tuple[tuple[float, float], ...]:
    """The same plan pulled in toward a centre. A chamfer, in one call.

    ``by`` is a fraction of the distance to the centre, so a ring stays a ring
    and a rectangle stays a rectangle — which is what makes it safe to use as
    the TOP of a prismatoid: a chamfer that changed the shape of the plan would
    twist the faces between them.
    """
    k = 1.0 - by
    return tuple((cx + (x - cx) * k, cz + (z - cz) * k) for x, z in poly)


def slab(x0: float, x1: float, z0: float, z1: float, y0: float, y1: float,
          *, chamfer: float = 0.0, batter: float = 0.0) -> Part:
    """A block with its edges taken off — the workhorse of everything below.

    ``chamfer`` pulls the TOP plan in, which is what stops a lid, a coping or a
    table top reading as a slab of cheese. ``batter`` pulls the BOTTOM plan in,
    which is what makes a plinth sit rather than float. Both are zero by
    default, so this degrades to exactly the box it replaces.
    """
    base = rect(x0, x1, z0, z1)
    cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
    return solid(inset(base, batter, cx, cz), inset(base, chamfer, cx, cz),
                 y0, y1)


def is_solid(part: Part) -> bool:
    """Which of the two forms this part is. Mirrored by ``isSolid`` in the
    browser, where the same question is ``typeof part[0] !== "number"``."""
    return not isinstance(part[0], (int, float))


def _poly_area(pts: Poly) -> float:
    """Unsigned area of a footprint polygon, in fractions of a square."""
    a = 0.0
    for i in range(len(pts)):
        x0, z0 = pts[i]
        x1, z1 = pts[(i + 1) % len(pts)]
        a += x0 * z1 - x1 * z0
    return abs(a) / 2.0


@dataclass(frozen=True)
class Skin:
    """One material identity a square can wear.

    ``substance`` is the material-catalogue slug, so every coral thing on every
    reef in every session shares one swatch — the same economics as
    ``object_ref`` keying on the KIND of thing rather than the square.

    ``variants`` is an optional shape override. When present it wins over
    whatever the code would otherwise be drawn as, INCLUDING the wall-face model
    used for ``#`` and ``R`` — which is the point, because a mountainside drawn
    as thin wall panels is precisely what made the pass read as architecture.
    Several arrangements are better than one: :func:`vtt.isocam.variant_of`
    picks per square, so four chunky cliff variants read as natural rock where
    one would read as a repeated stamp.

    ``directional`` makes a run of squares line up along the way the run goes
    instead of taking a per-square quarter-turn. A rail, a palisade and a tent
    wall are things that RUN; a boulder is not.
    """

    name: str
    substance: str
    art: str
    #: What the painter is told. One clause, joined into the iso prompt.
    words: str = ""
    variants: Optional[Variants] = None
    #: Drawn height in feet, overriding the tile's own. Refused on any code the
    #: rules quote a height for — see the module docstring.
    height_ft: float = 0.0
    #: Line up along the run rather than taking a per-square quarter-turn.
    directional: bool = False
    #: Point at the OUTDOORS: turn so the part's authored +z side faces
    #: whichever way this square is not enclosed. Beats ``directional`` where
    #: both are set. What a tent needed and neither other rule could give — a
    #: wall that does not know which side the weather is on can only lean the
    #: same amount both ways, which is to say not at all.
    outward: bool = False
    #: Pick the arrangement from a COARSE hash, so neighbouring squares usually
    #: match. For anything that is a MASS rather than a set of objects: a rock
    #: face varying per square is a field of separate cubes (measured — the
    #: first mountain pass came back exactly that), where the same variation
    #: spread over two-square blocks reads as ridges and shelves. Objects want
    #: the opposite, which is why it is a flag and not the default.
    smooth: bool = False
    #: How deep this floor's own SIDE is, in feet. Non-zero means the square
    #: carries its own edge wherever it meets something that is not the same
    #: skin — which is how a ship gets a hull. The default skirt only fires
    #: against a HOLE, and deep water is not a hole, so a sea ship had no sides
    #: at all: a deck lying flat on the water like a raft.
    skirt_ft: float = 0.0
    #: How far the BOTTOM of that side pulls in, as a fraction of a square.
    #: A vertical drop is a slab; pulled in, the same two triangles become a
    #: trapezoid, and a hull with tumblehome stops reading as a box. The pull is
    #: PERPENDICULAR to the edge, never toward the square's own centre: two
    #: squares along one straight run then offset their bottoms identically and
    #: the faces stay coplanar, which is the whole difference between a hull and
    #: a row of separate cones. The tile grid is untouched — this is a drawing,
    #: like WALL_THICKNESS.
    skirt_inset: float = 0.0
    #: Squares of the same non-empty body are one THING, and no side is drawn
    #: between them. A ship is a deck, a rail round it, a mast through it and a
    #: cabin on it — four skins and one hull. Without this the deck grew a hull
    #: side against its own mast, so the board had a shaft sunk round the mast
    #: and a paper-thin outer rim where the rail met the water.
    body: str = ""
    #: May this surface SLOPE between squares?
    #:
    #: Elevation is stored per square as whole feet, so ground drawn at one
    #: height per square is a flight of terraces. Natural ground does not do
    #: that; a floor, a road, a quay and a deck are LAID, and laid things are
    #: flat. The tile code alone cannot answer it — ``.`` is scree on a
    #: mountain pass and cobbles on a street, which is exactly the distinction
    #: a skin exists to make — so the skin carries it and
    #: ``terrain.SOFT_GROUND`` is only the fallback for a square wearing none.
    soft: bool = False
    #: How far above this square's drawn height the ROOF's ridge stands, in
    #: feet. Non-zero means the squares wearing this skin are a BUILDING, and
    #: one roof is traced over each contiguous run of them (:func:`vtt.hull.
    #: roofs`) instead of a gable being drawn on every square.
    #:
    #: That distinction is the whole point. A roof is bigger than a square, so
    #: drawing it a square at a time gives a terrace of houses a SAWTOOTH of
    #: one-square huts — twelve little ridges over what the prompt calls
    #: "close-packed two-storey townhouses" — and no amount of shape authoring
    #: inside one square can fix it, because what is wrong is the size of the
    #: unit. Same argument as a vessel's hull, arriving from the other side.
    roof_ft: float = 0.0
    #: Where the eaves sit, as a fraction of the drawn height. Below this the
    #: square draws its own wall; above it the traced roof takes over.
    roof_at: float = 1.0
    #: Does this skin clothe ONE THING standing on a square, rather than the
    #: ground or a mass?
    #:
    #: It decides how much world one repeat of the swatch covers. Ground and
    #: rock in the mass want a big repeat, or the picture lands at the pitch of
    #: the grid and the board reads as a tile set; a thing that IS one square
    #: wants exactly one square of picture, or it is drawn a smeared fraction
    #: of one. The same substance answers both ways — granite is a cliff face
    #: AND a field stone — which is why this is on the skin and not on the
    #: substance table.
    standalone: bool = False
    #: What the ROOF is made of, when that is not the wall.
    #:
    #: The traced roof wore the building's own material, on the reasoning that
    #: it should not invent a colour of its own. A declared material is not an
    #: invented colour, and the reasoning cost a street its legibility: a town
    #: of lime-plastered houses came back white walls under white roofs, one
    #: pale mass with a road through it. This skin's OWN words have said "steep
    #: tiled roofs above" since the day it was written — the same complaint the
    #: chamfered taproom post and the chitin hull answered, where the prose
    #: described something the geometry flatly contradicted.
    #:
    #: Empty keeps the old behaviour, which is right wherever the roof really
    #: is the wall carried over: a ruin's soot-stained shell has no tiles left.
    roof_skin: str = ""
    #: Extra NEGATIVE terms for this skin's swatch, on top of
    #: ``art.MATERIAL_NEGATIVE``.
    #:
    #: Here because the alternative kept being attempted inside ``art``, where
    #: it does the opposite of what it says: a swatch prompt is a POSITIVE
    #: prompt, so "no plants, no water surface" is a request for plants and a
    #: water surface. The seabed asked for both and got an aerial photograph of
    #: a BEACH — surf, sand and palm fronds. What a material must not be is a
    #: negative, and it belongs next to the material.
    negative: str = ""
    #: What this skin keeps being MISTAKEN FOR, for the render's negative.
    #:
    #: Distinct from ``negative`` above, which is about the swatch photograph.
    #: This is about the painting: the catalogue already says what a thing IS
    #: (``words``), and that turns out not to be enough — a taproom's posts came
    #: back as lit candles at every shape and at 1.6 prompt weight leading the
    #: prompt, and one clause in the NEGATIVE fixed them outright.
    #:
    #: It lives on the SKIN because a negative applies to the whole picture and
    #: therefore must only be said when this material is actually on the board.
    #: "No stone floor" is right in a taproom and catastrophic in a crypt, whose
    #: floor is stone; the same rule that took `hearth` and `window` out of the
    #: shared negative. Same shape as ``Grid.absent_terrain_negative``: derived
    #: from what is present, never a global.
    #:
    #: **Only ever an OBJECT, never a MATERIAL.** Measured both ways on the
    #: same board. "candles" for a post the model keeps substituting: the
    #: flames go and nothing else moves. "flagstones, stone floor, paving…" for
    #: a floor that came back stone: the floor went GREEN, and the street doing
    #: the same to "planking" turned its surround green too. Forbidding a
    #: material does not redirect the picture toward the right material, it
    #: pushes the whole palette away from that one and the model lands wherever
    #: it likes.
    #:
    #: The distinction that predicts it: a post HAS a silhouette with a correct
    #: reading the model was missing, so removing the wrong reading recovers the
    #: right one. A flat floor carries no shape information at all, so removing
    #: an option supplies nothing in its place. Same reason the mountain pass
    #: got worse when houses and doors were forbidden.
    misread: str = ""
    #: Draw at exactly the height given, with no per-instance jitter.
    #:
    #: Jitter is life for a rock face and a lie for anything a neighbouring
    #: square has to MEET: the posts of a watchtower and the platform they hold
    #: up are two different squares, and twelve percent of fifteen feet is the
    #: platform floating clear of its own legs. Anything built, and anything a
    #: creature climbs, says so here.
    exact: bool = False


def _v(*arrangements: Sequence[Part]) -> Variants:
    return tuple(tuple(a) for a in arrangements)


# --------------------------------------------------------------------------
# Shapes
#
# Written out rather than generated, because each one is a silhouette somebody
# has to look at and judge. The model paints the shape it is handed: a square
# column comes back a square column, and a cliff drawn as a thin panel comes
# back as a wall however loudly the prompt says "canyon".
# --------------------------------------------------------------------------

#: Rock, drawn as MASS.
#:
#: The wall-face model this replaces exists to stop an enclosed ROOM reading as
#: a tray, which is the right worry for masonry and the wrong one for a
#: mountainside — a pass is a canyon cut through solid rock, and the rock should
#: look solid.
#:
#: Two things learned from the first attempt, which came back as a field of
#: white DICE. Footprints must run the FULL square: inset even slightly, each
#: square is its own little block with a visible gap round it, and a hundred of
#: them is a bead curtain rather than a cliff. And the variation has to be in
#: the HEIGHT rather than in fussy stacked nubs on top — a rock face reads as
#: rock because its skyline is broken, and neighbouring squares at 100%, 74% and
#: 46% of the same height do that where three differently-decorated cubes of
#: equal height do not. (`height_scale`'s jitter is only 12%, deliberately, and
#: nowhere near enough on its own.)
#: A third thing learned, and it is why the footprints run PAST the square. A
#: shell one square thick beside a wandering track steps diagonally as often as
#: it steps square, and two diagonal neighbours meet at a single point — so a
#: hundred flush cliff squares came back as a picket of separate towers with
#: daylight between them. Bulging each block a tenth of a square makes diagonal
#: neighbours overlap and the face closes. It costs the track about seven inches
#: of drawn width, which is nothing, and a rock face that bulges slightly over
#: its own foot is what rock does.
#: A fourth thing learned, and it is the one that cost a village. Those
#: footprints were BOXES — vertical sides, flat tops, four right angles — at six
#: different heights, and a field of flat-topped boxes at varied heights is not
#: a cliff, it is a hill town. Rendered, the pass came back as snowy cottages
#: with wooden doors in them, and three attempts to argue the model out of it
#: from the painter's side (a colour init, a middle denoise, an explicit
#: negative) each failed in their own way: it invented carved stone instead, or
#: it flattened the whole board into grey blocks. Nothing in a prompt beats the
#: shape, which is the crypt-of-dice lesson (see ``isocam.OBJECT_VARIANTS``)
#: arriving outdoors.
#:
#: So a cliff square is a PRISMATOID: the footprint still runs the full square
#: and past it — everything above about merging still holds — and everything
#: above the ground is battered, canted and irregular. No vertical face, no flat
#: top, no right angle in plan. The bottoms are the only part still square, and
#: they are buried between neighbours where nothing can see them.
_OVER = 0.10
_BASE: Poly = ((-_OVER, -_OVER), (1 + _OVER, -_OVER),
               (1 + _OVER, 1 + _OVER), (-_OVER, 1 + _OVER))


def _crag(top: Poly, y1: float, y0: float = 0.0) -> Solid:
    """A mass rising from the full square to an irregular canted cap."""
    return solid(_BASE, top, y0, y1)


_CLIFF = _v(
    # A single tall canted mass, leaning off the vertical in both axes.
    (_crag(((0.18, 0.10), (0.92, 0.24), (0.80, 0.94), (0.10, 0.74)), 1.00),),
    # A broad shoulder with a spur riding up out of it, off to one side.
    (_crag(((0.04, 0.14), (1.00, 0.02), (1.06, 0.90), (0.02, 1.02)), 0.74),
     solid(((0.14, 0.22), (0.82, 0.10), (0.88, 0.80), (0.20, 0.90)),
           ((0.30, 0.40), (0.66, 0.34), (0.70, 0.66), (0.34, 0.72)),
           0.74, 0.94)),
    # Leaning hard: the cap is thrown a third of a square east of its foot,
    # which is what an undercut looks like from this camera.
    (_crag(((0.36, 0.12), (1.08, 0.22), (1.02, 0.86), (0.30, 0.96)), 0.62),
     solid(((0.40, 0.20), (1.00, 0.28), (0.96, 0.80), (0.36, 0.88)),
           ((0.52, 0.36), (0.86, 0.42), (0.82, 0.70), (0.50, 0.66)),
           0.62, 0.86)),
    # Tall and narrow-topped — a fin, standing out of the face.
    (_crag(((0.24, 0.30), (0.78, 0.18), (0.86, 0.72), (0.30, 0.84)), 0.88),
     solid(((0.30, 0.36), (0.72, 0.26), (0.78, 0.66), (0.34, 0.76)),
           ((0.46, 0.48), (0.60, 0.44), (0.62, 0.60), (0.48, 0.62)),
           0.88, 1.00)),
    # Low and nearly whole: a shelf the track runs under, barely battered.
    (_crag(((0.00, 0.06), (1.04, -0.02), (1.08, 1.00), (-0.04, 1.06)), 0.54),),
    # A tilted slab: the cap swung round, so its skyline is a diagonal.
    (_crag(((0.10, 0.02), (0.94, 0.22), (1.02, 0.90), (0.02, 0.78)), 0.70),
     solid(((0.14, 0.10), (0.86, 0.28), (0.92, 0.84), (0.08, 0.72)),
           ((0.26, 0.28), (0.62, 0.40), (0.66, 0.72), (0.24, 0.62)),
           0.70, 0.96)),
)

#: A fallen boulder: a rounded lump, and emphatically NOT a piece of cliff.
#:
#: The pass mapped its scattered rock onto the cliff skin, so every boulder was
#: drawn as the cliff is — a full-square footprint fourteen feet tall — and a
#: hundred of them made the whole board a field of white dice. That was read as
#: a variety problem and it was a CATEGORY problem: a cliff is a mass and wants
#: to fill its square so its neighbours merge into a face, and a boulder is an
#: object standing alone on open ground and wants a silhouette. They are two
#: different things that happened to be made of the same granite, which is
#: exactly the distinction a skin exists to draw.
#:
#: Eight-sided in plan and battered inward, because a rock the model is handed
#: as a prism comes back as a rock and one it is handed as a cube comes back as
#: masonry.
def _ring8(r: float, cx: float = 0.5, cz: float = 0.5) -> Poly:
    """A plain eight-sided plan — what a column, a drum or a post is in plan."""
    import math as _m
    return tuple((cx + _m.cos(i * _m.pi / 4 + _m.pi / 8) * r,
                  cz + _m.sin(i * _m.pi / 4 + _m.pi / 8) * r) for i in range(8))


def _lump(r: float, top_r: float, cx: float = 0.5, cz: float = 0.5,
          skew: float = 0.0) -> tuple[Poly, Poly]:
    """A rounded plan and a smaller one above it, for a weathered lump."""
    import math as _m
    base, cap = [], []
    for i in range(8):
        a = i * _m.pi / 4 + _m.pi / 8
        wob = 1.0 + skew * _m.cos(3 * a)
        base.append((cx + _m.cos(a) * r * wob, cz + _m.sin(a) * r * wob))
        cap.append((cx + _m.cos(a) * top_r * wob, cz + _m.sin(a) * top_r * wob))
    return tuple(base), tuple(cap)


_BOULDER = _v(
    (solid(*_lump(0.46, 0.26, skew=0.10), 0.00, 0.86),
     solid(*_lump(0.26, 0.05, 0.52, 0.48), 0.86, 1.00)),
    (solid(*_lump(0.44, 0.30, 0.46, 0.54, skew=-0.12), 0.00, 0.62),
     solid(*_lump(0.24, 0.10, 0.40, 0.44), 0.62, 0.94)),
    (solid(*_lump(0.48, 0.34, skew=0.16), 0.00, 0.44),
     solid(*_lump(0.30, 0.16, 0.56, 0.42), 0.44, 0.80),
     solid(*_lump(0.18, 0.06, 0.34, 0.62), 0.44, 0.66)),
    (solid(*_lump(0.40, 0.14, 0.50, 0.50, skew=0.08), 0.00, 1.00),),
)

#: A town street's walls are BUILDINGS, and they were drawn as garden walls.
#:
#: ``#`` unskinned is the wall-face model at ten feet — right for a room, and on
#: a street it is 43% of the board drawn as a waist-high slab. Rendered, the
#: market came back as a plank floor with wooden FENCES around it and no
#: buildings anywhere: the model painted the only thing a ten-foot slab beside a
#: paved strip can be.
#:
#: So a street square is a house: a solid front to two thirds of its height and
#: a ROOF above that, which is what says "building" from a camera on the
#: ceiling. ``smooth`` makes neighbouring squares share an arrangement, so a run
#: of them is a terrace with one ridge line rather than a row of separate huts.
_ROOF_LOW, _ROOF_HIGH = 0.62, 0.70
#: A taproom's post: a SQUARE oak stick with a brace at its head, not a column.
#:
#: `O` is drawn as an octagonal prism, which is a stone pillar, and painted as
#: one — the first taproom to grow posts came back with a dozen candles standing
#: on the floor. A post is square in section, chamfered, and carries a brace out
#: to the beam it holds, and the brace is the whole tell: a stick with nothing
#: on top of it is a candle, and a stick with a bracket is carpentry.
#: THICK, and that is the whole of the second attempt. A slim shaft with a
#: bright head is a CANDLE, and a taproom full of them is what came back — the
#: model reads a thin vertical stick in a lit room as a candlestand, at every
#: denoise. A post is two feet through, sits on a plinth, and carries a bracket
#: that runs the full width of its square at the head, which is a shape no
#: candle has.
#: A taproom post: a chamfered oak post on a stone pad, carrying a beam.
#:
#: Its own ``words`` have said "chamfered" since the day it was written, and it
#: was a plain box — the prompt describing something the silhouette flatly
#: contradicted, which is the arrangement the whole shape table exists to
#: prevent. An eight-sided plan IS the chamfer, the pad spreads under it, and
#: the beam it carries gets its arris taken off so the two do not read as one
#: cross of the same stuff.
_POST = _v(
    (slab(0.22, 0.78, 0.22, 0.78, 0.0, 0.07, chamfer=0.22),
     solid(ring(0.21), ring(0.19), 0.07, 1.0),
     solid(rect(0.00, 1.00, 0.41, 0.59), rect(0.00, 1.00, 0.43, 0.57),
           0.88, 0.97)),
    (slab(0.22, 0.78, 0.22, 0.78, 0.0, 0.07, chamfer=0.22),
     solid(ring(0.21), ring(0.19), 0.07, 1.0),
     solid(rect(0.41, 0.59, 0.00, 1.00), rect(0.43, 0.57, 0.00, 1.00),
           0.88, 0.97)),
)

#: A townhouse WALL — and only the wall. The roof used to be here, a gable per
#: square, which is why a street of them came back a sawtooth of huts; it is
#: traced over the whole building now (:func:`vtt.hull.roofs`).
#:
#: FLUSH, and that is a lesson rather than a preference. A batter — a wall
#: leaning in as it rises — is a property of a MASS, and applied per square it
#: makes a wedge-shaped gap with every neighbour: a terrace came back with a
#: bright hairline slot up the face at every square boundary. Where a shape
#: belongs to something bigger than a square, the square is the wrong place to
#: put it, which is the same sentence the roof above is an answer to.
_TOWNHOUSE = _v(
    ((0.0, 1.0, 0.0, 1.0, 0.0, _ROOF_HIGH),),
)


#: Half cover, four feet, out in a field: that is a BOULDER, not a crate.
#:
#: The tile is ``o`` and the generators scatter it outdoors for exactly the
#: mechanical reason they scatter it indoors — a waist-high thing to get behind,
#: which is what makes an open board a fight rather than a shooting gallery. The
#: code is right and the picture was not: a meadow came back with twenty-one
#: crates standing in it, and it was not the model inventing them, it was the
#: grid asking for them. This is the whole skins argument in one square — a tile
#: says what a square DOES and a skin says what it is MADE OF.
#:
#: No ``height_ft``: four feet is a number the RULES quote for this code, and a
#: skin may reshape a quoted height but never restate one. These are drawn to
#: the tile's own four feet, rounded instead of boxed.
_FIELD_STONE = _v(
    (solid(*_lump(0.42, 0.24, skew=0.12), 0.00, 0.82),
     solid(*_lump(0.22, 0.08, 0.54, 0.46), 0.82, 1.00)),
    (solid(*_lump(0.46, 0.30, 0.46, 0.54, skew=-0.14), 0.00, 0.70),
     solid(*_lump(0.24, 0.10, 0.42, 0.48), 0.70, 0.92)),
    (solid(*_lump(0.38, 0.16, 0.52, 0.48, skew=0.18), 0.00, 1.00),),
)

#: A dry ruin's snapped column: a ROUND drum, because a column is round.
#:
#: ``drowned-column`` was doing this job on land and it is built out of boxes —
#: fine at the bottom of the sea where everything is furred over, and on a ruins
#: board there are thirty-five of them and they came back painted as garden
#: BENCHES. An unskinned ``O`` is already drawn as an eight-sided prism for
#: exactly this reason; a skin that replaces it has to keep the roundness and
#: only break the top.
_SNAPPED_COLUMN = _v(
    (solid(_ring8(0.30), _ring8(0.28), 0.0, 0.62),
     solid(_ring8(0.29), _ring8(0.22, 0.54, 0.46), 0.62, 0.72)),
    (solid(_ring8(0.31), _ring8(0.27), 0.0, 0.86),
     solid(_ring8(0.28), _ring8(0.20, 0.46, 0.56), 0.86, 0.96)),
    # A stub, with its own fallen drum lying beside it.
    (solid(_ring8(0.32), _ring8(0.29), 0.0, 0.30),
     solid(_ring8(0.20, 0.70, 0.62), _ring8(0.18, 0.74, 0.66), 0.0, 0.22)),
    (solid(_ring8(0.30), _ring8(0.26, 0.54, 0.47), 0.0, 0.48),
     solid(_ring8(0.26, 0.54, 0.47), _ring8(0.22, 0.58, 0.44), 0.48, 0.58)),
)

#: Coral heads: lower, lumpier, branching. Same idea as the cliff and a
#: different silhouette, so a reef never reads as a quarry.
#: Rewritten as MASSES. The first version was thin vertical boxes in clusters —
#: which is a stand of REEDS, and that is exactly what every reef came back
#: painted as, on a board whose seabed conditioning was by then correct. Coral
#: is domes, plates and thick antlers; nothing on a reef is a stalk.
_CORAL = _v(
    # Brain coral: a boulder-shaped dome, deeply wrinkled.
    (solid(*_lump(0.38, 0.34, skew=0.14), 0.0, 0.46),
     solid(*_lump(0.34, 0.14, 0.52, 0.48, skew=0.10), 0.46, 0.74)),
    # Table coral: a short stem under a wide flat plate.
    (solid(*_lump(0.12, 0.16), 0.0, 0.44),
     solid(*_lump(0.44, 0.42, 0.48, 0.52, skew=0.16), 0.44, 0.56),
     solid(*_lump(0.18, 0.10, 0.66, 0.62), 0.0, 0.34)),
    # Antler coral: three THICK arms leaning out of one foot.
    (solid(*_lump(0.26, 0.20), 0.0, 0.24),
     solid(_lump(0.14, 0.09, 0.40, 0.44)[0], _lump(0.09, 0.06, 0.26, 0.30)[1],
           0.24, 0.86),
     solid(_lump(0.13, 0.08, 0.58, 0.46)[0], _lump(0.08, 0.05, 0.74, 0.34)[1],
           0.24, 0.70),
     solid(_lump(0.12, 0.08, 0.50, 0.62)[0], _lump(0.07, 0.05, 0.56, 0.80)[1],
           0.24, 0.92)),
    # A low knuckle of dead coral rock with a small dome beside it.
    (solid(*_lump(0.44, 0.38, 0.46, 0.50, skew=-0.12), 0.0, 0.34),
     solid(*_lump(0.22, 0.10, 0.62, 0.40), 0.34, 0.62)),
)

#: A snapped column. Ruins and drowned ruins want the pillar to have ALREADY
#: fallen — an intact colonnade on a seabed is a stranger sight than a broken
#: one, and the board had no way to say so.
#: ROUND, because a column is turned and a drum is a drum. Squared off, a
#: colonnade of these read as a field of broken fenceposts — which is the
#: crypt-of-dice failure again, on the seabed. The break is a plan slightly
#: wider than the drum below it, since stone shears out rather than in.
_BROKEN_COLUMN = _v(
    (solid(ring(0.20), ring(0.185), 0.0, 0.44),
     solid(ring(0.195), ring(0.175, 0.52, 0.47), 0.44, 0.52)),
    (solid(ring(0.20), ring(0.18), 0.0, 0.70),
     solid(ring(0.19, 0.49, 0.45), ring(0.16, 0.47, 0.43), 0.70, 0.78)),
    (solid(ring(0.185), ring(0.175), 0.0, 0.28),
     # The rest of it, lying where it fell: a drum on its side is an oval in
     # plan, and this one is snapped at both ends.
     solid(ring(0.13, 0.48, 0.35, n=8), ring(0.12, 0.48, 0.35, n=8), 0.0, 0.14)),
    (solid(ring(0.19, 0.47, 0.53), ring(0.175, 0.47, 0.53), 0.0, 0.58),
     solid(ring(0.18, 0.46, 0.52), ring(0.155, 0.45, 0.51), 0.58, 0.66),
     solid(ring(0.15, 0.76, 0.42, n=8), ring(0.14, 0.76, 0.42, n=8), 0.0, 0.16)),
)

#: A mast: a pole, a yard across it, and a top. One variant on purpose — a ship
#: has one mast and it is not a random thing.
#: A mast: a round spar, thicker at the partners than at the head, with two
#: yards crossed on it. Square-sectioned it was a post with two planks nailed
#: across — and a mast is the one thing on a ship's deck that is unmistakably
#: turned.
_MAST = _v(
    (slab(0.32, 0.68, 0.32, 0.68, 0.0, 0.06, chamfer=0.24),
     solid(ring(0.085), ring(0.055), 0.06, 1.0),
     solid(rect(0.06, 0.94, 0.455, 0.545),
           rect(0.10, 0.90, 0.47, 0.53), 0.60, 0.655),
     solid(rect(0.20, 0.80, 0.465, 0.535),
           rect(0.24, 0.76, 0.478, 0.522), 0.84, 0.875)),
)

#: A ship's rail: stanchions and a top rail, so you can see the sea THROUGH it.
#: Drawn along the run, and at exactly the three feet the rules quote — the
#: shape changes, the height may not.
_RAILING = _v(
    (solid(ring(0.062, 0.14, 0.5), ring(0.05, 0.14, 0.5), 0.0, 1.0),
     solid(ring(0.062, 0.50, 0.5), ring(0.05, 0.50, 0.5), 0.0, 1.0),
     solid(ring(0.062, 0.86, 0.5), ring(0.05, 0.86, 0.5), 0.0, 1.0),
     # The top rail is moulded — wider at its underside than its top, which is
     # what a handrail is and what stops it reading as a plank on sticks.
     solid(rect(0.00, 1.00, 0.425, 0.575),
           rect(0.00, 1.00, 0.445, 0.555), 0.80, 1.00),
     solid(rect(0.00, 1.00, 0.455, 0.545),
           rect(0.00, 1.00, 0.465, 0.535), 0.34, 0.46)),
)

#: A palisade: sharpened logs shoulder to shoulder, pointed tops.
#:
#: ROUND, and that is the whole difference. Written as boxes this was a row of
#: square posts with smaller square posts on top — a fence of table legs — and
#: at this camera the top face is most of what you see, so a square top says
#: "sawn timber" however loudly the words say "split logs". A log is a taper
#: with a point on it, and the point is a taper to almost nothing rather than
#: to an apex: an exact apex is a spike, and a palisade of spikes reads as a
#: trap rather than a wall.
def _log(cx: float, r: float, top: float) -> tuple:
    return (solid(ring(r, cx, 0.5), ring(r * 0.94, cx, 0.5), 0.0, top),
            solid(ring(r * 0.94, cx, 0.5), ring(r * 0.18, cx, 0.5),
                  top, min(1.0, top + 0.12)))


_PALISADE = _v(
    (*_log(0.12, 0.13, 0.86), *_log(0.38, 0.14, 0.92),
     *_log(0.64, 0.13, 0.84), *_log(0.89, 0.14, 0.90)),
)

#: A tent wall: one canvas plane running from the pegs up and INWARD.
#:
#: Four versions of this now, and the history is the argument for both the
#: prismatoid and the outward rule. The first was a mild lean at nine feet, and
#: every camp came back with three timber PENS in it. The second cut the slope
#: into four stacked boxes, which is a ziggurat: from above, four terraces of
#: canvas. The third was a genuine slope — and still nearly vertical, because
#: it leaned the same amount toward both faces of the wall, and a square is
#: five feet wide against a tent seven feet tall. None of them was a material
#: problem; the material was canvas throughout. The model paints the
#: silhouette it is handed.
#:
#: Authored with the OUTDOORS at +z, which ``outward`` then turns to face
#: whichever way that actually is. The square stays solid to sight — it is a
#: wall and remains one — but its SURFACE is a pitch instead of a face.
#: **Arrangement 0 is the run and arrangement 1 is the CORNER**, by the same
#: convention every outward skin uses. Four of the twelve squares in a tent's
#: ring face the weather on two sides at once, and a shape aimed at one of them
#: leaves the other a sheer face — so every tent had two pitched sides and two
#: cliffs. The corner is a HIP: one canvas plane drawn in toward the inside
#: corner from both outward edges at once, which is what a real tent does where
#: two runs of canvas meet.
_TENT_WALL = _v(
    (solid(((0.00, 0.00), (1.00, 0.00), (1.00, 1.00), (0.00, 1.00)),
           ((0.00, 0.00), (1.00, 0.00), (1.00, 0.20), (0.00, 0.20)),
           0.00, 0.72),
     # The eaves pole the canvas is lashed over.
     (0.00, 1.00, 0.02, 0.20, 0.70, 0.80),
     # Guy ropes out past the pegs. They reach BEYOND the square on purpose: a
     # rope is pegged into the ground outside the tent, and a part is an
     # offset, so it is allowed to say so.
     solid(((0.20, 1.20), (0.28, 1.20), (0.28, 1.28), (0.20, 1.28)),
           ((0.22, 0.10), (0.28, 0.10), (0.28, 0.18), (0.22, 0.18)),
           0.00, 0.68),
     solid(((0.72, 1.20), (0.80, 1.20), (0.80, 1.28), (0.72, 1.28)),
           ((0.72, 0.10), (0.78, 0.10), (0.78, 0.18), (0.72, 0.18)),
           0.00, 0.68)),
    # The hip. Outdoors on +z AND +x, so the canvas is drawn in toward the one
    # inside corner — a five-sided footprint pulling to a small cap over it.
    (solid(((0.00, 0.00), (1.00, 0.00), (1.00, 1.00), (0.00, 1.00)),
           ((0.00, 0.00), (0.20, 0.00), (0.20, 0.20), (0.00, 0.20)),
           0.00, 0.72),
     # Both eaves poles, meeting over the corner post.
     (0.02, 0.22, 0.02, 1.00, 0.70, 0.80),
     (0.02, 1.00, 0.02, 0.22, 0.70, 0.80),
     # The corner pole, and one guy rope out along the diagonal.
     (0.78, 0.96, 0.78, 0.96, 0.00, 0.66),
     solid(((1.16, 1.16), (1.26, 1.16), (1.26, 1.26), (1.16, 1.26)),
           ((0.80, 0.80), (0.88, 0.80), (0.88, 0.88), (0.80, 0.88)),
           0.00, 0.64)),
)

#: The canvas OVER a tent, on the squares you stand on inside it.
#:
#: A wall ring with a walkable floor in it is, seen from above, a roofless box —
#: which is exactly what the camps came back as and what "they look like pens"
#: meant. The roof has to be a separate thing because the interior is separate
#: squares, and it can be one: nothing decorative may reach cover height, and a
#: sheet of canvas six feet up reaches nothing at all. It starts well clear of
#: the floor, so :func:`occludes_floor` passes it and the square stays as
#: walkable in the picture as it is in the rules.
#:
#: FLAT, and that is the point rather than a shortcut. Each square would
#: happily carry its own little ridge, and four of those side by side is
#: corrugated iron — measured, and it is what the first roofed tents came back
#: as. A roof has to be continuous across squares that cannot see each other,
#: and the shape with that property is the one that does not vary. The PITCH
#: lives in the walls, where the outward rule can aim it; this is the span
#: between them, with the ridge batten on top.
_TENT_CANOPY = _v((
    (0.00, 1.00, 0.00, 1.00, 0.66, 0.76),
    (0.00, 1.00, 0.42, 0.58, 0.76, 0.84),
))

#: A tower wall: a tall solid mass with merlons crowning it.
#:
#: This replaces putting a nine-foot PARAPET on the ground storey, which is
#: what a watchtower's walls were built from and is why they came back as four
#: low crenellated boxes instead of towers. A parapet is the thing you crouch
#: behind on the roof; the storey below it is a wall, and it has to be tall
#: enough that the platform on top reads as raised.
#:
#: Two arrangements with the merlons in different places, quarter-turned per
#: square, so a ring of them crenellates unevenly instead of marching.
#: A stone tower: a plain wall under CAPPED merlons.
#:
#: The wall is flush for the reason `_TOWNHOUSE` is — a batter is a property of
#: a mass and per square it opens a slot against every neighbour. The merlons
#: are where the shape lives, and they are single squares, so they may be cut.
_TOWER = _v(
    ((0.00, 1.00, 0.00, 1.00, 0.0, 0.86),
     slab(0.02, 0.40, 0.02, 0.40, 0.86, 1.00, chamfer=0.10),
     slab(0.60, 0.98, 0.02, 0.40, 0.86, 1.00, chamfer=0.10)),
    ((0.00, 1.00, 0.00, 1.00, 0.0, 0.86),
     slab(0.30, 0.70, 0.02, 0.40, 0.86, 1.00, chamfer=0.10),
     slab(0.02, 0.34, 0.60, 0.98, 0.86, 1.00, chamfer=0.10)),
)

#: A doorway: two jambs and a lintel over an OPEN passage.
#:
#: Needed because an open doorway is a walkable square with no height, so in a
#: sixteen-foot tower wall it drew as a gap running from the ground to the sky —
#: which reads as a missing wall, not a way in. The passage is left clear below
#: the lintel; the square stays exactly as walkable as it always was.
_DOORWAY = _v(
    # Jambs SPLAYED — a doorway cut through a thick wall is wider on the
    # outside than in the reveal, which is the only cue at this angle that the
    # opening is cut through something rather than painted on it.
    (solid(rect(0.00, 0.17, 0.26, 0.74), rect(0.00, 0.13, 0.28, 0.72),
           0.00, 1.00),
     solid(rect(0.83, 1.00, 0.26, 0.74), rect(0.87, 1.00, 0.28, 0.72),
           0.00, 1.00),
     solid(rect(0.00, 1.00, 0.26, 0.74), rect(0.00, 1.00, 0.28, 0.72),
           0.58, 1.00)),
)

#: A tent flap: the canvas rolled back at the jambs, a valance across the top.
#: The same job as _DOORWAY and the same rule — the passage stays clear, because
#: the square is one the rules let you walk through.
#: Authored facing the outdoors at +z, like the wall it interrupts, so the way
#: in is on the side somebody would actually walk up to.
_FLAP = _v((
    solid(((0.00, 0.00), (0.17, 0.00), (0.17, 1.00), (0.00, 1.00)),
          ((0.00, 0.00), (0.13, 0.00), (0.13, 0.30), (0.00, 0.30)),
          0.00, 0.70),
    solid(((0.83, 0.00), (1.00, 0.00), (1.00, 1.00), (0.83, 1.00)),
          ((0.87, 0.00), (1.00, 0.00), (1.00, 0.30), (0.87, 0.30)),
          0.00, 0.70),
    (0.00, 1.00, 0.00, 0.24, 0.56, 0.78),
))

#: One leg of a timber watchtower: a pole, footed wide and leaning in.
#:
#: A stone tower is a building with a room in it, and the walled shelter is the
#: right shape for one. A timber tower is not a building at all — it is four
#: poles holding a platform up, open underneath, and you walk between the legs
#: rather than through a door. Drawing it as a stockade got a squat wooden box;
#: the thing that says "watchtower" is the LEGS and the daylight between them.
#:
#: The lean is small and it is the whole trick. Four exactly vertical posts read
#: as machined columns; battered a few inches over fifteen feet, they read as
#: cut trees somebody stood up in a hurry — which is what they are.
_TOWER_POST = _v((
    solid(((0.26, 0.26), (0.58, 0.26), (0.58, 0.58), (0.26, 0.58)),
          ((0.40, 0.40), (0.62, 0.40), (0.62, 0.62), (0.40, 0.62)),
          0.00, 0.88),
    # The head, squared off to take the platform's beams.
    (0.32, 0.72, 0.32, 0.72, 0.88, 1.00),
))

#: The platform a timber tower holds up, its rail, and the roof over it.
#:
#: All three live on ONE square — the middle of the tower's footprint — and
#: reach out over the rest of it. That is deliberate rather than lazy: the
#: platform is a REAL upper storey with its own terrain grid, and only one
#: storey is ever drawn at a time, so from the ground floor the platform is
#: something you look at rather than something you are on. Without this the
#: tower read as four poles and a roof with a gap where the floor should be.
#:
#: Coordinates run past the square on purpose. A part is an offset, so ``-2.0``
#: is two squares west; the shape is symmetric about the square's own centre so
#: a quarter turn leaves it exactly where it was, which is why the post tower is
#: an ODD five squares across.
_TOWER_TOP = _v((
    # The deck, whose top is the storey's own fifteen feet exactly.
    (-2.10, 3.10, -2.10, 3.10, 0.44, 0.50),
    # A rail round it. The platform's own floor carries a real low wall — three
    # feet, half cover; this is what that low wall looks like from underneath.
    (-2.10, 3.10, -2.10, -1.90, 0.50, 0.60),
    (-2.10, 3.10, 2.90, 3.10, 0.50, 0.60),
    (-2.10, -1.90, -1.90, 2.90, 0.50, 0.60),
    (2.90, 3.10, -1.90, 2.90, 0.50, 0.60),
    # Four corner uprights carrying the roof, with daylight under the eave so
    # the platform is somewhere you can see people standing.
    (-1.95, -1.65, -1.95, -1.65, 0.58, 0.72),
    (2.65, 2.95, -1.95, -1.65, 0.58, 0.72),
    (-1.95, -1.65, 2.65, 2.95, 0.58, 0.72),
    (2.65, 2.95, 2.65, 2.95, 0.58, 0.72),
    # The roof: a hipped pyramid, eaves just proud of the rail.
    solid(((-2.30, -2.30), (3.30, -2.30), (3.30, 3.30), (-2.30, 3.30)),
          ((0.34, 0.34), (0.66, 0.34), (0.66, 0.66), (0.34, 0.66)),
          0.70, 0.98),
    (0.36, 0.64, 0.36, 0.64, 0.98, 1.00),
))

#: A ladder leaning against a platform — two stringers and the rungs.
#:
#: The connector already existed and the board already marked it, but a mark on
#: the floor is a thing you read and a ladder is a thing you SEE. It is drawn on
#: the square the connector is on, so what the picture shows and what
#: ``take_stairs`` accepts are the same square by construction.
_LADDER = _v((
    solid(((0.24, 0.84), (0.33, 0.84), (0.33, 0.93), (0.24, 0.93)),
          ((0.24, 0.44), (0.33, 0.44), (0.33, 0.53), (0.24, 0.53)),
          0.00, 1.00),
    solid(((0.67, 0.84), (0.76, 0.84), (0.76, 0.93), (0.67, 0.93)),
          ((0.67, 0.44), (0.76, 0.44), (0.76, 0.53), (0.67, 0.53)),
          0.00, 1.00),
    # Rungs, and a rung is a stick: round, and slightly thinner at the middle
    # where a hundred boots have worn it.
    solid(rect(0.26, 0.74, 0.805, 0.875), rect(0.26, 0.74, 0.815, 0.865),
          0.14, 0.18),
    solid(rect(0.26, 0.74, 0.725, 0.795), rect(0.26, 0.74, 0.735, 0.785),
          0.36, 0.40),
    solid(rect(0.26, 0.74, 0.645, 0.715), rect(0.26, 0.74, 0.655, 0.705),
          0.58, 0.62),
    solid(rect(0.26, 0.74, 0.565, 0.635), rect(0.26, 0.74, 0.575, 0.625),
          0.80, 0.84),
))

#: A stone parapet: a merloned roof edge, for the platform ON TOP of a tower.
_PARAPET = _v(
    (solid(rect(0.00, 1.00, 0.28, 0.72), rect(0.00, 1.00, 0.31, 0.69),
           0.0, 0.62),
     slab(0.00, 0.30, 0.26, 0.74, 0.62, 1.00, chamfer=0.11),
     slab(0.42, 0.72, 0.26, 0.74, 0.62, 1.00, chamfer=0.11)),
)

#: Steampunk plating: riveted panels and a pipe run along the hull.
_PLATING = _v(
    (solid(rect(0.00, 1.00, 0.31, 0.69), rect(0.00, 1.00, 0.33, 0.67),
           0.0, 0.84),
     # A capping strake that throws out over the plate below it.
     slab(0.00, 1.00, 0.24, 0.76, 0.84, 1.0, chamfer=0.09),
     # The pipe run is a PIPE — the one round thing on a riveted hull, and
     # square it was indistinguishable from the panels it runs across.
     solid(ring(0.055, 0.22, 0.25), ring(0.055, 0.22, 0.25), 0.30, 0.62),
     solid(ring(0.055, 0.75, 0.25), ring(0.055, 0.75, 0.25), 0.30, 0.62)),
)

#: A grown hull: ribbed chitin, no straight line anywhere.
#: Its own comment promised "no straight line anywhere" and every part of it
#: was a rectangular box. A carapace swells and closes: each course is wider at
#: its waist than where it meets the next, which is what makes a run of them
#: read as segments of one body rather than as a stack of trays.
_CHITIN = _v(
    (solid(rect(0.00, 1.00, 0.33, 0.67), rect(0.00, 1.00, 0.28, 0.72),
           0.0, 0.58),
     solid(rect(0.04, 0.96, 0.27, 0.73), rect(0.14, 0.86, 0.33, 0.67),
           0.58, 0.86),
     solid(rect(0.18, 0.82, 0.34, 0.66), rect(0.30, 0.70, 0.42, 0.58),
           0.86, 1.00)),
    (solid(rect(0.00, 1.00, 0.29, 0.71), rect(0.00, 1.00, 0.25, 0.75),
           0.0, 0.70),
     solid(rect(0.10, 0.90, 0.30, 0.70), rect(0.26, 0.74, 0.40, 0.60),
           0.70, 1.00)),
)


#: How far a hull's bottom pulls in from its deck edge, as a fraction of a
#: square. Nearly nothing, and that is measured.
#:
#: A hull wants tumblehome, and a per-square side can very nearly give it one:
#: :func:`vtt.isocam.footprint` mitres the bottom at each vertex, so a square
#: whose outline turns still closes. What it cannot do is mitre ACROSS squares —
#: a vessel's deck is carved out of a grid, and where the outline steps, the
#: diagonal belongs to one square and the run beside it to the next. Each mitres
#: correctly on its own and their bottoms part company, so at 0.42 every step of
#: the bow opened a wedge of sky three feet wide at the keel. Rendered, a
#: skyship looked like three separate hull plates hung under one deck.
#:
#: This is the SKIRT_INSET lesson again one level down: flush faces meet, and
#: moved faces meet only if everything that moves them agrees. A tenth of a
#: square leaves a gap of about five inches, which is below what the camera
#: resolves, and still catches a different light down the side than a dead
#: vertical slab would. The keel's DEPTH is what distinguishes the vessels —
#: nine feet of freeboard on a caravel, fourteen of visible keel on an airship
#: nobody's water is hiding.
HULL_TAPER = 0.10


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------

#: The seabed, described as a SURFACE and nothing else.
#:
#: Written positively throughout. The first version said "seen from directly
#: above THROUGH clear turquoise water, no water surface in view, no plants" and
#: came back an aerial beach — surf line, dry sand and palm fronds — because
#: every one of those nouns is a request when it appears in a positive prompt.
#: What must be absent lives in ``_SEABED_NEG``.
_SEABED_ART = (
    "underwater sea floor filling the frame: pale sand rippled into parallel "
    "ridges, scattered shells and small pebbles, fine silt, dappled caustic "
    "light, a blue-green underwater haze over everything"
)

_SEABED_NEG = (
    "beach, shoreline, coast, surf, waves, waterline, foam, island, "
    "palm, palm frond, leaves, grass, reeds, lily pad, plants, aerial photo, "
    "water surface, reflection, sky"
)


SKINS: dict[str, Skin] = {s.name: s for s in (
    # --- rock, in its several honest forms -------------------------------
    Skin("cliff", "granite",
         "raw granite, close-up of the bare fractured rock face, cold "
         "blue-grey stone",
         words="the rock is a natural granite cliff face, fractured and "
               "weathered, not built masonry and not brickwork",
         variants=_CLIFF, height_ft=14, smooth=True),
    Skin("boulder", "granite",
         "raw granite, close-up of the bare fractured rock face, cold "
         "blue-grey stone",
         words="fallen boulders lie about the track, rounded and weathered, "
               "each one a separate stone",
         variants=_BOULDER, height_ft=8,
         standalone=True),
    # A ruined wall is a RUBBLE COURSE, not a panel. The first version was a
    # long thin slab with a coping on top, drawn along the run — which is a
    # fence, and is what came back painted: a garden fence around a ruin. What
    # makes masonry read as fallen masonry is that its top is BROKEN, so these
    # are three or four separate blocks of different heights with gaps between
    # them, each one battered in plan so nothing is a right angle.
    Skin("ruin-stub", "dressed-stone",
         "close-up of dressed and coursed limestone ashlar, warm pale "
         "sandy grey with ochre weathering",
         words="what walls remain are broken courses of fallen masonry — "
               "stubs of different heights with gaps between them, rubble "
               "spilling from the breaks",
         variants=_v(
             (solid(((0.00, 0.30), (0.34, 0.26), (0.36, 0.74), (0.02, 0.78)),
                    ((0.02, 0.32), (0.32, 0.30), (0.33, 0.70), (0.04, 0.74)),
                    0.0, 1.00),
              solid(((0.40, 0.24), (0.72, 0.28), (0.70, 0.72), (0.38, 0.76)),
                    ((0.42, 0.28), (0.70, 0.32), (0.68, 0.68), (0.40, 0.72)),
                    0.0, 0.62),
              solid(((0.78, 0.30), (1.00, 0.26), (1.00, 0.70), (0.76, 0.74)),
                    ((0.80, 0.34), (1.00, 0.30), (1.00, 0.66), (0.78, 0.70)),
                    0.0, 0.84)),
             (solid(((0.00, 0.26), (0.26, 0.30), (0.24, 0.76), (0.02, 0.72)),
                    ((0.02, 0.30), (0.24, 0.34), (0.22, 0.72), (0.04, 0.68)),
                    0.0, 0.54),
              solid(((0.32, 0.28), (0.66, 0.24), (0.68, 0.72), (0.34, 0.78)),
                    ((0.34, 0.32), (0.64, 0.28), (0.66, 0.68), (0.36, 0.74)),
                    0.0, 0.96),
              # The rubble that fell out of the gap, lying at the foot.
              solid(((0.70, 0.34), (0.94, 0.30), (0.96, 0.66), (0.72, 0.70)),
                    ((0.74, 0.40), (0.90, 0.38), (0.92, 0.62), (0.76, 0.64)),
                    0.0, 0.26)),
         ), directional=True),
    Skin("broken-column", "dressed-stone",
         "close-up of dressed and coursed limestone ashlar, warm pale "
         "sandy grey with ochre weathering",
         words="the columns are snapped off at different heights, weathered "
               "drums of pale stone with fallen sections lying beside them",
         variants=_SNAPPED_COLUMN,
         standalone=True),
    # NB the swatch prompt is nearly all STONE. The first one said "grass and
    # weeds forcing up through the joints" and the swatch came back mostly
    # green, so the terrain image was green, so the board was a lawn — the
    # material image is a sample of a SURFACE, and whatever it averages to is
    # the colour the painter starts from.
    Skin("ruin-floor", "ruined-paving",
         "close-up of cracked and heaved limestone paving, warm pale sandy "
         "grey, dry and dusty, a few thin weeds in the cracks",
         words="the ground is the RUINED FLOOR of the place — cracked "
               "flagstones and fallen masonry heaved apart, weeds in the "
               "joints; it is not a lawn and not open grass"),
    Skin("field-stone", "granite",
         "raw granite, close-up of the bare fractured rock face, cold "
         "blue-grey stone",
         words="the low rocks are lichened granite boulders, waist high",
         misread="crates, boxes, chests, barrels",
         variants=_FIELD_STONE,
         standalone=True),
    Skin("scree", "scree",
         "close-up of loose shale and broken slate scree",
         words="the ground is loose shale and scree", soft=True),
    Skin("cave-rock", "limestone",
         "a flat expanse of damp grey limestone rock, mineral streaks and "
         "flowstone, filling the whole frame",
         words="the walls are living cave rock, damp limestone",
         variants=_CLIFF, height_ft=13, smooth=True),

    # --- the sea ----------------------------------------------------------
    Skin("coral", "coral",
         "close-up of living reef coral, brain coral and branching polyps",
         words="the reef heads are living coral in ochre and violet, "
               "encrusted and irregular",
         variants=_CORAL, height_ft=9, smooth=True),
    # UNDER the water, not on it. The shared `~` swatch is a picture of a pond
    # SURFACE — ripples, bubbles, water-lily leaves round the edge — which is
    # right for a stream through a meadow and catastrophic on a reef, where
    # three quarters of the board wears it and the fight is happening twenty
    # feet down. The board came back as a green pond with reeds in it, and the
    # painter was doing as it was told.
    #
    # A swim board's water squares are not a surface at all: they are the
    # SEABED, seen from above through clear water. Say that in the swatch, and
    # say it again in the words — the board's own description already claims a
    # coral shelf and was losing the argument to the picture.
    Skin("seabed-shallow", "sunlit-shallows", _SEABED_ART,
         words="everything here is UNDERWATER on a sunlit sand shelf — the "
               "sand is seen through clear turquoise water, dappled with "
               "caustics; this is the sea floor, not a pond seen from the bank",
         negative=_SEABED_NEG, soft=True),
    Skin("seabed-deep", "deep-channel",
         "underwater: a deep blue-green trench cut into a pale sand floor, the "
         "bottom lost in darkness, fine silt, dappled light fading out with "
         "depth",
         words="the deeper channels drop away into blue-green darkness",
         negative=_SEABED_NEG, soft=True),
    Skin("seabed-sand", "sunlit-shallows", _SEABED_ART,
         words="the flats are pale rippled sand on the sea bed",
         negative=_SEABED_NEG, soft=True),
    Skin("drowned-column", "drowned-stone",
         "a flat expanse of pale ancient cut stone crusted with barnacles and "
         "green weed, filling the whole frame",
         words="the columns are ancient, SNAPPED OFF and toppled, furred with "
               "weed and barnacle — a drowned ruin, nothing intact",
         variants=_BROKEN_COLUMN, height_ft=9,
         standalone=True),
    # No height override: a low wall screens three feet and that is a number a
    # player reads off the board. The SHAPE says it is a ruin — an eroded,
    # broken-topped stub instead of a tidy waist-high course — and the height
    # stays exactly what the rules quote.
    Skin("drowned-wall", "drowned-stone",
         "a flat expanse of pale ancient cut stone crusted with barnacles and "
         "green weed, filling the whole frame",
         words="what walls remain are collapsed stubs of drowned masonry",
         variants=_v(
             ((0.00, 1.00, 0.18, 0.78, 0.0, 0.72),
              (0.06, 0.62, 0.22, 0.70, 0.72, 1.00)),
             ((0.00, 1.00, 0.22, 0.80, 0.0, 0.58),
              (0.34, 0.94, 0.26, 0.74, 0.58, 1.00)),
         ), directional=True),

    # --- the underneath ---------------------------------------------------
    Skin("sewer-brick", "sewer-brick",
         "close-up of slime-blackened brickwork, wet mortar, green-black "
         "staining and mineral weep",
         words="the brickwork is filthy — black slime to the tide line, weeping "
               "mortar, rust stains and fungal bloom"),
    Skin("sludge", "sludge",
         "thick stagnant green-brown sewage seen straight down, greasy scum "
         "film and floating litter, no bank and no edge anywhere in frame",
         words="the channel runs with foul green-brown effluent, scummed and "
               "littered"),
    Skin("sewer-ledge", "wet-flagstone",
         "close-up of wet slimed flagstone, standing water in the joints",
         words="the ledges are wet slimed flagstone"),

    # --- built things -----------------------------------------------------
    Skin("masonry", "dressed-stone",
         "close-up of dressed and coursed limestone ashlar, warm pale "
         "sandy grey with ochre weathering"),
    # A town street. Both halves are needed and neither is decoration: the
    # walls said "fence" and the road said "planking", on a board that gets no
    # terrain image at all (it is built up, so the depth map is the only
    # conditioning) — which is precisely when the material CLAUSE is the whole
    # of what the model has to go on.
    # The roof of a townhouse, and nothing else: no square ever wears it, so it
    # has no silhouette and no variants — it is a MATERIAL, reached through
    # `roof_skin`. Filed as a skin so it joins the swatch catalogue, the
    # staging seam and the vocabulary audit by the same door as every other
    # material rather than by a special case.
    Skin("roof-tile", "clay-tile",
         "close-up of a roof of overlapping curved clay pantiles, warm "
         "terracotta and weathered russet, moss in the laps",
         words="the roofs are steep and tiled, russet clay darkened by weather"),
    Skin("townhouse", "plaster-timber",
         "close-up of a timber-framed wall, white lime plaster between dark "
         "oak beams",
         words="the street is walled by the fronts of close-packed two-storey "
               "townhouses — lime plaster and dark timber framing, shuttered "
               "windows, doors onto the street, steep tiled roofs above",
         # A BUILDING: its roof is traced over the whole block rather than
         # drawn on each square. The eaves sit where the per-square gable used
         # to spring from (0.70 of the drawn height) and the ridge reaches the
         # same twenty-four feet the block always stood — so the proportions
         # are the ones that were tuned, and only the SIZE OF THE UNIT changed.
         variants=_TOWNHOUSE, height_ft=24, smooth=True,
         roof_ft=7.2, roof_at=0.70, roof_skin="roof-tile"),
    # What is left standing in a ruin. Its own skin rather than `townhouse`
    # because the roof tracer keys on one — a ruin's remaining roof is lower,
    # and a burnt-out shell should not be drawn under a townhouse's steep tile.
    Skin("ruin-house", "ruined-masonry",
         "close-up of fire-blackened rubble masonry, mortar washed out of the "
         "joints, weeds rooted in the courses",
         words="a few houses still stand — soot-stained walls, roofs half "
               "fallen in, doorways open to the weather",
         variants=_TOWNHOUSE, height_ft=18, smooth=True,
         roof_ft=4.5, roof_at=0.70),
    Skin("cobbles", "cobble",
         "close-up of a worn cobbled street surface, rounded granite setts and "
         "the gaps between them",
         words="the roadway is worn granite cobbles, rutted and greasy with use",
         # A road is LAID and it is not FLAT: it is laid over ground, and it
         # follows the ground. The "laid things are flat" rule is about a
         # floor — a dungeon's flagstones, a ship's deck — where the builder
         # levelled the site. Nobody levels a hillside to put a street on it.
         soft=True,
         misread=""),
    # A TAPROOM, and the three things a room the model already knows still gets
    # wrong. The floor is the reason this set exists: with the ground-only init
    # (see art._ground_init) a built board's floor finally has a channel, and a
    # channel carries whatever the grid declares — so a tavern declaring nothing
    # came back paved in dungeon flagstone, which is worse than the planking it
    # replaced, because a taproom floor really is boards.
    Skin("taproom-floor", "taproom-boards",
         "close-up of a worn oak plank floor, wide dark boards running one way, "
         "warm mid brown, scuffed and stained, black gaps between the boards",
         words="the floor is wide oak boards, dark with age and spilled ale",
         misread=""),
    # A HOUSE FLOOR is not a street, and a laid floor does not RIPPLE.
    # A town house's inside was skinned by the archetype default for `.`, which
    # on a street is `cobbles` — deliberately `soft`, because a road follows
    # the ground it is laid over. A floor does not: somebody levelled the plot
    # and laid boards on it, which is the "laid things are flat" rule the
    # dungeon's flagstones and a ship's deck already keep. Sharing the taproom's
    # SUBSTANCE, so it costs no second swatch (see `substances`), with words of
    # its own — a dwelling's boards are not black with spilled ale.
    Skin("house-floor", "taproom-boards",
         "close-up of a worn oak plank floor, wide dark boards running one way, "
         "warm mid brown, scuffed and stained, black gaps between the boards",
         words="the floors indoors are plain scrubbed boards"),
    Skin("taproom-wall", "plaster-timber",
         "close-up of a timber-framed wall, white lime plaster between dark "
         "oak beams",
         words="the walls are lime plaster between dark oak studs, low and "
               "smoke-stained, a shelf and a hook here and there"),
    # The BAR, and it is a skin rather than just a tile because a counter is
    # not a table: it is one long run you serve across and hide behind, and
    # both the painter and the segmentation map have a word for it that they do
    # not have for furniture in general.
    Skin("taproom-bar", "taproom-boards",
         "close-up of a worn oak plank floor, wide dark boards running one way, "
         "warm mid brown, scuffed and stained, black gaps between the boards",
         words="a long timber bar counter runs down one side, its top worn "
               "smooth, casks and shelves behind it"),
    Skin("taproom-post", "spar-timber",
         "a flat expanse of oiled timber, straight close grain all running one "
         "way, no corners and no edges",
         words="square oak posts carry the ceiling beams, chamfered and dark "
               "with smoke, a brace out to the beam at the head of each",
         misread="candles, candlestick, candelabra, lit candle, wax candle",
         variants=_POST, exact=True,
         standalone=True),
    # What a watchtower is BUILT of, and the DM's narration decides which — a
    # crossing in deep forest gets a timber tower and a mountain road a stone
    # one. See building_material: the board reads it off the biome the DM
    # already typed, so nothing new has to be asked for.
    Skin("tower-stone", "dressed-stone",
         "close-up of dressed and coursed limestone ashlar, warm pale "
         "sandy grey with ochre weathering",
         words="the watchtowers are squat drystone towers, merloned at the top",
         variants=_TOWER, height_ft=16),
    # A timber tower is NOT a stockade. See _TOWER_POST: it is four poles and
    # the daylight between them, so its three pieces are a leg, the platform
    # they carry, and the ladder up. They are all `exact` because they have to
    # MEET — a jittered post is a platform standing clear of its own legs.
    Skin("tower-post", "log-palisade",
         "close-up of a wall of upright split logs, bark and axe marks",
         words="the watchtower is a timber frame — four raked pine legs, "
               "cross-braced, holding a platform up over open ground",
         variants=_TOWER_POST, height_ft=17, exact=True),
    Skin("tower-top", "log-palisade",
         "close-up of a wall of upright split logs, bark and axe marks",
         words="the platform is planked and railed, under a shingled hip roof "
               "on four corner posts with a deep overhanging eave",
         variants=_TOWER_TOP, height_ft=30, exact=True),
    Skin("tower-ladder", "log-palisade",
         "close-up of a wall of upright split logs, bark and axe marks",
         words="a ladder is lashed to the frame, running up to a hatch in the "
               "platform",
         variants=_LADDER, height_ft=16, exact=True),
    Skin("tent-canopy", "canvas",
         "a flat expanse of heavy woven CLOTH — coarse canvas sailcloth, "
         "off-white and grey, individual threads visible, stained and patched, "
         "soft fabric with no wood and no planks anywhere",
         words="the tents are ROOFED — canvas over the whole span, ridged and "
               "sagging between the poles",
         variants=_TENT_CANOPY, height_ft=8, directional=True,
         body="tent", exact=True),
    Skin("flap", "canvas",
         "a flat expanse of heavy woven CLOTH — coarse canvas sailcloth, "
         "off-white and grey, individual threads visible, stained and patched, "
         "soft fabric with no wood and no planks anywhere",
         words="the tent flaps are tied back at the poles",
         variants=_FLAP, height_ft=8, outward=True, body="tent",
         exact=True),
    Skin("doorway-stone", "dressed-stone",
         "close-up of dressed and coursed limestone ashlar, warm pale "
         "sandy grey with ochre weathering",
         words="a low arched doorway is cut through at ground level",
         variants=_DOORWAY, height_ft=16, directional=True),
    Skin("doorway-timber", "log-palisade",
         "close-up of a wall of upright split logs, bark and axe marks",
         words="a plank-framed doorway is cut through at ground level",
         variants=_DOORWAY, height_ft=14, directional=True),
    Skin("parapet", "dressed-stone",
         "close-up of dressed and coursed limestone ashlar, warm pale "
         "sandy grey with ochre weathering",
         words="the tower tops are merloned stone parapets",
         variants=_PARAPET, height_ft=9, directional=True),
    Skin("palisade", "log-palisade",
         "close-up of a wall of upright split logs, bark and axe marks",
         words="the camp wall is a palisade of sharpened upright logs",
         variants=_PALISADE, height_ft=10, directional=True),
    Skin("canvas", "canvas",
         "a flat expanse of heavy woven CLOTH — coarse canvas sailcloth, "
         "off-white and grey, individual threads visible, stained and patched, "
         "soft fabric with no wood and no planks anywhere",
         words="the tents are heavy stained canvas over timber poles, guy ropes "
               "pegged out, flaps tied back",
         variants=_TENT_WALL, height_ft=8, outward=True, body="tent",
         exact=True),

    # --- ships ------------------------------------------------------------
    #
    # NB the tumblehome is nearly nothing, and that is measured rather than
    # timid. See HULL_TAPER.
    #
    # Every skin a vessel wears shares one BODY, and that is load-bearing: a
    # side is drawn wherever a hull skin meets something that is NOT the same
    # body. Without the group the deck grew a hull side against its own mast —
    # a shaft sunk through the middle of the ship — and the rail ring, being a
    # different skin again, had no side at all, so the outermost strake of every
    # vessel was a sheet of paper.
    Skin("hull", "tarred-planking",
         "close-up of tarred ship planking, dark pitch-black boards running one "
         "way with caulked seams and iron nail heads",
         words="the hull is tarred carvel planking with caulked seams",
         body="ship", exact=True),
    # The sea a ship SAILS ON, and it is not the sea a reef sits under. `W` is
    # look-agnostic — one swatch of dark green deep water everywhere — which is
    # right seen from below and paints as a flat green mat when a caravel is
    # standing on it. The reef learned this in reverse: its `~` was a picture of
    # a pond SURFACE, catastrophic on a board fought inside the water.
    Skin("sea-surface", "open-sea",
         "aerial photograph of open ocean, deep blue-green swell with white "
         "foam streaks along the crests, no shoreline and no horizon",
         words="the ship is under way on open sea — long swell running past the "
               "hull, foam breaking along the crests and a wake astern",
         negative="beach, shore, sand, horizon, sky, boat, land"),
    Skin("sea-deck", "deck-planking",
         "close-up of holystoned ship deck planking, pale scrubbed oak, pitched "
         "seams",
         words="a working sea deck — planking dark with spray, salt-bleached "
               "in patches, the hull tarred black where it meets the water, "
               "the sea closing round it",
         skirt_ft=9, skirt_inset=HULL_TAPER, body="ship", exact=True),
    # An airship is not a boat that happens to be up. You see its UNDERSIDE —
    # the whole keel, which a sea hull hides below the waterline — so it gets a
    # side twice as deep and drawn in twice as hard, and a hull plan fine at
    # both ends rather than a caravel's flat transom (see mapgen._hull).
    # Sharing the caravel's deck was most of why the two came back identical.
    Skin("sky-deck", "deck-planking",
         "close-up of holystoned ship deck planking, pale scrubbed oak, pitched "
         "seams",
         words="an airship's weather deck, dry and sun-bleached, hanging in "
               "open sky — the keel and its lift-fins visible beneath, nothing "
               "under her but cloud",
         skirt_ft=14, skirt_inset=HULL_TAPER, body="ship", exact=True),
    # A deckhouse with no lid is the tent's problem again: a wall ring round a
    # walkable floor is a roofless box seen from above. Its own skin, because
    # the inside is its own squares, and it starts well clear of the floor so
    # occludes_floor passes it.
    Skin("cabin-roof", "tarred-planking",
         "close-up of tarred ship planking, dark pitch-black boards running one "
         "way with caulked seams and iron nail heads",
         words="the deckhouse is roofed in tarred planking, a low coaming "
               "round its edge",
         variants=_v((
             (0.00, 1.00, 0.00, 1.00, 0.86, 0.96),
             (0.00, 1.00, 0.42, 0.58, 0.96, 1.02),
         )), height_ft=10, directional=True, body="ship", exact=True),
    Skin("mast", "spar-timber",
         "a flat expanse of oiled timber, straight close grain all running one "
         "way, no corners and no edges",
         words="a single mast steps amidships, yard crossed and rigging set up "
               "to the rails",
         variants=_MAST, height_ft=26, body="ship", exact=True,
         standalone=True),
    Skin("railing", "spar-timber",
         "a flat expanse of oiled timber, straight close grain all running one "
         "way, no corners and no edges",
         words="the deck is edged with a stanchion rail you can see the water "
               "through",
         variants=_RAILING, directional=True,
         skirt_ft=9, skirt_inset=HULL_TAPER, body="ship"),
    Skin("sky-rail", "spar-timber",
         "a flat expanse of oiled timber, straight close grain all running one "
         "way, no corners and no edges",
         words="the deck is edged with a stanchion rail you can see the sky "
               "through, safety lines rove between the posts",
         variants=_RAILING, directional=True,
         skirt_ft=14, skirt_inset=HULL_TAPER, body="ship"),
    Skin("plating", "riveted-brass",
         "close-up of riveted brass and iron plating, verdigris and oil stains",
         words="the vessel is a riveted brass-and-iron contraption — rivets, "
               "pipework, pressure gauges, vented steam",
         variants=_PLATING, height_ft=8, directional=True, body="ship"),
    Skin("chitin", "chitin",
         "the glossy black-green back of a giant beetle filling the whole "
         "frame, hard armour plating with fine parallel grooves, oily "
         "iridescent sheen",
         words="the vessel is GROWN rather than built — ridged chitin, veined "
               "and iridescent, no straight lines anywhere",
         variants=_CHITIN, height_ft=8, directional=True, body="ship"),
    # A vessel's DECK, which is most of what you see of it. The first pass gave
    # all three styles the same scrubbed oak and changed only the trim, and the
    # three came back indistinguishable — correctly, because they WERE the same
    # ship with different railings. What a hull is made of has to reach the
    # floor or it reaches nothing.
    Skin("plated-deck", "riveted-brass",
         # WARM metal, and NOT A WORD ABOUT VERDIGRIS. Twice measured: the
         # original asked for "verdigris and oil stains" and averaged to
         # (56,109,104), a teal green, so a whole deck of it painted as GRASS;
         # rewritten to keep "a few streaks of green verdigris in the seams" it
         # came back (100,111,76), olive. A swatch prompt is a POSITIVE prompt —
         # naming the green asks for the green, however small the helping you
         # request — which is the same trap `Skin.negative` exists for and the
         # same one the seabed fell into asking for "no plants".
         "a flat expanse of riveted brass and iron deck plating, warm golden "
         "brass polished by boots and darkened by oil, rows of iron rivets, "
         "filling the whole frame",
         negative="verdigris, green patina, oxidised copper, moss, grass",
         words="the deck is riveted metal plate, oil-stained, with grilles and "
               "pipe runs let into it, boilers and vented steam below the keel",
         skirt_ft=13, skirt_inset=HULL_TAPER, body="ship", exact=True),
    Skin("chitin-deck", "chitin",
         "the glossy black-green back of a giant beetle filling the whole "
         "frame, hard armour plating with fine parallel grooves, oily "
         "iridescent sheen",
         words="the deck is the creature's own back — ridged chitin underfoot, "
               "warm and faintly translucent, the belly of the thing curving "
               "away beneath",
         skirt_ft=13, skirt_inset=HULL_TAPER, body="ship", exact=True),
    # The rail versions. Same substance, same silhouette as any other rail, and
    # emphatically the same THREE FEET — a ship's rail is half cover, and what
    # it is made of has no vote on how tall it is.
    Skin("plating-rail", "riveted-brass",
         "close-up of riveted brass and iron plating, verdigris and oil stains",
         words="the deck is edged with a pipework rail you can see the air "
               "through",
         variants=_RAILING, directional=True,
         skirt_ft=13, skirt_inset=HULL_TAPER, body="ship"),
    Skin("chitin-rail", "chitin",
         "the glossy black-green back of a giant beetle filling the whole "
         "frame, hard armour plating with fine parallel grooves, oily "
         "iridescent sheen",
         words="the deck is edged with a grown chitin lip, ribbed and low",
         variants=_RAILING, directional=True,
         skirt_ft=13, skirt_inset=HULL_TAPER, body="ship"),
)}


# --------------------------------------------------------------------------
# Who wears what
# --------------------------------------------------------------------------

#: archetype -> {tile code: skin name}. Derived, never stored: the archetype is
#: on the map row, so this is a lookup rather than three hundred rows of the
#: same fact. A code with no entry keeps its default look, which is what every
#: board had before skins existed.
ARCH_SKINS: dict[str, dict[str, str]] = {
    # NB `O` is a BOULDER and not a piece of cliff. Both are granite and they
    # want opposite silhouettes: a cliff fills its square so its neighbours
    # merge into one face, a boulder stands alone and needs an outline. Sharing
    # one skin drew every fallen stone as a full-square fourteen-foot block and
    # made the whole pass a field of dice.
    "mountain-pass": {"R": "cliff", "#": "cliff", "O": "boulder",
                      ",": "scree", ".": "scree"},
    "terraces":      {"R": "cliff", "#": "cliff", "O": "boulder",
                      ",": "scree", ".": "scree", "o": "field-stone"},
    "cave":          {"R": "cave-rock", "#": "cave-rock", "O": "boulder"},
    "mine":          {"R": "cave-rock", "#": "cave-rock", "O": "boulder"},
    "reef":          {"R": "coral", "O": "drowned-column", "w": "drowned-wall",
                      "~": "seabed-shallow", "W": "seabed-deep",
                      "s": "seabed-sand"},
    "open-water":    {"R": "coral", "W": "seabed-deep", "~": "seabed-shallow",
                      "s": "seabed-sand"},
    "sewer":         {"#": "sewer-brick", "~": "sludge", ".": "sewer-ledge",
                      "W": "sludge", ",": "sewer-ledge"},
    # Outdoors, ``o`` is a boulder. Its RULES are unchanged — half cover, four
    # feet, breakable — and a camp keeps its actual crates, because a camp is
    # where supplies are.
    "open":          {"o": "field-stone"},
    "clearing":      {"o": "field-stone"},
    "forest":        {"o": "field-stone"},
    "swamp":         {"o": "field-stone"},
    "bridge":        {"o": "field-stone"},
    "ruins":         {"O": "broken-column", "o": "field-stone",
                      ",": "ruin-floor", ".": "ruin-floor", "w": "ruin-stub"},
    "camp":          {"#": "palisade"},
    "street":        {"#": "townhouse", "=": "cobbles", ".": "cobbles"},
    "tavern":        {"#": "taproom-wall", ".": "taproom-floor",
                      "O": "taproom-post"},
    # A sea ship and a skyship are not the same vessel, and sharing one deck
    # skin was most of why they came back looking identical. A caravel's deck
    # is wet, tarred and salt-bleached and its hull sits IN the water; a
    # skyship's is dry and hangs in air, and you see its underside.
    "ship":          {"b": "sea-deck", "w": "railing", "O": "mast",
                      "#": "hull", "W": "sea-surface", "~": "sea-surface"},
    "skyship":       {"b": "sky-deck", "w": "sky-rail", "O": "mast",
                      "#": "hull"},
    "sky-islands":   {"R": "cliff"},
    # NB: no entry for dungeon-room, crypt, dungeon-complex or arena. They had
    # one — `masonry`, dressed grey building stone — and it bought nothing: it
    # is precisely what the model paints for a dungeon wall unprompted. A skin
    # earns its place by saying something the model would NOT guess, and a
    # decorative one is not free, because a board carrying skins is a board the
    # renderer has to reason about differently. `masonry` survives for the
    # watchtower, where it is doing real work against a camp full of canvas.
}

#: A skyship is the one board with a genuine style CHOICE rather than a
#: material: the same deck can be a brass-and-steam contraption or a grown
#: organic hull, and both are right. The generator picks one and records it, so
#: a table's ship stays the ship it was.
SKYSHIP_STYLES: dict[str, dict[str, str]] = {
    "timber":    {},
    "steampunk": {"#": "plating", "w": "plating-rail", "b": "plated-deck",
                  "O": "mast"},
    "organic":   {"#": "chitin", "w": "chitin-rail", "b": "chitin-deck",
                  "O": "mast"},
}


def _check_heights() -> None:
    """A skin may reshape a quoted height. It may never restate one.

    ``cover_height_ft`` is the number a player reads off the board when they
    decide whether they can break line of sight, and against something stronger
    than them that decision is most of the fight. Drawing a crate at five feet
    because it looked better invents a difference the engine will not honour, at
    exactly the moment being misled is expensive.
    """
    from .terrain import cover_height_ft

    bad: dict[str, str] = {}
    for arch, mapping in list(ARCH_SKINS.items()) + \
            [("skyship:" + k, v) for k, v in SKYSHIP_STYLES.items()]:
        for code, name in mapping.items():
            sk = SKINS.get(name)
            if sk is None:
                bad[f"{arch}.{code}"] = f"unknown skin {name!r}"
            elif sk.height_ft and cover_height_ft(code):
                bad[f"{arch}.{code}"] = (
                    f"skin {name!r} redraws {code!r} at {sk.height_ft} ft, but "
                    f"the rules quote {cover_height_ft(code)} ft")
    if bad:
        raise ValueError(f"illegal skin assignment: {bad}")


#: What a code is made of when the archetype says nothing.
#:
#: Only ``R``, and it is not a style choice — it is a correction. A rock face is
#: in ``STRUCTURE_CODES``, so an unskinned one is drawn by the WALL-FACE model:
#: a thin slab hugging the open side, which is right for masonry and, at the one
#: or two percent of squares an ordinary outdoor board scatters rock across,
#: comes out as a pale box standing on the grass. The painter reads a box on
#: open ground as a CRATE, and a meadow strewn with crates is what every open,
#: forest, camp and bridge board came back as. The mountain pass and the cave
#: were never affected — they had said "cliff" and "cave-rock" for other reasons
#: — which is precisely why it survived so long.
#:
#: The default is ``boulder`` rather than ``cliff`` because scattered rock is
#: what these boards have: an OBJECT standing alone, which wants a silhouette,
#: where a cliff is a MASS that wants to merge with its neighbours. That
#: distinction is already written down in the catalogue and it is the same one
#: the pass learned the hard way.
DEFAULT_SKINS: dict[str, str] = {"R": "boulder"}


def skins_for(archetype: str, *, style: str = "") -> dict[str, str]:
    """The default skin for each tile code on a board of this archetype."""
    arch = (archetype or "").strip().lower()
    out = dict(DEFAULT_SKINS)
    out.update(ARCH_SKINS.get(arch, {}))
    if arch == "skyship" and style in SKYSHIP_STYLES:
        out.update(SKYSHIP_STYLES[style])
    return out


def skin_at(code: str, x: int, y: int, *, codes: Optional[dict] = None,
            squares: Optional[dict] = None) -> str:
    """This square's skin name, or "" for the code's own default look.

    A per-square override beats the archetype default, which is the whole point
    of having both: a camp is palisaded, and the tents inside it are canvas.
    """
    if squares:
        got = squares.get(f"{x},{y}")
        if got:
            return str(got)
    if codes:
        return str(codes.get(code) or "")
    return ""


def skin(name: str) -> Optional[Skin]:
    return SKINS.get(name or "")


def variants_of(name: str) -> Optional[Variants]:
    sk = SKINS.get(name or "")
    return sk.variants if sk else None


def height_of(name: str) -> float:
    sk = SKINS.get(name or "")
    return sk.height_ft if sk else 0.0


def is_directional(name: str) -> bool:
    sk = SKINS.get(name or "")
    return bool(sk and sk.directional)


def is_outward(name: str) -> bool:
    sk = SKINS.get(name or "")
    return bool(sk and sk.outward)


def is_smooth(name: str) -> bool:
    sk = SKINS.get(name or "")
    return bool(sk and sk.smooth)


def is_exact(name: str) -> bool:
    """Draw at exactly the stated height, with no per-instance jitter."""
    sk = SKINS.get(name or "")
    return bool(sk and sk.exact)


def body_of(name: str) -> str:
    sk = SKINS.get(name or "")
    return sk.body if sk else ""


def same_body(a: str, b: str) -> bool:
    """Are these two squares part of one THING?

    A ship is a deck, a rail round it, a mast through it and a cabin on it —
    four skins and one hull, so no side is drawn between any of them. The test
    is by group and not by skin, which is the bug this replaced: a deck square
    beside the mast counted as meeting something else and grew a hull face, so
    every vessel had a shaft sunk round its own mast.
    """
    if a == b:
        return True
    ba = body_of(a)
    return bool(ba) and ba == body_of(b)


def skirt_of(name: str) -> tuple[float, float]:
    """``(depth in feet, bottom inset)`` for a floor that carries its own side.

    ``(0, 0)`` means this square has no side of its own and falls back to the
    board-wide rule, which only draws one against a HOLE.
    """
    sk = SKINS.get(name or "")
    if sk is None or not sk.skirt_ft:
        return (0.0, 0.0)
    return (sk.skirt_ft, sk.skirt_inset)


#: Marks a square that a :mod:`vtt.setpieces` landmark stamped, as written into
#: the sparse per-square skin map by ``setpieces.place``.
SETPIECE_PREFIX = "setpiece:"


def is_setpiece(name: str) -> bool:
    """Is this square drawn by a landmark's MESH rather than by its own shape?

    A set piece is the one skin that hands over no silhouette of its own, and
    it is the reason this is a prefix rather than an entry in :data:`SKINS`: a
    mesh is a shape per LANDMARK, not a shape per square, so there is nothing
    for the per-square vocabulary to hold. Every other skin lookup returns the
    code's own default for a name it does not know, which is exactly wrong
    here — a set-piece square would then draw its ordinary block AND the mesh,
    a statue standing inside a pillar.

    Only STANDING geometry is suppressed. The floor is still drawn, because a
    landmark's walkable squares are real ground a creature stands on and their
    elevation is a rules answer.
    """
    return (name or "").startswith(SETPIECE_PREFIX)


def setpiece_slug(name: str) -> str:
    """The landmark a square belongs to, or "" if it belongs to none."""
    return (name or "")[len(SETPIECE_PREFIX):] if is_setpiece(name) else ""


def occludes_floor(name: str) -> bool:
    """Would this skin's shape stand in the way on a square you can walk on?

    The guard for a bug that already happened: a watchtower's whole wall ring
    was skinned in one pass, including its OPEN DOORWAY, so a square the rules
    let you walk through was drawn as a solid nine-foot merloned block. A
    picture contradicting the grid is the one thing the board must never do,
    and it is worse in this direction than in any other — the way in simply is
    not there.

    Judged by footprint at standing height: anything reaching the ground and
    covering most of the square is in the way. A lintel over a passage starts
    high and is fine; a canopy is fine; a wall is not.
    """
    sk = SKINS.get(name or "")
    if sk is None or not sk.variants:
        return False
    for parts in sk.variants:
        covered = 0.0
        for part in parts:
            if is_solid(part):
                bottom, _top, y0, _y1 = part
                if y0 <= 0.01:
                    covered += _poly_area(bottom)
            else:
                x0, x1, z0, z1, y0, _y1 = part
                if y0 <= 0.01:
                    covered += (x1 - x0) * (z1 - z0)
        if covered > 0.40:
            return True
    return False


#: Words in a DM's own biome text -> what things are BUILT of there.
#:
#: A watchtower on a forest crossing should be a timber stockade and one on a
#: mountain road drystone, and the DM has already said which without being
#: asked: the biome string they typed is sitting on the board row. Same
#: mechanism as `board_look` mapping free text onto a closed set, and the same
#: reason — the alternative is one material everywhere.
_BUILT_OF: tuple[tuple[tuple[str, ...], str], ...] = (
    (("forest", "wood", "jungle", "grove", "thicket", "timber", "pine",
      "frontier", "wild"), "timber"),
    (("stone", "mountain", "granite", "keep", "castle", "city", "town",
      "quarry", "cliff", "snow", "alpine"), "stone"),
)


def building_material(biome: str = "", default: str = "stone") -> str:
    """"stone" or "timber" — what a structure on this board is made of."""
    b = (biome or "").strip().lower()
    for words, mat in _BUILT_OF:
        if any(w in b for w in words):
            return mat
    return default


def substances() -> dict[str, str]:
    """Every substance the catalogue needs, ``{slug: subject}``.

    What ``scripts/material_prerender.py`` iterates. Substances are shared —
    a reef's coral heads and its coral rubble are one swatch — so this is far
    shorter than the skin list.
    """
    out: dict[str, str] = {}
    for sk in SKINS.values():
        out.setdefault(sk.substance, sk.art)
    return out


def misread_for(names: Sequence[str]) -> str:
    """What the painter must NOT read these materials as.

    Only for skins actually PRESENT — the whole point of hanging it on the skin
    rather than on the board is that a negative is global to the picture, so a
    taproom may forbid flagstones without a crypt losing its floor.
    """
    seen: list[str] = []
    for n in names:
        sk = SKINS.get(n or "")
        if sk and sk.misread and sk.misread not in seen:
            seen.append(sk.misread)
    return ", ".join(seen)


def words_for(names: Sequence[str]) -> str:
    """What the painter is told about this board's materials.

    Only for skins actually PRESENT, so an ordinary dungeon is not told about
    coral. Deduplicated by clause, because a reef wears three skins that share
    one sentence about drowned stone.
    """
    seen: list[str] = []
    for n in names:
        sk = SKINS.get(n or "")
        if sk and sk.words and sk.words not in seen:
            seen.append(sk.words)
    return ", ".join(seen)


_check_heights()
