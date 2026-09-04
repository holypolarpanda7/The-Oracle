"""The board's SHAPES, in Python, so one program can generate the other.

Every silhouette the board draws — how thick a wall is, how a tent's canvas
leans, which of four crowns a tree wears, where a hull's bottom mitres — is
data, and it is authored HERE and generated into
``activity-ui/src/lib/boardShapes.generated.ts`` by
``scripts/gen_board_shapes.py``. One source, one direction, no mirroring: the
browser reads a file it never edits.

This used to be `vtt/isocam.py`, and it used to be twice this size, because it
also held a CAMERA and a DEPTH RASTERIZER — a second implementation of the
whole board, in Python, so a ControlNet could be handed a picture of the same
room the browser was drawing. That was the price of the painted layer, and the
painted layer is gone: a painting is a photograph of the room from one place,
and the camera turns a full circle now. What went with it was an alignment
gate whose entire job was to catch the two implementations drifting apart, and
the drift it existed to catch is no longer possible.

What is left is the part that was never about the painter: what the shapes ARE.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional, Sequence

# The shape vocabulary itself — a part, and the prismatoid constructor that
# normalizes its winding. `skins` imports nothing from this package, so this is
# a plain import rather than the deferred ones further down.
from .skins import Part, rect, ring as _ring, slab as _slab, solid

#: Rotation about the vertical axis. 45° puts the board corner-on, so both wall
#: faces of a corner are visible and neither runs parallel to the screen edge.

#: A wall occupies its whole square in the RULES and always will — it is
#: impassable, it blocks sight, and none of that changes here. But drawn as a
#: full five-foot cube it presents an enormous top face, and a ring of those is
#: a rim, and a rim around a floor is a TRAY — which is what every enclosed
#: room read as. Thinned to a slab, most of what the eye gets is the wall's
#: vertical faces, which is what a wall actually looks like.
#:
#: The floor strip left either side is a drawing, not playable ground; the grid,
#: the movement wash and the outline all still say the square is solid.
WALL_THICKNESS = 0.34


def corner_lift_ft(rows, elevation, x: int, z: int,
                   cx: int, cz: int, skin_of=None) -> float:
    """The GROUND's height at a grid corner, in feet — averaged where it may be.

    Elevation is stored per square as whole feet, so a hillside is drawn as
    terraces: every square a flat plate at its own height with a step to its
    neighbour. Real ground does not do that, and the terracing is most of why
    an outdoor board reads as stacked blocks — the mountain pass came out a
    flight of stairs and a meadow with a knoll on it a wedding cake.

    A corner is shared by up to four squares and takes the mean of the ones
    that may JOIN this one: natural ground (a floor, a road, a bridge and a
    deck are LAID, and laid things are flat) within one STEP of it. A
    neighbour outside that is not counted, which is what keeps a ledge a
    cliff — the corner then reads this square's own height and the face
    between them stays vertical.

    Drawing only: a creature stands at its square's stated height and every
    rule reads the integer. Mirrored by ``cornerLiftFt`` in
    activity-ui/src/lib/boardView.ts, and compared by the alignment gate — a
    corner the two programs disagree about is a seam in the ground that the
    painting is then baked over.
    """
    from . import skins as _skins
    from .terrain import GROUND_RIPPLE_FT, SMOOTH_STEP_FT, SOFT_GROUND

    def _at(ax: int, az: int) -> float:
        return float((elevation or {}).get(f"{ax},{az}", 0) or 0)

    def _code(ax: int, az: int):
        if 0 <= az < len(rows) and 0 <= ax < len(rows[az]):
            return rows[az][ax]
        return None

    def _soft(ax: int, az: int) -> bool:
        """May this square's surface slope?

        The SKIN answers first, and has to: ``.`` is scree on a mountain pass
        and cobbles on a street, which is exactly the distinction a skin exists
        to make. A square wearing none falls back to the tile code.
        """
        c = _code(ax, az)
        if c is None:
            return False
        if skin_of is not None:
            sk = _skins.skin(skin_of(c, ax, az) or "")
            if sk is not None:
                return bool(getattr(sk, "soft", False))
        return c in SOFT_GROUND

    own = _at(x, z)
    # A CORNER'S height must be a property of the CORNER, not of whichever
    # square is asking — anything that reads the asker's own code or height
    # gives the two squares sharing an edge two different answers there, and
    # the ground tears along every seam. So: the squares meeting at this
    # corner, and nothing else.
    around = [(ax, az) for ax, az in ((cx - 1, cz - 1), (cx, cz - 1),
                                      (cx - 1, cz), (cx, cz))
              if _code(ax, az) is not None]
    if not around:
        return own
    if any(not _soft(ax, az) for ax, az in around):
        return own                          # something LAID meets here
    fts = [_at(ax, az) for ax, az in around]
    if max(fts) - min(fts) > SMOOTH_STEP_FT:
        return own                          # a LEDGE: the face stays vertical
    # …and a WANDER on top, hashed from the corner so both squares sharing it
    # get the same number and the ground cannot tear. See GROUND_RIPPLE_FT: it
    # is drawing and no rule reads it.
    wobble = ((_hash(cx, cz, 26699, 45989) % 2048) / 2048.0 - 0.5) * 2.0
    return sum(fts) / len(fts) + wobble * GROUND_RIPPLE_FT


def surface_lift_ft(rows, elevation, x: int, z: int,
                    u: float, v: float, skin_of=None) -> float:
    """The ground's height at a point INSIDE a square, bilinear over its
    corners. The floor is drawn as a fan over an outline that may have been
    chamfered, so every vertex has to land on the same surface or it tears."""
    a = corner_lift_ft(rows, elevation, x, z, x, z, skin_of)
    b = corner_lift_ft(rows, elevation, x, z, x + 1, z, skin_of)
    c = corner_lift_ft(rows, elevation, x, z, x, z + 1, skin_of)
    d = corner_lift_ft(rows, elevation, x, z, x + 1, z + 1, skin_of)
    return (a * (1 - u) + b * u) * (1 - v) + (c * (1 - u) + d * u) * v


def exposed(is_open, x: int, z: int) -> bool:
    """Is any of this solid square's EIGHT neighbours open floor?

    ``wall_parts`` asks the orthogonal question, because a wall's faces are
    orthogonal. A rock MASS needs the diagonal one too. A pass's track wanders,
    so the rock shell beside it steps diagonally as often as not — and a square
    whose only open neighbour is a diagonal was drawn as buried, which left a
    notch at every step and turned the face into a row of separate towers.
    """
    if wall_parts(is_open, x, z):
        return True
    return any(is_open(x + dx, z + dz)
               for dx, dz in ((-1, -1), (1, -1), (-1, 1), (1, 1)))


def wall_parts(is_open, x: int, z: int) -> tuple[tuple[float, float, float, float], ...]:
    """Footprint(s) for a wall square, as ``(x0, x1, z0, z1)`` offsets.

    Drawn as the FACE of the solid region rather than the square's own run.
    Both earlier attempts keyed on which way the wall runs, and both came out
    crenellated: `mapgen` walls are commonly two squares thick, so every square
    in the band reads as a corner and draws a plus, and a band of pluses has a
    notch at every seam.

    What you actually see of a thick wall is the skin where it meets open floor.
    So a wall square draws a slab hugging its open side, and — the part that
    pays for itself — NOTHING when it is buried, which is most of a thick band.

    **A THIN wall is a different thing and needs the other treatment.** Where a
    square has floor on more than one side it is not the skin of a mass, it is
    a wall with two faces: a cabin's bulkhead, a ruin's standing course, a
    stockade. Hugging each open side draws TWO slabs with a slot down the
    middle, which is exactly how a ship's deckhouse came back with double
    walls and a corridor between them. So a thin wall is drawn on its
    CENTRELINE instead — a hub in the middle and an arm toward each square the
    wall carries on into — which gives one wall, mitres its own corners, and
    stops cleanly at a stub end. It keeps less top face than a full cube, so
    the tray this rule exists to prevent stays prevented.
    """
    t = WALL_THICKNESS
    n, s = is_open(x, z - 1), is_open(x, z + 1)
    w, e = is_open(x - 1, z), is_open(x + 1, z)
    out: list[tuple[float, float, float, float]] = []
    if (n + s + w + e) >= 2:
        lo, hi = 0.5 - t / 2, 0.5 + t / 2
        out.append((lo, hi, lo, hi))                    # the hub
        if not n:
            out.append((lo, hi, 0.0, hi))               # the wall carries on
        if not s:
            out.append((lo, hi, lo, 1.0))
        if not w:
            out.append((0.0, hi, lo, hi))
        if not e:
            out.append((lo, 1.0, lo, hi))
        return tuple(out)
    if n:
        out.append((0.0, 1.0, 0.0, t))
    if s:
        out.append((0.0, 1.0, 1.0 - t, 1.0))
    if w:
        out.append((0.0, t, 0.0, 1.0))
    if e:
        out.append((1.0 - t, 1.0, 0.0, 1.0))
    return tuple(out)


#: Codes that are a HOLE, not ground: nothing is drawn on them at all.
#:
#: Open sky IS air, and a chasm is the absence of floor — drawing either as a
#: surface is the geometry inventing ground the rules say you fall through, and
#: it shows: a sky-islands board is 235 open-sky tiles, so drawn as floor it
#: came back a flat plane with a few blocks on it instead of stones floating in
#: nothing, and a bridge's 90 chasm squares paved over the gorge it crosses.
#:
#: Void is here for the older reason: on an upper storey it is open air you can
#: see and fall through, and a floor there would hide the hall below.
HOLE_CODES = frozenset({" ", "^", "x"})

#: A gap in a wall rather than a block filling a square. Mirrors
#: ``terrain.APERTURES``, kept here so this module stays importable on its own,
#: and read by the orientation rules: a doorway belongs to the wall it
#: interrupts, so a structure does not mistake its own way in for the outdoors.
_APERTURES = frozenset({"+", "/", "p"})

#: How thick a platform is, in feet, where its floor meets a hole.
#:
#: A floor quad has no thickness, so an island came out a paper cut-out hanging
#: in nothing and a bridge a ribbon over a gorge. Anywhere ground meets a hole
#: it gets a skirt, which is the same "draw the FACE where two things meet" rule
#: the walls already use, pointed downward.
#:
#: Eight feet rather than four: at four the edge was present in the geometry and
#: lost in the painting, a dark line against bright sky rather than a platform
#: with substance. This is a DRAWING depth and touches no rule — nothing stands
#: on the underside of a board.
SKIRT_FT = 8.0

#: How far the BOTTOM of a skirt pulls in toward its square, as a fraction.
#:
#: ZERO by default, and that is measured. Pulling the bottom edge in turns each
#: side into a trapezoid, which is exactly what a hull wants — but a skirt is
#: drawn per SQUARE, so once the bottoms move, neighbouring faces stop meeting
#: and leave a V-notch at every seam. Rendered, every board in the gallery
#: picked up what looked like a picket fence running round its base: the model
#: painted the notches as planking, on dungeons and caves and streets alike.
#: Flush, the faces are coplanar and merge into one continuous edge.
#:
#: A vessel asks for the taper anyway through its own skin (Skin.skirt_inset),
#: where the seams read as strakes and are an improvement rather than a fence.
SKIRT_INSET = 0.0


def _is_hole(rows, x: int, z: int) -> bool:
    """Is this square a hole — or off the board, which is the same to a skirt?"""
    if z < 0 or z >= len(rows):
        return True
    row = rows[z]
    if x < 0 or x >= len(row):
        return True
    return row[x] in HOLE_CODES


def _hash(x: int, z: int, a: int, b: int) -> int:
    """A stable 32-bit hash of a square, matching the JS side exactly.

    Masked to 32 bits because that is what JavaScript's bitwise operators do to
    their operands; without the mask the two languages agree until a coordinate
    is large enough to overflow, and then a room quietly draws itself
    differently in the depth map and the geometry.
    """
    return ((x * a) ^ (z * b)) & 0xFFFFFFFF


def _cloud(x: int, z: int) -> float:
    """Brightness for one square of open sky, around 1.0.

    Open sky was painted as a single flat colour across two hundred squares,
    and a large uniform horizontal plane seen from above is not sky — it is
    WATER, which is exactly what the first sky-island boards came back as,
    reflections included. What separates the two at this camera angle is cloud:
    broken, soft, brighter in patches. So the sky is mottled rather than filled.

    Two frequencies — a coarse one in three-square blocks for cloud masses and a
    fine one for break-up. Server-side only: the browser draws no geometry on a
    hole at all, so this needs no mirror.
    """
    coarse = (_hash(x // 3, z // 3, 12289, 40503) & 255) / 255.0
    fine = (_hash(x, z, 65521, 30011) & 255) / 255.0
    return 0.84 + 0.24 * (0.74 * coarse + 0.26 * fine)


def variant_of(x: int, z: int, count: int) -> int:
    """Which arrangement this square's object uses.

    Derived from the coordinates rather than rolled, so a room draws the same
    way every time it is looked at — and so the depth map the painter sees and
    the geometry the player sees pick the SAME one.
    """
    return _hash(x, z, 73856093, 19349663) % max(1, count) if count > 1 else 0


def variant_smooth(x: int, z: int, count: int) -> int:
    """Like :func:`variant_of`, but neighbouring squares usually agree.

    For anything that is a MASS rather than a set of separate objects. A rock
    face whose every square picks its own height independently is a field of
    cubes — that is not a guess, it is what the first mountain pass came back
    as — because only the squares bordering open floor are drawn, so a
    one-square-thick shell with per-square heights has nothing left to connect
    it. Sampling the arrangement over two-square blocks turns the same
    variation into ridges and shelves.
    """
    return (_hash(x // 2, z // 2, 73856093, 19349663) % max(1, count)
            if count > 1 else 0)


def yaw_of(x: int, z: int) -> int:
    """Quarter turns for this square's object. Breaks the grid-lock look."""
    return _hash(x, z, 83492791, 29819387) & 3


