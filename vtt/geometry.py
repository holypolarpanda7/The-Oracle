"""
Square-grid geometry — the rules-facing math of the tactical board.

Everything here is pure: it takes a :class:`vtt.terrain.Grid` plus plain tuples
and returns squares, feet, or a cover string. No database, no randomness, so it
can be unit-tested and reused by the UI's preview logic through the API.

What it answers:

``distance``          how far apart two creatures are (5-5-5 or the 5-10-5 variant)
``path``/``reachable``  where a speed budget can actually take you, around walls
                        and through difficult terrain
``line_of_sight``     can A see B
``cover_between``     the PHB corner rule — none / half / three-quarters / total
``area_squares``      spell templates (sphere, cone, line, cube, emanation) as
                      the exact set of squares they cover
``visible_squares``   field of view, for fog of war

Coordinates are square indices ``(x, y)`` with the origin top-left. Corner
coordinates (used for cover rays) are the *lattice* points, so square ``(x, y)``
has corners ``(x, y) .. (x+1, y+1)``.
"""
from __future__ import annotations

import heapq
import math
from typing import Callable, Iterable, Optional, Sequence

from .terrain import Grid

Square = tuple[int, int]
Point = tuple[float, float]

# Diagonal accounting rules.
CHEBYSHEV = "chebyshev"      # PHB default: every diagonal costs 5 ft
ALTERNATING = "alternating"  # DMG variant: diagonals cost 5, 10, 5, 10 ...
EUCLIDEAN = "euclidean"

COVER_ORDER = {"none": 0, "half": 1, "three-quarters": 2, "total": 3}
COVER_BY_RANK = {v: k for k, v in COVER_ORDER.items()}

_DIRS8: tuple[Square, ...] = (
    (1, 0), (-1, 0), (0, 1), (0, -1),
    (1, 1), (1, -1), (-1, 1), (-1, -1),
)


# ---------------------------------------------------------------- footprints

def footprint(x: int, y: int, squares_per_side: int = 1) -> list[Square]:
    """Every square a token of this size occupies, anchored at its top-left."""
    n = max(1, int(squares_per_side))
    return [(x + dx, y + dy) for dy in range(n) for dx in range(n)]


def center(sq: Square) -> Point:
    """The centre point of a square, in square units."""
    return (sq[0] + 0.5, sq[1] + 0.5)


def footprint_center(x: int, y: int, n: int = 1) -> Point:
    n = max(1, int(n))
    return (x + n / 2.0, y + n / 2.0)


def corners(sq: Square) -> list[Point]:
    x, y = sq
    return [(x, y), (x + 1.0, y), (x, y + 1.0), (x + 1.0, y + 1.0)]


def footprint_corners(x: int, y: int, n: int = 1) -> list[Point]:
    n = max(1, int(n))
    return [(x, y), (x + n, y), (x, y + n), (x + n, y + n)]


#: Corner rays are pulled this far inside their own square before being traced.
#: Exactly-on-the-corner rays slide along wall faces and would let a creature
#: see straight through a solid wall; a small inset keeps grazing honest while
#: still allowing genuine sight lines past a corner.
_INSET = 0.12


def sight_points(x: int, y: int, n: int = 1) -> list[Point]:
    """The centre plus four inset corners — the rays a token sees along."""
    n = max(1, int(n))
    i = _INSET
    return [
        (x + n / 2.0, y + n / 2.0),
        (x + i, y + i), (x + n - i, y + i),
        (x + i, y + n - i), (x + n - i, y + n - i),
    ]


def cover_corners(x: int, y: int, n: int = 1) -> list[Point]:
    """The four corners used by the PHB cover rule (inset, same reasoning)."""
    n = max(1, int(n))
    i = _INSET
    return [(x + i, y + i), (x + n - i, y + i),
            (x + i, y + n - i), (x + n - i, y + n - i)]


# ----------------------------------------------------------------- distance

def distance_squares(a: Square, b: Square, rule: str = CHEBYSHEV) -> float:
    dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
    if rule == EUCLIDEAN:
        return math.hypot(dx, dy)
    if rule == ALTERNATING:
        diag, straight = min(dx, dy), abs(dx - dy)
        # 5-10-5: every second diagonal costs double.
        return straight + diag + diag // 2
    return max(dx, dy)


