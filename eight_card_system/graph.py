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
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Union

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from .models import Entity, Relation, WorldEvent, WorldMeta, RelationType

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

    def render(self) -> str:
        """Compact text block to inject into the DM prompt."""
        by_id = {e.id: e for e in self.entities}

        def label(eid: Optional[int]) -> str:
            e = by_id.get(eid)
            return e.name if e else f"#{eid}"

        lines: list[str] = [f"# World state (day {self.world_day})"]

        if self.location:
            desc = (self.location.attributes or {}).get("description", "")
            lines.append(f"\n## Current location: {self.location.name}"
                         + (f" — {desc}" if desc else ""))

        # Group entities by type for a readable block.
        buckets: dict[str, list[Entity]] = {}
        for e in self.entities:
            if self.location and e.id == self.location.id:
                continue
            buckets.setdefault(e.type, []).append(e)

        pretty = {
            "npc": "People here / nearby",
            "faction": "Factions",
            "item": "Notable items",
            "quest": "Active threads",
            "place": "Nearby places",
        }
        for etype in ("npc", "place", "faction", "item", "quest"):
            group = buckets.get(etype)
            if not group:
                continue
            lines.append(f"\n## {pretty.get(etype, etype.title())}")
            for e in group:
                attrs = e.attributes or {}
                desc = attrs.get("description", "")
                status = "" if e.status == "active" else f" [{e.status}]"
                lines.append(f"- **{e.name}**{status}"
                             + (f": {desc}" if desc else ""))

        # Key current relationships worth stating explicitly.
        rel_lines: list[str] = []
        for r in self.relations:
            if r.rel_type in (RelationType.ALLIED_WITH, RelationType.HOSTILE_TO,
                              RelationType.MEMBER_OF, RelationType.OWNS):
                rel_lines.append(f"- {label(r.src_id)} {r.rel_type.replace('_', ' ')} {label(r.dst_id)}")
        if rel_lines:
            lines.append("\n## Relationships")
            lines.extend(rel_lines)

        if self.events:
            lines.append("\n## Recent history (most recent first)")
            for ev in self.events:
                lines.append(f"- (day {ev.world_day}) {ev.summary}")

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

    def advance_day(self, n: int = 1) -> int:
        with Session(self.engine) as s:
            meta = s.get(WorldMeta, 1)
            if meta is None:
                meta = WorldMeta(id=1, world_day=0)
            meta.world_day += n
            meta.updated_at = datetime.utcnow()
            s.add(meta)
            s.commit()
            return meta.world_day

    # ----- entity CRUD -----

    def upsert_entity(
        self,
        name: str,
        type: str,
        *,
        slug: Optional[str] = None,
        status: str = "active",
        attributes: Optional[dict] = None,
        tags: Optional[list] = None,
        discord_user_id: Optional[str] = None,
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
                if attributes is not None:
                    existing.attributes = {**(existing.attributes or {}), **attributes}
                if tags is not None:
                    existing.tags = tags
                if discord_user_id is not None:
                    existing.discord_user_id = discord_user_id
                existing.updated_at = datetime.utcnow()
                s.add(existing)
                s.commit()
                s.refresh(existing)
                return existing
            ent = Entity(
                name=name, type=type, slug=slug, status=status,
                attributes=attributes or {}, tags=tags or [],
                discord_user_id=discord_user_id, created_day=day,
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
                ent.updated_at = datetime.utcnow()
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

            location = None
            anchors: set[int] = set()
            if pc_e:
                anchors.add(pc_e.id)
                location = self._current_location(s, pc_e.id)
                if location:
                    anchors.add(location.id)

            # Anchor on entities whose name is mentioned in the action text.
            anchors |= self._match_named_entities(s, action_text)

            if not anchors:
                return WorldContext(location, set(), [], [], [], day)

            visited, rels = self._bfs(s, anchors, hops)

            # Pull quests/events tied to any visited entity even if just out of range.
            visited |= self._quests_touching(s, visited)

            entities = [s.get(Entity, eid) for eid in visited]
            entities = [e for e in entities if e is not None]

            events = self._recent_events(s, visited, max_events)

            return WorldContext(
                location=location,
                anchor_ids=anchors,
                entities=entities,
                relations=rels,
                events=events,
                world_day=day,
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
