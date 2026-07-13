"""Downtime activity resolution.

Everything tunable routes through ``game_config``:
  * lifestyle upkeep   -> ``economy.lifestyle_daily_gp`` x ``lifestyle_cost_multiplier``
  * crafting rate/cost -> ``crafting.gp_per_day``, ``materials_ratio``, ``progress_multiplier``

Resolvers return plain dicts. They do NOT advance the world calendar or write to
the DB themselves — the backend does that (via the world graph + economy tables)
so day-keeping stays in one place.
"""
from __future__ import annotations

from typing import Dict, Optional

from game_config import get_config

from .currency import gp_to_cp
from .lifestyle import cost_for_days

# Downtime activities the resolver understands.
ACTIVITIES = (
    "crafting",
    "profession",
    "recuperating",
    "research",
    "training",
    "carousing",
    "resting",
)


def resolve_downtime(
    activity: str,
    days: int,
    *,
    lifestyle_tier: str = "modest",
    extra_cost_gp: float = 0.0,
    earnings_gp: float = 0.0,
) -> Dict:
    """Resolve a generic downtime block.

    ``extra_cost_gp`` and ``earnings_gp`` let the DM fold in situational amounts
    (research bribes, carousing tabs, wages) on top of systemic lifestyle upkeep.
    Returns days, per-line gp figures, and a net ``cp_delta`` (negative = spent).
    """
    activity = activity.lower()
    if activity not in ACTIVITIES:
        raise ValueError(
            f"Unknown activity '{activity}'. Options: {', '.join(ACTIVITIES)}"
        )
    if days < 0:
        raise ValueError("days must be >= 0")

    upkeep = cost_for_days(lifestyle_tier, days)
    upkeep_gp = float(upkeep["total_gp"])
    net_gp = round(earnings_gp - upkeep_gp - extra_cost_gp, 4)

    summary = (
        f"{days} day(s) {activity}: upkeep {upkeep_gp:g} gp"
        + (f", costs {extra_cost_gp:g} gp" if extra_cost_gp else "")
        + (f", earns {earnings_gp:g} gp" if earnings_gp else "")
        + f" -> net {net_gp:+g} gp"
    )
    return {
        "activity": activity,
        "days": days,
        "lifestyle_tier": lifestyle_tier,
        "upkeep_gp": upkeep_gp,
        "extra_cost_gp": extra_cost_gp,
        "earnings_gp": earnings_gp,
        "net_gp": net_gp,
        "cp_delta": gp_to_cp(net_gp),
        "summary": summary,
    }


# ----- crafting -----

def craft_rate_gp_per_day() -> float:
    c = get_config().crafting
    return round(c.gp_per_day * c.progress_multiplier, 4)


def start_crafting(item_cost_gp: float, *, is_magic: bool = False) -> Dict:
    """Plan a crafting project for an item of market value ``item_cost_gp``.

    Returns the up-front materials cost, the per-day progress rate, and the total
    number of crafting days the project will take at the current config rate.
    """
    cfg = get_config().crafting
    if is_magic and not cfg.allow_magic_item_crafting:
        raise ValueError("Magic item crafting is disabled in the current config")
    if item_cost_gp <= 0:
        raise ValueError("item_cost_gp must be > 0")

    materials_gp = round(item_cost_gp * cfg.materials_ratio, 4)
    per_day = craft_rate_gp_per_day()
    total_days = max(1, -(-int(round(item_cost_gp)) // max(1, int(round(per_day)))))  # ceil div
    return {
        "target_cost_gp": item_cost_gp,
        "is_magic": is_magic,
        "materials_gp": materials_gp,
        "materials_cp": gp_to_cp(materials_gp),
        "gp_per_day": per_day,
        "estimated_days": total_days,
        "progress_gp": 0.0,
        "complete": False,
        "summary": (
            f"Craft {item_cost_gp:g} gp item: {materials_gp:g} gp materials up front, "
            f"{per_day:g} gp/day, ~{total_days} day(s)."
        ),
    }


def advance_crafting(
    *,
    target_cost_gp: float,
    progress_gp: float,
    gp_per_day: Optional[float] = None,
    days: int,
) -> Dict:
    """Advance a crafting project by ``days`` days. Returns updated state."""
    if days < 0:
        raise ValueError("days must be >= 0")
    per_day = gp_per_day if gp_per_day is not None else craft_rate_gp_per_day()
    new_progress = round(min(target_cost_gp, progress_gp + per_day * days), 4)
    complete = new_progress >= target_cost_gp
    remaining_gp = round(max(0.0, target_cost_gp - new_progress), 4)
    remaining_days = 0 if complete else max(1, -(-int(round(remaining_gp)) // max(1, int(round(per_day)))))
    return {
        "target_cost_gp": target_cost_gp,
        "progress_gp": new_progress,
        "gp_per_day": per_day,
        "days_applied": days,
        "complete": complete,
        "remaining_gp": remaining_gp,
        "remaining_days": remaining_days,
        "summary": (
            f"Progress {new_progress:g}/{target_cost_gp:g} gp"
            + (" — complete!" if complete else f", ~{remaining_days} day(s) left")
        ),
    }
