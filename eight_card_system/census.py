"""
The census — makes settlements and wilds feel inhabited without drowning the
context window or the database.

Three layers of density, cheapest first:

1. **Backdrop (statistics, zero entities)**: every settlement gets a census —
   population, ward names, trades — stored in its attributes and rendered as a
   couple of lines. The DM treats it as real and freely names minor folk
   consistent with it; the extraction loop promotes anyone who matters into a
   tracked entity. Hundreds of implied people, none stored.
2. **Anchors (real entities, on arrival)**: first time a PC arrives, a rolled
   batch of notable NPCs (with roles, dispositions, hooks) and key places
   (inn, shops) is created. Villages/towns flesh at once; cities flesh their
   census + wards immediately but each ward's anchors only when entered, so a
   city can imply thousands while context stays ward-local.
3. **Denizens (wilds)**: frontier stubs carry a rolled creature presence so
   even empty country has teeth and texture.

Everything is deterministic per slug (same settlement always rolls the same
folk) and idempotent (flags in attributes stop re-fleshing).
"""
from __future__ import annotations

import random
from typing import Optional

from sqlmodel import Session, select

from .graph import WorldGraph, slugify
from .models import Entity, Relation, RelationType, EntityType, PlaceScale

# ---------------------------------------------------------------------------
# Name / flavor tables
# ---------------------------------------------------------------------------

_FIRST = ["Berrin", "Salla", "Tomm", "Edda", "Garrick", "Mira", "Old Hob",
          "Petra", "Colm", "Ysolde", "Dannic", "Ferra", "Wilm", "Anka",
          "Jorun", "Liss", "Otho", "Brenna", "Kell", "Maud", "Rennic",
          "Sela", "Ulf", "Vess", "Harl", "Ida", "Nock", "Tilda"]
_SURNAME = ["Ashvale", "Millwright", "Copperpot", "Thatcher", "Reedmoor",
            "Blackbarrel", "Fenwick", "Stoneham", "Carter", "Weaver",
            "Saltmarsh", "Hollowell", "Brackwater", "Tanner", "Greenhollow",
            "Ironmonger", "Pyke", "Dunmoor", "Wold", "Harrow"]
_DISPOSITIONS = ["friendly", "gruff", "wary", "cheerful", "sly", "weary",
                 "curious", "proud", "nervous", "kindly"]
_HOOKS = [
    "owes coin to the wrong people",
    "recently widowed and taking it hard",
    "knows a rumor worth more than they realize",
    "is quietly skimming from their employer",
    "wants a dangerous errand run, discreetly",
    "lost a sibling to the roads last winter",
    "is new here and desperate to fit in",
    "keeps a weapon under the counter and knows how to use it",
    "informs for the local authority",
    "collects gossip and trades it like coin",
    "is saving to leave this place forever",
    "swears they saw something impossible last month",
]

# Ward archetypes -> (place-name templates, roles found there)
_WARD_TYPES: dict[str, tuple[list[str], list[str]]] = {
    "market": (["{s} Market", "The Wool Exchange", "Salter's Square"],
               ["merchant", "grain factor", "moneychanger", "carter", "fence",
                "map-maker"]),
    "temple": (["Temple Lane", "Shrinerow", "The Godsway"],
               ["priest", "acolyte", "gravedigger", "almoner"]),
    "craft": (["Tanners' Row", "Smith Street", "The Cooperage"],
              ["blacksmith", "cooper", "weaver", "tanner", "brewer", "mason"]),
    "docks": (["The Wharves", "Ferry Landing", "Netter's Quay"],
              ["harbormaster", "ferryman", "fisher", "smuggler", "netmaker",
               "tattoo artist"]),
    "gate": (["Gatewards", "The Tollhouse", "Wallside"],
             ["guard sergeant", "toll clerk", "stablemaster", "courier"]),
    "garrison": (["The Garrison", "Drillyard", "The Old Keep"],
                 ["guard captain", "drill sergeant", "quartermaster"]),
    "noble": (["Highcourt", "The Magistracy", "Silverhill"],
              ["magistrate", "steward", "clerk", "tax assessor"]),
    "slums": (["The Warrens", "Mudside", "Ratchurch"],
              ["pawnbroker", "rat-catcher", "beggar-king", "dice-den keeper",
               "tattoo artist"]),
}

# Which wards a settlement of a given scale draws from (in priority order).
_WARDS_BY_SCALE = {
    "village": [],
    "town": ["market", "craft", "temple", "gate", "docks", "slums"],
    "city": ["market", "craft", "temple", "gate", "garrison", "noble",
             "slums", "docks"],
}

