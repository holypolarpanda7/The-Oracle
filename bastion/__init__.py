"""The Oracle bastion layer: player strongholds, special facilities, and turns.

Owned 2024-era content. Cost/income/time knobs route through ``game_config``.
"""
from .models import Bastion, FacilityInstance, BastionEvent
from .catalog import (
    FACILITIES,
    FACILITY_TIER_LEVELS,
    SPACES,
    get_facility,
    facilities_for_level,
)
from .turn import (
    min_bastion_level,
    turn_length_days,
    facility_cost_gp,
    can_own_bastion,
    resolve_bastion_turn,
)

__all__ = [
    "Bastion",
    "FacilityInstance",
    "BastionEvent",
    "FACILITIES",
    "FACILITY_TIER_LEVELS",
    "SPACES",
    "get_facility",
    "facilities_for_level",
    "min_bastion_level",
    "turn_length_days",
    "facility_cost_gp",
    "can_own_bastion",
    "resolve_bastion_turn",
]
