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
    out = {"deaths": 0, "successions": 0, "drifted": 0, "festered": 0}
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

    # Threat festering + spread (same clock cadence). Events tie to the place so
    # they surface near a party passing through.
    for name, slug, new_danger, cause in _fester_threats(graph, today):
        graph.add_event(f"{name} grows more dangerous ({new_danger}) — {cause}.",
                        location=slug)
        out["festered"] += 1
    return out


# --- quest stakes clocks: living-time pressure from (in)activity -------------
# The party's engagement is the world's lever. A quest earns a clock ONLY when
# the DM gives it `stakes` (opt-in pressure — freedom preserved for open-ended
# threads). Touching a quest via any [[QUEST]] hook resets its neglect window
# (the ACTIVITY half, handled in the backend); this pass is the INACTIVITY half:
# neglected stakes escalate over world-days and, unheeded, the quest slips away —
# and its failure can leave the world materially worse (its place grows more
# dangerous). Quest success ripples the other way (handled on resolve).
STAKES_PERIOD_DAYS = 7        # world-days of neglect between escalations
STAKES_FAIL_LEVEL = 3         # escalations before an ignored quest slips beyond reach
_DANGER_LADDER = ["low", "moderate", "high", "deadly"]

# Threat festering: a place the party VISITED and then left unattended grows more
# dangerous as its denizens go unchecked — up to a cap (neglect alone never breeds
# a "deadly" tier; that's for authored/quest fallout).
FESTER_NEGLECT_DAYS = 60
FESTER_DANGER_CAP = "high"


def claim_peril_npc(graph: WorldGraph, quest_name: str, npc_ref,
                    *, session_id: Optional[str] = None) -> Optional[str]:
    """Kill an NPC imperiled by a FAILED quest — inaction has a cost. Skips the PC
    and any active party companion (they're never culled silently). Returns the
    NPC's name if claimed, else None."""
    npc = graph.get_entity(npc_ref)
    if npc is None or npc.type != "npc" or npc.status != "active":
        return None
    with Session(graph.engine) as s:
        companion = s.exec(select(Relation).where(
            Relation.rel_type == RelationType.TRAVELS_WITH,
            Relation.src_id == npc.id, Relation.valid_to == None)).first()  # noqa: E711
        if companion is not None:
            return None
        meta = s.get(WorldMeta, 1)
        today = meta.world_day if meta else 0
    graph.upsert_entity(npc.name, npc.type, slug=npc.slug, status="dead",
                        attributes={"died_day": today,
                                    "died_of": f"inaction ({quest_name})"})
    graph.add_event(f"{npc.name} was lost — no one came in time ({quest_name}).",
                    involved=[npc.slug], session_id=session_id)
    return npc.name


def shift_place_danger(graph: WorldGraph, slug: str, step: int, reason: str,
                       *, session_id: Optional[str] = None) -> Optional[str]:
    """Nudge a place's danger up/down one rung and log why. Returns the new rung
    (or None if the place has no danger rating to move). This is how party deeds
    leave a lasting mark: clearing a threat makes an area safer, failure worse."""
    place = graph.get_entity(slug)
    if place is None:
        return None
    attrs = dict(place.attributes or {})
    cur = str(attrs.get("danger", "")).lower()
    if cur not in _DANGER_LADDER:
        return None
    i = _DANGER_LADDER.index(cur)
    new_i = max(0, min(len(_DANGER_LADDER) - 1, i + step))
    if new_i == i:
        return cur
    new = _DANGER_LADDER[new_i]
    graph.upsert_entity(place.name, place.type, slug=place.slug,
                        status=place.status, attributes={"danger": new})
    verb = "grows more dangerous" if step > 0 else "grows safer"
    graph.add_event(f"{place.name} {verb} ({new}) — {reason}.",
                    location=place.slug, session_id=session_id)
    return new


def _place_is_held(attrs: dict, today: int) -> bool:
    """Civilization and party presence resist festering/spread — the counterweight
    that keeps the world from devolving. A settlement (has population/scale) or a
    recently-visited place holds the line."""
    if attrs.get("population") or str(attrs.get("scale", "")).lower() in (
            "town", "city", "village", "settlement"):
        return True
    lv = attrs.get("last_visited_day")
    return lv is not None and today - int(lv) < FESTER_NEGLECT_DAYS