_TRADES_BY_BIOME = {
    "farmland": ["grain", "wool", "milling"], "forest": ["timber", "charcoal", "furs"],
    "hills": ["herding", "quarrying", "cheese"], "river": ["river freight", "fishing", "reeds"],
    "swamp": ["peat", "eels", "dyes"], "mountains": ["mining", "smelting", "stonework"],
    "desert": ["caravans", "salt", "glass"], "coast": ["fishing", "salt", "shipping"],
}

# Wilds creature presence per biome — names chosen to hit the SRD rules tables
# so the DM gets exact statblocks when a fight starts.
_DENIZENS_BY_BIOME = {
    "farmland": ["wolf", "bandit", "giant rat", "goblin", "swarm of ravens"],
    "forest": ["wolf", "black bear", "goblin", "owlbear", "giant spider", "dryad"],
    "hills": ["wolf", "hobgoblin", "worg", "hill giant", "harpy"],
    "river": ["crocodile", "bandit", "giant frog", "merrow"],
    "swamp": ["lizardfolk", "giant frog", "will-o'-wisp", "crocodile", "shambling mound"],
    "mountains": ["eagle", "ogre", "harpy", "griffon", "stone giant"],
    "desert": ["giant scorpion", "gnoll", "dust mephit", "lamia"],
    "coast": ["giant crab", "sahuagin", "harpy", "merfolk"],
}
_DENIZENS_GENERIC = ["wolf", "bandit", "goblin", "giant rat"]

# Population + structure by settlement scale.
_PROFILES = {
    "village": {"pop": (80, 400), "wards": (0, 0), "anchors": (4, 6)},
    "town": {"pop": (600, 3000), "wards": (3, 5), "anchors": (2, 3)},   # per ward
    "city": {"pop": (5000, 25000), "wards": (6, 9), "anchors": (4, 7)},  # per ward, lazy
}


def person_name(rng: random.Random) -> str:
    return f"{rng.choice(_FIRST)} {rng.choice(_SURNAME)}"


def stub_denizens(biome: str, rng: random.Random, n: int = 3) -> list[str]:
    table = _DENIZENS_BY_BIOME.get(biome, _DENIZENS_GENERIC)
    return rng.sample(table, k=min(n, len(table)))


def _scale_of(e: Entity) -> str:
    return str((e.attributes or {}).get("scale") or e.subtype or "").strip().lower()


def _spawn_npc(graph: WorldGraph, rng: random.Random, role: str, home_ref,
               ward_label: str) -> Entity:
    # A name collision would MERGE two different people (upsert is by slug),
    # so reroll, then disambiguate by trade as villages did historically.
    name = person_name(rng)
    for _ in range(5):
        if graph.get_entity(slugify(name)) is None:
            break
        name = person_name(rng)
    if graph.get_entity(slugify(name)) is not None:
        name = f"{name} the {role.title()}"
    # Vitals for the entropy system: everyone is born, everyone dies.
    from . import entropy
    today = graph.current_day()
    age_years = rng.randint(*entropy.ADULT_AGE_RANGE)
    # create_entity: every spawned person is a NEW identity — a repeated name
    # (rerolls exhausted) still gets its own slug, never a merge.
    npc = graph.create_entity(
        name, EntityType.NPC,
        attributes={
            "description": f"{role} of {ward_label}; {rng.choice(_HOOKS)}.",
            "role": role,
            "disposition": rng.choice(_DISPOSITIONS),
            "born_day": today - age_years * entropy.DAYS_PER_YEAR,
            "lifespan_days": rng.randint(*entropy.LIFESPAN_RANGE_YEARS)
            * entropy.DAYS_PER_YEAR,
        },
        tags=["npc", "census", role.replace(" ", "-")],
    )
    graph.add_relation(npc, RelationType.LOCATED_IN, home_ref)
    return npc


def spawn_successor(graph: WorldGraph, dead_npc: Entity) -> Optional[Entity]:
    """Fill a dead census NPC's post with a (young-adult) successor.

    The world visibly turns over: the innkeeper dies, someone takes the keys.
    Deterministic per predecessor; the successor's description names them.
    """
    role = (dead_npc.attributes or {}).get("role")
    if not role:
        return None
    with Session(graph.engine) as s:
        rel = s.exec(select(Relation).where(
            Relation.src_id == dead_npc.id,
            Relation.rel_type == RelationType.LOCATED_IN,
            Relation.valid_to == None,  # noqa: E711
        )).first()
        home = s.get(Entity, rel.dst_id) if rel else None
    if home is None:
        return None
    rng = random.Random(f"successor:{dead_npc.slug}")
    heir = _spawn_npc(graph, rng, role, home, home.name)
    # Successors start young — no immediate re-death cascades.
    from . import entropy
    today = graph.current_day()
    graph.upsert_entity(
        heir.name, heir.type, slug=heir.slug, status=heir.status,
        attributes={
            "born_day": today - rng.randint(20, 35) * entropy.DAYS_PER_YEAR,
            "description": (heir.attributes or {}).get("description", "")
            + f" Took over after {dead_npc.name} passed.",
        },
    )
    graph.add_event(
        f"{heir.name} took over as {role} after {dead_npc.name}'s passing.",
        location=home.slug, involved=[heir.slug],
    )
    return heir


