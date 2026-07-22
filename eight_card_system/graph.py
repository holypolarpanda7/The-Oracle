"""
WorldGraph — read/write API and relevance-scoped retrieval over the world graph.

Design goals:
  * Append-only history: relations are opened/closed over in-world days.
  * Cheap, dependency-free retrieval: plain SQL + a small in-Python BFS.
  * Only ever hand the DM the *local* slice of the world (near the PC's location
    and the entities named in the current action), never the whole graph.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow() -> datetime:
    """Naive UTC now (datetime.utcnow() is deprecated since 3.12)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


from pathlib import Path
from typing import Iterable, Optional, Union

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from . import geo, shops
from .models import (
    Entity, Relation, WorldEvent, WorldMeta, RelationType,
    TimeOfDay, describe_date, DAYS_PER_MONTH,
    Attitude, CompanionControl, NpcAttr, attitude_for_trust,
    TRUST_MIN, TRUST_MAX,
)

EntityRef = Union[int, str, Entity]


def get_engine(database_url: Optional[str] = None) -> Engine:
    """Build (or reuse) a SQLModel engine.

    Defaults to the same ``oracle.db`` the FastAPI backend uses so the world graph
    lives alongside the character DB. Override with the ``DATABASE_URL`` env var or
    the ``database_url`` argument.
    """
    if database_url is None:
        database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        # Default: oracle-dm-backend/oracle.db next to the backend.
        backend_db = Path(__file__).resolve().parent.parent / "oracle-dm-backend" / "oracle.db"
        database_url = f"sqlite:///{backend_db}"
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, echo=False, connect_args=connect_args)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "entity"


