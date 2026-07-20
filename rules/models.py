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

from datetime import datetime, timezone


def _utcnow() -> datetime:
    """Naive UTC now (datetime.utcnow() is deprecated since 3.12)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


from typing import Any, Optional

from sqlalchemy import Column, JSON, String, Float, Integer, Boolean
from sqlmodel import Field, SQLModel

SRD_SOURCE = "SRD 5.1 (CC-BY-4.0)"
# Content the player legally owns (purchased books) but that is NOT in the SRD.
# Stored as concise mechanical facts in our own wording, never verbatim prose.
OWNED_SOURCE = "Owned (non-SRD)"


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

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


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

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Race(SQLModel, table=True):
    """A playable race — the mechanical essentials for deterministic character
    creation. Seeded offline from the CC-BY 5e SRD (plus the Custom Lineage
    variant the world allows)."""
    __tablename__ = "rules_race"

    id: Optional[int] = Field(default=None, primary_key=True)

    index_slug: str = Field(sa_column=Column(String, nullable=False, unique=True, index=True))
    name: str = Field(sa_column=Column(String, nullable=False, index=True))

    # {"str": 2, "cha": 1} — fixed bonuses. Custom lineage stores {} and sets
    # choose_bonus below instead.
    ability_bonuses: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    # For choose-your-own bonus races: [2, 1] means "+2 to one ability and +1
    # to another of the player's choice". Empty/None for fixed races.
    choose_bonus: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    speed: int = Field(default=30, sa_column=Column(Integer))
    size: str = Field(default="Medium", sa_column=Column(String))
    darkvision: bool = Field(default=False)
    languages: Optional[str] = Field(default=None, sa_column=Column(String))
    # One-line trait summaries: ["Fey Ancestry: advantage vs charm...", ...]
    traits: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    description: Optional[str] = Field(default=None, sa_column=Column(String))

    # Flavor sub-species the player picks under this species (2024 model — no
    # ability bonuses, only trait/darkvision/speed differences). Each entry:
    # {"slug", "name", "traits": [str], "darkvision"?: bool, "speed"?: int,
    #  "label"?: str}. Empty/None for species without lineages.
    lineages: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    # If this species grants a feat choice at creation, the pool to draw from:
    # "origin" (any origin feat), "any" (any feat you qualify for), or None.
    feat_choice: Optional[str] = Field(default=None, sa_column=Column(String))
    # The label shown for the lineage picker ("Elven Lineage", "Gnomish
    # Subrace", "Draconic Ancestry"). None when there are no lineages.
    lineage_label: Optional[str] = Field(default=None, sa_column=Column(String))

    source: str = Field(default=SRD_SOURCE, sa_column=Column(String, index=True))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Feat(SQLModel, table=True):
    """A feat. SRD feats may be seeded from repo code; feats from owned books are
    ingested LOCALLY by ``rules/owned_ingest.py`` and never committed (see CLAUDE.md)."""
    __tablename__ = "rules_feat"

    id: Optional[int] = Field(default=None, primary_key=True)

    index_slug: str = Field(sa_column=Column(String, nullable=False, unique=True, index=True))
    name: str = Field(sa_column=Column(String, nullable=False, index=True))

    # "origin" | "general" | "fighting-style" | "epic-boon" (2024 categories);
    # 2014-era feats ingest as "general".
    category: str = Field(default="general", sa_column=Column(String, index=True))
    prerequisite: Optional[str] = Field(default=None, sa_column=Column(String))
    # Minimum character level to take it (origin=1, general=4, epic boon=19).
    min_level: int = Field(default=1, sa_column=Column(Integer))
    repeatable: bool = Field(default=False)
    # Mechanical benefit text (local-only when book-derived).
    benefit: Optional[str] = Field(default=None, sa_column=Column(String))

    source: str = Field(default=SRD_SOURCE, sa_column=Column(String, index=True))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class DndClass(SQLModel, table=True):
    """A character class (Fighter, Wizard, ...) — the mechanical essentials only."""
    __tablename__ = "rules_class"

    id: Optional[int] = Field(default=None, primary_key=True)

    index_slug: str = Field(sa_column=Column(String, nullable=False, unique=True, index=True))
    name: str = Field(sa_column=Column(String, nullable=False, index=True))

    hit_die: Optional[int] = Field(default=None, sa_column=Column(Integer))   # e.g. 6, 8, 10, 12
    primary_ability: Optional[str] = Field(default=None, sa_column=Column(String))
    # The label a class gives its subclass choice (e.g. "Arcane Tradition").
    subclass_label: Optional[str] = Field(default=None, sa_column=Column(String))
    # The level at which this class chooses its subclass (SRD: 1, 2, or 3).
    subclass_level: int = Field(default=3, sa_column=Column(Integer))
    spellcasting_ability: Optional[str] = Field(default=None, sa_column=Column(String))

    # Level-1 skill proficiencies: choose N from the options list.
    skill_choices_n: int = Field(default=2, sa_column=Column(Integer))
    skill_options: Optional[Any] = Field(default=None, sa_column=Column(JSON))   # ["Athletics", ...]

    saving_throws: Optional[Any] = Field(default=None, sa_column=Column(JSON))   # ["STR","CON"]
    description: Optional[str] = Field(default=None, sa_column=Column(String))

    source: str = Field(default=SRD_SOURCE, sa_column=Column(String, index=True))
    raw: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Subclass(SQLModel, table=True):
    """A subclass/archetype (Champion, Bladesinger, ...).

    Non-SRD subclasses the player owns (e.g. Bladesinger from Tasha's) are stored
    with ``source = OWNED_SOURCE`` and concise, self-authored feature summaries so
    the DM brain knows they exist without reproducing book prose.
    """
    __tablename__ = "rules_subclass"

    id: Optional[int] = Field(default=None, primary_key=True)

    index_slug: str = Field(sa_column=Column(String, nullable=False, unique=True, index=True))
    name: str = Field(sa_column=Column(String, nullable=False, index=True))

    class_name: str = Field(sa_column=Column(String, nullable=False, index=True))  # e.g. "Wizard"
    class_slug: Optional[str] = Field(default=None, sa_column=Column(String, index=True))

    # List of {"level": int, "name": str, "summary": str}
    features: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    description: Optional[str] = Field(default=None, sa_column=Column(String))

    source: str = Field(default=SRD_SOURCE, sa_column=Column(String, index=True))
    raw: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Item(SQLModel, table=True):
    """Equipment and magic items — the numbers economy/crafting/combat need.

    Covers weapons, armor, adventuring gear, tools, mounts, trade goods (from SRD
    Equipment) and magic items. ``cost_gp`` is normalized to gold pieces so pricing,
    selling, and crafting math is uniform.
    """
    __tablename__ = "rules_item"

    id: Optional[int] = Field(default=None, primary_key=True)

    index_slug: str = Field(sa_column=Column(String, nullable=False, unique=True, index=True))
    name: str = Field(sa_column=Column(String, nullable=False, index=True))

    # Broad bucket (weapon, armor, adventuring-gear, tools, mounts-and-vehicles,
    # trade-goods, magic-item, ...) and a finer type label.
    category: Optional[str] = Field(default=None, sa_column=Column(String, index=True))
    item_type: Optional[str] = Field(default=None, sa_column=Column(String))

    cost_gp: Optional[float] = Field(default=None, sa_column=Column(Float, index=True))
    weight: Optional[float] = Field(default=None, sa_column=Column(Float))

    # Weapon numbers
    damage_dice: Optional[str] = Field(default=None, sa_column=Column(String))
    damage_type: Optional[str] = Field(default=None, sa_column=Column(String))
    two_handed_damage_dice: Optional[str] = Field(default=None, sa_column=Column(String))
    range_normal: Optional[int] = Field(default=None, sa_column=Column(Integer))
    range_long: Optional[int] = Field(default=None, sa_column=Column(Integer))
    properties: Optional[Any] = Field(default=None, sa_column=Column(JSON))   # list[str]

    # Armor numbers
    armor_class_base: Optional[int] = Field(default=None, sa_column=Column(Integer))
    armor_dex_bonus: Optional[bool] = Field(default=None, sa_column=Column(Boolean))
    armor_max_dex_bonus: Optional[int] = Field(default=None, sa_column=Column(Integer))
    str_minimum: Optional[int] = Field(default=None, sa_column=Column(Integer))
    stealth_disadvantage: Optional[bool] = Field(default=None, sa_column=Column(Boolean))

    # Magic-item bits
    rarity: Optional[str] = Field(default=None, sa_column=Column(String, index=True))
    requires_attunement: bool = Field(default=False, sa_column=Column(Boolean))

    desc: Optional[str] = Field(default=None, sa_column=Column(String))

    source: str = Field(default=SRD_SOURCE, sa_column=Column(String, index=True))
    raw: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class SrdEntry(SQLModel, table=True):
    """Generic SRD reference row for the broad mechanics sweep.

    One flexible table backs many SRD categories (conditions, skills, damage-types,
    backgrounds, feats, races, subraces, traits, languages, weapon-properties,
    ability-scores, alignments, magic-schools, ...). Structured querying isn't needed
    for these the way it is for monsters/spells/items — the DM brain just needs the
    name + description on demand — so they share one table keyed by ``category:slug``.
    """
    __tablename__ = "rules_srd_entry"

    id: Optional[int] = Field(default=None, primary_key=True)

    # Composite natural key "category:index_slug" for idempotent upserts.
    entry_key: str = Field(sa_column=Column(String, nullable=False, unique=True, index=True))
    category: str = Field(sa_column=Column(String, nullable=False, index=True))
    index_slug: str = Field(sa_column=Column(String, nullable=False, index=True))
    name: str = Field(sa_column=Column(String, nullable=False, index=True))

    desc: Optional[str] = Field(default=None, sa_column=Column(String))
    data: Optional[Any] = Field(default=None, sa_column=Column(JSON))   # raw JSON

    source: str = Field(default=SRD_SOURCE, sa_column=Column(String, index=True))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
