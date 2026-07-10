"""The Oracle survival layer: needs, exhaustion, weather, travel, light, rest.

Mostly pure logic — per-character survival state (HP, hit dice, exhaustion,
rations, water) lives on the backend ``Character`` and is threaded through these
functions. Every tunable number routes through ``game_config.survival``.
"""
from .exhaustion import (
    MAX_EXHAUSTION,
    effects_for_level,
    add_exhaustion,
    remove_exhaustion,
    hp_max_multiplier,
    speed_multiplier,
    describe as describe_exhaustion,
    long_rest_recovery,
)
from .needs import consume_day, forced_march_dc
from .encumbrance import carrying_capacity, encumbrance_status
from .rest import short_rest, long_rest
from .weather import (
    CLIMATES,
    season_for_month,
    generate_weather,
    active_hazard_tags,
)
from .environment import (
    cold_hazard,
    heat_hazard,
    wind_hazard,
    frigid_water_hazard,
    resolve as resolve_hazard,
    hazards_from_weather,
)
from .travel import TERRAIN, PACES, travel, navigation_dc, forage
from .light import (
    light_sources,
    source_spec,
    burn,
    effective_vision,
    LIGHT_LEVELS,
)

__all__ = [
    "MAX_EXHAUSTION",
    "effects_for_level",
    "add_exhaustion",
    "remove_exhaustion",
    "hp_max_multiplier",
    "speed_multiplier",
    "describe_exhaustion",
    "long_rest_recovery",
    "consume_day",
    "forced_march_dc",
    "carrying_capacity",
    "encumbrance_status",
    "short_rest",
    "long_rest",
    "CLIMATES",
    "season_for_month",
    "generate_weather",
    "active_hazard_tags",
    "cold_hazard",
    "heat_hazard",
    "wind_hazard",
    "frigid_water_hazard",
    "resolve_hazard",
    "hazards_from_weather",
    "TERRAIN",
    "PACES",
    "travel",
    "navigation_dc",
    "forage",
    "light_sources",
    "source_spec",
    "burn",
    "effective_vision",
    "LIGHT_LEVELS",
]
