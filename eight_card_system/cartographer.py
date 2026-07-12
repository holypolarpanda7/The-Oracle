"""
The cartographer — keeps a constrained frontier ahead of the players and
founds new regions as they range far from home.

Two jobs, both deterministic (seeded from slugs, so retries and re-applies
produce the same map):

1. **Frontier propagation** (``ensure_frontier_around``): whenever a PC
   explores a stub or moves somewhere thin on frontier, roll fresh unexplored
   stubs beyond it — biome drawn from the local climate band (with continuity
   toward the neighbor's biome), rolled danger, a settlement ceiling weighted
   by the region's archetype, and seed motifs. The map's edge always exists
   and is always pre-constrained before the DM narrates into it.

2. **Region founding** (inside the same pass): territory farther than
   ``REGION_RADIUS_MI`` from its region's heart gets a NEW region with its own
   rolled settlement budget and character. Heartlands can hold cities; the
   marches between them hold almost nothing — that spread is what makes
   kingdoms feel like kingdoms, decided by dice rather than LLM habit.
"""
from __future__ import annotations

import random
from typing import Optional

from sqlmodel import Session, select

from dm_guide.motifs import roll_motifs

from . import census, geo
from .graph import WorldGraph
from .models import Entity, Relation, RelationType, EntityType, PlaceScale

# Territory farther than this from its region's heart founds a new region.
REGION_RADIUS_MI = 55.0
# Every explored place keeps at least this many unexplored stubs ADJACENT to
# it — the map edge exists wherever the party stands, not just back home.
MIN_FRONTIER_STUBS = 2
# New stubs land this far beyond the place that spawned them.
STUB_MIN_MI, STUB_MAX_MI = 10.0, 22.0
# Never drop a stub on top of something that already exists.
STUB_CLEARANCE_MI = 6.0

# Candidate biomes per climate band (keys match dm_guide.motifs tables).
_BIOMES_BY_CLIMATE = {
    "arctic": ["mountains", "hills"],
    "subarctic": ["mountains", "forest", "hills"],
    "cool temperate": ["forest", "hills", "mountains", "river"],
    "temperate": ["farmland", "forest", "hills", "river", "swamp"],
    "warm temperate": ["farmland", "hills", "river", "coast"],
    "arid": ["desert", "hills"],
    "desert": ["desert"],
    "subtropical": ["swamp", "forest", "river", "coast"],
    "tropical": ["swamp", "forest", "coast", "river"],
}

# Region archetypes: (name, weight, budget-roller). The budget spread is the
# kingdom-shape dial: heartlands anchor realms, wilds are the empty marches.
_REGION_ARCHETYPES = [
    ("heartland", 20, lambda r: {"town": r.randint(2, 3), "village": r.randint(3, 5)}),
    ("provinces", 45, lambda r: {"town": 1, "village": r.randint(2, 3)}),
    ("marches", 25, lambda r: {"town": 0, "village": 1}),
    ("wilds", 10, lambda r: {"town": 0, "village": 0}),
]

# Ceiling weights (poi, village, town, city) by the region archetype the stub
# lands in. Only heartlands may promise a city site.
_CEILING_WEIGHTS = {
    "heartland": (("poi", 35), ("village", 30), ("town", 25), ("city", 10)),
    "provinces": (("poi", 50), ("village", 32), ("town", 18)),
    "marches": (("poi", 65), ("village", 30), ("town", 5)),
    "wilds": (("poi", 85), ("village", 15)),
}

_DANGERS = (("low", 35), ("moderate", 45), ("high", 20))

# Name fragments for generated places. Region names lean on biome+bearing so
# the DM (or players) can rename them in the fiction later — the slug persists.
_BIOME_REGION_NOUNS = {
    "farmland": ["Fields", "Vales", "Meadowlands"],
    "forest": ["Weald", "Deepwood", "Timberlands"],
    "hills": ["Downs", "Highlands", "Barrows"],
    "river": ["Reaches", "Fords", "Watershed"],
    "swamp": ["Fens", "Mires", "Sloughs"],
    "mountains": ["Peaks", "Crags", "Teeth"],
    "desert": ["Wastes", "Dunes", "Scablands"],
    "coast": ["Shores", "Strands", "Saltmarches"],
}
_REGION_ADJECTIVES = ["Silent", "Sundered", "Amber", "Grey", "Windswept",
                      "Old", "Farther", "Broken", "Gilded", "Mourning"]
