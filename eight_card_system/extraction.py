"""
Change extraction — turn a play beat into concrete world-graph mutations.

Per the chosen design, this uses a *separate* (second) LLM call: after the DM
narrates, we ask a cheap, strict model to read (player action + narration +
current world slice) and emit a JSON ``world_delta``. That delta is then applied
to the graph deterministically here.

The LLM client is injected so this module has no hard dependency on the backend's
OpenRouter code. Pass any ``call_llm(messages) -> str`` (the backend's
``call_openrouter_dm`` matches this signature).
"""
from __future__ import annotations

import json
from typing import Callable, Optional

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from . import cartographer, census, geo
from .graph import WorldGraph, WorldContext
from .models import Entity, Relation, RelationType, PlaceScale

LLMClient = Callable[[list[dict]], str]


# ----- Delta schema -----

class EntityDelta(BaseModel):
    name: str
    type: str  # place | npc | faction | item | quest | event | pc
    status: Optional[str] = None
    attributes: Optional[dict] = None
    tags: Optional[list[str]] = None


class RelationAdd(BaseModel):
    src: str
    rel_type: str
    dst: str
    attributes: Optional[dict] = None


class RelationClose(BaseModel):
    src: str
    rel_type: str
    dst: Optional[str] = None


class EventDelta(BaseModel):
    summary: str
    location: Optional[str] = None
    involved: Optional[list[str]] = None


class WorldDelta(BaseModel):
    entities: list[EntityDelta] = Field(default_factory=list)
    relations_add: list[RelationAdd] = Field(default_factory=list)
    relations_close: list[RelationClose] = Field(default_factory=list)
    events: list[EventDelta] = Field(default_factory=list)
    advance_days: int = 0


# ----- Prompt -----

_EXTRACTOR_SYSTEM = (
    "You are a world-state extractor for a text RPG. You NEVER narrate. You read the "
    "player's action and the DM's narration, then output ONLY the concrete, durable "
    "changes to world state as strict JSON.\n\n"
    "Rules:\n"
    "- Record only things that actually, canonically happened — not possibilities, "
    "intentions, or flavor.\n"
    "- Use existing entity names exactly when referring to known entities.\n"
    "- Introduce a new entity (in `entities`) before referencing it in a relation.\n"
    "- Movement: when a character goes somewhere, add a `located_in` relation to the "
    "new place (the graph closes the old one automatically).\n"
    "- Deaths/destruction: set the entity `status` to 'dead' or 'destroyed'.\n"
    "- Use `advance_days` only when significant in-world time passes (travel, rest, "
    "time-skips); otherwise 0.\n"
    "- Allowed entity types: place, npc, faction, item, quest, event, pc.\n"
    f"- Allowed relation types: {', '.join(sorted(RelationType.ALL))}.\n\n"
    "World laws for NEW place entities (a deterministic checker enforces these — "
    "delta that breaks them gets corrected or dropped, so follow them exactly):\n"
    "- Every new place MUST include `attributes.scale`, one of: region, settlement, "
    "district, building, room, wilds, dungeon, poi. For a settlement, say the precise "
    "size instead of the generic word: city, town, or village.\n"
    "- Every new place MUST be connected in the SAME delta via a `part_of`, "
    "`adjacent_to`, or `located_at` relation to a place that already exists or is "
    "also being created in this delta. An unconnected new place is dropped entirely.\n"
    "- Never invent a new settlement (city/town/village) unless the world slice's "
    "'Beyond the map' section names a frontier stub there whose scale ceiling allows "
    "it. Prefer small, poi-scale inventions (a shrine, a camp, a ruin) over new "
    "settlements — settlement budgets per region are limited and an over-budget "
    "settlement is downgraded to a poi automatically.\n"
    "- When the narration says where a new place lies, include `attributes.direction` "
    "(8-way compass, relative to the place it connects to) and "
    "`attributes.distance_miles` (a number). The world map places it exactly there; "
    "omitted values are filled with sensible defaults.\n"
    "- When an NPC meaningfully teaches a player about a place (directions, a "
    "described route, local geography), record it: add a `knows_about` relation "
    "from the pc to that place. This is what lets them draw it on a map later.\n\n"
    "Output JSON with this shape (omit empty arrays is fine):\n"
    "{\n"
    '  "entities": [{"name": "...", "type": "npc", "status": "active", "attributes": {"description": "..."}, "tags": ["..."]}],\n'
    '  "relations_add": [{"src": "...", "rel_type": "located_in", "dst": "..."}],\n'
    '  "relations_close": [{"src": "...", "rel_type": "located_in", "dst": "..."}],\n'
    '  "events": [{"summary": "...", "location": "...", "involved": ["..."]}],\n'
    '  "advance_days": 0\n'
    "}\n"
    "Output ONLY the JSON object. No prose, no code fences."
)


