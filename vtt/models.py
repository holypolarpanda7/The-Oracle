"""
Tactical-scene tables — the square-grid battlemap the Oracle drops onto the
table when a moment turns *tactical*.

Most of play is theater-of-the-mind: the DM narrates, the world graph remembers.
A **tactical scene** is the exception — combat, a timed puzzle, a chase's terrain
gauntlet, a trapped room — where exact position, distance, and line of sight
decide whether a choice was good. When one opens, this package becomes the
spatial source of truth for the duration, then closes and hands the world back
to prose.

Four tables, all in the backend's ``oracle.db``:

``TacticalMap``
    The board: a ``width x height`` grid of 5-ft squares, a terrain string per
    row (see :mod:`vtt.terrain`), an optional diffusion-rendered background
    keyed to an ``entity_image`` row, plus fog-of-war and lighting.
``MapToken``
    A creature or object standing on it. PC/monster tokens carry
    ``combatant_id`` so the board and the initiative tracker are the same fight
    seen twice — the tracker owns HP/conditions, the token owns position.
``MapEffect``
    Anything overlaid on the grid that isn't a creature: a spell's area, a
    lingering aura, a patch of grease, a wall of fire, a light source, a marker.
    Every effect stores its resolved ``squares`` so the UI, the rules, and the
    DM prompt all read the exact same footprint.
``MapEvent``
    Append-only log of what happened on the board (spawn/move/effect/reveal),
    so a scene can be replayed or audited exactly like ``combat_log``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Boolean, Column, Integer, JSON, String
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    """Naive UTC now (datetime.utcnow() is deprecated since 3.12)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SceneKind:
    """Why the board is out. Drives the map archetype and the close policy."""
    COMBAT = "combat"        # a fight — closes when the encounter ends
    PUZZLE = "puzzle"        # a spatial/timed puzzle room
    CHASE = "chase"          # a pursuit across hazardous ground
    HAZARD = "hazard"        # a trapped room / collapsing bridge
    EXPLORE = "explore"      # dungeon crawl with fog of war
    SOCIAL = "social"        # a standoff where "who stands where" matters
    ALL = (COMBAT, PUZZLE, CHASE, HAZARD, EXPLORE, SOCIAL)


class TokenKind:
    PC = "pc"
    NPC = "npc"
    MONSTER = "monster"
    OBJECT = "object"        # barrel, door, lever — a thing that occupies squares
    MARKER = "marker"        # a labelled point of interest (no creature)
    ALL = (PC, NPC, MONSTER, OBJECT, MARKER)


class Team:
    PARTY = "party"
    FOE = "foe"
    NEUTRAL = "neutral"
    ALL = (PARTY, FOE, NEUTRAL)


# Creature-size footprints, in squares on a side (SRD "Space").
SIZE_SQUARES: dict[str, int] = {
    "tiny": 1, "small": 1, "medium": 1,
    "large": 2, "huge": 3, "gargantuan": 4,
}


def size_squares(size: Optional[str]) -> int:
    return SIZE_SQUARES.get((size or "medium").strip().lower(), 1)


class EffectKind:
    """What an overlay *is* — the UI paints each family differently."""
    AREA = "area"            # an instantaneous spell area (fireball's burst)
    ZONE = "zone"            # a lingering area (grease, web, spike growth)
    AURA = "aura"            # follows its source token (paladin aura, torch)
    WALL = "wall"            # a barrier along a path of squares
    LIGHT = "light"          # bright/dim light emission
    HAZARD = "hazard"        # environmental damage (lava, caltrops)
    MARKER = "marker"        # a DM annotation / objective ping
    ALL = (AREA, ZONE, AURA, WALL, LIGHT, HAZARD, MARKER)


class Shape:
    """Template geometry (see :func:`vtt.geometry.area_squares`)."""
    SPHERE = "sphere"        # radius from a point (5e "sphere"/"radius"/"burst")
    CIRCLE = "circle"        # alias for sphere on a flat board
    CONE = "cone"
    LINE = "line"
    CUBE = "cube"
    SQUARE = "square"
    EMANATION = "emanation"  # radius measured from the source creature
    PATH = "path"            # an explicit list of squares (walls, spills)
    ALL = (SPHERE, CIRCLE, CONE, LINE, CUBE, SQUARE, EMANATION, PATH)


