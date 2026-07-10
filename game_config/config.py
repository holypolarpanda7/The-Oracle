"""
Central, tunable game configuration for The Oracle.

Every "house-rule" number that a DM might want to dial — XP gain, item prices,
daily/lifestyle costs, crafting speed, bastion costs, rest variants — lives here in
ONE place instead of being scattered across the economy, crafting, bastion, and
progression systems. Difficulty presets (``story`` / ``normal`` / ``hard`` /
``gritty``) ship as ready-made bundles, and everything is overridable from a JSON
file on disk so the numbers can be changed without touching code.

Resolution order for the active config:
  1. ``game_config/game_settings.json`` (or the path in ``$ORACLE_GAME_CONFIG``)
  2. that file's ``active_profile`` picks a difficulty preset
  3. the file's ``overrides`` block tweaks individual knobs on top of the preset

    from game_config import get_config
    cfg = get_config()
    price = base_gp * cfg.economy.item_cost_multiplier
    xp = base_xp * cfg.progression.xp_multiplier
"""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Optional

# ----- knob sections -----


@dataclass
class ProgressionConfig:
    xp_multiplier: float = 1.0            # scales XP awards
    milestone_leveling: bool = False      # ignore XP, level on DM milestones
    max_level: int = 20
    # XP thresholds to REACH each level (SRD table). Index 0 unused (level 1 = 0 xp).
    xp_thresholds: list[int] = field(default_factory=lambda: [
        0, 0, 300, 900, 2700, 6500, 14000, 23000, 34000, 48000, 64000,
        85000, 100000, 120000, 140000, 165000, 195000, 225000, 265000, 305000, 355000,
    ])


@dataclass
class EconomyConfig:
    item_cost_multiplier: float = 1.0     # buy price scaling
    sell_price_ratio: float = 0.5         # fraction of value recovered when selling
    lifestyle_cost_multiplier: float = 1.0
    starting_gold: int = 0                # bonus gp granted at character creation
    # SRD lifestyle upkeep in gp/day (before the multiplier). "free" costs nothing.
    lifestyle_daily_gp: dict[str, float] = field(default_factory=lambda: {
        "wretched": 0.0,
        "squalid": 0.1,
        "poor": 0.2,
        "modest": 1.0,
        "comfortable": 2.0,
        "wealthy": 4.0,
        "aristocratic": 10.0,
    })


@dataclass
class CraftingConfig:
    gp_per_day: float = 5.0               # SRD progress in item value per day
    materials_ratio: float = 0.5          # fraction of market value spent on materials
    progress_multiplier: float = 1.0      # >1 crafts faster (fewer days)
    allow_magic_item_crafting: bool = True


@dataclass
class BastionConfig:
    cost_multiplier: float = 1.0          # construction / special-facility costs
    bastion_turn_days: int = 7            # in-world days per bastion turn
    gold_income_multiplier: float = 1.0   # scales facility gp income
    special_facility_base_cost: int = 5000  # baseline gp for a special facility
    min_level: int = 5                    # character level a bastion unlocks at


@dataclass
class RestConfig:
    variant: str = "standard"             # standard | gritty | epic
    short_rest_hours: int = 1
    long_rest_hours: int = 8


@dataclass
class EncumbranceConfig:
    variant: str = "off"                  # off | standard | variant