def _build_messages(player_action: str, dm_narration: str, world_context_text: str) -> list[dict]:
    user = (
        "CURRENT WORLD SLICE:\n"
        f"{world_context_text}\n\n"
        "PLAYER ACTION:\n"
        f"{player_action}\n\n"
        "DM NARRATION:\n"
        f"{dm_narration}\n\n"
        "Extract the world_delta JSON now."
    )
    return [
        {"role": "system", "content": _EXTRACTOR_SYSTEM},
        {"role": "user", "content": user},
    ]


def _parse_json_object(raw: str) -> dict:
    """Best-effort extraction of the first JSON object from model output."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        # drop a leading language tag like "json\n"
        if "\n" in text:
            text = text.split("\n", 1)[1]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in extractor output")
    return json.loads(text[start:end + 1])


# ----- World laws (deterministic validation grammar) -----

# Precise settlement-size words the LLM may use in attributes["scale"], all of
# which are settlement-like for budget/nesting purposes.
_SETTLEMENT_WORDS = {"settlement", "city", "town", "village"}
# Everything else accepted in attributes["scale"] (aliases resolve to these).
_VALID_SCALES = set(PlaceScale.ALL) | _SETTLEMENT_WORDS
_BUILDING_LIKE = {"building", "room"}
_DEFAULT_SETTLEMENT_BUDGET = {"town": 1, "village": 2}


def _budget_bucket(scale_word: str) -> str:
    """Map a precise settlement-size word onto a settlement_budget key. City counts as town."""
    return "village" if scale_word == "village" else "town"


def _subtype_for_scale(scale_word: str) -> str:
    """Map a validated ``attributes.scale`` word onto the Entity.subtype to store."""
    word = (scale_word or "").strip().lower()
    if word in _SETTLEMENT_WORDS:
        return PlaceScale.SETTLEMENT
    if word in PlaceScale.ALL:
        return word
    return PlaceScale.POI


def validate_world_delta(
    graph: WorldGraph,
    delta: WorldDelta,
    context_entity_ids: Optional[set[int]] = None,
) -> tuple[WorldDelta, list[str], dict]:
    """Enforce deterministic "world laws" on a raw extractor delta.

    Returns ``(normalized_delta, notes, resolution)``. ``resolution`` maps
    each referenced name (lowercased) to the SLUG of the concrete identity it
    resolves to, or None when the name is new. Never raises — this only
    trims/relabels the delta, it never touches the DB.

    Identity scoping (names are labels, slugs are identities): a name resolves
    to the entity IN THE CURRENT CONTEXT SLICE first; a unique global match is
    accepted as a fallback; an ambiguous name with no context match resolves
    to nothing — the delta then creates a NEW identity rather than mutating a
    same-named stranger half a world away.
    """
    notes: list[str] = []
    ctx_ids = context_entity_ids or set()

    entities = [ed.model_copy(deep=True) for ed in delta.entities]
    relations_add = [ra.model_copy(deep=True) for ra in delta.relations_add]
    relations_close = list(delta.relations_close)
    events = list(delta.events)

    delta_place_names = {ed.name.strip().lower() for ed in entities if ed.type == "place"}
    existing_cache: dict[str, Optional[Entity]] = {}

    def resolve_existing(name: str) -> Optional[Entity]:
        key = name.strip().lower()
        if key not in existing_cache:
            candidates = graph.find_entities_by_name(name)
            chosen: Optional[Entity] = None
            in_ctx = [c for c in candidates if c.id in ctx_ids]
            if in_ctx:
                chosen = in_ctx[0]
            elif len(candidates) == 1:
                chosen = candidates[0]
            elif len(candidates) > 1:
                notes.append(
                    f"'{name}' is ambiguous ({len(candidates)} entities wear that "
                    "name) and none are in the current scene — treating as new")
            existing_cache[key] = chosen
        return existing_cache[key]

    def is_new_place(ed: EntityDelta) -> bool:
        return ed.type == "place" and resolve_existing(ed.name) is None

    # --- (a0) EXISTING places keep their scale: narration may redecorate a
    # town, but it can never shrink one into a village (or grow a shrine into
    # a city) by careless wording — geography scale is validator-owned. ---
    for ed in entities:
        if ed.type != "place":
            continue
        existing = resolve_existing(ed.name)
        if existing is None:
            continue
        cur_scale = str((existing.attributes or {}).get("scale")
                        or existing.subtype or "").strip().lower()
        new_scale = str((ed.attributes or {}).get("scale", "")).strip().lower()
        if cur_scale and new_scale and new_scale != cur_scale:
            attrs = dict(ed.attributes or {})
            attrs.pop("scale", None)
            ed.attributes = attrs
            notes.append(f"'{ed.name}': scale is fixed at '{cur_scale}' — "
                         f"narrated '{new_scale}' ignored")

    # --- (a) every NEW place must carry a recognized scale; default to poi ---
    for ed in entities:
        if not is_new_place(ed):
            continue
        attrs = dict(ed.attributes or {})
        raw = str(attrs.get("scale", "")).strip().lower()
        if raw not in _VALID_SCALES:
            notes.append(
                f"'{ed.name}': "
                + (f"unrecognized scale '{raw}'" if raw else "missing scale")
                + " — defaulted to poi"
            )
            raw = PlaceScale.POI
        attrs["scale"] = raw
        ed.attributes = attrs

    # --- (b) every NEW place must be spatially connected within this delta ---
    def other_end_of(name_key: str, ra: RelationAdd) -> Optional[str]:
        src_key, dst_key = ra.src.strip().lower(), ra.dst.strip().lower()
        if name_key == src_key:
            return dst_key
        if name_key == dst_key:
            return src_key
        return None

    def is_connected(ed: EntityDelta) -> bool:
        key = ed.name.strip().lower()
        for ra in relations_add:
            if ra.rel_type not in (RelationType.PART_OF, RelationType.ADJACENT_TO, RelationType.LOCATED_AT):
                continue
            other = other_end_of(key, ra)
            if other is None or other == key:
                continue
            if resolve_existing(other) is not None or other in delta_place_names:
                return True
        return False

    dropped: set[str] = set()
    kept_entities: list[EntityDelta] = []
    for ed in entities:
        if is_new_place(ed) and not is_connected(ed):
            dropped.add(ed.name.strip().lower())
            notes.append(f"dropped '{ed.name}': new place not spatially connected (no part_of/adjacent_to/located_at in this delta)")
            continue
        kept_entities.append(ed)
    entities = kept_entities
    delta_place_names -= dropped

    if dropped:
        def touches_dropped(*names: Optional[str]) -> bool:
            return any(n and n.strip().lower() in dropped for n in names)

        kept_ra: list[RelationAdd] = []
        for ra in relations_add:
            if touches_dropped(ra.src, ra.dst):
                notes.append(f"dropped relation {ra.rel_type}({ra.src} -> {ra.dst}): references a dropped entity")
                continue
            kept_ra.append(ra)
        relations_add = kept_ra

        kept_rc: list[RelationClose] = []
        for rc in relations_close:
            if touches_dropped(rc.src, rc.dst):
                notes.append(f"dropped relation-close {rc.rel_type}({rc.src}): references a dropped entity")
                continue
            kept_rc.append(rc)
        relations_close = kept_rc

        kept_ev: list[EventDelta] = []
        for ev in events:
            if touches_dropped(ev.location, *(ev.involved or [])):
                notes.append(f"dropped event referencing a dropped entity: {ev.summary[:60]!r}")
                continue
            kept_ev.append(ev)
        events = kept_ev

    # --- (c) settlement budget per region ---
    def part_of_target_in_delta(name: str) -> Optional[str]:
        key = name.strip().lower()
        for ra in relations_add:
            if ra.rel_type == RelationType.PART_OF and ra.src.strip().lower() == key:
                return ra.dst
        return None

    def existing_part_of_parent(entity_id: int) -> Optional[Entity]:
        with Session(graph.engine) as s:
            rel = s.exec(
                select(Relation).where(
                    Relation.src_id == entity_id,
                    Relation.rel_type == RelationType.PART_OF,
                    Relation.valid_to == None,  # noqa: E711
                )
            ).first()
            return s.get(Entity, rel.dst_id) if rel else None

    def find_region(start_name: str) -> Optional[Entity]:
        current = start_name
        seen: set[str] = set()
        for _ in range(10):
            key = current.strip().lower()
            if key in seen:
                return None
            seen.add(key)
            existing = resolve_existing(current)
            if existing is not None:
                if existing.subtype == PlaceScale.REGION:
                    return existing
                parent = existing_part_of_parent(existing.id)
                if parent is None:
                    return None
                current = parent.name
                continue
            target = part_of_target_in_delta(current)
            if target is None:
                return None
            current = target
        return None

    def count_settlements_in_region(region: Entity) -> dict[str, int]:
        counts = {"town": 0, "village": 0}
        with Session(graph.engine) as s:
            settlements = s.exec(
                select(Entity).where(Entity.subtype == PlaceScale.SETTLEMENT)
            ).all()
            for settlement in settlements:
                direct = s.exec(
                    select(Relation).where(
                        Relation.src_id == settlement.id,
                        Relation.rel_type == RelationType.PART_OF,
                        Relation.dst_id == region.id,
                        Relation.valid_to == None,  # noqa: E711
                    )
                ).first()
                reached = direct is not None
                if not reached:
                    mid_rels = s.exec(
                        select(Relation).where(
                            Relation.src_id == settlement.id,
                            Relation.rel_type == RelationType.PART_OF,
                            Relation.valid_to == None,  # noqa: E711
                        )
                    ).all()
                    for mr in mid_rels:
                        hop2 = s.exec(
                            select(Relation).where(
                                Relation.src_id == mr.dst_id,
                                Relation.rel_type == RelationType.PART_OF,
                                Relation.dst_id == region.id,
                                Relation.valid_to == None,  # noqa: E711
                            )
                        ).first()
                        if hop2:
                            reached = True
                            break
                if reached:
                    word = str((settlement.attributes or {}).get("scale", "town")).strip().lower()
                    counts[_budget_bucket(word)] += 1
        return counts

    # --- (e) place every new place on the globe (BEFORE the budget check, so
    # a settlement's distance from its region can exempt it as frontier) ---
    def connection_targets(name: str) -> list[str]:
        key = name.strip().lower()
        out: list[str] = []
        for ra in relations_add:
            if ra.rel_type in (RelationType.PART_OF, RelationType.ADJACENT_TO,
                               RelationType.LOCATED_AT):
                other = other_end_of(key, ra)
                if other and other != key:
                    out.append(other)
        return out

    def coords_of_existing(ent: Entity) -> Optional[tuple[float, float]]:
        c = geo.coords_from_attrs(ent.attributes)
        current = ent
        for _ in range(4):
            if c is not None:
                return c
            current = existing_part_of_parent(current.id) if current.id else None
            if current is None:
                return None
            c = geo.coords_from_attrs(current.attributes)
        return c

    def coords_of_name(name: str) -> Optional[tuple[float, float]]:
        existing = resolve_existing(name)
        if existing is not None:
            return coords_of_existing(existing)
        key = name.strip().lower()
        for ed in entities:
            if ed.type == "place" and ed.name.strip().lower() == key:
                return geo.coords_from_attrs(ed.attributes or {})
        return None

    def existing_settlement_coords() -> list[tuple[float, float]]:
        with Session(graph.engine) as s:
            rows = s.exec(
                select(Entity).where(Entity.subtype == PlaceScale.SETTLEMENT)
            ).all()
        return [c for r in rows if (c := geo.coords_from_attrs(r.attributes)) is not None]

    settlement_positions = None  # lazy: only query when a new settlement appears
    # Two passes so a place anchored to another new place resolves once the
    # anchor has been positioned.
    for _pass in range(2):
        for ed in entities:
            if not is_new_place(ed):
                continue
            attrs = dict(ed.attributes or {})
            if geo.coords_from_attrs(attrs) is not None:
                continue
            base = next(
                (c for t in connection_targets(ed.name) if (c := coords_of_name(t))),
                None,
            )
            if base is None:
                continue
            scale_word = str(attrs.get("scale", PlaceScale.POI))
            direction = str(attrs.get("direction", "")).strip().lower()
            if direction not in ("north", "northeast", "east", "southeast",
                                 "south", "southwest", "west", "northwest"):
                direction = geo.hashed_direction(ed.name)
            try:
                miles = float(attrs.get("distance_miles"))
            except (TypeError, ValueError):
                miles = geo.DEFAULT_SCALE_DISTANCE_MI.get(scale_word, 3.0)
            coords = geo.offset_coords(base, direction, miles)

            if scale_word in _SETTLEMENT_WORDS:
                if settlement_positions is None:
                    settlement_positions = existing_settlement_coords()
                too_close = next(
                    (p for p in settlement_positions
                     if geo.distance_mi(coords, p) < geo.MIN_SETTLEMENT_SPACING_MI), None)
                if too_close is not None:
                    notes.append(
                        f"'{ed.name}': a new {scale_word} within "
                        f"{geo.MIN_SETTLEMENT_SPACING_MI:g} mi of an existing settlement "
                        "— downgraded to poi (it reads as part of that settlement's orbit)"
                    )
                    attrs["scale"] = PlaceScale.POI
                else:
                    settlement_positions.append(coords)

            attrs["coords"] = geo.coords_attr(*coords)
            attrs["climate"] = geo.climate_for(coords)
            ed.attributes = attrs
            notes.append(
                f"'{ed.name}': placed {direction} of its anchor at ~{miles:g} mi "
                f"({attrs['climate']})"
            )

    # Settlements approved earlier in this same delta count against the budget
    # too — otherwise one delta could found three villages that each "see" zero.
    approved_in_delta: dict[tuple[int, str], int] = {}
    for ed in entities:
        if not is_new_place(ed):
            continue
        attrs = dict(ed.attributes or {})
        scale_word = attrs.get("scale", PlaceScale.POI)
        if scale_word not in _SETTLEMENT_WORDS:
            continue
        bucket = _budget_bucket(scale_word)
        region = find_region(ed.name)
        if region is None:
            # No part_of chain of its own — inherit the region of whatever it
            # is adjacent to / located at, so adjacency can't dodge the budget.
            key = ed.name.strip().lower()
            for ra in relations_add:
                if ra.rel_type in (RelationType.ADJACENT_TO, RelationType.LOCATED_AT):
                    other = other_end_of(key, ra)
                    if other:
                        region = find_region(other)
                        if region is not None:
                            break
        if region is None:
            notes.append(f"'{ed.name}': could not resolve a region for the settlement budget check — allowed")
            continue
        # Far-frontier exemption: a settlement founded beyond the region's
        # reach belongs to lands not yet charted. The cartographer founds a
        # fresh region out there; the old region's budget doesn't bind it.
        region_c = geo.coords_from_attrs(region.attributes)
        place_c = geo.coords_from_attrs(attrs)
        if region_c and place_c and \
                geo.distance_mi(region_c, place_c) > cartographer.REGION_RADIUS_MI:
            notes.append(
                f"'{ed.name}': beyond {region.name}'s reach "
                f"(> {cartographer.REGION_RADIUS_MI:g} mi) — frontier settlement, budget waived"
            )
            continue
        budget = (region.attributes or {}).get("settlement_budget") or _DEFAULT_SETTLEMENT_BUDGET
        allowed = int(budget.get(bucket, _DEFAULT_SETTLEMENT_BUDGET.get(bucket, 0)))
        existing_count = (count_settlements_in_region(region).get(bucket, 0)
                          + approved_in_delta.get((region.id, bucket), 0))
        if existing_count >= allowed:
            notes.append(
                f"'{ed.name}': {bucket} budget for region '{region.name}' exhausted "
                f"({existing_count}/{allowed}) — downgraded to poi"
            )
            attrs["scale"] = PlaceScale.POI
            desc = str(attrs.get("description", "")).strip()
            prefix = "A small outlying homestead/waystation"
            attrs["description"] = f"{prefix} — {desc}" if desc else f"{prefix}."
            ed.attributes = attrs
        else:
            approved_in_delta[(region.id, bucket)] = (
                approved_in_delta.get((region.id, bucket), 0) + 1)

    # --- (d) scale-nesting sanity on part_of relations ---
    def effective_scale(name: str) -> str:
        key = name.strip().lower()
        existing = resolve_existing(name)
        if existing is not None:
            return str((existing.attributes or {}).get("scale") or existing.subtype or "").strip().lower()
        for ed in entities:
            if ed.type == "place" and ed.name.strip().lower() == key:
                return str((ed.attributes or {}).get("scale", "")).strip().lower()
        return ""

    kept_ra2: list[RelationAdd] = []
    for ra in relations_add:
        if ra.rel_type == RelationType.PART_OF:
            src_scale = effective_scale(ra.src)
            dst_scale = effective_scale(ra.dst)
            if src_scale in _BUILDING_LIKE and dst_scale == PlaceScale.REGION:
                notes.append(
                    f"dropped part_of({ra.src} -> {ra.dst}): a {src_scale} cannot be part_of a "
                    "region directly — needs an intermediate settlement/district"
                )
                continue
            if src_scale in _SETTLEMENT_WORDS and dst_scale in _SETTLEMENT_WORDS:
                converted = False
                for ed in entities:
                    if ed.type == "place" and ed.name.strip().lower() == ra.src.strip().lower() \
                            and is_new_place(ed):
                        attrs = dict(ed.attributes or {})
                        attrs["scale"] = PlaceScale.DISTRICT
                        ed.attributes = attrs
                        converted = True
                        break
                if converted:
                    notes.append(
                        f"'{ra.src}': a settlement cannot be part_of another settlement "
                        f"('{ra.dst}') — converted to a district"
                    )
                else:
                    notes.append(
                        f"dropped part_of({ra.src} -> {ra.dst}): an existing settlement cannot be "
                        "nested inside another settlement"
                    )
                    continue
        kept_ra2.append(ra)
    relations_add = kept_ra2

    # Resolution map for the applier: every referenced name -> concrete slug
    # (or None = mint a new identity).
    all_names: set[str] = {ed.name for ed in entities}
    for ra in relations_add:
        all_names.update((ra.src, ra.dst))
    for rc in relations_close:
        all_names.add(rc.src)
        if rc.dst:
            all_names.add(rc.dst)
    for ev in events:
        if ev.location:
            all_names.add(ev.location)
        all_names.update(ev.involved or [])
    resolution: dict[str, Optional[str]] = {}
    for nm in all_names:
        if not nm:
            continue
        ent = resolve_existing(nm)
        resolution[nm.strip().lower()] = ent.slug if ent is not None else None

    normalized = delta.model_copy(update={
        "entities": entities,
        "relations_add": relations_add,
        "relations_close": relations_close,
        "events": events,
    })
    return normalized, notes, resolution


# ----- Apply -----

def apply_world_delta(
    graph: WorldGraph,
    delta: WorldDelta,
    *,
    session_id: Optional[str] = None,
    defer_clock: bool = False,
    context_entity_ids: Optional[set[int]] = None,
) -> dict:
    """Validate + apply a delta to the graph. Returns a small summary of what changed.

    ``defer_clock=True`` (multiplayer bubble mode): ``advance_days`` is
    reported in the summary but NOT applied to the global clock — the caller
    owns session-bubble time and ratchets the world via
    ``WorldGraph.ratchet_day`` (parallel bubbles take the max, never the sum).

    ``context_entity_ids`` scopes name->identity resolution to the current
    scene, so same-named strangers elsewhere in the world are never mutated.
    """
    delta, notes, resolution = validate_world_delta(
        graph, delta, context_entity_ids=context_entity_ids)
    for note in notes:
        print(f"[world-laws] {note}")

    summary = {"entities": 0, "relations_added": 0, "relations_closed": 0, "events": 0, "days": 0}
    summary["world_law_notes"] = notes

    def ref(name: Optional[str]) -> Optional[str]:
        """Translate a delta name to its resolved identity slug (or the raw name)."""
        if not name:
            return name
        return resolution.get(name.strip().lower()) or name

    for ed in delta.entities:
        if ed.type not in {"place", "npc", "faction", "item", "quest", "event", "pc"}:
            continue
        subtype = None
        if ed.type == "place":
            attrs = ed.attributes or {}
            if "scale" in attrs:
                subtype = _subtype_for_scale(attrs.get("scale"))
        key = ed.name.strip().lower()
        target_slug = resolution.get(key)
        if target_slug:
            # Known identity in scope: update it.
            graph.upsert_entity(
                ed.name, ed.type, slug=target_slug,
                subtype=subtype,
                status=ed.status or "active",
                attributes=ed.attributes,
                tags=ed.tags,
            )
        elif ed.type == "pc":
            # PCs are only ever minted by place_pc — never by narration.
            print(f"[world-laws] skipped unknown pc '{ed.name}' (PCs aren't created by extraction)")
            continue
        else:
            # New identity: unique slug, even if the name is already taken.
            ent = graph.create_entity(
                ed.name, ed.type,
                subtype=subtype,
                status=ed.status or "active",
                attributes=ed.attributes,
                tags=ed.tags,
            )
            resolution[key] = ent.slug
        summary["entities"] += 1

    for rc in delta.relations_close:
        if rc.rel_type not in RelationType.ALL:
            continue
        summary["relations_closed"] += graph.close_relation(
            ref(rc.src), rc.rel_type, ref(rc.dst) if rc.dst else None)

    # Places that should have fresh frontier rolled beyond them after this
    # delta lands: explored stubs and wherever a PC just moved.
    frontier_targets: list[str] = []

    for ra in delta.relations_add:
        if ra.rel_type not in RelationType.ALL:
            continue
        src_ref, dst_ref = ref(ra.src), ref(ra.dst)
        # located_in is single-valued: closing the old one keeps history clean.
        if ra.rel_type == RelationType.LOCATED_IN:
            graph.close_relation(src_ref, RelationType.LOCATED_IN)
        rel = graph.add_relation(src_ref, ra.rel_type, dst_ref, attributes=ra.attributes)
        if rel is not None:
            summary["relations_added"] += 1
            # (f) Stepping a PC into an unexplored frontier stub "detail"s it by play.
            if ra.rel_type == RelationType.LOCATED_IN:
                src_ent = graph.get_entity(src_ref)
                dst_ent = graph.get_entity(dst_ref)
                if src_ent and src_ent.type == "pc" and dst_ent:
                    if dst_ent.status == "unexplored":
                        graph.upsert_entity(
                            dst_ent.name, dst_ent.type, slug=dst_ent.slug,
                            status="active",
                            attributes={**(dst_ent.attributes or {}), "stub": False},
                        )
                        print(f"[world-laws] '{dst_ent.name}' explored by {src_ent.name} — status set to active")
                    if dst_ent.slug not in frontier_targets:
                        frontier_targets.append(dst_ent.slug)

    # The cartographer keeps a constrained frontier ahead of the party and
    # founds new regions when they range far — the mechanism that lets whole
    # kingdoms accrete from play (see cartographer.py).
    for slug in frontier_targets:
        try:
            for line in cartographer.ensure_frontier_around(graph, slug):
                print(f"[cartographer] {line}")
        except Exception as e:  # noqa: BLE001 — mapkeeping must never break play
            print(f"[cartographer] frontier pass failed at '{slug}': {e}")
        # The census fleshes wherever the party arrives: settlements get their
        # population/wards/notables on first visit, city wards on first entry.
        try:
            settlement = census.settlement_of(graph, slug)
            if settlement is not None:
                for line in census.flesh_settlement(graph, settlement.slug):
                    print(f"[census] {line}")
            target = graph.get_entity(slug)
            if target is not None and (target.attributes or {}).get("ward_type"):
                for line in census.ensure_ward_anchors(graph, target.slug):
                    print(f"[census] {line}")
        except Exception as e:  # noqa: BLE001
            print(f"[census] fleshing failed at '{slug}': {e}")

    for ev in delta.events:
        graph.add_event(
            ev.summary,
            location=ref(ev.location) if ev.location else None,
            involved=[ref(n) for n in (ev.involved or []) if n],
            changes=delta.model_dump(),
            session_id=session_id,
        )
        summary["events"] += 1

    if delta.advance_days and delta.advance_days > 0:
        summary["days"] = delta.advance_days
        if not defer_clock:
            graph.advance_day(delta.advance_days)

    return summary


def extract_and_apply(
    graph: WorldGraph,
    call_llm: LLMClient,
    *,
    player_action: str,
    dm_narration: str,
    world_context: WorldContext | str,
    session_id: Optional[str] = None,
    defer_clock: bool = False,
    context_entity_ids: Optional[set[int]] = None,
) -> tuple[Optional[WorldDelta], dict]:
    """Run the extractor LLM call and apply the resulting delta.

    Returns ``(delta, summary)``. On any failure, returns ``(None, {...error})``
    so a bad extraction never breaks the play loop.
    """
    ctx_text = world_context.render() if isinstance(world_context, WorldContext) else str(world_context)
    messages = _build_messages(player_action, dm_narration, ctx_text)
    try:
        raw = call_llm(messages)
        data = _parse_json_object(raw)
        delta = WorldDelta.model_validate(data)
    except Exception as exc:  # noqa: BLE001 — never break the game loop on extraction
        return None, {"error": f"{type(exc).__name__}: {exc}"}

    summary = apply_world_delta(graph, delta, session_id=session_id,
                                defer_clock=defer_clock,
                                context_entity_ids=context_entity_ids)
    return delta, summary
