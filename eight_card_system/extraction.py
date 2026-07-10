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

from .graph import WorldGraph, WorldContext
from .models import RelationType

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


# ----- Apply -----

def apply_world_delta(
    graph: WorldGraph,
    delta: WorldDelta,
    *,
    session_id: Optional[str] = None,
) -> dict:
    """Apply a validated delta to the graph. Returns a small summary of what changed."""
    summary = {"entities": 0, "relations_added": 0, "relations_closed": 0, "events": 0, "days": 0}

    for ed in delta.entities:
        if ed.type not in {"place", "npc", "faction", "item", "quest", "event", "pc"}:
            continue
        graph.upsert_entity(
            ed.name, ed.type,
            status=ed.status or "active",
            attributes=ed.attributes,
            tags=ed.tags,
        )
        summary["entities"] += 1

    for rc in delta.relations_close:
        if rc.rel_type not in RelationType.ALL:
            continue
        summary["relations_closed"] += graph.close_relation(rc.src, rc.rel_type, rc.dst)

    for ra in delta.relations_add:
        if ra.rel_type not in RelationType.ALL:
            continue
        # located_in is single-valued: closing the old one keeps history clean.
        if ra.rel_type == RelationType.LOCATED_IN:
            graph.close_relation(ra.src, RelationType.LOCATED_IN)
        rel = graph.add_relation(ra.src, ra.rel_type, ra.dst, attributes=ra.attributes)
        if rel is not None:
            summary["relations_added"] += 1

    for ev in delta.events:
        graph.add_event(
            ev.summary,
            location=ev.location,
            involved=ev.involved or [],
            changes=delta.model_dump(),
            session_id=session_id,
        )
        summary["events"] += 1

    if delta.advance_days and delta.advance_days > 0:
        graph.advance_day(delta.advance_days)
        summary["days"] = delta.advance_days

    return summary


def extract_and_apply(
    graph: WorldGraph,
    call_llm: LLMClient,
    *,
    player_action: str,
    dm_narration: str,
    world_context: WorldContext | str,
    session_id: Optional[str] = None,
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

    summary = apply_world_delta(graph, delta, session_id=session_id)
    return delta, summary
