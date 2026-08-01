"""Loot: what a piece of gear IS beyond its catalog row.

The rules catalog says a longsword deals 1d8 slashing. This package says which
longsword — the keen one that bites deeper, the one that drinks light, the one
a smith can still improve. See ``affixes.py``.
"""
from .affixes import (
    AFFIXES,
    Affix,
    affix_by_slug,
    describe_affixes,
    display_name,
    mechanical_bonuses,
    roll_affixes,
    slots_for_rarity,
    temper_cost_gp,
    temper_swap,
)

__all__ = [
    "AFFIXES", "Affix", "affix_by_slug", "describe_affixes", "display_name",
    "mechanical_bonuses", "roll_affixes", "slots_for_rarity", "temper_cost_gp",
    "temper_swap",
]
