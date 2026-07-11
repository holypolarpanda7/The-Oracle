"""
SQLModel schema for the persistent world graph.

Three core tables:
  - Entity     : nodes (place, npc, faction, item, quest, event marker, pc)
  - Relation   : edges with temporal validity (valid_from / valid_to in world-days)
  - WorldEvent : append-only log of things that happened

Plus a single-row WorldMeta table that tracks the current in-world day.

Temporal model ("permutates over time"):
  Nothing is deleted. A relation is "current" while `valid_to IS NULL`. When the
  world changes, the old relation is closed (its `valid_to` is set to the current
  world-day) and, if applicable, a new relation is opened. Entities carry a
  `status` (active / dead / destroyed / hidden / ...) instead of being removed.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Column, JSON, String, Integer
from sqlmodel import Field, SQLModel


# ----- Controlled vocabularies (kept as plain strings in the DB) -----

class EntityType:
    PLACE = "place"      # region, town, POI, dungeon, road, etc.
    NPC = "npc"
    FACTION = "faction"
    ITEM = "item"
    QUEST = "quest"
    EVENT = "event"      # a notable happening promoted to a first-class node
    PC = "pc"            # player character (mirror of backend Character)
    DEITY = "deity"      # gods/powers worshipped in the world (Faerûn pantheon)
    LORE = "lore"        # a rumor, secret, clue, or piece of knowledge

    ALL = {PLACE, NPC, FACTION, ITEM, QUEST, EVENT, PC, DEITY, LORE}


class PlaceScale:
    """Suggested ``subtype`` values for PLACE entities (largest -> smallest)."""
    PLANE = "plane"
    CONTINENT = "continent"
    REGION = "region"
    SETTLEMENT = "settlement"   # city / town / village
    DISTRICT = "district"
    BUILDING = "building"
    ROOM = "room"
    WILDS = "wilds"
    DUNGEON = "dungeon"
    POI = "poi"                 # generic point of interest

    ALL = {PLANE, CONTINENT, REGION, SETTLEMENT, DISTRICT, BUILDING, ROOM,
           WILDS, DUNGEON, POI}


class ItemRarity:
    """Suggested rarity stored in an ITEM entity's ``attributes['rarity']``."""
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    VERY_RARE = "very_rare"
    LEGENDARY = "legendary"
    ARTIFACT = "artifact"

    ALL = {COMMON, UNCOMMON, RARE, VERY_RARE, LEGENDARY, ARTIFACT}


class QuestState:
    """Lifecycle of a QUEST entity, stored in ``attributes['state']``."""
    OFFERED = "offered"     # known/available but not taken
    ACTIVE = "active"       # accepted and in progress
    COMPLETED = "completed"
    FAILED = "failed"

    ALL = {OFFERED, ACTIVE, COMPLETED, FAILED}


class Attitude:
    """NPC social attitude toward the party (5e social-interaction scale).

    Stored on a KNOWS/relation's ``attributes['attitude']`` or an NPC's
    ``attributes['attitude']``.
    """
    HOSTILE = "hostile"
    UNFRIENDLY = "unfriendly"
    INDIFFERENT = "indifferent"
    FRIENDLY = "friendly"
    HELPFUL = "helpful"

    ALL = {HOSTILE, UNFRIENDLY, INDIFFERENT, FRIENDLY, HELPFUL}


# Trust is a running numeric relationship score between an NPC and a specific PC,
# stored on the npc->pc KNOWS relation's ``attributes['trust']`` (and mirrored to
# an ``attitude`` band). It lets the DM track a relationship growing/souring over
# many interactions, beyond the coarse 5e attitude snapshot.
TRUST_MIN = -100
TRUST_MAX = 100


def attitude_for_trust(trust: int) -> str:
    """Map a running trust score to the 5e attitude band the NPC currently holds."""
    t = max(TRUST_MIN, min(TRUST_MAX, int(trust)))
    if t <= -60:
        return Attitude.HOSTILE
    if t <= -20:
        return Attitude.UNFRIENDLY
    if t < 20:
        return Attitude.INDIFFERENT
    if t < 60:
        return Attitude.FRIENDLY
    return Attitude.HELPFUL


