"""The isometric camera, server side — and the depth map it rasterizes.

**This file is the mirror of ``activity-ui/src/lib/isocam.ts``. Change one and
you must change the other.**

Why a camera lives on the server at all: the isometric board is drawn from
geometry in the browser, and the painted layer over it is produced here, by
conditioning a diffusion model on a DEPTH MAP of that same geometry. Two
programs in two languages therefore project the same room, and if their cameras
disagree by a degree the painting no longer sits on the walls — every shadow
lands beside the thing casting it. So the camera is a handful of constants and
a page of arithmetic, kept short enough to verify by eye, and
``scripts/iso_alignment_check.py`` asserts the two agree numerically.

It can be this short because the camera is ORTHOGRAPHIC and never rotates: the
projection is a plain affine map, so it inverts in closed form, and pan and zoom
are a translate-and-scale of the projected image. That last part is what lets a
painting baked at one framing stay aligned at every framing the player chooses.

World units are SQUARES: grid ``(x, y)`` is world ``(x, ·, y)``, X east and Z
south, Y up. Feet convert through the board's own ``square_ft``.
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
YAW_DEG = 45.0

#: How far the camera tilts down. Higher shows more floor (positions are easier
#: to read); lower shows more of the walls' faces. 40° favours the floor,
#: because this is a board you have to be able to count squares on.
PITCH_DEG = 40.0

_YAW = math.radians(YAW_DEG)
_PITCH = math.radians(PITCH_DEG)
_SIN_Y, _COS_Y = math.sin(_YAW), math.cos(_YAW)
_SIN_P, _COS_P = math.sin(_PITCH), math.cos(_PITCH)

#: Camera basis, world-space. Pitch down about X, then yaw about Y — the order
#: that keeps the horizon level (no roll).
RIGHT = (_COS_Y, 0.0, -_SIN_Y)
UP = (-_SIN_Y * _SIN_P, _COS_P, -_COS_Y * _SIN_P)
FORWARD = (-_SIN_Y * _COS_P, -_SIN_P, -_COS_Y * _COS_P)


@dataclass(frozen=True)
class Projected:
    x: float
    y: float      # grows DOWNWARD, to match screen convention
    depth: float  # grows with distance from the camera; a sort key, not a length


def project(wx: float, wy: float, wz: float) -> Projected:
    """Project a world point. Mirrors ``isocam.ts``'s ``project``."""
    return Projected(
        x=wx * _COS_Y - wz * _SIN_Y,
        y=-(wx * UP[0] + wy * UP[1] + wz * UP[2]),
        depth=wx * FORWARD[0] + wy * FORWARD[1] + wz * FORWARD[2],
    )


def unproject(sx: float, sy: float, wy: float) -> tuple[float, float]:
    """Invert :func:`project` onto a horizontal plane at height ``wy``.

    Two equations, two unknowns, by Cramer's rule — whose determinant is
    ``sin(pitch)``. That is why a pitch of 0 is forbidden: looking along the
    ground, every square on a line projects to one pixel.
    """
    rhs = sy + wy * _COS_P
    det = _SIN_P
    wx = (sx * _COS_Y * _SIN_P + _SIN_Y * rhs) / det
    wz = (_COS_Y * rhs - _SIN_Y * _SIN_P * sx) / det
    return wx, wz


#: Breathing room around the board in the CANONICAL framing, in squares.
#: Mirrored by FRAME_PAD_SQUARES in activity-ui/src/lib/isocam.ts — a
#: disagreement slides the whole painting off the geometry by a constant, which
#: is the most convincing kind of wrong because everything still looks
#: plausible.
FRAME_PAD_SQUARES = 0.25

@dataclass(frozen=True)
class Bounds:
    min_x: float
    max_x: float
    min_y: float
    max_y: float

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y


def bounds_of(w: int, h: int, tallest: float, base_y: float = 0.0) -> Bounds:
    """The projected bounding box of a ``w x h`` board whose tallest structure
    stands ``tallest`` units above its floor.

    Every extreme of an axis-aligned box lands on one of its eight corners under
    an affine map, so checking the corners is exact rather than a safe guess.
    This rectangle is the CANONICAL FRAMING: the depth map and the painting both
    span exactly it, which is what makes the painting independent of whatever
    viewport or zoom a player happens to have.
    """
    xs: list[float] = []
    ys: list[float] = []
    for wx in (0.0, float(w)):
        for wz in (0.0, float(h)):
            for wy in (base_y, base_y + tallest):
                p = project(wx, wy, wz)
                xs.append(p.x)
                ys.append(p.y)
    return Bounds(min(xs), max(xs), min(ys), max(ys))


# ---------------------------------------------------------------------------
# Depth rasterizer
# ---------------------------------------------------------------------------
#
# What the depth ControlNet is conditioned on. Not a picture of the room — a
# statement of how far away every part of it is, which is the one thing a text
# prompt can never say and the reason the painting lands on the geometry
# instead of near it.
#
# Convention: NEAR IS WHITE. That is what MiDaS-style depth looks like and what
# the SDXL depth ControlNets were trained on; handing one an inverted map gets a
# room turned inside out.


