"""Environmental hazards: extreme cold/heat, strong wind, frigid water.

Each hazard exposes the Constitution save DC (from ``config.survival``) and the
consequence of failure. Callers roll the save with the dice layer and pass the
result to ``resolve`` to get the mechanical outcome (usually +1 exhaustion).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from game_config import get_config


def cold_hazard(*, has_cold_gear: bool = False) -> Dict:
    cfg = get_config().survival
    if has_cold_gear:
        return {"hazard": "extreme_cold", "save_required": False,
                "note": "Cold-weather gear negates extreme cold."}
    return {
        "hazard": "extreme_cold",
        "save_required": True,
        "ability": "constitution",
        "dc": cfg.extreme_cold_dc,
        "period": "per hour",
        "on_fail": "gain 1 level of exhaustion",
    }


def heat_hazard(*, hours_exposed: int = 1, has_water: bool = True) -> Dict:
    cfg = get_config().survival
    dc = cfg.extreme_heat_dc_base + cfg.extreme_heat_dc_per_hour * max(0, hours_exposed - 1)
    note = None
    if not has_water:
        dc += 5
        note = "No water: disadvantage/DC increase applies."
    return {
        "hazard": "extreme_heat",
        "save_required": True,
        "ability": "constitution",
        "dc": dc,
        "period": f"after {hours_exposed}h",
        "on_fail": "gain 1 level of exhaustion",
        "note": note,
    }


def wind_hazard() -> Dict:
    cfg = get_config().survival
    return {
        "hazard": "strong_wind",
        "save_required": False,
        "disadvantage_ranged": cfg.strong_wind_ranged_disadvantage,
        "note": "Disadvantage on ranged weapon attacks and Perception checks relying on hearing.",
    }


def frigid_water_hazard(*, minutes_immersed: int = 1, con_score: int = 10) -> Dict:
    cfg = get_config().survival
    # A creature can tolerate roughly (1 + Con modifier) minutes before saving.
    from dice import ability_modifier

    grace = max(1, 1 + ability_modifier(con_score))
    return {
        "hazard": "frigid_water",
        "save_required": minutes_immersed > grace,
        "ability": "constitution",
        "dc": cfg.frigid_water_dc,
        "grace_minutes": grace,
        "period": "per minute after grace",
        "on_fail": "gain 1 level of exhaustion",
    }


def resolve(hazard: Dict, *, save_succeeded: bool) -> Dict:
    """Turn a rolled save into a consequence for an exhaustion-causing hazard."""
    if not hazard.get("save_required"):
        return {"exhaustion_delta": 0, "note": hazard.get("note", "No save required.")}
    if save_succeeded:
        return {"exhaustion_delta": 0, "note": f"Resisted {hazard['hazard']}."}
    return {"exhaustion_delta": 1, "note": f"Failed vs {hazard['hazard']}: +1 exhaustion."}


def hazards_from_weather(weather: Dict, *, has_cold_gear: bool = False,
                         has_water: bool = True) -> List[Dict]:
    """Build the concrete hazard specs implied by a weather dict."""
    from .weather import active_hazard_tags

    specs: List[Dict] = []
    for tag in active_hazard_tags(weather):
        if tag == "extreme_cold":
            specs.append(cold_hazard(has_cold_gear=has_cold_gear))
        elif tag == "extreme_heat":
            specs.append(heat_hazard(has_water=has_water))
        elif tag == "strong_wind":
            specs.append(wind_hazard())
        elif tag == "heavy_precipitation":
            specs.append({
                "hazard": "heavy_precipitation", "save_required": False,
                "note": "Lightly obscured area; disadvantage on Perception checks relying on sight.",
            })
    return specs