class TacticalMap(SQLModel, table=True):
    __tablename__ = "vtt_map"

    id: Optional[int] = Field(default=None, primary_key=True)

    # The table this board belongs to ("guild:channel").
    session_id: str = Field(sa_column=Column(String, nullable=False, index=True))
    # The fight it mirrors, when the scene is a combat.
    encounter_id: Optional[int] = Field(default=None, sa_column=Column(Integer, index=True))
    # The world-graph place it depicts, so re-entering a room can reuse its map.
    place_slug: Optional[str] = Field(default=None, sa_column=Column(String, index=True))

    name: str = Field(default="Tactical Scene", sa_column=Column(String))
    kind: str = Field(default=SceneKind.COMBAT, sa_column=Column(String, index=True))
    # Layout family from vtt.mapgen (dungeon-room, cave, forest, street, ...).
    archetype: str = Field(default="dungeon-room", sa_column=Column(String))
    biome: Optional[str] = Field(default=None, sa_column=Column(String))

    width: int = Field(default=20, sa_column=Column(Integer))     # squares
    height: int = Field(default=15, sa_column=Column(Integer))    # squares
    square_ft: int = Field(default=5, sa_column=Column(Integer))

    # ``height`` strings of ``width`` tile codes each (see vtt.terrain).
    terrain: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    # Per-square elevation in feet, sparse: {"x,y": ft}. Absent = ground level.
    elevation: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    # Doors and other stateful furniture: [{"x","y","state":"open|closed|locked",
    # "dc": int|None, "name": str}]. Terrain holds the code; this holds the state.
    doors: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    # Damage taken by breakable furniture, keyed "x,y" -> {hp, hp_max, ac,
    # name, material, resists, immune}. Same split as ``doors`` above: the
    # TERRAIN holds what a square is, this holds what has happened to it. A
    # square absent from here is simply undamaged, so a fresh board carries
    # nothing and only the things players actually hit take up room.
    objects: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    # Wreckage drawn over the base art, keyed "x,y" -> {code, image_id,
    # caption}. Small sprites, not a re-render: the base picture stays pinned
    # to the layout as GENERATED, and what the party broke is painted on top.
    # Cleared when the board itself is replaced.
    debris: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    # Object sprites in use on this board, keyed by tile NAME -> image id.
    # By kind rather than by square: eight pillars are one picture.
    object_art: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    # Per-square MATERIAL overrides, sparse: {"x,y": skin name}. See vtt.skins.
    # Most of what a board is made of is derived from its archetype and stored
    # nowhere; this holds only the exceptions a generator deliberately built —
    # canvas tents inside a palisaded camp, a ship's cabin against its hull.
    # Purely a look. No rule reads one, and none may.
    skins: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    # A whole-board style where the archetype genuinely offers a choice: a
    # skyship is timber, brass-and-steam or grown, and all three are right.
    board_style: str = Field(default="", sa_column=Column(String))

    # Upper floors. A gallery over a hall, a tower's storeys, a walkway above
    # a chasm: each is a level with its OWN terrain grid at its own height.
    # Level 0 is ``terrain`` above, so a single-storey board carries nothing
    # here and every existing board keeps working untouched.
    #
    #   [{"name": "Gallery", "terrain": [...], "base_ft": 15,
    #     "stairs": [{"x": 4, "y": 7, "to": 0, "tx": 4, "ty": 8}]}]
    #
    # The height axis was already folded into every distance, reach and spell
    # area on the board, so a level is mostly just a grid plus the number that
    # says how far up it is. What is genuinely new is the FLOOR: a void square
    # on an upper level is a hole you can see and fall through, and anywhere
    # else there is a ceiling between the two.
    levels: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    # Ambient light for the whole board: bright | dim | dark. Individual light
    # sources are MapEffect rows of kind "light".
    lighting: str = Field(default="bright", sa_column=Column(String))

    # Fog of war: ``height`` strings of ``width`` chars, "1" seen / "0" unseen.
    # None means the whole board is revealed (most combats).
    fog: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    # Diffusion-rendered top-down art, stored in ``entity_image``.
    background_image_id: Optional[int] = Field(default=None, sa_column=Column(Integer))
    # The painted ISOMETRIC view of this board, conditioned on a depth map of
    # the very geometry the Activity is drawing (see vtt/isocam.py). Its own
    # column rather than sharing the one above: the two are different pictures
    # of the same room for different clients, and a Discord table still wants
    # the top-down one. Absent = the board shows clean geometry, which is a
    # supported state and not a degraded one.
    iso_image_id: Optional[int] = Field(default=None, sa_column=Column(Integer))
    iso_art_status: str = Field(default="none", sa_column=Column(String))
    # pending | ready | offline | none — lets the UI show tiles while art renders.
    art_status: str = Field(default="none", sa_column=Column(String))
    art_prompt: Optional[str] = Field(default=None, sa_column=Column(String))

    # Layout seed: the same seed + archetype + size always rebuilds this board.
    seed: int = Field(default=0, sa_column=Column(Integer))

    active: bool = Field(default=True, sa_column=Column(Boolean, index=True))
    # Bumped on every mutation so clients can cheaply tell "did anything move?".
    revision: int = Field(default=0, sa_column=Column(Integer))
    # Free-form scene notes for the DM prompt (objectives, timers, exits).
    notes: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class MapToken(SQLModel, table=True):
    __tablename__ = "vtt_token"

    id: Optional[int] = Field(default=None, primary_key=True)
    map_id: int = Field(sa_column=Column(Integer, index=True, nullable=False))

    # Links back to the fight and the character sheet (either may be absent for
    # scenery objects and markers).
    combatant_id: Optional[int] = Field(default=None, sa_column=Column(Integer, index=True))
    character_id: Optional[int] = Field(default=None, sa_column=Column(Integer, index=True))
    monster_slug: Optional[str] = Field(default=None, sa_column=Column(String))

    name: str = Field(sa_column=Column(String, nullable=False))
    kind: str = Field(default=TokenKind.MONSTER, sa_column=Column(String))
    team: str = Field(default=Team.FOE, sa_column=Column(String, index=True))

    # Top-left square of the token's footprint.
    x: int = Field(default=0, sa_column=Column(Integer))
    y: int = Field(default=0, sa_column=Column(Integer))
    size: str = Field(default="medium", sa_column=Column(String))
    elevation_ft: int = Field(default=0, sa_column=Column(Integer))
    facing_deg: int = Field(default=0, sa_column=Column(Integer))

    # Movement, in feet — the tracker owns the action economy, the board owns
    # the distance actually walked this turn.
    speed_ft: int = Field(default=30, sa_column=Column(Integer))
    reach_ft: int = Field(default=5, sa_column=Column(Integer))
    moved_ft: int = Field(default=0, sa_column=Column(Integer))
    # Flying/swimming tokens ignore ground hazards and difficult terrain.
    movement_mode: str = Field(default="walk", sa_column=Column(String))

    # Hidden from the players' board (an unnoticed ambusher, a DM-only marker).
    hidden: bool = Field(default=False, sa_column=Column(Boolean))
    # Hiding is a CONTEST, so the number has to survive the roll. This is the
    # Stealth result a searcher must beat; without it every Search action is a
    # check against nothing and the DM invents a DC each time.
    stealth_dc: Optional[int] = Field(default=None, sa_column=Column(Integer))
    # Who has already found this creature, by token name. Being hidden is not a
    # property of the hider alone — the guard who spotted you sees you while
    # the rest of the room still doesn't, and one bool cannot say that.
    found_by: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    # Which floor this creature is standing on. 0 is the board's own terrain;
    # anything higher indexes TacticalMap.levels.
    level: int = Field(default=0, sa_column=Column(Integer))
    # Forcing itself through a space one size category smaller: half speed in
    # effect (an extra foot per foot), disadvantage on its attacks and Dex
    # saves, advantage on attacks against it. Board state rather than a DM
    # ruling, because the board is what knows the corridor is too narrow.
    squeezing: bool = Field(default=False, sa_column=Column(Boolean))
    # The mount this creature is riding, by token name. On the RIDER, not the
    # mount — the same shape as ``grappled_by``, and for the same reason: the
    # thing being carried is the one whose movement stops being its own.
    # Rider and mount share the mount's space, so there is no second position
    # to keep in step.
    mounted_on: Optional[str] = Field(default=None, sa_column=Column(String))
    # Whether this creature SWIMS, in feet — a trait of the creature, not of
    # the board. movement_mode says how it is moving right now, and on an
    # underwater board that is "swim" for everyone in it, including the
    # dwarf who is drowning; the underwater combat rules turn on which of
    # them actually has a swimming speed. None = never looked up, 0 = looked
    # up and hasn't got one.
    swim_speed_ft: Optional[int] = Field(default=None, sa_column=Column(Integer))
    # How this creature perceives, in feet: {"darkvision": 60, "blindsight": 10}.
    # A JSON column rather than four more columns, because it is a stat block's
    # senses line and that line grows — and because the bestiary already stores
    # it in exactly this shape. Empty/None means ordinary sight, which is the
    # right default for anything nobody bothered to say otherwise about.
    senses: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    # Prone tokens are drawn flat; defeated ones greyed out.
    prone: bool = Field(default=False, sa_column=Column(Boolean))
    defeated: bool = Field(default=False, sa_column=Column(Boolean))

    # The conditions that change what a creature can do ON THE BOARD, and so
    # have to live here rather than only in the combat tracker's condition list.
    # Restrained and grappled both mean Speed 0 — which is a movement rule, and
    # the board is what enforces movement.
    restrained: bool = Field(default=False, sa_column=Column(Boolean))
    # Name of whoever has hold of this creature. Held separately from a plain
    # "grappled" flag because the grappler can DRAG their captive along, so the
    # board needs to know which way the leash runs.
    grappled_by: Optional[str] = Field(default=None, sa_column=Column(String))

    # Portrait/creature art for the token face (an ``entity_image`` id).
    image_id: Optional[int] = Field(default=None, sa_column=Column(Integer))
    color: Optional[str] = Field(default=None, sa_column=Column(String))
    label: Optional[str] = Field(default=None, sa_column=Column(String))
    notes: Optional[str] = Field(default=None, sa_column=Column(String))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class MapEffect(SQLModel, table=True):
    __tablename__ = "vtt_effect"

    id: Optional[int] = Field(default=None, primary_key=True)
    map_id: int = Field(sa_column=Column(Integer, index=True, nullable=False))

    name: str = Field(default="Effect", sa_column=Column(String))
    kind: str = Field(default=EffectKind.AREA, sa_column=Column(String, index=True))
    shape: str = Field(default=Shape.SPHERE, sa_column=Column(String))

    # Template placement. Origin is a square; direction points a cone/line.
    origin_x: int = Field(default=0, sa_column=Column(Integer))
    origin_y: int = Field(default=0, sa_column=Column(Integer))
    radius_ft: int = Field(default=0, sa_column=Column(Integer))
    length_ft: int = Field(default=0, sa_column=Column(Integer))
    width_ft: int = Field(default=5, sa_column=Column(Integer))
    direction_deg: int = Field(default=0, sa_column=Column(Integer))

    # The resolved footprint: [[x, y], ...]. Authoritative — the UI paints it,
    # the rules test membership, the DM prompt summarises it.
    squares: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    # Mechanics the board enforces or reminds the DM about.
    difficult_terrain: bool = Field(default=False, sa_column=Column(Boolean))
    blocks_sight: bool = Field(default=False, sa_column=Column(Boolean))
    blocks_movement: bool = Field(default=False, sa_column=Column(Boolean))
    obscured: Optional[str] = Field(default=None, sa_column=Column(String))  # light|heavy
    # No sound can be created within or pass through. Its own column for the
    # same reason `obscured` has one: `kind` says how the UI paints an area,
    # not what standing in it does, and a silence zone is painted like any
    # other zone. Read by the casting gate — a Verbal component cannot be
    # spoken in here.
    silences: bool = Field(default=False, sa_column=Column(Boolean))
    # Which floor this area is on. A fireball in the hall is not also going off
    # on the gallery, and a board with one storey has everything on level 0.
    level: int = Field(default=0, sa_column=Column(Integer))
    damage: Optional[str] = Field(default=None, sa_column=Column(String))    # "2d6 fire"
    save_ability: Optional[str] = Field(default=None, sa_column=Column(String))
    save_dc: Optional[int] = Field(default=None, sa_column=Column(Integer))
    # on_enter | start_of_turn | end_of_turn | once — when the damage/save fires.
    trigger: Optional[str] = Field(default=None, sa_column=Column(String))

    # Presentation.
    color: Optional[str] = Field(default=None, sa_column=Column(String))
    opacity: float = Field(default=0.35)
    icon: Optional[str] = Field(default=None, sa_column=Column(String))

    # Lifetime. Auras follow their source; timed effects expire on a round.
    source_token_id: Optional[int] = Field(default=None, sa_column=Column(Integer, index=True))
    concentration: bool = Field(default=False, sa_column=Column(Boolean))
    created_round: int = Field(default=1, sa_column=Column(Integer))
    expires_round: Optional[int] = Field(default=None, sa_column=Column(Integer))
    active: bool = Field(default=True, sa_column=Column(Boolean, index=True))

    # all | party | foe | dm — who sees it on their board.
    visible_to: str = Field(default="all", sa_column=Column(String))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class MapEvent(SQLModel, table=True):
    """Append-only board telemetry: enough to replay a scene square by square."""
    __tablename__ = "vtt_event"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_utcnow, index=True)

    map_id: int = Field(sa_column=Column(Integer, index=True, nullable=False))
    session_id: Optional[str] = Field(default=None, sa_column=Column(String, index=True))
    round: Optional[int] = Field(default=None, sa_column=Column(Integer))

    # open | close | spawn | move | remove | effect | effect_end | reveal |
    # terrain | door | ping
    kind: str = Field(default="move", sa_column=Column(String, index=True))
    actor: Optional[str] = Field(default=None, sa_column=Column(String))
    summary: Optional[str] = Field(default=None, sa_column=Column(String))
    payload: Optional[Any] = Field(default=None, sa_column=Column(JSON))