@dataclass
class SurvivalConfig:
    """Rations/water, exhaustion, weather, travel, and light knobs."""
    enabled: bool = True
    # Provisions consumed per creature per day.
    food_per_day: float = 1.0             # pounds of food
    water_per_day: float = 1.0            # gallons of water
    # Grace period before deprivation starts causing exhaustion.
    days_without_food_before_exhaustion: int = 3
    days_without_water_before_exhaustion: int = 1
    # Forced march: travelling beyond this many hours/day risks exhaustion.
    forced_march_hours: int = 8
    forced_march_dc: int = 10
    # Environmental hazard saving-throw DCs (Constitution).
    extreme_cold_dc: int = 10             # per hour without cold-weather gear
    extreme_heat_dc_base: int = 5         # +1 per hour (see extreme_heat_dc_per_hour)
    extreme_heat_dc_per_hour: int = 1
    strong_wind_ranged_disadvantage: bool = True
    frigid_water_dc: int = 10
    # Overland travel pace in miles/hour and hours travelled per day.
    pace_miles_per_hour: dict[str, float] = field(default_factory=lambda: {
        "fast": 4.0, "normal": 3.0, "slow": 2.0,
    })
    travel_hours_per_day: int = 8
    # Foraging & navigation base DCs (terrain modifiers added at runtime).
    forage_dc: int = 10
    forage_yield_multiplier: float = 1.0  # scales food/water found
    navigation_dc: int = 10
    # Light-source burn times (minutes of fuel).
    torch_minutes: int = 60
    lantern_minutes: int = 360
    candle_minutes: int = 60
    # Long rest reduces exhaustion by 1 only if the character ate & drank.
    exhaustion_recovery_needs_food: bool = True
    # Provisions a freshly created character starts with.
    starting_rations: int = 5
    starting_water: int = 5


@dataclass
class HazardConfig:
    """Diseases, traps, and madness (owned, self-authored content)."""
    enabled: bool = True
    disease_save_dc_default: int = 11
    trap_detect_dc_default: int = 15
    trap_disarm_dc_default: int = 15
    madness_enabled: bool = True


@dataclass
class ReputationConfig:
    """Faction renown thresholds (standing label -> minimum renown)."""
    enabled: bool = True
    thresholds: dict[str, int] = field(default_factory=lambda: {
        "unknown": 0,
        "known": 3,
        "accepted": 10,
        "respected": 25,
        "honored": 50,
    })


@dataclass
class GameConfig:
    profile: str = "normal"
    progression: ProgressionConfig = field(default_factory=ProgressionConfig)
    economy: EconomyConfig = field(default_factory=EconomyConfig)
    crafting: CraftingConfig = field(default_factory=CraftingConfig)
    bastion: BastionConfig = field(default_factory=BastionConfig)
    rest: RestConfig = field(default_factory=RestConfig)
    encumbrance: EncumbranceConfig = field(default_factory=EncumbranceConfig)
    survival: SurvivalConfig = field(default_factory=SurvivalConfig)
    hazard: HazardConfig = field(default_factory=HazardConfig)
    reputation: ReputationConfig = field(default_factory=ReputationConfig)

    def to_dict(self) -> dict:
        return _dataclass_to_dict(self)


# ----- difficulty presets (overrides applied on top of the "normal" baseline) -----

