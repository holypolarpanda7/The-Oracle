"""
Combat state models — an initiative-ordered encounter and its combatants.

This is the mechanical spine of a fight: every creature in the initiative order
(PCs, NPCs, and monsters alike) gets a ``Combatant`` row tracking the numbers that
actually change during combat — HP, temp HP, AC, conditions, concentration, and
initiative. Encounters are tied to a game ``session_id`` so the DM brain can be fed
the live board state.

Shares the backend's ``oracle.db`` by default (see ``combat.tracker.CombatTracker``).
"""
from __future__ import annotations

from datetime import datetime, timezone


def _utcnow() -> datetime:
    """Naive UTC now (datetime.utcnow() is deprecated since 3.12)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


from typing import Any, Optional

from sqlalchemy import Column, JSON, String, Integer, Boolean
from sqlmodel import Field, SQLModel


class CombatantKind:
    PC = "pc"
    NPC = "npc"
    MONSTER = "monster"
    ALL = (PC, NPC, MONSTER)


class Condition:
    """The SRD conditions (names only — the mechanics live in the prose-rules layer)."""
    BLINDED = "blinded"
    CHARMED = "charmed"
    DEAFENED = "deafened"
    FRIGHTENED = "frightened"
    GRAPPLED = "grappled"
    INCAPACITATED = "incapacitated"
    INVISIBLE = "invisible"
    PARALYZED = "paralyzed"
    PETRIFIED = "petrified"
    POISONED = "poisoned"
    PRONE = "prone"
    RESTRAINED = "restrained"
    STUNNED = "stunned"
    UNCONSCIOUS = "unconscious"
    EXHAUSTION = "exhaustion"
    ALL = (
        BLINDED, CHARMED, DEAFENED, FRIGHTENED, GRAPPLED, INCAPACITATED, INVISIBLE,
        PARALYZED, PETRIFIED, POISONED, PRONE, RESTRAINED, STUNNED, UNCONSCIOUS,
        EXHAUSTION,
    )


class Encounter(SQLModel, table=True):
    __tablename__ = "combat_encounter"

    id: Optional[int] = Field(default=None, primary_key=True)

    # The game session this fight belongs to ("guild:channel").
    session_id: str = Field(sa_column=Column(String, nullable=False, index=True))
    name: str = Field(default="Encounter", sa_column=Column(String))

    round: int = Field(default=1, sa_column=Column(Integer))
    # Index into the (initiative-sorted) combatant order whose turn it is.
    turn_index: int = Field(default=0, sa_column=Column(Integer))
    active: bool = Field(default=True, sa_column=Column(Boolean, index=True))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Combatant(SQLModel, table=True):
    __tablename__ = "combat_combatant"

    id: Optional[int] = Field(default=None, primary_key=True)
    encounter_id: int = Field(sa_column=Column(Integer, index=True, nullable=False))

    name: str = Field(sa_column=Column(String, nullable=False))
    kind: str = Field(default=CombatantKind.MONSTER, sa_column=Column(String))

    # Optional links back to the source record.
    character_id: Optional[int] = Field(default=None, sa_column=Column(Integer, index=True))
    monster_slug: Optional[str] = Field(default=None, sa_column=Column(String, index=True))

    initiative: int = Field(default=0, sa_column=Column(Integer))
    dex_mod: int = Field(default=0, sa_column=Column(Integer))   # tiebreaker

    max_hp: int = Field(default=1, sa_column=Column(Integer))
    current_hp: int = Field(default=1, sa_column=Column(Integer))
    temp_hp: int = Field(default=0, sa_column=Column(Integer))
    armor_class: Optional[int] = Field(default=None, sa_column=Column(Integer))

    # Cover against ranged/targeted effects: none | half (+2 AC/Dex) |
    # three-quarters (+5) | total (can't be targeted).
    cover: str = Field(default="none", sa_column=Column(String))

    # Theater-of-the-mind spacing band, kept true by the DM's move hooks:
    # "melee with <name>" | "near" (within one move) | "far" (needs Dash/ranged).
    position: Optional[str] = Field(default=None, sa_column=Column(String))

    conditions: Optional[Any] = Field(default=None, sa_column=Column(JSON))     # list[str]
    concentration: Optional[str] = Field(default=None, sa_column=Column(String))  # what they concentrate on
    defeated: bool = Field(default=False, sa_column=Column(Boolean))
    notes: Optional[str] = Field(default=None, sa_column=Column(String))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