def _fester_threats(graph: WorldGraph, today: int) -> list[tuple]:
    """Neglected dangerous places worsen; a high threat can bleed one rung into an
    eligible neighbor. Bounded so it converges, not explodes: capped at
    ``FESTER_DANGER_CAP``, settlements/visited places resist, only high+ sources
    spread, and it's probabilistic per era. Returns (name, slug, new_danger, cause)."""
    cap_i = _DANGER_LADDER.index(FESTER_DANGER_CAP)
    era = today // ENTROPY_INTERVAL_DAYS
    changes: list[tuple] = []

    def danger_i(p) -> int:
        d = str((p.attributes or {}).get("danger", "")).lower()
        return _DANGER_LADDER.index(d) if d in _DANGER_LADDER else -1

    with Session(graph.engine) as s:
        places = list(s.exec(select(Entity).where(
            Entity.type == "place", Entity.status == "active")).all())
        by_id = {p.id: p for p in places}
        adj: dict[int, set] = {}
        for r in s.exec(select(Relation).where(
                Relation.rel_type == RelationType.ADJACENT_TO,
                Relation.valid_to == None)).all():  # noqa: E711
            adj.setdefault(r.src_id, set()).add(r.dst_id)
            adj.setdefault(r.dst_id, set()).add(r.src_id)

        # 1) self-festering: a VISITED-then-neglected dangerous+denizen place worsens.
        for p in places:
            a = p.attributes or {}
            di = danger_i(p)
            if di < 1 or di >= cap_i or not a.get("denizens"):
                continue
            lv = a.get("last_visited_day")
            if lv is None or today - int(lv) < FESTER_NEGLECT_DAYS:
                continue  # never seen it, or presence still holds it
            if random.Random(f"fester:{p.slug}:{era}").random() > 0.6:
                continue
            attrs = dict(a); attrs["danger"] = _DANGER_LADDER[di + 1]
            p.attributes = attrs; s.add(p)
            changes.append((p.name, p.slug, _DANGER_LADDER[di + 1],
                            "unchecked, its denizens grow bolder"))
        s.commit()

        # 2) spread: a high+ place bleeds one rung into an eligible neighbor.
        for p in places:
            if danger_i(p) < _DANGER_LADDER.index("high"):
                continue
            if random.Random(f"spread:{p.slug}:{era}").random() > 0.4:
                continue
            cands = []
            for nid in adj.get(p.id, ()):
                n = by_id.get(nid)
                if n is None:
                    continue
                ndi = danger_i(n)
                if 0 <= ndi < min(cap_i, danger_i(p)) and not _place_is_held(
                        n.attributes or {}, today):
                    cands.append(n)
            if not cands:
                continue
            n = random.Random(f"spread:{p.slug}:{era}").choice(
                sorted(cands, key=lambda x: x.slug))
            ndi = danger_i(n)
            attrs = dict(n.attributes or {}); attrs["danger"] = _DANGER_LADDER[ndi + 1]
            n.attributes = attrs; s.add(n)
            changes.append((n.name, n.slug, _DANGER_LADDER[ndi + 1],
                            f"the trouble in {p.name} is spilling over"))
        s.commit()
    return changes


def advance_quest_clocks(graph: WorldGraph, *, session_id: Optional[str] = None,
                         period_days: int = STAKES_PERIOD_DAYS,
                         fail_level: int = STAKES_FAIL_LEVEL) -> dict:
    """Escalate ACTIVE quests the party has neglected; a long-ignored quest fails
    and its fallout worsens its place. Cheap: only quests carrying `stakes` have a
    clock, and each is gated by its own neglect window."""
    out = {"escalated": 0, "failed": 0, "npcs_lost": 0}
    escalations: list[tuple[str, str, str, Optional[str]]] = []
    fails: list[tuple[str, str, Optional[str], Optional[str]]] = []
    with Session(graph.engine) as s:
        meta = s.get(WorldMeta, 1)
        if meta is None:
            return out
        today = meta.world_day
        quests = list(s.exec(select(Entity).where(
            Entity.type == "quest", Entity.status == "active")).all())
        for q in quests:
            attrs = dict(q.attributes or {})
            if str(attrs.get("state", "active")).lower() != "active":
                continue
            stakes = attrs.get("stakes")
            if not stakes:
                continue  # no stakes => no clock (not every thread presses)
            last = attrs.get("last_touched_day")
            if last is None:
                attrs["last_touched_day"] = today  # start the clock from now
                q.attributes = attrs
                s.add(q)
                continue
            period = max(1, int(attrs.get("stakes_period_days", period_days)))
            if today - int(last) < period:
                continue
            level = int(attrs.get("stakes_level", 0)) + 1
            attrs["stakes_level"] = level
            attrs["last_touched_day"] = today
            if level >= fail_level:
                attrs["state"] = "failed"
                fails.append((q.name, q.slug, attrs.get("location_slug"),
                              attrs.get("peril_npc")))
            else:
                escalations.append((q.name, str(stakes), q.slug,
                                    attrs.get("location_slug")))
            q.attributes = attrs
            s.add(q)
        s.commit()

    for name, stakes, slug, loc in escalations:
        graph.add_event(f"With no one acting, {name} worsens: {stakes}",
                        location=loc, involved=[slug], session_id=session_id)
        out["escalated"] += 1
    for name, slug, loc, peril in fails:
        graph.add_event(f"{name} slips beyond reach — the moment has passed.",
                        location=loc, involved=[slug], session_id=session_id)
        out["failed"] += 1
        if loc:
            shift_place_danger(graph, loc, +1, f"the fallout of {name}",
                               session_id=session_id)
        if peril and claim_peril_npc(graph, name, peril, session_id=session_id):
            out["npcs_lost"] += 1
    return out
