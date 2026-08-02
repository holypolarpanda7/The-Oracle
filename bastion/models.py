"""Bastion persistence: strongholds, their facilities, and turn events.

Shares ``oracle.db``. Bastions are owned 2024-era content; the facility catalog
lives in ``bastion.catalog`` (self-authored mechanical summaries).
"""
from __future__ import annotations

from datetime import datetime, timezone


def _utcnow() -> datetime:
    """Naive UTC now (datetime.utcnow() is deprecated since 3.12)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


from typing import Optional

from sqlmodel import Field, SQLModel


class Bastion(SQLModel, table=True):
    """A character's stronghold."""

    __tablename__ = "bastion_bastion"

    id: Optional[int] = Field(default=None, primary_key=True)
    character_id: int = Field(index=True)
    name: str = ""
    level_acquired: int = 5
    defenders: int = 0            # bastion defender hirelings
    turns_taken: int = 0          # number of resolved bastion turns
    last_turn_day: Optional[int] = None  # world-day of the last resolved turn
    notes: Optional[str] = None

    # --- mobile bastions (see bastion/mobile.py) ---
    # A bastion built inside a vehicle travels, provided one of its special
    # facilities can propel it. It is NOT a separate kind of thing: the same
    # rows, the same turns, plus a position and a destination.
    #
    # ``place_slug`` is its world-graph place — which is what lets the party be
    # inside it, lets it be somewhere, and lets it move without the world layer
    # learning a new idea. ``vehicle_kind`` is free text for the fiction
    # ("airship", "lightning rail cart", "galleon"); ``airship_id`` links the
    # flying ones to a real vessel in ``airships/`` so hull, stations and core
    # are tracked properly rather than being narration.
    vehicle_kind: Optional[str] = None
    place_slug: Optional[str] = None
    airship_id: Optional[int] = None
    destination_slug: Optional[str] = None
    underway: bool = False
    miles_remaining: float = 0.0

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @property
    def mobile(self) -> bool:
        return bool(self.vehicle_kind)


class FacilityInstance(SQLModel, table=True):
    """A special facility installed in a bastion."""

    __tablename__ = "bastion_facility"

    id: Optional[int] = Field(default=None, primary_key=True)
    bastion_id: int = Field(index=True)
    facility_slug: str = Field(index=True)
    name: str = ""
    facility_type: str = "special"  # basic | special
    space: Optional[str] = None      # cramped | roomy | vast
    hirelings: int = 0
    current_order: Optional[str] = None  # order issued for the upcoming turn
    enabled: bool = True
    created_at: datetime = Field(default_factory=_utcnow)


class BastionEvent(SQLModel, table=True):
    """Append-only log of what happened on each bastion turn."""

    __tablename__ = "bastion_event"

    id: Optional[int] = Field(default=None, primary_key=True)
    bastion_id: int = Field(index=True)
    turn: int = 0
    world_day: Optional[int] = None
    event_type: str = "order"   # order | income | attack | special
    facility_slug: Optional[str] = None
    description: str = ""
    cp_delta: int = 0
    created_at: datetime = Field(default_factory=_utcnow)
