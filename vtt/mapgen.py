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
import re
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
    # Named rooms this layout BUILT, as [{"name","x","y","w","h"}]. Today: a
    # vessel's compartments. A room is not a rule — the walls and the doorway
    # are already tiles by the time this is written — it is what the place is
    # CALLED, which is the difference between a deckhouse and the armoury of a
    # bastion that flies.
    rooms: list[dict] = field(default_factory=list)
    # Buildings this layout raised, as [{"x","y","w","h","storeys"}]. Each is
    # one HOUSE with its own inside — the roof tracer needs them one at a time
    # or a terrace comes back under a single roof, which is a warehouse.
    buildings: list[dict] = field(default_factory=list)
    # The level sheet over each pool, {"x,y": feet} — sparse, and DERIVED from
    # the grid plus the elevation the sink cut (see vtt.water). Traced on this
    # side because a pool's surface is a property of the whole POOL and no
    # square can see one, which is the argument that put vtt.hull here too.
    water: dict[str, float] = field(default_factory=dict)
    # How the ground LIES here, from placelore.relief_of — an input, like
    # `style` and `wanted_rooms`. The tactical layer must not know what a world
    # graph is (the `_bastion_rooms` line), so the caller that HAS a place
    # hands the answer down; a caller with none leaves this empty and the
    # generator reads the biome prose instead.
    relief: dict = field(default_factory=dict)
    # Rooms the CALLER asked for, by name. An input, consumed by the generator
    # exactly as ``style`` is: a bastion airship carries its owner's facilities,
    # and nothing else on the board could know their names.
    wanted_rooms: tuple[str, ...] = ()

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

#: How far a sea's swell rises, in feet. Three: enough for the depth map to
#: carry the water at all, small enough that riding a crest is not a hill.
SWELL_FT = 3

#: Archetypes that are MEANT to be mostly untraversable — a hull in the sea, an
#: island in the sky — and so are judged by whether they produced anything to
#: stand on rather than by what fraction of the board it is.
SPARSE_ARCHETYPES = frozenset({"ship", "skyship", "open-water", "sky-islands",
                               "reef"})

#: The smallest deck worth calling a board, in squares — the same twelve
#: `_rig_ship` already treats as "too small for a hold", so the two agree about
#: what a small ship is. Twelve squares is sixty feet of deck: a cramped board
#: for a boarding action, which is the point of a cutter, and emphatically not
#: the collapsed generator this floor exists to catch.
VESSEL_DECK_FLOOR = 12

#: The smallest walkable area worth calling a board, in squares — about a
#: twelve-by-ten room.
#:
#: An ABSOLUTE floor, and it has to be. This was an eighth of the board, which
#: is a sensible-looking rule that grows with the area while the walkable
#: content of a corridor-shaped place grows with its LENGTH. A mountain pass is
#: a track: at 24x18 its two hundred walkable squares clear an eighth easily,
#: and at 48x36 the same generator producing four times the track is condemned
#: for not producing eight times — so the board was thrown away and replaced
#: with a MEADOW. Silently, because the fallback is a real board.
#:
#: That is the shape of bug this whole pass is about: a rule written as a
#: fraction of the board, which is right at one size and wrong at another.
PLAYABLE_FLOOR = 120

STEP_FT = 5
LEDGE_FT = 10


# --------------------------------------------------------------------------
# A ROAD IS AS STEEP AS THE COUNTRY IT CROSSES.
#
# The street's fall used to be ``rng.randint(3, 8)`` whatever the board said it
# was standing in, so a town on the plains and a town clinging to a mountain
# had the same slope on the same die — the ``_for_area`` complaint arriving
# from a different direction. A number that ought to be DERIVED from something
# the board already knows was rolled instead.
#
# WHERE the answer lives matters more than the answer. "How rugged is this
# country" was already settled, in ``eight_card_system.placelore.RELIEF``,
# beside the closed terrain vocabulary the scene art, the battlemap floor, the
# drawn map and the travel cost all read — so this module ASKS rather than
# keeping a second, fuzzier table of its own. It cannot import that side (the
# tactical layer must not know what a world graph is, the same line that keeps
# ``_bastion_rooms`` in the backend), so relief arrives as an INPUT the way
# ``style`` and ``wanted_rooms`` do, and the words below are the fallback for a
# caller that has none — a demo, the Proving Grounds, the selftest.
#
#: Free text -> the terrain vocabulary. Only for a caller with no relief to
#: hand over; anything that HAS a place should pass its relief instead.
_TERRAIN_WORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("mountain", "alpine", "crag", "peak", "highland", "gorge", "ravine",
      "cliff", "scarp", "pass", "summit"), "mountains"),
    (("hill", "downs", "upland", "foothill", "moor", "ridge", "vale",
      "valley", "dale"), "hills"),
    (("swamp", "marsh", "fen", "bog", "mire", "moss"), "swamp"),
    (("desert", "dune", "waste", "erg", "badland"), "desert"),
    (("coast", "shore", "harbour", "harbor", "port", "quay", "beach",
      "estuary", "island", "cove"), "coast"),
    (("forest", "wood", "jungle", "grove", "thicket", "taiga"), "forest"),
    (("plain", "steppe", "prairie", "flat", "delta", "polder", "lowland",
      "meadow", "farm", "field", "pasture", "grass"), "farmland"),
    (("river", "ford", "bank", "floodplain"), "river"),
    (("city", "town", "street", "village", "urban"), "urban"),
)

#: What a board that never said where it is gets — deliberately the numbers the
#: street had before any of this. A change nobody asked for is not an
#: improvement, and most boards carry no biome at all.
_RELIEF_DEFAULT = {"fall_ft": (3, 8), "waves": (0, 1), "cross": 0.20}

#: The steepest a road is allowed to change between one square and the next, in
#: feet. ONE, which is the smallest step the rules have: climbing it costs the
#: foot per foot the SRD charges and nothing on a road is ever a fall. It is a
#: cap on the WALK rather than on the budget, so a mountain road does not get a
#: gentler slope for being long — it spends the whole board climbing.
ROAD_MAX_STEP_FT = 1


def terrain_of(biome: str = "") -> str:
    """The terrain word for some free text, or "" if it says nothing.

    A last resort. ``placelore`` already decides this for every place in the
    world, and a caller holding one should pass the answer rather than the
    prose it came from.
    """
    b = (biome or "").strip().lower()
    for words, terrain in _TERRAIN_WORDS:
        if any(w in b for w in words):
            return terrain
    return ""


def road_relief(biome: str = "", relief: Optional[dict] = None) -> dict:
    """How steep, how wavy and how canted a road across this country is.

    ``relief`` is ``placelore.relief_of(...)`` handed down by a caller that
    knows where it is; ``biome`` is the DM's own words, read only when nothing
    better arrived.
    """
    if relief:
        return {"fall_ft": tuple(relief.get("fall_ft") or (3, 8)),
                "waves": tuple(relief.get("waves") or (0, 1)),
                "cross": float(relief.get("cross") or 0.0)}
    name = terrain_of(biome)
    if not name:
        return dict(_RELIEF_DEFAULT)
    try:                       # a checkout with no world graph still lays roads
        from eight_card_system.placelore import relief_of
    except Exception:
        return dict(_RELIEF_DEFAULT)
    return road_relief(relief=relief_of(name))


def _relief_walk(rng: random.Random, n: int, fall: int, waves: int) -> list[int]:
    """A height profile ``n`` squares long, in whole feet from its own lowest.

    Built as a WALK toward a target curve rather than by sampling the curve,
    which is what enforces :data:`ROAD_MAX_STEP_FT` by construction: each
    square moves at most a foot toward where the curve wants it, so no amount
    of budget or waviness can put a drop in a street. A road that cannot spend
    its whole fall simply arrives at the far end still climbing, which is what
    a road too steep for its country does in life as well.
    """
    if n <= 1 or fall <= 0:
        return [0] * max(1, n)
    # The target: a ramp, plus `waves` full rise-and-dips riding on it. The
    # ramp is what makes one end of the street worth more than the other; the
    # waves are what stop a mountain road being an inclined plane.
    amp = fall * (0.35 if waves else 0.0)
    phase = rng.random() * math.tau
    targets = []
    for i in range(n):
        t = i / (n - 1)
        y = t * fall
        if waves:
            y += amp * math.sin(phase + t * math.tau * waves)
        targets.append(y)
    out: list[float] = [targets[0]]
    for want in targets[1:]:
        cur = out[-1]
        out.append(cur + max(-ROAD_MAX_STEP_FT,
                             min(ROAD_MAX_STEP_FT, want - cur)))
    ints = [int(round(v)) for v in out]
    low = min(ints)
    return [v - low for v in ints]


#: The fall, in feet, that counts as country as rough as it gets. Mountains
#: sit at the top of :data:`placelore.RELIEF` and everything else is read
#: against them, so ``_ruggedness`` is "how mountainous is this" on a 0..1
#: scale — which is the shape every generator wants: not a height, a DIAL.
_RUGGED_FULL_FT = 18.0


