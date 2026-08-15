"""
Procedural battlemap layouts — deterministic, seeded, LLM-free.

The *mechanics* of a tactical map must never be improvised by a language model:
where the walls are decides who can be shot, and a hallucinated pillar is a
rules bug. So layout is generated here from a seed, and only the *art* (see
:mod:`vtt.art`) goes to the diffusion model — which paints what this module has
already decided.

    from vtt.mapgen import generate_map
    m = generate_map("cave", width=24, height=18, seed=1234)
    print(m.grid.render())
    m.spawn_party, m.spawn_foes   # where the two sides walk in

Every generator guarantees:
  * a connected walkable region (islands are carved together or filled in),
  * at least one party spawn zone and one foe spawn zone, far apart,
  * terrain variety that matters — cover to hide behind, difficult ground to
    cost movement, and at least one interesting risk on most archetypes.

``archetype_for`` maps loose DM language ("in the cave mouth", "a tavern
brawl") onto the closest generator, so the DM never has to know the names.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Sequence

from . import skins as _skins
from .skins import SKYSHIP_STYLES
from .terrain import APERTURES, FLOOR, VOID, WALL, Grid, aperture_axis

Square = tuple[int, int]


@dataclass
class GeneratedMap:
    grid: Grid
    archetype: str
    seed: int
    # Zones the scene engine drops tokens into (already walkable).
    spawn_party: list[Square] = field(default_factory=list)
    spawn_foes: list[Square] = field(default_factory=list)
    # [{"x","y","state","name","dc"}]
    doors: list[dict] = field(default_factory=list)
    # {"x,y": feet} — sparse elevation for ledges and platforms.
    elevation: dict[str, int] = field(default_factory=dict)
    lighting: str = "bright"
    # The medium the board is fought in: "walk" (ground), "swim" (underwater),
    # "fly" (aloft). Connectivity, spawn zones and the "did the generator
    # collapse?" check are all judged in THIS mode — an open-water board is one
    # connected space to a swimmer even though a walker would drown in it.
    mode: str = "walk"
    # A one-line description used for the art prompt and the DM's board header.
    description: str = ""
    # Suggested ambient effects the scene engine may materialise (hazards,
    # light sources): [{"kind","name","shape","x","y","radius_ft",...}]
    effects: list[dict] = field(default_factory=list)
    # Per-square material overrides, sparse: {"x,y": skin name}. See vtt.skins.
    # The archetype already says what most of a board is made of; this is for
    # the exceptions a generator BUILDS — a camp is palisaded, and the tents
    # inside it are canvas. Purely a look: no rule reads one.
    skins: dict[str, str] = field(default_factory=dict)
    # The DM's own words for where this is. Not used by any rule — generators
    # read it to decide what a STRUCTURE is built of (see skins.building_material),
    # because the DM has already said whether this is deep forest or a
    # mountain road and should not be asked twice.
    biome: str = ""
    # A whole-board style choice where the archetype genuinely has one (a
    # skyship is timber, brass-and-steam or grown). Recorded so a table's ship
    # stays the ship it was.
    style: str = ""
    # Upper storeys this layout builds, and the ladders and stairs into them:
    # [{"name","base_ft","terrain":[rows]}] and [{"level","x","y","to_level",
    # "to_x","to_y","name"}]. A watchtower is a real second floor, not a prop.
    levels: list[dict] = field(default_factory=list)
    stairs: list[dict] = field(default_factory=list)
    # Landmarks placed on this board: [{"slug","x","y","yaw"}]. See
    # vtt.setpieces — the mesh is drawing, the tiles it stamped are the rules,
    # and they are already in ``grid`` by the time this list is written.
    setpieces: list[dict] = field(default_factory=list)

    @property
    def width(self) -> int:
        return self.grid.width

    @property
    def height(self) -> int:
        return self.grid.height


# ------------------------------------------------------------------ helpers

def _rng(seed: int) -> random.Random:
    return random.Random(seed & 0x7FFFFFFF)


#: The tile a corridor is carved from, per movement mode — carving stone floor
#: through a reef or a cloudbank would be nonsense.
_CORRIDOR_CODE = {"walk": FLOOR, "swim": "~", "fly": "^"}


def _walkable(grid: Grid, mode: str = "walk") -> set[Square]:
    return {(x, y) for x, y in grid.squares() if grid.passable(x, y, mode=mode)}


def _connective(grid: Grid, x: int, y: int, mode: str = "walk") -> bool:
    """Can you GET through this square, granted you may open what's in it?

    A closed door is impassable and is NOT a wall, and connectivity has to know
    the difference. Judged by ``passable`` alone, a room reached only through a
    shut door reads as cut off, and :func:`_connect_regions` obligingly carves a
    second way in — which is why every door the complex generator placed used to
    have the corridor's own mouth standing open beside it, guarding nothing.
    """
    return grid.passable(x, y, mode=mode) or grid.get(x, y) in APERTURES


def _regions(grid: Grid, mode: str = "walk") -> list[set[Square]]:
    """Connected traversable regions (4-way — diagonal-only links don't count as
    a corridor a Large creature could use)."""
    seen: set[Square] = set()
    out: list[set[Square]] = []
    for sq in (s for s in grid.squares() if _connective(grid, *s, mode)):
        if sq in seen:
            continue
        stack, region = [sq], set()
        seen.add(sq)
        while stack:
            x, y = stack.pop()
            region.add((x, y))
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if (nx, ny) in seen:
                    continue
                if grid.in_bounds(nx, ny) and _connective(grid, nx, ny, mode):
                    seen.add((nx, ny))
                    stack.append((nx, ny))
        out.append(region)
    return sorted(out, key=len, reverse=True)


def _threshold_doors(grid: Grid, rng: random.Random, rooms, out,
                     *, chance: float = 0.8, name: str = "door",
                     dc: Optional[int] = None) -> int:
    """Hang doors where corridors breach room walls. Returns how many.

    The threshold is the gap the corridor already made, and that is the only
    place a door does anything. Punched into some other wall square, a door
    stands beside an open corridor mouth: it guards nothing, closing it changes
    nothing, and the party walks round it without noticing it was there.

    Corners are skipped — a door needs a wall either side to hang from, which
    is exactly what :func:`terrain.aperture_axis` reports.
    """
    made = 0
    for x0, y0, x1, y1 in rooms:
        ring = ([(x, y0) for x in range(x0 + 1, x1)]
                + [(x, y1) for x in range(x0 + 1, x1)]
                + [(x0, y) for y in range(y0 + 1, y1)]
                + [(x1, y) for y in range(y0 + 1, y1)])
        for x, y in ring:
            if grid.get(x, y) != FLOOR:
                continue                       # not a breach
            if not aperture_axis(grid, x, y):
                continue                       # nothing to hang it from
            if rng.random() > chance:
                continue
            grid.set(x, y, "+")
            out.doors.append({"x": x, "y": y, "state": "closed",
                              "name": name, "dc": dc})
            made += 1
    return made


def _dominant_blocker(grid: Grid, mode: str = "walk") -> str:
    """The wall code this map is mostly built from — so filling a dead pocket in
    a cave leaves rock, not a stray dungeon wall."""
    counts: dict[str, int] = {}
    for x, y in grid.squares():
        c = grid.get(x, y)
        if not grid.passable(x, y, mode=mode) and c != VOID:
            counts[c] = counts.get(c, 0) + 1
    return max(counts, key=counts.get) if counts else WALL  # type: ignore[arg-type]


def _connect_regions(grid: Grid, rng: random.Random, mode: str = "walk") -> None:
    """Carve corridors until every traversable square is reachable from the main
    region; anything too small to bother with is filled back in as solid."""
    solid = _dominant_blocker(grid, mode)
    code = _CORRIDOR_CODE.get(mode, FLOOR)
    for _ in range(12):
        regions = _regions(grid, mode)
        if len(regions) <= 1:
            return
        main = regions[0]
        for other in regions[1:]:
            if len(other) < 4:
                for x, y in other:
                    grid.set(x, y, solid)
                continue
            a = min(other, key=lambda s: min(
                abs(s[0] - m[0]) + abs(s[1] - m[1]) for m in _sample(main, rng, 24)))
            b = min(_sample(main, rng, 48),
                    key=lambda s: abs(s[0] - a[0]) + abs(s[1] - a[1]))
            _carve_corridor(grid, a, b, code)
            break


def _sample(pool: set[Square], rng: random.Random, n: int) -> list[Square]:
    items = list(pool)
    if len(items) <= n:
        return items
    return rng.sample(items, n)


def _carve_corridor(grid: Grid, a: Square, b: Square, code: str = FLOOR) -> None:
    x, y = a
    while x != b[0]:
        x += 1 if b[0] > x else -1
        grid.set(x, y, code)
    while y != b[1]:
        y += 1 if b[1] > y else -1
        grid.set(x, y, code)


def _scatter(grid: Grid, rng: random.Random, code: str, chance: float, *,
             only_on: tuple[str, ...] = (FLOOR,),
             within: Optional[tuple[int, int, int, int]] = None,
             keep_passable: bool = True, mode: str = "walk") -> None:
    """Sprinkle a tile across the floor without cutting the map in half.

    ``within`` is an inclusive ``(x0, y0, x1, y1)`` box. A generator that has
    built ROOMS wants its scatter in one of them — a tavern's casks belong in
    the store and not through the taproom — and the alternative is a hand-rolled
    loop that forgets the passability guard, which is how a store full of
    barrels came back with a third of it bricked up: `_connect_regions` fills
    any pocket under four squares with solid.
    """
    for x, y in list(grid.squares()):
        if within and not (within[0] <= x <= within[2]
                           and within[1] <= y <= within[3]):
            continue
        if grid.get(x, y) not in only_on or rng.random() > chance:
            continue
        prev = grid.get(x, y)
        grid.set(x, y, code)
        if keep_passable and not grid.passable(x, y, mode=mode) and len(
                _regions(grid, mode)) > 1:
            grid.set(x, y, prev)


def _island(grid: Grid, rng: random.Random, cx: int, cy: int, radius: float,
            code: str) -> int:
    """A SOLID irregular patch. Returns how many squares it laid.

    ``_blob`` throws random points at a neighbourhood, which is right for
    scattering weed or rubble and wrong for ground: scaled up to island size it
    leaves a lace of single-square holes, and on a sky board every one of those
    holes is a lethal drop somebody has to path around. An island is a shape
    with a wobbly edge, so it is drawn as one — a radius modulated by a couple
    of harmonics, filled solid inside.
    """
    import math as _m

    p1, p2 = rng.random() * _m.tau, rng.random() * _m.tau
    laid = 0
    for x, y in grid.squares():
        dx, dy = x - cx, y - cy
        d = _m.hypot(dx, dy)
        if d > radius * 1.4:
            continue
        a = _m.atan2(dy, dx)
        rr = radius * (1.0 + 0.24 * _m.sin(a * 3 + p1)
                       + 0.13 * _m.sin(a * 5 + p2))
        if d <= rr:
            grid.set(x, y, code)
            laid += 1
    return laid


# --------------------------------------------------------------------------
# Height
#
# The board has folded elevation into every distance, reach, cover check and
# spell area since it went 3D, climbing costs a foot per foot as the SRD has it,
# and stepping off ten feet or more is reported as a FALL for the DM to charge.
# All of that was true and almost nothing used it: fourteen of the twenty-one
# archetypes generated a board that was perfectly flat, including the one called
# mountain-pass. A fight on flat ground is the same fight from either side, and
# height is the cheapest asymmetry there is — it costs movement to take, it
# costs a fall to leave in a hurry, and it changes who can see whom without
# changing a single rule.
#
# Two heights, on purpose. A STEP is free to come down and cheap to climb, so it
# shapes a fight without punishing anyone; a LEDGE is the one you have to decide
# about, because stepping off it is a fall.
# --------------------------------------------------------------------------

STEP_FT = 5
LEDGE_FT = 10


def _raise(out: GeneratedMap, squares: Iterable[Square], ft: int) -> None:
    """Record height on squares. Zero is stored as nothing, not as zero."""
    for x, y in squares:
        if ft:
            out.elevation[f"{x},{y}"] = int(ft)
        else:
            out.elevation.pop(f"{x},{y}", None)


def _terrace(g: Grid, out: GeneratedMap, x0: int, y0: int, x1: int, y1: int,
             ft: int, *, on: tuple[str, ...] = (), steps: str = "") -> list[Square]:
    """Raise (or sink) a rectangle, and mark the way up.

    ``steps`` names a side (n/s/e/w) whose edge squares are set to half the
    height, which is a ramp: it halves each climb, it is somewhere a creature
    can stand between the two levels, and it tells a reader where the ledge is
    meant to be taken. Without one a terrace is a wall you may climb anywhere,
    which is legal and reads as nothing.
    """
    got: list[Square] = []
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            if not g.in_bounds(x, y):
                continue
            if on and g.get(x, y) not in on:
                continue
            got.append((x, y))
    _raise(out, got, ft)
    # A ramp is only worth having on a LEDGE. Half of a five-foot step is two
    # feet, which is a number nobody needs to be told and which costs a climb
    # of five feet either way — the step is already cheap enough to take.
    if steps and got and abs(ft) >= LEDGE_FT:
        edge = {"n": [(x, y0) for x in range(x0, x1 + 1)],
                "s": [(x, y1) for x in range(x0, x1 + 1)],
                "w": [(x0, y) for y in range(y0, y1 + 1)],
                "e": [(x1, y) for y in range(y0, y1 + 1)]}.get(steps, [])
        ramp = [sq for sq in edge if sq in got]
        _raise(out, ramp, int(ft / 2))
        for x, y in ramp:
            if g.get(x, y) not in APERTURES and g.passable(x, y):
                g.set(x, y, "u")
    return got


def _mound(g: Grid, rng: random.Random, out: GeneratedMap, cx: int, cy: int,
           radius: float, ft: int, *, on: tuple[str, ...] = ()) -> list[Square]:
    """A rounded rise in two tiers — the shape of ground rather than of masonry.

    Outer ring at half height so the climb is two short ones and the silhouette
    is a hill instead of a plinth. Nothing here changes what a square IS; a
    mound of grass is still grass.
    """
    got: list[Square] = []
    for x, y in g.squares():
        if on and g.get(x, y) not in on:
            continue
        d = math.hypot(x - cx, y - cy) / max(0.5, radius)
        if d > 1.0:
            continue
        got.append((x, y))
        _raise(out, [(x, y)], ft if d < 0.55 else int(ft / 2))
    return got



def _plateaus(g: Grid, rng: random.Random, out: GeneratedMap, *,
              tiers: int = 3, step_ft: int = LEDGE_FT,
              on: tuple[str, ...] = (), face: str = "R",
              ramps: int = 2) -> None:
    """Stack the whole board into terraces, with cliffs between and ways up.

    The difference between a board with a ledge on it and a board that IS
    stepped. Each tier is a band across the board at its own height; between
    two tiers there is a face of impassable rock, so the only way up is a RAMP —
    which is what makes the high ground a position to be taken rather than a
    square anyone can climb onto from anywhere.

    Boundaries wander (a straight terrace is a retaining wall, not a hillside),
    and the ramps are cut before the cliff is drawn so a tier is never sealed.
    ``_connect_regions`` runs after every generator and would carve its own way
    through, which is a safety net and not a design: two deliberate ramps read
    as a path up a mesa, and a hole punched through a cliff by a connectivity
    pass reads as nothing at all.
    """
    tiers = max(2, tiers)
    across = g.width >= g.height
    span, depth = (g.height, g.width) if across else (g.width, g.height)
    band = max(2, span // tiers)
    # Where each boundary sits, as it wanders along the board.
    edges: list[list[int]] = []
    for t in range(1, tiers):
        pos = t * band
        line = []
        for _ in range(depth):
            pos = max(1, min(span - 2, pos + rng.choice((-1, 0, 0, 0, 1))))
            line.append(pos)
        edges.append(line)

    def tier_at(x: int, y: int) -> int:
        # Ground rises AWAY from the camera, and that is a drawing decision
        # with a reason: the isometric view looks along +x and +z, so a tier
        # that climbs toward the viewer hides its own riser behind the tier in
        # front of it. The first terraced board was measured as perfectly flat
        # for exactly that reason — the heights were all there and not one face
        # was visible. Climbing away from the camera turns every riser to face
        # it.
        a, b = (y, x) if across else (x, y)
        n = 0
        for line in edges:
            if a < line[min(b, len(line) - 1)]:
                n += 1
        return n

    for x, y in g.squares():
        if on and g.get(x, y) not in on:
            continue
        t = tier_at(x, y)
        if t:
            _raise(out, [(x, y)], t * step_ft)

    # The faces, and the gaps left in them. A ramp is a run of squares that
    # keeps the LOWER tier's height, so walking up one is a climb of nothing at
    # the boundary and a climb of the step once you leave it — the same total,
    # spread over ground you can be shot on.
    for i, line in enumerate(edges):
        gaps = sorted(rng.sample(range(2, max(3, depth - 2)),
                                 k=min(ramps, max(1, depth // 8))))
        wide = max(2, depth // 14)
        for b in range(depth):
            a = line[min(b, len(line) - 1)]
            x, y = (b, a) if across else (a, b)
            if not g.in_bounds(x, y):
                continue
            if on and g.get(x, y) not in on:
                continue
            if any(abs(b - gp) <= wide for gp in gaps):
                # A way up: keep it walkable, and step it half a tier so the
                # climb is two short ones rather than one that eats a turn.
                _raise(out, [(x, y)], int((len(edges) - i - 0.5) * step_ft))
                if g.passable(x, y):
                    g.set(x, y, "u")
                continue
            g.set(x, y, face)
            out.elevation.pop(f"{x},{y}", None)


def _storey(out: GeneratedMap, g: Grid, name: str, base_ft: int,
            squares: Iterable[Square], code: str = FLOOR) -> int:
    """Build a real upper (or lower) FLOOR out of the squares given.

    A level starts as all VOID and you lay only the floor you mean, which is
    what makes a gallery a gallery: everywhere else is open to the room below,
    you can see and fall through it, and the two storeys share the fight. Same
    machinery ``structures`` uses for a watchtower platform and a ship's hold.

    Returns the level index for ``_stair``.
    """
    rows = [[VOID] * g.width for _ in range(g.height)]
    n = 0
    for x, y in squares:
        if g.in_bounds(x, y):
            rows[y][x] = code
            n += 1
    if not n:
        return 0
    out.levels.append({"name": name, "base_ft": int(base_ft),
                       "terrain": ["".join(r) for r in rows], "stairs": []})
    return len(out.levels)


def _stair(out: GeneratedMap, level: int, frm: Square, to: Square,
           kind: str = "stairs") -> None:
    """Join a square on one floor to a square on another, both ways."""
    if not level:
        return
    out.stairs.append({"level": 0, "x": int(frm[0]), "y": int(frm[1]),
                       "to_level": int(level), "to_x": int(to[0]),
                       "to_y": int(to[1]), "kind": kind})


def _blob(grid: Grid, rng: random.Random, cx: int, cy: int, size: int,
          code: str) -> None:
    """An organic patch of a tile around a point."""
    for _ in range(max(1, size)):
        r = rng.random() * math.sqrt(size)
        a = rng.random() * math.tau
        x, y = int(round(cx + math.cos(a) * r)), int(round(cy + math.sin(a) * r))
        if grid.in_bounds(x, y):
            grid.set(x, y, code)


def _edge_zone(grid: Grid, side: str, depth: int = 3,
               mode: str = "walk") -> list[Square]:
    """Traversable squares along one edge — a natural entry zone."""
    out: list[Square] = []
    for x, y in grid.squares():
        if not grid.passable(x, y, mode=mode):
            continue
        if side == "west" and x < depth:
            out.append((x, y))
        elif side == "east" and x >= grid.width - depth:
            out.append((x, y))
        elif side == "north" and y < depth:
            out.append((x, y))
        elif side == "south" and y >= grid.height - depth:
            out.append((x, y))
    return out


def _opposed_zones(grid: Grid, rng: random.Random,
                   mode: str = "walk") -> tuple[list[Square], list[Square]]:
    """Two entry zones on opposite edges, both non-empty (falls back to the
    left/right halves of whatever is traversable)."""
    pairs = [("west", "east"), ("north", "south"), ("east", "west"), ("south", "north")]
    rng.shuffle(pairs)
    for a, b in pairs:
        za = _edge_zone(grid, a, mode=mode)
        zb = _edge_zone(grid, b, mode=mode)
        if len(za) >= 3 and len(zb) >= 3:
            return za, zb
    walk = sorted(_walkable(grid, mode))
    if not walk:
        return [], []
    mid = len(walk) // 2
    return walk[:max(1, mid // 3)], walk[-max(1, mid // 3):]


def _room(grid: Grid, x0: int, y0: int, x1: int, y1: int, *,
          floor: str = FLOOR, wall: str = WALL) -> None:
    grid.fill_rect(x0, y0, x1, y1, floor)
    grid.outline_rect(x0, y0, x1, y1, wall)


#: Which way is "out" through each wall of a room.
_OUTWARD = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}

#: Wall-ish codes a door has to be punched all the way through.
_DOOR_BLOCKERS = ("#", "R", " ")


def _door_on_wall(grid: Grid, rng: random.Random, x0: int, y0: int,
                  x1: int, y1: int, side: Optional[str] = None,
                  code: str = "+") -> Optional[Square]:
    """Punch a door through one wall of a rectangular room — all the way through.

    "All the way through" is the part that was missing. Most generators fill the
    whole grid with wall and then carve a room inside it, so a room's own wall
    ring has MORE wall behind it. Setting one square of that ring to a door left
    the door opening onto solid rock: an alcove, not a way out. On the board it
    read as a door standing in the middle of a wall for no reason, and the
    floorplan handed to ControlNet showed a notch rather than a gap.

    So the door walks OUTWARD to the last wall square before open ground (or
    before the edge of the board), everything between it and the room becomes
    floor, and the door lands where the wall actually ends. A door in a
    one-square interior wall doesn't move at all — the walk stops immediately.
    """
    sides = [side] if side else ["north", "south", "east", "west"]
    rng.shuffle(sides)
    for s in sides:
        if s in ("north", "south") and x1 - x0 >= 2:
            x = rng.randint(x0 + 1, x1 - 1)
            y = y0 if s == "north" else y1
        elif s in ("east", "west") and y1 - y0 >= 2:
            y = rng.randint(y0 + 1, y1 - 1)
            x = x0 if s == "west" else x1
        else:
            continue
        if not grid.in_bounds(x, y):
            continue
        dx, dy = _OUTWARD[s]
        cx, cy = x, y
        while (grid.in_bounds(cx + dx, cy + dy)
               and grid.get(cx + dx, cy + dy) in _DOOR_BLOCKERS):
            cx, cy = cx + dx, cy + dy
        px, py = x, y
        while (px, py) != (cx, cy):          # hollow out the wall behind it
            grid.set(px, py, FLOOR)
            px, py = px + dx, py + dy
        grid.set(cx, cy, code)
        return (cx, cy)
    return None


# --------------------------------------------------------------- generators
# Each takes (grid, rng, out) and mutates the grid + fills spawn/description.

def _gen_dungeon_room(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, WALL)
    m = 1
    _room(g, m, m, g.width - 1 - m, g.height - 1 - m)
    # Pillars in a rough colonnade — real cover, symmetric enough to read as built.
    step = max(3, min(5, g.width // 5))
    for y in range(m + 2, g.height - m - 1, step):
        for x in range(m + 2, g.width - m - 1, step):
            if rng.random() < 0.7:
                g.set(x, y, "O")
    # Rubble and a scattering of furniture.
    _scatter(g, rng, ",", 0.06)
    _scatter(g, rng, "o", 0.03)
    for side in ("west", "east"):
        d = _door_on_wall(g, rng, m, m, g.width - 1 - m, g.height - 1 - m, side)
        if d:
            out.doors.append({"x": d[0], "y": d[1], "state": "closed",
                              "name": "door", "dc": None})
    # A DAIS, or a floor that drops. Either way the room stops being a flat
    # rectangle: whoever holds the high end shoots down into it, and getting up
    # there costs a climb.
    m2 = m + 1
    if rng.random() < 0.7:
        d = max(3, min(6, g.height // 4))
        top = rng.random() < 0.5
        y0 = m2 if top else g.height - 1 - m2 - d
        _terrace(g, out, m2, y0, g.width - 1 - m2, y0 + d, STEP_FT,
                 on=(FLOOR, ",", "o", "O"), steps=("s" if top else "n"))
        out.description = ("a pillared stone chamber, flagstones cracked with "
                           "age, a broad dais along one end")
    else:
        pit = max(4, min(9, g.width // 3))
        px = (g.width - pit) // 2
        py = (g.height - max(3, pit // 2)) // 2
        _terrace(g, out, px, py, px + pit, py + max(3, pit // 2), -STEP_FT,
                 on=(FLOOR, ",", "o"), steps="w")
        out.description = ("a pillared stone chamber built around a sunken "
                           "floor, flagstones cracked with age")
    out.lighting = rng.choice(["dim", "dim", "bright"])


def _bsp_cells(x0: int, y0: int, x1: int, y1: int, rng: random.Random,
               depth: int = 0, min_side: int = 7) -> list[tuple[int, int, int, int]]:
    """Recursively halve a rectangle until the pieces are room-sized."""
    w, h = x1 - x0, y1 - y0
    if depth >= 3 or (w < min_side * 2 and h < min_side * 2):
        return [(x0, y0, x1, y1)]
    horizontal = h > w if abs(w - h) > 2 else rng.random() < 0.5
    if horizontal and h >= min_side * 2:
        cut = rng.randint(y0 + min_side, y1 - min_side)
        return (_bsp_cells(x0, y0, x1, cut, rng, depth + 1, min_side)
                + _bsp_cells(x0, cut, x1, y1, rng, depth + 1, min_side))
    if w >= min_side * 2:
        cut = rng.randint(x0 + min_side, x1 - min_side)
        return (_bsp_cells(x0, y0, cut, y1, rng, depth + 1, min_side)
                + _bsp_cells(cut, y0, x1, y1, rng, depth + 1, min_side))
    return [(x0, y0, x1, y1)]


def _gen_dungeon_complex(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, WALL)
    rooms: list[tuple[int, int, int, int]] = []
    for cx0, cy0, cx1, cy1 in _bsp_cells(0, 0, g.width - 1, g.height - 1, rng):
        # Inset the room inside its cell so neighbouring rooms don't share walls.
        w = max(3, (cx1 - cx0) - rng.randint(1, 3))
        h = max(3, (cy1 - cy0) - rng.randint(1, 3))
        x0 = rng.randint(cx0, max(cx0, cx1 - w))
        y0 = rng.randint(cy0, max(cy0, cy1 - h))
        x1, y1 = min(g.width - 1, x0 + w), min(g.height - 1, y0 + h)
        if x1 - x0 < 3 or y1 - y0 < 3:
            continue
        rooms.append((x0, y0, x1, y1))
        _room(g, x0, y0, x1, y1)
    rng.shuffle(rooms)
    for (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) in zip(rooms, rooms[1:]):
        a = ((ax0 + ax1) // 2, (ay0 + ay1) // 2)
        b = ((bx0 + bx1) // 2, (by0 + by1) // 2)
        _carve_corridor(g, a, b)
    # Doors go where the corridors came IN, which is the only place they matter.
    _threshold_doors(g, rng, rooms, out)
    _scatter(g, rng, ",", 0.05)
    _scatter(g, rng, "o", 0.02)
    out.lighting = "dim"
    out.description = "a warren of stone rooms joined by narrow corridors"
    # Not every room in a complex is on the same course of stone. A third of
    # them sit a step up or down, so a doorway is a step and a corridor fight
    # has a high side.
    for rx0, ry0, rx1, ry1 in rooms:
        if rng.random() >= 0.34:
            continue
        _terrace(g, out, rx0 + 1, ry0 + 1, rx1 - 1, ry1 - 1,
                 rng.choice((STEP_FT, STEP_FT, -STEP_FT)),
                 on=(FLOOR, ",", "o", "O"))


def _gen_cave(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    # Cellular automata: noise, then smooth into caverns.
    for x, y in g.squares():
        edge = x == 0 or y == 0 or x == g.width - 1 or y == g.height - 1
        g.set(x, y, "R" if (edge or rng.random() < 0.44) else FLOOR)
    for _ in range(4):
        snapshot = g.copy()
        for x, y in g.squares():
            walls = sum(
                1 for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                if (dx or dy) and not snapshot.passable(x + dx, y + dy)
            )
            if x in (0, g.width - 1) or y in (0, g.height - 1):
                g.set(x, y, "R")
            elif walls >= 5:
                g.set(x, y, "R")
            elif walls <= 3:
                g.set(x, y, FLOOR)
    _connect_regions(g, rng)
    # Water seeps into the low ground; stalagmites give cover.
    for _ in range(rng.randint(1, 3)):
        cx, cy = rng.randrange(g.width), rng.randrange(g.height)
        if g.passable(cx, cy):
            _blob(g, rng, cx, cy, rng.randint(4, 10), "~")
    _scatter(g, rng, ",", 0.08)
    _scatter(g, rng, "O", 0.03)
    out.lighting = "dark"
    out.description = "a damp natural cavern, stalagmites and standing water"
    # A cave is the one place that is obviously three-dimensional and the board
    # drew it flat. A SHELF along one side of the cavern, ten feet up, reached
    # where the rock has fallen in — and the floor of the deepest part lower
    # than the rest, so a fight in here has a top and a bottom.
    floor = [(x, y) for x, y in g.squares() if g.get(x, y) in (FLOOR, ",")]
    if floor:
        xs = [x for x, _y in floor]
        ys = [y for _x, y in floor]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        band = max(2, (y1 - y0) // 5)
        top = rng.random() < 0.5
        sy0 = y0 if top else y1 - band
        _terrace(g, out, x0, sy0, x1, sy0 + band, LEDGE_FT,
                 on=(FLOOR, ","), steps=("s" if top else "n"))
        _mound(g, rng, out, (x0 + x1) / 2, (y0 + y1) / 2,
               max(2.0, min(x1 - x0, y1 - y0) / 4.0), -STEP_FT, on=(FLOOR, ","))
        out.description += ", a shelf of rock along one wall above the floor"


def _gen_forest(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, "g")
    for _ in range(int(g.width * g.height * 0.02)):
        cx, cy = rng.randrange(g.width), rng.randrange(g.height)
        _blob(g, rng, cx, cy, rng.randint(1, 4), "T")
    _scatter(g, rng, "\"", 0.12, only_on=("g",))
    # A stream cutting across, with a ford or a fallen log to cross by — and
    # its BANKS, which is the difference between a stream and a blue stripe. It
    # was two squares wide, flat, and painted over as grass every time: at
    # board scale a one- or two-square feature with no relief is a couple of
    # percent of the picture and the model simply does not keep it. Cut five
    # feet down it is carried by the depth map, and it is cover and a scramble
    # in the rules besides.
    if rng.random() < 0.6:
        sx = rng.randrange(2, max(3, g.width - 2))
        y = 0
        cut: list[Square] = []
        while y < g.height:
            wide = 2 if rng.random() < 0.35 else 1
            for d in range(wide + 1):
                x = min(g.width - 1, sx + d)
                g.set(x, y, "~")
                cut.append((x, y))
            sx = max(1, min(g.width - 2, sx + rng.choice((-1, 0, 0, 1))))
            y += 1
        _raise(out, cut, -STEP_FT)
        bridge_y = rng.randrange(1, g.height - 1)
        for x in range(g.width):
            if g.get(x, bridge_y) == "~":
                # A log over the gully, at the height of the banks it joins.
                g.set(x, bridge_y, "b")
                _raise(out, [(x, bridge_y)], 0)
    _connect_regions(g, rng)
    out.lighting = rng.choice(["bright", "dim"])
    out.description = "old woodland — thick trunks, tangled undergrowth, a shallow stream"
    # Woodland is not a table top: a knoll to hold and a hollow to be caught in.
    _mound(g, rng, out, rng.randrange(2, max(3, g.width - 2)),
           rng.randrange(2, max(3, g.height - 2)),
           rng.uniform(2.0, 3.5), STEP_FT, on=("g", "\""))
    if rng.random() < 0.6:
        _mound(g, rng, out, rng.randrange(2, max(3, g.width - 2)),
               rng.randrange(2, max(3, g.height - 2)),
               rng.uniform(2.0, 3.0), -STEP_FT, on=("g", "\""))


def _gen_clearing(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, "g")
    for x, y in g.squares():
        d = math.hypot(x - g.width / 2, y - g.height / 2)
        if d > min(g.width, g.height) * 0.42 and rng.random() < 0.65:
            g.set(x, y, "T")
    _scatter(g, rng, "\"", 0.07, only_on=("g",))
    if rng.random() < 0.5:
        cx, cy = g.width // 2, g.height // 2
        g.set(cx, cy, ",")
        out.effects.append({"kind": "light", "name": "campfire", "shape": "sphere",
                            "x": cx, "y": cy, "radius_ft": 20, "color": "#ffb347"})
    out.description = "a open glade ringed by dark trees"
    if rng.random() < 0.28:
        _plateaus(g, rng, out, tiers=2, on=("g", "\"", ","), face="R", ramps=2)
        out.description += ", the ground stepping up to a higher shelf"
    elif rng.random() < 0.75:
        _mound(g, rng, out, g.width // 2 + rng.randint(-4, 4),
               g.height // 2 + rng.randint(-3, 3),
               rng.uniform(2.5, 4.0), LEDGE_FT, on=("g", "\"", ","))
        out.description += ", a green barrow mound at its centre"


def _gen_street(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, "=")
    band = max(2, g.height // 4)
    # Buildings crowd both sides; alleys break the line so it isn't a shooting gallery.
    for y in range(0, band):
        for x in range(g.width):
            g.set(x, y, WALL)
    for y in range(g.height - band, g.height):
        for x in range(g.width):
            g.set(x, y, WALL)
    for _ in range(rng.randint(1, 3)):
        ax = rng.randrange(1, g.width - 1)
        for y in range(0, band):
            g.set(ax, y, "=")
    for _ in range(rng.randint(1, 3)):
        ax = rng.randrange(1, g.width - 1)
        for y in range(g.height - band, g.height):
            g.set(ax, y, "=")
    _scatter(g, rng, "o", 0.05, only_on=("=",))
    _scatter(g, rng, "n", 0.02, only_on=("=",))
    _scatter(g, rng, ",", 0.04, only_on=("=",))
    out.lighting = rng.choice(["bright", "dim"])
    out.description = "a narrow city street between shuttered buildings, crates stacked at the walls"
    # ROOFS. A street fight with archers above it is the asymmetric fight, and
    # the buildings were solid blocks with nothing on top. The roof level is
    # laid over the building squares only, so the street itself stays open sky
    # from up there — you can see down into it, shoot into it, and fall into it.
    roof = [(x, y) for x, y in g.squares() if g.get(x, y) == WALL]
    if len(roof) > 12:
        level = _storey(out, g, "Rooftops", 20, roof)
        if level:
            # Outside stairs: a square of street beside a building, and the
            # roof square it climbs to. Two or three of them, spread out — one
            # way up to a roof that covers half the board is a chokepoint
            # rather than a second storey, and the point of the roofs is that
            # both sides can use them.
            want = 2 if len(roof) < 120 else 3
            placed: list[Square] = []
            for x, y in sorted(roof, key=lambda sq: (sq[0] * 7 + sq[1] * 13) % 101):
                if len(placed) >= want:
                    break
                if any(abs(x - px) + abs(y - py) < max(6, g.width // 4)
                       for px, py in placed):
                    continue
                for dx, dy in ((0, 1), (1, 0), (0, -1), (-1, 0)):
                    sx, sy = x + dx, y + dy
                    if g.in_bounds(sx, sy) and g.get(sx, sy) in ("=", FLOOR, ","):
                        g.set(sx, sy, "u")
                        _stair(out, level, (sx, sy), (x, y), kind="stair")
                        placed.append((x, y))
                        break
            out.description += ", roofs above it reached by an outside stair"


def _gen_tavern(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    """A taproom, and the thing that stops it being a hall.

    It was one rectangle the size of the whole board with a bar strip, five
    tables and a fire in it — four hundred squares of which three hundred and
    twenty-five were bare floor. Painted, that is a barn: nothing stands up
    anywhere between the walls, so the depth map hands the model a floor and two
    distant walls and the model paints exactly that.

    Three things fix it, and only one of them is furniture.

    **POSTS.** A low-beamed taproom is a room held up in the MIDDLE, and the
    board had nothing standing anywhere but its edges. A grid of timber posts is
    relief the depth map can carry right through the room, it is what makes an
    interior read as a beamed one rather than as a hall, and mechanically it is
    the cover an indoor fight has always wanted — something to break line of
    sight behind without leaving the room. They are laid on a spacing, not
    scattered: a post holds a beam up, and beams run in lines.

    **BACK ROOMS.** A tavern is a taproom, a kitchen behind the bar and a store
    off it — so the board is carved rather than filled, and the walls between
    them are what turns one box into a place with corners to fight round. The
    doors are real doors, which the fleeing side can shut.

    **SNUGS.** A stub of partition off a wall makes a booth: two squares of
    three-quarters cover, and a silhouette that is not a straight line.
    """
    g.fill_rect(0, 0, g.width - 1, g.height - 1, WALL)
    # The taproom takes most of the board; the service rooms take a strip along
    # one side. A tavern with nothing behind the bar is a bar in a field.
    back = max(3, min(6, g.width // 5)) if g.width >= 16 else 0
    tap_x1 = g.width - 1 - (back + 1 if back else 0)
    _room(g, 0, 0, tap_x1, g.height - 1)
    if back:
        cut = rng.randrange(g.height // 3, max(g.height // 3 + 1,
                                               2 * g.height // 3))
        _room(g, tap_x1, 0, g.width - 1, cut)               # the kitchen
        _room(g, tap_x1, cut, g.width - 1, g.height - 1)    # the store
        for y0, y1, name in ((0, cut, "kitchen door"),
                             (cut, g.height - 1, "store door")):
            d = _door_on_wall(g, rng, tap_x1, y0, g.width - 1, y1, "west")
            if d:
                out.doors.append({"x": d[0], "y": d[1], "state": "closed",
                                  "name": name, "dc": None})
        # Casks and sacks in the store, a range and a block in the kitchen.
        _scatter(g, rng, "o", 0.22, only_on=(FLOOR,),
                 within=(tap_x1 + 1, cut + 1, g.width - 2, g.height - 2))
        _scatter(g, rng, "n", 0.22, only_on=(FLOOR,),
                 within=(tap_x1 + 1, 1, g.width - 2, cut - 1))

    # FIXTURES BEFORE FURNITURE, and a set that says which squares the
    # furniture may not have. The hearth and the front door each wall
    # themselves in on three sides, so a table or a post landing on the fourth
    # sealed them into a pocket — and `_connect_regions` fills every pocket
    # under four squares with solid, which is how the fire vanished off a board
    # in five and the way IN off one in a hundred, with nothing in the log to
    # say either had ever been laid.
    keep: set[Square] = set()

    # The hearth, in a breast that stands proud of the wall — a fire drawn flat
    # against a flat wall is a light source, not a fireplace — and an apron of
    # floor in front of it, which is where anyone standing at the fire stands.
    hy = max(2, min(g.height - 3, g.height // 2 + rng.randint(-2, 2)))
    g.set(1, hy, "f")
    keep.add((1, hy))
    for dy in (-1, 1):
        if g.in_bounds(1, hy + dy):
            g.set(1, hy + dy, WALL)
    for ax in range(2, min(5, tap_x1)):
        g.set(ax, hy, FLOOR)
        keep.add((ax, hy))
    out.effects.append({"kind": "light", "name": "hearth", "shape": "sphere",
                        "x": 1, "y": hy, "radius_ft": 20, "color": "#ff8c42"})

    # The bar, with a GAP in it: a run of counter a creature cannot get behind
    # is a wall, and the whole point of a bar in a brawl is that someone is
    # behind it.
    bar_y = 1 if rng.random() < 0.5 else g.height - 2
    gap = rng.randrange(3, max(4, tap_x1 - 2))
    for x in range(2, tap_x1 - 1):
        if x != gap:
            g.set(x, bar_y, "n")
            # The counter says it is a counter. It is furniture to every RULE —
            # same cover, same three feet — and a per-square skin is the only
            # thing that lets the painter and the segmentation map tell a bar
            # from the tables in front of it.
            out.skins[f"{x},{bar_y}"] = "taproom-bar"
    behind = bar_y - 1 if bar_y == 1 else bar_y + 1
    for x in range(2, tap_x1 - 1):
        if g.in_bounds(x, behind) and g.get(x, behind) == FLOOR \
                and (x, behind) not in keep and rng.random() < 0.3:
            g.set(x, behind, "o")                            # casks on the back shelf

    # The way IN, with its own apron for the same reason the hearth has one.
    d = _door_on_wall(g, rng, 0, 0, tap_x1, g.height - 1,
                      "north" if bar_y != 1 else "south")
    if d:
        g.set(d[0], d[1], "/")
        keep.add(d)
        step = 1 if d[1] == 0 else -1
        for i in range(1, 4):
            sq = (d[0], d[1] + step * i)
            if g.in_bounds(*sq) and 0 < sq[1] < g.height - 1:
                g.set(*sq, FLOOR)
                keep.add(sq)
        out.doors.append({"x": d[0], "y": d[1], "state": "open",
                          "name": "tavern door", "dc": None})

    # POSTS on a spacing, clear of the bar and of the walls. Impassable, so
    # they are laid on a lattice a creature always has a way through.
    step_x = 4 if tap_x1 >= 14 else 3
    for px in range(3, tap_x1 - 1, step_x):
        for py in range(3, g.height - 2, 4):
            if g.get(px, py) == FLOOR and (px, py) not in keep \
                    and abs(py - bar_y) > 1:
                g.set(px, py, "O")

    # Tables where the posts are not: a table and the bench beside it, which is
    # what a table is for and what makes a cluster rather than a lone square.
    for _ in range(rng.randint(5, 8)):
        tx = rng.randrange(2, max(3, tap_x1 - 1))
        ty = rng.randrange(2, g.height - 2)
        if g.get(tx, ty) != FLOOR or (tx, ty) in keep:
            continue
        g.set(tx, ty, "n")
        for dx, dy in rng.sample([(1, 0), (-1, 0), (0, 1), (0, -1)], 2):
            sq = (tx + dx, ty + dy)
            if g.in_bounds(*sq) and g.get(*sq) == FLOOR and sq not in keep:
                # A BENCH, and it is `n` rather than `w`: both screen three feet,
                # and `w` is labelled "low wall" on the board — a bench with a
                # wall's name on it is the picture and the grid disagreeing in
                # the one place the label exists to stop them.
                g.set(*sq, "n")
                break

    # A SNUG: a partition off the long wall, and the booth it makes.
    if tap_x1 >= 10 and g.height >= 10 and rng.random() < 0.7:
        sy = rng.randrange(3, g.height - 4)
        sx = 1 if rng.random() < 0.5 else tap_x1 - 3
        for i in range(rng.randint(2, 3)):
            sq = (sx + i, sy)
            if g.in_bounds(*sq) and sq not in keep and g.get(*sq) in (FLOOR, "n"):
                g.set(*sq, WALL)
    out.lighting = "dim"
    out.description = ("a low-beamed taproom, its ceiling carried on timber "
                       "posts, tables and benches between them, a fire burning "
                       "in the hearth")
    if back:
        out.description += ", a kitchen and a store behind the bar"
    # A GALLERY over the taproom. The classic tavern brawl is fought up and
    # down the stairs, and every tavern this generator has ever made was one
    # flat room — the machinery for a real storey has existed since floors went
    # in and no generator used it. Only over the TAPROOM: a gallery over the
    # kitchen is a floor nobody can see from the room the fight is in.
    # Connect BEFORE the gallery, not after. `_connect_regions` carves
    # corridors as plain floor and fills small pockets as solid, so running it
    # last quietly paved over the stair square it had no idea was a stair.
    _connect_regions(g, rng)
    # The taproom's inside, which excludes its wall ring — the front door sits
    # IN that ring, and counting it dragged the gallery's extent out to the
    # board's edge and made the run a row deeper than the room.
    inner = [(x, y) for x, y in g.squares()
             if 0 < x <= tap_x1 - 1 and 0 < y < g.height - 1
             and g.get(x, y) not in (WALL, VOID)]
    if inner and tap_x1 >= 13 and g.height >= 12:
        xs = [x for x, _y in inner]
        ys = [y for _x, y in inner]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        depth = max(2, (y1 - y0) // 4)
        north = rng.random() < 0.5
        gy0 = y0 if north else y1 - depth
        walk = [(x, y) for x, y in inner if gy0 <= y <= gy0 + depth]
        level = _storey(out, g, "Gallery", 10, walk)
        if level:
            # The stair stands at one end of the run, on the taproom floor —
            # and the first candidate is not good enough. The taproom's edges
            # now carry posts, tables and a hearth breast, so taking the
            # left-most square and giving up if the square below it is occupied
            # left a gallery with no way onto it on three boards in five.
            for foot in sorted(walk, key=lambda sq: (sq[0], sq[1])):
                below = (foot[0], foot[1] + depth + 1 if north else foot[1] - 1)
                # A post or a table in the way is MOVED. Refusing instead left
                # a gallery with no way onto it, and a storey nobody can reach
                # is worse than a taproom one post short.
                if g.in_bounds(*below) and g.get(*below) not in (WALL, VOID) \
                        and below not in keep:
                    g.set(*below, "u")
                    _stair(out, level, below, foot)
                    break
            out.description += ", a gallery running along one side above it"


def _gen_bridge(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    """A crossing, and the thing that GUARDS a crossing.

    A bridge with bare ground at both ends is a corridor with a hole in it: the
    only decision it offers is whether to cross. A tower at each end is what
    makes it a place — high ground that has to be climbed to, a room to be
    fought out of, and an archer who can shoot down the length of the span while
    the party is out on it with nowhere to stand. That last part is the reason
    it is worth building rather than drawing: the tower top is a real storey, so
    every distance, cover and area check already knows the archer is up there.
    """
    from . import structures

    chasm = "x" if rng.random() < 0.5 else "W"
    g.fill_rect(0, 0, g.width - 1, g.height - 1, "," if chasm == "x" else "g")
    gap_top = g.height // 2 - max(1, g.height // 6)
    gap_bot = g.height // 2 + max(1, g.height // 6)
    g.fill_rect(0, gap_top, g.width - 1, gap_bot, chasm)
    span_x = rng.randrange(max(2, g.width // 4), max(3, 3 * g.width // 4))
    for y in range(gap_top, gap_bot + 1):
        g.set(span_x, y, "b")
        if g.width > 14:
            g.set(span_x + 1, y, "b")

    # A gatehouse at each end, offset to the side so it overlooks the mouth of
    # the span without standing in it. Tried in a few places and skipped where
    # the bank is too narrow, rather than forced — a tower half off the board is
    # worse than a bank with none.
    ground = (",", "g")
    # What the towers are built of is the DM's call, and they have already made
    # it: the biome they typed is on the board row. A forest crossing gets a
    # timber stockade, a mountain road drystone.
    material = _skins.building_material(out.biome)
    # A post tower is always five squares across (see structures.POST_TOWER_SIZE
    # for why it must be odd); a stone one may shrink to four to fit a shallow
    # bank.
    sizes = ((structures.POST_TOWER_SIZE,) if material == "timber" else (5, 4))
    for bank_far, anchor in ((True, gap_top), (False, gap_bot)):
        placed = False
        for size in sizes:
            # A bank is only as deep as the generator left it, so the tower is
            # pushed hard against the outside edge and the SIZE is what gives
            # way — never the placement, which would put it out over the gorge.
            y0 = max(0, anchor - size - 1) if bank_far else min(
                g.height - size, anchor + 2)
            for dx in (2, -size - 1, 3, -size - 2, 4, -size):
                b = structures.watchtower(g, rng, out, span_x + dx, y0,
                                          on=ground, base_ft=15,
                                          material=material,
                                          name="tower top")
                if b.interior:
                    out.skins.update(b.skins)
                    out.doors.extend(b.doors)
                    placed = True
                    break
            if placed:
                break

    # Boulders, and NOWHERE near the crossing. Scattered at 4% across the whole
    # bank these were the loose blocks that made no sense — a rock face is a
    # full-height sight blocker, so one dropped at the mouth of the span reads
    # as the bridge being walled off, and a couple did land there. A crossing's
    # approach is the one place on this board that has to stay clear.
    approach = {(span_x + dx, y)
                for dx in (-2, -1, 0, 1, 2, 3)
                for y in list(range(gap_top - 3, gap_top))
                + list(range(gap_bot + 1, gap_bot + 4))}
    for x, y in list(g.squares()):
        if (x, y) in approach or g.get(x, y) not in ground:
            continue
        if rng.random() < 0.025:
            g.set(x, y, "R")
    _connect_regions(g, rng)
    crossing = ("a plank bridge over a black chasm" if chasm == "x"
                else "a plank bridge over deep cold water")
    guard = ("a timber watchtower on four raked legs standing over"
             if material == "timber"
             else "a squat drystone watchtower at")
    out.description = f"{crossing}, {guard} each end of the span"
    out.effects.append({"kind": "marker", "name": "the span", "shape": "path",
                        "squares": [[span_x, y] for y in range(gap_top, gap_bot + 1)]})


def _gen_ruins(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, ",")
    for _ in range(rng.randint(3, 6)):
        w = rng.randint(3, max(4, g.width // 3))
        h = rng.randint(3, max(4, g.height // 3))
        x0 = rng.randint(0, max(0, g.width - w - 1))
        y0 = rng.randint(0, max(0, g.height - h - 1))
        # Broken walls: outline, then knock chunks out of it.
        g.outline_rect(x0, y0, x0 + w, y0 + h, "w" if rng.random() < 0.5 else WALL)
        for x, y in list(g.squares()):
            if g.get(x, y) in (WALL, "w") and rng.random() < 0.45:
                g.set(x, y, ",")
    _scatter(g, rng, "O", 0.03, only_on=(",",))
    _scatter(g, rng, "\"", 0.06, only_on=(",",))
    _connect_regions(g, rng)
    out.description = "toppled masonry and broken colonnades, weeds through the flagstones"
    # What is left of a building is rarely all at one height. Sometimes the
    # whole site is terraced — foundations at three heights with the retaining
    # walls still standing — and otherwise one floor still stands at the far
    # end over a cellar.
    if rng.random() < 0.3:
        _plateaus(g, rng, out, tiers=3, on=(",", "\"", FLOOR), face="#",
                  ramps=2)
        out.description += (", the site terraced in three courses with the "
                            "retaining walls still standing")
    elif rng.random() < 0.8:
        tw = max(4, g.width // 3)
        th = max(3, g.height // 3)
        tx = rng.randrange(1, max(2, g.width - tw - 1))
        ty = rng.randrange(1, max(2, g.height - th - 1))
        _terrace(g, out, tx, ty, tx + tw, ty + th, LEDGE_FT,
                 on=(",", "\"", FLOOR), steps=rng.choice(("n", "s", "e", "w")))
    if rng.random() < 0.6:
        cw = max(3, g.width // 5)
        ch = max(3, g.height // 5)
        cx0 = rng.randrange(1, max(2, g.width - cw - 1))
        cy0 = rng.randrange(1, max(2, g.height - ch - 1))
        _terrace(g, out, cx0, cy0, cx0 + cw, cy0 + ch, -LEDGE_FT,
                 on=(",", "\"", FLOOR), steps=rng.choice(("n", "s", "e", "w")))
    out.description += ", a floor still standing at one end over a fallen cellar"


def _gen_camp(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    """Tents you can get INSIDE, in a palisaded ring around the fire.

    The tents used to be 2x2 blocks of impassable furniture, which meant a
    creature could only ever be BESIDE one — and a token on the neighbouring
    square read as a soldier standing on the canvas. A tent is a room: walls
    that block sight, a floor, and a flap to come in by. That turns the camp
    from an obstacle course into somewhere with insides to clear, which is what
    a camp assault actually is.
    """
    from . import structures

    g.fill_rect(0, 0, g.width - 1, g.height - 1, "g")
    cx, cy = g.width // 2, g.height // 2

    # Tents first, while the ground is still clear: they need a clean footprint,
    # and scatter dropped afterwards flows around them.
    want = rng.randint(3, 5)
    pitched = 0
    for _ in range(60):
        if pitched >= want:
            break
        tx = rng.randrange(1, max(2, g.width - 6))
        ty = rng.randrange(1, max(2, g.height - 6))
        if abs(tx - cx) < 4 and abs(ty - cy) < 4:
            continue                       # leave the fire its circle
        b = structures.tent(g, rng, tx, ty, on=("g",))
        if b.interior:
            out.skins.update(b.skins)
            pitched += 1

    g.set(cx, cy, "f")
    out.effects.append({"kind": "light", "name": "campfire", "shape": "sphere",
                        "x": cx, "y": cy, "radius_ft": 20, "color": "#ffb347"})

    # One stretch of palisade with a gate in it. A full ring would wall the
    # board in; a run along one approach is what a camp actually throws up, and
    # it gives the fight a front.
    if g.width > 14 and g.height > 10:
        top = rng.random() < 0.5
        wy = 1 if top else g.height - 2
        x_from = rng.randrange(1, max(2, g.width // 3))
        x_to = min(g.width - 2, x_from + rng.randint(g.width // 3, g.width // 2))
        gate = rng.randrange(x_from + 1, max(x_from + 2, x_to))
        for x in range(x_from, x_to + 1):
            if g.get(x, wy) != "g":
                continue                   # never wall a tent up
            g.set(x, wy, "/" if x == gate else WALL)
            out.skins[f"{x},{wy}"] = "palisade"

    _scatter(g, rng, "o", 0.03, only_on=("g",))
    _scatter(g, rng, "\"", 0.05, only_on=("g",))
    _connect_regions(g, rng)
    out.lighting = "dim"
    out.description = ("a war camp — canvas tents around a guttering fire, a "
                       "log palisade thrown up across one approach")
    # A camp that has been anywhere throws up a bank. One side of the board
    # stands a step higher behind it, which is the asymmetry a raid is fought
    # across.
    if rng.random() < 0.7:
        band = max(2, g.height // 6)
        top = rng.random() < 0.5
        y0 = 0 if top else g.height - 1 - band
        _terrace(g, out, 0, y0, g.width - 1, y0 + band, STEP_FT,
                 on=("g", "\"", ",", FLOOR))


def _hull(g: Grid, rng: random.Random, surround: str, deck: str = "b",
          plan: str = "sea") -> int:
    """Carve a SHIP-shaped deck out of the board. Returns the stern's x.

    The first shape was a symmetric lens — the same taper at both ends — which
    is a leaf, not a vessel, and it is why every ship board read as a rectangle
    with the corners knocked off.

    There are TWO plans now, and the reason is that one was not enough: a
    skyship and a caravel wearing the same outline and near enough the same
    skins came back as the same boat, which is a fair complaint about a flying
    ship. A **sea** hull comes to a point at the bow, holds its full beam
    through the waist and finishes in a broad flat transom, because it is a
    box that has to float and be steered from the back. A **sky** hull is
    slender and fine at BOTH ends — nothing about air rewards a transom — and
    fullest amidships where the lift is. Those silhouettes are what the painter
    is conditioned on, so they are most of the difference between the two.

    Either way the outline is a staircase, because it is carved out of squares.
    What stops it LOOKING like one is `isocam.footprint`, which cuts every
    outer corner so a one-square step is drawn as the diagonal it means.
    """
    g.fill_rect(0, 0, g.width - 1, g.height - 1, surround)
    length = max(6, g.width - 2)
    beam = max(4, g.height - 2)
    cy = (g.height - 1) / 2.0

    def half_beam(t: float) -> float:
        """Half-width at t along the hull; 0 is the bow, 1 the stern."""
        if plan == "sky":
            # Fine at both ends, fullest a little aft of amidships. The floor
            # keeps a spine of deck at the very tips, so the bow is a point you
            # can stand on rather than a gap.
            k = max(0.16, math.sin(math.pi * min(1.0, t * 0.92 + 0.04)) ** 0.62)
            return k * 0.88 * (beam / 2.0)
        if t < 0.42:                       # the bow, entering fine
            k = 0.10 + 0.90 * (t / 0.42) ** 0.62
        elif t < 0.78:                     # the waist, full beam
            k = 1.0
        else:                              # quarters easing to a flat stern
            k = 1.0 - 0.28 * ((t - 0.78) / 0.22) ** 1.4
        return k * (beam / 2.0)

    for i in range(length):
        x = 1 + i
        hb = half_beam(i / float(length - 1))
        for y in range(g.height):
            if abs(y - cy) <= hb:
                g.set(x, y, deck)
    return length          # the last deck column is x = length


def _rail(g: Grid, rng: random.Random, surround: str) -> None:
    """A stanchion rail everywhere the deck meets nothing, bar one gangway.

    Continuous on purpose. It used to be dropped at random on a fifth of the
    squares, which reads as a rail somebody has already smashed — and on a
    board where falling off the edge is a real outcome, a gap in the rail is a
    mechanical statement (half cover ends here) that nobody meant to make.
    """
    edge = [(x, y) for x, y in g.squares()
            if g.get(x, y) == "b"
            and any(g.get(x + dx, y + dy) == surround
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))]
    if not edge:
        return
    gangway = edge[rng.randrange(len(edge))]
    for x, y in edge:
        if (x, y) != gangway:
            g.set(x, y, "w")


def _rig_ship(g: Grid, rng: random.Random, out: GeneratedMap, stern_x: int,
              *, cabin_skin: str = "hull") -> None:
    """The furniture that makes a deck a ship: mast, cabin, hatch and hold."""
    from . import structures
    from .terrain import VOID

    cy = g.height // 2
    # The mast steps a little forward of amidships, where a mast goes.
    mast_x = max(2, int(stern_x * 0.44))
    for dx in (0, 1, -1, 2):
        if g.get(mast_x + dx, cy) == "b":
            g.set(mast_x + dx, cy, "O")
            mast_x = mast_x + dx
            break

    # The captain's quarters, aft. Placed against the transom where the beam is
    # still full, and skipped rather than squeezed if the board is too small.
    built = None
    for back in (5, 6, 4, 7):
        for w in (5, 4):
            x0 = stern_x - back
            y0 = cy - 2
            b = structures.cabin(g, rng, x0, y0, skin=cabin_skin, on=("b",))
            if b.interior:
                built = b
                break
        if built:
            break
    if built:
        out.skins.update(built.skins)

    # Below decks: a real storey under the weather deck, reached by one hatch.
    # Negative base_ft is the whole trick — an upper floor and a hold are the
    # same machinery pointed opposite ways, and every distance, cover and area
    # check already folds a level's base height in.
    deck_squares = [(x, y) for x, y in g.squares() if g.get(x, y) == "b"]
    if len(deck_squares) < 12:
        return
    rows = [[VOID] * g.width for _ in range(g.height)]
    for x, y in deck_squares:
        rows[y][x] = FLOOR
    out.levels.append({"name": "Below Decks", "base_ft": -8,
                       "terrain": ["".join(r) for r in rows], "stairs": []})
    level = len(out.levels)
    hx, hy = deck_squares[rng.randrange(len(deck_squares))]
    out.stairs.append({"level": 0, "x": hx, "y": hy, "to_level": level,
                       "to_x": hx, "to_y": hy, "kind": "companionway"})


#: A sea ship's FREEBOARD: how far her weather deck stands above the water.
#:
#: A real fact, not a drawing. Without it the deck and the sea were the same
#: height, so a caravel's whole hull was underwater and the board showed a rail
#: lying flat on the ocean — the one archetype where the geometry said nothing
#: at all. It is stored as ordinary elevation, which every distance, reach,
#: cover and area check already folds in, so hauling somebody up out of the
#: water is six feet of climb in the rules exactly as it is in the picture.
#: A skyship needs none: what it hangs over is a hole, and a hole already
#: gives a floor its whole side.
SHIP_FREEBOARD_FT = 6


def _gen_ship(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    stern = _hull(g, rng, "W")
    _rail(g, rng, "W")
    _rig_ship(g, rng, out, stern)
    _scatter(g, rng, "o", 0.05, only_on=("b",))
    _scatter(g, rng, "%", 0.02, only_on=("b",))
    _connect_regions(g, rng)
    for x, y in g.squares():
        if g.get(x, y) not in ("W", "~"):
            out.elevation[f"{x},{y}"] = SHIP_FREEBOARD_FT
        # NO SWELL, and it was tried. The sea round a caravel is a perfectly
        # flat plane, the depth map carries nothing about it, and the painter
        # keeps the terrain image's flat colour — so the reef's lesson (give the
        # depth map relief and the picture follows) looks like it should apply.
        # It does not: elevation is stored PER SQUARE and drawn flat-topped, so
        # three feet of swell came back as a field of terraced slabs reading as
        # broken ice floes. Same shape as the cliff that was a stack of boxes —
        # water needs a surface the board has no way to express. Left flat
        # deliberately; the fix, if there is one, is a renderer that can round a
        # water top, not a number here.
    out.description = ("the deck of a ship under sail — a single mast stepped "
                       "amidships, a rail you can see the sea through, the "
                       "captain's cabin aft and a hatch down into the hold")


def _gen_arena(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, WALL)
    cx, cy = (g.width - 1) / 2, (g.height - 1) / 2
    rx, ry = g.width / 2 - 1, g.height / 2 - 1
    for x, y in g.squares():
        if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0:
            g.set(x, y, "s")
    for _ in range(rng.randint(2, 5)):
        px = rng.randrange(2, g.width - 2)
        py = rng.randrange(2, g.height - 2)
        if g.get(px, py) == "s":
            g.set(px, py, rng.choice(("O", "o", ",")))
    # The tiers are the whole point of a PIT, and the board had none: the sand
    # sat at the same height as the stone around it, so "pit" was a word in the
    # description. The ring inside the wall is raised a full ledge, which makes
    # the floor a place you drop INTO and have to climb out of.
    for x, y in list(g.squares()):
        if g.get(x, y) != "s":
            continue
        d = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2
        if d > 0.72:
            _raise(out, [(x, y)], LEDGE_FT if d > 0.86 else STEP_FT)
    out.lighting = "bright"
    out.description = ("a sand-floored fighting pit sunk below stone tiers, "
                       "the crowd looking down into it")


def _gen_crypt(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, WALL)
    _room(g, 1, 1, g.width - 2, g.height - 2)
    # Rows of sarcophagi make aisles — a fight of angles and corners.
    for y in range(3, g.height - 3, 3):
        for x in range(3, g.width - 3, 2):
            if rng.random() < 0.75:
                g.set(x, y, "A")
    _scatter(g, rng, ",", 0.05)
    _scatter(g, rng, "%", 0.04)
    d = _door_on_wall(g, rng, 1, 1, g.width - 2, g.height - 2)
    if d:
        out.doors.append({"x": d[0], "y": d[1], "state": "closed",
                          "name": "crypt door", "dc": 15})
    out.lighting = "dark"
    out.description = "a burial vault, stone coffins in ranks, dust and cobweb"
    # The bier: the one thing in a burial vault that is meant to be looked up
    # at, and the board had it flush with the floor.
    bw = max(3, g.width // 4)
    bh = max(3, g.height // 4)
    bx = (g.width - bw) // 2
    by = (g.height - bh) // 2
    _terrace(g, out, bx, by, bx + bw, by + bh, STEP_FT, on=(FLOOR, ",", "A"))


def _gen_swamp(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, "m")
    for _ in range(int(g.width * g.height * 0.03)):
        cx, cy = rng.randrange(g.width), rng.randrange(g.height)
        _blob(g, rng, cx, cy, rng.randint(3, 9), rng.choice(("~", "~", "W")))
    for _ in range(int(g.width * g.height * 0.01)):
        cx, cy = rng.randrange(g.width), rng.randrange(g.height)
        _blob(g, rng, cx, cy, rng.randint(1, 3), "T")
    _scatter(g, rng, "g", 0.15, only_on=("m",))
    _connect_regions(g, rng)
    out.lighting = "dim"
    out.description = "black bog water between hummocks of reed and drowned trees"
    # Hummocks: dry ground a step above the mire, which is where anyone with
    # sense stands and what everyone else has to wade to.
    for _ in range(rng.randint(3, 6)):
        _mound(g, rng, out, rng.randrange(g.width), rng.randrange(g.height),
               rng.uniform(1.5, 3.0), STEP_FT, on=("g", "m", "\""))


def _gen_pass(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, "R")
    # A winding walkable pass with a drop on one side.
    y = g.height // 2
    track: list[int] = []
    for x in range(g.width):
        width = rng.randint(2, max(3, g.height // 3))
        for dy in range(-width // 2, width // 2 + 1):
            if g.in_bounds(x, y + dy):
                g.set(x, y + dy, "," if rng.random() < 0.3 else FLOOR)
        y = max(2, min(g.height - 3, y + rng.choice((-1, 0, 0, 1))))
        track.append(y)

    # THE DROP: one ravine running alongside the track, not speckle.
    #
    # This used to punch five percent of the rock out at random, which is not
    # what a mountain does and — worse — is not what a rock MASS can survive.
    # Only the rock bordering something open is drawn, so a few hundred holes
    # scattered through it left every remaining square bordering one: the whole
    # face shattered into separate full-square blocks and the board came back a
    # field of white dice. A gorge is one continuous absence with two edges, so
    # the rock either side of it stays a face.
    below = rng.random() < 0.5
    edge = 0
    for x in range(g.width):
        edge = max(2, min(6, edge + rng.choice((-1, 0, 0, 1))))
        depth = rng.randint(1, 3)
        for d in range(depth):
            yy = track[x] + edge + d if below else track[x] - edge - d
            if g.in_bounds(x, yy) and g.get(x, yy) == "R":
                g.set(x, yy, "x")
    _connect_regions(g, rng)
    # Boulders fallen into the track. They wear the BOULDER skin and not the
    # cliff's: both are the same granite, and a mass wants to fill its square
    # so its neighbours merge while a stone lying alone wants a silhouette.
    _scatter(g, rng, "O", 0.035, only_on=(FLOOR, ","))
    out.elevation = {f"{x},{yy}": 10 for x, yy in g.squares()
                     if g.get(x, yy) == "," and rng.random() < 0.2}
    out.description = ("a high pass cut between raw granite cliffs — fractured "
                       "rock faces, fallen boulders, loose scree underfoot")
    # A pass HAS levels — that is what a pass is, a track cut across a slope —
    # and the board had ten raised squares on it. The whole track is stepped
    # now: benches of rock at ten and twenty feet with cliff faces between them
    # and ramps of scree where you can get up.
    _plateaus(g, rng, out, tiers=3, on=(".", ",", "g"), face="R", ramps=2)
    out.description += (", the track stepping up in benches of rock with "
                        "cliffs between them")


def _gen_sewer(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, WALL)
    mid = g.height // 2
    channel = max(2, g.height // 4)
    g.fill_rect(1, mid - channel, g.width - 2, mid + channel, FLOOR)
    g.fill_rect(1, mid - channel // 2, g.width - 2, mid + channel // 2, "~")
    for x in range(2, g.width - 2, max(3, g.width // 5)):
        g.set(x, mid, "b")
    _scatter(g, rng, ",", 0.06, only_on=(FLOOR,))
    _scatter(g, rng, "%", 0.03, only_on=(FLOOR,))
    # Silted-up stretches where the channel has choked. Mud, not water: it costs
    # the same movement and it is the honest reason a sewer smells.
    for _ in range(rng.randint(1, 3)):
        _blob(g, rng, rng.randrange(2, max(3, g.width - 2)), mid,
              rng.randint(2, 6), "m")
    out.lighting = "dark"
    out.description = ("a vaulted sewer tunnel — slime-blackened brick to the "
                       "tide line, weeping mortar, ledges either side of a slow "
                       "channel of green filth")
    # The ledges are WALKWAYS: they stand above the channel, which is the whole
    # reason to be on one. Drawn flush they were just dry floor.
    _raise(out, [(x, y) for x, y in g.squares()
                 if g.get(x, y) in (FLOOR, ",")], STEP_FT)


#: How far a reef channel is cut below the shelf, in feet.
#:
#: Ten, because that is a real drop: it puts a creature in the channel below the
#: lip for sight and cover, costs a climb to leave, and — since elevation is
#: drawn — gives the depth map something to be. It is also why a channel is
#: worth crossing rather than a strip of differently-coloured floor.
REEF_CHANNEL_FT = -10


def _carve_channel(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    """Cut one winding channel clean across the shelf, and record its depth.

    A random WALK rather than a scatter of blobs: a channel is a thing water
    cut, so it runs from one side of the board to the other and a swimmer can
    follow it. Blobs made ponds, which is the shape the whole reef used to have.
    """
    across = rng.random() < 0.5
    span, other = (g.width, g.height) if across else (g.height, g.width)
    pos = rng.randrange(other // 4, max(other // 4 + 1, 3 * other // 4))
    # Three to five squares wide. Wider was measured and it eats the board: at
    # half up to 3, two channels took a third of the shelf, and a reef that is
    # mostly channel is the flat plain again with the colours swapped.
    half = rng.choice((1, 1, 2))
    for step in range(span):
        pos = max(1, min(other - 2, pos + rng.choice((-1, 0, 0, 1))))
        if rng.random() < 0.12:
            half = max(1, min(2, half + rng.choice((-1, 1))))
        for d in range(-half, half + 1):
            x, y = (step, pos + d) if across else (pos + d, step)
            if not g.in_bounds(x, y):
                continue
            g.set(x, y, "W")
            out.elevation[f"{x},{y}"] = REEF_CHANNEL_FT


def _gen_reef(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    """Underwater: a coral SHELF, cut by channels and studded with heads.

    It used to be seventy-three percent featureless shallow water with two
    percent coral in it, and that was wrong twice over. Tactically it is a plain
    — no cover, no lanes, nothing to decide — on the archetype whose own
    description promises "sight lines die at twenty feet". And visually a board
    that flat gives the depth map nothing at all to carry, so the painted layer
    fell back on its prior for a flat green expanse and returned a POND, which
    no amount of conditioning could argue it out of.

    So: a sand shelf with silt and weed over it, coral heads standing in
    clusters, and channels cut ten feet down through the lot of them. The relief
    is the point on both sides of the wire — it is cover and a climb to the
    rules, and it is the only thing the depth map can say to the painter.
    """
    g.fill_rect(0, 0, g.width - 1, g.height - 1, "s")
    # Coral first, channels second: the water cut THROUGH the reef, and a
    # channel that stops politely at a coral head is a path, not a channel.
    for _ in range(int(g.width * g.height * 0.03)):
        cx, cy = rng.randrange(g.width), rng.randrange(g.height)
        _blob(g, rng, cx, cy, rng.randint(2, 7), "R")      # coral heads
    for _ in range(rng.randint(2, 3)):
        _carve_channel(g, rng, out)
    # Silt and weed over the shelf: difficult going, and never in the channels,
    # which are open water.
    _scatter(g, rng, "~", 0.16, only_on=("s",), mode="swim")

    # A drowned ruin. Columns on a seabed should already have FALLEN — an intact
    # colonnade underwater is a stranger sight than a broken one, and the board
    # had no way to say so until skins could. The layout is a proper rectangle
    # of stubs rather than scattered pillars, because what makes a ruin read as
    # architecture is that it was once regular.
    if g.width >= 14 and g.height >= 12 and rng.random() < 0.8:
        rw = rng.randint(5, max(6, g.width // 2))
        rh = rng.randint(4, max(5, g.height // 2))
        rx = rng.randrange(1, max(2, g.width - rw - 1))
        ry = rng.randrange(1, max(2, g.height - rh - 1))
        for x in range(rx, rx + rw + 1):
            for y in range(ry, ry + rh + 1):
                if not g.in_bounds(x, y) or g.get(x, y) not in ("~", "s"):
                    continue
                on_x = x in (rx, rx + rw)
                on_y = y in (ry, ry + rh)
                if not (on_x or on_y):
                    continue
                # A colonnade on a REGULAR pitch, because what makes a ruin
                # read as architecture is that it was once regular — scattered
                # at random it is indistinguishable from the coral heads
                # already on the board.
                pitched = ((y - ry) % 2 == 0) if on_x else ((x - rx) % 2 == 0)
                if pitched:
                    g.set(x, y, "O")            # a column, snapped off
                    out.skins[f"{x},{y}"] = "drowned-column"
                elif rng.random() < 0.55:
                    g.set(x, y, "w")            # the wall worn down to a stub
                    out.skins[f"{x},{y}"] = "drowned-wall"
    _scatter(g, rng, "O", 0.015, only_on=("~", "s"), mode="swim")
    out.mode = "swim"
    out.lighting = "dim"
    out.description = ("a sunlit coral shelf under the sea — pale sand flats "
                       "and weed, coral heads taller than a man standing in "
                       "banks, deep blue channels cut ten feet down through "
                       "the reef, and the snapped columns of a drowned ruin")


def _gen_open_water(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    """Open sea: blue water in every direction, with wreckage to shelter behind."""
    g.fill_rect(0, 0, g.width - 1, g.height - 1, "W")
    for _ in range(rng.randint(3, 7)):
        cx, cy = rng.randrange(g.width), rng.randrange(g.height)
        _blob(g, rng, cx, cy, rng.randint(3, 10), "~")     # kelp / shallows
    # Drifting wreckage: something to stand on, something to hide behind.
    for _ in range(rng.randint(2, 5)):
        cx, cy = rng.randrange(1, g.width - 1), rng.randrange(1, g.height - 1)
        g.set(cx, cy, "b")
        for dx, dy in ((1, 0), (0, 1), (1, 1)):
            if rng.random() < 0.6:
                g.set(cx + dx, cy + dy, rng.choice(("b", "o")))
    out.mode = "swim"
    out.lighting = "dim"
    out.description = ("open water — drifting wreckage and ribbons of kelp, "
                       "blue falling away to black below")


def _gen_sky(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    """Aloft: floating stone islands in open air. Only a flier (or a very good
    jumper) crosses between them; the islands themselves are solid ground."""
    g.fill_rect(0, 0, g.width - 1, g.height - 1, "^")
    islands = rng.randint(3, 5)
    centres: list[tuple[int, int]] = []
    for i in range(islands):
        # Kept APART. Random centres cluster, and three islands that overlap
        # are one continent with a ragged edge — which is a normal board with
        # a lot of wasted margin, not a fight in open air. The whole point of
        # the archetype is the gap between them.
        cx = cy = 0
        for _try in range(40):
            cx = rng.randrange(2, max(3, g.width - 2))
            cy = rng.randrange(2, max(3, g.height - 2))
            if all(math.hypot(cx - ox, cy - oy) >= 9 for ox, oy in centres):
                break
        centres.append((cx, cy))
        # Big enough to FIGHT on, and SOLID. The old sizes drew a scatter of
        # two- and three-square specks, which is a board where nobody can stand
        # next to anybody — and drawn at that size they read as pixels on a map
        # rather than as stones hanging in the air.
        _island(g, rng, cx, cy, rng.uniform(2.6, 4.6), "g")
        if rng.random() < 0.5:
            g.set(cx, cy, "R")                              # a spire of rock
        # Islands hang at different heights — the fight has a third axis.
        height = rng.choice((0, 0, 10, 20))
        if height:
            for x, y in g.squares():
                if g.get(x, y) == "g" and abs(x - cx) <= 3 and abs(y - cy) <= 3:
                    out.elevation[f"{x},{y}"] = height
    _scatter(g, rng, ",", 0.06, only_on=("g",), mode="fly")
    _scatter(g, rng, "T", 0.03, only_on=("g",), mode="fly")
    out.mode = "fly"
    out.lighting = "bright"
    out.description = ("islands of broken stone hanging in open sky, roots "
                       "trailing into cloud beneath them")


def _gen_skyship(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    """Aloft: the deck of a flying ship, with nothing at all past the rail.

    A skyship is the one board whose style is a genuine CHOICE rather than a
    material fact. A flying vessel can be a timber ship that happens to fly, a
    riveted brass-and-steam contraption, or something grown rather than built —
    and all three are right, so the generator picks one from the seed and
    records it. Everything downstream (materials, silhouettes, what the painter
    is told) follows from that one word.
    """
    # A style asked for wins; otherwise the seed decides, so a table's ship
    # stays the ship it was.
    if out.style not in SKYSHIP_STYLES:
        out.style = rng.choice(("timber", "timber", "steampunk", "organic"))
    stern = _hull(g, rng, "^", plan="sky")
    _rail(g, rng, "^")
    _rig_ship(g, rng, out, stern,
              cabin_skin={"steampunk": "plating",
                          "organic": "chitin"}.get(out.style, "hull"))
    _scatter(g, rng, "o", 0.05, only_on=("b",), mode="fly")
    out.mode = "fly"
    out.lighting = "bright"
    out.description = {
        "steampunk": ("the deck of a skyship — a riveted brass-and-iron "
                      "contraption, boiler venting, pipework along the rails, "
                      "open air past every side"),
        "organic": ("the deck of a skyship that was GROWN rather than built — "
                    "ridged chitin underfoot, veined and iridescent, open air "
                    "past every side"),
    }.get(out.style,
          "the deck of a skyship under sail, rigging taut, open air past "
          "every rail")


def _gen_open(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    """The fallback: open ground with just enough cover to make choices matter."""
    g.fill_rect(0, 0, g.width - 1, g.height - 1, "g")
    _scatter(g, rng, ",", 0.05, only_on=("g",))
    _scatter(g, rng, "o", 0.02, only_on=("g",))
    for _ in range(rng.randint(1, 4)):
        cx, cy = rng.randrange(g.width), rng.randrange(g.height)
        _blob(g, rng, cx, cy, rng.randint(1, 3), rng.choice(("T", "R", "\"")))
    _connect_regions(g, rng)
    out.description = "open ground, scattered rocks and scrub"
    # Sometimes the whole field is stepped — a run of low mesas with rock
    # between them — and otherwise it gets a knoll. Both beat a table top, and
    # a board that is ALWAYS terraced stops being a thing you notice.
    if rng.random() < 0.3:
        _plateaus(g, rng, out, tiers=2, on=("g", ",", "\""), face="R", ramps=2)
        out.description = ("open ground stepping up in low mesas, rock faces "
                           "between them")
    elif rng.random() < 0.8:
        _mound(g, rng, out, rng.randrange(g.width // 4, 3 * g.width // 4),
               rng.randrange(g.height // 4, 3 * g.height // 4),
               rng.uniform(2.5, 4.5), LEDGE_FT, on=("g", ",", "\""))
        out.description = "open ground rising to a knoll, scattered rocks and scrub"


def _gen_terraces(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    """Stacked plateaus: three or four floors of ground with cliffs between.

    The board every other archetype only hints at. Height here is not a feature
    ON the map, it is the SHAPE of the map: each tier is a place to hold, the
    faces between them are impassable rock, and the ramps are the two or three
    squares everyone is going to fight over. A creature on the top tier is
    twenty or thirty feet above the bottom one, which every distance, cover and
    spell-area check on this board already folds in.
    """
    g.fill_rect(0, 0, g.width - 1, g.height - 1, "g")
    _scatter(g, rng, ",", 0.10, only_on=("g",))
    _plateaus(g, rng, out, tiers=rng.choice((3, 3, 4)), on=("g", ","),
              face="R", ramps=rng.choice((2, 2, 3)))
    # Scrub and fallen rock on the flats, which is what makes a terrace a place
    # rather than a step in a diagram.
    _scatter(g, rng, "\"", 0.05, only_on=("g",))
    _scatter(g, rng, "o", 0.02, only_on=("g", ","))
    for _ in range(rng.randint(1, 3)):
        cx, cy = rng.randrange(g.width), rng.randrange(g.height)
        _blob(g, rng, cx, cy, rng.randint(1, 4), "R")
    _connect_regions(g, rng)
    out.description = ("stacked plateaus of dry rock — flats of scree and "
                       "scrub at three heights, sheer faces between them, and "
                       "ramps where the rock has fallen away")


#: archetype -> generator. Keep the keys stable: they're persisted on the map row.
ARCHETYPES: dict[str, Callable[[Grid, random.Random, GeneratedMap], None]] = {
    "dungeon-room": _gen_dungeon_room,
    "dungeon-complex": _gen_dungeon_complex,
    "cave": _gen_cave,
    "forest": _gen_forest,
    "clearing": _gen_clearing,
    "street": _gen_street,
    "tavern": _gen_tavern,
    "bridge": _gen_bridge,
    "ruins": _gen_ruins,
    "camp": _gen_camp,
    "ship": _gen_ship,
    "arena": _gen_arena,
    "crypt": _gen_crypt,
    "swamp": _gen_swamp,
    "mountain-pass": _gen_pass,
    "sewer": _gen_sewer,
    "reef": _gen_reef,
    "open-water": _gen_open_water,
    "sky-islands": _gen_sky,
    "skyship": _gen_skyship,
    "open": _gen_open,
    "terraces": _gen_terraces,
}


#: The words that say a fight is not on dry ground. Consulted FIRST, and ahead
#: of anything built, because a medium is not scenery: it decides who can be
#: there at all, what a weapon does, and how everything moves. An underwater
#: ruin is fought swimming, whatever the ruin is made of — so the sea wins the
#: argument with the architecture, where the architecture wins it with the
#: country.
#:
#: It also settles the collision the tiers below would otherwise create:
#: matching is by substring and "shipwreck" contains "ship", so a wreck named
#: with no other clue would be fought on a ship's DECK. Reading the sea first
#: puts it on the seabed, which is where it lies.
_MEDIUM: tuple[tuple[tuple[str, ...], str], ...] = (
    (("skyship", "airship", "flying ship", "sky ship"), "skyship"),
    (("sky island", "floating island", "cloud", "aloft", "midair", "mid-air",
      "open sky", "in the air"), "sky-islands"),
    (("reef", "coral", "seabed", "sea floor", "seafloor", "lagoon",
      "underwater", "under the sea"), "reef"),
    (("open water", "open sea", "ocean", "the deep", "shipwreck", "sunken"),
     "open-water"),
)

#: Loose words for a STRUCTURE — something built, which a fight is fought
#: inside or around. First match wins, so specific phrases come before generic
#: ones.
#:
#: Consulted BEFORE the settings below, and that order is the whole point. A DM
#: writing "an overgrown temple in the jungle" has named two things, and the one
#: that decides the layout is the temple: the jungle is already carried by the
#: place's biome, by the skins and by what the painter is told, while nothing
#: else in the chain can put walls, terraces or a plaza on the board. Read the
#: other way round — which is what one flat list did, since "jungle" happened to
#: sit above "temple" in it — the board came back a plain patch of forest and
#: the building the narration promised existed nowhere.
_STRUCTURES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("tavern", "inn", "taproom", "alehouse", "common room"), "tavern"),
    (("crypt", "tomb", "burial", "sarcoph", "mausoleum", "catacomb"), "crypt"),
    (("sewer", "drain", "culvert"), "sewer"),
    (("arena", "pit fight", "colosseum", "duelling ground"), "arena"),
    (("camp", "tents", "bivouac", "encampment"), "camp"),
    (("ruin", "rubble", "toppled", "abandoned temple", "overgrown temple",
      "jungle temple", "lost temple", "temple ruins", "ziggurat"), "ruins"),
    (("street", "alley", "market", "plaza", "town square", "city"), "street"),
    (("bridge", "span"), "bridge"),
    (("ship", "deck", "boat", "galley", "vessel"), "ship"),
    (("dungeon", "corridor", "vault", "keep", "castle", "hall", "temple",
      "chamber", "room"), "dungeon-room"),
)

#: Loose words for the COUNTRY a fight happens in. Consulted when nothing built
#: was named — and it usually is what a DM names, so this is not the poor
#: relation the ordering makes it look.
_SETTINGS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("chasm", "ravine", "gorge"), "bridge"),
    (("swamp", "bog", "marsh", "fen", "mire"), "swamp"),
    (("cave", "cavern", "grotto", "tunnel", "underdark"), "cave"),
    (("terrace", "plateau", "mesa", "escarpment", "stepped", "quarry",
      "tiers of rock"), "terraces"),
    (("pass", "mountain", "cliff", "ledge", "scree"), "mountain-pass"),
    (("forest", "wood", "grove", "thicket", "jungle"), "forest"),
    (("clearing", "glade", "meadow"), "clearing"),
    (("field", "plain", "road", "hill", "moor", "desert", "tundra"), "open"),
)


def archetype_for(text: Optional[str], default: str = "open") -> str:
    """Pick the closest layout family for a scrap of DM language.

    Medium beats architecture beats country — see :data:`_MEDIUM`.
    """
    t = (text or "").strip().lower()
    if not t:
        return default
    if t in ARCHETYPES:
        return t
    for table in (_MEDIUM, _STRUCTURES, _SETTINGS):
        for words, arch in table:
            if any(w in t for w in words):
                return arch
    return default


#: Terrain -> the layout family that terrain fights on. Keyed by the world
#: graph's biome vocabulary (see ``eight_card_system/placelore.py``), but kept
#: here as plain strings so ``vtt`` stays standalone — the arena uses these
#: generators with no world graph behind them at all.
_BIOME_ARCHETYPE = {
    "forest": "forest", "swamp": "swamp", "mountains": "mountain-pass",
    "sea": "open-water", "coast": "open", "river": "open",
    "hills": "open", "farmland": "open", "desert": "open",
    # built surfaces
    "urban": "street", "interior": "tavern",
    "dungeon": "dungeon-room", "underdark": "cave",
}


def archetype_for_place(*, hint: Optional[str] = None, biome: Optional[str] = None,
                        scale: Optional[str] = None, default: str = "open") -> str:
    """The layout family for a fight breaking out at a known place.

    Precedence is deliberate:

    1. an explicit ``hint`` — the DM said "a smoky taproom", and the fiction is
       the DM's to author;
    2. the place's ``biome`` — what the world graph says the ground actually is.
       This is the one that stops a brawl in The Grey Tors being fought on a
       featureless plain just because the name contains no keyword;
    3. the place's ``scale`` word, for interiors the biome doesn't cover;
    4. ``default``.

    Note the hint is only honoured when it MATCHES something. A place name is
    usually passed as the hint, and most names ("Millbrook") key nothing — that
    is exactly when the biome should decide, so an unmatched hint falls through
    rather than pinning the default.
    """
    if hint:
        hit = archetype_for(hint, default="")
        if hit:
            return hit
    b = (biome or "").strip().lower()
    if b in _BIOME_ARCHETYPE:
        return _BIOME_ARCHETYPE[b]
    if b:
        hit = archetype_for(b, default="")
        if hit:
            return hit
    s = (scale or "").strip().lower()
    if s in _BIOME_ARCHETYPE:
        return _BIOME_ARCHETYPE[s]
    if s:
        hit = archetype_for(s, default="")
        if hit:
            return hit
    return default


#: Which landmarks an archetype may stand, best first.
#:
#: NAMED per archetype rather than offered as a general "prefab anything"
#: facility, for the reason :mod:`vtt.setpieces` gives: the LLM decides fiction
#: and the code decides mechanics, so a DM saying a ruined temple stands here
#: is fiction and choosing its footprint and where it goes is not.
#:
#: A slug whose mesh was never collected is harmless and deliberately left in —
#: the piece still stamps its tiles, so it is a real obstacle with real cover,
#: and it simply draws from the board's own geometry until somebody unzips the
#: pack. That is the same degradation ``source=None`` gets on purpose.
_SETPIECES: dict[str, tuple[str, ...]] = {
    "forest": ("jungle-giant", "boulder-heap"),
    "clearing": ("jungle-giant", "standing-stone"),
    "swamp": ("jungle-giant", "ruined-wall"),
    "ruins": ("step-pyramid", "great-statue", "ruined-arch", "broken-pillar",
              "ruined-wall"),
    "crypt": ("mausoleum", "broken-pillar"),
    "cave": ("cave-pillar", "boulder-heap"),
    "mountain-pass": ("boulder-heap", "standing-stone"),
    "terraces": ("standing-stone", "boulder-heap", "ruined-arch"),
    "street": ("village-fountain", "gatehouse-tower"),
    "camp": ("standing-stone", "boulder-heap"),
    "arena": ("great-statue", "temple-plinth"),
    "bridge": ("gatehouse-tower",),
    # "open" is deliberately ABSENT. It is the fallback archetype — what a
    # board is when nothing is known about it, including when a generator
    # collapsed — so giving it landmarks makes the default board grow to hold
    # scenery nobody asked for. The selftest caught exactly that. A featureless
    # plain is what "open" means; "clearing" is the outdoor board with things
    # on it.
    "reef": ("shipwreck",),
    "open-water": ("shipwreck",),
}

#: Roughly how much of a board may be landmark, as a fraction of its squares.
#:
#: A cap rather than a count, because the pieces differ enormously — a jungle
#: giant reserves eighty-one squares and a broken pillar one — and "two
#: landmarks" means something completely different on each. Deliberately small:
#: a set piece is something a fight happens AROUND, and a board that is mostly
#: scenery has nowhere left to fight.
SETPIECE_BUDGET = 0.14


def setpiece_area_for(archetype: str,
                      asked: Sequence[str] = ()) -> int:
    """Squares this board's landmarks would like, before a board exists.

    Read by :func:`vtt.triggers.board_size_for`, which has to size the board
    BEFORE anything is generated on it. Only the biggest is counted rather than
    the whole pool: ``_place_setpieces`` spends against a budget and stops, so
    summing the pool would size every forest for a tree AND a boulder field
    when it will only ever get one of each on a small board — and the largest
    piece is the one that actually cannot be fitted otherwise.

    ``asked`` is what the DM named, which is counted the same way and for a
    stronger reason: a landmark the fiction has already promised the table is
    the one that must not be dropped for want of room.
    """
    from . import setpieces as _sp

    best = 0
    for slug in list(asked) + list(_SETPIECES.get(
            (archetype or "").strip().lower()) or ()):
        piece = _sp.piece(slug)
        if piece is not None:
            best = max(best, piece.width * piece.depth)
    return best


def _place_setpieces(grid: Grid, rng: random.Random, out: GeneratedMap,
                     asked: Sequence[str] = ()) -> None:
    """Stand this board's landmarks on it, within budget.

    Runs after connectivity so a landmark cannot sever the board, and before
    the spawn zones so nobody is dropped inside one. ``fits`` demands a clear
    square of margin all round, which is also what keeps landmarks out of
    corridors — a one-square passage has walls in the margin ring and is
    refused.

    ``asked`` is what the DM's ``landmark=`` named. It is placed FIRST, is
    exempt from the archetype's pool (a ziggurat in a forest is a fair thing to
    narrate, and the pool is a default rather than a permission), and is exempt
    from the coin flip below — a landmark someone asked for by name is not a
    surprise to be rationed.
    """
    from . import setpieces as _sp

    asked = [s for s in dict.fromkeys(asked) if _sp.piece(s) is not None]
    pool = [s for s in (_SETPIECES.get(out.archetype) or ())
            if s not in asked]
    if not asked and not pool:
        return
    budget = int(grid.width * grid.height * SETPIECE_BUDGET)
    # The board was sized for what the DM asked for (`setpiece_area_for` counts
    # it), so a budget that then refuses it would be the two halves of one
    # decision disagreeing. The asked-for pieces raise the ceiling; everything
    # after them still spends against it.
    for slug in asked:
        pc = _sp.piece(slug)
        budget = max(budget, pc.width * pc.depth)
    if budget < 1:
        return
    want: list[str] = list(asked)
    spent = sum(_sp.piece(s).width * _sp.piece(s).depth for s in asked)
    for slug in pool:
        piece = _sp.piece(slug)
        if piece is None:
            continue
        cost = piece.width * piece.depth
        # A landmark bigger than the whole budget is not shrunk to fit; it is
        # simply not this board's landmark. The alternative is scaling it, and
        # its height is a stated fact the rules read.
        if spent + cost > budget:
            continue
        # Every board getting every landmark it may have makes each one
        # furniture. Half of them, from the seed, keeps a colossus rare enough
        # to be worth narrating.
        if want and rng.random() < 0.5:
            continue
        want.append(slug)
        spent += cost
    if not want:
        return
    for placed in _sp.setpieces_for(grid, want, seed=out.seed, mode=out.mode,
                                    clear=asked):
        rec = {"slug": placed.slug, "x": placed.x,
               "y": placed.y, "yaw": placed.yaw}
        # A piece the DM INVENTED carries its name in the record, and it has to:
        # the ad-hoc register is process-local, so a board read back in a fresh
        # process would find the slug unknown and quietly drop the landmark it
        # was built around. The name is the whole seed — `named_feature` is
        # deterministic, so the same phrase rebuilds the same piece — which is
        # why nothing else about it needs storing.
        pc = _sp.piece(placed.slug)
        if pc is not None and placed.slug.startswith("feature-"):
            rec["name"] = pc.name
        out.setpieces.append(rec)
        out.skins.update(placed.skins)
        out.elevation.update(placed.elevation)


def generate_map(archetype: str = "open", *, width: int = 20, height: int = 15,
                 seed: Optional[int] = None,
                 lighting: Optional[str] = None,
                 style: str = "", biome: str = "",
                 landmarks: Sequence[str] = ()) -> GeneratedMap:
    """Build a board. The same ``(archetype, width, height, seed, style)``
    always produces the identical grid — so a map can be regenerated from its
    row.

    ``style`` asks for a particular flavour where the archetype offers a real
    choice rather than a material fact (today: a skyship is timber, steampunk
    or grown). Left empty the generator picks from the seed, which is what
    every caller written before styles existed does.

    ``landmarks`` are catalogue slugs the caller wants standing here — the DM
    narrated them, so they are placed before the archetype's own pool and
    without the rationing that keeps a colossus rare on boards nobody asked for
    one. They are still placed by the code, and a piece that cannot be fitted
    is simply absent: the fiction chooses what, the board chooses where.
    """
    archetype = archetype if archetype in ARCHETYPES else archetype_for(archetype)
    width = max(8, min(60, int(width)))
    height = max(8, min(60, int(height)))
    seed = random.randint(1, 2**31 - 1) if seed is None else int(seed)
    rng = _rng(seed)
    grid = Grid.blank(width, height)
    out = GeneratedMap(grid=grid, archetype=archetype, seed=seed, style=style,
                       biome=biome)
    ARCHETYPES[archetype](grid, rng, out)

    # Every board must be one connected space and have room to stand — judged in
    # the medium it's fought in, so an open-water board isn't condemned for
    # being unwalkable.
    _connect_regions(grid, rng, out.mode)
    if len(_walkable(grid, out.mode)) < (width * height) // 8:
        # A generator collapsed (rare corner of the CA); fall back to open ground
        # rather than handing the table an unplayable board.
        grid = Grid.blank(width, height)
        out = GeneratedMap(grid=grid, archetype=archetype, seed=seed,
                           biome=biome)
        _gen_open(grid, rng, out)

    # A skin recorded by a generator can outlive the square it described: the
    # connectivity net runs afterwards and will carve a corridor straight
    # through a tent wall if it has to, leaving a canvas WALL skin sitting on
    # what is now open floor — drawn solid, over a square the rules let you
    # walk across. The board must never show a way through that is not there,
    # nor block one that is, so the last thing that touches the grid drops any
    # skin the grid has outgrown. A safety net for every future generator, in
    # the same spirit as _connect_regions itself.
    if out.skins:
        from .terrain import tile as _tile
        out.skins = {
            key: name for key, name in out.skins.items()
            if not (_tile(grid.get(*(int(v) for v in key.split(",")))
                          ).move_cost_ft and _skins.occludes_floor(name))
        }

    # Landmarks last, so they stand on a board already proved connected — and
    # before the spawn zones, so nobody starts inside one.
    _place_setpieces(grid, rng, out, asked=landmarks)

    party, foes = _opposed_zones(grid, rng, out.mode)
    out.spawn_party, out.spawn_foes = party, foes
    if lighting:
        out.lighting = lighting
    if not out.description:
        out.description = "open ground"
    return out
