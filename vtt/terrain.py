"""
Tile taxonomy for the tactical grid.

A board is stored as one string per row, one character per 5-ft square — compact
enough to sit in a JSON column, cheap to ship over the socket, and readable in a
log or a prompt. Every code maps to a :class:`Tile` describing what the square
*does* mechanically: what it costs to enter, whether it blocks sight, what cover
it grants, and whether standing in it hurts.

    from vtt.terrain import Grid, TILES

    g = Grid.blank(20, 15)
    g.set(4, 4, "#")            # a wall
    g.cost(4, 4)                # None -> impassable
    TILES["~"].move_cost        # 2    -> difficult terrain

The art layer (:mod:`vtt.art`) reads the same codes to describe the room to the
diffusion model, so what the players see matches what the rules enforce.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Optional


@dataclass(frozen=True)
class Tile:
    code: str
    name: str
    # Feet to enter a square of this tile at normal speed (5 = open ground,
    # 10 = difficult terrain). ``None`` means impassable by walking.
    move_cost_ft: Optional[int]
    blocks_sight: bool = False
    # Cover this tile grants to a creature *behind* it: none | half |
    # three-quarters | total.
    cover: str = "none"
    # Standing here is dangerous (lava, spikes) — the DM is reminded, the UI
    # hatches the square.
    hazard: bool = False
    # Water/chasm: a walking creature can't simply stand here, but a swimmer or
    # flier can. Impassable tiles with ``traversable_flying`` are crossable from
    # the air.
    traversable_flying: bool = False
    traversable_swimming: bool = False
    # How tall this obstacle SCREENS, in feet — for a creature sheltering
    # behind it. Cover is really a question about height, and the DMG's own
    # definition of total cover is "completely concealed by an obstacle", but
    # without a number the engine could only give a crate one fixed rating,
    # whoever was behind it and however they were lying.
    #
    # Set ONLY on obstacles that span their square and are limited by HEIGHT: a
    # crate, a low wall, an overturned table. A pillar or a tree is rated
    # three-quarters because it is NARROW, and lying down does not make a trunk
    # any wider; a portcullis is bars, and no amount of lying flat makes them
    # opaque. 0 means "height is not what limits this cover", which is the
    # right answer for all of them. The Oracle's own tuning, like _BREAKABLE.
    cover_height_ft: int = 0
    # A short phrase the battlemap prompt and the DM board use.
    art: str = ""


def _t(code: str, name: str, cost: Optional[int], **kw) -> Tile:
    return Tile(code=code, name=name, move_cost_ft=cost, **kw)


#: Ground whose SURFACE may slope between squares.
#:
#: Elevation is stored per square as whole feet, so a hillside is drawn as
#: terraces: every square a flat plate at its own height with a step to its
#: neighbour. Real ground does not do that, and the terracing is most of why an
#: outdoor board reads as stacked blocks — the mountain pass is a flight of
#: stairs, a meadow with a knoll on it is a wedding cake.
#:
#: Smoothing is DRAWING and changes no rule: a creature still stands at its
#: square's own stated height, every distance and cover check still reads the
#: integer, and only the surface BETWEEN square centres bends. Two guards keep
#: that honest. It applies to natural ground only — a flagstone floor, a road,
#: a bridge and a ship's deck are LAID, and laid things are flat — and only
#: across a difference of one STEP. A LEDGE is the height the rules make you
#: decide about, and sloping one would draw a ramp where the board says there
#: is a drop.
#:
#: WATER is not on this list, and the omission is the point. A liquid surface
#: is LEVEL — that is what being a liquid means — so a pool averaged with the
#: hummock beside it came back tilted, running visibly uphill into the bank. On
#: a board fought UNDER the water there is no surface in view and the seabed is
#: ordinary ground, which is why the ``seabed-*`` skins carry ``soft`` and the
#: codes do not: the skin answers first, exactly as it does for scree and
#: cobbles sharing a ``.``.
SOFT_GROUND = frozenset({"g", "s", ",", '"', "m"})

#: The codes that are a body of WATER, as opposed to ground that happens to be
#: wet. See :mod:`vtt.water`.
WATER_CODES = frozenset({"~", "W"})

#: How far below its bank a pool's surface is DRAWN, in feet. Drawing only —
#: without it the sheet is flush with the bank it meets and the two z-fight
#: along every shore. Small enough that nothing reads as a step.
WATERLINE_DROP_FT = 0.4

#: The largest difference two squares may have and still be joined by a slope,
#: in feet. One STEP. See ``mapgen.STEP_FT`` / ``LEDGE_FT``: a step is cheap to
#: climb and free to come down, so smoothing it misleads nobody; a ledge is a
#: fall, and a picture that ramps it is a picture that lies about the one
#: height a player has to decide about.
SMOOTH_STEP_FT = 5

#: How far natural ground WANDERS between one corner and the next, in feet.
#:
#: The other half of the same complaint, and the one the smoothing above cannot
#: reach: outdoor relief is mostly built from LEDGES, which must stay hard, so
#: everything between them is still a dead-flat plate. Real ground is never
#: flat, and a meadow drawn as one is a billiard table with grass on it.
#:
#: This is the ``HEIGHT_JITTER`` precedent applied to the ground: a DRAWN
#: wander that no rule reads. Kept under a foot and a half on purpose — a
#: creature still stands at its square's stated elevation, every distance,
#: cover and area check reads the integer, and `drawnTopFt` (which decides who
#: is hidden) ignores it entirely. It rides on the smoothed corner, so it
#: appears only where the ground was already allowed to slope: never on a laid
#: floor, never across a ledge.
GROUND_RIPPLE_FT = 1.2

#: Every legal tile code. Unknown codes fall back to open floor.
TILES: dict[str, Tile] = {t.code: t for t in (
    # --- open ground ---
    _t(".", "floor", 5, art="open floor"),
    _t("g", "grass", 5, art="grass"),
    _t("s", "sand", 5, art="packed sand"),
    _t("b", "bridge", 5, art="wooden bridge planks"),
    _t("=", "road", 5, art="cobbled road"),
    # --- difficult ground ---
    _t(",", "rubble", 10, art="loose rubble and broken stone"),
    _t("\"", "undergrowth", 10, art="thick undergrowth and brambles"),
    _t("~", "shallow water", 10, traversable_swimming=True, art="shallow water"),
    _t("m", "mud", 10, art="sucking mud"),
    _t("i", "ice", 10, art="cracked ice"),
    _t("%", "webs", 10, art="thick spider webs"),
    _t("u", "stairs", 10, art="stone stairs"),
    # --- blocking ---
    _t("#", "wall", None, blocks_sight=True, cover="total", art="stone wall"),
    _t("R", "rock face", None, blocks_sight=True, cover="total",
       art="rough rock face"),
    # "thick tree trunk" was the art phrase, and it described the old geometry
    # exactly: a post. What the board draws now is a crown on a trunk, and what
    # an overhead camera sees of it is the crown.
    _t("T", "tree", None, blocks_sight=True, cover="three-quarters",
       art="broad-canopied tree"),
    _t("O", "pillar", None, blocks_sight=True, cover="three-quarters",
       art="carved stone pillar"),
    _t("o", "crate", None, cover="half", cover_height_ft=4,
       art="stacked crates and barrels"),
    _t("n", "furniture", None, cover="half", cover_height_ft=3,
       art="overturned table and benches"),
    _t("w", "low wall", None, cover="half", cover_height_ft=3,
       art="waist-high broken wall"),
    _t("W", "deep water", None, traversable_swimming=True, art="deep dark water"),
    # Open sky: a flier's ground. Unlike a chasm this is not a hazard — the
    # board IS the air, so crossing it is ordinary movement for anything aloft.
    _t("^", "open sky", None, traversable_flying=True, art="open sky, cloud far below"),
    _t("x", "chasm", None, traversable_flying=True, hazard=True, art="yawning chasm"),
    _t("l", "lava", None, traversable_flying=True, hazard=True, art="molten lava"),
    _t("f", "fire", 10, hazard=True, art="burning wreckage"),
    _t("A", "altar", None, cover="half", cover_height_ft=4,
       art="carved stone altar"),
    # --- stateful furniture (state lives on TacticalMap.doors) ---
    _t("+", "door", None, blocks_sight=True, cover="total",
       art="heavy closed door"),
    _t("/", "open door", 5, art="open doorway"),
    _t("p", "portcullis", None, cover="three-quarters",
       art="iron portcullis"),
    # --- out of play ---
    _t(" ", "void", None, art="empty darkness"),
)}

FLOOR = "."
WALL = "#"
VOID = " "

#: Codes a generator may scatter as decoration without changing connectivity.
DECOR_CODES = ("o", "n", "O", "T", ",", "\"")


def tile(code: str) -> Tile:
    """Look up a tile, defaulting to open floor for unknown codes."""
    return TILES.get(code, TILES[FLOOR])


def tile_rule(code: str) -> str:
    """What a square DOES, in a few words — for the DM prompt's legend.

    Derived from the Tile itself rather than written out per code, so a tile
    added later describes itself correctly without anyone remembering to update
    a second table.
    """
    t = tile(code)
    if t.move_cost_ft is None:
        if t.traversable_swimming and t.traversable_flying:
            base = "swimmers and fliers only"
        elif t.traversable_swimming:
            base = "swimmers only — a walker can't be here"
        elif t.traversable_flying:
            base = "fliers only — a walker can't be here"
        else:
            base = "impassable"
        if t.cover != "none" and not (t.traversable_swimming or t.traversable_flying):
            base += f", {t.cover} cover"
    elif t.move_cost_ft > 5:
        base = "difficult, costs double"
        if t.traversable_swimming:
            base += "; swimmable"
    else:
        base = "open"
    if t.blocks_sight and t.move_cost_ft is None:
        base += ", blocks sight"
    if t.hazard:
        base += ", HAZARD"
    return base


#: How tall each tile STANDS, in feet — what the isometric board extrudes and
#: what the depth map handed to the painter is built from.
#:
#: Distinct from ``cover_height_ft``, which answers a rules question ("how tall
#: does this screen a creature") and is 0 for anything whose cover is not
#: limited by height. This answers a drawing question, so a pillar needs a
#: figure even though the rules never care how tall it is. Where the rules DO
#: have a number, it is used — a crate is four feet in both.
#:
#: Mirrored by TILE_HEIGHT_FT in activity-ui/src/lib/boardView.ts. This side is
#: authoritative; keep them in step.
STAND_HEIGHT_FT: dict[str, int] = {
    "#": 10, "R": 10,          # structure
    # A tree is 18 ft, not the 12 it was. Twelve is a sapling, and drawn as a
    # trunk with a crown on it (see isocam.OBJECT_VARIANTS) a sapling reads as a
    # bush; the crown needs room above head height to be a canopy at all. It
    # costs the rules nothing — a tree's cover is three-quarters because it is
    # NARROW, so `cover_height_ft` is 0 and this number is drawing only.
    "T": 18, "O": 10,          # tree, pillar
    "+": 8, "p": 8,            # closed door, portcullis
    "o": 4, "A": 4, "n": 3, "w": 3,   # these four carry a cover_height_ft
}


def tile_height_ft(code: str) -> int:
    """How tall this tile stands, in feet. 0 is floor you walk on."""
    return STAND_HEIGHT_FT.get(code, 0)


def cover_height_ft(code: str) -> int:
    """How tall the obstacle on this square stands, in feet. 0 for open ground."""
    return int(tile(code).cover_height_ft or 0)


#: How much of the world a creature presents to be shot at, by size — standing,
#: and flat on the ground. The prone figures are what make lying down behind a
#: crate mean something: a Medium creature standing is six feet of target and a
#: four-foot crate covers most of it, while the same creature prone is about a
#: foot of target and the crate covers ALL of it.
#:
#: The Oracle's own numbers. 5e never states a creature's height as a rule, but
#: it does define total cover as "completely concealed by an obstacle", and
#: concealment is a question about height whether or not anyone writes it down.
_STANDING_HEIGHT_FT = {"tiny": 2, "small": 4, "medium": 6,
                       "large": 10, "huge": 15, "gargantuan": 20}
_PRONE_HEIGHT_FT = {"tiny": 1, "small": 1, "medium": 1,
                    "large": 2, "huge": 3, "gargantuan": 4}


def profile_height_ft(size: str, prone: bool = False) -> int:
    """How tall a target of this size is — the height an obstacle must match
    to conceal it completely."""
    key = (size or "medium").strip().lower()
    table = _PRONE_HEIGHT_FT if prone else _STANDING_HEIGHT_FT
    return int(table.get(key, table["medium"]))


#: What a destructible square LEAVES BEHIND, and what it takes to break it.
#:
#: Keyed by tile code: (becomes, armour class, hit points, material). These are
#: THE ORACLE'S OWN tuning, not a table copied from anywhere — sensible numbers
#: for this game, chosen so that smashing a crate is a turn well spent and
#: breaching a stone wall is a project. Every value is overridable per-square by
#: whoever places the object, so a table that wants different numbers sets them.
#:
#: A code absent from here cannot be broken by ordinary damage. That is
#: deliberate for floors, water and open sky: there is nothing there to break.
_BREAKABLE: dict[str, tuple[str, int, int, str]] = {
    # code:  becomes, AC, HP, material
    "o": (",", 13, 12, "wood"),      # stacked crates and barrels
    "n": (",", 12, 10, "wood"),      # overturned furniture -> splinters
    "T": (",", 13, 40, "wood"),      # a tree trunk
    "O": (",", 17, 60, "stone"),     # a carved pillar
    "w": (",", 15, 30, "stone"),     # a waist-high broken wall
    "A": (",", 17, 45, "stone"),     # an altar
    "+": ("/", 15, 25, "wood"),      # a door — smashed OPEN, not to rubble
    "p": (",", 19, 45, "metal"),     # an iron portcullis
    "#": (",", 17, 90, "stone"),     # a wall: a breach, if you have the time
    "R": (",", 17, 150, "stone"),    # a rock face: bring tools
}

#: Damage every inanimate thing shrugs off. Deliberately just the two that are
#: uncontroversial for objects — anything more opinionated belongs in per-square
#: overrides rather than baked in here.
OBJECT_IMMUNITIES = ("poison", "psychic")

#: Material -> damage types it resists. Our tuning, same as above.
_MATERIAL_RESISTS = {
    "stone": ("piercing", "slashing", "lightning"),
    "metal": ("piercing", "slashing", "cold"),
    "wood": ("piercing",),
}


def is_breakable(code: str) -> bool:
    return code in _BREAKABLE


def object_stats(code: str) -> Optional[dict]:
    """Default AC/HP/material for a breakable square, or None."""
    row = _BREAKABLE.get(code)
    if row is None:
        return None
    becomes, ac, hp, material = row
    return {"becomes": becomes, "ac": ac, "hp": hp, "hp_max": hp,
            "material": material, "name": tile(code).name,
            "resists": list(_MATERIAL_RESISTS.get(material, ())),
            "immune": list(OBJECT_IMMUNITIES)}


#: Discrete objects that are DRAWN as sprites on their own square rather than
#: left to the battlemap painting. Anything a player must be able to point at —
#: and anything that can break — belongs here, because a text prompt cannot put
#: a pillar on square 6,5 and a painted pillar cannot become rubble.
#:
#: Walls and rock faces are deliberately absent: they are structure, and the
#: ControlNet floorplan puts those in the painting itself.
#:
#: Every subject names the VIEW before it names the thing. A battlemap is
#: orthographic — the camera is on the ceiling — but the model's prior for
#: "a wooden door" is a door photographed from in front, and a trailing "seen
#: from above" does not outweigh it. So the subject leads with the part of the
#: object that actually faces a viewer directly overhead (a pillar's capital, a
#: crate's lid, a tree's crown); the elevation view has to be argued out of the
#: prompt, not appended to it.
OBJECT_SPRITES: dict[str, str] = {
    "O": ("the flat circular capital of a stone pillar directly below the "
          "viewer, a ring of stone on the floor, orthographic top-down"),
    "o": ("the square plank lids of three large iron-banded wooden crates "
          "packed together directly below the viewer, big square boards of "
          "dark timber, orthographic top-down"),
    "n": ("an overturned wooden table and its benches lying flat on the floor "
          "directly below the viewer, orthographic top-down"),
    "T": ("the crown of a broad tree directly below the viewer, a circle of "
          "foliage, orthographic top-down"),
    "A": ("the flat top slab of a carved stone altar directly below the "
          "viewer, orthographic top-down"),
    "w": ("the flat coping stones along the top of a low broken wall directly "
          "below the viewer, a straight band of masonry, orthographic top-down"),
    "+": ("a closed wooden door lying flat in its stone frame directly below "
          "the viewer, a plain banded rectangle spanning the opening, "
          "orthographic top-down floorplan"),
    "/": ("a door swung wide open against the wall beside its empty stone "
          "frame directly below the viewer, orthographic top-down floorplan"),
    "p": ("an iron portcullis of vertical bars filling a stone opening "
          "directly below the viewer, orthographic top-down floorplan"),
}

#: What a diffusion model must NOT do with a battlemap sprite. Perspective is
#: the whole fight: anything with a horizon, a vanishing point or a visible
#: front face reads as a picture propped up on the floor.
SPRITE_NEGATIVE = (
    "side view, front view, three-quarter view, isometric, perspective, "
    "elevation, eye level, horizon, sky, background wall, room, interior "
    "scene, vanishing point, shadow cast sideways, people, text, label, "
    "border, frame"
)

#: Short names for the board's own labels — the tile name where it's already
#: short, something shorter where it isn't.
SPRITE_LABELS: dict[str, str] = {
    "n": "table",
    "o": "crates",
    "/": "doorway",
    "p": "gate",
}


def sprite_subject(code: str) -> Optional[str]:
    """What to draw on this square, or None if the painting handles it."""
    return OBJECT_SPRITES.get(code)


def sprite_label(code: str) -> str:
    """A word or two naming this square's object, for drawing on the board."""
    return SPRITE_LABELS.get(code) or tile(code).name