#: How much a tile's DRAWN height may wander, as a fraction.
#:
#: Applied only where height is not a rules answer — see `height_scale`. Small
#: on purpose: this is for a wall run that isn't machined flat, not for ruins.
HEIGHT_JITTER = 0.12


def height_scale(code: str, x: int, z: int, cover_height_ft: int) -> float:
    """Per-instance height multiplier for a tile, in ``[1 - JITTER, 1]``.

    **Never varies a height the RULES quote.** A crate screens four feet, a low
    wall three, and a player deciding whether to break line of sight — which is
    most of what a fight against a stronger enemy consists of — reads that off
    the picture. Drawing one crate shorter than another would be inventing a
    difference the engine will not honour, and it would mislead in exactly the
    situation where being misled is expensive.

    ``cover_height_ft > 0`` marks precisely the tiles whose height IS the
    answer, so they are left alone and everything else gets a little life.
    """
    if cover_height_ft > 0:
        return 1.0
    return 1.0 - HEIGHT_JITTER * (_hash(x, z, 19349663, 83492791) & 255) / 255.0


def run_axis(same, x: int, z: int) -> int:
    """Quarter turns that line a part up with the RUN it belongs to.

    The companion to :func:`yaw_of`, for the things a per-square random turn
    gets wrong. A boulder may face any way; a ship's rail, a palisade and a tent
    wall are things that RUN, and turned individually they come out as a row of
    quarter-turned fragments rather than one continuous rail.

    Parts are authored running along x, so this returns 0 for an x-run and 1 for
    a z-run. ``same(x, z)`` asks whether the neighbour is part of the same run.
    """
    along_x = int(bool(same(x - 1, z))) + int(bool(same(x + 1, z)))
    along_z = int(bool(same(x, z - 1))) + int(bool(same(x, z + 1)))
    return 1 if along_z > along_x else 0