# Venues that can open inside a ward: (name pool, venue kind, keeper role).
# The seedy institutions every living city needs — gambling dens where coin
# changes hands after dark, tattoo parlors where stories get inked in.
_WARD_VENUES: dict[str, list[tuple[list[str], str, str]]] = {
    "slums": [
        (["The Crooked Die", "The Lucky Rat", "The Velvet Purse"],
         "gambling den", "den keeper"),
        (["The Needle & Ash", "Inkfang's Parlor"], "tattoo parlor", "tattoo artist"),
    ],
    "docks": [
        (["The Drowned Dice", "The Last Wager"], "gambling den", "den keeper"),
        (["The Sailor's Mark", "The Anchor & Ink"], "tattoo parlor", "tattoo artist"),
    ],
    "market": [
        (["The Gilded Wager", "The Turning Coin"], "gambling den", "den keeper"),
    ],
}
_VENUE_CHANCE = 0.65


def _flesh_ward_anchors(graph: WorldGraph, rng: random.Random, ward: Entity,
                        ward_type: str, count: int, log: list[str]) -> None:
    _, roles = _WARD_TYPES[ward_type]
    picked = rng.sample(roles, k=min(count, len(roles)))
    for role in picked:
        _spawn_npc(graph, rng, role, ward, ward.name)
    log.append(f"peopled '{ward.name}' with {len(picked)} notables ({', '.join(picked)})")

    # Maybe a venue opens its doors here (gambling den, tattoo parlor, ...).
    for pool, venue_kind, keeper_role in _WARD_VENUES.get(ward_type, []):
        if rng.random() > _VENUE_CHANCE:
            continue
        vname = rng.choice(pool)
        if graph.get_entity(slugify(vname)) is not None:
            vname = f"{vname} ({ward.name})"
        venue = graph.create_entity(
            vname, EntityType.PLACE, subtype=venue_kind.replace(" ", "-"),
            attributes={"scale": "building", "venue": venue_kind,
                        "description": f"A {venue_kind} in {ward.name}."},
            tags=["venue", venue_kind.replace(" ", "-")],
        )
        graph.add_relation(venue, RelationType.PART_OF, ward)
        _spawn_npc(graph, rng, keeper_role, venue, vname)
        log.append(f"opened '{vname}' ({venue_kind}) with its {keeper_role}")


def ensure_ward_anchors(graph: WorldGraph, ward_ref) -> list[str]:
    """Populate a city ward's notables the first time a PC enters it."""
    log: list[str] = []
    ward = graph.get_entity(ward_ref)
    if ward is None:
        return log
    attrs = dict(ward.attributes or {})
    if attrs.get("fleshed") or attrs.get("ward_type") is None:
        return log
    rng = random.Random(f"census:{ward.slug}")
    profile = _PROFILES["city"]
    _flesh_ward_anchors(graph, rng, ward, attrs["ward_type"],
                        rng.randint(*profile["anchors"]), log)
    graph.upsert_entity(ward.name, ward.type, slug=ward.slug, status=ward.status,
                        attributes={"fleshed": True})
    return log


