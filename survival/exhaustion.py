"""The exhaustion ladder (6 levels) and its recovery.

The SRD tracks exhaustion as six cumulative levels. We keep the *level* as an
integer on the character; this module turns a level into its mechanical effects
and handles gaining/losing levels (a long rest with food & water removes one).
"""
from __future__ import annotations

from typing import Dict, List

from game_config import get_config

MAX_EXHAUSTION = 6

# Cumulative effects: level N includes every effect at or below N.
_LEVEL_EFFECTS: Dict[int, str] = {
    1: "Disadvantage on ability checks",
    2: "Speed halved",
    3: "Disadvantage on attack rolls and saving throws",
    4: "Hit point maximum halved",
    5: "Speed reduced to 0",
    6: "Death",
}


def clamp_level(level: int) -> int:
    return max(0, min(MAX_EXHAUSTION, int(level)))


def effects_for_level(level: int) -> List[str]:
    """All active effects at a given exhaustion level (cumulative)."""
    level = clamp_level(level)
    return [_LEVEL_EFFECTS[n] for n in range(1, level + 1)]


def add_exhaustion(level: int, amount: int = 1) -> Dict:
    """Increase exhaustion. Returns the new level, whether it's lethal, and effects."""
    new_level = clamp_level(level + amount)
    return {
        "level": new_level,
        "dead": new_level >= MAX_EXHAUSTION,
        "effects": effects_for_level(new_level),
        "changed": new_level - clamp_level(level),
    }


def remove_exhaustion(level: int, amount: int = 1) -> Dict:
    new_level = clamp_level(level - amount)
    return {
        "level": new_level,
        "dead": False,
        "effects": effects_for_level(new_level),
        "changed": new_level - clamp_level(level),
    }


def hp_max_multiplier(level: int) -> float:
    """0.5 once exhaustion reaches level 4+, else 1.0 (for max-HP halving)."""
    return 0.5 if clamp_level(level) >= 4 else 1.0


def speed_multiplier(level: int) -> float:
    lvl = clamp_level(level)
    if lvl >= 5:
        return 0.0
    if lvl >= 2:
        return 0.5
    return 1.0


def describe(level: int) -> str:
    lvl = clamp_level(level)
    if lvl == 0:
        return "No exhaustion."
    effects = "; ".join(effects_for_level(lvl))
    return f"Exhaustion {lvl}/{MAX_EXHAUSTION}: {effects}."


def long_rest_recovery(level: int, *, ate_and_drank: bool) -> Dict:
    """A long rest removes one exhaustion level if the config's food rule is met."""
    needs_food = get_config().survival.exhaustion_recovery_needs_food
    if needs_food and not ate_and_drank:
        return {
            "level": clamp_level(level),
            "recovered": 0,
            "note": "No exhaustion recovered — the character did not eat and drink.",
        }
    result = remove_exhaustion(level, 1)
    result["recovered"] = -result["changed"]
    result["note"] = "Recovered one exhaustion level from a long rest."
    return result
