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
from typing import Optional, Sequence

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


def _tri(buf, pts, zs) -> None:
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
    np.copyto(view, z, where=inside & (z < view))


def _quad(buf, pts, zs) -> None:
    _tri(buf, (pts[0], pts[1], pts[2]), (zs[0], zs[1], zs[2]))
    _tri(buf, (pts[0], pts[2], pts[3]), (zs[0], zs[2], zs[3]))


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
OBJECT_PARTS: dict[str, tuple[tuple[float, float, float, float, float, float], ...]] = {
    # Sarcophagus / altar: a long chest with an overhanging tapered lid.
    "A": ((0.10, 0.90, 0.30, 0.70, 0.00, 0.72),
          (0.06, 0.94, 0.26, 0.74, 0.72, 1.00)),
    # Table: a top on four legs. The legs are what stop it being a block.
    "n": ((0.12, 0.22, 0.16, 0.26, 0.00, 0.72),
          (0.78, 0.88, 0.16, 0.26, 0.00, 0.72),
          (0.12, 0.22, 0.74, 0.84, 0.00, 0.72),
          (0.78, 0.88, 0.74, 0.84, 0.00, 0.72),
          (0.06, 0.94, 0.10, 0.90, 0.72, 1.00)),
    # Crates: stacked and offset, never one cube filling the square.
    "o": ((0.08, 0.58, 0.10, 0.62, 0.00, 0.62),
          (0.46, 0.92, 0.36, 0.90, 0.00, 0.48),
          (0.16, 0.56, 0.18, 0.58, 0.62, 1.00)),
    # Low wall: a coping course on a thinner base, so it reads as masonry.
    "w": ((0.18, 0.82, 0.00, 1.00, 0.00, 0.80),
          (0.10, 0.90, 0.00, 1.00, 0.80, 1.00)),
}


def _box(face, x0: float, x1: float, z0: float, z1: float,
         y1: float, y0: float = 0.0) -> None:
    """A box: top face plus the two sides this camera can see."""
    face([(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)])
    # Only +x and +z face the camera at yaw 45; the other two are never visible
    # and rasterizing them is work the z-buffer throws away.
    face([(x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (x0, y0, z1)])
    face([(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)])


def _prism(face, cx: float, cz: float, r: float, y1: float,
           y0: float = 0.0, sides: int = 8) -> None:
    """An upright n-sided prism — a pillar, or a tree's trunk and crown.

    Mirrors the shapes ``vttScene3d.ts`` builds, and that correspondence is the
    point rather than a nicety: the model paints the silhouette it is handed, so
    a square column in the depth map comes back as a square column in the
    picture however round the geometry beside it happens to be.
    """
    pts = []
    for i in range(sides + 1):
        a = (i / sides) * 2 * math.pi + math.pi / sides
        pts.append((cx + math.cos(a) * r, cz + math.sin(a) * r))
    for i in range(sides):
        (ax, az), (bx, bz) = pts[i], pts[i + 1]
        face([(cx, y1, cz), (ax, y1, az), (bx, y1, bz), (cx, y1, cz)])
        face([(ax, y0, az), (ax, y1, az), (bx, y1, bz), (bx, y0, bz)])


def depth_image(rows: Sequence[str], *, height_ft, square_ft: int = 5,
                px_per_square: int = 48, pad_squares: float = FRAME_PAD_SQUARES,
                structure: Optional[set[str]] = None,
                max_px: int = 1536) -> bytes:
    """Rasterize the board's geometry to a depth map. Returns PNG bytes.

    ``height_ft`` is a callable ``code -> feet`` (``vtt.terrain.tile_height_ft``
    in practice); ``structure`` names the codes drawn as full-square blocks. The
    shapes here must match what ``vttScene3d.ts`` builds, or the painting is
    conditioned on a room the player is not looking at.
    """
    import numpy as np
    from PIL import Image

    h_rows = len(rows)
    w_cols = max((len(r) for r in rows), default=0)
    if not h_rows or not w_cols:
        return b""

    units = lambda ft: ft / float(square_ft or 5)          # noqa: E731
    tallest = units(max((height_ft(c) for r in rows for c in r), default=0))
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
    struct = structure or set()

    def face(corners: Sequence[tuple[float, float, float]]) -> None:
        ps = [project(*c) for c in corners]
        _quad(buf, [to_px(p) for p in ps], [p.depth for p in ps])

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
            ft = height_ft(code)
            top = units(ft)
            # Floor under everything.
            face([(x, 0, z), (x, 0, z + 1), (x + 1, 0, z + 1), (x + 1, 0, z)])
            if ft <= 0:
                continue
            if code in struct:
                _box(face, x, x + 1, z, z + 1, top)
            elif code in ("O", "T"):
                # Round, because the model paints the silhouette it is handed
                # and a square column comes back as a square column.
                _prism(face, x + 0.5, z + 0.5, 0.32, top)
                if code == "T":
                    _prism(face, x + 0.5, z + 0.5, 0.46, top, y0=top * 0.4)
            elif code in OBJECT_PARTS:
                # A built silhouette. Drawn as one cube these came back as DICE
                # — a crypt of thirty four-foot cubes reads as a board game
                # however loudly the prompt says "stone coffins in ranks". The
                # shape is the sentence the model actually listens to.
                for px0, px1, pz0, pz1, py0, py1 in OBJECT_PARTS[code]:
                    _box(face, x + px0, x + px1, z + pz0, z + pz1,
                         top * py1, y0=top * py0)
            else:
                m = 0.1
                _box(face, x + m, x + 1 - m, z + m, z + 1 - m, top)

    finite = np.isfinite(buf)
    if not finite.any():
        return b""

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