DIFFICULTY_PRESETS: dict[str, dict] = {
    "story": {
        "progression": {"xp_multiplier": 1.5},
        "economy": {"item_cost_multiplier": 0.75, "sell_price_ratio": 0.6,
                    "lifestyle_cost_multiplier": 0.5, "starting_gold": 50},
        "crafting": {"progress_multiplier": 2.0},
        "bastion": {"cost_multiplier": 0.75, "gold_income_multiplier": 1.25},
        # Survival stays on but forgiving: easier saves, richer foraging.
        "survival": {"days_without_food_before_exhaustion": 5, "extreme_cold_dc": 8,
                     "extreme_heat_dc_base": 3, "forced_march_dc": 8, "frigid_water_dc": 8,
                     "forage_dc": 8, "forage_yield_multiplier": 1.5, "navigation_dc": 8},
        "hazard": {"disease_save_dc_default": 9, "trap_detect_dc_default": 12},
    },
    "normal": {},  # the dataclass defaults are the "normal" baseline
    "hard": {
        "progression": {"xp_multiplier": 0.75},
        "economy": {"item_cost_multiplier": 1.25, "sell_price_ratio": 0.4,
                    "lifestyle_cost_multiplier": 1.5},
        "crafting": {"progress_multiplier": 0.75},
        "bastion": {"cost_multiplier": 1.25, "gold_income_multiplier": 0.85},
        "survival": {"days_without_food_before_exhaustion": 2, "extreme_cold_dc": 12,
                     "extreme_heat_dc_base": 7, "forced_march_dc": 12, "frigid_water_dc": 12,
                     "forage_dc": 13, "forage_yield_multiplier": 0.75, "navigation_dc": 13},
        "hazard": {"disease_save_dc_default": 13, "trap_detect_dc_default": 17,
                   "trap_disarm_dc_default": 17},
        "encumbrance": {"variant": "standard"},
    },
    "gritty": {
        "progression": {"xp_multiplier": 0.5},
        "economy": {"item_cost_multiplier": 1.5, "sell_price_ratio": 0.35,
                    "lifestyle_cost_multiplier": 2.0},
        "crafting": {"progress_multiplier": 0.5},
        "bastion": {"cost_multiplier": 1.5, "gold_income_multiplier": 0.75},
        "rest": {"variant": "gritty", "short_rest_hours": 8, "long_rest_hours": 168},
        "encumbrance": {"variant": "standard"},
        # Unforgiving wilderness: deprivation bites fast, saves are brutal.
        "survival": {"days_without_food_before_exhaustion": 1,
                     "days_without_water_before_exhaustion": 0,
                     "extreme_cold_dc": 15, "extreme_heat_dc_base": 10, "forced_march_dc": 14,
                     "frigid_water_dc": 15, "forage_dc": 15, "forage_yield_multiplier": 0.5,
                     "navigation_dc": 15},
        "hazard": {"disease_save_dc_default": 15, "trap_detect_dc_default": 18,
                   "trap_disarm_dc_default": 18},
    },
}


# ----- (de)serialization helpers -----

def _dataclass_to_dict(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _dataclass_to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_dataclass_to_dict(v) for v in obj]
    return obj


def _apply_overrides(cfg: GameConfig, overrides: dict) -> None:
    """Deep-merge a dict of ``{section: {knob: value}}`` onto a GameConfig in place."""
    for section_name, section_vals in (overrides or {}).items():
        section = getattr(cfg, section_name, None)
        if section is None:
            continue
        if is_dataclass(section) and isinstance(section_vals, dict):
            valid = {f.name for f in fields(section)}
            for knob, value in section_vals.items():
                if knob in valid:
                    setattr(section, knob, value)
        elif not is_dataclass(section):
            setattr(cfg, section_name, section_vals)


def build_config(profile: str = "normal", overrides: Optional[dict] = None) -> GameConfig:
    """Construct a GameConfig from a difficulty preset plus optional overrides."""
    profile = (profile or "normal").lower()
    cfg = GameConfig(profile=profile if profile in DIFFICULTY_PRESETS else "normal")
    _apply_overrides(cfg, DIFFICULTY_PRESETS.get(cfg.profile, {}))
    if overrides:
        _apply_overrides(cfg, overrides)
    return cfg


def default_config_path() -> Path:
    env = os.getenv("ORACLE_GAME_CONFIG")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "game_settings.json"


def load_config(path: Optional[os.PathLike | str] = None) -> GameConfig:
    """Load config from a JSON file. Falls back to the ``normal`` preset if absent."""
    p = Path(path) if path else default_config_path()
    if not p.exists():
        return build_config("normal")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # pragma: no cover - corrupt file shouldn't crash the game
        print(f"[game_config] failed to read {p}: {e}; using 'normal' defaults")
        return build_config("normal")
    return build_config(data.get("active_profile", "normal"), data.get("overrides"))


_ACTIVE: Optional[GameConfig] = None


def get_config() -> GameConfig:
    """Return the process-wide active config (cached; call ``reload_config`` to refresh)."""
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = load_config()
    return _ACTIVE


def reload_config(path: Optional[os.PathLike | str] = None) -> GameConfig:
    """Force a re-read of the config file and update the cached active config."""
    global _ACTIVE
    _ACTIVE = load_config(path)
    return _ACTIVE


def set_config(cfg: GameConfig) -> GameConfig:
    """Override the active config in-process (useful for tests or an admin command)."""
    global _ACTIVE
    _ACTIVE = cfg
    return _ACTIVE
