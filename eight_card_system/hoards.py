"""
Lost hoards — treasure the WORLD buries, and the charts that leak out.

Until this module, a treasure map could only exist because the DM decided in
the moment that one did. That is fine as far as it goes, but it means every
buried thing is an answer to the party's presence. A world that has been going
on without them loses caravans, buries war-chests ahead of an army, and forgets
where a smuggler put his cut — and does it whether or not anyone is watching.

Two objects come out of one event, and the split is the point:

* a **hoard site** — a real PLACE with real coordinates, somewhere out on the
  frontier, marked with what's in it and what's sitting on top of it. Because
  it's a place, everything else already works on it: the tactical board can
  open there, the cartographer's frontier can grow past it, and a treasure map
  can mark it, since a chart is inked from coordinates like any other sheet.
* a **chart** — a world ITEM located in a settlement some distance off, which
  knows the slug of the site it leads to. It is the loose end. Somebody's
  effects, a fence's back room, a dead man's boot. The DM doesn't have to
  invent the destination when handing it over; they hand over the object and
  the code already knows where it points.

Both are placed AWAY from the party on purpose. A hoard that spawns under the
players' feet is a vending machine; one that spawns four days' travel away,
with a chart sitting in a town they might pass through, is a rumour with
somewhere to go.

Everything here is deterministic per (slug, era) like the rest of ``entropy``,
so a re-run of a pass can't mint a second copy of the same hoard.
"""
from __future__ import annotations

import random
from typing import Optional

from sqlmodel import Session, select

from . import geo
from .graph import WorldGraph
from .models import Entity, EntityType, PlaceScale, RelationType

#: World-days between hoard passes, and the chance one lands when a pass runs.
#: Rare on purpose: the fun of a treasure map is that it is not the weather.
HOARD_INTERVAL_DAYS = 60
HOARD_CHANCE = 0.35
#: Never more than this many undiscovered hoards in the world at once. Without
#: a cap, a long-running campaign accretes buried gold faster than any party
#: can dig it up, and every chart stops meaning anything.
MAX_OPEN_HOARDS = 4

#: How far from the nearest PC a new hoard may land. Close enough to be worth
#: going, far enough that going is a decision.
HOARD_MIN_MI, HOARD_MAX_MI = 30.0, 140.0
#: A chart surfaces in a settlement within this far of the hoard — whoever lost
#: it was in the area.
CHART_NEAR_MI = 90.0

#: (fiction, what's in it, what's guarding it). The tier feeds the DM's own
#: loot call; this module never rolls items, because ``loot/`` owns that and a
#: second roller would drift from it.
_ORIGINS = [
    ("a tax caravan that never reached its lord", "coin and plate", "moderate"),
    ("a war-chest buried a day ahead of an advancing army", "coin and arms", "high"),
    ("a smuggler's cut, cached and never collected", "contraband", "low"),
    ("the estate of a merchant who died on the road", "trade goods and coin", "low"),
    ("a temple's reliquary, hidden when the raiders came", "relics", "moderate"),
    ("a dragon's overlooked spill, scattered when its lair was sacked",
     "gems and oddments", "high"),
    ("a company's pay, cached by a paymaster who deserted", "coin", "moderate"),
]

_SITE_NOUNS = {
    "farmland": ["Fallow", "Ditch", "Stone Barn"],
    "forest": ["Hollow", "Deadfall", "Root Cellar"],
    "hills": ["Cairn", "Scarp", "Sheepfold"],
    "river": ["Oxbow", "Ford", "Sunken Wain"],
    "swamp": ["Sink", "Reedbank", "Drowned Hut"],
    "mountains": ["Scree", "Cleft", "Watch-Stone"],
    "desert": ["Pan", "Dry Well", "Buried Camp"],
    "coast": ["Cove", "Wreck", "Tide Cave"],
}
_SITE_ADJECTIVES = ["Forgotten", "Drowned", "Crooked", "Blackened", "Quiet",
                    "Hollow", "Last", "Nameless", "Cold"]

_CHART_FORMS = [
    "a smuggler's chart", "a soldier's scrawled map", "a dying man's map",
    "a chart sewn into a coat lining", "a map scratched on a wax tablet",
    "a merchant's private survey",
]

