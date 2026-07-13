"""Lifestyle upkeep costs, driven entirely by ``game_config``.

Lifestyle tiers follow the SRD downtime table (wretched .. aristocratic). The
per-day gold cost lives in ``GameConfig.economy.lifestyle_daily_gp`` and is
scaled by ``lifestyle_cost_multiplier`` (difficulty presets move this knob).
"""
from __future__ import annotations

from typing import Dict, List

from game_config import get_config

from .currency import gp_to_cp


def lifestyle_tiers() -> List[str]:
    return list(get_config().economy.lifestyle_daily_gp.keys())


def daily_cost_gp(tier: str) -> float:
    """Effective per-day upkeep in gp for a lifestyle tier, after multipliers."""
    econ = get_config().economy
    base = econ.lifestyle_daily_gp.get(tier)
    if base is None:
        raise ValueError(
            f"Unknown lifestyle tier '{tier}'. Options: {', '.join(lifestyle_tiers())}"
        )
    return round(base * econ.lifestyle_cost_multiplier, 4)


def cost_for_days(tier: str, days: int) -> Dict[str, int | float]:
    """Total upkeep for maintaining ``tier`` for ``days`` days."""
    if days < 0:
        raise ValueError("days must be >= 0")
    per_day = daily_cost_gp(tier)
    total_gp = round(per_day * days, 4)
    return {
        "tier": tier,
        "days": days,
        "per_day_gp": per_day,
        "total_gp": total_gp,
        "total_cp": gp_to_cp(total_gp),
    }