def distance_ft(a: Square, b: Square, square_ft: int = 5,
                rule: str = CHEBYSHEV) -> int:
    return int(round(distance_squares(a, b, rule) * square_ft))


def token_distance_ft(a: Sequence[Square], b: Sequence[Square],
                      square_ft: int = 5, rule: str = CHEBYSHEV) -> int:
    """Distance between two *footprints* — the nearest pair of squares wins.

    Adjacent medium creatures come out at 5 ft, which is what "in reach" means.
    """
    if not a or not b:
        return 0
    best = min(distance_squares(p, q, rule) for p in a for q in b)
    return int(round(best * square_ft))


def in_reach(a: Sequence[Square], b: Sequence[Square], reach_ft: int = 5,
             square_ft: int = 5, rule: str = CHEBYSHEV) -> bool:
    return token_distance_ft(a, b, square_ft, rule) <= max(0, reach_ft)


# ------------------------------------------------------------------ raycast

def bresenham(a: Square, b: Square) -> list[Square]:
    """Integer line between two squares (inclusive of both ends)."""
    x0, y0 = a
    x1, y1 = b
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    out: list[Square] = []
    while True:
        out.append((x0, y0))
        if (x0, y0) == (x1, y1):
            return out
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x0 += sx
        if e2 < dx:
            err += dx
            y0 += sy


_EPS = 1e-6


def _segment_hits_square(p0: Point, p1: Point, sq: Square,
                         shrink: float = 0.02) -> bool:
    """Does the segment pass *through* a square (not merely graze its edge)?

    The square's box is shrunk slightly so a ray running exactly along a wall
    face isn't counted as blocked — otherwise every shot down a corridor would
    read as obstructed.
    """
    x, y = sq
    lo_x, hi_x = x + shrink, x + 1 - shrink
    lo_y, hi_y = y + shrink, y + 1 - shrink
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    t0, t1 = 0.0, 1.0
    for delta, origin, lo, hi in ((dx, p0[0], lo_x, hi_x),
                                  (dy, p0[1], lo_y, hi_y)):
        if abs(delta) < _EPS:
            if origin < lo or origin > hi:
                return False
            continue
        ta = (lo - origin) / delta
        tb = (hi - origin) / delta
        if ta > tb:
            ta, tb = tb, ta
        t0 = max(t0, ta)
        t1 = min(t1, tb)
        if t0 > t1:
            return False
    return t1 > t0 + _EPS