def _ruggedness(out: "GeneratedMap", default: str = "") -> float:
    """How broken this board's country is, 0 (a salt flat) to 1 (mountains).

    Generators used fixed probabilities for their height features — a third of
    open boards terraced, four fifths of the rest given a knoll — which is the
    ``rng.randint(3, 8)`` complaint one level up: a meadow on the plains and a
    meadow in the high country were equally likely to come back stepped.

    ``default`` is the country an archetype IS, for the ones that name their
    own: a mountain pass is mountainous whether or not anybody said so, and
    reading the generic middling answer there would make it gentler than the
    board it replaced.
    """
    r = out.relief or {}
    if not r:
        name = terrain_of(out.biome) or default
        if name:
            try:
                from eight_card_system.placelore import relief_of
                r = relief_of(name)
            except Exception:
                r = {}
    lo, hi = (r.get("fall_ft") or _RELIEF_DEFAULT["fall_ft"])
    return max(0.0, min(1.0, ((lo + hi) / 2.0) / _RUGGED_FULL_FT))


def road_profile(rng: random.Random, width: int, height: int,
                 biome: str = "",
                 relief: Optional[dict] = None) -> dict[tuple[int, int], int]:
    """Ground height per square for a road board, in feet. Sparse-ready.

    Along the board's length and ACROSS its width, each a walk of its own, so
    a mountain street is canted as well as climbing — which is most of what
    makes one read as cut into a hillside rather than laid on a table. Both
    walks respect the one-foot step, so their sum steps at most a foot in
    either direction.
    """
    r = road_relief(biome, relief)
    along_ft = rng.randint(*r["fall_ft"])
    n_waves = rng.randint(*r["waves"])
    along = _relief_walk(rng, width, along_ft, n_waves)
    if rng.random() < 0.5:
        along = along[::-1]
    across_ft = int(round(along_ft * r["cross"]))
    lateral = _relief_walk(rng, height, across_ft,
                           1 if (n_waves and across_ft >= 4) else 0)
    if rng.random() < 0.5:
        lateral = lateral[::-1]
    return {(x, y): along[x] + lateral[y]
            for x in range(width) for y in range(height)}



# --------------------------------------------------------------------------
# A BIGGER BOARD MEANS MORE OF THE PLACE, NOT A BIGGER PLACE.
#
# A square is five feet. That makes almost every dimension on a board a real
# measurement somebody can picture, and it is the thing to hold on to when the
# board gets bigger: a chamber is twenty to forty-five feet across whether the
# board is a hundred feet wide or two hundred and fifty, a corridor is five or
# ten feet, a street is a cart and two people passing. What a bigger board buys
# is MORE chambers, MORE street, MORE camp — never one chamber four times the
# size, which is a cathedral with a goblin in it.
#
# Measured, before this existed, by comparing the largest connected open region
# at 24x18 against 48x36: the dungeon complex grew its rooms **7.5x**, the
# taproom 5.2x, the crypt 4.9x. Six halls of seventy-five by sixty feet is not
# a dungeon. `vtt/selftest.py` guards it now — a generator may be rewritten,
# but not back into a hall.
#
# Open country is the exception and needs no rule: a meadow, a forest and a
# marsh SHOULD be one region four times the size, because that is what more of
# them is.
# --------------------------------------------------------------------------

#: The board every count below is quoted against — the combat default, and the
#: size these generators were originally tuned at.
BASE_AREA = 24 * 18

#: A chamber, in FEET. Twenty is a cell, forty-five a hall; past that it stops
#: being a room and starts being a courtyard, which is a different tile.
ROOM_MIN_FT, ROOM_MAX_FT = 20, 45

#: A passage, in feet. Five is one person at a time and ten is two abreast,
#: which is the whole of the tactical difference a corridor makes.
CORRIDOR_FT = 10

#: A town street, kerb to kerb: a cart and two people passing.
STREET_FT = 25

#: How deep a block of houses is, front to back — a room and a half and a yard.
BLOCK_FT = 40

#: A big coaching inn's public room, front to back. Past this it is a hall.
TAPROOM_FT = 70

#: A GREAT hall — a cathedral nave, and the largest single room the board will
#: build. Past this it stops being a room and becomes a parade ground with a
#: roof on.
HALL_FT = 90


def _sq(ft: int) -> int:
    """Feet to whole squares, at least one."""
    return max(1, int(round(ft / 5.0)))


def _for_area(g: Grid, n_at_base: float, *, most: int = 10_000) -> int:
    """How many of a thing this board should hold.

    ``n_at_base`` is the count that looked right on a 24x18 board. Scaling by
    AREA rather than by width is what keeps density constant — the alternative
    is a board twice as wide with the same number of tents in it, which is the
    same emptiness the size increase was meant to fix.
    """
    return max(1, min(most, int(round(n_at_base * g.width * g.height / BASE_AREA))))


def _elev_of(out: GeneratedMap, level: int = 0) -> dict:
    """The height map of one storey, made on demand.

    Level 0 is the board's own ``elevation``; an upper storey keeps its own
    inside its ``levels`` entry — the same split the engine stores, and for the
    same reason. It exists at all because every height primitive below used to
    write the GROUND floor whatever floor it was building, so a gallery, a
    rooftop and a ship's hold were table tops by construction: the whole
    vocabulary this module grew for the ground stopped at the stairs.
    """
    if not int(level or 0):
        return out.elevation
    idx = int(level) - 1
    if not (0 <= idx < len(out.levels)):
        return {}                    # no such storey: writing nowhere is right
    return out.levels[idx].setdefault("elevation", {})


def _raise(out: GeneratedMap, squares: Iterable[Square], ft: int,
           level: int = 0) -> None:
    """Record height on squares. Zero is stored as nothing, not as zero."""
    elev = _elev_of(out, level)
    for x, y in squares:
        if ft:
            elev[f"{x},{y}"] = int(ft)
        else:
            elev.pop(f"{x},{y}", None)


def _terrace(g: Grid, out: GeneratedMap, x0: int, y0: int, x1: int, y1: int,
             ft: int, *, on: tuple[str, ...] = (), steps: str = "",
             level: int = 0) -> list[Square]:
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
    _raise(out, got, ft, level)
    # A ramp is only worth having on a LEDGE. Half of a five-foot step is two
    # feet, which is a number nobody needs to be told and which costs a climb
    # of five feet either way — the step is already cheap enough to take.
    if steps and got and abs(ft) >= LEDGE_FT:
        edge = {"n": [(x, y0) for x in range(x0, x1 + 1)],
                "s": [(x, y1) for x in range(x0, x1 + 1)],
                "w": [(x0, y) for y in range(y0, y1 + 1)],
                "e": [(x1, y) for y in range(y0, y1 + 1)]}.get(steps, [])
        ramp = [sq for sq in edge if sq in got]
        _raise(out, ramp, int(ft / 2), level)
        for x, y in ramp:
            if g.get(x, y) not in APERTURES and g.passable(x, y):
                g.set(x, y, "u")
    return got


def _mound(g: Grid, rng: random.Random, out: GeneratedMap, cx: int, cy: int,
           radius: float, ft: int, *, on: tuple[str, ...] = (),
           level: int = 0) -> list[Square]:
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
        _raise(out, [(x, y)], ft if d < 0.55 else int(ft / 2), level)
    return got



def _plateaus(g: Grid, rng: random.Random, out: GeneratedMap, *,
              tiers: int = 3, step_ft: int = LEDGE_FT,
              on: tuple[str, ...] = (), face: str = "R",
              ramps: int = 2, level: int = 0) -> None:
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
            _raise(out, [(x, y)], t * step_ft, level)

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
                _raise(out, [(x, y)], int((len(edges) - i - 0.5) * step_ft),
                       level)
                if g.passable(x, y):
                    g.set(x, y, "u")
                continue
            g.set(x, y, face)
            _elev_of(out, level).pop(f"{x},{y}", None)


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
                       "terrain": ["".join(r) for r in rows],
                       # Its own height map, empty to begin with. A storey is
                       # allowed relief exactly as the ground is.
                       "elevation": {}, "stairs": []})
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

