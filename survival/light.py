"""Light sources and vision. Torches burn down; darkness has teeth.

Burn times come from ``config.survival``. The vision helper resolves what a
creature can effectively see given the ambient light level and darkvision.
"""
from __future__ import annotations

from typing import Dict, Optional

from game_config import get_config

# radius in feet (bright, dim). Minutes are pulled from config where applicable.
_SOURCES = {
    "torch":     {"bright": 20, "dim": 40, "minutes_key": "torch_minutes"},
    "lantern":   {"bright": 30, "dim": 60, "minutes_key": "lantern_minutes"},
    "candle":    {"bright": 5, "dim": 10, "minutes_key": "candle_minutes"},
    "campfire":  {"bright": 20, "dim": 40, "minutes": 480},
    "everburning": {"bright": 20, "dim": 40, "minutes": None},  # magical, never runs out
}

LIGHT_LEVELS = ("bright", "dim", "dark")


def light_sources() -> Dict:
    return _SOURCES


def source_spec(kind: str) -> Optional[Dict]:
    spec = _SOURCES.get(kind)
    if not spec:
        return None
    cfg = get_config().survival
    minutes = spec.get("minutes")
    if "minutes_key" in spec:
        minutes = getattr(cfg, spec["minutes_key"])
    return {
        "kind": kind,
        "bright_radius": spec["bright"],
        "dim_radius": spec["dim"],
        "minutes": minutes,  # None = never runs out
    }


def burn(kind: str, minutes_remaining: Optional[int], minutes_elapsed: int) -> Dict:
    """Advance a lit source. Returns remaining fuel and whether it went out."""
    spec = source_spec(kind)
    if not spec:
        return {"error": f"Unknown light source '{kind}'."}
    if spec["minutes"] is None:
        return {"kind": kind, "minutes_remaining": None, "went_out": False,
                "note": f"{kind} is inexhaustible."}
    if minutes_remaining is None:
        minutes_remaining = spec["minutes"]
    remaining = max(0, int(minutes_remaining) - int(minutes_elapsed))
    return {
        "kind": kind,
        "minutes_remaining": remaining,
        "went_out": remaining <= 0,
        "note": (f"{kind} sputters out." if remaining <= 0
                 else f"{kind}: {remaining} min of fuel left."),
    }


def effective_vision(light_level: str, *, has_darkvision: bool = False,
                     darkvision_ft: int = 60) -> Dict:
    """What a creature effectively perceives at a light level."""
    level = light_level if light_level in LIGHT_LEVELS else "bright"
    if level == "bright":
        return {"sees": "normally", "perception_disadvantage": False}
    if level == "dim":
        # Dim light is lightly obscured -> disadvantage on sight Perception.
        if has_darkvision:
            return {"sees": "normally (darkvision treats dim as bright)",
                    "perception_disadvantage": False}
        return {"sees": "lightly obscured", "perception_disadvantage": True}
    # dark
    if has_darkvision:
        return {"sees": f"dim within {darkvision_ft} ft (darkvision)",
                "perception_disadvantage": True,
                "note": "Beyond darkvision range the creature is effectively blinded."}
    return {"sees": "blinded", "perception_disadvantage": True,
            "note": "Attacks vs unseen creatures have disadvantage; attacks against you have advantage."}
