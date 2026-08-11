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
          on: tuple[str, ...], margin: int = 0) -> bool:
    """Room for a structure here, on ground of the right kind?

    The FOOTPRINT must be wholly on the board and wholly on the right ground.
    The margin around it is checked only where it is on the board at all —
    the edge of the board is not something a tent can collide with, and
    requiring clear ground out there rejected every placement against a wall,
    a bank or a shoreline. That bug cost the bridge both its watchtowers: at
    24x18 the only ground wide enough sits against the board edge, so every
    candidate was refused and the boards came back bare.
    """
    if x0 < 0 or y0 < 0 or x0 + w > g.width or y0 + h > g.height:
        return False
    for x in range(x0 - margin, x0 + w + margin):
        for y in range(y0 - margin, y0 + h + margin):
            inside = x0 <= x < x0 + w and y0 <= y < y0 + h
            if not g.in_bounds(x, y):
                if inside:
                    return False
                continue           # off the board: nothing to collide with
            if g.get(x, y) not in on:
                return False
    return True


def shelter(g: Grid, rng: random.Random, x0: int, y0: int, w: int, h: int, *,
            skin: str, wall: str = WALL, floor: str = FLOOR,
            door: str = "/", on: tuple[str, ...] = (),
            interior_floor: Optional[str] = None,
            door_skin: str = "", interior_skin: str = "",
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
    if on and not _fits(g, x0, y0, w, h, on, margin=margin):
        return _empty()

    built = _empty()
    inner = interior_floor or floor
    for x in range(x0, x0 + w):
        for y in range(y0, y0 + h):
            edge = x in (x0, x0 + w - 1) or y in (y0, y0 + h - 1)
            g.set(x, y, wall if edge else inner)
            if edge:
                # Only the WALLS wear the wall's material. Skinning the whole
                # footprint gave the interior floor the wall's own silhouette
                # too, so a tower was solid right through and the room inside
                # it existed only in the rules. The floor keeps whatever the
                # board is made of, which is what a beaten earth floor or a
                # deck should look like anyway.
                built.skins[f"{x},{y}"] = skin
            else:
                built.interior.append((x, y))
                # A ROOF, where the structure has one. Seen from above, a wall
                # ring with a walkable floor in it is a roofless box, which is
                # what every camp came back as: three timber pens round a fire.
                # The covering has to be its own skin because the inside is its
                # own squares, and it is safe to be one because it starts well
                # clear of the floor — see skins.occludes_floor, which is the
                # guard that stops a picture closing a square the rules leave
                # open.
                if interior_skin:
                    built.skins[f"{x},{y}"] = interior_skin

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
    built.door_at = (dx, dy)
    # The doorway is NOT the wall, and giving it the wall's skin is the bug
    # this parameter exists to prevent: the square stays walkable in the rules
    # and gets DRAWN as a solid block, so the way in is not there. An empty
    # door_skin leaves it looking like the floor, which is honest; a real one
    # (jambs and a lintel, a flap tied back) is better, and both leave the
    # passage clear — see skins.occludes_floor.
    if door_skin:
        built.skins[f"{dx},{dy}"] = door_skin
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
    return shelter(g, rng, x0, y0, w, h, skin="canvas", door_skin="flap",
                   interior_skin="tent-canopy", on=on, interior_floor=FLOOR)


def _upper_level(out, g: Grid, base_ft: int, name: str) -> int:
    """The 1-based index of this structure's own storey, made if it is new.

    A new level starts as ALL VOID, because a gallery — or a tower top — is the
    strip you build and everywhere else is open to whatever is below.
    """
    from .terrain import VOID

    for i, lv in enumerate(out.levels):
        if lv.get("name") == name.title() and lv.get("base_ft") == base_ft:
            return i + 1
    out.levels.append({"name": name.title(), "base_ft": int(base_ft),
                       "terrain": [VOID * g.width for _ in range(g.height)],
                       "stairs": []})
    return len(out.levels)


def _floor_out(out, level: int, x0: int, y0: int, size: int,
               edge: str, inner: str) -> None:
    """Lay a square of floor on an upper storey, with its own edging."""
    rows = [list(r) for r in out.levels[level - 1]["terrain"]]
    for x in range(x0, x0 + size):
        for y in range(y0, y0 + size):
            on_edge = x in (x0, x0 + size - 1) or y in (y0, y0 + size - 1)
            rows[y][x] = edge if on_edge else inner
    out.levels[level - 1]["terrain"] = ["".join(r) for r in rows]


#: How many squares a post tower stands on. ODD on purpose: the platform and
#: the roof are drawn from the MIDDLE square and reach out over the rest, and
#: only an odd footprint has a middle square whose own centre is the whole
#: structure's centre — which is what keeps the roof where it belongs when the
#: square takes its quarter turn.
POST_TOWER_SIZE = 5


def _post_tower(g: Grid, rng: random.Random, out, x0: int, y0: int, *,
                on: tuple[str, ...], base_ft: int, name: str) -> Built:
    """Four raked legs holding a platform up, open underneath.

    A timber tower is not a small building, and drawing it as one — a walled
    shelter in log cladding — got a squat wooden box with a door in it. What
    says "watchtower" is the LEGS and the daylight between them: you walk under
    it, and the only way up is the ladder.

    Rules-wise this is cheaper than the walled version rather than dearer. The
    legs are ``O`` pillars, which is what a post IS to the engine — impassable,
    three-quarters cover, narrow — and the ground between them stays the ground
    it was. Nothing new was needed for any of it.
    """
    size = POST_TOWER_SIZE
    if on and not _fits(g, x0, y0, size, size, on, margin=1):
        return _empty()

    built = _empty()
    corners = {(x0, y0), (x0 + size - 1, y0),
               (x0, y0 + size - 1), (x0 + size - 1, y0 + size - 1)}
    for px, py in corners:
        g.set(px, py, "O")
        built.skins[f"{px},{py}"] = "tower-post"
    built.interior = [(x, y) for x in range(x0, x0 + size)
                      for y in range(y0, y0 + size) if (x, y) not in corners]

    level = _upper_level(out, g, base_ft, name)
    # The platform's own edge is a real low wall: half cover, three feet, the
    # height the rules quote for anyone shooting from up there.
    _floor_out(out, level, x0, y0, size, "w", FLOOR)

    mid = size // 2
    # The platform and the roof are DRAWN from the middle square and reach out
    # over the whole footprint, because only one storey is ever drawn at a time
    # — so from the ground the platform is something you look at rather than
    # something you are on. Without it the tower was four poles and a roof with
    # a gap where the floor should be.
    built.skins[f"{x0 + mid},{y0 + mid}"] = "tower-top"

    lx, ly = x0 + mid, y0 + mid + 1
    built.skins[f"{lx},{ly}"] = "tower-ladder"
    out.stairs.append({"level": 0, "x": lx, "y": ly, "to_level": level,
                       "to_x": lx, "to_y": ly, "kind": "ladder"})
    return built


def watchtower(g: Grid, rng: random.Random, out, x0: int, y0: int, *,
               on: tuple[str, ...] = (), base_ft: int = 15,
               material: str = "stone",
               name: str = "watchtower") -> Built:
    """A tower with a fighting top, of whatever the country builds in.

    Two quite different structures, because they really are two different
    things and pretending otherwise is what made the timber one wrong. In
    STONE it is a building: a room at the bottom, a doorway, a merloned top.
    In TIMBER it is a frame — see :func:`_post_tower` — four legs and a
    platform with nothing between them but air.

    The upper storey is a REAL floor — its own terrain grid at its own height,
    reached only by the connector, exactly like any other level. So an archer up
    there is fifteen feet above the fight for every distance, reach, cover and
    spell-area check on the board, and the party has to climb to reach them.
    None of that is new machinery; it is what upper floors already do.

    The top is drawn with a parapet skin so it reads as somewhere to shoot from,
    and the parapet is a low wall — half cover, three feet, the height the rules
    quote — rather than something invented for the look.
    """
    if material == "timber":
        return _post_tower(g, rng, out, x0, y0, on=on, base_ft=base_ft,
                           name=name)

    size = rng.choice((4, 4, 5))
    built = shelter(g, rng, x0, y0, size, size, skin="tower-stone",
                    door_skin="doorway-stone", on=on)
    if not built.interior:
        return built

    level = _upper_level(out, g, base_ft, name)
    _floor_out(out, level, x0, y0, size, "w", FLOOR)

    # The ladder: an interior square below, the matching square above.
    lx, ly = built.interior[len(built.interior) // 2]
    out.stairs.append({"level": 0, "x": lx, "y": ly, "to_level": level,
                       "to_x": lx, "to_y": ly, "kind": "ladder"})
    # NB: the ground storey keeps the skins `shelter` gave it. An earlier pass
    # re-skinned this whole footprint as `parapet` — a nine-foot merloned edge,
    # which is the thing you crouch behind ON the roof, not the storey holding
    # it up. Applied down here it made every tower a low crenellated box with
    # its doorway bricked over. The parapet belongs to the level above, where
    # it is an ordinary low wall and needs no skin at all.
    return built


def cabin(g: Grid, rng: random.Random, x0: int, y0: int, *,
          skin: str = "hull", roof: str = "cabin-roof",
          on: tuple[str, ...] = ("b",)) -> Built:
    """A deckhouse: the captain's quarters, or a hold companionway.

    Same shape as any other shelter; it exists as its own name because a ship's
    generator reads better for it and because the default ground it may stand on
    is deck planking rather than grass.
    """
    w = rng.choice((4, 5))
    h = rng.choice((4, 4, 5))
    return shelter(g, rng, x0, y0, w, h, skin=skin, on=on,
                   interior_floor="b", interior_skin=roof)
