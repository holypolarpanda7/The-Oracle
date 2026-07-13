"""Short and long rests: hit-dice healing and resource/exhaustion recovery.

Rest *durations* come from ``config.rest`` (the gritty variant makes a short rest
8h and a long rest a week). Hit points and hit dice are tracked on the character.
"""
from __future__ import annotations

from typing import Dict, Optional

from dice import roll as dice_roll

from game_config import get_config

from .exhaustion import long_rest_recovery


def _die_faces(hit_die: str) -> int:
    return int(str(hit_die).lower().lstrip("d"))


def short_rest(
    *,
    current_hp: int,
    max_hp: int,
    hit_die: str,
    hit_dice_remaining: int,
    con_mod: int,
    spend: int = 1,
    rng=None,
) -> Dict:
    """Spend up to ``spend`` hit dice to heal. Returns updated HP & dice."""
    if hit_dice_remaining <= 0 or spend <= 0:
        return {
            "current_hp": current_hp,
            "hit_dice_remaining": hit_dice_remaining,
            "healed": 0,
            "rolls": [],
            "note": "No hit dice available to spend.",
            "rest_hours": get_config().rest.short_rest_hours,
        }

    spend = min(spend, hit_dice_remaining)
    faces = _die_faces(hit_die)
    healed = 0
    rolls = []
    for _ in range(spend):
        r = dice_roll(f"1d{faces}", rng=rng)
        gain = max(0, r.total + con_mod)
        healed += gain
        rolls.append({"die": r.total, "con": con_mod, "gain": gain})

    new_hp = min(max_hp, current_hp + healed)
    return {
        "current_hp": new_hp,
        "hit_dice_remaining": hit_dice_remaining - spend,
        "healed": new_hp - current_hp,
        "dice_spent": spend,
        "rolls": rolls,
        "rest_hours": get_config().rest.short_rest_hours,
        "note": f"Spent {spend} hit die(s), healed {new_hp - current_hp} HP.",
    }


def long_rest(
    *,
    current_hp: int,
    max_hp: int,
    hit_dice_total: int,
    hit_dice_remaining: int,
    exhaustion: int = 0,
    ate_and_drank: bool = True,
) -> Dict:
    """Full HP, recover half your hit dice (min 1), and shed one exhaustion level."""
    regained_dice = max(1, hit_dice_total // 2)
    new_remaining = min(hit_dice_total, hit_dice_remaining + regained_dice)

    exh = long_rest_recovery(exhaustion, ate_and_drank=ate_and_drank)

    return {
        "current_hp": max_hp,
        "hp_restored": max_hp - current_hp,
        "hit_dice_remaining": new_remaining,
        "hit_dice_regained": new_remaining - hit_dice_remaining,
        "exhaustion": exh["level"],
        "exhaustion_recovered": exh.get("recovered", 0),
        "rest_hours": get_config().rest.long_rest_hours,
        "note": (
            f"Long rest: HP restored to {max_hp}, "
            f"{new_remaining - hit_dice_remaining} hit die(s) back. {exh['note']}"
        ),
    }
