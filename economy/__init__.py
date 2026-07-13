"""The Oracle economy layer: coin purses, lifestyle upkeep, downtime & crafting.

All tunable numbers route through ``game_config`` so difficulty presets and the
``game_settings.json`` overrides move the whole economy at once.
"""
from .models import DowntimeLog, CraftingProject
from .currency import (
    COIN_CP,
    DENOMINATIONS,
    empty_purse,
    to_cp,
    gp_value,
    gp_to_cp,
    from_cp,
    add_coins,
    can_afford,
    subtract_cost,
    format_purse,
)
from .lifestyle import lifestyle_tiers, daily_cost_gp, cost_for_days
from .downtime import (
    ACTIVITIES,
    resolve_downtime,
    craft_rate_gp_per_day,
    start_crafting,
    advance_crafting,
)

__all__ = [
    "DowntimeLog",
    "CraftingProject",
    "COIN_CP",
    "DENOMINATIONS",
    "empty_purse",
    "to_cp",
    "gp_value",
    "gp_to_cp",
    "from_cp",
    "add_coins",
    "can_afford",
    "subtract_cost",
    "format_purse",
    "lifestyle_tiers",
    "daily_cost_gp",
    "cost_for_days",
    "ACTIVITIES",
    "resolve_downtime",
    "craft_rate_gp_per_day",
    "start_crafting",
    "advance_crafting",
]
