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

    ALL = {PLACE, NPC, FACTION, ITEM, QUEST, EVENT, PC}


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

    ALL = {
        LOCATED_IN, ADJACENT_TO, PART_OF, MEMBER_OF, ALLIED_WITH,
        HOSTILE_TO, KNOWS, OWNS, INVOLVES, LOCATED_AT,
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

    # active / dead / destroyed / hidden / departed ...
    status: str = Field(default="active", sa_column=Column(String, index=True))

    # Freeform: description, disposition, danger, hp, etc.
    attributes: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    # List of short keywords for cheap relevance matching.
    tags: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    # Link back to backend Character rows for PC entities.
    discord_user_id: Optional[str] = Field(default=None, sa_column=Column(String, index=True))

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
    """Single-row table holding global world state (current in-world day)."""
    __tablename__ = "world_meta"

    id: Optional[int] = Field(default=1, primary_key=True)
    world_day: int = Field(default=0)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