_LABEL_BY_NAME = {tile(c).name: v for c, v in SPRITE_LABELS.items()}


def short_name(name: str) -> str:
    """The board-label form of a tile name, for text recorded as a name.

    Wreckage remembers what it WAS as a name ("furniture"), not as a code, so
    this is the same shortening :func:`sprite_label` does, reached from the
    other side.
    """
    return _LABEL_BY_NAME.get(name, name)


#: (codes that mean "this is really here", what to forbid when none of them
#: are). Deliberately short: only terrain that changes how a square is ENTERED,
#: because that is the only kind a player can be wrong about in a way that
#: costs them something. Fire is absent on purpose — negating flame would take
#: the torches out of every dungeon to prevent a confusion nobody has.
_ART_TERRAIN_NEGATIVES: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"~", "W"}),
     "water, pool, puddle, standing water, stream, river, flooded floor, "
     "wet reflective ground"),
    (frozenset({"l"}), "lava, magma, molten rock"),
    (frozenset({"x"}), "chasm, pit, hole in the floor, bottomless drop"),
    (frozenset({"i"}), "ice, frost, frozen surface"),
    (frozenset({"^"}), "open sky, clouds, empty air, aerial view of clouds"),
)

#: Apertures: a way THROUGH a wall run, rather than a thing standing on floor.
#: They are drawn and reasoned about differently from every other object —
#: a door belongs to the wall it interrupts, not to the square it occupies.
APERTURES = frozenset({"+", "/", "p"})

