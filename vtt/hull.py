"""A vessel's SHELL: one traced outline, not a side per square.

A hull is bigger than a square and has to be drawn as one thing. Carved out of
a grid its outline is a staircase, and the first fix cut each step's outer
corner within its own square — which joins the steps into a line but a
one-square line, so the hull still read as a series of little bevels rather
than a shape. What a hull wants is the corners FARTHEST from its centre joined
directly, skipping the notches between them.

That cannot be done per square: it needs the outline as a loop. So it is done
here, once, on the server —

    * :func:`shells` traces the boundary of each vessel-bodied region,
    * drops the notch vertices that a short step leaves behind, keeping the
      triangle each one gave up so the deck can be filled out to the new line,
    * and mitres the bottom of the whole loop, which per-square geometry
      could never do because the mitre has to reach across squares.

— and shipped in ``state()``. **Both renderers draw the result rather than
recomputing it**, which is the one way to make a change like this that cannot
drift: there is no second implementation. Contrast the shape tables, which are
data and so can be generated, and the camera, which is arithmetic and so has to
be gated.

Nothing here is a rule. The shell is a drawing over squares the grid already
decided, exactly as the skin layer is, and a creature stands where the tile
code says it stands.
"""
from __future__ import annotations

import math
from typing import Callable, Optional, Sequence

from . import skins as _skins
from . import terrain as _terrain

Pt = tuple[float, float]

#: How long a segment may be and still have its notch smoothed away, in
#: squares.
#:
#: The bow of a hull tapers a square at a time and wants joining up; the waist
#: is a long straight run and is straight because the ship is. Dropping every
#: notch regardless would swing a chord across the whole waist and hang two
#: square yards of hull out over the water, which is not smoothing but
#: inventing. Two squares reaches every taper the generators produce and
#: touches nothing that is deliberately flat.
MAX_SMOOTH_RUN = 2.0