def is_solid_part(part) -> bool:
    """Which of the two part forms this is — a box, or a prismatoid.

    Told apart by whether the first element is a NUMBER, which is a test both
    languages can make cheaply and identically. See :mod:`vtt.skins` for what
    the second form buys.
    """
    return not isinstance(part[0], (int, float))


#: Which way a quarter turn sends a part authored facing +z (south).
#: ``(x, z) -> (1 - z, x)`` per quarter, so south goes to west, then north,
#: then east. Mirrored by OUT_DIRS in activity-ui/src/lib/boardView.ts.
OUT_DIRS = ((0, 1), (-1, 0), (0, -1), (1, 0))


def out_corner(inside, x: int, z: int) -> bool:
    """Does this square face the outdoors on two sides that MEET?

    A tent's corner. Four of the twelve squares in a tent's wall ring are one,
    and a shape aimed at only one of their two outsides leaves the other a
    sheer face — so every tent had two pitched sides and two cliffs, which is
    what "the tents need corner pieces" means. A corner needs its own
    arrangement, and this is the question that selects it.
    """
    outs = [not inside(x + dx, z + dz) for dx, dz in OUT_DIRS]
    return sum(outs) == 2 and any(outs[t] and outs[(t - 1) % 4] for t in range(4))


def out_axis(inside, x: int, z: int, corner: bool = False) -> int:
    """Quarter turns that point a part at the OUTDOORS.

    The third orientation rule, and the one a tent needed. :func:`yaw_of` turns
    a boulder any way at all and :func:`run_axis` lines a rail up with its run —
    but neither can answer "which side of this wall is the weather on", and
    without that a tent's canvas can only lean by the same amount in both
    directions, which is to say not at all. Given the outside, the same square
    carries an eaves plane sloping up and IN, and the tent gets the pitch that
    is the whole of what says tent.

    ``inside(x, z)`` says whether a neighbour belongs to the same structure.
    Directions are tried in a fixed order so a square picks the same one every
    time and in both languages.

    With ``corner``, the turn is chosen so BOTH of the arrangement's outward
    sides land on real outdoors: a part is authored facing +z, and a quarter
    turn sends its +x side to the direction one step back round the compass, so
    the corner arrangement wants a turn where those two agree.
    """
    outs = [not inside(x + dx, z + dz) for dx, dz in OUT_DIRS]
    if corner:
        for turns in range(4):
            if outs[turns] and outs[(turns - 1) % 4]:
                return turns
    for turns in range(4):
        if outs[turns]:
            return turns
    return 0