def _squares_along(p0: Point, p1: Point) -> list[Square]:
    """Candidate squares a segment might touch (bounding walk, cheap)."""
    x0, y0 = int(math.floor(min(p0[0], p1[0]))), int(math.floor(min(p0[1], p1[1])))
    x1, y1 = int(math.ceil(max(p0[0], p1[0]))), int(math.ceil(max(p0[1], p1[1])))
    return [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


def ray_blocked(grid: Grid, p0: Point, p1: Point, *,
                blocker: Optional[Callable[[int, int], bool]] = None,
                ignore: Iterable[Square] = ()) -> bool:
    """True if any sight-blocking square lies between two points."""
    skip = set(ignore)
    is_blocker = blocker or (lambda x, y: grid.blocks_sight(x, y))
    for sq in _squares_along(p0, p1):
        if sq in skip:
            continue
        if not grid.in_bounds(*sq):
            continue
        if not is_blocker(sq[0], sq[1]):
            continue
        if _segment_hits_square(p0, p1, sq):
            return True
    return False


def has_line_of_sight(grid: Grid, a: Square, b: Square, *,
                      a_size: int = 1, b_size: int = 1,
                      blocker: Optional[Callable[[int, int], bool]] = None) -> bool:
    """Can a creature at ``a`` see one at ``b``?

    Permissive corner rule: sight exists when *any* ray from the source's centre
    or corners reaches the target's centre or corners unobstructed.
    """
    src_pts = sight_points(a[0], a[1], a_size)
    dst_pts = sight_points(b[0], b[1], b_size)
    ignore = set(footprint(a[0], a[1], a_size)) | set(footprint(b[0], b[1], b_size))
    for sp in src_pts:
        for dp in dst_pts:
            if not ray_blocked(grid, sp, dp, blocker=blocker, ignore=ignore):
                return True
    return False


def line_of_effect(grid: Grid, origin: Square, target: Square) -> bool:
    """Can a spell's energy reach a square (walls stop it; a creature doesn't)?"""
    return not ray_blocked(
        grid, center(origin), center(target),
        blocker=lambda x, y: grid.cost(x, y) is None and grid.blocks_sight(x, y),
        ignore={origin, target},
    )


# -------------------------------------------------------------------- cover

def cover_between(grid: Grid, attacker: Square, target: Square, *,
                  attacker_size: int = 1, target_size: int = 1,
                  obstacles: Optional[dict[Square, str]] = None) -> str:
    """The PHB corner rule, best-case for the attacker.

    Pick a corner of the attacker's space, trace to each of the target's four
    corners: 1-2 lines blocked = half cover, 3 = three-quarters, 4 = total
    (can't be targeted). Squares whose *tile* grants cover (a low wall, a crate)
    and any ``obstacles`` (other creatures, usually half cover) are counted with
    their own rating, so ducking behind a barrel gives half even though the ray
    technically passes.
    """
    obstacles = obstacles or {}
    own = set(footprint(attacker[0], attacker[1], attacker_size))
    theirs = set(footprint(target[0], target[1], target_size))
    ignore = own | theirs

    def rating(x: int, y: int) -> str:
        if not grid.in_bounds(x, y):
            return "none"
        r = grid.cover_at(x, y)
        o = obstacles.get((x, y), "none")
        return COVER_BY_RANK[max(COVER_ORDER.get(r, 0), COVER_ORDER.get(o, 0))]

    best = "total"
    for src in cover_corners(attacker[0], attacker[1], attacker_size):
        blocked = 0
        partial = 0
        for dst in cover_corners(target[0], target[1], target_size):
            worst = 0
            for sq in _squares_along(src, dst):
                if sq in ignore or not grid.in_bounds(*sq):
                    continue
                rank = COVER_ORDER.get(rating(sq[0], sq[1]), 0)
                if rank == 0:
                    continue
                if _segment_hits_square(src, dst, sq):
                    worst = max(worst, rank)
            if worst >= COVER_ORDER["total"]:
                blocked += 1
            else:
                partial = max(partial, worst)
        if blocked >= 4:
            result = "total"
        elif blocked == 3:
            result = "three-quarters"
        elif blocked >= 1:
            result = "half"
        else:
            result = COVER_BY_RANK[partial]
        if COVER_ORDER[result] < COVER_ORDER[best]:
            best = result
        if best == "none":
            break
    return best


# --------------------------------------------------------------- templates

# A cone or line originates at the *edge* of the caster's space, never its
# middle — nobody is caught in their own breath weapon. Measuring from square
# centres, that means the template starts half a square out.
_ORIGIN_OFFSET = 0.5


def _cone_contains(origin: Point, pt: Point, length: float,
                   direction_deg: float) -> bool:
    """A 5e cone: at distance d from the apex it is d wide, so half-width d/2."""
    rad = math.radians(direction_deg)
    ux, uy = math.cos(rad), math.sin(rad)
    dx, dy = pt[0] - origin[0], pt[1] - origin[1]
    along = dx * ux + dy * uy
    across = abs(-dx * uy + dy * ux)
    if along < _ORIGIN_OFFSET - 0.001 or along > length + 0.001:
        return False
    return across <= along / 2.0 + 0.001


def _line_contains(origin: Point, pt: Point, length: float, width: float,
                   direction_deg: float) -> bool:
    rad = math.radians(direction_deg)
    ux, uy = math.cos(rad), math.sin(rad)
    dx, dy = pt[0] - origin[0], pt[1] - origin[1]
    along = dx * ux + dy * uy
    across = abs(-dx * uy + dy * ux)
    return (_ORIGIN_OFFSET - 0.001 <= along <= length + 0.001
            and across <= max(0.5, width / 2.0) - 0.001)


def area_squares(shape: str, origin: Square, *, radius_ft: int = 0,
                 length_ft: int = 0, width_ft: int = 5,
                 direction_deg: float = 0.0, square_ft: int = 5,
                 origin_size: int = 1, grid: Optional[Grid] = None,
                 respect_walls: bool = True,
                 path: Optional[Sequence[Square]] = None) -> list[Square]:
    """Resolve a template into the squares it covers.

    A square is in the area when its centre is inside the shape — the common
    VTT convention, and the one the players can eyeball from the overlay. When
    ``grid`` is given and ``respect_walls`` is on, squares with no line of
    effect from the origin are dropped, so a fireball doesn't leak through a
    wall.
    """
    shape = (shape or "sphere").strip().lower()
    if shape == "path":
        out = [tuple(p) for p in (path or [])]  # type: ignore[misc]
        return [s for s in out if grid is None or grid.in_bounds(*s)]

    ox, oy = origin
    o_pt = footprint_center(ox, oy, origin_size)
    r_sq = radius_ft / float(square_ft)
    l_sq = length_ft / float(square_ft)
    w_sq = width_ft / float(square_ft)

    reach = max(r_sq, l_sq, w_sq) + origin_size + 1
    lo_x, hi_x = int(math.floor(ox - reach)), int(math.ceil(ox + reach))
    lo_y, hi_y = int(math.floor(oy - reach)), int(math.ceil(oy + reach))
    if grid is not None:
        lo_x, lo_y = max(0, lo_x), max(0, lo_y)
        hi_x, hi_y = min(grid.width - 1, hi_x), min(grid.height - 1, hi_y)

    hits: list[Square] = []
    for y in range(lo_y, hi_y + 1):
        for x in range(lo_x, hi_x + 1):
            c = center((x, y))
            if shape in ("sphere", "circle", "cylinder"):
                ok = math.hypot(c[0] - o_pt[0], c[1] - o_pt[1]) <= r_sq + 0.001
            elif shape == "emanation":
                ok = min(
                    max(abs(x - sx), abs(y - sy))
                    for sx, sy in footprint(ox, oy, origin_size)
                ) <= r_sq + 0.001
            elif shape == "cone":
                ok = _cone_contains(o_pt, c, l_sq or r_sq, direction_deg)
            elif shape == "line":
                ok = _line_contains(o_pt, c, l_sq, w_sq, direction_deg)
            elif shape in ("cube", "square"):
                side = (l_sq or r_sq or w_sq)
                half = side / 2.0
                ok = abs(c[0] - o_pt[0]) <= half + 0.001 and abs(c[1] - o_pt[1]) <= half + 0.001
            else:
                ok = math.hypot(c[0] - o_pt[0], c[1] - o_pt[1]) <= r_sq + 0.001
            if ok:
                hits.append((x, y))

    if grid is not None and respect_walls:
        hits = [s for s in hits if s == origin or line_of_effect(grid, origin, s)]
    return hits


def visible_squares(grid: Grid, origin: Square, radius_ft: int, *,
                    square_ft: int = 5, origin_size: int = 1) -> list[Square]:
    """Field of view from a square — every square within radius it can see."""
    r = int(math.ceil(radius_ft / float(square_ft)))
    out: list[Square] = []
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            x, y = origin[0] + dx, origin[1] + dy
            if not grid.in_bounds(x, y):
                continue
            if max(abs(dx), abs(dy)) > r:
                continue
            if has_line_of_sight(grid, origin, (x, y), a_size=origin_size):
                out.append((x, y))
    return out


# ------------------------------------------------------------- movement

def _step_cost(grid: Grid, frm: Square, to: Square, *, mode: str,
               diagonal_rule: str, diag_count: int,
               extra_cost: Optional[Callable[[int, int], int]] = None,
               square_ft: int = 5,
               allow_corner_cutting: bool = False) -> Optional[int]:
    """Feet to step between adjacent squares, or ``None`` if illegal."""
    base = grid.cost(to[0], to[1], mode=mode)
    if base is None:
        return None
    dx, dy = to[0] - frm[0], to[1] - frm[1]
    if dx and dy and not allow_corner_cutting:
        # No slipping diagonally between two walls / around a corner.
        if grid.cost(frm[0] + dx, frm[1], mode=mode) is None and \
           grid.cost(frm[0], frm[1] + dy, mode=mode) is None:
            return None
    cost = base
    if extra_cost is not None:
        cost += max(0, int(extra_cost(to[0], to[1])))
    if dx and dy and diagonal_rule == ALTERNATING and diag_count % 2 == 1:
        cost += square_ft
    return cost


def reachable_costs(grid: Grid, start: Square, budget_ft: int, *,
                    size: int = 1, mode: str = "walk",
                    diagonal_rule: str = CHEBYSHEV,
                    blocked: Optional[set[Square]] = None,
                    soft_blocked: Optional[set[Square]] = None,
                    extra_cost: Optional[Callable[[int, int], int]] = None,
                    square_ft: int = 5) -> dict[Square, int]:
    """Dijkstra flood-fill: every square reachable within a movement budget.

    ``blocked`` squares can't be entered at all (enemies, closed doors).
    ``soft_blocked`` squares can be moved *through* but not stopped in (allies) —
    they're returned with their cost but flagged by the caller as no-stop.
    Returns ``{square: feet spent}`` including the start at 0.
    """
    blocked = blocked or set()
    soft_blocked = soft_blocked or set()
    budget_ft = max(0, int(budget_ft))
    best: dict[tuple[Square, int], int] = {(start, 0): 0}
    out: dict[Square, int] = {start: 0}
    heap: list[tuple[int, Square, int]] = [(0, start, 0)]
    while heap:
        cost, sq, diag = heapq.heappop(heap)
        if cost > best.get((sq, diag), 1 << 30):
            continue
        for dx, dy in _DIRS8:
            nxt = (sq[0] + dx, sq[1] + dy)
            if not _fits(grid, nxt, size, mode=mode, blocked=blocked):
                continue
            step = _step_cost(grid, sq, nxt, mode=mode,
                              diagonal_rule=diagonal_rule, diag_count=diag,
                              extra_cost=extra_cost, square_ft=square_ft)
            if step is None:
                continue
            total = cost + step
            if total > budget_ft:
                continue
            ndiag = diag + (1 if dx and dy else 0)
            key = (nxt, ndiag % 2)
            if total < best.get(key, 1 << 30):
                best[key] = total
                if total < out.get(nxt, 1 << 30):
                    out[nxt] = total
                heapq.heappush(heap, (total, nxt, ndiag % 2))
    return out


def _fits(grid: Grid, sq: Square, size: int, *, mode: str,
          blocked: set[Square]) -> bool:
    """Can a token of this size stand with its top-left at ``sq``?"""
    for p in footprint(sq[0], sq[1], size):
        if not grid.in_bounds(*p):
            return False
        if grid.cost(p[0], p[1], mode=mode) is None:
            return False
        if p in blocked:
            return False
    return True


def find_path(grid: Grid, start: Square, goal: Square, *, size: int = 1,
              mode: str = "walk", diagonal_rule: str = CHEBYSHEV,
              blocked: Optional[set[Square]] = None,
              extra_cost: Optional[Callable[[int, int], int]] = None,
              square_ft: int = 5,
              max_ft: Optional[int] = None) -> tuple[list[Square], int]:
    """A* from ``start`` to ``goal``. Returns ``(path, feet)``; ``([], 0)`` when
    no legal route exists (or none within ``max_ft``)."""
    blocked = blocked or set()
    if start == goal:
        return [start], 0
    if not _fits(grid, goal, size, mode=mode, blocked=blocked):
        return [], 0

    def h(sq: Square) -> int:
        return int(distance_squares(sq, goal, CHEBYSHEV) * square_ft)

    heap: list[tuple[int, int, Square, int]] = [(h(start), 0, start, 0)]
    came: dict[tuple[Square, int], tuple[Square, int]] = {}
    best: dict[tuple[Square, int], int] = {(start, 0): 0}
    while heap:
        _f, cost, sq, diag = heapq.heappop(heap)
        if sq == goal:
            path = [sq]
            key = (sq, diag)
            while key in came:
                key = came[key]
                path.append(key[0])
            path.reverse()
            return path, cost
        if cost > best.get((sq, diag), 1 << 30):
            continue
        for dx, dy in _DIRS8:
            nxt = (sq[0] + dx, sq[1] + dy)
            if not _fits(grid, nxt, size, mode=mode, blocked=blocked):
                continue
            step = _step_cost(grid, sq, nxt, mode=mode,
                              diagonal_rule=diagonal_rule, diag_count=diag,
                              extra_cost=extra_cost, square_ft=square_ft)
            if step is None:
                continue
            total = cost + step
            if max_ft is not None and total > max_ft:
                continue
            ndiag = (diag + (1 if dx and dy else 0)) % 2
            key = (nxt, ndiag)
            if total < best.get(key, 1 << 30):
                best[key] = total
                came[key] = (sq, diag)
                heapq.heappush(heap, (total + h(nxt), total, nxt, ndiag))
    return [], 0


def nearest_free(grid: Grid, target: Square, *, size: int = 1,
                 mode: str = "walk", blocked: Optional[set[Square]] = None,
                 max_rings: int = 12) -> Optional[Square]:
    """The closest legal square to ``target`` a token of ``size`` can occupy."""
    blocked = blocked or set()
    if _fits(grid, target, size, mode=mode, blocked=blocked):
        return target
    for r in range(1, max_rings + 1):
        ring: list[Square] = []
        for dx in range(-r, r + 1):
            ring.append((target[0] + dx, target[1] - r))
            ring.append((target[0] + dx, target[1] + r))
        for dy in range(-r + 1, r):
            ring.append((target[0] - r, target[1] + dy))
            ring.append((target[0] + r, target[1] + dy))
        for sq in ring:
            if _fits(grid, sq, size, mode=mode, blocked=blocked):
                return sq
    return None


def path_cost_ft(grid: Grid, path: Sequence[Square], *, mode: str = "walk",
                 diagonal_rule: str = CHEBYSHEV, square_ft: int = 5,
                 extra_cost: Optional[Callable[[int, int], int]] = None) -> Optional[int]:
    """Total feet for an explicit path, or ``None`` if a step is illegal."""
    total, diag = 0, 0
    for a, b in zip(path, path[1:]):
        if max(abs(a[0] - b[0]), abs(a[1] - b[1])) != 1:
            return None
        step = _step_cost(grid, a, b, mode=mode, diagonal_rule=diagonal_rule,
                          diag_count=diag, extra_cost=extra_cost,
                          square_ft=square_ft)
        if step is None:
            return None
        total += step
        if a[0] != b[0] and a[1] != b[1]:
            diag += 1
    return total


def opportunity_triggers(path: Sequence[Square], threats: dict[int, tuple[list[Square], int]],
                         *, mover_size: int = 1, square_ft: int = 5,
                         diagonal_rule: str = CHEBYSHEV) -> list[int]:
    """Which threatening creatures the mover *leaves the reach of*.

    ``threats`` maps an id to ``(footprint, reach_ft)``. A trigger fires when
    the mover starts the path inside that reach and ends outside it — the SRD
    condition for an opportunity attack (Disengage is the caller's business).
    """
    if len(path) < 2:
        return []
    start_fp = footprint(path[0][0], path[0][1], mover_size)
    end_fp = footprint(path[-1][0], path[-1][1], mover_size)
    out: list[int] = []
    for tid, (fp, reach) in threats.items():
        was = in_reach(start_fp, fp, reach, square_ft, diagonal_rule)
        now = in_reach(end_fp, fp, reach, square_ft, diagonal_rule)
        if was and not now:
            out.append(tid)
    return out


def band_for_distance(distance: int, engaged_with: Optional[str] = None) -> str:
    """Collapse an exact distance back to the engine's spacing band.

    The gridless combat engine reasons in "melee with X" / "near" / "far"; the
    board keeps that vocabulary true so both layers agree on who can be hit.
    """
    if engaged_with:
        return f"melee with {engaged_with}"
    return "near" if distance <= 30 else "far"
