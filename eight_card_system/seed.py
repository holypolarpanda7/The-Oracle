"""
Seed the persistent world.

Two seeders:
  - ``seed_minimal_world`` (the backend default): ONLY a starting location —
    origin region + one town + the tavern the PC wakes up in + four frontier
    stubs. Everything else (NPCs, quests, lore, further places) is created
    through play by the narration→extraction loop, inside the world laws.
  - ``seed_starter_world`` (demo/tests): the original richer hand-authored
    starter town with NPCs and a quest.

Idempotent: entities are upserted by slug, so running either twice won't
duplicate. The starter region is "Greenfields" to match the backend's default
home_region. Millbrook is the world origin: coords (0, 0); all other coords
are miles east/north of it (see geo.py).
"""
from __future__ import annotations

import random
from typing import Optional

from dm_guide.motifs import roll_motifs

from . import census, geo
from .graph import WorldGraph
from .models import EntityType, RelationType, PlaceScale, QuestState, Attitude

# Fixed seed so re-running the seeder is idempotent/deterministic — the same
# motifs get rolled for the same stub every time.
_STUB_RNG_SEED = 8112


def _frontier_stub_specs(stub_rng: random.Random) -> list[dict]:
    """The four pre-constrained frontier stubs ringing Millbrook.

    Coords are committed here so distance/direction are *known*, not narrated:
    render() derives compass + travel time from them.
    """
    return [
        dict(
            name="The North Fields", biome="farmland", danger="low",
            scale_ceiling="village", direction="north", miles=12.0,
            description="Open farmland rolling north of Millbrook, dotted with smallholds.",
            motifs=roll_motifs("farmland", 3, rng=stub_rng),
        ),
        dict(
            name="The West Hills", biome="hills", danger="moderate",
            scale_ceiling="poi", direction="west", miles=20.0,
            description="Steep grazing hills west of Millbrook, sheep trails and old cairns.",
            motifs=roll_motifs("hills", 3, rng=stub_rng),
        ),
        dict(
            name="The South River Road", biome="river", danger="low",
            scale_ceiling="poi", direction="south", miles=12.0,
            description="The Mill River road south, low banks and ferry crossings.",
            motifs=roll_motifs("river", 3, rng=stub_rng),
        ),
        dict(
            name="The Eastwood", biome="forest", danger="moderate",
            scale_ceiling="poi", direction="east", miles=10.0,
            description="Old-growth forest crowding Millbrook's east road.",
            motifs=roll_motifs("forest", 3, rng=stub_rng),
        ),
    ]


def _seed_frontier_stubs(
    graph: WorldGraph, millbrook, greenfields, *, include_east: bool = True
) -> dict:
    """Upsert the frontier stubs + their spatial relations. Returns slug->entity.

    ``include_east=False`` skips the eastern stub for seeds that already place
    something east of town (the full starter world has Duskwood there).
    """
    stub_rng = random.Random(_STUB_RNG_SEED)
    out: dict = {}
    for spec in _frontier_stub_specs(stub_rng):
        if not include_east and spec["direction"] == "east":
            continue
        coords = geo.from_origin(spec["direction"], spec["miles"])
        stub = graph.upsert_entity(
            spec["name"], EntityType.PLACE,
            subtype=PlaceScale.WILDS, status="unexplored",
            attributes={
                "stub": True,
                "biome": spec["biome"],
                "danger": spec["danger"],
                "scale_ceiling": spec["scale_ceiling"],
                "motifs": spec["motifs"],
                "denizens": census.stub_denizens(spec["biome"], stub_rng),
                "description": spec["description"],
                "coords": geo.coords_attr(*coords),
            },
            tags=["frontier", "stub", spec["biome"]],
        )
        graph.add_relation(
            stub, RelationType.ADJACENT_TO, millbrook,
            attributes={
                "direction": spec["direction"],
                "travel_time": geo.travel_time_str(spec["miles"]),
            },
        )
        graph.add_relation(stub, RelationType.PART_OF, greenfields)
        out[stub.slug] = stub
    return out


