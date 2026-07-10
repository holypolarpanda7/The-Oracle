"""Persistence for faction reputation (renown per character per faction)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Reputation(SQLModel, table=True):
    __tablename__ = "reputation_standing"

    id: Optional[int] = Field(default=None, primary_key=True)
    character_id: int = Field(index=True)
    faction_slug: str = Field(index=True)
    faction_name: str = ""
    renown: int = Field(default=0)
    notes: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
