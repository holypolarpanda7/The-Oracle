"""
Rules reference — structured SRD game data (monsters, spells) for The Oracle.

Seeded from the open, CC-BY-4.0 5e SRD dataset so the DM brain and internal dice
roller have exact numbers. Prose rules (RAG) are a separate, later layer.

    from rules import ingest_srd, RulesLibrary
    ingest_srd()
    lib = RulesLibrary()
    goblin = lib.get_monster("goblin")
"""
from .models import Monster, Spell, SRD_SOURCE
from .ingest import ingest_srd, get_engine
from .query import (
    RulesLibrary,
    ability_modifier,
    format_monster_brief,
    format_spell_brief,
)

__all__ = [
    "Monster",
    "Spell",
    "SRD_SOURCE",
    "ingest_srd",
    "get_engine",
    "RulesLibrary",
    "ability_modifier",
    "format_monster_brief",
    "format_spell_brief",
]