def seed_minimal_world(graph: WorldGraph) -> dict:
    """Seed ONLY a starting location: region, one town, the tavern, frontier stubs.

    No NPCs, quests, lore, or extra POIs — the living world grows from play.
    Returns key entities by slug.
    """
    graph.create_tables()

    greenfields = graph.upsert_entity(
        "Greenfields", EntityType.PLACE, subtype=PlaceScale.REGION,
        attributes={
            "description": "A patchwork of farmland and low hills at the frontier's edge.",
            "scale": "region",
            "settlement_budget": {"town": 1, "village": 2},
            "coords": geo.coords_attr(geo.ORIGIN_LAT, geo.ORIGIN_LON),
        },
        tags=["region", "frontier", "rural"],
    )
    millbrook = graph.upsert_entity(
        "Millbrook", EntityType.PLACE, subtype=PlaceScale.SETTLEMENT,
        attributes={
            "description": "A modest walled town on the Mill River, last stop before the wilds.",
            "scale": "town",
            "population": 900,
            "coords": geo.coords_attr(geo.ORIGIN_LAT, geo.ORIGIN_LON),
        },
        tags=["town", "walled", "river"],
    )
    tankard = graph.upsert_entity(
        "The Silver Tankard", EntityType.PLACE, subtype="tavern",
        attributes={
            "description": "A warm, smoke-stained tavern that doubles as Millbrook's meeting hall.",
            "scale": "poi",
        },
        tags=["tavern", "poi", "social"],
    )

    graph.add_relation(millbrook, RelationType.PART_OF, greenfields)
    graph.add_relation(tankard, RelationType.PART_OF, millbrook)

    stubs = _seed_frontier_stubs(graph, millbrook, greenfields)

    return {
        "greenfields": greenfields, "millbrook": millbrook, "tankard": tankard,
        **stubs,
    }


def seed_starter_world(graph: WorldGraph) -> dict:
    """Create the starter region/town/POIs/NPCs/faction/quest and wire relations.

    Returns a dict of the key entities by slug for convenience.
    """
    graph.create_tables()

    e = graph.upsert_entity  # shorthand

    # --- Places ---
    greenfields = e(
        "Greenfields", EntityType.PLACE, subtype=PlaceScale.REGION,
        attributes={
            "description": "A patchwork of farmland and low hills at the frontier's edge.",
            "scale": "region",
            "settlement_budget": {"town": 1, "village": 2},
            "coords": geo.coords_attr(geo.ORIGIN_LAT, geo.ORIGIN_LON),
        },
        tags=["region", "frontier", "rural"],
    )
    millbrook = e(
        "Millbrook", EntityType.PLACE, subtype=PlaceScale.SETTLEMENT,
        attributes={
            "description": "A modest walled town on the Mill River, last stop before the wilds.",
            "scale": "town",
            "population": 900,
            "government": "town council",
            "coords": geo.coords_attr(geo.ORIGIN_LAT, geo.ORIGIN_LON),
        },
        tags=["town", "walled", "river"],
    )
    tankard = e(
        "The Silver Tankard", EntityType.PLACE, subtype="tavern",
        attributes={
            "description": "A warm, smoke-stained tavern that doubles as Millbrook's meeting hall.",
            "scale": "poi",
        },
        tags=["tavern", "poi", "social"],
    )
    market = e(
        "Millbrook Market", EntityType.PLACE, subtype="market",
        attributes={"description": "A cramped square of stalls and haggling.", "scale": "poi"},
        tags=["market", "poi", "trade"],
    )
    temple = e(
        "Shrine of the Great Mother", EntityType.PLACE, subtype="temple",
        attributes={
            "description": "A humble field-stone shrine to Chauntea tended by Millbrook's farmers.",
            "scale": "poi",
        },
        tags=["temple", "poi", "faith"],
    )
    duskwood = e(
        "Duskwood", EntityType.PLACE, subtype=PlaceScale.WILDS,
        attributes={
            "description": "An old forest east of Millbrook where the light never quite reaches.",
            "scale": "wilds",
            "danger": "moderate",
            "coords": geo.coords_attr(*geo.from_origin("east", 10.0)),
        },
        tags=["forest", "wilds", "dangerous"],
    )

    # --- Deity ---
    chauntea = e(
        "Chauntea", EntityType.DEITY,
        attributes={
            "description": "The Great Mother, goddess of agriculture and the harvest.",
            "domain": "agriculture, life, plenty",
            "alignment": "neutral good",
        },
        tags=["deity", "life", "harvest"],
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
            "attitude": Attitude.FRIENDLY,
            "role": "tavernkeeper",
        },
        tags=["npc", "tavern", "information"],
    )
    aldric = e(
        "Captain Aldric", EntityType.NPC,
        attributes={
            "description": "Weathered captain of Millbrook's small guard.",
            "disposition": "gruff",
            "attitude": Attitude.INDIFFERENT,
            "role": "guard captain",
        },
        tags=["npc", "guard", "authority"],
    )
    ferran = e(
        "Old Ferran", EntityType.NPC,
        attributes={
            "description": "A hermit who trades in Duskwood herbs and unsettling rumors.",
            "disposition": "wary",
            "attitude": Attitude.UNFRIENDLY,
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
            "state": QuestState.OFFERED,
            "hook": "Ask around the Silver Tankard.",
        },
        tags=["quest", "mystery"],
    )

    # --- Lore / rumor ---
    duskwood_lights = e(
        "Rumor: lights in Duskwood", EntityType.LORE,
        attributes={
            "description": "Farmers swear pale lights drift among the Duskwood trees after dark.",
            "reliability": "unconfirmed",
        },
        tags=["rumor", "duskwood", "clue"],
    )

    R = RelationType
    # Spatial hierarchy
    graph.add_relation(millbrook, R.PART_OF, greenfields)
    graph.add_relation(tankard, R.PART_OF, millbrook)
    graph.add_relation(market, R.PART_OF, millbrook)
    graph.add_relation(temple, R.PART_OF, millbrook)
    graph.add_relation(duskwood, R.ADJACENT_TO, millbrook)
    graph.add_relation(millbrook, R.PART_OF, greenfields)

    # Frontier stubs: adjacent to Millbrook (the jumping-off point) and part of
    # the region, so they show up under "Beyond the map" until explored.
    # Duskwood already covers the east in this richer seed.
    stubs = _seed_frontier_stubs(graph, millbrook, greenfields, include_east=False)

    # Where people are right now
    graph.add_relation(marta, R.LOCATED_IN, tankard)
    graph.add_relation(aldric, R.LOCATED_IN, millbrook)
    graph.add_relation(ferran, R.LOCATED_IN, duskwood)
    graph.add_relation(ledger, R.LOCATED_IN, tankard)

    # Social / org
    graph.add_relation(aldric, R.MEMBER_OF, council)
    graph.add_relation(council, R.GOVERNS, millbrook)
    graph.add_relation(marta, R.KNOWS, aldric)
    graph.add_relation(marta, R.KNOWS, ferran)

    # Faith
    graph.add_relation(marta, R.WORSHIPS, chauntea)

    # Knowledge / rumor: Ferran is the one who spreads the Duskwood rumor.
    graph.add_relation(ferran, R.KNOWS_ABOUT, duskwood_lights)
    graph.add_relation(duskwood_lights, R.LOCATED_AT, duskwood)

    # Quest wiring
    graph.add_relation(marta, R.GIVES_QUEST, missing_merchant)
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
        "market": market, "temple": temple, "duskwood": duskwood,
        "chauntea": chauntea, "council": council,
        "marta": marta, "aldric": aldric, "ferran": ferran,
        "ledger": ledger, "missing_merchant": missing_merchant,
        "duskwood_lights": duskwood_lights,
        **stubs,
    }