def _side_chambers(g: Grid, rng: random.Random, out: GeneratedMap,
                   hall: tuple[int, int, int, int]) -> None:
    """Rooms off a great hall, in whatever the hall did not take.

    A hall on a big board used to be the whole board — one flat rectangle two
    hundred feet across. Capping it leaves a margin, and a margin round a hall
    is what a hall actually has: vestries, cells, a stair, the way in. The same
    warren machinery the complex uses, because a suite of small rooms off one
    another is the same problem however it is reached.
    """
    hx0, hy0, hx1, hy1 = hall
    spare = [(0, 0, g.width - 1, hy0 - 2), (0, hy1 + 2, g.width - 1, g.height - 1),
             (0, hy0 - 1, hx0 - 2, hy1 + 1), (hx1 + 2, hy0 - 1, g.width - 1, hy1 + 1)]
    made: list[tuple[int, int, int, int]] = []
    for sx0, sy0, sx1, sy1 in spare:
        if sx1 - sx0 < 5 or sy1 - sy0 < 5:
            continue
        for cx0, cy0, cx1, cy1 in _bsp_cells(sx0, sy0, sx1, sy1, rng,
                                             min_side=_sq(ROOM_MIN_FT) + 2,
                                             max_side=_sq(ROOM_MAX_FT) + 2):
            x0, y0 = cx0 + 1, cy0 + 1
            x1, y1 = min(g.width - 2, cx1 - 1), min(g.height - 2, cy1 - 1)
            if x1 - x0 < 3 or y1 - y0 < 3:
                continue
            _room(g, x0, y0, x1, y1)
            made.append((x0, y0, x1, y1))
    prev = ((hx0 + hx1) // 2, (hy0 + hy1) // 2)
    for x0, y0, x1, y1 in made:
        _carve_corridor(g, prev, ((x0 + x1) // 2, (y0 + y1) // 2))
        prev = ((x0 + x1) // 2, (y0 + y1) // 2)
    if made:
        _threshold_doors(g, rng, made, out)


def _gen_dungeon_room(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, WALL)
    # A GREAT HALL is grand and it is still a room: ninety by sixty feet is a
    # cathedral nave, and past that it is a parade ground with a roof on. The
    # chamber used to be the whole board, so a 46x34 one came back two hundred
    # and thirty feet across — one flat rectangle with pillars in it, which is
    # the one shape this archetype was written to avoid.
    # A margin only becomes side chambers when there is room for one on each
    # side; anything less and the hall simply takes it. Otherwise a board that
    # is merely a little bigger than a hall comes back as a hall with two
    # useless strips of rock beside it — and a board that was ALREADY one room
    # stops being one, which is not what a bigger default was for.
    spare_for = _sq(ROOM_MIN_FT) + 3
    hall_w = min(g.width - 2, _sq(HALL_FT))
    hall_h = min(g.height - 2, _sq(HALL_FT * 2 // 3))
    if (g.width - 2 - hall_w) < spare_for * 2:
        hall_w = g.width - 2
    if (g.height - 2 - hall_h) < spare_for * 2:
        hall_h = g.height - 2
    hx = 1 + (g.width - 2 - hall_w) // 2
    hy = 1 + (g.height - 2 - hall_h) // 2
    m = 1
    _room(g, hx, hy, hx + hall_w - 1, hy + hall_h - 1)
    # …and what a bigger board buys is the rest of the PLACE: side chambers off
    # the hall and the ways between them, so there is somewhere to fall back to
    # and somewhere to come round by.
    _side_chambers(g, rng, out, (hx, hy, hx + hall_w - 1, hy + hall_h - 1))
    # Pillars in a rough colonnade — real cover, symmetric enough to read as built.
    step = max(3, min(5, hall_w // 5))
    for y in range(hy + 1, hy + hall_h - 1, step):
        for x in range(hx + 1, hx + hall_w - 1, step):
            if rng.random() < 0.7:
                g.set(x, y, "O")
    # Rubble and a scattering of furniture.
    _scatter(g, rng, ",", 0.06)
    _scatter(g, rng, "o", 0.03)
    for side in ("west", "east"):
        d = _door_on_wall(g, rng, hx, hy, hx + hall_w - 1, hy + hall_h - 1, side)
        if d:
            out.doors.append({"x": d[0], "y": d[1], "state": "closed",
                              "name": "door", "dc": None})
    # A DAIS, or a floor that drops. Either way the room stops being a flat
    # rectangle: whoever holds the high end shoots down into it, and getting up
    # there costs a climb.
    m2 = m + 1
    if rng.random() < 0.7:
        d = max(3, min(6, hall_h // 4))
        top = rng.random() < 0.5
        y0 = hy + 1 if top else hy + hall_h - 2 - d
        _terrace(g, out, hx + 1, y0, hx + hall_w - 2, y0 + d, STEP_FT,
                 on=(FLOOR, ",", "o", "O"), steps=("s" if top else "n"))
        out.description = ("a pillared stone chamber, flagstones cracked with "
                           "age, a broad dais along one end")
    else:
        pit = max(4, min(9, hall_w // 3))
        px = hx + (hall_w - pit) // 2
        py = hy + (hall_h - max(3, pit // 2)) // 2
        _terrace(g, out, px, py, px + pit, py + max(3, pit // 2), -STEP_FT,
                 on=(FLOOR, ",", "o"), steps="w")
        out.description = ("a pillared stone chamber built around a sunken "
                           "floor, flagstones cracked with age")
    out.lighting = rng.choice(["dim", "dim", "bright"])


def _bsp_cells(x0: int, y0: int, x1: int, y1: int, rng: random.Random,
               depth: int = 0, min_side: int = 7,
               max_side: int = 0) -> list[tuple[int, int, int, int]]:
    """Recursively halve a rectangle until the pieces are room-sized.

    ``max_side`` is the whole reason this stops where it does. It used to stop
    at ``depth >= 3``, which is a fixed EIGHT cells however big the rectangle
    is — so doubling the board doubled every room instead of doubling the
    number of them, and a 48x36 complex came back as six halls of seventy-five
    by sixty feet. Depth is still a backstop against pathological recursion;
    what actually decides is whether a cell is bigger than a room.
    """
    w, h = x1 - x0, y1 - y0
    big = max_side and (w > max_side or h > max_side)
    if depth >= 12 or (not big and w < min_side * 2 and h < min_side * 2):
        return [(x0, y0, x1, y1)]
    horizontal = h > w if abs(w - h) > 2 else rng.random() < 0.5
    if horizontal and h >= min_side * 2:
        cut = rng.randint(y0 + min_side, y1 - min_side)
        return (_bsp_cells(x0, y0, x1, cut, rng, depth + 1, min_side, max_side)
                + _bsp_cells(x0, cut, x1, y1, rng, depth + 1, min_side, max_side))
    if w >= min_side * 2:
        cut = rng.randint(x0 + min_side, x1 - min_side)
        return (_bsp_cells(x0, y0, cut, y1, rng, depth + 1, min_side, max_side)
                + _bsp_cells(cut, y0, x1, y1, rng, depth + 1, min_side, max_side))
    return [(x0, y0, x1, y1)]


def _gen_dungeon_complex(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, WALL)
    rooms: list[tuple[int, int, int, int]] = []
    # Cells the size of a ROOM plus its walls, not a fixed eight of them: see
    # _bsp_cells and the doctrine at the top of this module. A room is twenty to
    # forty-five feet across whatever the board is, and a board four times the
    # area holds four times as many.
    cell_min = _sq(ROOM_MIN_FT) + 2
    cell_max = _sq(ROOM_MAX_FT) + 2
    for cx0, cy0, cx1, cy1 in _bsp_cells(0, 0, g.width - 1, g.height - 1, rng,
                                         min_side=cell_min, max_side=cell_max):
        # Inset the room inside its cell so neighbouring rooms don't share walls.
        w = max(3, min(_sq(ROOM_MAX_FT), (cx1 - cx0) - rng.randint(1, 3)))
        h = max(3, min(_sq(ROOM_MAX_FT), (cy1 - cy0) - rng.randint(1, 3)))
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
    # Woodland is not a table top: knolls to hold and hollows to be caught in.
    # HOW MANY grows with the board (the `_for_area` rule — one knoll was
    # right at 24x18 and is a curiosity on four times the ground) and HOW HIGH
    # is the country's: woodland on a plain rolls in steps, woodland on a
    # hillside has ledges in it. Default forest, because that is what this
    # archetype IS when nobody has said otherwise.
    rug = _ruggedness(out, default="forest")
    lift = LEDGE_FT if rug >= 0.4 else STEP_FT
    for _ in range(_for_area(g, 1, most=6)):
        _mound(g, rng, out, rng.randrange(2, max(3, g.width - 2)),
               rng.randrange(2, max(3, g.height - 2)),
               rng.uniform(2.0, 3.5), lift, on=("g", "\""))
    for _ in range(_for_area(g, 1, most=5)):
        if rng.random() < 0.3 + rug:
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
    # A glade is ONE glade, so this stays one feature however big the board —
    # what the country decides is which feature, and how likely. Default
    # forest: a clearing is a hole in woodland.
    rug = _ruggedness(out, default="forest")
    if rng.random() < min(0.85, rug):
        _plateaus(g, rng, out, tiers=2, on=("g", "\"", ","), face="R", ramps=2,
                  step_ft=LEDGE_FT if rug >= 0.4 else STEP_FT)
        out.description += ", the ground stepping up to a higher shelf"
    elif rng.random() < 0.45 + rug:
        _mound(g, rng, out, g.width // 2 + rng.randint(-4, 4),
               g.height // 2 + rng.randint(-3, 3),
               rng.uniform(2.5, 4.0),
               LEDGE_FT if rug >= 0.4 else STEP_FT, on=("g", "\"", ","))
        out.description += ", a green barrow mound at its centre"


def _terrace_houses(g: Grid, rng: random.Random, out: GeneratedMap,
                    blocks: list[tuple[int, int, int, int]]) -> None:
    """Build a terrace along every side of every block that faces a road.

    A house is not a different KIND of thing from a tent — it is
    :func:`structures.townhouse`, which is a shelter with a party wall on each
    side and its door on a NAMED side — and everything a tent already earns
    comes with it: the inside is real squares, sight and cover read the walls,
    and the way in is a doorway the engine understands. The street used to be a
    block of solid masonry with a roof traced over it, which is scenery you
    fight around rather than in.

    Houses are fifteen to thirty feet across, laid shoulder to shoulder with a
    party wall between neighbours, and some of them stand a storey or two
    taller. That is what a street looks like, and it is also the cheapest
    vertical terrain a town has: a real floor, reached by a real stair, from
    which a real archer shoots down into the road.
    """
    from . import structures

    lo_w, hi_w = _sq(structures.HOUSE_FT[0]), _sq(structures.HOUSE_FT[1])
    deep = _sq(BLOCK_FT)
    placed: list[dict] = []

    def _road_at(x: int, y: int) -> bool:
        """Is there a ROADWAY on this square — a real one, on the board.

        Off the board does NOT count, and treating it as a road is what sealed
        one house in three: the strip past the last lane has nothing below it
        but the edge, so its terrace was built facing outward and every door in
        it opened off the map. `_connect_regions` then punched a hole in the
        back wall to reach the interior, which is the failure the whole
        planned-alley pass exists to prevent — arriving from a direction the
        planning could not see. Every block is bounded by a lane on at least
        one side by construction, so nothing loses its frontage.
        """
        return g.in_bounds(x, y) and g.get(x, y) == "="

    def _plan(run_w: int, must_alley: bool) -> list[int]:
        """House widths along a frontage, with 0 standing for an ALLEY.

        Planned before anything is built, because an alley has to be DECIDED
        rather than discovered: a terrace with a yard behind it and no way
        through is a sealed block, and `_connect_regions` then carves its own
        hole in somebody's wall — which reads as nothing at all, exactly as it
        does when it punches through a cliff.
        """
        run: list[int] = []
        at = 0
        while at + lo_w <= run_w:
            wide = min(rng.randint(lo_w, hi_w), run_w - at)
            if wide < lo_w:
                break
            run.append(wide)
            at += wide
            if at + 1 + lo_w <= run_w and rng.random() < 0.22:
                run.append(0)
                at += 1
        if must_alley and 0 not in run and len(run) >= 2:
            # No room was left for one, so BUY it: the narrowest house on the
            # frontage gives up a square. Refused if that would take it under
            # the smallest house a street has, and the caller then has a block
            # with no yard rather than a terrace of sheds.
            i = min(range(len(run)), key=lambda k: run[k])
            if run[i] - 1 >= lo_w:
                run[i] -= 1
                run.insert(i + 1, 0)
        return run

    for bx0, by0, bx1, by1 in blocks:
        bw, bh = bx1 - bx0 + 1, by1 - by0 + 1
        if bw < 4 or bh < 4:
            continue
        # Which sides of this block front onto a road, and how deep a terrace
        # may run back from each.
        #
        # Depth is SHARED, and getting that wrong is how 175 pairs of houses in
        # 120 boards came to be built on top of each other: both frontages took
        # `min(deep, bh)` independently, so any block between one and two
        # houses deep had its two terraces overlap, the second overwriting the
        # first's walls and leaving its roof traced over squares that were no
        # longer there.
        north, south = _road_at(bx0, by0 - 1), _road_at(bx0, by1 + 1)
        sides: list[tuple[str, int, int, int, int]] = []
        yard = 0
        if north and south:
            if bh <= 2 * deep:
                front = bh // 2                    # back to back, no overlap
                sides = [("s", bx0, by0, bw, front),
                         ("n", bx0, by0 + front, bw, bh - front)]
            else:
                sides = [("s", bx0, by0, bw, deep),
                         ("n", bx0, by1 - deep + 1, bw, deep)]
                yard = bh - 2 * deep
        elif north:
            sides = [("s", bx0, by0, bw, min(deep, bh))]
            yard = max(0, bh - deep)
        elif south:
            sides = [("n", bx0, by1 - min(deep, bh) + 1, bw, min(deep, bh))]
            yard = max(0, bh - deep)
        for side, ox, oy, run_w, run_h in sides:
            if run_h < 4:
                continue
            # `side` is the wall the door goes IN, so a block whose road is to
            # the north has its doors in its north wall.
            door_side = "n" if side == "s" else "s"
            at = 0
            for wide in _plan(run_w, bool(yard)):
                if not wide:                       # an ALLEY: the best kiting
                    at += 1                        # ground a town has, and the
                    continue                       # only way through a terrace
                b_storeys = rng.choice((0, 0, 1, 1, 2))
                b = structures.townhouse(
                    g, rng, out, ox + at, oy, wide, run_h,
                    street=door_side, storeys=b_storeys)
                if b.interior:
                    out.skins.update(b.skins)
                    out.doors.extend(b.doors)
                    placed.append({"x": ox + at, "y": oy, "w": wide,
                                   "h": run_h, "storeys": b_storeys})
                at += wide
    out.buildings = placed


def _gen_street(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    # A town is BLOCKS with HOUSES on them, and every number here is a real
    # measurement: a roadway a cart and two people can pass on, a block of
    # houses about forty feet deep, a house fifteen to thirty feet across its
    # frontage. Sized as fractions of the board — which is how this was
    # written — a bigger street came back a boulevard between buildings a
    # hundred feet deep, which is a warehouse district nobody asked for.
    road = _sq(STREET_FT)
    block = _sq(BLOCK_FT)
    g.fill_rect(0, 0, g.width - 1, g.height - 1, WALL)

    def _lanes(span: int) -> list[int]:
        """Where the roadways run. Pitch is two blocks back to back plus the
        road between them, which is what a town actually is."""
        out_ = []
        at = block
        while at + road + block <= span:
            out_.append(at)
            at += block * 2 + road
        return out_ or ([max(0, (span - road) // 2)] if span >= road + 2 else [])

    hlanes = _lanes(g.height)
    # Cross streets only once the board is long enough to want one: a roadway
    # running the whole way with no way off it is more street and the same
    # corridor.
    vlanes = _lanes(g.width) if g.width >= block * 2 + road * 2 else []
    for y0 in hlanes:
        g.fill_rect(0, y0, g.width - 1, min(g.height - 1, y0 + road - 1), "=")
    for x0 in vlanes:
        g.fill_rect(x0, 0, min(g.width - 1, x0 + road - 1), g.height - 1, "=")

    # The BLOCKS are the rectangles the roads left, derived rather than
    # searched for. Scanning the grid for frontage cannot work once anything
    # else has touched it: one alley put a road square in every row, so every
    # row read as facing a street and no terrace was ever laid.
    def _spans(lanes: list[int], span: int) -> list[tuple[int, int]]:
        cuts = [(-road, 0)] + [(a, a + road - 1) for a in lanes] + [(span, span)]
        out_ = []
        for (_, e), (b, _) in zip(cuts, cuts[1:]):
            if b - 1 >= e + 1:
                out_.append((e + 1, b - 1))
        return out_

    blocks = [(x0, y0, x1, y1)
              for x0, x1 in _spans(vlanes, g.width)
              for y0, y1 in _spans(hlanes, g.height)]
    _terrace_houses(g, rng, out, blocks)

    # A TOWN IS ON GROUND, and ground is not a billiard table. The roadway was
    # dead flat, which is what made a street read as a diagram: nothing to
    # shoot down, nothing to labour up, no reason for one end to be worth more
    # than the other. Everything is in ONE-FOOT steps — the smallest the rules
    # have — so climbing it costs the foot per foot the SRD charges and nothing
    # on a street ever becomes a drop. The cobble skin is `soft`, so the
    # surface between the steps is drawn as a slope rather than as terraces;
    # see terrain.SOFT_GROUND.
    #
    # HOW steep is the COUNTRY's business, not a die's: see road_profile. A
    # town on the plains is usually level and a town on a mountainside climbs,
    # saddles and climbs again, canted across its width as it goes.
    ground = road_profile(rng, g.width, g.height, out.biome, out.relief)
    # A BUILDING IS LEVEL INSIDE, and the steeper the street the more that
    # matters: a house six squares deep on a mountain road would otherwise have
    # six feet of fall across its own floor. A floor is LAID — the same
    # sentence that keeps a flagstone hall flat — so the builder cuts and fills
    # the plot to one height and the difference lands OUTSIDE, as the step up
    # to the door that every hill town has.
    for b in out.buildings:
        plot = [(x, y)
                for x in range(int(b["x"]), int(b["x"]) + int(b["w"]))
                for y in range(int(b["y"]), int(b["y"]) + int(b["h"]))
                if g.in_bounds(x, y)]
        if not plot:
            continue
        sill = sorted(ground[sq] for sq in plot)[len(plot) // 2]
        for sq in plot:
            ground[sq] = sill
    for (x, y), ft in ground.items():
        if ft:
            _raise(out, [(x, y)], ft)
    _scatter(g, rng, "o", 0.05, only_on=("=",))
    _scatter(g, rng, "n", 0.02, only_on=("=",))
    _scatter(g, rng, ",", 0.04, only_on=("=",))
    out.lighting = rng.choice(["bright", "dim"])
    out.description = "a narrow city street between shuttered buildings, crates stacked at the walls"
    # ROOFS. A street fight with archers above it is the asymmetric fight, and
    # the buildings were solid blocks with nothing on top. The roof level is
    # laid over the building squares only, so the street itself stays open sky
    # from up there — you can see down into it, shoot into it, and fall into it.
    # Every square of every HOUSE, not every wall on the board: the block's
    # back land is not a roof, and since the terrace went in the two are no
    # longer the same set.
    roof = sorted({(x, y) for b in out.buildings
                   for x in range(b["x"], b["x"] + b["w"])
                   for y in range(b["y"], b["y"] + b["h"])
                   if g.in_bounds(x, y)})
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
            # A ROOF IS NOT A PLANE. The roof level was laid flat, so a street
            # fight's whole second storey — the one the comment above calls the
            # asymmetric fight — was twenty feet of table top: nowhere to take,
            # nothing to crouch behind, no reason to be on one part of it
            # rather than another. Every square with roof on all four sides is
            # a building's own ridge, raised a step; the rim it leaves is the
            # eaves. That is a hipped roof, it is derived from the footprint
            # rather than rolled, and it gives an archer up there somewhere
            # worth standing and somebody coming up the stair a way to close
            # without being seen the whole time.
            # Depth in from the eaves, by erosion: ring 0 is the edge of the
            # roof, ring 1 the course inside it, and so on. Raising by RING
            # rather than by "is it interior" is the difference between a
            # hipped roof and a mesa with a rim — on a big block the interior
            # is most of the roof, and one step for all of it is a plateau.
            roofset = set(roof)
            depth: dict[Square, int] = {}
            edge = [sq for sq in roof
                    if not {(sq[0] + 1, sq[1]), (sq[0] - 1, sq[1]),
                            (sq[0], sq[1] + 1), (sq[0], sq[1] - 1)} <= roofset]
            for sq in edge:
                depth[sq] = 0
            frontier = list(edge)
            while frontier:
                nxt = []
                for x, y in frontier:
                    for sq in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                        if sq in roofset and sq not in depth:
                            depth[sq] = depth[(x, y)] + 1
                            nxt.append(sq)
                frontier = nxt
            # Two tiers at most. The second only appears on a block deep enough
            # to have a middle, so a narrow townhouse gets eaves and a ridge and
            # a warehouse gets a real place to stand — and stepping off THAT is
            # a ten-foot fall, which is the height the rules make you decide
            # about.
            for sq, d in depth.items():
                if d >= 3:
                    _raise(out, [sq], LEDGE_FT, level)
                elif d >= 1:
                    _raise(out, [sq], STEP_FT, level)
            # A TERRACE IS NOT ONE HEIGHT. Every house on it stands where its
            # neighbours do not, and a roof level drawn at one height is the
            # thing that made a street read as one warehouse. Per-storey
            # elevation is exactly what this is for: the level's `base_ft` is
            # where the lowest roof sits and each house's own storeys raise its
            # squares above that.
            for b in out.buildings:
                extra = int(b.get("storeys") or 0) * 10
                if not extra:
                    continue
                for x in range(b["x"], b["x"] + b["w"]):
                    for y in range(b["y"], b["y"] + b["h"]):
                        key = f"{x},{y}"
                        cur = _elev_of(out, level).get(key, 0)
                        _raise(out, [(x, y)], cur + extra, level)
            out.description += ", roofs above it reached by an outside stair"


def _inn_rooms(g: Grid, rng: random.Random, out: GeneratedMap,
               tap_x1: int, tap_y1: int, back: int) -> None:
    """Whatever is left of the board once the taproom has taken its own size.

    An inn is a public room and a warren behind it — a parlour, a snug, a stair
    hall, a cellar way, the yard door. The taproom used to be the whole board,
    so a bigger board was a bigger barn; this is what a bigger board should buy
    instead, and it is the dungeon's own machinery because a suite of small
    rooms off one another is the same problem.
    """
    spare = [(0, tap_y1 + 1, g.width - 1, g.height - 1),
             (tap_x1 + (back + 2 if back else 1), 0, g.width - 1, tap_y1)]
    made: list[tuple[int, int, int, int]] = []
    for sx0, sy0, sx1, sy1 in spare:
        if sx1 - sx0 < 5 or sy1 - sy0 < 4:
            continue
        for cx0, cy0, cx1, cy1 in _bsp_cells(sx0, sy0, sx1, sy1, rng,
                                             min_side=_sq(ROOM_MIN_FT) + 2,
                                             max_side=_sq(ROOM_MAX_FT) + 2):
            x0, y0 = cx0 + 1, cy0 + 1
            x1, y1 = min(g.width - 1, cx1 - 1), min(g.height - 1, cy1 - 1)
            if x1 - x0 < 3 or y1 - y0 < 3:
                continue
            _room(g, x0, y0, x1, y1)
            made.append((x0, y0, x1, y1))
    # Joined to each other and to the taproom, or they are sealed boxes behind
    # a wall — and `_connect_regions` would then carve its own way in, which
    # reads as nothing.
    prev = (tap_x1 // 2, tap_y1 // 2)
    for x0, y0, x1, y1 in made:
        _carve_corridor(g, prev, ((x0 + x1) // 2, (y0 + y1) // 2))
        prev = ((x0 + x1) // 2, (y0 + y1) // 2)
    if made:
        _threshold_doors(g, rng, made, out)


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
    # A TAPROOM is a taproom. Filling the board with one made a 48x36 inn a
    # barn a hundred and ninety feet across; a big coaching inn's public room
    # is more like sixty by forty-five. What a bigger board buys is the REST of
    # the inn — a parlour, a snug, a stair hall — which the same warren
    # machinery the dungeon uses lays behind it.
    tap_x1 = min(tap_x1, _sq(TAPROOM_FT))
    tap_y1 = min(g.height - 1, _sq(ROOM_MAX_FT))
    _room(g, 0, 0, tap_x1, tap_y1)
    _inn_rooms(g, rng, out, tap_x1, tap_y1, back)
    if back:
        cut = rng.randrange(max(1, tap_y1 // 3),
                            max(2, 2 * tap_y1 // 3))
        _room(g, tap_x1, 0, g.width - 1, cut)               # the kitchen
        _room(g, tap_x1, cut, g.width - 1, tap_y1)          # the store
        for y0, y1, name in ((0, cut, "kitchen door"),
                             (cut, g.height - 1, "store door")):
            d = _door_on_wall(g, rng, tap_x1, y0, g.width - 1, y1, "west")
            if d:
                out.doors.append({"x": d[0], "y": d[1], "state": "closed",
                                  "name": name, "dc": None})
        # Casks and sacks in the store, a range and a block in the kitchen.
        _scatter(g, rng, "o", 0.22, only_on=(FLOOR,),
                 within=(tap_x1 + 1, cut + 1, g.width - 2, tap_y1 - 1))
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
    hy = max(2, min(tap_y1 - 2, tap_y1 // 2 + rng.randint(-2, 2)))
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
             if 0 < x <= tap_x1 - 1 and 0 < y < tap_y1
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
                    # A raised LANDING at the far end of the run. The gallery
                    # was a plank ten feet up and nothing else: a fight on it
                    # was two lines of creatures with no reason to be anywhere
                    # in particular. One step at the end away from the stair is
                    # the whole asymmetry a walkway can honestly carry — the
                    # far end is worth taking, and taking it costs the length
                    # of the gallery under somebody's bow.
                    far = max(walk, key=lambda sq: abs(sq[0] - below[0]))
                    landing = [sq for sq in walk
                               if abs(sq[0] - far[0]) <= 1
                               and abs(sq[0] - below[0]) > 3]
                    if landing:
                        _raise(out, landing, STEP_FT, level)
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
    # More of them on a bigger board, each the size a building is. Sized as a
    # third of the board they grew instead, so a big ruin was three enormous
    # halls rather than a village somebody burned.
    from . import structures

    houses: list[dict] = []
    for _ in range(_for_area(g, 4.5, most=40)):
        w = rng.randint(3, _sq(ROOM_MAX_FT))
        h = rng.randint(3, _sq(ROOM_MAX_FT))
        x0 = rng.randint(0, max(0, g.width - w - 1))
        y0 = rng.randint(0, max(0, g.height - h - 1))
        # ONE IN THREE IS STILL A BUILDING. A ruin drawn only as broken
        # outlines is a set of walls to run between and never anything to be
        # INSIDE — and the whole point of a ruin is that some of it is still
        # standing. A survivor gets a real doorway, a real inside, and now and
        # then a floor still up above it. The rest are the outlines they always
        # were, which is what makes the survivors read as survivors.
        if w >= 5 and h >= 5 and rng.random() < 0.34:
            b = structures.townhouse(
                g, rng, out, x0, y0, w, h,
                street=rng.choice(("n", "s", "e", "w")),
                storeys=1 if rng.random() < 0.35 else 0,
                skin="ruin-house",
                # Nothing was scrubbing these boards. A ruin's inside is the
                # same heaved paving as the rest of the site.
                floor_skin="ruin-floor")
            if b.interior:
                out.skins.update(b.skins)
                out.doors.extend(b.doors)
                houses.append({"x": x0, "y": y0, "w": w, "h": h})
                # …and then time takes some of it. Only the WALLS, never the
                # doorway or the floor: a ruin you cannot get into is a block.
                for x in range(x0, x0 + w):
                    for y in range(y0, y0 + h):
                        if g.get(x, y) == WALL and rng.random() < 0.22:
                            g.set(x, y, "w")
                            out.skins.pop(f"{x},{y}", None)
                continue
        # Broken walls: outline, then knock chunks out of it.
        g.outline_rect(x0, y0, x0 + w, y0 + h, "w" if rng.random() < 0.5 else WALL)
        for x, y in list(g.squares()):
            if g.get(x, y) in (WALL, "w") and rng.random() < 0.45:
                g.set(x, y, ",")
    out.buildings = houses
    _scatter(g, rng, "O", 0.03, only_on=(",",))
    _scatter(g, rng, "\"", 0.06, only_on=(",",))
    _connect_regions(g, rng)
    out.description = "toppled masonry and broken colonnades, weeds through the flagstones"
    # THE HILL IT WAS BUILT ON. Everything below this is masonry — foundations,
    # retaining walls, a floor still up — and none of it should answer to the
    # country: a ruin on a plain has exactly the same courses as one in the
    # hills, because people built them, and the odds of a wall still standing
    # are about how long ago it fell. What the country decides is the GROUND
    # the site occupies, and it goes first so the built work sits on top of it.
    rug = _ruggedness(out)
    for _ in range(_for_area(g, 1, most=3)):
        if rng.random() < 0.2 + rug:
            _mound(g, rng, out, rng.randrange(2, max(3, g.width - 2)),
                   rng.randrange(2, max(3, g.height - 2)),
                   rng.uniform(2.5, 4.0),
                   LEDGE_FT if rug >= 0.5 else STEP_FT, on=(",", "\"", FLOOR))
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
    # A camp is TENTS, so four times the ground is four times the camp — not
    # the same four tents standing in a field with a fire in the middle of it.
    want = _for_area(g, rng.randint(3, 5), most=40)
    pitched = 0
    for _ in range(want * 14):
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
    # THE GROUND IT WAS PITCHED ON, before anything anybody built. A camp takes
    # the country it halted in — the bank below is the other kind of height
    # entirely, and the two are deliberately not the same decision.
    rug = _ruggedness(out)
    for _ in range(_for_area(g, 1, most=4)):
        if rng.random() < 0.25 + rug:
            _mound(g, rng, out, rng.randrange(2, max(3, g.width - 2)),
                   rng.randrange(2, max(3, g.height - 2)),
                   rng.uniform(2.0, 3.5),
                   LEDGE_FT if rug >= 0.5 else STEP_FT, on=("g", "\""))
    # A camp that has been anywhere throws up a bank. One side of the board
    # stands a step higher behind it, which is the asymmetry a raid is fought
    # across.
    #
    # NOT country. Soldiers dig this, and they dig the same bank on a plain as
    # in the hills — so unlike the ground above, `_ruggedness` has no business
    # here. Worth saying out loud: the interesting half of hanging relief off
    # the terrain is knowing which heights are the LAND's and which are
    # somebody's labour.
    # Nearly always: it is the camp's only vertical feature, and at 0.7 the
    # selftest's three seeds could all miss it — which they did, the moment the
    # tent count changed the rng stream. A guard that depends on a coin flip is
    # a guard that reports the weather.
    if rng.random() < 0.9:
        # A BANK, in feet: fifteen to twenty-five deep, which is a ditch and
        # its spoil. A sixth of the board grew with it, so a big camp came back
        # standing behind an earthwork forty feet thick.
        band = max(2, min(_sq(25), g.height // 6))
        top = rng.random() < 0.5
        y0 = 0 if top else g.height - 1 - band
        _terrace(g, out, 0, y0, g.width - 1, y0 + band, STEP_FT,
                 on=("g", "\"", ",", FLOOR))


def _hull(g: Grid, rng: random.Random, surround: str, deck: str = "b",
          plan: str = "sea", cls=None) -> int:
    """Carve a SHIP-shaped deck out of the board. Returns the stern's x.

    The first shape was a symmetric lens — the same taper at both ends — which
    is a leaf, not a vessel, and is why every ship board read as a rectangle
    with the corners knocked off.

    The second shape was two plans, sea and sky, and it was still one ship
    apiece: length and beam came from the BOARD (``width - 2`` by
    ``height - 2``), so a two-crew skiff and a forty-passenger cruiser were the
    same outline in the same frame. A vessel now brings its own CLASS — see
    :mod:`vtt.vessels` — which carries length, beam, fineness and plan, so a
    cutter and a galleon are different ships and a small one is a small ship in
    a lot of sea rather than a small ship stretched to the edges.

    Either way the outline is a staircase, because it is carved out of squares.
    What stops it LOOKING like one is `isocam.footprint`, which cuts every outer
    corner so a one-square step is drawn as the diagonal it means.
    """
    from . import vessels as _v

    if cls is None:
        cls = _v.rolled(rng.randrange(1 << 30), sky=(plan == "sky"))
    g.fill_rect(0, 0, g.width - 1, g.height - 1, surround)
    length, beam = _v.fitted(cls, g.width, g.height)
    # Centred rather than pinned to the left edge: a hull shorter than the board
    # sitting against one side reads as a ship half off the picture.
    x0 = max(1, (g.width - length) // 2)
    cy = (g.height - 1) / 2.0

    for i in range(length):
        x = x0 + i
        hb = _v.half_beam(cls, i / float(max(1, length - 1)), beam)
        for y in range(g.height):
            if abs(y - cy) <= hb:
                g.set(x, y, deck)
    return x0 + length - 1     # the last deck column


def _rail(g: Grid, rng: random.Random, surround: str) -> None:
    """A stanchion rail everywhere the deck meets nothing, bar one gangway.

    Continuous on purpose. It used to be dropped at random on a fifth of the
    squares, which reads as a rail somebody has already smashed — and on a
    board where falling off the edge is a real outcome, a gap in the rail is a
    mechanical statement (half cover ends here) that nobody meant to make.
    """
    orth = ((1, 0), (-1, 0), (0, 1), (0, -1))
    edge = [(x, y) for x, y in g.squares()
            if g.get(x, y) == "b"
            and any(g.get(x + dx, y + dy) == surround for dx, dy in orth)]
    if not edge:
        return
    # A rail may not EAT the ship. `w` is impassable, and on a narrow hull —
    # a cutter, a courier, the fine ends of anything — almost every deck square
    # touches the sea, so railing all of them left a vessel with two squares to
    # stand on and the board fell back to open ground as a collapsed generator.
    # A square only takes a rail if the deck still reaches round it: two deck
    # neighbours or more, which keeps a walkable spine down the hull and leaves
    # the tips of the bow and stern as deck a creature can stand on.
    keep = {sq for sq in edge
            if sum(1 for dx, dy in orth
                   if g.get(sq[0] + dx, sq[1] + dy) == "b") < 2}
    railable = [sq for sq in edge if sq not in keep]
    if not railable:
        return
    gangway = railable[rng.randrange(len(railable))]
    for x, y in railable:
        if (x, y) != gangway:
            g.set(x, y, "w")


#: What a compartment is called when nobody named it, from aft forward. A
#: ship's rooms have names and they are not interchangeable — the great cabin
#: is aft under the transom and the forecastle is in the bows, and a board that
#: says so is a board a player can be told to meet someone in.
_COMPARTMENTS = ("the great cabin", "the deckhouse", "the chart room",
                 "the forecastle")


def _rig_ship(g: Grid, rng: random.Random, out: GeneratedMap, stern_x: int,
              *, cabin_skin: str = "hull", cls=None, surround: str = "W") -> None:
    """The furniture that makes a deck a ship: mast, compartments, hatch, hold."""
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
    #
    # A SMALL vessel gets none, and that is a rule about ships rather than about
    # boards: a cutter is a hull, a mast and an open deck, and building a cabin
    # into one leaves nowhere to fight. Measured — with the cabin, a railed
    # cutter came out with under twenty walkable squares and the board was
    # discarded as a collapsed generator, so every small ship silently became a
    # meadow.
    # HOW MANY is the class's business. A cutter is a hull, a mast and an open
    # deck; a cruiser is decks and cabins along her length, and drawing both as
    # "one deckhouse aft" is most of why every vessel read as the same vessel
    # from inside as well as out. A caller may ask for more by NAMING them —
    # a bastion that flies is a hall with rooms in it, and those rooms are the
    # facilities its owner paid for.
    open_deck = sum(1 for x, y in g.squares() if g.get(x, y) == "b")
    names = [n for n in (out.wanted_rooms or ()) if str(n).strip()]
    want = max(len(names), int(getattr(cls, "compartments", 0) or 0),
               1 if open_deck >= 34 else 0)
    # Every compartment eats deck, and deck is where the fight happens. The
    # cheapest way to build a board with nowhere to stand is to grant every
    # room somebody asked for, so the hull rations them by its own size and the
    # ones that do not fit are simply absent — the `landmarks` rule.
    want = min(want, len(_COMPARTMENTS), max(0, (open_deck - 20) // 14))
    # Swept from the transom FORWARD, one square at a time, because where a
    # deckhouse fits is decided by the hull rather than by arithmetic: the beam
    # narrows toward the bow, so the forward tries fail on their own and the
    # count rations itself. Names are handed out in the order rooms are BUILT,
    # never by which attempt they were — keying them to the attempt index spent
    # a name on every place a cabin would not go, so a ship with one compartment
    # called it the chart room and a bastion's armoury was never built at all.
    built_any: list[dict] = []
    x0 = stern_x - 5
    while x0 >= 1 and len(built_any) < want:
        b = structures.cabin(g, rng, x0, cy - 2, skin=cabin_skin, on=("b",))
        if not b.interior:
            x0 -= 1
            continue
        out.skins.update(b.skins)
        xs = [p[0] for p in b.interior]
        ys = [p[1] for p in b.interior]
        i = len(built_any)
        built_any.append({
            "name": names[i] if i < len(names) else _COMPARTMENTS[i],
            "level": 0,
            "x": min(xs), "y": min(ys),
            "w": max(xs) - min(xs) + 1, "h": max(ys) - min(ys) + 1,
        })
        # Clear of the one just built, plus a square of deck between them: two
        # deckhouses sharing a wall is one long shed.
        x0 = min(xs) - (max(xs) - min(xs) + 1) - 4
    out.rooms.extend(built_any)

    # Below decks: a real storey under the weather deck, reached by one hatch.
    # Negative base_ft is the whole trick — an upper floor and a hold are the
    # same machinery pointed opposite ways, and every distance, cover and area
    # check already folds a level's base height in.
    # The whole HULL, not the open deck: a hold runs under the mast, under the
    # rail and under the deckhouse too. Taking only walkable deck punched the
    # hold full of holes and left the floor beneath a cabin as an island of
    # planking surrounded by nothing, which no stair reached and no bulkhead
    # explained.
    deck_squares = [(x, y) for x, y in g.squares() if g.get(x, y) != surround]
    if len(deck_squares) < 12:
        return
    rows = [[VOID] * g.width for _ in range(g.height)]
    for x, y in deck_squares:
        rows[y][x] = FLOOR
    level = len(out.levels) + 1

    # Rooms the weather deck had no beam for go BELOW, which is where a ship
    # puts them anyway. A deckhouse is limited by how wide the hull is amidships
    # — a trader is nine squares across and holds exactly one — so a bastion
    # that flies would have been a vessel with an armoury and nothing else. The
    # hold is the whole footprint at -8 ft, and dividing it costs nothing new:
    # a BULKHEAD is a wall and a doorway is a doorway, both already tiles.
    over = names[len(built_any):]
    if over:
        xs_all = sorted({x for x, _y in deck_squares})
        bands = max(1, min(len(over), len(xs_all) // 5))
        step = len(xs_all) // bands
        cuts = [xs_all[i * step] for i in range(1, bands)]
        for bx in cuts:
            col = [y for y in range(g.height) if rows[y][bx] == FLOOR]
            for y in col:
                rows[y][bx] = WALL
            # One way through, or the hold is a row of sealed boxes and the
            # companionway lands in whichever of them the dice picked.
            door = [y for y in col
                    if rows[y][bx - 1] == FLOOR and rows[y][bx + 1] == FLOOR]
            if door:
                rows[door[len(door) // 2]][bx] = "/"
        edges = [xs_all[0]] + cuts + [xs_all[-1] + 1]
        for i, name in enumerate(over[:bands]):
            lo, hi = edges[i], edges[i + 1] - 1
            ys = [y for y in range(g.height)
                  if any(rows[y][x] == FLOOR for x in range(lo, hi + 1))]
            if not ys:
                continue
            out.rooms.append({"name": name, "level": level,
                              "x": lo, "y": min(ys),
                              "w": hi - lo + 1, "h": max(ys) - min(ys) + 1})

    out.levels.append({"name": "Below Decks", "base_ft": -8,
                       "terrain": ["".join(r) for r in rows],
                       "elevation": {}, "stairs": []})
    walkable = [sq for sq in deck_squares if g.get(sq[0], sq[1]) == "b"] \
        or deck_squares
    hx, hy = walkable[rng.randrange(len(walkable))]
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
    from . import vessels as _v
    cls = _v.rolled(out.seed, sky=False, width=g.width, height=g.height)
    stern = _hull(g, rng, "W", cls=cls)
    _rail(g, rng, "W")
    _rig_ship(g, rng, out, stern, cls=cls, surround="W")
    _scatter(g, rng, "o", 0.05, only_on=("b",))
    _scatter(g, rng, "%", 0.02, only_on=("b",))
    _connect_regions(g, rng)
    for x, y in g.squares():
        if g.get(x, y) not in ("W", "~"):
            out.elevation[f"{x},{y}"] = SHIP_FREEBOARD_FT
            continue
        # SWELL. The sea round a caravel was a perfectly flat plane, so the
        # depth map carried nothing about it and the painter kept the terrain
        # image's flat colour — a mat with the ship sitting on it. This is the
        # reef's lesson applied to the surface instead of the floor: give the
        # depth map relief and the picture follows.
        #
        # Elevation is per-square and drawn flat-topped, so the crests come out
        # FACETED rather than rounded. That was my objection and the call went
        # the other way: faceted water still reads as water in motion, which a
        # mirror-flat plane never did. Long, low and running one way, because
        # that is what swell is. Three feet changes nothing anybody will notice
        # — deep water is impassable to a walker, and a swimmer riding a crest
        # is three feet up, which is correct.
        wave = math.sin((x * 0.9 + y * 1.7) * 0.55) + 0.4 * math.sin(y * 0.9)
        out.elevation[f"{x},{y}"] = int(round(SWELL_FT * (wave + 1.4) / 2.8))
    out.description = (f"the deck of {cls.words} under sail — a single mast "
                       "stepped amidships, a rail you can see the sea through, "
                       "the captain's cabin aft and a hatch down into the hold")


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
    # A vault is a vault: forty-five feet across at the outside. A bigger board
    # holds MORE of them, opening off one another, rather than one burial hall
    # the size of a cathedral — which is what a single room filling the board
    # came to at 48x36.
    vaults = _bsp_cells(0, 0, g.width - 1, g.height - 1, rng,
                        min_side=_sq(ROOM_MIN_FT) + 2,
                        max_side=_sq(ROOM_MAX_FT) + 2)
    cells: list[tuple[int, int, int, int]] = []
    for vx0, vy0, vx1, vy1 in vaults:
        x0, y0 = vx0 + 1, vy0 + 1
        x1, y1 = min(g.width - 2, vx1 - 1), min(g.height - 2, vy1 - 1)
        if x1 - x0 < 3 or y1 - y0 < 3:
            continue
        _room(g, x0, y0, x1, y1)
        cells.append((x0, y0, x1, y1))
    if not cells:
        _room(g, 1, 1, g.width - 2, g.height - 2)
        cells = [(1, 1, g.width - 2, g.height - 2)]
    # Joined in a chain, so the vault reads as a catacomb rather than as a set
    # of sealed boxes. `_threshold_doors` then hangs a door where each way in
    # actually broke the wall.
    for (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) in zip(cells, cells[1:]):
        _carve_corridor(g, ((ax0 + ax1) // 2, (ay0 + ay1) // 2),
                        ((bx0 + bx1) // 2, (by0 + by1) // 2))
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
    # One bier, in the biggest vault, and the size a bier is.
    big = max(cells, key=lambda c: (c[2] - c[0]) * (c[3] - c[1]))
    bw = min(_sq(ROOM_MIN_FT), max(3, (big[2] - big[0]) // 2))
    bh = min(_sq(ROOM_MIN_FT), max(3, (big[3] - big[1]) // 2))
    bx = big[0] + ((big[2] - big[0]) - bw) // 2
    by = big[1] + ((big[3] - big[1]) - bh) // 2
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
    # HOW MANY grows with the board. Three to six was right at 24x18 and is a
    # scattering of islands across four times the mire — and a hummock is the
    # only dry ground here, so running out of them is running out of the one
    # thing that makes a bog worth fighting in. The HEIGHT does not scale: a
    # bog is flat wherever it is, which is what `swamp` says in RELIEF, and a
    # hummock is a step by definition.
    for _ in range(_for_area(g, rng.randint(3, 6), most=22)):
        _mound(g, rng, out, rng.randrange(g.width), rng.randrange(g.height),
               rng.uniform(1.5, 3.0), STEP_FT, on=("g", "m", "\""))


def _gen_pass(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, "R")
    # A winding walkable pass with a drop on one side.
    y = g.height // 2
    track: list[int] = []
    for x in range(g.width):
        # A TRACK, in feet: ten to thirty across, which is a cart at a squeeze
        # and two carts passing. A third of the board instead, and a bigger
        # pass came back a valley floor with a cliff at each edge.
        width = rng.randint(_sq(10), _sq(30))
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
    # HOW MANY benches is the country's: a pass through hill country is two
    # steps and one through the high peaks is four. The default is MOUNTAINS
    # rather than the generic middling answer — a mountain pass is mountainous
    # whether or not the DM said so, and reading the generic case here would
    # make the archetype gentler than it was before relief existed.
    rug = _ruggedness(out, default="mountains")
    tiers = 2 if rug < 0.45 else (3 if rug < 0.9 else 4)
    _plateaus(g, rng, out, tiers=tiers, on=(".", ",", "g"), face="R", ramps=2)
    out.description += (", the track stepping up in benches of rock with "
                        "cliffs between them")


def _gen_sewer(g: Grid, rng: random.Random, out: GeneratedMap) -> None:
    g.fill_rect(0, 0, g.width - 1, g.height - 1, WALL)
    # A TUNNEL is a tunnel: a channel a few feet across with a ledge either
    # side. Sized as a quarter of the board it widened into a canal, and a
    # bigger board came back as one enormous culvert instead of a system.
    channel = _sq(10)
    ledge = _sq(CORRIDOR_FT)
    bore = channel + ledge * 2
    # How many tunnels the board holds is a question about its HEIGHT, not its
    # area: a channel runs the length of the board, so more of them stack
    # across it. Scaled by area instead, a 48x36 sewer came back four lanes
    # deep with the walls between them eaten — seventy per cent open floor,
    # which is a cistern.
    runs = max(1, (g.height - bore) // (bore * 2 + 2))
    lanes = [int((i + 1) * g.height / (runs + 1)) for i in range(runs)]
    for mid in lanes:
        lo, hi = max(1, mid - bore // 2), min(g.height - 2, mid + bore // 2)
        g.fill_rect(1, lo, g.width - 2, hi, FLOOR)
        g.fill_rect(1, max(1, mid - channel // 2), g.width - 2,
                    min(g.height - 2, mid + channel // 2), "~")
        for x in range(2, g.width - 2, max(3, _for_area(g, 5, most=12))):
            g.set(x, mid, "b")
    # Cross-culverts, so a system is a system and not a set of parallel pipes.
    for i in range(len(lanes) - 1):
        cx = rng.randrange(3, max(4, g.width - 3))
        g.fill_rect(cx, lanes[i], cx + ledge - 1, lanes[i + 1], FLOOR)
    mid = lanes[len(lanes) // 2]
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
    from . import vessels as _v
    cls = _v.rolled(out.seed, sky=True, width=g.width, height=g.height)
    stern = _hull(g, rng, "^", plan="sky", cls=cls)
    _rail(g, rng, "^")
    _rig_ship(g, rng, out, stern, cls=cls, surround="^",
              cabin_skin={"steampunk": "plating",
                          "organic": "chitin"}.get(out.style, "hull"))
    _scatter(g, rng, "o", 0.05, only_on=("b",), mode="fly")
    out.mode = "fly"
    out.lighting = "bright"
    # The CLASS and the STYLE are two independent facts about a vessel — how big
    # and what shape, and what it is made of — so both are said. A grown chitin
    # courier and a grown chitin cruiser are different ships.
    out.description = {
        "steampunk": (f"the deck of {cls.words} — a riveted brass-and-iron "
                      "contraption, boiler venting, pipework along the rails, "
                      "open air past every side"),
        "organic": (f"the deck of {cls.words}, GROWN rather than built — "
                    "ridged chitin underfoot, veined and iridescent, open air "
                    "past every side"),
    }.get(out.style,
          f"the deck of {cls.words} under sail, rigging taut, open air past "
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
    # ...and HOW OFTEN is the country's business. A third of open boards used
    # to come back stepped whatever the DM said the country was, which is the
    # street's own fall arriving one level up. See _ruggedness.
    rug = _ruggedness(out)
    # Capped short of certainty: a board that is ALWAYS terraced stops being a
    # thing anyone notices, so even the high country sometimes gives a knoll.
    if rng.random() < min(0.85, rug):
        # The STEP is the country's too, and for the reason below: a ten-foot
        # face is the height a player has to decide about, and low country has
        # no business quoting it.
        _plateaus(g, rng, out, tiers=2, on=("g", ",", "\""), face="R", ramps=2,
                  step_ft=LEDGE_FT if rug >= 0.4 else STEP_FT)
        out.description = ("open ground stepping up in low mesas, rock faces "
                           "between them")
    elif rng.random() < 0.3 + rug:
        # A knoll on a plain is a STEP; in hill country it is a ledge worth
        # taking. Height is a rules number, so which one is a decision and not
        # a jitter — see "never vary a height the RULES quote".
        _mound(g, rng, out, rng.randrange(g.width // 4, 3 * g.width // 4),
               rng.randrange(g.height // 4, 3 * g.height // 4),
               rng.uniform(2.5, 4.5),
               LEDGE_FT if rug >= 0.4 else STEP_FT, on=("g", ",", "\""))
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


#: Words that decide what a vessel is MADE of, per style. Matched on word
#: boundaries for the `landmark_for` reason — "arch" lives inside "archer" —
#: and deliberately small: this is not a vocabulary anybody has to learn, it is
#: the words people already use when they describe a flying ship.
_STYLE_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("steampunk", ("brass", "brazen", "steam", "riveted", "rivets", "boiler",
                   "iron", "clockwork", "pipework", "copper", "gearwork")),
    ("organic",   ("grown", "living", "chitin", "chitinous", "carapace",
                   "coral", "bone", "veined", "iridescent", "shell")),
    ("timber",    ("timber", "oak", "wooden", "wood", "planked", "sail",
                   "sails", "canvas")),
)


def style_for(text: Optional[str]) -> str:
    """The board style a scrap of description asks for, or "" for none.

    A skyship's style is the one whole-board choice that is a genuine CHOICE
    rather than a material fact, and it was rolled from the seed — so a player
    who paid to build a riveted brass contraption got a timber ship five times
    in ten, and everything downstream (materials, silhouettes, what the painter
    is told) followed that one wrong word.

    Empty when nothing is said, because the seed deciding is the right answer
    for a vessel nobody has described. A DESCRIBED vessel is not that.
    """
    t = f" {(text or '').strip().lower()} "
    if not t.strip():
        return ""
    for style, words in _STYLE_WORDS:
        for w in words:
            if re.search(rf"\b{re.escape(w)}\b", t):
                return style
    return ""


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
                 landmarks: Sequence[str] = (),
                 rooms: Sequence[str] = (),
                 relief: Optional[dict] = None) -> GeneratedMap:
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

    ``rooms`` names compartments a generator that BUILDS rooms should call its
    own — today a vessel's. The same bargain as ``landmarks``: the caller says
    what the rooms are, the hull decides how many of them there is deck for and
    where they stand.
    """
    archetype = archetype if archetype in ARCHETYPES else archetype_for(archetype)
    width = max(8, min(60, int(width)))
    height = max(8, min(60, int(height)))
    seed = random.randint(1, 2**31 - 1) if seed is None else int(seed)
    rng = _rng(seed)
    grid = Grid.blank(width, height)
    out = GeneratedMap(grid=grid, archetype=archetype, seed=seed, style=style,
                       biome=biome, relief=dict(relief or {}),
                       wanted_rooms=tuple(str(r) for r in rooms if str(r).strip()))
    ARCHETYPES[archetype](grid, rng, out)

    # Every board must be one connected space and have room to stand — judged in
    # the medium it's fought in, so an open-water board isn't condemned for
    # being unwalkable.
    _connect_regions(grid, rng, out.mode)
    # A DELIBERATELY sparse board is not a collapsed one. A vessel is a small
    # solid thing in a large expanse of sea or sky, and judging it by "an eighth
    # of the board must be walkable" condemned every ship smaller than a galleon
    # to be replaced by a meadow. The floor for those is an absolute one: is
    # there a deck to fight on at all.
    floor = (VESSEL_DECK_FLOOR if archetype in SPARSE_ARCHETYPES
             else min((width * height) // 8, PLAYABLE_FLOOR))
    if len(_walkable(grid, out.mode)) < floor:
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

    # Water lies in a DEPRESSION, and it does so board-wide rather than
    # generator by generator: any layout that lays a pool gets its bed cut
    # below its own bank. Only ever downward, so a generator that already dug
    # its channel (a sewer's sludge run under its walkways) keeps what it dug.
    from . import water as _water
    _rows = grid.to_rows()
    _water.sink(_rows, out.elevation, mode=out.mode)
    out.water = _water.surfaces(_rows, out.elevation, mode=out.mode)

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