#: What counts as "the wall continues this way" when working out which way an
#: aperture faces. Other apertures count: a double door is still one run.
_RUN_STRUCTURE = frozenset({"#", "R", "w"}) | APERTURES


def aperture_axis(grid: "Grid", x: int, y: int) -> str:
    """Which way the wall runs through an aperture: ``"ew"``, ``"ns"`` or ``""``.

    A door is a gap in a wall, so it has a direction — and everything that
    draws one needs it. Read from the grid rather than stored, for the same
    reason :meth:`VttEngine.objects_for` reads the grid: the wall around the
    door is already the truth about which way it faces, and a second record
    would only be a second thing to keep in step.

    ``""`` means the neighbours don't agree on a run (a door in open ground, or
    at a corner) — callers should fall back to drawing it square.
    """
    def runs(ax: int, ay: int) -> bool:
        if not grid.in_bounds(ax, ay):
            return True                 # the map edge is as solid as a wall
        return grid.get(ax, ay) in _RUN_STRUCTURE

    ew = runs(x - 1, y) and runs(x + 1, y)
    ns = runs(x, y - 1) and runs(x, y + 1)
    if ew and not ns:
        return "ew"
    if ns and not ew:
        return "ns"
    return ""


def required_mode(code: str) -> Optional[str]:
    """The medium a square DEMANDS, if it demands one. None for ordinary ground.

    This is what lets the board move a creature into the right medium instead of
    expecting the narration to remember: a square of deep water is swum, a
    square of open sky is flown, and nothing has to be told twice.
    """
    t = tile(code)
    if t.move_cost_ft is not None:
        return None
    if t.traversable_swimming:
        return "swim"
    if t.traversable_flying:
        return "fly"
    return None