def backfill_coords(graph: WorldGraph) -> int:
    """Give a pre-globe world coords for the known seed places (idempotent).

    Worlds seeded before spherical coordinates existed have no positions;
    without them climate and bearings can't render. Only fills gaps — never
    touches an entity that already has coords. Returns how many were filled.
    """
    known: dict[str, tuple[float, float]] = {
        "greenfields": (geo.ORIGIN_LAT, geo.ORIGIN_LON),
        "millbrook": (geo.ORIGIN_LAT, geo.ORIGIN_LON),
        "duskwood": geo.from_origin("east", 10.0),
        "the-north-fields": geo.from_origin("north", 12.0),
        "the-west-hills": geo.from_origin("west", 20.0),
        "the-south-river-road": geo.from_origin("south", 12.0),
        "the-eastwood": geo.from_origin("east", 10.0),
    }
    filled = 0
    for slug, coords in known.items():
        e = graph.get_entity(slug)
        if e is not None and geo.coords_from_attrs(e.attributes) is None:
            graph.upsert_entity(
                e.name, e.type, slug=slug, status=e.status,
                attributes={"coords": geo.coords_attr(*coords)},
            )
            filled += 1
    return filled


def place_pc(
    graph: WorldGraph,
    name: str,
    *,
    discord_user_id: Optional[str] = None,
    location_slug: str = "the-silver-tankard",
    attributes: Optional[dict] = None,
) -> object:
    """Create/refresh a PC entity and place them at a starting location.

    Identity is owner-scoped: the SAME player re-entering with the same
    character reuses their entity; two DIFFERENT players named "Kara" get two
    distinct entities (unique slugs). Callers should use the returned
    entity's ``.slug`` — never re-derive it from the name.
    """
    pc = None
    if discord_user_id:
        pc = graph.find_pc(discord_user_id, name)
    if pc is None:
        # Legacy fallback: a pre-identity entity with this exact slug and no
        # (or matching) owner is the same character from an older DB.
        from .graph import slugify as _slugify
        e = graph.get_entity(_slugify(name))
        if e is not None and getattr(e, "type", None) == EntityType.PC and \
                getattr(e, "discord_user_id", None) in (None, discord_user_id):
            pc = e
    if pc is not None:
        pc = graph.upsert_entity(
            pc.name, EntityType.PC, slug=pc.slug,
            attributes=attributes or {},
            discord_user_id=discord_user_id,
            tags=["pc"],
        )
    else:
        pc = graph.create_entity(
            name, EntityType.PC,
            attributes=attributes or {},
            discord_user_id=discord_user_id,
            tags=["pc"],
        )
    graph.move_entity(pc, location_slug)
    return pc
