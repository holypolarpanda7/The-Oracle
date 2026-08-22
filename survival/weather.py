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
#
# THE WORLD'S OWN BANDS COME FIRST. `eight_card_system.geo.climate_for` derives
# a place's band from its LATITUDE and produces seven of them — arctic,
# subarctic, cool temperate, temperate, warm temperate, subtropical, tropical —
# and this table knew three. `climate if climate in CLIMATES else "temperate"`
# never complains about a word it has not got, so **four of the seven silently
# came out temperate**: the subarctic never froze and the subtropics were never
# warm, everywhere in the world, every day of the year. Same shape of bug as
# `TERRAIN.get(name, TERRAIN["grassland"])` costing a sea crossing as a stroll
# over a meadow, in the module next door.
#
# `desert`, `coastal` and `mountain` are kept and are NOT bands — they are the
# TERRAIN axis, which arrived here because this module was written standalone.
# Nothing in the world produces them and nothing should: what a desert or a
# summit does to the sky is `placelore.WEATHER_BIAS`, applied on top of
# whatever band the latitude gives, so a mountain in the tropics and one in the
# north are not the same mountain. They stay for a caller that has only a word.
CLIMATES = ("arctic", "subarctic", "cool temperate", "temperate",
            "warm temperate", "subtropical", "tropical",
            "desert", "coastal", "mountain")

_TEMP_BANDS = ["frigid", "cold", "cool", "mild", "warm", "hot", "sweltering"]

# Base band index by (climate, season). 0=frigid .. 6=sweltering.
_CLIMATE_SEASON_BASE: Dict[str, Dict[str, int]] = {
    "arctic":         {"winter": 0, "spring": 1, "summer": 2, "autumn": 1},
    "subarctic":      {"winter": 0, "spring": 2, "summer": 3, "autumn": 2},
    "cool temperate": {"winter": 1, "spring": 2, "summer": 4, "autumn": 2},
    "temperate":      {"winter": 1, "spring": 3, "summer": 5, "autumn": 3},
    "warm temperate": {"winter": 2, "spring": 4, "summer": 5, "autumn": 4},
    "subtropical":    {"winter": 3, "spring": 4, "summer": 6, "autumn": 5},
    "tropical":       {"winter": 4, "spring": 5, "summer": 6, "autumn": 5},
    # The terrain axis, kept for a caller that has only a word. See above.
    "desert":         {"winter": 3, "spring": 5, "summer": 6, "autumn": 5},
    "coastal":        {"winter": 2, "spring": 3, "summer": 5, "autumn": 4},
    "mountain":       {"winter": 0, "spring": 2, "summer": 3, "autumn": 2},
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


def _bias(terrain: str) -> Dict:
    """What this country does to the sky. Empty where there is no world graph.

    Guarded, because `survival/` must stand up in a checkout with no world at
    all — the same reason `threads.people_vocabulary` imports lazily.
    """
    if not terrain:
        return {"temp": 0, "damp": 0.0, "wind": 0.0}
    try:
        from eight_card_system.placelore import weather_bias_of
    except Exception:
        return {"temp": 0, "damp": 0.0, "wind": 0.0}
    return weather_bias_of(terrain)


#: What a day turns into when the country makes it wetter, by temperature.
#: A marsh does not make it SNOW harder — it closes in — so the damp ladder is
#: read off the band rather than off the precipitation it is replacing.
_WETTER = {"clear": ("fog", "light rain"), "fog": ("light rain", "fog"),
           "light rain": ("heavy rain", "heavy rain"),
           "heavy rain": ("heavy rain", "heavy rain"),
           "snow": ("blizzard", "snow"), "blizzard": ("blizzard", "blizzard")}
_DRIER = {"blizzard": "snow", "heavy rain": "light rain", "light rain": "clear",
          "snow": "clear", "fog": "clear", "clear": "clear"}


def _damp(rng: random.Random, precip: str, damp: float, band_idx: int) -> str:
    """Push a day one rung wetter or drier, as often as the country says."""
    if not damp:
        return precip
    if rng.random() >= abs(damp):
        return precip
    if damp > 0:
        pair = _WETTER.get(precip)
        return (pair[0] if band_idx <= 1 or band_idx >= 4 else pair[1]) \
            if pair else precip
    return _DRIER.get(precip, precip)


def _wind_weights(wind: float) -> list[float]:
    """Re-weight the wind table toward (or away from) the exposed end.

    A weighting rather than a re-roll, so the whole distribution moves: a coast
    is not "sometimes suddenly a gale", it is windier on an ordinary day.
    """
    k = 1.0 + max(-0.9, min(2.0, wind))
    return [5.0 / k, 4.0, 2.0 * k, 1.0 * k * k]


def generate_weather(world_day: int, *, climate: str = "temperate", month: int = 1,
                    terrain: str = "") -> Dict:
    """Deterministic weather for a day. Returns temperature band, precip, and wind.

    ``terrain`` is the country it is happening OVER, in the world graph's own
    vocabulary (see ``eight_card_system.placelore.WEATHER_BIAS``). Without it a
    mountain top and the marsh in the valley below it — same latitude, same
    climate band, same day — got identical weather every time, and the fog that
    is most of what a marsh IS arrived there no more often than on a ploughed
    field. Optional and defaulting to no bias, so every caller written before
    this keeps the weather it had.
    """
    climate = climate if climate in CLIMATES else "temperate"
    season = season_for_month(month)
    rng = random.Random(_seed(world_day, climate))
    bias = _bias(terrain)

    base = _CLIMATE_SEASON_BASE[climate][season]
    band_idx = max(0, min(len(_TEMP_BANDS) - 1,
                          base + rng.randint(-1, 1) + int(bias["temp"])))
    temperature = _TEMP_BANDS[band_idx]

    # Precipitation weighted by temperature: cold -> snow, hot -> mostly clear.
    if band_idx <= 1:
        precip = rng.choice(["clear", "snow", "snow", "blizzard", "fog"])
    elif band_idx >= 5:
        precip = rng.choice(["clear", "clear", "clear", "light rain", "fog"])
    else:
        precip = rng.choice(_PRECIP[:3] + ["clear", "fog"])
    precip = _damp(rng, precip, float(bias["damp"]), band_idx)

    wind = rng.choices(_WIND, weights=_wind_weights(float(bias["wind"])))[0]

    return {
        "world_day": int(world_day),
        "climate": climate,
        "terrain": terrain or "",
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