class Grid:
    """A mutable rectangular grid of tile codes.

    Row-major: ``rows[y][x]``. Coordinates are square indices, origin top-left.
    """

    __slots__ = ("width", "height", "rows")

    def __init__(self, rows: list[str]):
        self.rows = [list(r) for r in rows]  # type: ignore[assignment]
        self.height = len(self.rows)
        self.width = len(self.rows[0]) if self.rows else 0
        # Normalise ragged input so indexing is always safe.
        for r in self.rows:
            if len(r) < self.width:
                r.extend([FLOOR] * (self.width - len(r)))
            elif len(r) > self.width:
                del r[self.width:]

    # ----- construction -----

    @classmethod
    def blank(cls, width: int, height: int, fill: str = FLOOR) -> "Grid":
        width, height = max(1, int(width)), max(1, int(height))
        return cls([fill * width for _ in range(height)])

    @classmethod
    def from_rows(cls, rows: Optional[Iterable[str]]) -> "Grid":
        rows = [str(r) for r in (rows or [])]
        return cls(rows) if rows else cls.blank(1, 1)

    def to_rows(self) -> list[str]:
        return ["".join(r) for r in self.rows]

    def copy(self) -> "Grid":
        return Grid(self.to_rows())

    # ----- access -----

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def get(self, x: int, y: int) -> str:
        if not self.in_bounds(x, y):
            return VOID
        return self.rows[y][x]

    def set(self, x: int, y: int, code: str) -> None:
        if self.in_bounds(x, y):
            self.rows[y][x] = code

    def tile_at(self, x: int, y: int) -> Tile:
        return tile(self.get(x, y))

    def fill_rect(self, x0: int, y0: int, x1: int, y1: int, code: str) -> None:
        for y in range(max(0, y0), min(self.height, y1 + 1)):
            for x in range(max(0, x0), min(self.width, x1 + 1)):
                self.rows[y][x] = code

    def outline_rect(self, x0: int, y0: int, x1: int, y1: int, code: str) -> None:
        for x in range(max(0, x0), min(self.width, x1 + 1)):
            self.set(x, y0, code)
            self.set(x, y1, code)
        for y in range(max(0, y0), min(self.height, y1 + 1)):
            self.set(x0, y, code)
            self.set(x1, y, code)

    def squares(self) -> Iterator[tuple[int, int]]:
        for y in range(self.height):
            for x in range(self.width):
                yield x, y

    # ----- mechanics -----

    def cost(self, x: int, y: int, *, mode: str = "walk") -> Optional[int]:
        """Feet to enter this square, or ``None`` if the mode can't enter it."""
        t = self.tile_at(x, y)
        if mode == "fly":
            # Fliers ignore ground cost and cross chasms, but not solid matter.
            if t.move_cost_ft is None and not t.traversable_flying and t.cover == "total":
                return None
            if t.move_cost_ft is None and not t.traversable_flying:
                return None if t.cover in ("total", "three-quarters") else 5
            return 5
        if mode == "swim":
            if t.traversable_swimming:
                return 10
            return t.move_cost_ft
        return t.move_cost_ft

    def passable(self, x: int, y: int, *, mode: str = "walk") -> bool:
        return self.cost(x, y, mode=mode) is not None

    def blocks_sight(self, x: int, y: int) -> bool:
        return self.tile_at(x, y).blocks_sight

    def cover_at(self, x: int, y: int) -> str:
        return self.tile_at(x, y).cover

    def hazard_at(self, x: int, y: int) -> bool:
        return self.tile_at(x, y).hazard

    # ----- rendering -----

    def render(self) -> str:
        """The board as text — what the DM prompt sees."""
        return "\n".join(self.to_rows())

    def legend(self, *, rules: bool = False) -> str:
        """Legend for the codes actually present, e.g. ``# wall, ~ water``.

        With ``rules``, each entry also says what the square DOES — which is
        what the DM prompt needs. Naming a tile without its rule is how a model
        ends up narrating a wade across deep water that the board then refuses:
        it could see the ``W``, but nothing told it what a ``W`` costs.
        """
        seen: list[str] = []
        for row in self.rows:
            for c in row:
                if c not in seen and c != FLOOR:
                    seen.append(c)
        if not rules:
            return ", ".join(f"{c} {tile(c).name}" for c in seen)
        return "; ".join(f"{c} {tile(c).name} ({tile_rule(c)})" for c in seen)

    def absent_terrain_negative(self) -> str:
        """What the PAINTING must not invent, because the rules don't have it.

        The art is a texture and the grid is the truth, which settles who wins
        an argument — it does not stop the argument happening. A model handed a
        dungeon floorplan will cheerfully paint a pool across a room the tiles
        say is dry flagstone, and then a player asks how deep it is and the DM,
        who only ever sees the grid, says there is no water there. Nobody is
        wrong and everybody is confused.

        So terrain that would change how a square is ENTERED is forbidden to
        the picture unless the grid actually has it. Cosmetic invention is
        still welcome; this is a short list on purpose. Derived from the grid,
        so a given layout always gets the same negative and the art cache
        (keyed on that same grid) stays coherent.
        """
        present = {c for row in self.rows for c in row}
        return ", ".join(phrase for codes, phrase in _ART_TERRAIN_NEGATIVES
                         if present.isdisjoint(codes))

    def describe(self) -> str:
        """A short prose inventory of the terrain, for the art prompt."""
        counts: dict[str, int] = {}
        for row in self.rows:
            for c in row:
                counts[c] = counts.get(c, 0) + 1
        parts = []
        for code, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            t = tile(code)
            if code in (FLOOR, VOID) or not t.art:
                continue
            if n >= max(4, (self.width * self.height) // 60):
                parts.append(t.art)
        return ", ".join(parts[:8])


def difficult(code: str) -> bool:
    t = tile(code)
    return t.move_cost_ft is not None and t.move_cost_ft > 5
