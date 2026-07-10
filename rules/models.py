"""
SQLModel schema for the SRD rules reference (structured game data).

These tables hold *mechanically exact* game entities — monster stat blocks and
spells — seeded from the open, Creative-Commons 5e SRD dataset. They exist so the
DM brain and the internal dice roller can look up real numbers (AC, HP, attack
bonuses, damage dice, save DCs) instead of hallucinating them.

Prose rules (grappling, resting, DM guidance, etc.) are intentionally NOT stored
here — those belong in a later vector-RAG layer. This is the structured half only.

Source: 5e-bits/5e-database (SRD 5.1, CC-BY-4.0). Attribution required if shared.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Column, JSON, String, Float, Integer, Boolean
from sqlmodel import Field, SQLModel

SRD_SOURCE = "SRD 5.1 (CC-BY-4.0)"


class Monster(SQLModel, table=True):
    __tablename__ = "rules_monster"

    id: Optional[int] = Field(default=None, primary_key=True)

    index_slug: str = Field(sa_column=Column(String, nullable=False, unique=True, index=True))
    name: str = Field(sa_column=Column(String, nullable=False, index=True))

    size: Optional[str] = Field(default=None, sa_column=Column(String))
    type: Optional[str] = Field(default=None, sa_column=Column(String, index=True))
    subtype: Optional[str] = Field(default=None, sa_column=Column(String))
    alignment: Optional[str] = Field(default=None, sa_column=Column(String))

    armor_class: Optional[int] = Field(default=None, sa_column=Column(Integer))
    ac_desc: Optional[str] = Field(default=None, sa_column=Column(String))
    hit_points: Optional[int] = Field(default=None, sa_column=Column(Integer))
    hit_dice: Optional[str] = Field(default=None, sa_column=Column(String))
    hit_points_roll: Optional[str] = Field(default=None, sa_column=Column(String))

    # Ability scores
    strength: Optional[int] = Field(default=None, sa_column=Column(Integer))
    dexterity: Optional[int] = Field(default=None, sa_column=Column(Integer))
    constitution: Optional[int] = Field(default=None, sa_column=Column(Integer))
    intelligence: Optional[int] = Field(default=None, sa_column=Column(Integer))
    wisdom: Optional[int] = Field(default=None, sa_column=Column(Integer))
    charisma: Optional[int] = Field(default=None, sa_column=Column(Integer))

    challenge_rating: Optional[float] = Field(default=None, sa_column=Column(Float, index=True))
    proficiency_bonus: Optional[int] = Field(default=None, sa_column=Column(Integer))
    xp: Optional[int] = Field(default=None, sa_column=Column(Integer))

    languages: Optional[str] = Field(default=None, sa_column=Column(String))

    # Structured JSON blobs (used directly by the roller/combat layer)
    speed: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    proficiencies: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    senses: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    damage_vulnerabilities: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    damage_resistances: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    damage_immunities: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    condition_immunities: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    special_abilities: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    actions: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    legendary_actions: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    source: str = Field(default=SRD_SOURCE, sa_column=Column(String))
    raw: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Spell(SQLModel, table=True):
    __tablename__ = "rules_spell"

    id: Optional[int] = Field(default=None, primary_key=True)

    index_slug: str = Field(sa_column=Column(String, nullable=False, unique=True, index=True))
    name: str = Field(sa_column=Column(String, nullable=False, index=True))

    level: int = Field(default=0, sa_column=Column(Integer, index=True))
    school: Optional[str] = Field(default=None, sa_column=Column(String))

    casting_time: Optional[str] = Field(default=None, sa_column=Column(String))
    range: Optional[str] = Field(default=None, sa_column=Column(String))
    duration: Optional[str] = Field(default=None, sa_column=Column(String))
    material: Optional[str] = Field(default=None, sa_column=Column(String))

    concentration: bool = Field(default=False, sa_column=Column(Boolean))
    ritual: bool = Field(default=False, sa_column=Column(Boolean))

    attack_type: Optional[str] = Field(default=None, sa_column=Column(String))
    dc_type: Optional[str] = Field(default=None, sa_column=Column(String))
    dc_success: Optional[str] = Field(default=None, sa_column=Column(String))

    components: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    classes: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    damage: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    desc: Optional[str] = Field(default=None, sa_column=Column(String))
    higher_level: Optional[str] = Field(default=None, sa_column=Column(String))

    source: str = Field(default=SRD_SOURCE, sa_column=Column(String))
    raw: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