@dataclass
class WorldContext:
    """The relevant slice of the world for one DM turn."""
    location: Optional[Entity]
    anchor_ids: set[int]
    entities: list[Entity]
    relations: list[Relation]
    events: list[WorldEvent]
    world_day: int
    date_str: str = ""

    def _slice_coords(self, entity: Optional[Entity]) -> Optional[tuple[float, float]]:
        """An entity's coords, or its PART_OF parent's within this slice (2 hops)."""
        current = entity
        for _ in range(3):
            if current is None:
                return None
            c = geo.coords_from_attrs(current.attributes)
            if c is not None:
                return c
            parent_id = next(
                (r.dst_id for r in self.relations
                 if r.rel_type == RelationType.PART_OF and r.src_id == current.id),
                None,
            )
            current = next((e for e in self.entities if e.id == parent_id), None)
        return None

    def merchant_scale(self, npc: Entity) -> str:
        """Enclosing settlement scale for a merchant NPC, walked in-slice."""
        loc_id = next((r.dst_id for r in self.relations
                       if r.rel_type == RelationType.LOCATED_IN and r.src_id == npc.id),
                      None)
        current = next((e for e in self.entities if e.id == loc_id), None)
        for _ in range(4):
            if current is None:
                break
            sc = str((current.attributes or {}).get("scale")
                     or current.subtype or "").lower()
            if sc in ("village", "town", "city", "settlement"):
                return sc
            pid = next((r.dst_id for r in self.relations
                        if r.rel_type == RelationType.PART_OF and r.src_id == current.id),
                       None)
            current = next((e for e in self.entities if e.id == pid), None)
        return "village"

    def _frontier_anchor_ids(self) -> set[int]:
        """Location + its immediate PART_OF parent (e.g. a town), for stub adjacency."""
        ids: set[int] = set()
        if not self.location:
            return ids
        ids.add(self.location.id)
        for r in self.relations:
            if r.rel_type == RelationType.PART_OF and r.src_id == self.location.id:
                ids.add(r.dst_id)
        return ids

    def _frontier_stub_lines(self) -> tuple[list[str], set[int]]:
        """Render lines for unexplored stubs adjacent to the current location.

        Returns ``(lines, stub_ids)`` — ``stub_ids`` lets the caller exclude
        these from the regular "Nearby places" listing so they aren't doubled up.
        """
        anchors = self._frontier_anchor_ids()
        if not anchors:
            return [], set()
        lines: list[str] = []
        stub_ids: set[int] = set()
        for e in self.entities:
            if e.type != "place":
                continue
            attrs = e.attributes or {}
            if e.status != "unexplored" and not attrs.get("stub"):
                continue
            edge = next(
                (r for r in self.relations
                 if r.rel_type == RelationType.ADJACENT_TO
                 and ((r.src_id == e.id and r.dst_id in anchors)
                      or (r.dst_id == e.id and r.src_id in anchors))),
                None,
            )
            if edge is None:
                continue
            stub_ids.add(e.id)
            eattrs = edge.attributes or {}
            direction = eattrs.get("direction", "?")
            travel = eattrs.get("travel_time", "unknown travel time")
            # Coords beat edge annotations when both ends are placed on the globe.
            here = self._slice_coords(self.location)
            there = geo.coords_from_attrs(attrs)
            if here and there:
                direction = geo.compass_between(here, there)
                travel = geo.travel_time_str(geo.distance_mi(here, there))
            biome = attrs.get("biome", "wilds")
            danger = attrs.get("danger", "unknown")
            ceiling = attrs.get("scale_ceiling", "poi")
            motifs = attrs.get("motifs") or []
            line = (f"- **{e.name}** ({direction}, {travel}): unexplored {biome}, "
                    f"danger {danger}, largest settlement possible: {ceiling}.")
            if motifs:
                line += f" Seeds: {'; '.join(motifs)}"
            denizens = attrs.get("denizens") or []
            if denizens:
                line += f" Signs of: {', '.join(denizens)}."
            lines.append(line)
        return lines, stub_ids

    def render(self) -> str:
        """Compact text block to inject into the DM prompt."""
        by_id = {e.id: e for e in self.entities}

        def label(eid: Optional[int]) -> str:
            e = by_id.get(eid)
            return e.name if e else f"#{eid}"

        header = self.date_str or f"day {self.world_day}"
        lines: list[str] = [f"# World state ({header})"]

        if self.location:
            desc = (self.location.attributes or {}).get("description", "")
            climate = geo.climate_for(self._slice_coords(self.location))
            lines.append(f"\n## Current location: {self.location.name}"
                         + (f" — {desc}" if desc else "")
                         + f" (climate: {climate})")
            # Census backdrop for the settlement we're in (or inside of):
            # implied population the DM may draw minor folk from freely.
            settle = self.location
            for _ in range(3):
                if (settle.attributes or {}).get("census"):
                    break
                parent_id = next(
                    (r.dst_id for r in self.relations
                     if r.rel_type == RelationType.PART_OF and r.src_id == settle.id),
                    None,
                )
                settle = next((e for e in self.entities if e.id == parent_id), None)
                if settle is None:
                    break
            if settle is not None and (settle.attributes or {}).get("census"):
                sa = settle.attributes or {}
                bits = [f"{settle.name}: ~{sa.get('population', '?')} souls"]
                if sa.get("wards"):
                    bits.append(f"wards: {', '.join(sa['wards'])}")
                if sa.get("trades"):
                    bits.append(f"trades: {', '.join(sa['trades'])}")
                lines.append("Census — " + "; ".join(bits)
                             + ". (Freely name minor folk consistent with this.)")

        stub_lines, _shown_stub_ids = self._frontier_stub_lines()

        # Group entities by type for a readable block. Frontier stubs never belong
        # in the regular listing — they only ever appear (conditionally) under
        # "Beyond the map" below, so they don't show up twice or out of context.
        def is_stub(e: Entity) -> bool:
            return e.status == "unexplored" or bool((e.attributes or {}).get("stub"))

        buckets: dict[str, list[Entity]] = {}
        for e in self.entities:
            if self.location and e.id == self.location.id:
                continue
            if e.type == "place" and is_stub(e):
                continue
            buckets.setdefault(e.type, []).append(e)

        pretty = {
            "npc": "People here / nearby",
            "faction": "Factions",
            "item": "Notable items",
            "quest": "Active threads",
            "place": "Nearby places",
            "deity": "Powers & faiths",
            "lore": "Rumors & lore",
        }
        for etype in ("npc", "place", "faction", "item", "quest", "deity", "lore"):
            group = buckets.get(etype)
            if not group:
                continue
            lines.append(f"\n## {pretty.get(etype, etype.title())}")
            here = self._slice_coords(self.location) if self.location else None
            for e in group:
                attrs = e.attributes or {}
                desc = attrs.get("description", "")
                status = "" if e.status == "active" else f" [{e.status}]"
                extra = ""
                if etype == "npc":
                    bits = [str(attrs[k]) for k in ("attitude", "role", "memory")
                            if attrs.get(k)]
                    if bits:
                        extra = f" ({', '.join(bits)})"
                elif etype == "place" and here:
                    # Known bearings: derived from coords, never narrated guesswork.
                    there = geo.coords_from_attrs(attrs)
                    if there:
                        d = geo.distance_mi(here, there)
                        if d >= 0.5:
                            extra = f" ({geo.compass_between(here, there)}, {geo.travel_time_str(d)})"
                lines.append(f"- **{e.name}**{status}{extra}" + (f": {desc}" if desc else ""))
                # Live alignment for powers (canon-relevant) and for anyone whose
                # deeds have drifted their soul off its origin — so the DM narrates
                # who they are NOW, not who they were seeded as.
                if etype == "deity" or attrs.get("align_axes"):
                    from . import relationships as _rel
                    lbl = _rel.axes_to_label(_rel.get_axes(e))
                    origin = attrs.get("align_origin")
                    drift = f" (drifted from {origin})" if origin and origin != lbl else ""
                    lines.append(f"  · alignment: {lbl}{drift}")
                # Established legend about this power/person/place (recorded lore):
                # a bounded one-liner kept consistent across sessions.
                lore = attrs.get("lore")
                if lore:
                    lines.append(f"  · lore: {lore}")
                # Merchants show this week's rolled stock — prices are canon.
                if etype == "npc":
                    role_l = str(attrs.get("role", "")).strip().lower()
                    if role_l in shops.MERCHANT_ROLES:
                        stock = shops.roll_stock(
                            e.slug, role_l, self.merchant_scale(e), self.world_day)
                        if stock:
                            lines.append(f"  · stock (this week): {shops.stock_line(stock)}")

        if stub_lines:
            lines.append("\n## Beyond the map")
            lines.extend(stub_lines)

        # Party companions traveling with a PC in this slice (with control mode).
        pc_ids = {e.id for e in self.entities if e.type == "pc"}
        party_lines = [
            f"- {label(r.src_id)}"
            + (f" — {r.attributes.get('role')}" if (r.attributes or {}).get("role") else "")
            + f" (companion of {label(r.dst_id)}, "
            + ("player-run" if (r.attributes or {}).get("control") == CompanionControl.PLAYER else "DM-run")
            + ")"
            for r in self.relations
            if r.rel_type == RelationType.TRAVELS_WITH and r.dst_id in pc_ids
        ]
        if party_lines:
            lines.append("\n## Party companions")
            lines.extend(party_lines)

        # Key current relationships worth stating explicitly. When an edge carries
        # an established "why" (recorded lore), state it so the DM stays consistent
        # with what a priest/sage said last time — never re-improvises the origin.
        # A ledgered bond is rendered LIVE: its decayed, alignment-weighted net
        # (from the source's current alignment) gives the temperature word and top
        # reason, so the DM sees a feud cooling or a friendship souring in motion.
        from . import relationships as _rel
        rel_lines = []
        for r in self.relations:
            attrs = r.attributes or {}
            ledger = attrs.get("ledger")
            typed = r.rel_type in (RelationType.ALLIED_WITH, RelationType.HOSTILE_TO,
                                   RelationType.MEMBER_OF, RelationType.OWNS)
            if not ledger and not typed:
                continue  # skip bare knows/trust edges with no recorded history
            if ledger:
                src_ent = by_id.get(r.src_id)
                axes = _rel.get_axes(src_ent) if src_ent is not None else {"m": 0, "e": 0}
                net = _rel.net_sentiment(ledger, axes, self.world_day)
                line = f"- {label(r.src_id)} {_rel.band_word(net)} toward {label(r.dst_id)}"
                why = attrs.get("reason") or _rel._top_reason(ledger, axes, self.world_day)
                if why:
                    line += f" — {why}"
            else:
                line = f"- {label(r.src_id)} {r.rel_type.replace('_', ' ')} {label(r.dst_id)}"
                why = attrs.get("reason")
                if why:
                    line += f" — {why}"
            rel_lines.append(line)
        if rel_lines:
            lines.append("\n## Relationships")
            lines.extend(rel_lines)

        if self.events:
            lines.append("\n## Recent history (most recent first)")
            lines.extend(f"- (day {ev.world_day}) {ev.summary}" for ev in self.events)

        return "\n".join(lines)