def _tri(buf, pts, zs, rgb=None, colour=None, tint: float = 1.0) -> None:
    """Rasterize one triangle into a float depth buffer, keeping the nearest.

    Depth is interpolated with barycentric weights, which is EXACT here rather
    than an approximation: the projection is affine and every face is planar, so
    depth really is linear in screen space. A flat fill per tile would band a
    floor into one step per square.
    """
    import numpy as np

    h, w = buf.shape
    (x0, y0), (x1, y1), (x2, y2) = pts
    min_x = max(0, int(math.floor(min(x0, x1, x2))))
    max_x = min(w - 1, int(math.ceil(max(x0, x1, x2))))
    min_y = max(0, int(math.floor(min(y0, y1, y2))))
    max_y = min(h - 1, int(math.ceil(max(y0, y1, y2))))
    if min_x > max_x or min_y > max_y:
        return
    denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    if abs(denom) < 1e-9:
        return

    ys, xs = np.mgrid[min_y:max_y + 1, min_x:max_x + 1]
    px = xs + 0.5
    py = ys + 0.5
    a = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / denom
    b = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / denom
    c = 1.0 - a - b
    inside = (a >= -1e-6) & (b >= -1e-6) & (c >= -1e-6)
    if not inside.any():
        return
    z = a * zs[0] + b * zs[1] + c * zs[2]
    view = buf[min_y:max_y + 1, min_x:max_x + 1]
    win = inside & (z < view)
    if rgb is not None and colour is not None:
        # Same z-test as the depth write, so the colour picture and the depth
        # picture can never disagree about which surface is in front.
        sub = rgb[min_y:max_y + 1, min_x:max_x + 1]
        for ch in range(3):
            np.copyto(sub[:, :, ch],
                      np.full(win.shape, colour[ch] * tint, dtype=np.float64),
                      where=win)
    np.copyto(view, z, where=win)


def _quad(buf, pts, zs, rgb=None, colour=None, tint: float = 1.0) -> None:
    _tri(buf, (pts[0], pts[1], pts[2]), (zs[0], zs[1], zs[2]), rgb, colour, tint)
    _tri(buf, (pts[0], pts[2], pts[3]), (zs[0], zs[2], zs[3]), rgb, colour, tint)