_STUB_NOUNS = {
    "farmland": ["fallows", "pastures", "hedgelands"],
    "forest": ["woods", "thickets", "groves"],
    "hills": ["hills", "ridges", "tors"],
    "river": ["banks", "shallows", "oxbows"],
    "swamp": ["marshes", "bogs", "reedbeds"],
    "mountains": ["slopes", "passes", "screes"],
    "desert": ["flats", "badlands", "pans"],
    "coast": ["dunes", "coves", "headlands"],
}


def _weighted(rng: random.Random, pairs) -> str:
    total = sum(w for _, w in pairs)
    roll = rng.uniform(0, total)
    acc = 0.0
    for value, w in pairs:
        acc += w
        if roll <= acc:
            return value
    return pairs[-1][0]


def _region_of(graph: WorldGraph, s: Session, entity: Entity) -> Optional[Entity]:
    """Walk PART_OF upward to the entity's region (4 hops)."""
    current = entity
    for _ in range(4):
        if current is None:
            return None
        if current.subtype == PlaceScale.REGION:
            return current
        rel = s.exec(
            select(Relation).where(
                Relation.src_id == current.id,
                Relation.rel_type == RelationType.PART_OF,
                Relation.valid_to == None,  # noqa: E711
            )
        ).first()
        current = s.get(Entity, rel.dst_id) if rel else None
    return None


def _coordful_places(s: Session) -> list[tuple[Entity, tuple[float, float]]]:
    out = []
    for e in s.exec(select(Entity).where(Entity.type == EntityType.PLACE)).all():
        c = geo.coords_from_attrs(e.attributes)
        if c is not None:
            out.append((e, c))
    return out


def _unique_name(graph: WorldGraph, base: str, fallback_suffix: str) -> str:
    def taken(name: str) -> bool:
        return (graph.get_entity(name.lower().replace(" ", "-")) is not None
                or graph.get_entity(name) is not None)

    for candidate in (base, f"{base} of {fallback_suffix}",
                      f"Farther {base[4:] if base.startswith('The ') else base}"):
        if not taken(candidate):
            return candidate
    # Last resort: number it — ugly but never merges two distinct places.
    n = 2
    while taken(f"{base} {n}"):
        n += 1
    return f"{base} {n}"


def _found_region(
    graph: WorldGraph, rng: random.Random, coords: tuple[float, float],
    biome: str, old_region: Optional[Entity], notes: list[str],
) -> Entity:
    archetype = _weighted(rng, [(n, w) for n, w, _ in _REGION_ARCHETYPES])
    budget_roller = next(b for n, _, b in _REGION_ARCHETYPES if n == archetype)
    budget = budget_roller(rng)
    nouns = _BIOME_REGION_NOUNS.get(biome, ["Marches"])
    base = f"The {rng.choice(_REGION_ADJECTIVES)} {rng.choice(nouns)}"
    if old_region is not None:
        bearing = None
        old_c = geo.coords_from_attrs(old_region.attributes)
        if old_c:
            bearing = geo.compass_between(old_c, coords)
        name = _unique_name(graph, base, f"the {bearing}" if bearing else "the frontier")
    else:
        name = _unique_name(graph, base, "the frontier")

    region = graph.upsert_entity(
        name, EntityType.PLACE, subtype=PlaceScale.REGION,
        attributes={
            "description": f"Largely uncharted {biome} country; its name is what travelers call it.",
            "scale": "region",
            "archetype": archetype,
            "settlement_budget": budget,
            "coords": geo.coords_attr(*coords),
            "climate": geo.climate_for(coords),
        },
        tags=["region", archetype, biome],
    )
    if old_region is not None:
        graph.add_relation(region, RelationType.ADJACENT_TO, old_region)
    notes.append(
        f"founded region '{name}' ({archetype}: {budget['town']} town(s), "
        f"{budget['village']} village(s); {geo.climate_for(coords)})"
    )
    return region


