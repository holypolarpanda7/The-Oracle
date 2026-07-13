"""
Internal dice roller for The Oracle's DM brain.

Lets the DM resolve rolls itself (using stored character/monster numbers) and
narrate results inline — a single-voice UX that replaces the Avrae copy-paste flow.

    from dice import roll, ability_check, attack_roll, damage_roll
    roll("2d6+3")
    ability_check(5, dc=15, advantage=True, label="Stealth")
    attack_roll(4, target_ac=13)
    damage_roll("1d6+2", crit=True)
"""
from .roller import RollResult, roll, double_dice, contains_dice
from .mechanics import (
    CheckResult,
    AttackResult,
    ability_check,
    saving_throw,
    attack_roll,
    damage_roll,
    ability_modifier,
    proficiency_bonus_for_level,
)

__all__ = [
    "RollResult",
    "roll",
    "double_dice",
    "contains_dice",
    "CheckResult",
    "AttackResult",
    "ability_check",
    "saving_throw",
    "attack_roll",
    "damage_roll",
    "ability_modifier",
    "proficiency_bonus_for_level",
]
