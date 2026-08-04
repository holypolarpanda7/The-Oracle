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
from typing import Callable, Optional

from .terrain import FLOOR, VOID, WALL, Grid

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


def _regions(grid: Grid, mode: str = "walk") -> list[set[Square]]:
    """Connected traversable regions (4-way — diagonal-only links don't count as
    a corridor a Large creature could use)."""
    seen: set[Square] = set()
    out: list[set[Square]] = []
    for sq in _walkable(grid, mode):
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
                if grid.in_bounds(nx, ny) and grid.passable(nx, ny, mode=mode):
                    seen.add((nx, ny))
                    stack.append((nx, ny))
        out.append(region)
    return sorted(out, key=len, reverse=True)


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
             keep_passable: bool = True, mode: str = "walk") -> None:
    """Sprinkle a tile across the floor without cutting the map in half."""
    for x, y in list(grid.squares()):
        if grid.get(x, y) not in only_on or rng.random() > chance:
            continue
        prev = grid.get(x, y)
        grid.set(x, y, code)
        if keep_passable and not grid.passable(x, y, mode=mode) and len(
                _regions(grid, mode)) > 1:
            grid.set(x, y, prev)


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
    out.lighting = rng.choice(["dim", "dim", "bright"])
    out.description = "a pillared stone chamber, flagstones cracked with age"


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
    for r in rooms[1:]:
        if rng.random() < 0.5:
            d = _door_on_wall(g, rng, *r)
            if d:
                out.doors.append({"x": d[0], "y": d[1], "state": "closed",
                                  "name": "door", "dc": None})
    _scatter(g, rng, ",", 0.05)
    _scatter(g, rng, "o", 0.02)
    out.lighting = "dim"
    out.description = "a warren of stone rooms joined by narrow corridors"


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


def _gen_forest(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, "g")
    for _ in range(int(g.width * g.height * 0.02)):
        cx, cy = rng.randrange(g.width), rng.randrange(g.height)
        _blob(g, rng, cx, cy, rng.randint(1, 4), "T")
    _scatter(g, rng, "\"", 0.12, only_on=("g",))
    # A stream cutting across, with a ford or a fallen log to cross by.
    if rng.random() < 0.6:
        sx = rng.randrange(2, max(3, g.width - 2))
        y = 0
        while y < g.height:
            g.set(sx, y, "~")
            g.set(min(g.width - 1, sx + 1), y, "~")
            sx = max(1, min(g.width - 2, sx + rng.choice((-1, 0, 0, 1))))
            y += 1
        bridge_y = rng.randrange(1, g.height - 1)
        for x in range(g.width):
            if g.get(x, bridge_y) == "~":
                g.set(x, bridge_y, "b")
    _connect_regions(g, rng)
    out.lighting = rng.choice(["bright", "dim"])
    out.description = "old woodland — thick trunks, tangled undergrowth, a shallow stream"


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


def _gen_tavern(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, WALL)
    _room(g, 0, 0, g.width - 1, g.height - 1)
    # A bar along one wall, tables through the room, a hearth at the back.
    bar_y = 1 if rng.random() < 0.5 else g.height - 2
    for x in range(2, g.width - 2):
        g.set(x, bar_y, "n")
    for _ in range(rng.randint(3, 6)):
        tx = rng.randrange(2, g.width - 2)
        ty = rng.randrange(2, g.height - 2)
        if g.get(tx, ty) == FLOOR:
            g.set(tx, ty, "n")
            if rng.random() < 0.5 and g.in_bounds(tx + 1, ty):
                g.set(tx + 1, ty, "n")
    hx, hy = g.width - 2, g.height // 2
    g.set(hx, hy, "f")
    out.effects.append({"kind": "light", "name": "hearth", "shape": "sphere",
                        "x": hx, "y": hy, "radius_ft": 20, "color": "#ff8c42"})
    d = _door_on_wall(g, rng, 0, 0, g.width - 1, g.height - 1, "west")
    if d:
        g.set(d[0], d[1], "/")
        out.doors.append({"x": d[0], "y": d[1], "state": "open",
                          "name": "tavern door", "dc": None})
    out.lighting = "dim"
    out.description = "a low-beamed taproom, tables and benches, a fire burning in the hearth"