class CompanionControl:
    """Who issues a party companion's actions once an NPC joins the party.

    The PLAYER decides this per companion: run the NPC themselves, or hand the
    reins to the DM. Stored on the npc->pc ``travels_with`` relation's
    ``attributes['control']``.
    """
    PLAYER = "player"   # the player controls the companion's actions
    DM = "dm"           # the DM runs the companion as an ally NPC

    ALL = {PLAYER, DM}


# Canonical attribute keys for an NPC entity's ``attributes`` JSON. Keeping these
# consistent lets the DM tools + prompt read/write a predictable NPC "dossier".
class NpcAttr:
    DESCRIPTION = "description"   # one-line summary
    RACE = "race"
    ROLE = "role"                # occupation / function (tavernkeeper, guard, ...)
    DISPOSITION = "disposition"  # personality in a word or two
    ATTITUDE = "attitude"        # default 5e attitude (per-PC trust overrides this)
    VOICE = "voice"              # how they speak (accent, verbal tics)
    GOALS = "goals"              # what they want
    SECRETS = "secrets"          # DM-only info the player must earn
    STATBLOCK = "statblock"      # compact combat block for companions/foes
    LEVEL = "level"



class TimeOfDay:
    """Coarse time-of-day segments used by the world clock."""
    DAWN = "dawn"
    MORNING = "morning"
    MIDDAY = "midday"
    AFTERNOON = "afternoon"
    DUSK = "dusk"
    NIGHT = "night"

    # Ordered for advancing the clock; wrapping past NIGHT rolls to a new day.
    ORDER = [DAWN, MORNING, MIDDAY, AFTERNOON, DUSK, NIGHT]
    ALL = set(ORDER)


# Calendar of Harptos month names (Faerûn), 12 months of 30 days each. These are
# factual world labels, not book prose. Override for a different setting.
HARPTOS_MONTHS = [
    "Hammer", "Alturiak", "Ches", "Tarsakh", "Mirtul", "Kythorn",
    "Flamerule", "Eleasis", "Eleint", "Marpenoth", "Uktar", "Nightal",
]
DAYS_PER_MONTH = 30


class RelationType:
    # spatial
    LOCATED_IN = "located_in"        # entity -> place (where it currently is)
    ADJACENT_TO = "adjacent_to"      # place <-> place (travel/adjacency)
    PART_OF = "part_of"              # place -> larger place (town -> region)
    # social / organizational
    MEMBER_OF = "member_of"          # npc/pc -> faction
    ALLIED_WITH = "allied_with"      # faction <-> faction / npc <-> npc
    HOSTILE_TO = "hostile_to"        # entity -> entity
    KNOWS = "knows"                  # npc/pc -> npc/pc
    # possession / involvement
    OWNS = "owns"                    # entity -> item
    INVOLVES = "involves"            # quest/event -> any entity
    LOCATED_AT = "located_at"        # quest/event anchored to a place
    # religion / governance / commerce / knowledge
    WORSHIPS = "worships"            # npc/pc/faction -> deity
    GOVERNS = "governs"              # faction/npc -> place
    SELLS = "sells"                  # npc/faction -> item
    GIVES_QUEST = "gives_quest"      # npc/faction -> quest
    KNOWS_ABOUT = "knows_about"      # npc/pc -> lore/any (information held)
    # party / companionship
    TRAVELS_WITH = "travels_with"    # npc -> pc (companion currently in the party)

    ALL = {
        LOCATED_IN, ADJACENT_TO, PART_OF, MEMBER_OF, ALLIED_WITH,
        HOSTILE_TO, KNOWS, OWNS, INVOLVES, LOCATED_AT,
        WORSHIPS, GOVERNS, SELLS, GIVES_QUEST, KNOWS_ABOUT, TRAVELS_WITH,
    }

    # Relation types that are symmetric (traversed both ways for adjacency logic)
    SYMMETRIC = {ADJACENT_TO, ALLIED_WITH, KNOWS}