#: How thick a wall is DRAWN, as a fraction of its square.
#:
#: A wall occupies its whole square in the RULES and always will — it is
#: impassable, it blocks sight, and none of that changes here. But drawn as a
#: full five-foot cube it presents an enormous top face, and at this camera
#: angle that top face IS the rim that made every enclosed room read as a tray.
#: Thinned to a slab, most of what the eye gets is the wall's vertical faces,
#: which is what a wall actually looks like.
#:
#: The floor strip left either side is a drawing, not playable ground; the grid,
#: the movement wash and the outline all still say the square is solid.
#: Mirrored by WALL_THICKNESS in activity-ui/src/lib/boardView.ts.
WALL_THICKNESS = 0.34


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
    "T": (
        # Broadleaf: a round, heavy head.
        (solid(_ring(0.11), _ring(0.075, 0.52, 0.48), 0.00, 0.46),
         solid(_ring(0.22, 0.52, 0.48, 0.14), _ring(0.50, 0.52, 0.48, 0.10),
               0.38, 0.68),
         solid(_ring(0.50, 0.52, 0.48, 0.10), _ring(0.13, 0.54, 0.46),
               0.68, 1.00)),
        # Older and leaning, its head thrown off the trunk.
        (solid(_ring(0.12), _ring(0.08, 0.44, 0.56), 0.00, 0.52),
         solid(_ring(0.20, 0.44, 0.56, 0.16), _ring(0.46, 0.42, 0.58, 0.12),
               0.44, 0.74),
         solid(_ring(0.46, 0.42, 0.58, 0.12), _ring(0.11, 0.40, 0.60),
               0.74, 1.00)),
        # A conifer: skirted low, tapering the whole way to a spire.
        (solid(_ring(0.09), _ring(0.07), 0.00, 0.30),
         solid(_ring(0.42, 0.50, 0.50, 0.10), _ring(0.25, 0.50, 0.50, 0.08),
               0.24, 0.62),
         solid(_ring(0.25, 0.50, 0.50, 0.08), _ring(0.04), 0.62, 1.00)),
        # A split crown — two heads off one bole, which is what an old
        # broadleaf in the open does.
        (solid(_ring(0.13), _ring(0.09, 0.50, 0.50), 0.00, 0.40),
         solid(_ring(0.18, 0.38, 0.44, 0.14), _ring(0.34, 0.34, 0.40, 0.12),
               0.36, 0.66),
         solid(_ring(0.34, 0.34, 0.40, 0.12), _ring(0.10, 0.32, 0.38),
               0.66, 0.94),
         solid(_ring(0.16, 0.64, 0.58, 0.14), _ring(0.30, 0.68, 0.62, 0.10),
               0.40, 0.72),
         solid(_ring(0.30, 0.68, 0.62, 0.10), _ring(0.09, 0.70, 0.64),
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


def _box(face, x0: float, x1: float, z0: float, z1: float,
         y1: float, y0: float = 0.0, shade=None) -> None:
    """A box: top face plus the two sides this camera can see."""
    if shade:
        shade(TOP_TINT)
    face([(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)])
    # Only +x and +z face the camera at yaw 45; the other two are never visible
    # and rasterizing them is work the z-buffer throws away.
    if shade:
        shade(SIDE_A_TINT)
    face([(x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (x0, y0, z1)])
    if shade:
        shade(SIDE_B_TINT)
    face([(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)])
    if shade:
        shade(TOP_TINT)


def _tint_for(nx: float, ny: float, nz: float) -> float:
    """Face shading from a normal, agreeing exactly with the box's three tints.

    A box only ever shows three faces and could name their tints outright. A
    prismatoid's sides point wherever the shape points — a raked tower leg, a
    tent's pitched canvas, a hull tapering in under the waterline — so the
    shading has to be a function of the direction rather than a table. Weighted
    by how much each face looks up, south and east, it reproduces TOP_TINT,
    SIDE_A_TINT and SIDE_B_TINT exactly on the axis-aligned cases and
    interpolates between them everywhere else.
    """
    wy, wz, wx = max(0.0, ny), max(0.0, nz), max(0.0, nx)
    s = wy + wz + wx
    if s <= 1e-9:
        return SIDE_B_TINT * 0.7          # facing away; barely ever visible
    return (TOP_TINT * wy + SIDE_A_TINT * wz + SIDE_B_TINT * wx) / s


def _solid(face, shade, ox: float, oz: float, bottom, top,
           y0: float, y1: float) -> None:
    """A PRISMATOID: two polygons at two heights, joined edge by edge.

    The primitive that stops everything being a cube. A box is the special case
    where both polygons are the same rectangle, so nothing had to be rewritten
    to gain tapers, leans and cut corners — see :mod:`vtt.skins` for what each
    of those is worth.

    Back faces are skipped rather than drawn and thrown away by the z-buffer,
    which is the same saving ``_box`` takes by only drawing its +x and +z sides;
    the difference is that a leaning face has to be TESTED rather than known.
    """
    n = len(bottom)
    if n < 3:
        return
    if shade:
        shade(TOP_TINT)
    face([(ox + x, y1, oz + z) for x, z in top])
    for i in range(n):
        j = (i + 1) % n
        ax, az = bottom[i]
        bx, bz = bottom[j]
        cx, cz = top[i]
        dx, dz = top[j]
        # Normal of the quad (top[i], bottom[i], bottom[j], top[j]).
        ux, uy, uz = ax - cx, y0 - y1, az - cz
        vx, vy, vz = bx - cx, y0 - y1, bz - cz
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length < 1e-12:
            continue                       # a degenerate edge: a ridge, an apex
        nx, ny, nz = nx / length, ny / length, nz / length
        if nx * FORWARD[0] + ny * FORWARD[1] + nz * FORWARD[2] >= 0:
            continue                       # facing away from the camera
        if shade:
            shade(_tint_for(nx, ny, nz))
        face([(ox + cx, y1, oz + cz), (ox + ax, y0, oz + az),
              (ox + bx, y0, oz + bz), (ox + dx, y1, oz + dz)])
    if shade:
        shade(TOP_TINT)


def draw_parts(face, shade, parts, turns: int, ox: float, oz: float,
               top: float, base: float = 0.0) -> None:
    """Draw one arrangement's parts, in whichever of the two forms each is.

    The one place the part vocabulary is interpreted server-side; mirrored by
    ``drawParts`` in ``vttScene3d.ts``. Anything that iterates parts goes
    through here, so a new form has exactly two places to reach.
    """
    for raw in parts:
        part = rotate_part(raw, turns)
        if is_solid_part(part):
            bottom, cap, py0, py1 = part
            _solid(face, shade, ox, oz, bottom, cap,
                   base + top * py0, base + top * py1)
        else:
            px0, px1, pz0, pz1, py0, py1 = part
            _box(face, ox + px0, ox + px1, oz + pz0, oz + pz1,
                 base + top * py1, y0=base + top * py0, shade=shade)




@lru_cache(maxsize=32)
def _obj_triangles(path: str) -> tuple[tuple[tuple[float, float, float], ...], ...]:
    """An OBJ's faces as triangles, in the file's own units.

    Deliberately minimal: ``v`` and ``f`` and nothing else. No materials, no
    normals, no texture coordinates — the geometry here is a DEPTH OCCLUDER,
    and the painted layer supplies every appearance the board ever shows. That
    is also why the catalogue prefers OBJ over glTF: this is the whole loader,
    and it needs no dependency the server did not already have.

    Faces of more than three vertices are fanned, matching what ``face()``
    does with a polygon everywhere else on the board.
    """
    verts: list[tuple[float, float, float]] = []
    tris: list[tuple[tuple[float, float, float], ...]] = []
    try:
        with open(path, "r", errors="ignore") as fh:
            for line in fh:
                if line.startswith("v "):
                    p = line.split()
                    if len(p) >= 4:
                        try:
                            verts.append((float(p[1]), float(p[2]), float(p[3])))
                        except ValueError:
                            pass
                elif line.startswith("f "):
                    idx: list[int] = []
                    for tok in line.split()[1:]:
                        # "f v/vt/vn" — only the vertex index is wanted.
                        head = tok.split("/")[0]
                        try:
                            i = int(head)
                        except ValueError:
                            continue
                        # OBJ is 1-based and allows NEGATIVE indices counting
                        # back from the newest vertex. Reading a -1 as an
                        # absolute index silently builds a mesh out of the
                        # wrong corners, which looks like a broken model rather
                        # than a broken parser.
                        idx.append(i - 1 if i > 0 else len(verts) + i)
                    for k in range(1, len(idx) - 1):
                        try:
                            tris.append((verts[idx[0]], verts[idx[k]],
                                         verts[idx[k + 1]]))
                        except IndexError:
                            continue
    except OSError:
        return ()
    return tuple(tris)


def setpiece_triangles(inst: dict, mesh_file: str, *,
                       floor_y: float = 0.0) -> list[tuple[tuple[float, float, float], ...]]:
    """A landmark's triangles placed on the board, in board units.

    The transform is fixed and short, and every term of it comes from the
    server: ``scale`` and ``pivot`` are measured off this same file by
    :func:`vtt.setpieces.mesh_fit`, so there is no arithmetic here for the
    browser to arrive at differently — it applies the identical five steps to
    the identical numbers.

        scale -> centre on the footprint -> yaw -> stand on the floor

    The yaw is the piece's own ``yaw_fix`` (which way the model was authored)
    plus the quarter turn it was PLACED at, and it turns about the footprint's
    centre. Rotating the mesh without rotating its tiles is the bug
    ``_turned`` exists to prevent, and this is the other half of it.
    """
    from .setpieces import rotate_xz as _sp_rotate

    tris = _obj_triangles(mesh_file)
    if not tris:
        return []
    s = float(inst.get("scale") or 0.0)
    if s <= 0:
        return []
    px, py, pz = (list(inst.get("pivot") or (0.0, 0.0, 0.0)) + [0.0, 0.0, 0.0])[:3]
    up_z = str(inst.get("up") or "y") == "z"
    yaw_deg = (int(inst.get("yaw_fix") or 0) + int(inst.get("yaw") or 0)) % 360
    # The footprint's centre, in board squares. Half a square per unit width is
    # what puts an even-sided landmark on the seam and an odd-sided one on the
    # middle square's own centre — the same reason structures.py insists a post
    # tower's footprint is odd.
    cx = float(inst.get("x") or 0) + float(inst.get("w") or 1) / 2.0
    cz = float(inst.get("y") or 0) + float(inst.get("d") or 1) / 2.0
    out: list[tuple[tuple[float, float, float], ...]] = []
    for tri in tris:
        pts: list[tuple[float, float, float]] = []
        for vx, vy, vz in tri:
            if up_z:
                vy, vz = vz, -vy
            x = vx * s - px
            y = vy * s - py
            z = vz * s - pz
            rx, rz = _sp_rotate(x, z, yaw_deg)
            pts.append((cx + rx, floor_y + y, cz + rz))
        out.append(tuple(pts))
    return out


def terrain_image(rows: Sequence[str], *, colour_of, **kw) -> bytes:
    """The board painted in its own terrain colours. PNG bytes.

    The companion to the depth map, and the half it cannot supply. Depth says
    how far away everything is and nothing about what it is MADE of — fine for
    a walled room, where the walls carry the meaning, and useless outdoors,
    where the board's whole character is FLAT. Water, grass, road and ice all
    sit at height zero and are invisible to depth, which is why a forest came
    back with a pond painted where the grid has none: the model knew water
    belonged somewhere and had no way to learn where.

    Same geometry, same camera, same silhouette — only the output differs. Used
    as an img2img base, it hands the model layout, terrain type and scale, and
    leaves it the one job it is good at.

    ``colour_of(code, skin) -> (r, g, b)``; taking those from the material
    catalogue keeps this picture and the geometry board describing the same
    room — including the skinned squares, so a reef's coral is painted coral.
    """
    return depth_image(rows, _colour_of=colour_of, **kw)


def coverage_mask(rows: Sequence[str], **kw) -> bytes:
    """Where the board actually IS, as a black-and-white PNG.

    The board projects to a DIAMOND and the painting is its bounding RECTANGLE,
    so roughly half the canvas is corner the geometry never covers. Given that
    much empty margin the model paints a second room out there at its own scale
    — five or six times the board's — and the real room reads as a doll's house
    inside a giant one. Words do not stop it: the framing and the negatives both
    ask for empty black and it paints a hearth anyway.

    So the corners are cut rather than requested. This is the stencil, computed
    where it can be looked at without a browser.
    """
    return depth_image(rows, _mask_only=True, **kw)


def depth_image(rows: Sequence[str], *, height_ft, cover_ft=None, decor=None,
                skin_of=None, elevation=None, shells=None, roofs=None,
                setpieces=None,
                _mask_only: bool = False, _colour_of=None, _flat: bool = False,
                square_ft: int = 5,
                px_per_square: int = 48, pad_squares: float = FRAME_PAD_SQUARES,
                structure: Optional[set[str]] = None,
                max_px: int = 1536) -> bytes:
    """Rasterize the board's geometry to a depth map. Returns PNG bytes.

    ``height_ft`` is a callable ``code -> feet`` (``vtt.terrain.tile_height_ft``
    in practice); ``structure`` names the codes drawn as full-square blocks. The
    shapes here must match what ``vttScene3d.ts`` builds, or the painting is
    conditioned on a room the player is not looking at.

    ``skin_of(code, x, z) -> name`` says what a square is MADE of (see
    :mod:`vtt.skins`). A skin may hand over its own silhouette and its own
    drawn height, which is how a mountainside stops being drawn as masonry
    panels; it can never change what the square DOES.

    ``elevation`` is the board's own sparse ``{"x,y": feet}``. It was stored,
    shipped and folded into every distance, reach, cover and area check, and
    drawn by nobody — so a mountain-pass ledge stood ten feet up in the rules
    and flat in the picture, and a ship's deck sat at sea level. A picture
    contradicting the grid is the one thing the board must never do, and this
    was the largest remaining case of it.
    """
    import numpy as np
    from PIL import Image

    from . import skins as _skins

    cover_ft = cover_ft or (lambda _c: 0)
    h_rows = len(rows)
    w_cols = max((len(r) for r in rows), default=0)
    if not h_rows or not w_cols:
        return b""

    def skin_at(x: int, z: int) -> str:
        if skin_of is None or z < 0 or z >= h_rows:
            return ""
        row = rows[z]
        if x < 0 or x >= len(row):
            return ""
        return skin_of(row[x], x, z) or ""

    def eff_height(code: str, x: int, z: int) -> float:
        """How tall this square is DRAWN. A skin may raise it; the rules never
        quote a height for anything a skin is allowed to raise."""
        return _skins.height_of(skin_at(x, z)) or height_ft(code)

    units = lambda ft: ft / float(square_ft or 5)          # noqa: E731
    elev = elevation or {}

    def floor_y(x: int, z: int) -> Optional[float]:
        """How high this square's FLOOR is drawn, or None if it is not there.

        None means a hole or off the board — nothing to stand on, and the
        square beside it needs a side all the way down rather than a step.
        """
        if z < 0 or z >= h_rows:
            return None
        row = rows[z]
        if x < 0 or x >= len(row) or row[x] in HOLE_CODES:
            return None
        return units(int(elev.get(f"{x},{z}", 0) or 0))

    # Over the squares rather than the codes, because a skin's height is a
    # property of where the square IS — a 26-ft mast that the bounds never heard
    # about is a mast with its top cropped off. Elevation counts for the same
    # reason: an island hanging twenty feet up is twenty feet of frame.
    tallest = units(max(
        (eff_height(rows[z][x], x, z) + int(elev.get(f"{x},{z}", 0) or 0)
         for z in range(h_rows) for x in range(len(rows[z]))),
        default=0))
    # A landmark's height belongs to the FRAME, not to any square. Its tiles
    # say what standing on them costs and nothing about how far up the mesh
    # goes, so a sixty-foot tree over a square of open ground contributes zero
    # here — and comes back with its crown cropped off, which is exactly what a
    # 26-ft mast did before skin heights were counted.
    for _inst in (setpieces or []):
        base = int(elev.get(f"{int(_inst.get('x') or 0)},"
                            f"{int(_inst.get('y') or 0)}", 0) or 0)
        tallest = max(tallest,
                      units(float(_inst.get("height_ft") or 0) + base))
    b = bounds_of(w_cols, h_rows, tallest)

    scale = px_per_square
    px_w = int(round((b.width + pad_squares * 2) * scale))
    px_h = int(round((b.height + pad_squares * 2) * scale))
    if max(px_w, px_h) > max_px:                            # keep SDXL-sized
        k = max_px / float(max(px_w, px_h))
        scale *= k
        px_w = int(round((b.width + pad_squares * 2) * scale))
        px_h = int(round((b.height + pad_squares * 2) * scale))
    # Multiples of 8, which is what the VAE wants.
    px_w = max(8, (px_w // 8) * 8)
    px_h = max(8, (px_h // 8) * 8)

    ox = -(b.min_x - pad_squares) * scale
    oy = -(b.min_y - pad_squares) * scale

    def to_px(p: Projected) -> tuple[float, float]:
        return (p.x * scale + ox, p.y * scale + oy)

    buf = np.full((px_h, px_w), np.inf, dtype=np.float64)
    # RGB written alongside the depth buffer, using the same z-test, so the
    # colour picture and the depth picture are the same room by construction.
    rgb = np.zeros((px_h, px_w, 3), dtype=np.float64) if _colour_of else None
    face_tint = [1.0]          # shading for the face being drawn
    struct = structure or set()
    cur_colour = [(128, 128, 128)]

    def face(corners: Sequence[tuple[float, float, float]]) -> None:
        """One planar face of any vertex count, as a fan from its first point.

        Four corners take the same two-triangle split ``_quad`` always took, so
        every board that existed before polygons rasterizes identically.
        """
        ps = [project(*c) for c in corners]
        pts = [to_px(p) for p in ps]
        zs = [p.depth for p in ps]
        for i in range(1, len(pts) - 1):
            _tri(buf, (pts[0], pts[i], pts[i + 1]), (zs[0], zs[i], zs[i + 1]),
                 rgb=rgb, colour=cur_colour[0], tint=face_tint[0])

    def shade(t: float) -> None:
        # FLAT refuses the shading, and a segmentation map needs it to. Every
        # other colour picture wants faces lit — it is what makes a wall read
        # as a wall — but a seg map's colours are CLASS IDS, and a shaded class
        # id is a different colour, which is either a different class or none
        # at all. The seg net would read a lit wall as two materials meeting.
        face_tint[0] = 1.0 if _flat else t

    def is_open(x: int, z: int) -> bool:
        """Floor a creature could stand on — not structure, not off the board.

        Void counts as closed: on an upper storey it is open air, and a wall
        should not grow a face onto a hole.
        """
        if z < 0 or z >= h_rows:
            return False
        row = rows[z]
        if x < 0 or x >= len(row):
            return False
        return row[x] not in struct and row[x] != " "

    # Far to near. Under this camera the view direction is (-x, -y, -z), so a
    # tile's distance rises with x + z; the z-buffer makes the order a
    # formality, but it keeps the traversal cache-friendly and matches how the
    # client stacks its own geometry.
    for total in range(w_cols + h_rows + 1):
        for x in range(max(0, total - h_rows + 1), min(w_cols, total + 1)):
            z = total - x
            if z < 0 or z >= h_rows or x >= len(rows[z]):
                continue
            code = rows[z][x]
            if code == " ":
                continue
            if code in HOLE_CODES:
                # A hole carries no geometry — but it is still part of the board
                # the painter is asked to fill. Left out of the coverage mask,
                # a sky board's sky would be CUT AWAY, and open sky is exactly
                # the thing that should be painted as sky. The depth map leaves
                # it at maximum distance, which is what tells the model it is
                # looking at air rather than ground.
                # ...and it is made of SOMETHING, which the colour pass has to
                # say. Left out of it too, open sky came back flat black: the
                # depth map says "maximally far", the model renders far as dark,
                # and the words alone did not outvote it. Depth says how far;
                # the terrain image says what is out there.
                if _mask_only or _colour_of:
                    if _colour_of:
                        cur_colour[0] = _colour_of(code, "")
                        shade(_cloud(x, z) if code == "^" else 1.0)
                    face([(x, 0, z), (x, 0, z + 1),
                          (x + 1, 0, z + 1), (x + 1, 0, z)])
                continue
            sk = skin_at(x, z)
            # Explicit rather than inherited: without this the floor quad below
            # is drawn with whatever tint the PREVIOUS square left behind, which
            # happened to be TOP_TINT for every ordinary square and would be a
            # patch of cloud the moment one of them was sky.
            shade(TOP_TINT)
            if _colour_of:
                cur_colour[0] = _colour_of(code, sk)
            ft = _skins.height_of(sk) or height_ft(code)
            # A little life in the heights — but never where the rules quote
            # one, and never on anything BUILT, whose parts have to meet a
            # neighbouring square's. See height_scale and Skin.exact.
            top = units(ft) * (1.0 if _skins.is_exact(sk)
                               else height_scale(code, x, z, cover_ft(code)))
            # Two ways a floor can END, and it needs a side either way or it is
            # a sheet of paper hanging in nothing.
            #
            # Against a HOLE, which is the board-wide rule and gives an island
            # its underside. Or against anything that is not the same BODY,
            # which is how a vessel gets a hull: deep water is not a hole, so a
            # sea ship used to have no sides at all — a deck lying flat on the
            # water like a raft.
            here = floor_y(x, z) or 0.0
            sk_ft, sk_in = _skins.skirt_of(sk)
            # Sides are indexed the way `footprint` winds them: west, south,
            # east, north.
            nbrs = ((x - 1, z), (x, z + 1), (x + 1, z), (x, z - 1))
            if sk_ft:
                # A VESSEL, and its side is not this square's business: the
                # hull is one traced SHELL over the whole body (see vtt.hull),
                # because joining the corners farthest from the middle needs
                # the outline as a loop and no square can see one. The floor is
                # still drawn here; the sides are drawn once, below.
                inset = 0.0
                side_ends = [False, False, False, False]
                side_drop = [here] * 4
            else:
                inset = SKIRT_INSET
                side_ends = []
                side_drop = []
                for ax, az in nbrs:
                    below = floor_y(ax, az)
                    if below is None:          # a hole, or off the board
                        side_ends.append(True)
                        side_drop.append(here - units(SKIRT_FT))
                    elif below < here - 1e-9:  # a STEP: a ledge, a quay, a deck
                        side_ends.append(True)
                        side_drop.append(below)
                    else:
                        side_ends.append(False)
                        side_drop.append(here)
            poly, edge_ends, low = footprint(
                side_ends[0], side_ends[2], side_ends[3], side_ends[1], inset)
            # Floor under everything — cut to the outline, so a hull that steps
            # a square at a time is drawn as the diagonal it should be.
            face([(x + px, here, z + pz) for px, pz in poly])
            for k, closed in enumerate(edge_ends):
                if not closed:
                    continue
                m = (k + 1) % len(poly)
                ax, az = poly[k]
                bx, bz = poly[m]
                ex, ez = bx - ax, bz - az
                run = math.sqrt(ex * ex + ez * ez)
                if run < 1e-9:
                    continue
                drop = side_drop[k]
                # Outward is the edge turned a quarter, for a ring wound
                # counter-clockwise seen from above. The bottom comes from
                # `footprint`, which MITRES it at every vertex — offsetting each
                # side along its own normal keeps a straight run coplanar and
                # opens a wedge of daylight wherever the outline turns.
                if shade:
                    shade(_tint_for(-ez / run, 0.0, ex / run))
                face([(x + ax, here, z + az), (x + low[k][0], drop, z + low[k][1]),
                      (x + low[m][0], drop, z + low[m][1]), (x + bx, here, z + bz)])
            if shade:
                shade(TOP_TINT)
            if ft <= 0:
                continue
            if _skins.is_setpiece(sk):
                # The landmark's own mesh is this square's standing geometry.
                # Drawing the tile's shape as well puts a statue inside a
                # pillar — and the floor above has already been laid, which is
                # the half that must NOT be skipped: a set piece's walkable
                # squares are real ground at a real elevation.
                continue
            sk_vars = _skins.variants_of(sk)
            if sk_vars:
                # A skin's silhouette wins over everything, INCLUDING the
                # wall-face model. That is the point: a mountainside drawn as
                # thin panels round a corridor is what made the pass read as
                # architecture. What survives from the wall model is the part
                # that pays for itself — a structure square with no open side
                # is buried, and buried rock is not drawn.
                if code in struct and not exposed(is_open, x, z):
                    continue
                pick = (variant_smooth if _skins.is_smooth(sk) else variant_of)
                parts = sk_vars[pick(x, z, len(sk_vars))]
                # "Part of the same structure" means the same skin OR a solid
                # neighbour. The second half is what lets a DOORWAY find its
                # wall: its neighbours are the tower's own masonry, which is a
                # different skin, so matching on skin alone left every door
                # facing an arbitrary way — jambs across the opening and the
                # lintel over nothing.
                # ...and a DOORWAY is part of the wall it interrupts, which is
                # already the project's rule about doors. Without it a tent's
                # canvas could take its own flap for the outdoors and pitch the
                # roof at the way in.
                def _same(ax: int, az: int, _s=sk) -> bool:
                    if _skins.same_body(skin_at(ax, az), _s):
                        return True
                    if az < 0 or az >= h_rows:
                        return False
                    row = rows[az]
                    return 0 <= ax < len(row) and (row[ax] in struct
                                                   or row[ax] in _APERTURES)
                if _skins.is_outward(sk):
                    # An outward skin does NOT roll for its arrangement: which
                    # one it wears is a fact about where the square sits.
                    # Arrangement 0 is the plain run, 1 is the CORNER — aimed
                    # at a single outside, the other side of a corner is left a
                    # sheer face, and a tent had two pitched sides and two
                    # cliffs.
                    at_corner = len(sk_vars) > 1 and out_corner(_same, x, z)
                    parts = sk_vars[1 if at_corner else 0]
                    turns = out_axis(_same, x, z, corner=at_corner)
                elif _skins.is_directional(sk):
                    turns = run_axis(_same, x, z)
                else:
                    turns = yaw_of(x, z)
                draw_parts(face, shade, parts, turns, x, z, top, here)
            elif code in struct:
                for wx0, wx1, wz0, wz1 in wall_parts(is_open, x, z):
                    _box(face, x + wx0, x + wx1, z + wz0, z + wz1,
                         here + top, y0=here, shade=shade)
            elif code in OBJECT_VARIANTS:
                # A built silhouette. Drawn as one cube these came back as DICE
                # — a crypt of thirty four-foot cubes reads as a board game
                # however loudly the prompt says "stone coffins in ranks". The
                # shape is the sentence the model actually listens to.
                vs = OBJECT_VARIANTS[code]
                parts = vs[variant_of(x, z, len(vs))]
                draw_parts(face, shade, parts, yaw_of(x, z), x, z, top, here)
            else:
                m = 0.1
                _box(face, x + m, x + 1 - m, z + m, z + 1 - m, here + top,
                     y0=here, shade=shade)

    # The landmarks. Drawn after the squares because a mesh is not a square's
    # geometry and has no place in a traversal ordered by x + z — the z-buffer
    # settles the overlap, exactly as it does for the shells below.
    for inst in (setpieces or []):
        # Resolved here rather than demanded from the caller, so ``state()``'s
        # own list can be handed straight in — the arrangement ``decor`` and
        # ``shells`` already have. The URL in the instance is for the browser;
        # the server needs the file it was copied from.
        path = inst.get("_file")
        if not path:
            from . import setpieces as _sp
            found = _sp.mesh_path(str(inst.get("slug") or ""))
            path = str(found) if found is not None else ""
        if not path:
            continue
        ix = int(inst.get("x") or 0)
        iz = int(inst.get("y") or 0)
        if shade:
            shade(TOP_TINT)
        if _colour_of:
            # The code the piece STAMPS, not the code under its origin — most
            # of a footprint is reserved ground now, so the corner square is
            # usually one the landmark never touched.
            cur_colour[0] = _colour_of(str(inst.get("code") or "#"), "")
        base = units(int(elev.get(f"{ix},{iz}", 0) or 0))
        for tri in setpiece_triangles(inst, str(path), floor_y=base):
            face(tri)

    # The vessel shells. One traced outline per hull rather than a side per
    # square — see vtt.hull for why that cannot be done a square at a time.
    for shell in (shells or []):
        loop = shell.get("loop") or []
        low = shell.get("low") or loop
        if len(loop) < 3:
            continue
        top = units(int(shell.get("top_ft") or 0))
        drop = top - units(float(shell.get("drop_ft") or 0))
        if _colour_of:
            code, _, name = str(shell.get("slot") or "").partition("@")
            cur_colour[0] = _colour_of(code or "b", name)
        # The deck out to its own hull. Each triangle is what a smoothed notch
        # gave up, so without them the planking stops short of the line the
        # hull was drawn to.
        shade(TOP_TINT)
        for tri in (shell.get("fill") or []):
            face([(p[0], top, p[1]) for p in tri])
        n = len(loop)
        for i in range(n):
            j = (i + 1) % n
            ax, az = loop[i]
            bx, bz = loop[j]
            ex, ez = bx - ax, bz - az
            run = math.sqrt(ex * ex + ez * ez)
            if run < 1e-9:
                continue
            shade(_tint_for(-ez / run, 0.0, ex / run))
            face([(ax, top, az), (low[i][0], drop, low[i][1]),
                  (low[j][0], drop, low[j][1]), (bx, top, bz)])
        shade(TOP_TINT)

    # ROOFS. One per building, traced over its whole footprint rather than a
    # gable per square — see vtt.hull.roofs. Drawn after the walls it sits on
    # and before the scenery, which is the same place in the order a shell
    # takes for the same reason.
    for roof in (roofs or []):
        eaves = roof.get("eaves") or []
        ridge = roof.get("ridge") or eaves
        if len(eaves) < 3 or len(ridge) != len(eaves):
            continue
        lo = units(float(roof.get("eaves_ft") or 0))
        hi = units(float(roof.get("ridge_ft") or 0))
        if _colour_of:
            cur_colour[0] = _colour_of("#", str(roof.get("skin") or ""))
        n = len(eaves)
        for i in range(n):
            j = (i + 1) % n
            ax, az = eaves[i]
            bx, bz = eaves[j]
            cx, cz = ridge[j]
            dx, dz = ridge[i]
            ex, ez = bx - ax, bz - az
            run = math.sqrt(ex * ex + ez * ez)
            if run < 1e-9:
                continue
            # A roof PITCH catches the light differently from a wall, which is
            # most of what tells the two apart from a camera on the ceiling.
            shade(_tint_for(-ez / run, 0.55, ex / run))
            if abs(cx - dx) < 1e-9 and abs(cz - dz) < 1e-9:
                face([(ax, lo, az), (bx, lo, bz), (cx, hi, cz)])
            else:
                face([(ax, lo, az), (bx, lo, bz), (cx, hi, cz), (dx, hi, dz)])
        # The ridge itself, so a hip is closed rather than open to the sky.
        if any(abs(ridge[i][0] - ridge[0][0]) > 1e-9
               or abs(ridge[i][1] - ridge[0][1]) > 1e-9 for i in range(n)):
            shade(TOP_TINT)
            face([(p[0], hi, p[1]) for p in ridge])

    # Scenery last: it stands ON the floor and never occludes anything the
    # rules care about, so it needs no ordering of its own.
    from .decor import DECOR_KINDS
    for d in (decor or []):
        spec = DECOR_KINDS.get(d.get("kind", ""))
        if not spec:
            continue
        ft, parts = spec[0], spec[1]
        if _colour_of:
            cur_colour[0] = _colour_of("decor:" + d.get("kind", ""), "")
        dx, dz = int(d["x"]), int(d["y"])
        draw_parts(face, shade, parts, yaw_of(dx, dz), dx, dz, units(ft),
                   floor_y(dx, dz) or 0.0)

    finite = np.isfinite(buf)
    if not finite.any():
        return b""
    if _colour_of is not None:
        from io import BytesIO
        img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), mode="RGB")
        out = BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    if _mask_only:
        img = Image.fromarray((finite * 255).astype(np.uint8), mode="L")
        from io import BytesIO
        out = BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()

    # ABSOLUTE distance, near white — and the relief experiment that replaced
    # it for a while is worth recording, because it was measured worse.
    #
    # The complaint relief was meant to fix is real: normalized across a whole
    # board, recession dominates, a ten-foot wall is a couple of percent of the
    # range, and an open board's few trees came back as sawn-off stumps.
    # Subtracting the ground plane fixes exactly that and breaks everything
    # else — with no recession left, an enclosed room stops reading as a room
    # and starts reading as a shallow TRAY. A crypt came back as a stone dish
    # of dice, a tavern as an empty picture frame. Recession is what tells the
    # model it is looking into a space rather than down at an object.
    #
    # The stump problem was only ever an OPEN-board problem, and open boards no
    # longer come here at all (see `worth_painting`). So the honest answer is
    # the simple one.
    lo, hi = buf[finite].min(), buf[finite].max()
    span = float(hi - lo) or 1.0
    norm = np.where(finite, 1.0 - (buf - lo) / span, 0.0)
    img = Image.fromarray((norm * 255).astype(np.uint8), mode="L").convert("RGB")
    from io import BytesIO
    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