def flesh_settlement(graph: WorldGraph, settlement_ref) -> list[str]:
    """Give a settlement its census, wards, inn, and first notables.

    Idempotent (attributes["census"] flags completion) and deterministic per
    slug. Villages/towns are peopled immediately; a city creates its wards but
    peoples each one only when a PC first enters it (``ensure_ward_anchors``).
    """
    log: list[str] = []
    settlement = graph.get_entity(settlement_ref)
    if settlement is None:
        return log
    attrs = dict(settlement.attributes or {})
    if attrs.get("census"):
        return log
    scale = _scale_of(settlement)
    if scale in ("settlement",):
        scale = "town"
    if scale not in _PROFILES:
        return log

    rng = random.Random(f"census:{settlement.slug}")
    profile = _PROFILES[scale]
    population = attrs.get("population") or rng.randint(*profile["pop"])

    # Trades come from the biomes around it (adjacent places), else generic.
    biomes: list[str] = []
    with Session(graph.engine) as s:
        ent = graph._resolve_entity(s, settlement.slug)
        rels = s.exec(select(Relation).where(
            Relation.rel_type == RelationType.ADJACENT_TO,
            Relation.valid_to == None,  # noqa: E711
        )).all()
        for r in rels:
            if ent.id in (r.src_id, r.dst_id):
                other = s.get(Entity, r.dst_id if r.src_id == ent.id else r.src_id)
                b = (other.attributes or {}).get("biome") if other else None
                if b:
                    biomes.append(b)
    trade_pool = sorted({t for b in (biomes or ["farmland"])
                         for t in _TRADES_BY_BIOME.get(b, ["trade"])})
    trades = rng.sample(trade_pool, k=min(3, len(trade_pool)))

    # Wards (towns and cities).
    ward_names: list[str] = []
    ward_entities: list[tuple[Entity, str]] = []
    n_wards = rng.randint(*profile["wards"]) if profile["wards"][1] else 0
    if n_wards:
        types = _WARDS_BY_SCALE[scale][:]
        # Docks only make sense with water nearby.
        if not any(b in ("river", "coast") for b in biomes):
            types = [t for t in types if t != "docks"]
        rng.shuffle(types)
        for ward_type in types[:n_wards]:
            templates, _ = _WARD_TYPES[ward_type]
            wname = rng.choice(templates).format(s=settlement.name)
            # Distinct settlements roll from the same template pool — prefix
            # with the settlement so "Mudside" here never merges with one there.
            if graph.get_entity(slugify(wname)) is not None:
                wname = f"{settlement.name} {wname.removeprefix('The ')}"
            ward = graph.upsert_entity(
                wname, EntityType.PLACE, subtype=PlaceScale.DISTRICT,
                attributes={"scale": "district", "ward_type": ward_type,
                            "description": f"The {ward_type} quarter of {settlement.name}."},
                tags=["ward", ward_type],
            )
            graph.add_relation(ward, RelationType.PART_OF, settlement)
            ward_names.append(wname)
            ward_entities.append((ward, ward_type))

    # Census onto the settlement itself — the statistical backdrop.
    graph.upsert_entity(
        settlement.name, settlement.type, slug=settlement.slug,
        status=settlement.status,
        attributes={"census": True, "population": population,
                    "wards": ward_names, "trades": trades},
    )
    log.append(f"census for '{settlement.name}': pop ~{population}, "
               f"{len(ward_names) or 'no'} wards, trades: {', '.join(trades)}")

    # An inn: always, everywhere — the adventurer's front door.
    inn_pool = ["The Drowned Rat", "The Gilded Ewe", "The Last Lantern",
                "The Oak & Iron", "The Wandering Coin", "The Split Keg"]
    rng.shuffle(inn_pool)
    inn_name = next((n for n in inn_pool if graph.get_entity(slugify(n)) is None),
                    f"The {settlement.name} Arms")
    if graph.get_entity(slugify(inn_name)) is None:
        inn = graph.upsert_entity(
            inn_name, EntityType.PLACE, subtype="tavern",
            attributes={"scale": "building",
                        "description": f"{settlement.name}'s inn and common room."},
            tags=["tavern", "inn"],
        )
        graph.add_relation(inn, RelationType.PART_OF, settlement)
        _spawn_npc(graph, rng, "innkeeper", inn, inn_name)
        log.append(f"raised '{inn_name}' (inn) with its keeper")

    # People it: villages and towns immediately; city wards wait for footsteps.
    if scale == "village":
        roles = ["reeve", "miller", "smith", "herbalist", "shepherd", "carpenter"]
        for role in rng.sample(roles, k=rng.randint(*profile["anchors"])):
            _spawn_npc(graph, rng, role, settlement, settlement.name)
        log.append(f"peopled '{settlement.name}' with village notables")
    elif scale == "town":
        for ward, ward_type in ward_entities:
            _flesh_ward_anchors(graph, rng, ward, ward_type,
                                rng.randint(*profile["anchors"]), log)
    # city: wards exist; ensure_ward_anchors fires on first entry.

    return log


def settlement_of(graph: WorldGraph, place_ref) -> Optional[Entity]:
    """The enclosing settlement of a place (itself, or via PART_OF, 3 hops)."""
    with Session(graph.engine) as s:
        current = graph._resolve_entity(s, place_ref)
        for _ in range(4):
            if current is None:
                return None
            if current.subtype == PlaceScale.SETTLEMENT or \
                    _scale_of(current) in ("village", "town", "city", "settlement"):
                return current
            rel = s.exec(select(Relation).where(
                Relation.src_id == current.id,
                Relation.rel_type == RelationType.PART_OF,
                Relation.valid_to == None,  # noqa: E711
            )).first()
            current = s.get(Entity, rel.dst_id) if rel else None
    return None