def rotate_part(part, turns: int):
    """Turn one part's footprint a quarter at a time about its square's centre.

    ``(x, z) -> (1 - z, x)`` per quarter, which keeps it inside the square —
    and keeps a shape that is symmetric about the centre exactly where it was,
    which is what lets a watchtower's roof reach out over the whole footprint
    from one square without a random turn moving it. Both part forms rotate by
    the same map; a box rotates its two corners, a solid rotates every vertex.
    """
    t = turns & 3
    if is_solid_part(part):
        bottom, top, y0, y1 = part
        for _ in range(t):
            bottom = tuple((1.0 - z, x) for x, z in bottom)
            top = tuple((1.0 - z, x) for x, z in top)
        return (bottom, top, y0, y1)
    x0, x1, z0, z1, y0, y1 = part
    for _ in range(t):
        x0, x1, z0, z1 = 1.0 - z1, 1.0 - z0, x0, x1
    return (x0, x1, z0, z1, y0, y1)



#: The square's four corners, in the order the floor's top face is wound —
#: counter-clockwise seen from above, so the face's normal points up.
_CORNERS = ((0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0))


def _outline_bottoms(pts, ends, inset: float):
    """Where each outline vertex sits at the BOTTOM of the side below it.

    A MITRE, not a per-edge offset, and the difference is the whole reason this
    is a function. Pulling each side's bottom straight back along its own
    normal keeps two collinear sides coplanar — but where the outline turns,
    the two sides' bottoms part company and leave a notch, which on a hull is a
    wedge of daylight at every corner of the bow. Offsetting the VERTEX along
    the bisector of its two sides gives both of them the same bottom point, so
    the shell closes.
    """
    n = len(pts)
    if inset <= 0.0 or n < 3:
        return [(p[0], p[1]) for p in pts]

    def normal(i: int):
        ax, az = pts[i]
        bx, bz = pts[(i + 1) % n]
        ex, ez = bx - ax, bz - az
        run = math.sqrt(ex * ex + ez * ez)
        if run < 1e-9:
            return None
        return (-ez / run, ex / run)      # outward, for a ring wound CCW above

    out = []
    for i in range(n):
        a = normal(i - 1) if ends[i - 1] else None
        b = normal(i) if ends[i] else None
        if a and b:
            bx, bz = a[0] + b[0], a[1] + b[1]
            mag = math.sqrt(bx * bx + bz * bz)
            if mag < 1e-9:                # a spike doubling back on itself
                dx, dz = a[0] * inset, a[1] * inset
            else:
                bx, bz = bx / mag, bz / mag
                # 1/cos(half angle), clamped so a very sharp corner does not
                # throw its bottom vertex across the board.
                k = inset / max(0.30, bx * a[0] + bz * a[1])
                dx, dz = bx * k, bz * k
        elif a or b:
            m = a or b
            dx, dz = m[0] * inset, m[1] * inset
        else:
            dx = dz = 0.0
        out.append((pts[i][0] - dx, pts[i][1] - dz))
    return out


