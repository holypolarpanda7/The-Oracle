"""Buildings a creature can get INSIDE, composed from tiles that already exist.

A camp's tents were four squares of ``n`` — impassable furniture three feet
tall — so a creature could never be in a tent, only beside one, and a token
standing on the next square read as a soldier perched on the canvas. A bridge
had nothing at either end. A ship had a deck and no cabin, no hold and no way
below.

**No new rules primitive was needed for any of that**, which is the useful
finding here. A tent is a walkable floor, a wall ring that blocks sight, and a
gap to get in by — three things the tile taxonomy has had since the beginning.
A watchtower is the same plus an upper storey and a ladder, and
:meth:`VttEngine.add_level` / :meth:`add_stairs` have existed since floors went
in. What was missing was somebody to COMPOSE them, and a way to say the walls
are canvas rather than quarried stone — which is what :mod:`vtt.skins` is for.

So everything here writes ordinary tile codes and records a skin beside them.
Cover, movement, sight and breakability all keep working with no change,
because to the rules a tent wall is a wall.

**Scale is a rules question, not a drawing one.** A structure has to be big
enough inside to be worth entering: a 5-ft interior holds one Medium creature
and nothing else, so nothing here builds one smaller than 10 ft across. That is
the difference between somewhere to fight from and a box with a hole in it.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from .terrain import FLOOR, WALL, Grid

#: The smallest interior worth entering, in squares. One square holds one
#: Medium creature with no room to move; two by two holds a squad, or one
#: creature plus what it is guarding.
MIN_INTERIOR = 2


@dataclass
class Built:
    """What a structure put on the board, for the generator to record."""

    #: {"x,y": skin name} — the materials this structure is made of.
    skins: dict[str, str]
    #: Squares inside it, which a generator should keep clear of scatter.
    interior: list[tuple[int, int]]
    #: [{"x","y","state","name"}] for any door hung in it.
    doors: list[dict]
    #: Where somebody standing in the doorway would be.
    door_at: Optional[tuple[int, int]] = None

    def merge(self, other: "Built") -> "Built":
        self.skins.update(other.skins)
        self.interior.extend(other.interior)
        self.doors.extend(other.doors)
        return self


def _empty() -> Built:
    return Built(skins={}, interior=[], doors=[])


def _fits(g: Grid, x0: int, y0: int, w: int, h: int,
          on: tuple[str, ...]) -> bool:
    """Room for a structure here, on ground of the right kind?"""
    if x0 < 0 or y0 < 0 or x0 + w > g.width or y0 + h > g.height:
        return False
    return all(g.get(x, y) in on
               for x in range(x0, x0 + w) for y in range(y0, y0 + h))


def shelter(g: Grid, rng: random.Random, x0: int, y0: int, w: int, h: int, *,
            skin: str, wall: str = WALL, floor: str = FLOOR,
            door: str = "/", on: tuple[str, ...] = (),
            interior_floor: Optional[str] = None,
            margin: int = 1) -> Built:
    """A walled enclosure with a floor inside and one way in. The base shape.

    Tents, huts, cabins and tower bases are all this with different materials
    and different sizes, which is why it is one function rather than four. The
    opening is a real ``/`` open doorway, not a missing wall: the engine already
    knows what a doorway is, it can be shut later if somebody hangs a door in
    it, and it reads on the board as a way in rather than as a gap in a ruin.

    Returns an empty :class:`Built` and touches nothing if it will not fit,
    so a generator can simply try a few places.
    """
    if w - 2 < MIN_INTERIOR or h - 2 < MIN_INTERIOR:
        # Refused rather than shrunk. A structure too small to stand inside is
        # the thing this module exists to stop building.
        return _empty()
    # The margin is not tidiness. Two shelters built back to back share a wall
    # and can seal a pocket between them, and the generator's connectivity net
    # then carves a corridor straight through somebody's tent to reach it —
    # which is how a camp came out with a tent wall missing and a trench of
    # floor running out of it. Demanding clear ground around each one costs a
    # few rejected placements and removes the failure entirely.
    if on and not _fits(g, x0 - margin, y0 - margin,
                        w + margin * 2, h + margin * 2, on):
        return _empty()

    built = _empty()
    inner = interior_floor or floor
    for x in range(x0, x0 + w):
        for y in range(y0, y0 + h):
            edge = x in (x0, x0 + w - 1) or y in (y0, y0 + h - 1)
            g.set(x, y, wall if edge else inner)
            built.skins[f"{x},{y}"] = skin
            if not edge:
                built.interior.append((x, y))

    # The way in goes on a side, never a corner — a corner opening leaves the
    # two walls meeting nowhere and reads as a collapse.
    side = rng.choice(("n", "s", "e", "w"))
    if side in ("n", "s"):
        dx = rng.randrange(x0 + 1, x0 + w - 1)
        dy = y0 if side == "n" else y0 + h - 1
    else:
        dy = rng.randrange(y0 + 1, y0 + h - 1)
        dx = x0 if side == "w" else x0 + w - 1
    g.set(dx, dy, door)
    built.skins[f"{dx},{dy}"] = skin
    built.door_at = (dx, dy)
    return built


def tent(g: Grid, rng: random.Random, x0: int, y0: int, *,
         on: tuple[str, ...] = ("g",)) -> Built:
    """A canvas tent big enough to fight in, or hide in, or search.

    Sized 4x4 or 5x4 — a 10-to-15-foot interior. Its walls block sight and give
    total cover exactly as any wall does, which is correct: you cannot see
    through a tent, and the fact that a determined creature could cut its way
    out is what the object-damage rules are for (canvas is not a hard target).
    """
    w = rng.choice((4, 4, 5))
    h = rng.choice((4, 4, 5))
    return shelter(g, rng, x0, y0, w, h, skin="canvas", on=on,
                   interior_floor=FLOOR)


def watchtower(g: Grid, rng: random.Random, out, x0: int, y0: int, *,
               on: tuple[str, ...] = (), base_ft: int = 15,
               name: str = "watchtower") -> Built:
    """A stone tower with a room at the bottom and a fighting top, joined by a
    ladder.

    The upper storey is a REAL floor — its own terrain grid at its own height,
    reached only by the connector, exactly like any other level. So an archer up
    there is fifteen feet above the fight for every distance, reach, cover and
    spell-area check on the board, and the party has to climb to reach them.
    None of that is new machinery; it is what upper floors already do.

    The top is drawn with a parapet skin so it reads as somewhere to shoot from,
    and the parapet is a low wall — half cover, three feet, the height the rules
    quote — rather than something invented for the look.
    """
    from .terrain import VOID

    size = rng.choice((4, 4, 5))
    built = shelter(g, rng, x0, y0, size, size, skin="masonry", on=on)
    if not built.interior:
        return built

    # The upper floor: void everywhere except this tower's own footprint.
    level = None
    for i, lv in enumerate(out.levels):
        if lv.get("name") == name.title() and lv.get("base_ft") == base_ft:
            level = i + 1
            break
    if level is None:
        rows = [VOID * g.width for _ in range(g.height)]
        out.levels.append({"name": name.title(), "base_ft": int(base_ft),
                           "terrain": rows, "stairs": []})
        level = len(out.levels)

    rows = [list(r) for r in out.levels[level - 1]["terrain"]]
    for x in range(x0, x0 + size):
        for y in range(y0, y0 + size):
            edge = x in (x0, x0 + size - 1) or y in (y0, y0 + size - 1)
            rows[y][x] = "w" if edge else FLOOR
    out.levels[level - 1]["terrain"] = ["".join(r) for r in rows]

    # The ladder: an interior square below, the matching square above.
    lx, ly = built.interior[len(built.interior) // 2]
    out.stairs.append({"level": 0, "x": lx, "y": ly, "to_level": level,
                       "to_x": lx, "to_y": ly, "kind": "ladder"})
    for x in range(x0, x0 + size):
        for y in range(y0, y0 + size):
            edge = x in (x0, x0 + size - 1) or y in (y0, y0 + size - 1)
            built.skins[f"{x},{y}"] = "parapet" if edge else "masonry"
    return built


def cabin(g: Grid, rng: random.Random, x0: int, y0: int, *,
          skin: str = "hull", on: tuple[str, ...] = ("b",)) -> Built:
    """A deckhouse: the captain's quarters, or a hold companionway.

    Same shape as any other shelter; it exists as its own name because a ship's
    generator reads better for it and because the default ground it may stand on
    is deck planking rather than grass.
    """
    w = rng.choice((4, 5))
    h = rng.choice((4, 4, 5))
    return shelter(g, rng, x0, y0, w, h, skin=skin, on=on,
                   interior_floor="b")