class Entity(SQLModel, table=True):
    __tablename__ = "world_entity"

    id: Optional[int] = Field(default=None, primary_key=True)

    type: str = Field(sa_column=Column(String, nullable=False, index=True))
    name: str = Field(sa_column=Column(String, nullable=False, index=True))
    # Stable, url-safe handle for lookups/references (e.g. "millbrook").
    slug: str = Field(sa_column=Column(String, nullable=False, index=True))
    # Finer classification within a type (e.g. place->"tavern", item->"weapon").
    subtype: Optional[str] = Field(default=None, sa_column=Column(String, index=True))

    # active / dead / destroyed / hidden / departed ...
    status: str = Field(default="active", sa_column=Column(String, index=True))

    # Freeform: description, disposition, danger, hp, etc.
    attributes: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    # List of short keywords for cheap relevance matching.
    tags: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    # Link back to backend Character rows for PC entities.
    discord_user_id: Optional[str] = Field(default=None, sa_column=Column(String, index=True))

    # Link to a backend Character sheet (the full mechanical record). Both PC and
    # NPC entities may carry this: NPCs are full characters too (class/subclass/
    # level/features), so their combat/progression lives on that Character row.
    character_id: Optional[int] = Field(default=None, sa_column=Column(Integer, index=True))

    created_day: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Relation(SQLModel, table=True):
    __tablename__ = "world_relation"

    id: Optional[int] = Field(default=None, primary_key=True)

    src_id: int = Field(sa_column=Column(Integer, nullable=False, index=True))
    rel_type: str = Field(sa_column=Column(String, nullable=False, index=True))
    dst_id: int = Field(sa_column=Column(Integer, nullable=False, index=True))

    attributes: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    # Temporal validity in in-world days. valid_to IS NULL  => currently true.
    valid_from: int = Field(default=0, index=True)
    valid_to: Optional[int] = Field(default=None, index=True)

    created_at: datetime = Field(default_factory=datetime.utcnow)


class WorldEvent(SQLModel, table=True):
    __tablename__ = "world_event"

    id: Optional[int] = Field(default=None, primary_key=True)

    world_day: int = Field(default=0, index=True)
    summary: str = Field(sa_column=Column(String, nullable=False))

    location_id: Optional[int] = Field(default=None, sa_column=Column(Integer, index=True))
    # Entity ids referenced by this event (for relevance lookups).
    involved: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    # Structured record of the delta that produced this event (audit trail).
    changes: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    session_id: Optional[str] = Field(default=None, sa_column=Column(String, index=True))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WorldMeta(SQLModel, table=True):
    """Single-row table holding global world state (the in-world clock).

    ``world_day`` is an absolute day counter (day 0 = campaign start). The
    calendar fields (``year``/``month``/``day_of_month``/``time_of_day``) are a
    human-facing Calendar-of-Harptos view kept in sync by ``WorldGraph``.
    """
    __tablename__ = "world_meta"

    id: Optional[int] = Field(default=1, primary_key=True)
    world_day: int = Field(default=0)

    # Calendar of Harptos view of the current date/time.
    year: int = Field(default=1492)              # Dalereckoning (DR)
    month: int = Field(default=1)                # 1..12 index into HARPTOS_MONTHS
    day_of_month: int = Field(default=1)         # 1..30
    time_of_day: str = Field(default=TimeOfDay.MORNING, sa_column=Column(String))

    updated_at: datetime = Field(default_factory=datetime.utcnow)


def describe_date(meta: "WorldMeta") -> str:
    """Render a WorldMeta as e.g. 'morning of 5 Mirtul, 1492 DR (day 12)'."""
    idx = max(1, min(12, meta.month)) - 1
    month_name = HARPTOS_MONTHS[idx]
    return (f"{meta.time_of_day} of {meta.day_of_month} {month_name}, "
            f"{meta.year} DR (day {meta.world_day})")
