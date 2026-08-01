"""
What a place LOOKS like — the one answer three renderers have to agree on.

The world graph already knows the truth about a location: the cartographer
rolled its biome, danger and motifs; ``geo`` derives its climate from latitude;
``survival.weather`` derives the day's weather from the climate. Three separate
pictures are then drawn from that same location — the establishing scene when
the party arrives, the tactical board when a fight breaks out there, and the
parchment map a player drafts of the region — and until this module they each
guessed independently. The scene render read ``description`` and ignored
``biome`` entirely; the battlemap took ``biome`` and nothing else; the map drew
no terrain at all. Same place, three unrelated visual answers.

``character_of`` is the single source those three now share. It resolves the
place's character ONCE — inheriting a biome when the narration invented a place
without one — and hands out the three phrasings each renderer needs:

    ch = character_of(world, "the-grey-tors")
    ch.scene_look()      # "windswept slopes of bare rock..."   -> arrival art
    ch.board_look()      # "loose scree, ledges, cold light..." -> vtt battlemap
    ch.map_terrain()     # "grey mountain ridges, snowline..."  -> world map

Biome INHERITANCE is the load-bearing part. The extractor mints places from
narration ("the Hollow Barrow") with no biome at all, and a place with no
terrain is a place the renderers have to invent terrain for — which is exactly
how a barrow in the moors comes back as a jungle. So a place with no biome
borrows one, in order, from: its PART_OF ancestors, its ADJACENT_TO neighbours,
the nearest coord-ful place on the globe, and finally its climate band. The
resolved value is WRITTEN BACK to the entity (``biome_inherited: True`` marks
it as borrowed rather than rolled), because a biome that is re-derived per
render is a biome that can change between the scene and the board.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from sqlmodel import Session, select

from . import geo
from .models import Entity, Relation, RelationType, EntityType

# How far afield to look for a neighbour's biome before giving up on the graph
# and falling back to the climate band.
BIOME_BORROW_RADIUS_MI = 45.0

# Biomes the cartographer rolls, plus the interior kinds narration invents.
# Each entry is (scene phrasing, board phrasing, cartographic phrasing):
#   scene  — an establishing shot at eye level
#   board  — the floor of a 5-ft grid, seen from straight overhead
#   map    — how the terrain reads on a drawn map, at miles-per-inch
_TERRAIN = {
    "farmland": (
        "open ploughed country, hedgerows and drystone walls, scattered barns",
        "tilled soil and stubble, cart ruts, low field walls, hay bales",
        "patchwork green fields, hedgerows, scattered farmsteads",
    ),
    "forest": (
        "deep old woodland, moss-furred trunks, shafts of light through the canopy",
        "forest floor of leaf litter and roots, thick trunks, fallen deadwood",
        "dense dark-green forest canopy, wooded hills",
    ),
    "hills": (
        "rolling green hills, bare shoulders of rock, wind-bent thorn",
        "sloping turf and outcrops, loose stone, gorse clumps",
        "rolling green-brown hill country, contoured slopes",
    ),
    "river": (
        "a broad slow river, reed banks, gravel shallows",
        "running water and gravel bars, muddy banks, reed beds",
        "a winding blue river with tributaries, fords marked",
    ),
    "swamp": (
        "standing black water, drowned trees, drifting mist",
        "shallow water and mud, tussocks, rotting boardwalk, cypress knees",
        "olive-green marshland, braided waterways, reed flats",
    ),
    "mountains": (
        "sheer grey peaks, snowline, scree fans below the cliffs",
        "bare rock shelves and loose scree, ledges, drifted snow",
        "grey mountain ridges with white snowline, steep relief",
    ),
    "desert": (
        "sun-bleached dunes and cracked hardpan, heat shimmer",
        "sand and cracked pan, wind ripples, sun-bleached bone",
        "pale ochre dunes and sand flats, dry wadis",
    ),
    "coast": (
        "a rocky shoreline, breaking surf, salt-scoured cliffs",
        "wet sand and tide pools, weed-slick rock, driftwood",
        "a ragged coastline with sandy bays and offshore rocks",
    ),
    "sea": (
        "open water to the horizon, long swell, spray off the crests",
        "open water, swell and foam, floating wreckage",
        "open blue ocean with depth shading and wave hatching",
    ),
    # ----- BUILT surfaces: what a place IS, not the country it sits in -----
    # These are never borrowed by a neighbour and never persisted as terrain
    # (see _OUTDOOR): a tavern next door doesn't make the road indoors.
    "underdark": (
        "a lightless cavern system, dripping stone, pale fungal growth",
        "damp cave floor, flowstone, stalagmite clusters, dark pools",
        "cavern network shown in cutaway, tunnels and chambers",
    ),
    "dungeon": (
        "worked stone corridors, old masonry, guttering torchlight",
        "flagstone floor, mortared walls, rubble, scorched stone",
        "a keyed floor plan of vaults and passages",
    ),
    "urban": (
        "close-packed timber and stone buildings, narrow lanes, chimney smoke",
        "cobbles and packed dirt, crates, barrels, low walls, shopfronts",
        "a walled town plan, streets and roof clusters",
    ),
    "interior": (
        "a lamplit interior of timber and stone, low beams, worn furnishings",
        "plank flooring, tables and benches, a hearth, stacked crates",
        "a building shown in floor plan",
    ),
}
_GENERIC_TERRAIN = (
    "wild country, broken ground, scattered scrub",
    "packed earth and patchy grass, loose stone",
    "unsurveyed country, sketched relief",
)

#: The biomes that describe OUTDOOR COUNTRY — the ones the cartographer rolls.
#: Only these are inheritable and persistable: they answer "what land is this",
#: which is a property of the coordinates and so is shared by everything
#: standing on them. The built surfaces above answer "what is this place", which
#: is a property of the place alone.
_OUTDOOR = {"farmland", "forest", "hills", "river", "swamp", "mountains",
            "desert", "coast", "sea"}

#: Place KIND -> the surface to draw there, overriding the surrounding country.
#: Keyed by ``subtype`` and by ``attributes["scale"]``, because the graph uses
#: both vocabularies: the seed files taverns as subtype "tavern", the extractor
#: files them as scale "building". A fight in the Silver Tankard happens on
#: floorboards no matter that Greenfields is farmland.
_KIND_SURFACE = {
    # settlements and their innards
    "settlement": "urban", "town": "urban", "city": "urban",
    "village": "urban", "district": "urban",
    # buildings and rooms
    "building": "interior", "room": "interior", "tavern": "interior",
    "inn": "interior", "market": "interior", "temple": "interior",
    "shrine": "interior", "shop": "interior", "hall": "interior",
    "keep": "interior", "house": "interior",
    # the underground
    "dungeon": "dungeon", "crypt": "dungeon", "tomb": "dungeon",
    "ruin": "dungeon", "cave": "underdark", "cavern": "underdark",
}

# Last-resort biome per climate band, used only when the graph offers nothing
# to borrow from. Deliberately the most typical member of that band's roster in
# ``cartographer._BIOMES_BY_CLIMATE``, so an inherited biome is never a biome
# the cartographer would refuse to roll there.
_CLIMATE_BIOME = {
    "arctic": "mountains", "subarctic": "forest", "cool temperate": "forest",
    "temperate": "farmland", "warm temperate": "hills", "arid": "desert",
    "desert": "desert", "subtropical": "forest", "tropical": "forest",
}


def terrain_words(biome: str, register: str = "scene") -> str:
    """The visual phrasing for a biome in one of three registers.

    ``register`` is "scene" (eye level), "board" (overhead, 5-ft squares) or
    "map" (drawn cartography). An unknown biome falls through to generic
    phrasing rather than being dropped — a render with no terrain clause at all
    is how a moor becomes a jungle.
    """
    idx = {"scene": 0, "board": 1, "map": 2}.get(register, 0)
    return _TERRAIN.get((biome or "").strip().lower(), _GENERIC_TERRAIN)[idx]


@dataclass
class PlaceCharacter:
    """Everything the renderers need to agree about one location.

    Two terrain values, deliberately: ``biome`` is the surface to draw AT this
    place (a taproom's floorboards), ``terrain`` is the outdoor country it sits
    IN (the farmland Millbrook stands on). Scene and board renders want the
    first; the world map, which draws country rather than rooms, wants the
    second. Collapsing them is what put a river through a tavern.
    """
    slug: str
    name: str
    scale: str = "poi"
    biome: str = ""
    terrain: str = ""
    biome_inherited: bool = False
    climate: str = "temperate"
    season: str = ""
    time_of_day: str = ""
    weather: str = ""
    precipitation: str = ""
    danger: str = ""
    description: str = ""
    motifs: list[str] = field(default_factory=list)
    denizens: list[str] = field(default_factory=list)
    structures: list[str] = field(default_factory=list)
    region: str = ""
    region_archetype: str = ""
    coords: Optional[tuple[float, float]] = None
    unexplored: bool = False

    # ----- the three phrasings -----

    def scene_look(self, limit: int = 320) -> str:
        """Eye-level appearance for an establishing render of the place."""
        bits = [self.description, terrain_words(self.biome, "scene")]
        # One motif only: motifs are POI-scale hooks, and stacking three of them
        # gives the model three subjects to fight over in one frame.
        if self.motifs:
            bits.append(self.motifs[0])
        bits.append(self._conditions())
        return _join(bits)[:limit]

    def board_look(self, limit: int = 240) -> str:
        """Ground-level texture for the battlemap under the tactical grid.

        No motifs and no description here — the board's subject is the FLOOR,
        and mentioning "a hanging lantern with no one tending it" to an
        overhead render just puts a lantern in the middle of the fight.
        """
        bits = [terrain_words(self.biome, "board"), self._conditions(brief=True)]
        return _join(bits)[:limit]

    def map_terrain(self, limit: int = 200) -> str:
        """How this place's COUNTRY reads as drawn cartography.

        Keyed on ``terrain``, not ``biome``: a map drawn at miles-per-inch
        shows the farmland a town stands in, not the town's floorboards.
        """
        return _join([terrain_words(self.terrain or self.biome, "map")])[:limit]

    def context_key(self) -> str:
        """The image-cache bucket: same place, new season or weather, new art.

        Tracks ``_conditions`` exactly, indoors included — a bucket that varies
        on something the prompt doesn't mention just re-renders an identical
        picture into a new slot every time the weather turns.
        """
        if self.indoors:
            return "indoors"
        return _join([self.season, self.time_of_day, self.precipitation]) or "default"

    @property
    def indoors(self) -> bool:
        """True where the sky doesn't reach — no weather, no season."""
        return self.biome in ("interior", "dungeon", "underdark")

    def _conditions(self, brief: bool = False) -> str:
        """Season / light / weather, the parts that change between visits.

        Indoors this collapses to nothing: a taproom render that lists "winter,
        snow" gets snow ON THE FLOORBOARDS. Time of day goes too — under a roof
        it's lamplight either way, and the terrain phrasing already says so.
        """
        if self.indoors:
            return ""
        bits = [self.season, self.time_of_day]
        if self.precipitation and self.precipitation != "clear":
            bits.append(self.precipitation)
        elif not brief and self.weather:
            bits.append(self.weather)
        return _join(bits)


def _join(bits) -> str:
    seen: list[str] = []
    for b in bits:
        b = str(b or "").strip().strip(",")
        if b and b.lower() not in {s.lower() for s in seen}:
            seen.append(b)
    return ", ".join(seen)


# ----- biome resolution -------------------------------------------------


def _biome_of(entity: Optional[Entity]) -> str:
    """A place's own OUTDOOR terrain, from attributes or the cartographer's tags.

    Restricted to ``_OUTDOOR`` on purpose — this is the value neighbours borrow,
    and "interior" is not a fact about the land.
    """
    if entity is None:
        return ""
    attrs = entity.attributes or {}
    for key in ("biome", "terrain"):
        v = str(attrs.get(key) or "").strip().lower()
        if v in _OUTDOOR:
            return v
    for tag in (entity.tags or []):
        if str(tag).strip().lower() in _OUTDOOR:
            return str(tag).strip().lower()
    return ""


def surface_for(subtype: Optional[str], scale: Optional[str]) -> str:
    """The built surface a place's KIND implies, or "" for open country.

    Checked against both vocabularies the graph uses (``subtype`` from the seed,
    ``attributes["scale"]`` from the extractor) — whichever answers first.
    """
    for word in (subtype, scale):
        hit = _KIND_SURFACE.get(str(word or "").strip().lower())
        if hit:
            return hit
    return ""


def _ancestors(s: Session, place: Entity, hops: int = 4) -> list[Entity]:
    out: list[Entity] = []
    current = place
    for _ in range(hops):
        rel = s.exec(select(Relation).where(
            Relation.src_id == current.id,
            Relation.rel_type == RelationType.PART_OF,
            Relation.valid_to == None,  # noqa: E711
        )).first()
        if rel is None:
            break
        parent = s.get(Entity, rel.dst_id)
        if parent is None or parent.id in {a.id for a in out}:
            break
        out.append(parent)
        current = parent
    return out


def _neighbours(s: Session, place: Entity) -> list[Entity]:
    rels = s.exec(select(Relation).where(
        Relation.rel_type == RelationType.ADJACENT_TO,
        Relation.valid_to == None,  # noqa: E711
    )).all()
    out: list[Entity] = []
    for r in rels:
        if place.id not in (r.src_id, r.dst_id):
            continue
        other = s.get(Entity, r.dst_id if r.src_id == place.id else r.src_id)
        if other is not None:
            out.append(other)
    return out


def resolve_terrain(graph, s: Session, place: Entity) -> tuple[str, bool]:
    """The outdoor country this place stands in, borrowing when it has none.

    Returns ``(terrain, inherited)``. Order: its own attributes/tags, its
    PART_OF ancestors (a tavern is in the town is in the region), its
    ADJACENT_TO neighbours, the nearest coord-ful place within
    ``BIOME_BORROW_RADIUS_MI``, then the climate band — which is defined
    everywhere, so this never returns "".
    """
    own = _biome_of(place)
    if own:
        return own, bool((place.attributes or {}).get("biome_inherited"))

    for parent in _ancestors(s, place):
        b = _biome_of(parent)
        if b:
            return b, True

    for other in _neighbours(s, place):
        b = _biome_of(other)
        if b:
            return b, True

    here = graph._coords_in_db(s, place)
    if here is not None:
        best: tuple[float, str] = (BIOME_BORROW_RADIUS_MI, "")
        for e in s.exec(select(Entity).where(Entity.type == EntityType.PLACE)).all():
            if e.id == place.id:
                continue
            b = _biome_of(e)
            c = geo.coords_from_attrs(e.attributes)
            if not b or c is None:
                continue
            d = geo.distance_mi(here, c)
            if d < best[0]:
                best = (d, b)
        if best[1]:
            return best[1], True

    return _CLIMATE_BIOME.get(geo.climate_for(here), "hills"), True


def _persist_biome(graph, *, name: str, slug: str, status: str,
                   attributes: dict, biome: str) -> None:
    """Write a borrowed biome back, so every later render agrees with this one.

    A biome re-derived per render is a biome that can drift between the arrival
    scene and the battlemap opened in the same room.

    ``status`` is passed through deliberately: ``upsert_entity`` defaults it to
    "active", so writing a stub's biome without it would quietly mark an
    unexplored frontier stub as explored. Best-effort otherwise — a failed
    write costs coherence, never the turn that triggered it.
    """
    try:
        graph.upsert_entity(
            name, EntityType.PLACE, slug=slug, status=status,
            attributes={**(attributes or {}),
                        "biome": biome, "biome_inherited": True},
        )
    except Exception as e:  # noqa: BLE001 — cosmetic bookkeeping
        print(f"[placelore] could not persist biome for '{slug}': {e}")


# ----- the seam ---------------------------------------------------------


def character_of(graph, place_ref, *, persist: bool = True) -> Optional[PlaceCharacter]:
    """Resolve everything the renderers need about a place. None if unknown.

    ``persist`` writes an inherited biome back onto the entity (see module
    docstring). Pass ``persist=False`` for read-only callers such as a probe.
    """
    if place_ref is None:
        return None
    with Session(graph.engine) as s:
        place = graph._resolve_entity(s, place_ref)
        if place is None or place.type != EntityType.PLACE:
            return None

        attrs = place.attributes or {}
        scale = str((attrs.get("scale") or place.subtype or "poi")).lower()
        # The land it stands on, and the surface it presents. A tavern in
        # farmland is terrain=farmland, biome=interior.
        terrain, inherited = resolve_terrain(graph, s, place)
        biome = surface_for(place.subtype, attrs.get("scale")) or terrain
        coords = graph._coords_in_db(s, place)
        climate = geo.climate_for(coords)

        region_name, region_arch = "", ""
        for parent in _ancestors(s, place):
            if str(parent.subtype or "").lower() == "region":
                region_name = parent.name
                region_arch = str((parent.attributes or {}).get("archetype") or "")
                break

        # Named things INSIDE this place — the buildings and landmarks a scene
        # render should show and a map should mark.
        structures: list[str] = []
        child_rels = s.exec(select(Relation).where(
            Relation.rel_type == RelationType.PART_OF,
            Relation.dst_id == place.id,
            Relation.valid_to == None,  # noqa: E711
        )).all()
        for r in child_rels[:12]:
            child = s.get(Entity, r.src_id)
            if child is not None and child.type == EntityType.PLACE \
                    and child.status not in ("archived", "destroyed"):
                structures.append(child.name)

        motifs = [str(m) for m in (attrs.get("motifs") or [])][:3]
        denizens = [str(d) for d in (attrs.get("denizens") or [])][:4]
        description = str(attrs.get("description") or attrs.get("look") or "")
        danger = str(attrs.get("danger") or "")
        unexplored = place.status == "unexplored" or bool(attrs.get("stub"))
        # Everything the write-back needs, read out before the session closes.
        name, slug, status = place.name, place.slug, place.status

    # Only the outdoor terrain is written back: it's a fact about the land, so
    # every place standing on it should agree. The built surface is derived
    # from the place's kind and costs nothing to recompute.
    if inherited and persist and not attrs.get("biome"):
        _persist_biome(graph, name=name, slug=slug, status=status,
                       attributes=attrs, biome=terrain)

    season, time_of_day, weather, precip = _clock_and_weather(graph, climate)

    return PlaceCharacter(
        slug=slug, name=name, scale=scale, biome=biome, terrain=terrain,
        biome_inherited=inherited, climate=climate,
        season=season, time_of_day=time_of_day,
        weather=weather, precipitation=precip,
        danger=danger, description=description,
        motifs=motifs, denizens=denizens, structures=structures,
        region=region_name, region_archetype=region_arch,
        coords=coords, unexplored=unexplored,
    )


def _clock_and_weather(graph, climate: str) -> tuple[str, str, str, str]:
    """(season, time_of_day, weather summary, precipitation) — best effort.

    Weather is derived, not stored, so a missing survival module or an empty
    clock costs the renders their mood and nothing else.
    """
    try:
        from .models import WorldMeta
        from survival.weather import generate_weather, season_for_month
        with Session(graph.engine) as s:
            wm = s.get(WorldMeta, 1)
        # A world whose clock has never ticked has no WorldMeta row at all
        # (``current_day`` reads it but doesn't create it — only
        # ``current_date_str`` does). Fall back to the calendar's own defaults
        # rather than dropping the season, or day-one art comes out seasonless.
        day = wm.world_day if wm else 0
        month = wm.month if wm else 1
        time_of_day = str(wm.time_of_day if wm else "morning")
        w = generate_weather(day, climate=climate, month=month)
        return (season_for_month(month), time_of_day,
                str(w.get("summary") or ""), str(w.get("precipitation") or ""))
    except Exception as e:  # noqa: BLE001 — mood is optional, the turn is not
        print(f"[placelore] weather unavailable: {e}")
        return "", "", "", ""


def biome_at(graph, coords: tuple[float, float], *,
             radius_mi: float = BIOME_BORROW_RADIUS_MI) -> str:
    """The biome of the nearest known place to a point on the globe.

    The world map needs terrain for squares that hold no place at all; this is
    how those squares get coloured from the graph rather than from the model's
    imagination. Falls back to the climate band, which is defined everywhere.
    """
    best: tuple[float, str] = (radius_mi, "")
    with Session(graph.engine) as s:
        for e in s.exec(select(Entity).where(Entity.type == EntityType.PLACE)).all():
            b = _biome_of(e)
            c = geo.coords_from_attrs(e.attributes)
            if not b or c is None:
                continue
            d = geo.distance_mi(coords, c)
            if d < best[0]:
                best = (d, b)
    return best[1] or _CLIMATE_BIOME.get(geo.climate_for(coords), "hills")
