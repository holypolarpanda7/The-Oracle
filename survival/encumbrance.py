"""Carrying capacity and encumbrance (a PHB variant, gated by config).

Capacity derives from Strength (Str x 15 lb). The variant rules add two
thresholds: encumbered (Str x 5) and heavily encumbered (Str x 10).
"""
from __future__ import annotations

from typing import Dict

from game_config import get_config


def carrying_capacity(str_score: int) -> float:
    return float(str_score) * 15.0


def encumbrance_status(str_score: int, weight_carried: float) -> Dict:
    """Classify a load. Respects ``config.encumbrance.variant`` (off/standard/variant)."""
    variant = get_config().encumbrance.variant
    capacity = carrying_capacity(str_score)
    over_capacity = weight_carried > capacity

    if variant == "off":
        return {
            "variant": "off",
            "capacity": capacity,
            "weight": weight_carried,
            "status": "overloaded" if over_capacity else "ok",
            "speed_penalty_ft": 0,
            "note": "Encumbrance variant is off; only raw capacity is checked.",
        }

    enc = float(str_score) * 5.0
    heavy = float(str_score) * 10.0
    if weight_carried > heavy:
        status, penalty = "heavily_encumbered", 20
    elif weight_carried > enc:
        status, penalty = "encumbered", 10
    else:
        status, penalty = "unencumbered", 0

    return {
        "variant": "variant",
        "capacity": capacity,
        "encumbered_at": enc,
        "heavily_encumbered_at": heavy,
        "weight": weight_carried,
        "status": status,
        "speed_penalty_ft": penalty,
        "over_capacity": over_capacity,
        "note": {
            "unencumbered": "No penalty.",
            "encumbered": "Speed -10 ft.",
            "heavily_encumbered": "Speed -20 ft; disadvantage on Str/Dex/Con checks & attacks.",
        }[status],
    }
