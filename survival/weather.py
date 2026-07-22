"""A lightweight, deterministic weather model tied to the world calendar.

Weather is generated from ``(world_day, climate)`` with a stable seed, so the same
day in the same region always produces the same weather (reproducible, no storage
needed). Season is derived from the world-calendar month. The output feeds
``environment.py`` to decide which hazards are in play.
"""
from __future__ import annotations

import random
from typing import Dict, List

# Region climates and their seasonal temperature bias (index into _TEMP_BANDS).
CLIMATES = ("temperate", "arctic", "desert", "coastal", "tropical", "mountain")

_TEMP_BANDS = ["frigid", "cold", "cool", "mild", "warm", "hot", "sweltering"]

# Base band index by (climate, season). 0=frigid .. 6=sweltering.
_CLIMATE_SEASON_BASE: Dict[str, Dict[str, int]] = {
    "temperate": {"winter": 1, "spring": 3, "summer": 5, "autumn": 3},
    "arctic":    {"winter": 0, "spring": 1, "summer": 2, "autumn": 1},
    "desert":    {"winter": 3, "spring": 5, "summer": 6, "autumn": 5},
    "coastal":   {"winter": 2, "spring": 3, "summer": 5, "autumn": 4},
    "tropical":  {"winter": 4, "spring": 5, "summer": 6, "autumn": 5},
    "mountain":  {"winter": 0, "spring": 2, "summer": 3, "autumn": 2},
}

_PRECIP = ["clear", "light rain", "heavy rain", "snow", "blizzard", "fog"]
_WIND = ["calm", "light breeze", "strong wind", "gale"]


def season_for_month(month: int) -> str:
    """World-calendar month (1..12) -> season."""
    m = ((int(month) - 1) % 12) + 1
    if m in (12, 1, 2):
        return "winter"
    if m in (3, 4, 5):
        return "spring"
    if m in (6, 7, 8):
        return "summer"
    return "autumn"


def _seed(world_day: int, climate: str) -> int:
    idx = CLIMATES.index(climate) if climate in CLIMATES else 0
    return int(world_day) * 1000003 + idx * 97 + 7


def generate_weather(world_day: int, *, climate: str = "temperate", month: int = 1) -> Dict:
    """Deterministic weather for a day. Returns temperature band, precip, and wind."""
    climate = climate if climate in CLIMATES else "temperate"
    season = season_for_month(month)
    rng = random.Random(_seed(world_day, climate))

    base = _CLIMATE_SEASON_BASE[climate][season]
    band_idx = max(0, min(len(_TEMP_BANDS) - 1, base + rng.randint(-1, 1)))
    temperature = _TEMP_BANDS[band_idx]

    # Precipitation weighted by temperature: cold -> snow, hot -> mostly clear.
    if band_idx <= 1:
        precip = rng.choice(["clear", "snow", "snow", "blizzard", "fog"])
    elif band_idx >= 5:
        precip = rng.choice(["clear", "clear", "clear", "light rain", "fog"])
    else:
        precip = rng.choice(_PRECIP[:3] + ["clear", "fog"])

    wind = rng.choices(_WIND, weights=[5, 4, 2, 1])[0]

    return {
        "world_day": int(world_day),
        "climate": climate,
        "season": season,
        "temperature": temperature,
        "temperature_index": band_idx,
        "precipitation": precip,
        "wind": wind,
        "summary": f"{temperature.title()}, {precip}, {wind} ({season}).",
    }


def active_hazard_tags(weather: Dict) -> List[str]:
    """Which environmental hazards a weather dict implies (see environment.py)."""
    tags: List[str] = []
    if weather["temperature_index"] <= 1:
        tags.append("extreme_cold")
    if weather["temperature_index"] >= 6:
        tags.append("extreme_heat")
    if weather["wind"] in ("strong wind", "gale"):
        tags.append("strong_wind")
    if weather["precipitation"] in ("heavy rain", "snow", "blizzard", "fog"):
        tags.append("heavy_precipitation")
    return tags
