"""Persistence for ongoing afflictions (diseases and madness on a character).

Traps are resolved instantly and don't need a row; diseases and madness persist,
so each active one gets an ``Affliction`` row tied to the character and world-day.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Affliction(SQLModel, table=True):
    __tablename__ = "hazard_affliction"

    id: Optional[int] = Field(default=None, primary_key=True)
    character_id: int = Field(index=True)
    kind: str = Field(index=True)          # disease | madness
    slug: Optional[str] = None             # catalog slug (diseases) or madness tier
    name: str = ""
    severity: Optional[str] = None         # short | long | indefinite (madness)
    description: Optional[str] = None
    onset_day: Optional[int] = None        # world-day it took hold
    ends_day: Optional[int] = None         # world-day it lifts (None = indefinite)
    active: bool = Field(default=True, index=True)
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
