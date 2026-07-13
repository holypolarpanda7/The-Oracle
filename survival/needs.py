"""Rations & water consumption and the deprivation -> exhaustion pipeline.

State tracked on the character (passed in / returned as a dict):
  * rations               days of food remaining
  * water                 days of water remaining
  * days_without_food     consecutive days short on food
  * days_without_water    consecutive days short on water
  * exhaustion            current exhaustion level

One "survival day" consumes a day of food and water if available; running out
accrues deprivation, and once the config grace period is exceeded the character
gains a level of exhaustion. Water deprivation bites faster than food.
"""
from __future__ import annotations

from typing import Dict

from game_config import get_config

from .exhaustion import add_exhaustion, clamp_level


def consume_day(
    *,
    rations: int,
    water: int,
    days_without_food: int = 0,
    days_without_water: int = 0,
    exhaustion: int = 0,
) -> Dict:
    """Advance one day of provisions. Returns the updated survival state."""
    cfg = get_config().survival
    notes = []
    exhaustion = clamp_level(exhaustion)
    gained = 0

    # --- food ---
    if rations > 0:
        rations -= 1
        days_without_food = 0
    else:
        days_without_food += 1
        if days_without_food > cfg.days_without_food_before_exhaustion:
            res = add_exhaustion(exhaustion, 1)
            exhaustion = res["level"]
            gained += 1
            notes.append(f"Starving (day {days_without_food}): +1 exhaustion.")
        else:
            notes.append(f"No food (day {days_without_food}).")

    # --- water (harsher) ---
    if water > 0:
        water -= 1
        days_without_water = 0
    else:
        days_without_water += 1
        if days_without_water > cfg.days_without_water_before_exhaustion:
            res = add_exhaustion(exhaustion, 1)
            exhaustion = res["level"]
            gained += 1
            notes.append(f"Dehydrated (day {days_without_water}): +1 exhaustion.")
        else:
            notes.append(f"No water (day {days_without_water}).")

    return {
        "rations": rations,
        "water": water,
        "days_without_food": days_without_food,
        "days_without_water": days_without_water,
        "exhaustion": exhaustion,
        "exhaustion_gained": gained,
        "dead": exhaustion >= 6,
        "notes": notes or ["Fed and watered."],
        "summary": (
            f"Food {rations}d / Water {water}d remaining"
            + (f"; +{gained} exhaustion (now {exhaustion})" if gained else "")
        ),
    }


def forced_march_dc(hours_travelled: int) -> Dict:
    """Return the Con save DC for marching beyond the daily limit, or None."""
    cfg = get_config().survival
    if hours_travelled <= cfg.forced_march_hours:
        return {"required": False, "dc": None}
    extra = hours_travelled - cfg.forced_march_hours
    return {
        "required": True,
        "dc": cfg.forced_march_dc,
        "extra_hours": extra,
        "note": (
            f"Forced march: {extra} hour(s) past {cfg.forced_march_hours}. "
            f"Each creature makes a DC {cfg.forced_march_dc} Con save or gains 1 exhaustion."
        ),
    }
