"""
Eight Card System — Persistent world knowledge graph for The Oracle.

A time-evolving graph of the game world (places, NPCs, factions, items, quests,
events). World changes are append-only: facts are opened and closed over in-world
time rather than overwritten, so full history is preserved. The DM brain reads only
the *relevant* slice of the graph (near the character's location / current action),
never the whole world.

Public surface:
    from eight_card_system import WorldGraph, get_engine
    from eight_card_system.models import Entity, Relation, WorldEvent, EntityType
"""

from .models import (
    Entity,
    Relation,
    WorldEvent,
    WorldMeta,
    EntityType,
    RelationType,
    PlaceScale,
    ItemRarity,
    QuestState,
    Attitude,
    CompanionControl,
    NpcAttr,
    TimeOfDay,
    attitude_for_trust,
    describe_date,
)
from .graph import WorldGraph, get_engine, WorldContext

__all__ = [
    "WorldGraph",
    "get_engine",
    "WorldContext",
    "Entity",
    "Relation",
    "WorldEvent",
    "WorldMeta",
    "EntityType",
    "RelationType",
    "PlaceScale",
    "ItemRarity",
    "QuestState",
    "Attitude",
    "CompanionControl",
    "NpcAttr",
    "TimeOfDay",
    "attitude_for_trust",
    "describe_date",
]
