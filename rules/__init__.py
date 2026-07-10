"""
Rules reference — structured SRD game data (monsters, spells) for The Oracle.

Seeded from the open, CC-BY-4.0 5e SRD dataset so the DM brain and internal dice
roller have exact numbers. Prose rules (RAG) are a separate, later layer.

    from rules import ingest_srd, RulesLibrary
    ingest_srd()
    lib = RulesLibrary()
    goblin = lib.get_monster("goblin")
"""
from .models import Monster, Spell, DndClass, Subclass, SRD_SOURCE, OWNED_SOURCE
from .ingest import ingest_srd, seed_classes_and_subclasses, get_engine
from .query import (
    RulesLibrary,
    ability_modifier,
    format_monster_brief,
    format_spell_brief,
)
from .leveling import level_up_report, asi_at_level, average_hp_gain

__all__ = [
    "Monster",
    "Spell",
    "DndClass",
    "Subclass",
    "SRD_SOURCE",
    "OWNED_SOURCE",
    "ingest_srd",
    "seed_classes_and_subclasses",
    "get_engine",
    "RulesLibrary",
    "ability_modifier",
    "format_monster_brief",
    "format_spell_brief",
    "level_up_report",
    "asi_at_level",
    "average_hp_gain",
]