def ensure_frontier_around(graph: WorldGraph, place_ref, *, notes: Optional[list[str]] = None) -> list[str]:
    """Guarantee unexplored, pre-constrained stubs near a place.

    Called when a stub is explored or a PC moves somewhere. No-op unless the
    frontier has thinned. Returns (and optionally appends to ``notes``) a log
    of what was created. Deterministic per (place slug): retries can't
    duplicate — identical names upsert onto themselves.
    """
    log: list[str] = [] if notes is None else notes
    with Session(graph.engine) as s:
        place = graph._resolve_entity(s, place_ref)
        if place is None or place.type != EntityType.PLACE:
            return log
        # Frontier hangs off map-level places. A building/room inherits coords
        # from its settlement — chart around THAT, not the taproom door.
        for _ in range(3):
            if geo.coords_from_attrs(place.attributes) is not None:
                break
            rel = s.exec(
                select(Relation).where(
                    Relation.src_id == place.id,
                    Relation.rel_type == RelationType.PART_OF,
                    Relation.valid_to == None,  # noqa: E711
                )
            ).first()
            parent = s.get(Entity, rel.dst_id) if rel else None
            if parent is None:
                break
            place = parent
        here = graph._coords_in_db(s, place)
        if here is None:
            return log

        adj_rels = s.exec(
            select(Relation).where(
                Relation.rel_type == RelationType.ADJACENT_TO,
                Relation.valid_to == None,  # noqa: E711
            )
        ).all()
        neighbor_ids = {
            (r.dst_id if r.src_id == place.id else r.src_id)
            for r in adj_rels if place.id in (r.src_id, r.dst_id)
        }
        nearby_stubs = 0
        for nid in neighbor_ids:
            n = s.get(Entity, nid)
            if n is not None and n.status == "unexplored":
                nearby_stubs += 1
        if nearby_stubs >= MIN_FRONTIER_STUBS:
            return log

        region = _region_of(graph, s, place)
        place_biome = (place.attributes or {}).get("biome", "")

    rng = random.Random(f"cartographer:{place.slug}")
    want = MIN_FRONTIER_STUBS - nearby_stubs + rng.randint(0, 1)
    # Spread directions: away-facing bias comes free because near-side space
    # is already occupied and gets skipped by the clearance check.
    directions = rng.sample(list(geo._COMPASS_BEARING.keys()), k=min(8, want * 3))

    made = 0
    for direction in directions:
        if made >= want:
            break
        miles = rng.uniform(STUB_MIN_MI, STUB_MAX_MI)
        coords = geo.offset_coords(here, direction, miles)
        with Session(graph.engine) as s:
            all_places = _coordful_places(s)
        if any(geo.distance_mi(coords, c) < STUB_CLEARANCE_MI for _, c in all_places):
            continue

        climate = geo.climate_for(coords)
        candidates = _BIOMES_BY_CLIMATE.get(climate, ["hills"])
        if place_biome in candidates and rng.random() < 0.5:
            biome = place_biome  # terrain continuity
        else:
            biome = rng.choice(candidates)

        # Founding a region happens lazily, exactly when territory outruns the
        # old one — this is how kingdoms get room to exist at all.
        stub_region = region
        region_c = geo.coords_from_attrs(region.attributes) if region is not None else None
        if region is None or (region_c and geo.distance_mi(region_c, coords) > REGION_RADIUS_MI):
            stub_region = _found_region(graph, rng, coords, biome, region, log)
            region = stub_region  # subsequent stubs in this batch join it

        archetype = (stub_region.attributes or {}).get("archetype", "provinces")
        ceiling = _weighted(rng, _CEILING_WEIGHTS.get(archetype, _CEILING_WEIGHTS["provinces"]))
        danger = _weighted(rng, _DANGERS)
        noun = rng.choice(_STUB_NOUNS.get(biome, ["wilds"]))
        # Short, evocative, non-recursive names ("The Grey Tors"); collisions
        # fall back to a directional qualifier rather than chaining parents.
        base = f"The {rng.choice(_REGION_ADJECTIVES)} {noun.title()}"
        name = _unique_name(graph, base, f"the {direction}")

        stub = graph.upsert_entity(
            name, EntityType.PLACE, subtype=PlaceScale.WILDS, status="unexplored",
            attributes={
                "stub": True,
                "biome": biome,
                "danger": danger,
                "scale_ceiling": ceiling,
                "motifs": roll_motifs(biome, 3, rng=rng),
                "denizens": census.stub_denizens(biome, rng),
                "description": f"Unexplored {biome} {direction} of {place.name}.",
                "coords": geo.coords_attr(*coords),
                "climate": climate,
            },
            tags=["frontier", "stub", biome],
        )
        graph.add_relation(
            stub, RelationType.ADJACENT_TO, place,
            attributes={"direction": direction, "travel_time": geo.travel_time_str(miles)},
        )
        graph.add_relation(stub, RelationType.PART_OF, stub_region)
        log.append(
            f"charted frontier '{name}' ({direction}, ~{miles:.0f} mi: {biome}, "
            f"danger {danger}, ceiling {ceiling})"
        )
        made += 1

    return log
