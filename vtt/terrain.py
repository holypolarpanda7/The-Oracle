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
    # A short phrase the battlemap prompt and the DM board use.
    art: str = ""


def _t(code: str, name: str, cost: Optional[int], **kw) -> Tile:
    return Tile(code=code, name=name, move_cost_ft=cost, **kw)


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
    _t("R", "rock face", None, blocks_sight=True, cover="total", art="rough rock face"),
    _t("T", "tree", None, blocks_sight=True, cover="three-quarters", art="thick tree trunk"),
    _t("O", "pillar", None, blocks_sight=True, cover="three-quarters", art="carved stone pillar"),
    _t("o", "crate", None, cover="half", art="stacked crates and barrels"),
    _t("n", "furniture", None, cover="half", art="overturned table and benches"),
    _t("w", "low wall", None, cover="half", art="waist-high broken wall"),
    _t("W", "deep water", None, traversable_swimming=True, art="deep dark water"),
    # Open sky: a flier's ground. Unlike a chasm this is not a hazard — the
    # board IS the air, so crossing it is ordinary movement for anything aloft.
    _t("^", "open sky", None, traversable_flying=True, art="open sky, cloud far below"),
    _t("x", "chasm", None, traversable_flying=True, hazard=True, art="yawning chasm"),
    _t("l", "lava", None, traversable_flying=True, hazard=True, art="molten lava"),
    _t("f", "fire", 10, hazard=True, art="burning wreckage"),
    _t("A", "altar", None, cover="half", art="carved stone altar"),
    # --- stateful furniture (state lives on TacticalMap.doors) ---
    _t("+", "door", None, blocks_sight=True, cover="total", art="heavy closed door"),
    _t("/", "open door", 5, art="open doorway"),
    _t("p", "portcullis", None, cover="three-quarters", art="iron portcullis"),
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

    def legend(self) -> str:
        """Legend for the codes actually present, e.g. ``# wall, ~ water``."""
        seen: list[str] = []
        for row in self.rows:
            for c in row:
                if c not in seen and c != FLOOR:
                    seen.append(c)
        return ", ".join(f"{c} {tile(c).name}" for c in seen)

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
