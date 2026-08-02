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
    load_facility_overrides,
    propulsion_facilities,
)

# Setting-specific facilities live in a gitignored local file; merging them at
# import time means every consumer sees one catalog and nobody has to remember
# to call the loader (a missing file is a silent no-op).
load_facility_overrides()
from .mobile import (
    TravelPlan,
    can_travel,
    plan_travel,
    advance,
    propulsion_of,
    suspended_facilities,
    daily_hours,
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
    "load_facility_overrides",
    "propulsion_facilities",
    "TravelPlan",
    "can_travel",
    "plan_travel",
    "advance",
    "propulsion_of",
    "suspended_facilities",
    "daily_hours",
    "min_bastion_level",
    "turn_length_days",
    "facility_cost_gp",
    "can_own_bastion",
    "resolve_bastion_turn",
]
