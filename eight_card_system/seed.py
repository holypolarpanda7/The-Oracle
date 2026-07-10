"""
Seed a small, coherent starter world so there is something persistent to inhabit.

Idempotent: entities are upserted by slug, so running it twice won't duplicate.
The starter region is "Greenfields" to match the backend's default home_region.
"""
from __future__ import annotations

from typing import Optional

from .graph import WorldGraph
from .models import EntityType, RelationType


def seed_starter_world(graph: WorldGraph) -> dict:
    """Create the starter region/town/POIs/NPCs/faction/quest and wire relations.

    Returns a dict of the key entities by slug for convenience.
    """
    graph.create_tables()

    e = graph.upsert_entity  # shorthand

    # --- Places ---
    greenfields = e(
        "Greenfields", EntityType.PLACE,
        attributes={
            "description": "A patchwork of farmland and low hills at the frontier's edge.",
            "scale": "region",
        },
        tags=["region", "frontier", "rural"],
    )
    millbrook = e(
        "Millbrook", EntityType.PLACE,
        attributes={
            "description": "A modest walled town on the Mill River, last stop before the wilds.",
            "scale": "town",
        },
        tags=["town", "walled", "river"],
    )
    tankard = e(
        "The Silver Tankard", EntityType.PLACE,
        attributes={
            "description": "A warm, smoke-stained tavern that doubles as Millbrook's meeting hall.",
            "scale": "poi",
        },
        tags=["tavern", "poi", "social"],
    )
    market = e(
        "Millbrook Market", EntityType.PLACE,
        attributes={"description": "A cramped square of stalls and haggling.", "scale": "poi"},
        tags=["market", "poi", "trade"],
    )
    duskwood = e(
        "Duskwood", EntityType.PLACE,
        attributes={
            "description": "An old forest east of Millbrook where the light never quite reaches.",
            "scale": "wilds",
            "danger": "moderate",
        },
        tags=["forest", "wilds", "dangerous"],
    )

    # --- Factions ---
    council = e(
        "Millbrook Council", EntityType.FACTION,
        attributes={"description": "The handful of elders and guildfolk who run the town."},
        tags=["faction", "governance"],
    )

    # --- NPCs ---
    marta = e(
        "Marta Fenn", EntityType.NPC,
        attributes={
            "description": "The Silver Tankard's keeper — sharp-eyed, hears everything.",
            "disposition": "friendly",
            "role": "tavernkeeper",
        },
        tags=["npc", "tavern", "information"],
    )
    aldric = e(
        "Captain Aldric", EntityType.NPC,
        attributes={
            "description": "Weathered captain of Millbrook's small guard.",
            "disposition": "gruff",
            "role": "guard captain",
        },
        tags=["npc", "guard", "authority"],
    )
    ferran = e(
        "Old Ferran", EntityType.NPC,
        attributes={
            "description": "A hermit who trades in Duskwood herbs and unsettling rumors.",
            "disposition": "wary",
            "role": "hermit",
        },
        tags=["npc", "hermit", "duskwood"],
    )

    # --- Item ---
    ledger = e(
        "The Merchant's Ledger", EntityType.ITEM,
        attributes={"description": "A water-stained ledger left behind by a missing trader."},
        tags=["item", "clue"],
    )

    # --- Quest ---
    missing_merchant = e(
        "The Missing Merchant", EntityType.QUEST,
        attributes={
            "description": "A trader bound for Millbrook never arrived. Marta wants to know why.",
            "state": "open",
            "hook": "Ask around the Silver Tankard.",
        },
        tags=["quest", "mystery"],
    )

    R = RelationType
    # Spatial hierarchy
    graph.add_relation(millbrook, R.PART_OF, greenfields)
    graph.add_relation(tankard, R.PART_OF, millbrook)
    graph.add_relation(market, R.PART_OF, millbrook)
    graph.add_relation(duskwood, R.ADJACENT_TO, millbrook)
    graph.add_relation(millbrook, R.PART_OF, greenfields)

    # Where people are right now
    graph.add_relation(marta, R.LOCATED_IN, tankard)
    graph.add_relation(aldric, R.LOCATED_IN, millbrook)
    graph.add_relation(ferran, R.LOCATED_IN, duskwood)
    graph.add_relation(ledger, R.LOCATED_IN, tankard)

    # Social / org
    graph.add_relation(aldric, R.MEMBER_OF, council)
    graph.add_relation(marta, R.KNOWS, aldric)
    graph.add_relation(marta, R.KNOWS, ferran)

    # Quest wiring
    graph.add_relation(missing_merchant, R.LOCATED_AT, millbrook)
    graph.add_relation(missing_merchant, R.INVOLVES, marta)
    graph.add_relation(missing_merchant, R.INVOLVES, ferran)
    graph.add_relation(missing_merchant, R.INVOLVES, ledger)

    # A founding event so there's history.
    graph.add_event(
        "A trader's overdue caravan becomes the talk of the Silver Tankard.",
        location=millbrook,
        involved=[marta, missing_merchant],
    )

    return {
        "greenfields": greenfields, "millbrook": millbrook, "tankard": tankard,
        "market": market, "duskwood": duskwood, "council": council,
        "marta": marta, "aldric": aldric, "ferran": ferran,
        "ledger": ledger, "missing_merchant": missing_merchant,
    }


def place_pc(
    graph: WorldGraph,
    name: str,
    *,
    discord_user_id: Optional[str] = None,
    location_slug: str = "the-silver-tankard",
    attributes: Optional[dict] = None,
) -> object:
    """Create/refresh a PC entity and place them at a starting location."""
    pc = graph.upsert_entity(
        name, EntityType.PC,
        attributes=attributes or {},
        discord_user_id=discord_user_id,
        tags=["pc"],
    )
    graph.move_entity(pc, location_slug)
    return pc