#: Marks a place as a hoard site, and an item as a chart to one.
HOARD_ATTR = "hoard"
CHART_ATTR = "chart_to"


def open_hoards(graph: WorldGraph) -> list[Entity]:
    """Hoard sites nobody has emptied yet."""
    with Session(graph.engine) as s:
        return [e for e in s.exec(select(Entity).where(
            Entity.type == EntityType.PLACE)).all()
            if (e.attributes or {}).get(HOARD_ATTR)
            and not (e.attributes or {}).get(HOARD_ATTR, {}).get("claimed")]


def chart_target(graph: WorldGraph, chart_ref) -> Optional[str]:
    """The slug a chart item points at, or None if it isn't a chart.

    This is what lets the DM hand over the OBJECT without restating the
    destination — and, more usefully, without being able to get it wrong.
    """
    ent = graph.get_entity(chart_ref)
    if ent is None:
        for cand in graph.find_entities_by_name(str(chart_ref)):
            if (cand.attributes or {}).get(CHART_ATTR):
                ent = cand
                break
    if ent is None:
        return None
    target = (ent.attributes or {}).get(CHART_ATTR)
    return str(target) if target else None


def _pc_positions(graph: WorldGraph) -> list[tuple[float, float]]:
    out = []
    with Session(graph.engine) as s:
        for pc in s.exec(select(Entity).where(Entity.type == EntityType.PC)).all():
            c = graph._coords_in_db(s, pc)
            if c is not None:
                out.append(c)
    return out


def _chart_town(graph: WorldGraph, coords, rng: random.Random) -> Optional[Entity]:
    """Where the chart turns up: a settlement near the hoard, else the nearest.

    The near-radius is a preference, not a requirement. A chart is a portable
    object — it ends up wherever the person who lost it got to, which may be a
    long way from what they were describing. Requiring a town within
    ``CHART_NEAR_MI`` meant that on a young map, where the only settlement is
    the starting town, no hoard ever leaked a chart at all and the whole
    mechanism sat silent.
    """
    candidates: list[tuple[float, Entity]] = []
    with Session(graph.engine) as s:
        for e in s.exec(select(Entity).where(
                Entity.type == EntityType.PLACE,
                Entity.status == "active")).all():
            scale = str((e.attributes or {}).get("scale")
                        or e.subtype or "").lower()
            if scale not in ("settlement", "town", "city", "village"):
                continue
            c = geo.coords_from_attrs(e.attributes)
            if c is not None:
                candidates.append((geo.distance_mi(coords, c), e))
    if not candidates:
        return None
    near = sorted((d, e) for d, e in candidates if d <= CHART_NEAR_MI)
    if near:
        return rng.choice([e for _, e in near])
    return min(candidates, key=lambda t: (t[0], t[1].slug))[1]


def _has_neighbour_biome(graph: WorldGraph, coords) -> bool:
    """True when some charted place near ``coords`` can lend its terrain."""
    from . import placelore
    with Session(graph.engine) as s:
        for e in s.exec(select(Entity).where(
                Entity.type == EntityType.PLACE)).all():
            if not placelore._biome_of(e):
                continue
            c = geo.coords_from_attrs(e.attributes)
            if c is not None and geo.distance_mi(coords, c) <= \
                    placelore.BIOME_BORROW_RADIUS_MI:
                return True
    return False


