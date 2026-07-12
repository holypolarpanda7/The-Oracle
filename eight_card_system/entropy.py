"""
Entropy — time works on the world, keyed SOLELY to the canonical clock.

Three mechanisms, cheapest first:

1. **Memory fade (read-time, free)**: an NPC's trust toward a PC decays toward
   indifference as world-days pass since their last interaction. Computed as a
   pure function wherever trust is read; persisted only when trust is next
   adjusted. Long absence ⇒ the NPC "half-remembers" the PC — which also
   handles absent multiplayer players for free.
2. **Demographic pass (on clock ticks)**: census NPCs carry a birth day and a
   rolled lifespan; when enough world time passes they age, die (an event is
   logged), and their post is filled by a successor. Deterministic per slug.
3. **Population drift**: settlement populations random-walk slowly per year.

Main-cast protection: NPCs a player has real history with (|trust| >= 20),
current companions, and quest-involved NPCs are never killed or blanked by
entropy — time touches them narratively, not destructively.
"""
from __future__ import annotations

import random
from typing import Optional

from sqlmodel import Session, select

from .graph import WorldGraph
from .models import Entity, Relation, RelationType, WorldEvent, WorldMeta

DAYS_PER_YEAR = 360  # 12 months x 30 days (Calendar of Harptos view)

# --- memory fade tuning ---
MEMORY_GRACE_DAYS = 30          # no fade inside this window
MEMORY_DECAY_DAYS_PER_POINT = 15  # after grace: 1 trust point fades per N days
MEMORY_FADED_GAP_DAYS = 180     # past this gap, the NPC only half-remembers
MEMORY_DIM_GAP_DAYS = 720       # past this, the PC is a story they once heard

# --- main-cast protection ---
PROTECTED_TRUST = 20            # |trust| at/above this marks "main cast"

# --- demographics ---
ENTROPY_INTERVAL_DAYS = 30      # minimum days between demographic passes
ADULT_AGE_RANGE = (18, 65)      # rolled age for NPCs without a birth day
LIFESPAN_RANGE_YEARS = (68, 95)  # rolled human-ish lifespan
POP_DRIFT_PER_YEAR = 0.03       # settlements drift up to +-3%/year


def decayed_trust(trust: int, last_day: Optional[int], today: int) -> int:
    """Trust drifted toward 0 by elapsed time. Pure function; never widens."""
    if not trust or last_day is None:
        return int(trust or 0)
    gap = max(0, today - int(last_day) - MEMORY_GRACE_DAYS)
    fade = gap // MEMORY_DECAY_DAYS_PER_POINT
    if trust > 0:
        return max(0, trust - fade)
    return min(0, trust + fade)


def memory_state(last_day: Optional[int], today: int) -> str:
    """How well an NPC remembers a PC: fresh | faded | dim."""
    if last_day is None:
        return "fresh"
    gap = today - int(last_day)
    if gap >= MEMORY_DIM_GAP_DAYS:
        return "dim"
    if gap >= MEMORY_FADED_GAP_DAYS:
        return "faded"
    return "fresh"


def _is_protected(s: Session, npc: Entity, pc_ids: set[int]) -> bool:
    """Main cast: companions, trusted/hated acquaintances, quest-involved."""
    rels = s.exec(select(Relation).where(
        Relation.src_id == npc.id,
        Relation.valid_to == None,  # noqa: E711
    )).all()
    for r in rels:
        if r.rel_type == RelationType.TRAVELS_WITH:
            return True
        if r.rel_type == RelationType.KNOWS and r.dst_id in pc_ids:
            if abs(int((r.attributes or {}).get("trust", 0))) >= PROTECTED_TRUST:
                return True
    involving = s.exec(select(Relation).where(
        Relation.rel_type == RelationType.INVOLVES,
        Relation.dst_id == npc.id,
        Relation.valid_to == None,  # noqa: E711
    )).all()
    for r in involving:
        src = s.get(Entity, r.src_id)
        if src is not None and src.type == "quest":
            state = str((src.attributes or {}).get("state", "")).lower()
            if state in ("offered", "active"):
                return True
    return False


def _ensure_vitals(npc: Entity, today: int) -> tuple[int, int]:
    """Roll (and lazily persist via caller) born_day + lifespan_days."""
    attrs = npc.attributes or {}
    rng = random.Random(f"vitals:{npc.slug}")
    born = attrs.get("born_day")
    if born is None:
        age_years = rng.randint(*ADULT_AGE_RANGE)
        # Anchor on creation day so a fresh pass can't insta-kill a new NPC.
        anchor = npc.created_day if npc.created_day else today
        born = anchor - age_years * DAYS_PER_YEAR
    lifespan = attrs.get("lifespan_days")
    if lifespan is None:
        lifespan = rng.randint(*LIFESPAN_RANGE_YEARS) * DAYS_PER_YEAR
    return int(born), int(lifespan)