def footprint(e_w: bool, e_e: bool, e_n: bool, e_s: bool,
              inset: float = 0.0):
    """This square's floor outline, which edges face the outside, and where the
    bottom of each side sits.

    Returns ``(points, edge_ends, bottoms)`` — a polygon in square fractions,
    one flag per edge saying whether the floor STOPS there (so a side has to be
    drawn), and the same polygon pulled in by ``inset`` for the bottom of those
    sides.

    A square's outline is its square. There WAS a corner chamfer here, cutting
    each stair step's outer corner so a carved hull read as a line rather than
    a flight of steps — it worked, and it was superseded: a one-square cut is
    still a one-square answer, and joining the corners farthest from a hull's
    middle needs the outline as a LOOP, which no square can see. That is
    :mod:`vtt.hull`, traced once on the server. Removed rather than left
    standing, because a code path nothing reaches is a trap for whoever comes
    next.
    """
    side_ends = (e_w, e_s, e_e, e_n)      # edge i runs corner i -> corner i+1
    pts = list(_CORNERS)
    ends = list(side_ends)
    return pts, ends, _outline_bottoms(pts, ends, inset)


#: What each object is SHAPED like, as parts of its square.
#:
#: One box per tile is what made a crypt read as a tray of dice. The model
#: paints the silhouette it is handed — proved when a pillar went from a cube to
#: a prism and came back as a column — so the way to be understood is to hand it
#: a recognisable outline. Deliberately not a finer voxel grid: subdividing a
#: cube gets you a stair-stepped cube, whereas a tapered lid and four legs are a
#: sarcophagus and a table at almost no cost.
#:
#: Each part is ``(x0, x1, z0, z1, y_from, y_to)`` in FRACTIONS — of the square
#: for x/z, of the tile's standing height for y. Mirrored by OBJECT_PARTS in
#: activity-ui/src/lib/boardView.ts; the depth map and the geometry must be the
#: same object or the painting is conditioned on something the player is not
#: looking at.
OBJECT_VARIANTS: dict[str, tuple[tuple[Part, ...], ...]] = {
    # A TREE: a slim trunk under a crown that is wider than it is tall.
    #
    # It was two stacked cylinders written by hand in each language — and they
    # had already drifted, so the depth map the painting is conditioned on
    # carried a different tree from the one the player was looking at (Python
    # gave it a 0.32 trunk running the whole height, the browser a 0.13 trunk
    # stopping at 0.55). Both drew a post with a slightly wider post on top,
    # and a forest came back painted as a field of sawn-off stumps on a lawn.
    # That is the same failure as the crypt-of-dice and the cliff-as-village:
    # the model paints the silhouette it is handed, and a cylinder is a stump.
    #
    # Here instead so BOTH sides read one table, and shaped as a real crown:
    # rising out of the trunk, widest around two thirds up, closing to a soft
    # top. The last ring is small but never a point — a crown that comes to an
    # apex is a conifer, and only the third arrangement is meant to be one.
    # THE CROWN IS AS WIDE AS THE TREE IS TALL, and it did not used to be. A
    # tree is drawn 18 ft — 3.6 squares — and the widest ring here was 0.50,
    # which is a crown five feet across: a 3.6:1 pole, on a table whose own
    # comment called it "a round, heavy head". Nobody had put the two numbers
    # beside each other. A real broadleaf is roughly as wide as it is tall.
    #
    # THIS IS THE ONE PLACE THE PICTURE IS ALLOWED TO OVERRUN THE GRID, and it
    # is a deliberate exception rather than a slip. A canopy covers squares
    # that are open, walkable and shootable — the rules do not change and the
    # tile still owns exactly its own square — so the board has to be able to
    # SEE through it. `vttScene3d` fades the canopy around anything standing
    # under it; without that fade this change hides tokens and is a
    # regression. The two go together.
    "T": (
        # Broadleaf: a round, heavy head, near enough as broad as it is high.
        (solid(_ring(0.11), _ring(0.075, 0.52, 0.48), 0.00, 0.46),
         solid(_ring(0.80, 0.52, 0.48, 0.14), _ring(1.80, 0.52, 0.48, 0.10),
               0.38, 0.68),
         solid(_ring(1.80, 0.52, 0.48, 0.10), _ring(0.46, 0.54, 0.46),
               0.68, 1.00)),
        # Older and leaning, its head thrown off the trunk.
        (solid(_ring(0.12), _ring(0.08, 0.44, 0.56), 0.00, 0.52),
         solid(_ring(0.70, 0.40, 0.60, 0.16), _ring(1.60, 0.36, 0.64, 0.12),
               0.44, 0.74),
         solid(_ring(1.60, 0.36, 0.64, 0.12), _ring(0.38, 0.32, 0.68),
               0.74, 1.00)),
        # A conifer: skirted low, tapering the whole way to a spire. Kept
        # NARROW on purpose — a spruce really is a cone and not a ball, and one
        # narrow silhouette among three broad ones is what makes a mixed wood
        # read as a mixed wood.
        (solid(_ring(0.09), _ring(0.07), 0.00, 0.30),
         solid(_ring(0.90, 0.50, 0.50, 0.10), _ring(0.52, 0.50, 0.50, 0.08),
               0.24, 0.62),
         solid(_ring(0.52, 0.50, 0.50, 0.08), _ring(0.06), 0.62, 1.00)),
        # A split crown — two heads off one bole, which is what an old
        # broadleaf in the open does. The heads are pushed a full square apart
        # so that at this size they read as two and not as one lumpy one.
        (solid(_ring(0.13), _ring(0.09, 0.50, 0.50), 0.00, 0.40),
         solid(_ring(0.50, 0.10, 0.30, 0.14), _ring(1.15, 0.02, 0.22, 0.12),
               0.36, 0.66),
         solid(_ring(1.15, 0.02, 0.22, 0.12), _ring(0.34, 0.00, 0.20),
               0.66, 0.94),
         solid(_ring(0.44, 0.90, 0.72, 0.14), _ring(1.05, 0.98, 0.80, 0.10),
               0.40, 0.72),
         solid(_ring(1.05, 0.98, 0.80, 0.10), _ring(0.30, 1.00, 0.82),
               0.72, 1.00)),
    ),
    # Sarcophagus / altar: a long chest with an overhanging tapered lid, and a
    # squatter cracked-open one.
    # ALTAR: a plinth, a battered die and a proud cornice under the slab.
    #
    # Two stacked boxes before, which is a wedding cake. The parts here are the
    # parts a mason would actually cut, and the reason they read is that each
    # one is a PRISMATOID: the plinth sits (battered in at the foot), the die
    # leans in as it rises, the cornice throws out over it, and the mensa on top
    # has its arris taken off. None of it is decoration — at this camera the
    # only thing that says "worked stone" rather than "block" is the line where
    # two planes of slightly different size meet.
    "A": (
        (_slab(0.08, 0.92, 0.26, 0.74, 0.00, 0.14, batter=0.05, chamfer=0.06),
         _slab(0.14, 0.86, 0.30, 0.70, 0.14, 0.74, chamfer=0.05),
         _slab(0.10, 0.90, 0.27, 0.73, 0.74, 0.88, batter=0.22),
         _slab(0.06, 0.94, 0.24, 0.76, 0.88, 1.00, chamfer=0.10)),
        # A broken one: the mensa slid off and the die is open to the weather.
        (_slab(0.12, 0.88, 0.26, 0.74, 0.00, 0.12, batter=0.06, chamfer=0.05),
         _slab(0.16, 0.84, 0.30, 0.70, 0.12, 0.66, chamfer=0.07),
         solid(rect(0.10, 0.64, 0.24, 0.78), rect(0.16, 0.58, 0.28, 0.72),
               0.66, 0.94),
         _slab(0.62, 0.98, 0.30, 0.70, 0.58, 0.80, chamfer=0.14)),
    ),
    # TABLE: turned legs, an apron between them, and a top with an edge.
    #
    # Four square posts and a slab before. A leg that TAPERS reads as turned
    # even at eight sides and thirty feet up; the apron is what makes it
    # joinery rather than four sticks holding a board; and the top's chamfer is
    # what keeps it from reading as a lid lying on them.
    "n": (
        (solid(_ring(0.055, 0.17, 0.21), _ring(0.075, 0.17, 0.21), 0.00, 0.68),
         solid(_ring(0.055, 0.83, 0.21), _ring(0.075, 0.83, 0.21), 0.00, 0.68),
         solid(_ring(0.055, 0.17, 0.79), _ring(0.075, 0.17, 0.79), 0.00, 0.68),
         solid(_ring(0.055, 0.83, 0.79), _ring(0.075, 0.83, 0.79), 0.00, 0.68),
         _slab(0.14, 0.86, 0.16, 0.84, 0.54, 0.68),          # the apron
         _slab(0.06, 0.94, 0.10, 0.90, 0.68, 0.80, chamfer=0.16),
         _slab(0.04, 0.96, 0.08, 0.92, 0.80, 1.00, chamfer=0.05)),
        # Overturned: the top on edge, legs in the air, one of them snapped.
        (solid(rect(0.10, 0.26, 0.12, 0.88), rect(0.13, 0.23, 0.14, 0.86),
               0.00, 1.00),
         solid(_ring(0.05, 0.34, 0.26), _ring(0.035, 0.34, 0.26), 0.58, 0.82),
         solid(_ring(0.05, 0.34, 0.74), _ring(0.035, 0.34, 0.74), 0.58, 0.76),
         _slab(0.24, 0.44, 0.20, 0.80, 0.50, 0.60, chamfer=0.12)),
    ),
    # CRATES: stacked and offset, and each one a box with its LID proud.
    #
    # The overhang is the whole tell. A crate drawn as a plain cuboid is a
    # cuboid — and a square full of them is the crypt-of-dice failure with a
    # different label. A battered body, a chamfered top edge and a lid standing
    # a little wider than what it closes is what a packing case looks like from
    # above, which is the only angle this board has.
    "o": (
        (_slab(0.08, 0.58, 0.10, 0.62, 0.00, 0.52, batter=0.04, chamfer=0.05),
         _slab(0.06, 0.60, 0.08, 0.64, 0.52, 0.62, chamfer=0.14),
         _slab(0.46, 0.92, 0.36, 0.90, 0.00, 0.40, batter=0.04, chamfer=0.05),
         _slab(0.44, 0.94, 0.34, 0.92, 0.40, 0.48, chamfer=0.14),
         _slab(0.16, 0.56, 0.18, 0.58, 0.62, 0.92, batter=0.04, chamfer=0.05),
         _slab(0.14, 0.58, 0.16, 0.60, 0.92, 1.00, chamfer=0.14)),
        (_slab(0.12, 0.66, 0.14, 0.70, 0.00, 0.90, batter=0.03, chamfer=0.04),
         _slab(0.10, 0.68, 0.12, 0.72, 0.90, 1.00, chamfer=0.12),
         _slab(0.62, 0.94, 0.52, 0.92, 0.00, 0.46, batter=0.04, chamfer=0.05),
         _slab(0.60, 0.96, 0.50, 0.94, 0.46, 0.54, chamfer=0.14)),
        # A broken one among them: staves sprung, no lid left.
        (_slab(0.10, 0.52, 0.20, 0.64, 0.00, 0.60, batter=0.04, chamfer=0.05),
         _slab(0.08, 0.54, 0.18, 0.66, 0.60, 0.70, chamfer=0.14),
         solid(rect(0.54, 0.90, 0.14, 0.56), rect(0.50, 0.94, 0.10, 0.60),
               0.00, 0.86),
         _slab(0.34, 0.74, 0.60, 0.94, 0.00, 0.36, batter=0.05, chamfer=0.06),
         _slab(0.32, 0.76, 0.58, 0.96, 0.36, 0.44, chamfer=0.16)),
    ),
    # LOW WALL: a battered course under a coping that sheds.
    #
    # Two boxes before, which is a kerb. A wall is thicker at the foot than at
    # the head, and its coping is proud of both faces and weathered to one side
    # — the coping's top plan is offset, so the cap slopes, which is the one
    # line that says masonry rather than concrete at this distance.
    "w": (
        (solid(rect(0.16, 0.84, 0.00, 1.00), rect(0.22, 0.78, 0.00, 1.00),
               0.00, 0.78),
         solid(rect(0.10, 0.90, 0.00, 1.00), rect(0.16, 0.86, 0.00, 1.00),
               0.78, 1.00)),
    ),
    # PILLAR: a base, a shaft with entasis, and a capital.
    #
    # It was `prism(cx, cz, 0.32)` written by hand in BOTH renderers and in
    # neither table — the last shape in the project that lived where the
    # generated gate could not see it, and the exact arrangement the tree was
    # moved out of for having drifted between the two languages. A plain
    # cylinder is also a post: what makes a column a column is that it swells
    # slightly at a third of its height and carries something at the top.
    "O": (
        (solid(_ring(0.40), _ring(0.34), 0.00, 0.10),
         solid(_ring(0.34), _ring(0.335), 0.10, 0.34),
         solid(_ring(0.335), _ring(0.30), 0.34, 0.88),
         solid(_ring(0.30), _ring(0.40), 0.88, 1.00)),
        # A plainer one, square-set: a pier rather than a column.
        (_slab(0.14, 0.86, 0.14, 0.86, 0.00, 0.09, chamfer=0.16),
         _slab(0.20, 0.80, 0.20, 0.80, 0.09, 0.90, chamfer=0.06),
         _slab(0.14, 0.86, 0.14, 0.86, 0.90, 1.00, batter=0.18)),
    ),
}

#: Variant 0 of each, for anything that only wants one answer.
OBJECT_PARTS = {k: v[0] for k, v in OBJECT_VARIANTS.items()}


#: Face shading for the colour pass. Top full, the two visible sides darker —
#: without it the init image is a flat plan and the model paints a flat plan.
TOP_TINT, SIDE_A_TINT, SIDE_B_TINT = 1.0, 0.72, 0.58