def spawn_lost_hoard(graph: WorldGraph, today: int, *,
                     rng: Optional[random.Random] = None) -> Optional[dict]:
    """Bury one hoard out in the world, and leak a chart to it. None if not now.

    Returns a summary dict for the caller to log as a world event. Never
    raises: mapkeeping must not be able to break the clock tick that called it.
    """
    era = today // HOARD_INTERVAL_DAYS
    rng = rng or random.Random(f"hoard:{era}")
    if rng.random() > HOARD_CHANCE:
        return None
    if len(open_hoards(graph)) >= MAX_OPEN_HOARDS:
        return None

    anchors = _pc_positions(graph)
    if not anchors:
        return None
    origin = rng.choice(sorted(anchors))
    direction = rng.choice(list(geo._COMPASS_BEARING.keys()))
    miles = rng.uniform(HOARD_MIN_MI, HOARD_MAX_MI)
    coords = geo.offset_coords(origin, direction, miles)

    # Don't bury it on top of somewhere that already exists — the site is
    # supposed to be a place nobody has a reason to visit.
    from .cartographer import STUB_CLEARANCE_MI
    with Session(graph.engine) as s:
        for e in s.exec(select(Entity).where(Entity.type == EntityType.PLACE)).all():
            c = geo.coords_from_attrs(e.attributes)
            if c is not None and geo.distance_mi(coords, c) < STUB_CLEARANCE_MI:
                return None

    # Country the map hasn't reached has no biome to borrow, and
    # ``placelore.biome_at`` then falls back to the single most typical member
    # of the climate band — which made every far-flung hoard "farmland". Roll
    # from that band's whole roster instead, exactly as the cartographer does
    # when it charts a stub out past the known world.
    from . import placelore
    from .cartographer import _BIOMES_BY_CLIMATE
    biome = placelore.biome_at(graph, coords,
                               radius_mi=placelore.BIOME_BORROW_RADIUS_MI)
    if not _has_neighbour_biome(graph, coords):
        biome = rng.choice(_BIOMES_BY_CLIMATE.get(geo.climate_for(coords),
                                                  ["hills"]))
    story, contents, guard = rng.choice(_ORIGINS)
    noun = rng.choice(_SITE_NOUNS.get(biome, ["Cache"]))
    name = f"The {rng.choice(_SITE_ADJECTIVES)} {noun}"
    if graph.get_entity(name.lower().replace(" ", "-")) is not None:
        name = f"{name} ({direction.title()})"

    site = graph.upsert_entity(
        name, EntityType.PLACE, subtype=PlaceScale.POI, status="unexplored",
        attributes={
            "description": f"An unremarkable spot in {biome} country. "
                           f"Something was left here.",
            "scale": "poi",
            "biome": biome,
            "coords": geo.coords_attr(*coords),
            "climate": geo.climate_for(coords),
            "danger": guard,
            # Prominence 0: a buried cache is famous to nobody, so it never
            # clutters a regional or world sheet. Only a CHART marks it, and a
            # chart carries its goal past the cut by being the marked feature.
            "prominence": 0,
            HOARD_ATTR: {
                "story": story,
                "contents": contents,
                "buried_day": today,
                "claimed": False,
            },
        },
        tags=["hoard", "unexplored", biome],
    )

    # The chart: a thing, in a town, that knows where it points.
    chart_form = rng.choice(_CHART_FORMS)
    chart = None
    town = _chart_town(graph, coords, rng)
    if town is not None:
        chart = graph.create_entity(
            chart_form, EntityType.ITEM,
            attributes={
                "description": f"{chart_form.capitalize()}, marked with a cross "
                               f"and no name.",
                CHART_ATTR: site.slug,
                "surfaced_day": today,
            },
            tags=["chart", "treasure-map", "rumor"],
        )
        graph.add_relation(chart.slug, RelationType.LOCATED_IN, town.slug)

    return {
        "site": site.slug,
        "site_name": site.name,
        "story": story,
        "contents": contents,
        "guard": guard,
        "biome": biome,
        "chart": chart.slug if chart is not None else None,
        "chart_name": chart_form if chart is not None else None,
        "chart_town": town.name if town is not None else None,
        "miles": round(miles),
        "direction": direction,
    }


def claim_hoard(graph: WorldGraph, site_ref) -> bool:
    """Mark a hoard emptied, so it stops counting against the cap.

    Called when the party actually digs it up. The site stays in the world (it
    is a place they have now been) — only its treasure is spent.
    """
    ent = graph.get_entity(site_ref)
    if ent is None:
        return False
    attrs = dict(ent.attributes or {})
    hoard = dict(attrs.get(HOARD_ATTR) or {})
    if not hoard or hoard.get("claimed"):
        return False
    hoard["claimed"] = True
    attrs[HOARD_ATTR] = hoard
    graph.upsert_entity(ent.name, EntityType.PLACE, slug=ent.slug,
                        status=ent.status, attributes=attrs)
    return True
