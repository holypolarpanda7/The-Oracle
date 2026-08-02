"""SQLModel schema for flying vessels and their crew stations.

Two tables sharing the backend's ``oracle.db``:

  - ``Airship``        : one vessel that exists in the world
  - ``CrewStation``    : an installed station, with its OWN AC and hit points

The station-per-row shape is load-bearing rather than tidiness. A crew station
is damaged, disabled and repaired independently of the hull — a turret at 0 HP
stops working while the ship sails on — so it needs its own durable state, and
"which stations does this ship have" is the thing upgrading a vessel changes.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import Column, Integer, JSON, String
from sqlmodel import Field, SQLModel, create_engine
from sqlalchemy.engine import Engine


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CoreState:
    """The elemental core drives everything; its state gates most actions."""
    ENGAGED = "engaged"
    SUPPRESSED = "suppressed"   # dormant: crawls and hovers, stations offline
    BROKEN = "broken"           # shattered shard: hovers, never moves again

    ALL = {ENGAGED, SUPPRESSED, BROKEN}


class Airship(SQLModel, table=True):
    """A vessel in the world. Hull state lives here, station state next door."""

    __tablename__ = "airship_vessel"

    id: Optional[int] = Field(default=None, primary_key=True)

    #: Catalog slug this was built from (see airships/catalog.py).
    kind: str = Field(sa_column=Column(String, nullable=False, index=True))
    name: str = Field(default="Unnamed Vessel", sa_column=Column(String))

    #: Whose ship it is: a Character id, and/or the table that flies it.
    owner_character_id: Optional[int] = Field(default=None, sa_column=Column(Integer, index=True))
    session_id: Optional[str] = Field(default=None, sa_column=Column(String, index=True))

    #: The world-graph place slug for the vessel itself. An airship IS a place —
    #: that is what lets the party be located_in it, lets arrival art render its
    #: deck, and lets it move without any of that machinery learning a new idea.
    place_slug: Optional[str] = Field(default=None, sa_column=Column(String, index=True))

    armor_class: int = Field(default=15, sa_column=Column(Integer))
    hp: int = Field(default=100, sa_column=Column(Integer))
    hp_max: int = Field(default=100, sa_column=Column(Integer))
    #: Damage below this from a single source is ignored entirely.
    damage_threshold: int = Field(default=0, sa_column=Column(Integer))

    speed_mph: float = Field(default=8.0)          # overland/journey pace
    fly_speed_ft: int = Field(default=80, sa_column=Column(Integer))  # tactical

    crew_max: int = Field(default=6, sa_column=Column(Integer))
    passengers_max: int = Field(default=10, sa_column=Column(Integer))
    cargo_tons: float = Field(default=1.0)

    core_state: str = Field(default=CoreState.ENGAGED, sa_column=Column(String))
    #: World-day the core may be re-engaged (a dispel holds it down a while).
    core_locked_until_day: Optional[int] = Field(default=None, sa_column=Column(Integer))

    #: Emergency repairs work once per docking; this records that it's spent.
    emergency_repaired: bool = Field(default=False)
    docked_at: Optional[str] = Field(default=None, sa_column=Column(String))

    #: Counts of applied upgrades, each capped by the catalog.
    upgrades: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    notes: Optional[str] = Field(default=None, sa_column=Column(String))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @property
    def wrecked(self) -> bool:
        return self.hp <= 0

    @property
    def can_move(self) -> bool:
        """A suppressed core still hovers and crawls; a broken one never moves."""
        return not self.wrecked and self.core_state != CoreState.BROKEN


class CrewStation(SQLModel, table=True):
    """One installed station: its own AC, HP, and who is manning it."""

    __tablename__ = "airship_station"

    id: Optional[int] = Field(default=None, primary_key=True)
    airship_id: int = Field(index=True)

    station_slug: str = Field(sa_column=Column(String, nullable=False, index=True))
    name: str = Field(default="", sa_column=Column(String))

    armor_class: int = Field(default=15, sa_column=Column(Integer))
    hp: int = Field(default=20, sa_column=Column(Integer))
    hp_max: int = Field(default=20, sa_column=Column(Integer))

    #: Who is at this station right now (free text: a PC name or a hireling).
    operator: Optional[str] = Field(default=None, sa_column=Column(String))
    #: Dispel Magic knocks a station out for a minute rather than damaging it.
    disabled_until_round: Optional[int] = Field(default=None, sa_column=Column(Integer))
    #: Per-station bookkeeping (charges spent, calibrated, uses today).
    state: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=_utcnow)

    @property
    def operable(self) -> bool:
        return self.hp > 0


def get_engine(database_url: Optional[str] = None) -> Engine:
    """Default to the backend's ``oracle.db`` (shared with rules/world)."""
    if database_url is None:
        database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        backend_db = Path(__file__).resolve().parent.parent / "oracle-dm-backend" / "oracle.db"
        database_url = f"sqlite:///{backend_db}"
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, echo=False, connect_args=connect_args)