def _gen_bridge(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
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
    _scatter(g, rng, "R", 0.04, only_on=(",", "g"))
    out.description = ("a rope-and-plank bridge over a black chasm" if chasm == "x"
                       else "a plank bridge over deep, cold water")
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


def _gen_camp(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, "g")
    cx, cy = g.width // 2, g.height // 2
    g.set(cx, cy, "f")
    out.effects.append({"kind": "light", "name": "campfire", "shape": "sphere",
                        "x": cx, "y": cy, "radius_ft": 20, "color": "#ffb347"})
    for _ in range(rng.randint(3, 6)):
        a = rng.random() * math.tau
        r = min(g.width, g.height) * rng.uniform(0.2, 0.4)
        tx, ty = int(cx + math.cos(a) * r), int(cy + math.sin(a) * r)
        for dx in range(2):
            for dy in range(2):
                if g.in_bounds(tx + dx, ty + dy):
                    g.set(tx + dx, ty + dy, "n")
    _scatter(g, rng, "o", 0.03, only_on=("g",))
    _scatter(g, rng, "\"", 0.05, only_on=("g",))
    out.lighting = "dim"
    out.description = "a war camp — tents in a loose ring around a guttering fire"


def _gen_ship(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, "W")
    # A hull: taper the bow and stern so it reads as a deck, not a raft.
    inset = max(1, g.height // 6)
    for y in range(g.height):
        taper = int(abs(y - (g.height - 1) / 2) / max(1, (g.height - 1) / 2) * inset * 2)
        for x in range(taper + 1, g.width - taper - 1):
            g.set(x, y, "b")
    # Railing: every deck square with open water beside it.
    for x, y in list(g.squares()):
        if g.get(x, y) != "b":
            continue
        if any(g.get(x + dx, y + dy) == "W"
               for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
            if rng.random() < 0.8:
                g.set(x, y, "w")
    mast_x = g.width // 2
    g.set(mast_x, g.height // 2, "O")
    _scatter(g, rng, "o", 0.05, only_on=("b",))
    _scatter(g, rng, "%", 0.02, only_on=("b",))
    _connect_regions(g, rng)
    out.description = "the deck of a ship — rigging, crates lashed to the rail, salt spray"


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
    out.lighting = "bright"
    out.description = "a sand-floored fighting pit ringed by stone tiers"


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


def _gen_pass(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, "R")
    # A winding walkable pass with a drop on one side.
    y = g.height // 2
    for x in range(g.width):
        width = rng.randint(2, max(3, g.height // 3))
        for dy in range(-width // 2, width // 2 + 1):
            if g.in_bounds(x, y + dy):
                g.set(x, y + dy, "," if rng.random() < 0.3 else FLOOR)
        y = max(2, min(g.height - 3, y + rng.choice((-1, 0, 0, 1))))
    for x in range(g.width):
        for yy in range(g.height):
            if g.get(x, yy) == "R" and rng.random() < 0.05:
                g.set(x, yy, "x")
    _connect_regions(g, rng)
    out.elevation = {f"{x},{yy}": 10 for x, yy in g.squares()
                     if g.get(x, yy) == "," and rng.random() < 0.2}
    out.description = "a high mountain track between rock faces, loose scree underfoot"


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
    out.lighting = "dark"
    out.description = "a vaulted sewer tunnel, ledges either side of a slow green channel"


def _gen_reef(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    """Underwater: a coral shelf. Shallows a walker can wade, channels only a
    swimmer crosses, coral heads that block both sight and line of effect."""
    g.fill_rect(0, 0, g.width - 1, g.height - 1, "~")
    for _ in range(int(g.width * g.height * 0.02)):
        cx, cy = rng.randrange(g.width), rng.randrange(g.height)
        _blob(g, rng, cx, cy, rng.randint(4, 12), "W")     # deep channels
    for _ in range(int(g.width * g.height * 0.015)):
        cx, cy = rng.randrange(g.width), rng.randrange(g.height)
        _blob(g, rng, cx, cy, rng.randint(1, 4), "R")      # coral heads
    _scatter(g, rng, "s", 0.08, only_on=("~",), mode="swim")
    _scatter(g, rng, "O", 0.02, only_on=("~",), mode="swim")
    out.mode = "swim"
    out.lighting = "dim"
    out.description = ("a sunlit coral shelf under the sea — sand flats, deep "
                       "blue channels, coral heads taller than a man")


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
    islands = rng.randint(3, 6)
    for i in range(islands):
        cx = rng.randrange(2, max(3, g.width - 2))
        cy = rng.randrange(2, max(3, g.height - 2))
        _blob(g, rng, cx, cy, rng.randint(8, 24), "g")
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
    """Aloft: the deck of a flying ship, with nothing at all past the rail."""
    g.fill_rect(0, 0, g.width - 1, g.height - 1, "^")
    inset = max(1, g.height // 6)
    for y in range(g.height):
        taper = int(abs(y - (g.height - 1) / 2) / max(1, (g.height - 1) / 2) * inset * 2)
        for x in range(taper + 1, g.width - taper - 1):
            g.set(x, y, "b")
    for x, y in list(g.squares()):
        if g.get(x, y) != "b":
            continue
        if any(g.get(x + dx, y + dy) == "^"
               for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
            if rng.random() < 0.8:
                g.set(x, y, "w")                            # the rail
    g.set(g.width // 2, g.height // 2, "O")                 # the mast
    _scatter(g, rng, "o", 0.05, only_on=("b",), mode="fly")
    out.mode = "fly"
    out.lighting = "bright"
    out.description = ("the deck of a skyship under sail, rigging taut, open "
                       "air past every rail")


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
}


#: Loose words the DM might use -> the archetype that fits. First match wins,
#: so put the specific phrases before the generic ones.
_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("skyship", "airship", "flying ship", "sky ship"), "skyship"),
    (("sky island", "floating island", "cloud", "aloft", "midair", "mid-air",
      "open sky", "in the air"), "sky-islands"),
    (("reef", "coral", "seabed", "sea floor", "seafloor", "lagoon",
      "underwater", "under the sea"), "reef"),
    (("open water", "open sea", "ocean", "the deep", "shipwreck", "sunken"),
     "open-water"),
    (("tavern", "inn", "taproom", "alehouse", "common room"), "tavern"),
    (("crypt", "tomb", "burial", "sarcoph", "mausoleum", "catacomb"), "crypt"),
    (("sewer", "drain", "culvert"), "sewer"),
    (("bridge", "span", "chasm", "ravine", "gorge"), "bridge"),
    (("ship", "deck", "boat", "galley", "vessel"), "ship"),
    (("arena", "pit fight", "colosseum", "duelling ground"), "arena"),
    (("camp", "tents", "bivouac", "encampment"), "camp"),
    (("swamp", "bog", "marsh", "fen", "mire"), "swamp"),
    (("cave", "cavern", "grotto", "tunnel", "underdark"), "cave"),
    (("ruin", "rubble", "toppled", "abandoned temple"), "ruins"),
    (("street", "alley", "market", "plaza", "town square", "city"), "street"),
    (("pass", "mountain", "cliff", "ledge", "scree"), "mountain-pass"),
    (("forest", "wood", "grove", "thicket", "jungle"), "forest"),
    (("clearing", "glade", "meadow"), "clearing"),
    (("dungeon", "corridor", "vault", "keep", "castle", "hall", "temple",
      "chamber", "room"), "dungeon-room"),
    (("field", "plain", "road", "hill", "moor", "desert", "tundra"), "open"),
)


def archetype_for(text: Optional[str], default: str = "open") -> str:
    """Pick the closest layout family for a scrap of DM language."""
    t = (text or "").strip().lower()
    if not t:
        return default
    if t in ARCHETYPES:
        return t
    for words, arch in _KEYWORDS:
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


def generate_map(archetype: str = "open", *, width: int = 20, height: int = 15,
                 seed: Optional[int] = None,
                 lighting: Optional[str] = None) -> GeneratedMap:
    """Build a board. The same ``(archetype, width, height, seed)`` always
    produces the identical grid — so a map can be regenerated from its row."""
    archetype = archetype if archetype in ARCHETYPES else archetype_for(archetype)
    width = max(8, min(60, int(width)))
    height = max(8, min(60, int(height)))
    seed = random.randint(1, 2**31 - 1) if seed is None else int(seed)
    rng = _rng(seed)
    grid = Grid.blank(width, height)
    out = GeneratedMap(grid=grid, archetype=archetype, seed=seed)
    ARCHETYPES[archetype](grid, rng, out)

    # Every board must be one connected space and have room to stand — judged in
    # the medium it's fought in, so an open-water board isn't condemned for
    # being unwalkable.
    _connect_regions(grid, rng, out.mode)
    if len(_walkable(grid, out.mode)) < (width * height) // 8:
        # A generator collapsed (rare corner of the CA); fall back to open ground
        # rather than handing the table an unplayable board.
        grid = Grid.blank(width, height)
        out = GeneratedMap(grid=grid, archetype=archetype, seed=seed)
        _gen_open(grid, rng, out)

    party, foes = _opposed_zones(grid, rng, out.mode)
    out.spawn_party, out.spawn_foes = party, foes
    if lighting:
        out.lighting = lighting
    if not out.description:
        out.description = "open ground"
    return out
