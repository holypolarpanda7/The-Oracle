"""Overland travel: pace, time/distance, navigation, and foraging.

Pace speeds and daily hours come from ``config.survival``. Terrain modifiers make
wild country slower to cross, harder to navigate, and leaner to forage.
"""
from __future__ import annotations

from typing import Dict, Optional

from game_config import get_config

# Per-terrain modifiers: travel speed factor, navigation DC bump, forage DC bump.
TERRAIN = {
    "road":      {"speed": 1.0, "nav_dc": -5, "forage_dc": 2, "forage_base": 8},
    "grassland": {"speed": 1.0, "nav_dc": 0,  "forage_dc": 0, "forage_base": 8},
    "forest":    {"speed": 0.75, "nav_dc": 5, "forage_dc": 0, "forage_base": 10},
    "hills":     {"speed": 0.75, "nav_dc": 2, "forage_dc": 2, "forage_base": 12},
    "mountains": {"speed": 0.5, "nav_dc": 8,  "forage_dc": 5, "forage_base": 15},
    "swamp":     {"speed": 0.5, "nav_dc": 8,  "forage_dc": 2, "forage_base": 12},
    "desert":    {"speed": 0.75, "nav_dc": 10, "forage_dc": 10, "forage_base": 20},
    "arctic":    {"speed": 0.5, "nav_dc": 10, "forage_dc": 10, "forage_base": 20},
    "urban":     {"speed": 1.0, "nav_dc": -10, "forage_dc": -5, "forage_base": 5},
}

PACES = ("fast", "normal", "slow")


def _terrain(terrain: str) -> Dict:
    return TERRAIN.get(terrain, TERRAIN["grassland"])


def travel(distance_miles: float, *, pace: str = "normal", terrain: str = "grassland") -> Dict:
    """Time to cover a distance at a pace over terrain. Returns hours & days."""
    cfg = get_config().survival
    pace = pace if pace in PACES else "normal"
    mph = cfg.pace_miles_per_hour[pace] * _terrain(terrain)["speed"]
    if mph <= 0:
        mph = 0.5
    hours = distance_miles / mph
    days = hours / cfg.travel_hours_per_day
    effects = {
        "fast": "-5 penalty to passive Perception; can't stealth.",
        "normal": "Normal travel.",
        "slow": "Can use stealth; +bonus to noticing things.",
    }[pace]
    return {
        "distance_miles": distance_miles,
        "pace": pace,
        "terrain": terrain,
        "miles_per_hour": round(mph, 2),
        "hours": round(hours, 2),
        "days": round(days, 2),
        "pace_effect": effects,
        "summary": f"{distance_miles} mi of {terrain} at {pace} pace ~ {round(hours, 1)}h.",
    }


def navigation_dc(terrain: str = "grassland") -> Dict:
    """Wisdom (Survival) DC to avoid becoming lost in the given terrain."""
    cfg = get_config().survival
    dc = cfg.navigation_dc + _terrain(terrain)["nav_dc"]
    return {
        "ability": "wisdom", "skill": "survival",
        "dc": max(5, dc), "terrain": terrain,
        "on_fail": "The party becomes lost and travels off-course.",
    }


def forage(terrain: str = "grassland", *, foragers: int = 1) -> Dict:
    """Foraging DC and the food/water yield on success (scaled by config)."""
    cfg = get_config().survival
    t = _terrain(terrain)
    dc = max(5, t["forage_base"] + t["forage_dc"] + (cfg.forage_dc - 10))
    # Yield per successful forager, scaled by config multiplier.
    base_food = 1 + max(0, (20 - dc) // 5)
    base_water = 1 + max(0, (20 - dc) // 5)
    food = round(base_food * cfg.forage_yield_multiplier * foragers, 1)
    water = round(base_water * cfg.forage_yield_multiplier * foragers, 1)
    return {
        "ability": "wisdom", "skill": "survival",
        "dc": dc, "terrain": terrain, "foragers": foragers,
        "food_on_success": food, "water_on_success": water,
        "note": f"On a success each forager finds ~{food} lb food and ~{water} gal water.",
    }