class WorldGraph:
    def __init__(self, engine: Optional[Engine] = None, database_url: Optional[str] = None):
        self.engine = engine or get_engine(database_url)

    # ----- setup / time -----

    def create_tables(self) -> None:
        SQLModel.metadata.create_all(self.engine)

    def current_day(self) -> int:
        with Session(self.engine) as s:
            meta = s.get(WorldMeta, 1)
            return meta.world_day if meta else 0

    def current_date_str(self) -> str:
        """Human-facing world-calendar date/time string."""
        with Session(self.engine) as s:
            meta = self._ensure_meta(s)
            return describe_date(meta)

    def _ensure_meta(self, s: Session) -> WorldMeta:
        meta = s.get(WorldMeta, 1)
        if meta is None:
            meta = WorldMeta(id=1)
            s.add(meta)
            s.commit()
            s.refresh(meta)
        return meta

    @staticmethod
    def _roll_day(meta: WorldMeta, n: int = 1) -> None:
        """Advance the calendar by ``n`` days in place (12 months x 30 days)."""
        for _ in range(max(0, n)):
            meta.world_day += 1
            meta.day_of_month += 1
            if meta.day_of_month > DAYS_PER_MONTH:
                meta.day_of_month = 1
                meta.month += 1
                if meta.month > 12:
                    meta.month = 1
                    meta.year += 1

    def advance_day(self, n: int = 1) -> int:
        with Session(self.engine) as s:
            meta = self._ensure_meta(s)
            self._roll_day(meta, n)
            meta.time_of_day = TimeOfDay.DAWN
            meta.updated_at = _utcnow()
            s.add(meta)
            s.commit()
            return meta.world_day

    def ratchet_day(self, target_day: int) -> int:
        """Advance the clock to ``target_day`` if (and only if) it's ahead.

        The multiplayer time rule: session bubbles run in PARALLEL world time,
        so the clock takes the max of closing bubbles, never the sum, and
        never moves backward. Returns the (possibly unchanged) current day.
        """
        with Session(self.engine) as s:
            meta = self._ensure_meta(s)
            if target_day > meta.world_day:
                self._roll_day(meta, target_day - meta.world_day)
                meta.updated_at = _utcnow()
                s.add(meta)
                s.commit()
            return meta.world_day

    def wall_floor(self, *, days_per_real_day: float = 1.0) -> Optional[int]:
        """The wall-clock floor day (None before the anchor exists). The lead
        cap is measured against this: story time may run ahead of the floor
        by at most WORLD_LEAD_CAP_DAYS (enforced by the caller)."""
        import time as _time
        with Session(self.engine) as s:
            meta = self._ensure_meta(s)
            if meta.real_anchor_ts is None:
                return None
            return meta.anchor_world_day + int(
                (_time.time() - meta.real_anchor_ts) / 86400.0
                * max(0.0, days_per_real_day))

    def coords_of(self, ref: EntityRef) -> Optional[tuple]:
        """Public: an entity's coords, inherited via located_in/part_of."""
        e = self.get_entity(ref)
        if e is None:
            return None
        with Session(self.engine) as s:
            row = s.get(Entity, e.id)
            return self._coords_in_db(s, row) if row is not None else None

    def location_of(self, ref: EntityRef) -> Optional[Entity]:
        """Public: the place an entity is currently located_in (or None)."""
        e = self.get_entity(ref)
        if e is None:
            return None
        with Session(self.engine) as s:
            return self._current_location(s, e.id)

    def sync_clock(self, *, days_per_real_day: float = 1.0) -> int:
        """Apply the wall-clock floor: the world keeps breathing while nobody
        plays. Anchored on first call; the floor never outruns a bubble that
        already ratcheted ahead (max semantics). Returns the current day."""
        import time as _time
        now = _time.time()
        with Session(self.engine) as s:
            meta = self._ensure_meta(s)
            if meta.real_anchor_ts is None:
                meta.real_anchor_ts = now
                meta.anchor_world_day = meta.world_day
                s.add(meta)
                s.commit()
                return meta.world_day
            floor = meta.anchor_world_day + int(
                (now - meta.real_anchor_ts) / 86400.0 * max(0.0, days_per_real_day))
            if floor > meta.world_day:
                self._roll_day(meta, floor - meta.world_day)
                meta.updated_at = _utcnow()
                s.add(meta)
                s.commit()
            return meta.world_day

    def advance_time(self, steps: int = 1) -> str:
        """Advance the clock by coarse segments; wrapping past night rolls a day."""
        with Session(self.engine) as s:
            meta = self._ensure_meta(s)
            order = TimeOfDay.ORDER
            idx = order.index(meta.time_of_day) if meta.time_of_day in order else 0
            for _ in range(max(0, steps)):
                idx += 1
                if idx >= len(order):
                    idx = 0
                    self._roll_day(meta, 1)
            meta.time_of_day = order[idx]
            meta.updated_at = _utcnow()
            s.add(meta)
            s.commit()
            return meta.time_of_day

    # ----- entity CRUD -----
    #
    # Identity model: the SLUG is the identity, the NAME is just a label.
    # Names may repeat freely (two "Marta Fenn"s in different towns, two
    # players both named "Kara"); create_entity mints a fresh unique slug,
    # while upsert_entity addresses an existing identity by its slug.

    @staticmethod
    def _unique_slug(s: Session, base: str) -> str:
        """A slug no existing entity holds: base, then base-2, base-3, ..."""
        base = base or "entity"
        if not s.exec(select(Entity).where(Entity.slug == base)).first():
            return base
        n = 2
        while s.exec(select(Entity).where(Entity.slug == f"{base}-{n}")).first():
            n += 1
        return f"{base}-{n}"

    def create_entity(
        self,
        name: str,
        type: str,
        *,
        subtype: Optional[str] = None,
        status: str = "active",
        attributes: Optional[dict] = None,
        tags: Optional[list] = None,
        discord_user_id: Optional[str] = None,
        character_id: Optional[int] = None,
    ) -> Entity:
        """ALWAYS create a new entity — same names get distinct identities."""
        with Session(self.engine) as s:
            slug = self._unique_slug(s, slugify(name))
            ent = Entity(
                name=name, type=type, slug=slug, subtype=subtype, status=status,
                attributes=attributes or {}, tags=tags or [],
                discord_user_id=discord_user_id, character_id=character_id,
                created_day=self._day(s),
            )
            s.add(ent)
            s.commit()
            s.refresh(ent)
            return ent

    def find_pc(self, discord_user_id: str, name: Optional[str] = None) -> Optional[Entity]:
        """A player's PC entity by owner (+ name when they own several).

        The safe way to address PCs now that names aren't unique: two players
        can both play a 'Kara' without ever colliding.
        """
        with Session(self.engine) as s:
            rows = list(s.exec(select(Entity).where(
                Entity.type == "pc",
                Entity.discord_user_id == discord_user_id,
            )).all())
            if name:
                low = name.strip().lower()
                return next((e for e in rows if e.name.lower() == low), None)
            return rows[0] if rows else None

    def find_entities_by_name(self, name: str) -> list[Entity]:
        """Every entity wearing this name (or exact slug) — may be several."""
        with Session(self.engine) as s:
            out: dict[int, Entity] = {}
            by_slug = s.exec(select(Entity).where(Entity.slug == name)).first()
            if by_slug is not None:
                out[by_slug.id] = by_slug
            low = (name or "").strip().lower()
            for e in s.exec(select(Entity)).all():
                if e.name.lower() == low:
                    out[e.id] = e
            return list(out.values())

    def upsert_entity(
        self,
        name: str,
        type: str,
        *,
        slug: Optional[str] = None,
        subtype: Optional[str] = None,
        status: str = "active",
        attributes: Optional[dict] = None,
        tags: Optional[list] = None,
        discord_user_id: Optional[str] = None,
        character_id: Optional[int] = None,
    ) -> Entity:
        slug = slug or slugify(name)
        with Session(self.engine) as s:
            existing = s.exec(
                select(Entity).where(Entity.slug == slug, Entity.type == type)
            ).first()
            day = self._day(s)
            if existing:
                existing.name = name
                existing.status = status
                if subtype is not None:
                    existing.subtype = subtype
                if attributes is not None:
                    existing.attributes = {**(existing.attributes or {}), **attributes}
                if tags is not None:
                    existing.tags = tags
                if discord_user_id is not None:
                    existing.discord_user_id = discord_user_id
                if character_id is not None:
                    existing.character_id = character_id
                existing.updated_at = _utcnow()
                s.add(existing)
                s.commit()
                s.refresh(existing)
                return existing
            ent = Entity(
                name=name, type=type, slug=slug, subtype=subtype, status=status,
                attributes=attributes or {}, tags=tags or [],
                discord_user_id=discord_user_id, character_id=character_id,
                created_day=day,
            )
            s.add(ent)
            s.commit()
            s.refresh(ent)
            return ent

    def get_entity(self, ref: EntityRef) -> Optional[Entity]:
        with Session(self.engine) as s:
            return self._resolve_entity(s, ref)

    def set_status(self, ref: EntityRef, status: str) -> None:
        with Session(self.engine) as s:
            ent = self._resolve_entity(s, ref)
            if ent:
                ent.status = status
                ent.updated_at = _utcnow()
                s.add(ent)
                s.commit()

    # ----- relation CRUD (temporal) -----

    def add_relation(
        self,
        src: EntityRef,
        rel_type: str,
        dst: EntityRef,
        *,
        attributes: Optional[dict] = None,
        valid_from: Optional[int] = None,
    ) -> Optional[Relation]:
        with Session(self.engine) as s:
            src_e = self._resolve_entity(s, src)
            dst_e = self._resolve_entity(s, dst)
            if not src_e or not dst_e:
                return None
            day = valid_from if valid_from is not None else self._day(s)
            # Avoid duplicate identical open relations.
            dup = s.exec(
                select(Relation).where(
                    Relation.src_id == src_e.id,
                    Relation.rel_type == rel_type,
                    Relation.dst_id == dst_e.id,
                    Relation.valid_to == None,  # noqa: E711
                )
            ).first()
            if dup:
                return dup
            rel = Relation(
                src_id=src_e.id, rel_type=rel_type, dst_id=dst_e.id,
                attributes=attributes or {}, valid_from=day,
            )
            s.add(rel)
            s.commit()
            s.refresh(rel)
            return rel

    def close_relation(
        self,
        src: EntityRef,
        rel_type: str,
        dst: Optional[EntityRef] = None,
        *,
        at_day: Optional[int] = None,
    ) -> int:
        """Close (end) currently-open relations matching src/rel_type[/dst].

        Returns the number of relations closed.
        """
        with Session(self.engine) as s:
            src_e = self._resolve_entity(s, src)
            if not src_e:
                return 0
            day = at_day if at_day is not None else self._day(s)
            stmt = select(Relation).where(
                Relation.src_id == src_e.id,
                Relation.rel_type == rel_type,
                Relation.valid_to == None,  # noqa: E711
            )
            if dst is not None:
                dst_e = self._resolve_entity(s, dst)
                if not dst_e:
                    return 0
                stmt = stmt.where(Relation.dst_id == dst_e.id)
            open_rels = s.exec(stmt).all()
            for r in open_rels:
                r.valid_to = day
                s.add(r)
            s.commit()
            return len(open_rels)

    def move_entity(self, entity: EntityRef, place: EntityRef) -> Optional[Relation]:
        """Convenience: close the current ``located_in`` and open a new one."""
        self.close_relation(entity, RelationType.LOCATED_IN)
        return self.add_relation(entity, RelationType.LOCATED_IN, place)

    # ----- established lore (the durable "why" behind facts) -----
    #
    # When the DM narrates WHY two powers feud (or a defining legend about one),
    # that "why" must persist or a later player asking gets a contradictory tale.
    # We store it as CHEAPLY as possible: a bounded ``reason`` string stamped onto
    # the relationship edge that already exists (no new node, no new edge), or a
    # short ``lore`` string on a single entity. Rendered back in get_world_context
    # so the DM stays consistent. This is the memory-optimized companion to the
    # closed-pantheon world-law: the roster is fixed, and now the STORIES stick.

    _LORE_MAX = 240  # cap the stored string — one terse sentence, never a scene

    # Priority of edge types to attach a relational "why" to (best-typed first).
    _LORE_REL_PRIORITY = (
        RelationType.HOSTILE_TO, RelationType.ALLIED_WITH, RelationType.MEMBER_OF,
        RelationType.WORSHIPS, RelationType.GOVERNS, RelationType.OWNS,
        RelationType.KNOWS,
    )
    # Tiny sentiment cues to pick an edge type when the pair has none yet. Matched
    # as WHOLE WORDS (via a prefix check on each token) so short cues don't fire
    # on unrelated words ("war" in "toward", "kin" in "king").
    _LORE_HOSTILE_CUES = (
        "hate", "hated", "war", "wars", "betray", "betrayed", "wrong", "wronged",
        "enemy", "enemies", "enmity", "feud", "grudge", "curse", "cursed", "slew",
        "slain", "killed", "vengeance", "revenge", "rival", "spite", "broke",
        "wounded", "stole",
    )
    _LORE_ALLIED_CUES = (
        "ally", "allied", "love", "loved", "friend", "friends", "oath", "pact",
        "bond", "bonded", "saved", "swore", "loyal", "loyalty", "wed", "married",
        "kindred", "brother", "sister",
    )

    @staticmethod
    def _has_cue(words: set[str], cues: tuple) -> bool:
        return any(c in words for c in cues)

    def _bound_lore(self, reason: str) -> str:
        reason = " ".join((reason or "").split())
        if len(reason) > self._LORE_MAX:
            reason = reason[: self._LORE_MAX - 1].rstrip() + "…"
        return reason

    def record_lore(
        self,
        subject: EntityRef,
        obj: Optional[EntityRef] = None,
        *,
        reason: str,
        rel_type: Optional[str] = None,
    ) -> Optional[dict]:
        """Persist the durable "why" behind a fact — memory-cheap, no new nodes.

        - ``obj`` given: stamp a bounded ``reason`` onto the open relationship edge
          between subject & object (either direction; best-typed edge wins). If the
          pair has no edge yet, one is opened — its type is ``rel_type`` if given,
          else inferred from the reason's sentiment (hostile/allied), else ``knows``.
        - ``obj`` omitted: stamp a short ``lore`` legend on the subject entity.

        Idempotent-ish: re-recording overwrites the same slot rather than growing
        the graph. Returns a small dict describing what was written, or None.
        """
        reason = self._bound_lore(reason)
        if not reason:
            return None
        with Session(self.engine) as s:
            subj_e = self._resolve_entity(s, subject)
            if subj_e is None:
                return None

            # Single-entity legend: one bounded string on the entity itself.
            if obj is None:
                subj_e.attributes = {**(subj_e.attributes or {}), "lore": reason}
                subj_e.updated_at = _utcnow()
                s.add(subj_e)
                s.commit()
                return {"mode": "entity", "subject": subj_e.slug, "reason": reason}

            obj_e = self._resolve_entity(s, obj)
            if obj_e is None:
                return None
            day = self._day(s)

            # Any open edge between the two, either direction.
            pair = {subj_e.id, obj_e.id}
            rels = [
                r for r in s.exec(
                    select(Relation).where(Relation.valid_to == None)  # noqa: E711
                ).all()
                if {r.src_id, r.dst_id} == pair
            ]
            edge = None
            if rel_type:
                edge = next((r for r in rels if r.rel_type == rel_type), None)
            if edge is None:
                for rt in self._LORE_REL_PRIORITY:
                    edge = next((r for r in rels if r.rel_type == rt), None)
                    if edge is not None:
                        break
            if edge is None:
                edge = next(iter(rels), None)

            if edge is not None:
                edge.attributes = {**(edge.attributes or {}), "reason": reason}
                s.add(edge)
                s.commit()
                return {"mode": "relation", "rel_type": edge.rel_type,
                        "created": False, "reason": reason}

            # No edge yet: open one, inferring sentiment when not told.
            rt = rel_type
            if rt is None:
                words = set(re.findall(r"[a-z]+", reason.lower()))
                allied = self._has_cue(words, self._LORE_ALLIED_CUES)
                hostile = self._has_cue(words, self._LORE_HOSTILE_CUES)
                rt = (RelationType.HOSTILE_TO if hostile and not allied
                      else RelationType.ALLIED_WITH if allied and not hostile
                      else RelationType.KNOWS)
            rel = Relation(src_id=subj_e.id, rel_type=rt, dst_id=obj_e.id,
                           attributes={"reason": reason}, valid_from=day)
            s.add(rel)
            s.commit()
            return {"mode": "relation", "rel_type": rt, "created": True,
                    "reason": reason}

    # ----- NPC relationships: trust & party companionship -----

    def _open_relation(
        self, s: Session, src_id: int, rel_type: str, dst_id: int
    ) -> Optional[Relation]:
        return s.exec(
            select(Relation).where(
                Relation.src_id == src_id,
                Relation.rel_type == rel_type,
                Relation.dst_id == dst_id,
                Relation.valid_to == None,  # noqa: E711
            )
        ).first()

    def get_trust(self, npc: EntityRef, pc: EntityRef) -> Optional[int]:
        """Current trust score an NPC holds toward a PC, or None if unacquainted."""
        with Session(self.engine) as s:
            npc_e = self._resolve_entity(s, npc)
            pc_e = self._resolve_entity(s, pc)
            if not npc_e or not pc_e:
                return None
            rel = self._open_relation(s, npc_e.id, RelationType.KNOWS, pc_e.id)
            if not rel:
                return None
            return int((rel.attributes or {}).get("trust", 0))

    def adjust_trust(
        self, npc: EntityRef, pc: EntityRef, delta: int, *, reason: str = ""
    ) -> Optional[dict]:
        """Nudge an NPC's trust toward a PC, opening the acquaintance if needed.

        Returns ``{trust, attitude, delta}`` or None if either entity is unknown.
        """
        with Session(self.engine) as s:
            npc_e = self._resolve_entity(s, npc)
            pc_e = self._resolve_entity(s, pc)
            if not npc_e or not pc_e:
                return None
            day = self._day(s)
            rel = self._open_relation(s, npc_e.id, RelationType.KNOWS, pc_e.id)
            if not rel:
                rel = Relation(
                    src_id=npc_e.id, rel_type=RelationType.KNOWS, dst_id=pc_e.id,
                    attributes={"trust": 0}, valid_from=day,
                )
            attrs = dict(rel.attributes or {})
            # Entropy: trust fades toward indifference over world time; the
            # decayed value becomes the new base the moment they interact again.
            from . import entropy
            base = entropy.decayed_trust(
                int(attrs.get("trust", 0)), attrs.get("last_day"), day)
            new_trust = max(TRUST_MIN, min(TRUST_MAX, base + int(delta)))
            attitude = attitude_for_trust(new_trust)
            attrs["trust"] = new_trust
            attrs["attitude"] = attitude
            attrs["last_day"] = day
            if reason:
                attrs["last_reason"] = reason
            rel.attributes = attrs
            s.add(rel)
            # Mirror the derived attitude onto the NPC for quick single-PC reads.
            npc_e.attributes = {**(npc_e.attributes or {}), NpcAttr.ATTITUDE: attitude}
            npc_e.updated_at = _utcnow()
            s.add(npc_e)
            s.commit()
            return {"trust": new_trust, "attitude": attitude, "delta": int(delta)}

    def recruit_companion(
        self,
        npc: EntityRef,
        pc: EntityRef,
        *,
        control: str = CompanionControl.DM,
        role: str = "",
    ) -> Optional[Relation]:
        """Add an NPC to a PC's party via a ``travels_with`` relation.

        ``control`` records whether the PLAYER or the DM runs the companion.
        """
        control = control if control in CompanionControl.ALL else CompanionControl.DM
        with Session(self.engine) as s:
            npc_e = self._resolve_entity(s, npc)
            pc_e = self._resolve_entity(s, pc)
            if not npc_e or not pc_e:
                return None
            day = self._day(s)
            # A companion travels with one party at a time: close any prior tie.
            existing = s.exec(
                select(Relation).where(
                    Relation.src_id == npc_e.id,
                    Relation.rel_type == RelationType.TRAVELS_WITH,
                    Relation.valid_to == None,  # noqa: E711
                )
            ).all()
            for r in existing:
                r.valid_to = day
                s.add(r)
            rel = Relation(
                src_id=npc_e.id, rel_type=RelationType.TRAVELS_WITH, dst_id=pc_e.id,
                attributes={"control": control, "role": role, "joined_day": day},
                valid_from=day,
            )
            s.add(rel)
            s.commit()
            s.refresh(rel)
            return rel

    def dismiss_companion(self, npc: EntityRef, pc: EntityRef) -> int:
        """Remove an NPC from a PC's party (close the ``travels_with`` relation)."""
        return self.close_relation(npc, RelationType.TRAVELS_WITH, pc)

    def set_companion_control(
        self, npc: EntityRef, pc: EntityRef, control: str
    ) -> Optional[str]:
        """Switch who runs a companion (player | dm). Returns the new mode or None."""
        if control not in CompanionControl.ALL:
            return None
        with Session(self.engine) as s:
            npc_e = self._resolve_entity(s, npc)
            pc_e = self._resolve_entity(s, pc)
            if not npc_e or not pc_e:
                return None
            rel = self._open_relation(s, npc_e.id, RelationType.TRAVELS_WITH, pc_e.id)
            if not rel:
                return None
            rel.attributes = {**(rel.attributes or {}), "control": control}
            s.add(rel)
            s.commit()
            return control

    def list_companions(self, pc: EntityRef) -> list[dict]:
        """Companions currently traveling with a PC, with control mode + role."""
        with Session(self.engine) as s:
            pc_e = self._resolve_entity(s, pc)
            if not pc_e:
                return []
            rels = s.exec(
                select(Relation).where(
                    Relation.rel_type == RelationType.TRAVELS_WITH,
                    Relation.dst_id == pc_e.id,
                    Relation.valid_to == None,  # noqa: E711
                )
            ).all()
            out: list[dict] = []
            for r in rels:
                npc = s.get(Entity, r.src_id)
                if not npc:
                    continue
                attrs = r.attributes or {}
                out.append({
                    "npc": npc,
                    "slug": npc.slug,
                    "name": npc.name,
                    "control": attrs.get("control", CompanionControl.DM),
                    "role": attrs.get("role", ""),
                    "joined_day": attrs.get("joined_day"),
                })
            return out

    # ----- events -----

    def add_event(
        self,
        summary: str,
        *,
        location: Optional[EntityRef] = None,
        involved: Optional[Iterable[EntityRef]] = None,
        changes: Optional[dict] = None,
        session_id: Optional[str] = None,
    ) -> WorldEvent:
        with Session(self.engine) as s:
            loc_id = None
            if location is not None:
                loc = self._resolve_entity(s, location)
                loc_id = loc.id if loc else None
            involved_ids: list[int] = []
            for ref in involved or []:
                e = self._resolve_entity(s, ref)
                if e:
                    involved_ids.append(e.id)
            ev = WorldEvent(
                world_day=self._day(s),
                summary=summary,
                location_id=loc_id,
                involved=involved_ids,
                changes=changes or {},
                session_id=session_id,
            )
            s.add(ev)
            s.commit()
            s.refresh(ev)
            return ev

    # ----- size management (keep the DB bounded as the world grows) -----

    def _coords_in_db(self, s: Session, entity: Entity) -> Optional[tuple[float, float]]:
        """An entity's coords, inherited via located_in then part_of (4 hops)."""
        current: Optional[Entity] = entity
        # Non-places first hop through where they are.
        if current is not None and current.type != "place":
            current = self._current_location(s, current.id)
        for _ in range(4):
            if current is None:
                return None
            c = geo.coords_from_attrs(current.attributes)
            if c is not None:
                return c
            rel = s.exec(
                select(Relation).where(
                    Relation.src_id == current.id,
                    Relation.rel_type == RelationType.PART_OF,
                    Relation.valid_to == None,  # noqa: E711
                )
            ).first()
            current = s.get(Entity, rel.dst_id) if rel else None
        return None

    # Scales fine-grained enough to be archival candidates; big geography and
    # the social fabric (factions, quests, deities, lore) always stay.
    _ARCHIVABLE_PLACE_SCALES = {"poi", "building", "room", "district", "dungeon"}

    def enforce_world_caps(
        self,
        *,
        max_entities: int = 3000,
        max_events: int = 4000,
        keep_event_days: int = 120,
        pc_radius_mi: float = 60.0,
        batch: int = 200,
    ) -> dict:
        """Bound long-term growth without losing canon.

        - Entities: when actives exceed ``max_entities``, fine-detail entities
          (small places, NPCs, items) that are far from every PC and untouched
          by recent events are flipped to status ``"archived"`` — dropped from
          context retrieval but revived automatically if a player names them.
        - Events: when the log exceeds ``max_events``, events older than
          ``keep_event_days`` are compacted into one chronicle entry per
          location; the originals are deleted (their essence survives in the
          chronicle summary).
        Returns counts of what was done. Cheap when under the caps.
        """
        out = {"archived": 0, "events_compacted": 0}
        with Session(self.engine) as s:
            day = self._day(s)

            # --- entity archival -------------------------------------------
            active = list(s.exec(
                select(Entity).where(Entity.status != "archived")
            ).all())
            excess = len(active) - max_entities
            if excess > 0:
                # Entities touched by recent events are off-limits.
                recent_ids: set[int] = set()
                for ev in s.exec(
                    select(WorldEvent).where(WorldEvent.world_day >= day - keep_event_days)
                ).all():
                    recent_ids |= set(ev.involved or [])
                    if ev.location_id:
                        recent_ids.add(ev.location_id)

                pc_coords = [
                    c for pc in active if pc.type == "pc"
                    if (c := self._coords_in_db(s, pc)) is not None
                ]

                def far_from_pcs(c: Optional[tuple[float, float]]) -> bool:
                    if c is None or not pc_coords:
                        return False  # unknown position: be conservative, keep it
                    return all(geo.distance_mi(c, pc) > pc_radius_mi for pc in pc_coords)

                candidates = [
                    e for e in active
                    if e.id not in recent_ids
                    and (
                        (e.status == "active"
                         and ((e.type == "place"
                               and str((e.attributes or {}).get("scale") or e.subtype or "")
                               .lower() in self._ARCHIVABLE_PLACE_SCALES)
                              or e.type in ("npc", "item")))
                        # Far unexplored stubs are regenerable scaffolding: the
                        # cartographer re-rolls frontier wherever the party goes.
                        or (e.type == "place" and e.status == "unexplored")
                    )
                ]
                # Oldest-touched first.
                candidates.sort(key=lambda e: e.updated_at)
                for e in candidates:
                    if out["archived"] >= min(batch, excess):
                        break
                    if not far_from_pcs(self._coords_in_db(s, e)):
                        continue
                    e.status = "archived"
                    e.updated_at = _utcnow()
                    s.add(e)
                    out["archived"] += 1
                s.commit()

            # --- event compaction -------------------------------------------
            total_events = len(list(s.exec(select(WorldEvent.id)).all()))
            if total_events > max_events:
                cutoff = day - keep_event_days
                old = list(s.exec(
                    select(WorldEvent).where(WorldEvent.world_day < cutoff)
                ).all())
                by_loc: dict[Optional[int], list[WorldEvent]] = {}
                for ev in old:
                    by_loc.setdefault(ev.location_id, []).append(ev)
                for loc_id, group in by_loc.items():
                    if len(group) < 2:
                        continue
                    group.sort(key=lambda ev: (ev.world_day, ev.id))
                    summaries = "; ".join(ev.summary for ev in group)
                    if len(summaries) > 400:
                        summaries = summaries[:397] + "..."
                    involved: list[int] = []
                    for ev in group:
                        for eid in (ev.involved or []):
                            if eid not in involved:
                                involved.append(eid)
                    chronicle = WorldEvent(
                        world_day=group[-1].world_day,
                        summary=f"Chronicle of earlier days: {summaries}",
                        location_id=loc_id,
                        involved=involved[:20],
                        changes={"compacted": len(group)},
                    )
                    s.add(chronicle)
                    for ev in group:
                        s.delete(ev)
                    out["events_compacted"] += len(group)
                s.commit()
        return out

    def location_climate(self, entity_ref: EntityRef) -> Optional[str]:
        """Climate band at an entity's position (via its location/parents), or None."""
        with Session(self.engine) as s:
            e = self._resolve_entity(s, entity_ref)
            if e is None:
                return None
            c = self._coords_in_db(s, e)
            return geo.climate_for(c) if c is not None else None

    # ----- retrieval -----

    def get_world_context(
        self,
        pc: EntityRef,
        action_text: str = "",
        *,
        hops: int = 2,
        max_events: int = 8,
    ) -> WorldContext:
        """Assemble the relevant world slice around a PC's location + action."""
        with Session(self.engine) as s:
            pc_e = self._resolve_entity(s, pc)
            day = self._day(s)
            meta = s.get(WorldMeta, 1)
            date_str = describe_date(meta) if meta else ""

            location = None
            anchors: set[int] = set()
            if pc_e:
                anchors.add(pc_e.id)
                location = self._current_location(s, pc_e.id)
                if location:
                    anchors.add(location.id)

            # Anchor on entities whose name is mentioned in the action text.
            named = self._match_named_entities(s, action_text)
            anchors |= named

            # Naming an archived entity revives it: the data was dormant, not
            # gone, and the player just proved it still matters.
            for eid in named:
                ent = s.get(Entity, eid)
                if ent and ent.status == "archived":
                    ent.status = "active"
                    ent.updated_at = _utcnow()
                    s.add(ent)
            s.commit()

            if not anchors:
                return WorldContext(location, set(), [], [], [], day, date_str)

            visited, rels = self._bfs(s, anchors, hops)

            # Pull quests/events tied to any visited entity even if just out of range.
            visited |= self._quests_touching(s, visited)

            entities = [s.get(Entity, eid) for eid in visited]
            # Archived detail stays out of the DM's context (anchors excepted —
            # they were just revived above).
            entities = [e for e in entities
                        if e is not None and (e.status != "archived" or e.id in anchors)]
            kept_ids = {e.id for e in entities}
            rels = [r for r in rels if r.src_id in kept_ids and r.dst_id in kept_ids]

            # Entropy at read time: NPC attitudes shown to the DM reflect
            # time-decayed trust, and long-absent PCs are half-remembered.
            # Annotates the DETACHED instances only — nothing is persisted
            # until the next trust adjustment writes the decayed base.
            from . import entropy
            by_id_tmp = {e.id: e for e in entities}
            pc_ids_tmp = {e.id for e in entities if e.type == "pc"}
            for r in rels:
                if r.rel_type != RelationType.KNOWS or r.dst_id not in pc_ids_tmp:
                    continue
                npc_ent = by_id_tmp.get(r.src_id)
                if npc_ent is None or npc_ent.type != "npc":
                    continue
                rattrs = r.attributes or {}
                if "trust" not in rattrs:
                    continue
                eff = entropy.decayed_trust(
                    int(rattrs.get("trust", 0)), rattrs.get("last_day"), day)
                state = entropy.memory_state(rattrs.get("last_day"), day)
                annotated = dict(npc_ent.attributes or {})
                annotated[NpcAttr.ATTITUDE] = attitude_for_trust(eff)
                if state != "fresh":
                    annotated["memory"] = ("barely recalls you" if state == "dim"
                                           else "memory of you has faded")
                npc_ent.attributes = annotated

            events = self._recent_events(s, visited, max_events)

            return WorldContext(
                location=location,
                anchor_ids=anchors,
                entities=entities,
                relations=rels,
                events=events,
                world_day=day,
                date_str=date_str,
            )

    # ----- internals -----

    @staticmethod
    def _day(s: Session) -> int:
        meta = s.get(WorldMeta, 1)
        return meta.world_day if meta else 0

    @staticmethod
    def _resolve_entity(s: Session, ref: EntityRef) -> Optional[Entity]:
        if isinstance(ref, Entity):
            return ref
        if isinstance(ref, int):
            return s.get(Entity, ref)
        if isinstance(ref, str):
            # Try slug first, then case-insensitive name.
            ent = s.exec(select(Entity).where(Entity.slug == ref)).first()
            if ent:
                return ent
            ref_l = ref.strip().lower()
            for e in s.exec(select(Entity)).all():
                if e.name.lower() == ref_l:
                    return e
        return None

    @staticmethod
    def _current_location(s: Session, entity_id: int) -> Optional[Entity]:
        rel = s.exec(
            select(Relation).where(
                Relation.src_id == entity_id,
                Relation.rel_type == RelationType.LOCATED_IN,
                Relation.valid_to == None,  # noqa: E711
            )
        ).first()
        return s.get(Entity, rel.dst_id) if rel else None

    @staticmethod
    def _match_named_entities(s: Session, text: str) -> set[int]:
        if not text or not text.strip():
            return set()
        text_l = text.lower()
        hits: set[int] = set()
        for e in s.exec(select(Entity)).all():
            name = e.name.lower()
            if len(name) >= 3 and name in text_l:
                hits.add(e.id)
        return hits

    def _bfs(self, s: Session, anchors: set[int], hops: int) -> tuple[set[int], list[Relation]]:
        visited: set[int] = set(anchors)
        frontier: set[int] = set(anchors)
        collected: dict[int, Relation] = {}
        for _ in range(max(0, hops)):
            if not frontier:
                break
            next_frontier: set[int] = set()
            for eid in frontier:
                out_rels = s.exec(
                    select(Relation).where(
                        Relation.src_id == eid,
                        Relation.valid_to == None,  # noqa: E711
                    )
                ).all()
                in_rels = s.exec(
                    select(Relation).where(
                        Relation.dst_id == eid,
                        Relation.valid_to == None,  # noqa: E711
                    )
                ).all()
                for r in list(out_rels) + list(in_rels):
                    collected[r.id] = r
                    other = r.dst_id if r.src_id == eid else r.src_id
                    if other not in visited:
                        next_frontier.add(other)
            visited |= next_frontier
            frontier = next_frontier
        return visited, list(collected.values())

    @staticmethod
    def _quests_touching(s: Session, visited: set[int]) -> set[int]:
        if not visited:
            return set()
        extra: set[int] = set()
        quest_rels = s.exec(
            select(Relation).where(
                Relation.rel_type.in_([RelationType.INVOLVES, RelationType.LOCATED_AT]),  # type: ignore[attr-defined]
                Relation.valid_to == None,  # noqa: E711
            )
        ).all()
        for r in quest_rels:
            if r.dst_id in visited or r.src_id in visited:
                extra.add(r.src_id)
        return extra

    @staticmethod
    def _recent_events(s: Session, visited: set[int], limit: int) -> list[WorldEvent]:
        if not visited:
            return []
        # Fetch a recent window and filter for relevance in Python (JSON list
        # membership is awkward to express portably in SQL).
        window = s.exec(
            select(WorldEvent).order_by(WorldEvent.world_day.desc(), WorldEvent.id.desc()).limit(200)  # type: ignore[attr-defined]
        ).all()
        relevant: list[WorldEvent] = []
        for ev in window:
            involved = set(ev.involved or [])
            if ev.location_id in visited or (involved & visited):
                relevant.append(ev)
            if len(relevant) >= limit:
                break
        return relevant
