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
class GameConfig:
    profile: str = "normal"
    progression: ProgressionConfig = field(default_factory=ProgressionConfig)
    economy: EconomyConfig = field(default_factory=EconomyConfig)
    crafting: CraftingConfig = field(default_factory=CraftingConfig)
    bastion: BastionConfig = field(default_factory=BastionConfig)
    rest: RestConfig = field(default_factory=RestConfig)
    encumbrance: EncumbranceConfig = field(default_factory=EncumbranceConfig)

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
    },
    "normal": {},  # the dataclass defaults are the "normal" baseline
    "hard": {
        "progression": {"xp_multiplier": 0.75},
        "economy": {"item_cost_multiplier": 1.25, "sell_price_ratio": 0.4,
                    "lifestyle_cost_multiplier": 1.5},
        "crafting": {"progress_multiplier": 0.75},
        "bastion": {"cost_multiplier": 1.25, "gold_income_multiplier": 0.85},
    },
    "gritty": {
        "progression": {"xp_multiplier": 0.5},
        "economy": {"item_cost_multiplier": 1.5, "sell_price_ratio": 0.35,
                    "lifestyle_cost_multiplier": 2.0},
        "crafting": {"progress_multiplier": 0.5},
        "bastion": {"cost_multiplier": 1.5, "gold_income_multiplier": 0.75},
        "rest": {"variant": "gritty", "short_rest_hours": 8, "long_rest_hours": 168},
        "encumbrance": {"variant": "standard"},
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
