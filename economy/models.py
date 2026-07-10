"""Economy persistence: downtime logs and long-running crafting projects.

Shares ``oracle.db`` with the rest of the systems.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class DowntimeLog(SQLModel, table=True):
    """One resolved downtime activity for a character."""

    __tablename__ = "economy_downtime_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    character_id: int = Field(index=True)
    activity: str = Field(index=True)  # crafting/profession/recuperating/research/training/carousing
    days: int = 0
    start_day: Optional[int] = None  # world-day the activity began
    end_day: Optional[int] = None    # world-day it finished
    cp_delta: int = 0                # net copper change (earned minus lifestyle/costs)
    result_summary: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CraftingProject(SQLModel, table=True):
    """A mundane or (optionally) magic item being crafted over many days."""

    __tablename__ = "economy_crafting_project"

    id: Optional[int] = Field(default=None, primary_key=True)
    character_id: int = Field(index=True)
    item_slug: Optional[str] = Field(default=None, index=True)
    item_name: str = ""
    is_magic: bool = False
    target_cost_gp: float = 0.0     # market price of the finished item
    materials_gp: float = 0.0       # up-front materials cost (paid at start)
    progress_gp: float = 0.0        # gp of "work" completed so far
    gp_per_day: float = 5.0         # effective progress per crafting day
    days_spent: int = 0
    complete: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
