"""The Exhaustion condition (2024 rules) and its recovery.

2024 Exhaustion (from SRD 5.2, CC-BY-4.0) replaced the 2014 six-effect ladder
with a single scaling penalty: while you have Exhaustion, every D20 Test is
reduced by 2 x your Exhaustion level, and your Speed is reduced by 5 ft x your
level. There are still six levels; level 6 is death. We keep the *level* as an
integer on the character; this module turns a level into its effects and handles
gaining/losing levels (a long rest removes one).
"""
from __future__ import annotations

from typing import Dict, List

from game_config import get_config

MAX_EXHAUSTION = 6


def clamp_level(level: int) -> int:
    return max(0, min(MAX_EXHAUSTION, int(level)))


def d20_penalty(level: int) -> int:
    """2024: every D20 Test (ability checks, attack rolls, saving throws) is
    reduced by 2 x the Exhaustion level. Returned as a NEGATIVE number to add to
    a roll. Does NOT apply to save DCs, damage, or passive scores."""
    return -2 * clamp_level(level)


def speed_reduction_feet(level: int) -> int:
    """2024: Speed is reduced by 5 ft x the Exhaustion level (positive feet)."""
    return 5 * clamp_level(level)


def effects_for_level(level: int) -> List[str]:
    """The active effects at a given Exhaustion level (2024 scaling model)."""
    lvl = clamp_level(level)
    if lvl <= 0:
        return []
    if lvl >= MAX_EXHAUSTION:
        return ["Death"]
    return [
        f"-{2 * lvl} to all D20 Tests (ability checks, attack rolls, saving throws)",
        f"Speed reduced by {5 * lvl} ft",
    ]


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


def describe(level: int) -> str:
    lvl = clamp_level(level)
    if lvl == 0:
        return "No exhaustion."
    if lvl >= MAX_EXHAUSTION:
        return f"Exhaustion {lvl}/{MAX_EXHAUSTION}: Death."
    return (f"Exhaustion {lvl}/{MAX_EXHAUSTION}: -{2 * lvl} to all D20 Tests "
            f"(checks, attacks, saves); Speed -{5 * lvl} ft.")


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