def build_life_record(graph: WorldGraph, pc_ref) -> Optional[dict]:
    """A dead (or dying) PC's life, gathered from the temporal graph.

    Everything here is canon the graph already holds: the event log, closed
    ``located_in`` history, quest involvements, companions, and earned trust.
    Feeds the memorial the bot posts when a player character dies for good.
    """
    with Session(graph.engine) as s:
        pc = graph._resolve_entity(s, pc_ref)
        if pc is None or pc.type != "pc":
            return None
        today = graph._day(s)

        deeds: list[str] = []
        for ev in s.exec(select(WorldEvent).order_by(
                WorldEvent.world_day)).all():  # type: ignore[attr-defined]
            if pc.id in set(ev.involved or []):
                deeds.append(f"(day {ev.world_day}) {ev.summary}")

        rels = s.exec(select(Relation).where(
            Relation.src_id == pc.id)).all()
        place_ids = [r.dst_id for r in rels
                     if r.rel_type == RelationType.LOCATED_IN]
        places: list[str] = []
        for pid in dict.fromkeys(place_ids):  # ordered de-dupe
            p = s.get(Entity, pid)
            if p is not None:
                places.append(p.name)

        quests: list[str] = []
        for r in s.exec(select(Relation).where(
                Relation.rel_type == RelationType.INVOLVES,
                Relation.dst_id == pc.id)).all():
            q = s.get(Entity, r.src_id)
            if q is not None and q.type == "quest":
                state = str((q.attributes or {}).get("state", "")).lower()
                quests.append(f"{q.name} [{state or 'unresolved'}]")

        companions: list[str] = []
        friends: list[str] = []
        for r in s.exec(select(Relation).where(
                Relation.dst_id == pc.id)).all():
            src = s.get(Entity, r.src_id)
            if src is None:
                continue
            if r.rel_type == RelationType.TRAVELS_WITH:
                companions.append(src.name)
            elif r.rel_type == RelationType.KNOWS:
                if int((r.attributes or {}).get("trust", 0)) >= PROTECTED_TRUST:
                    friends.append(src.name)

        return {
            "name": pc.name,
            "days_adventured": max(0, today - (pc.created_day or 0)),
            "died_day": (pc.attributes or {}).get("died_day", today),
            "date_str": graph.current_date_str(),
            "deeds": deeds[-12:],           # the latest chapters weigh most
            "places": places[:15],
            "quests": quests[:8],
            "companions": list(dict.fromkeys(companions))[:6],
            "friends": list(dict.fromkeys(friends))[:8],
        }


def run_if_due(graph: WorldGraph, *, interval_days: int = ENTROPY_INTERVAL_DAYS) -> dict:
    """Demographic pass, gated so it runs at most once per interval of world
    time. Cheap no-op between intervals. Returns a summary of what happened."""
    out = {"deaths": 0, "successions": 0, "drifted": 0}
    with Session(graph.engine) as s:
        meta = s.get(WorldMeta, 1)
        if meta is None:
            return out
        today = meta.world_day
        if today - meta.last_entropy_day < interval_days:
            return out
        elapsed = today - meta.last_entropy_day if meta.last_entropy_day else interval_days
        meta.last_entropy_day = today
        s.add(meta)

        pc_ids = {e.id for e in s.exec(
            select(Entity).where(Entity.type == "pc")).all()}
        npcs = list(s.exec(select(Entity).where(
            Entity.type == "npc", Entity.status == "active")).all())

        # Collect plain data before commits — commit expires ORM instances.
        deaths: list[tuple[str, str, str]] = []  # (name, slug, role)
        for npc in npcs:
            born, lifespan = _ensure_vitals(npc, today)
            attrs = dict(npc.attributes or {})
            if attrs.get("born_day") is None or attrs.get("lifespan_days") is None:
                attrs["born_day"], attrs["lifespan_days"] = born, lifespan
                npc.attributes = attrs
                s.add(npc)
            if today - born < lifespan:
                continue
            if _is_protected(s, npc, pc_ids):
                continue  # main cast ages, but time never deletes them
            npc.status = "dead"
            npc.attributes = {**attrs, "died_day": today}
            s.add(npc)
            deaths.append((npc.name, npc.slug, str(attrs.get("role") or "")))
        s.commit()

        # Population drift: a slow random walk, deterministic per (slug, era).
        years = max(1, elapsed // DAYS_PER_YEAR) if elapsed >= DAYS_PER_YEAR else 0
        if years:
            for settle in s.exec(select(Entity).where(Entity.type == "place")).all():
                attrs = dict(settle.attributes or {})
                pop = attrs.get("population")
                if not attrs.get("census") or not pop:
                    continue
                rng = random.Random(f"drift:{settle.slug}:{today // DAYS_PER_YEAR}")
                factor = 1.0 + rng.uniform(-POP_DRIFT_PER_YEAR, POP_DRIFT_PER_YEAR) * years
                attrs["population"] = max(10, int(pop * factor))
                settle.attributes = attrs
                s.add(settle)
                out["drifted"] += 1
            s.commit()

    # Deaths get events + successors outside the session (graph API calls).
    from . import census
    for name, slug, role in deaths:
        graph.add_event(
            f"{name}{f', the {role},' if role else ''} passed away of old age.",
            involved=[slug],
        )
        out["deaths"] += 1
        if role:
            dead = graph.get_entity(slug)  # fresh, fully-loaded instance
            if dead is not None and census.spawn_successor(graph, dead):
                out["successions"] += 1
    return out