def _regions(cells: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    """Split into 4-connected groups — two ships on one board are two hulls."""
    seen: set[tuple[int, int]] = set()
    out: list[set[tuple[int, int]]] = []
    for start in sorted(cells):
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        region: set[tuple[int, int]] = set()
        while stack:
            x, z = stack.pop()
            region.add((x, z))
            for n in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
                if n in cells and n not in seen:
                    seen.add(n)
                    stack.append(n)
        out.append(region)
    return out


def _loops(cells: set[tuple[int, int]]) -> list[list[Pt]]:
    """Trace the region's boundary as loops of grid corner points.

    Each boundary edge is emitted DIRECTED so the region is on its left, which
    is the same counter-clockwise-from-above winding the floor's own top face
    uses — so the outward normal of every side is the edge turned a quarter,
    exactly as it is everywhere else on the board.
    """
    edges: dict[Pt, list[Pt]] = {}

    def add(a: Pt, b: Pt) -> None:
        edges.setdefault(a, []).append(b)

    for (x, z) in cells:
        if (x - 1, z) not in cells:
            add((x, z), (x, z + 1))
        if (x, z + 1) not in cells:
            add((x, z + 1), (x + 1, z + 1))
        if (x + 1, z) not in cells:
            add((x + 1, z + 1), (x + 1, z))
        if (x, z - 1) not in cells:
            add((x + 1, z), (x, z))

    def turn_rank(d: Pt, e: Pt) -> int:
        """Prefer the tightest turn back around the region.

        A boundary can PINCH — two parts of the same hull meeting at one grid
        corner — and there the walker has two ways to go. Taking whichever came
        to hand split the outline into fragments: a skyship traced as one big
        loop plus five stray squares, and the stray ones drew their own little
        hull sides in the middle of the deck. Always turning as tightly as
        possible toward the interior keeps the walk on the outline it started
        on, which is the standard fix and the only one that is not luck.
        """
        cross = d[0] * e[1] - d[1] * e[0]
        if cross < 0:
            return 0                       # toward the region
        if e == d:
            return 1                       # straight on
        if cross > 0:
            return 2                       # away from it
        return 3                           # doubling back

    loops: list[list[Pt]] = []
    while edges:
        start = min(edges)
        loop = [start]
        cur = start
        d: Optional[Pt] = None
        for _ in range(len(cells) * 4 + 8):        # a hard bound, not a hope
            nxts = edges.get(cur)
            if not nxts:
                break
            if d is None or len(nxts) == 1:
                nxt = nxts[0]
            else:
                nxt = min(nxts, key=lambda p: turn_rank(
                    d, (p[0] - cur[0], p[1] - cur[1])))
            nxts.remove(nxt)
            if not nxts:
                edges.pop(cur, None)
            d = (nxt[0] - cur[0], nxt[1] - cur[1])
            if nxt == start:
                break
            loop.append(nxt)
            cur = nxt
        if len(loop) >= 4:
            loops.append(loop)
    return loops


def _area(loop: Sequence[Pt]) -> float:
    n = len(loop)
    return sum(loop[i][0] * loop[(i + 1) % n][1] - loop[(i + 1) % n][0] * loop[i][1]
               for i in range(n)) / 2.0


def _simplify(loop: Sequence[Pt]) -> list[Pt]:
    """Drop points that lie on a straight run between their neighbours."""
    n = len(loop)
    out = [p for i, p in enumerate(loop)
           if (p[0] - loop[i - 1][0]) * (loop[(i + 1) % n][1] - p[1])
           != (p[1] - loop[i - 1][1]) * (loop[(i + 1) % n][0] - p[0])]
    return out if len(out) >= 3 else list(loop)


def _smooth(loop: Sequence[Pt]) -> tuple[list[Pt], list[tuple[Pt, Pt, Pt]]]:
    """Join the corners farthest from the middle; return what was given up.

    A staircase alternates a corner that sticks OUT and a notch that steps back
    in. Dropping the notches connects the outer corners directly, which is what
    turns a flight of steps into one slanted line. Each dropped notch leaves a
    triangle of deck outside the new line — returned rather than discarded, so
    the deck can be filled out to meet its own hull instead of stopping short
    of it.
    """
    n = len(loop)
    if n < 4:
        return list(loop), []
    sign = -1.0 if _area(loop) < 0 else 1.0
    keep: list[Pt] = []
    fill: list[tuple[Pt, Pt, Pt]] = []
    for i in range(n):
        a, b, c = loop[i - 1], loop[i], loop[(i + 1) % n]
        ax, az = b[0] - a[0], b[1] - a[1]
        bx, bz = c[0] - b[0], c[1] - b[1]
        cross = ax * bz - az * bx
        sticks_out = cross * sign > 0
        short = (max(abs(ax), abs(az)) <= MAX_SMOOTH_RUN
                 and max(abs(bx), abs(bz)) <= MAX_SMOOTH_RUN)
        if cross and not sticks_out and short:
            fill.append((a, b, c))
            continue
        keep.append(b)
    return (keep, fill) if len(keep) >= 3 else (list(loop), [])


def shells(rows: Sequence[str], skin_of: Optional[Callable] = None,
           elevation: Optional[dict] = None) -> list[dict]:
    """Every vessel shell on this board, ready to draw.

    ``skin_of(code, x, z) -> name``, as everywhere else. A square joins a shell
    when its skin belongs to a BODY and declares its own side; the whole ship —
    deck, rail, mast and cabin — is one body, so the shell traces the vessel
    and not the deck planking inside it.
    """
    if skin_of is None:
        return []
    elev = elevation or {}
    by_body: dict[str, set[tuple[int, int]]] = {}
    for z, row in enumerate(rows):
        for x, code in enumerate(row):
            name = skin_of(code, x, z) or ""
            body = _skins.body_of(name)
            if body and _skins.skirt_of(name)[0]:
                by_body.setdefault(body, set()).add((x, z))

    from .isocam import _outline_bottoms

    out: list[dict] = []
    for body, cells in sorted(by_body.items()):
        for region in _regions(cells):
            deep, inset, slot, top_ft = 0.0, 0.0, "", 0
            tally: dict[str, int] = {}
            for (x, z) in region:
                name = skin_of(rows[z][x], x, z) or ""
                ft, ins = _skins.skirt_of(name)
                if ft > deep:
                    deep, inset = ft, ins
                tally[f"{rows[z][x]}@{name}"] = tally.get(f"{rows[z][x]}@{name}", 0) + 1
                top_ft = max(top_ft, int(elev.get(f"{x},{z}", 0) or 0))
            slot = max(tally, key=lambda k: tally[k]) if tally else ""
            # Only the OUTER boundary. A vessel's body is full of holes — the
            # mast's square, the cabin's, every crate lashed on deck — because
            # each of those replaced the deck's tile with its own and so left
            # the region. Traced without this, a skyship came back as one hull
            # plus five little hulls sunk into its own planking.
            loops = _loops(region)
            if not loops:
                continue
            for loop in [max(loops, key=lambda lp: abs(_area(lp)))]:
                pts, fill = _smooth(_simplify(loop))
                if len(pts) < 3:
                    continue
                low = _outline_bottoms(pts, [True] * len(pts), inset)
                out.append({
                    "body": body, "slot": slot,
                    "top_ft": top_ft, "drop_ft": deep,
                    "loop": [[p[0], p[1]] for p in pts],
                    "low": [[p[0], p[1]] for p in low],
                    "fill": [[[a[0], a[1]], [b[0], b[1]], [c[0], c[1]]]
                             for a, b, c in fill],
                })
    return out


# --------------------------------------------------------------------------
# Roofs
#
# The same argument as a hull, arriving from the other direction. A roof is
# bigger than a square, so it cannot be drawn a square at a time — and drawing
# it a square at a time is exactly what the board did: the ``townhouse`` skin
# carried a gable per square, so a terrace of houses came out a SAWTOOTH of
# one-square huts, twelve little ridges over what the prompt calls "close-packed
# two-storey townhouses". No amount of shape authoring inside one square fixes
# that, because the thing that is wrong is the size of the unit.
#
# So a building is traced like a vessel, and one roof is put on it. Everything
# the hull tracer learned applies unchanged: only the OUTER loop (a building's
# footprint is full of holes where a doorway or a chimney replaced its tile),
# the notch vertices dropped so a staircase outline becomes a line, and the
# whole thing computed HERE and shipped, because an algorithm over the board is
# the one kind of geometry two languages cannot be trusted to agree about.
# --------------------------------------------------------------------------

#: How far a roof's eaves overhang the wall below, in squares. Small, and
#: load-bearing for the same reason ``HULL_TAPER`` is: an eave flush with its
#: wall is a flat top, and the shadow line under an overhang is most of what
#: says "roof" from above.
ROOF_EAVES = 0.12


def _offset_loop(loop: Sequence[Pt], d: float) -> list[Pt]:
    """The loop moved by ``d`` — inward when positive, out when negative.

    A true hipped roof's ridge is the straight skeleton of its footprint, which
    is a real piece of computational geometry and far more than this needs. A
    uniform offset is the same thing for any rectangle — which nearly every
    building footprint is — and close enough for the rest.

    Two things it has to get right, and the first version got both wrong.

    **The corner factor is ``d / |bisector|²``, not ``d / |bisector|``.** The
    average of two unit normals is SHORTER than either (its length is the
    cosine of the half-angle), so a corner offset along it has to travel
    ``d / cos`` to put both edges at ``d`` — and scaling a vector of length
    ``cos`` to length ``d / cos`` is a factor of ``d / cos²``. Getting that
    wrong by the one factor left a two-square-wide terrace with a ridge half a
    square across instead of a ridge LINE, which is a flat-topped slab.

    **A polygon collapsed to a line is the ANSWER, not a failure.** That is
    exactly what a ridge is over a building narrower than twice ``d``. What
    must be rejected is an offset that has passed THROUGH the middle and turned
    itself inside out, which is a different thing and is told apart by asking
    whether any edge now runs backwards. When one does, the offset is halved
    and tried again — so a footprint with an awkward notch gets a shallower
    roof rather than a knot.
    """
    n = len(loop)
    if n < 3 or d == 0:
        return list(loop)
    sign = -1.0 if _area(loop) < 0 else 1.0
    for _ in range(7):
        out: list[Pt] = []
        for i in range(n):
            a, b, c = loop[i - 1], loop[i], loop[(i + 1) % n]
            norms = []
            for (px, pz), (qx, qz) in ((a, b), (b, c)):
                ex, ez = qx - px, qz - pz
                ln = math.hypot(ex, ez)
                if ln:
                    norms.append((-ez / ln * sign, ex / ln * sign))
            if not norms:
                out.append(b)
                continue
            mx = sum(v[0] for v in norms) / len(norms)
            mz = sum(v[1] for v in norms) / len(norms)
            ln2 = mx * mx + mz * mz
            k = d / ln2 if ln2 > 1e-9 else 0.0
            out.append((b[0] + mx * k, b[1] + mz * k))
        # Inside out? An edge that now runs the other way means the offset has
        # crossed the middle. A DEGENERATE edge (zero length) is fine: that is
        # two corners meeting, which is what a hip does.
        flipped = False
        for i in range(n):
            j = (i + 1) % n
            ox, oz = loop[j][0] - loop[i][0], loop[j][1] - loop[i][1]
            nx, nz = out[j][0] - out[i][0], out[j][1] - out[i][1]
            if ox * nx + oz * nz < -1e-9:
                flipped = True
                break
        if not flipped:
            return out
        d *= 0.5
    return list(loop)


def _inside(loop: Sequence[Sequence[float]], px: float, pz: float) -> bool:
    """Ray casting, the standard way. Used to ask what a roof covers."""
    hit = False
    n = len(loop)
    for i in range(n):
        x1, z1 = loop[i][0], loop[i][1]
        x2, z2 = loop[(i + 1) % n][0], loop[(i + 1) % n][1]
        if (z1 > pz) != (z2 > pz):
            cut = x1 + (pz - z1) * (x2 - x1) / ((z2 - z1) or 1e-12)
            if px < cut:
                hit = not hit
    return hit


def _encloses_floor(rows: Sequence[str], loop: Sequence[Sequence[float]]) -> bool:
    """Is there a square under this outline a creature could stand on?"""
    xs = [p[0] for p in loop]
    zs = [p[1] for p in loop]
    for z in range(max(0, int(min(zs))), min(len(rows), int(max(zs)) + 1)):
        row = rows[z]
        for x in range(max(0, int(min(xs))), min(len(row), int(max(xs)) + 1)):
            if (_terrain.code_cost(row[x], "walk") is not None
                    and _inside(loop, x + 0.5, z + 0.5)):
                return True
    return False


def roofs(rows: Sequence[str], skin_of: Optional[Callable] = None,
          elevation: Optional[dict] = None,
          footprints: Optional[Sequence[dict]] = None) -> list[dict]:
    """One roof per BUILDING on this board, traced from its footprint.

    A square joins a building when its skin declares ``roof_ft`` — how far
    above the square's own drawn height the ridge stands.

    ``footprints`` is how a generator says where one house ENDS and the next
    begins. Without it, contiguous squares of the same skin are one building —
    which is right for a lone hut and wrong for a terrace, where every house
    wears the same plaster and the whole row came back under one roof. A run of
    separate houses under one roof is a warehouse, and the party wall between
    two of them is invisible from above unless somebody says it is there.
    """
    if skin_of is None:
        return []
    elev = elevation or {}
    by_skin: dict[str, set[tuple[int, int]]] = {}
    if footprints:
        for i, fp in enumerate(footprints):
            cells = {(x, z)
                     for x in range(int(fp["x"]), int(fp["x"]) + int(fp["w"]))
                     for z in range(int(fp["y"]), int(fp["y"]) + int(fp["h"]))
                     if 0 <= z < len(rows) and 0 <= x < len(rows[z])}
            named = {skin_of(rows[z][x], x, z) or "" for x, z in cells}
            roofed = [n for n in named
                      if getattr(_skins.skin(n), "roof_ft", 0)]
            if cells and roofed:
                # Keyed so each house is its own group even though every one
                # of them wears the same skin.
                by_skin[f"{roofed[0]}#{i}"] = cells
    else:
        for z, row in enumerate(rows):
            for x, code in enumerate(row):
                name = skin_of(code, x, z) or ""
                sk = _skins.skin(name)
                if sk is not None and getattr(sk, "roof_ft", 0):
                    by_skin.setdefault(name, set()).add((x, z))

    out: list[dict] = []
    for key, cells in sorted(by_skin.items()):
        name = key.split("#")[0]
        sk = _skins.skin(name)
        if sk is None:
            continue
        for region in _regions(cells):
            loops = _loops(region)
            if not loops:
                continue
            loop = max(loops, key=lambda lp: abs(_area(lp)))
            pts, _fill = _smooth(_simplify(loop))
            if len(pts) < 3:
                continue
            base = min(int(elev.get(f"{x},{z}", 0) or 0) for x, z in region)
            # The eaves stand a little proud of the wall, and the ridge is set
            # in by half the SHORT dimension — a hip. Measured off the traced
            # outline rather than the bounding box, so an L-shaped block gets a
            # roof that follows it.
            xs = [p[0] for p in pts]
            zs = [p[1] for p in pts]
            short = max(1.0, min(max(xs) - min(xs), max(zs) - min(zs)))
            # Eaves OUT, ridge IN. The ridge is set in by half the short
            # dimension, which is what a hip is: over a building narrower than
            # its own inset the offset collapses to a line, and that line IS
            # the ridge. Over a square one it collapses to a point, and that is
            # a pyramid — both are the right answer rather than a failure.
            eaves = _offset_loop(pts, -ROOF_EAVES)
            ridge = _offset_loop(eaves, short / 2.0 + ROOF_EAVES)
            if len(ridge) != len(eaves):
                continue
            # WINDING IS NORMALIZED HERE, NEVER TRUSTED — the `skins.solid`
            # rule, and it bites twice as hard on shipped geometry. Every face
            # of a pitch takes its normal from the order of its vertices, so a
            # loop traced the other way round shades the near pitch as though
            # it faced away and, in the browser, culls the roof outright: the
            # building simply has no top and nothing in either program looks
            # broken. Counter-clockwise seen from above, which is negative
            # under this shoelace because z grows southward.
            if _area(eaves) > 0:
                eaves = list(reversed(eaves))
                ridge = list(reversed(ridge))
            out.append({
                "skin": name,
                # Is there anywhere UNDER this roof a creature could stand?
                # A roof over a solid block is a cap and belongs on the board;
                # a roof over a ROOM is a lid, and the renderer takes it off so
                # that the cutaway which already took the near walls down is
                # worth something. Answered here because this is where the
                # building is known — the browser has a traced outline and no
                # way back to what is under it.
                #
                # Asked of what the OUTLINE ENCLOSES, not of the region's own
                # squares. Without `footprints` a region is the contiguous run
                # of roofed skin — the wall RING and nothing else — so a house
                # would report its own masonry and never the room inside it,
                # and every lone hut would keep its lid. With footprints the
                # two agree, which is why this was not caught by looking.
                "hollow": _encloses_floor(rows, eaves),
                # The material this roof wears, when it is not the building's.
                # `slot` has been in this record since roofs were traced and
                # nothing ever filled it; the client falls back to the wall's
                # material when it is empty, so a skin that declares no roof
                # material is drawn exactly as before.
                "slot": (f"#@{sk.roof_skin}" if getattr(sk, "roof_skin", "")
                         else ""),
                "eaves_ft": base + float(sk.height_ft or 0) * float(sk.roof_at),
                "ridge_ft": base + float(sk.height_ft or 0) * float(sk.roof_at)
                            + float(sk.roof_ft),
                "eaves": [[p[0], p[1]] for p in eaves],
                "ridge": [[p[0], p[1]] for p in ridge],
            })
    return out
