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
class DMGuideConfig:
    """AI Dungeon Master guidance + encounter/DC helper tuning."""
    enabled: bool = True
    # Whether to inject the self-authored DM best-practice text into the system
    # prompt, and how much of it ("brief" = condensed, "full" = the long form).
    inject_guidance: bool = True
    guidance_verbosity: str = "brief"    # "brief" | "full"
    # Difficulty-check DC suggestions the DM should lean on.
    dc_by_difficulty: dict[str, int] = field(default_factory=lambda: {
        "trivial": 5, "easy": 10, "medium": 15, "hard": 20,
        "very_hard": 25, "nearly_impossible": 30,
    })
    # Global nudge added to suggested DCs (e.g. +2 at gritty, -2 at story).
    dc_bias: int = 0
    # Per-character encounter XP budget by threat tier (self-authored curve;
    # scaled by party level at runtime). Multiplied by difficulty_budget_mult.
    encounter_base_budget: dict[str, int] = field(default_factory=lambda: {
        "easy": 50, "medium": 100, "hard": 150, "deadly": 200,
    })
    difficulty_budget_mult: float = 1.0
    # Multiplier applied to summed monster XP based on how many monsters there are
    # (a mob is scarier than its raw XP). Keyed by lower-bound monster count.
    count_multipliers: dict[str, float] = field(default_factory=lambda: {
        "1": 1.0, "2": 1.5, "3": 2.0, "7": 2.5, "11": 3.0, "15": 4.0,
    })


@dataclass
class SessionMemoryConfig:
    """Bounded conversation memory for long-running DM sessions."""
    recent_turns: int = 12
    compaction_threshold: int = 18
    summary_max_chars: int = 4000
    compaction_max_tokens: int = 320
    compaction_timeout_seconds: int = 25


@dataclass
class ImageryConfig:
    """Self-hosted diffusion image generation + storage for scene visuals.

    Talks to a local image backend (ComfyUI in API mode by default). Safety is
    the operator's to control: the positive style and negative prompt are just
    plain, editable strings here — no external content filter is imposed.
    """
    enabled: bool = True
    # ----- generation backend -----
    backend: str = "comfyui"                 # "comfyui" (only backend for now)
    base_url: str = "http://127.0.0.1:8188"  # ComfyUI listen address
    # Default ("safe") checkpoint — the strong non-NSFW model. Point this at a
    # quality SDXL finetune (Juggernaut XL / RealVisXL / a dark-fantasy concept
    # model), NOT base SDXL. See imagery/MODELS.md.
    checkpoint: str = "juggernautXL_v9.safetensors"
    # Mature checkpoint — used ONLY when a render is flagged mature (a Pony-family
    # SDXL model). Leave None to disable NSFW-capable rendering entirely; when
    # None, mature-flagged renders silently fall back to ``checkpoint``.
    checkpoint_mature: Optional[str] = "ponyDiffusionV6XL.safetensors"
    # Pony-family models key their quality off score tags and a rating token, and
    # want their own negatives — applied instead of style_prompt/negative_prompt
    # when a render is mature. Kept editable; operator owns content policy.
    mature_style_prompt: str = (
        "score_9, score_8_up, score_7_up, rating_explicit, "
        "painterly digital illustration, dramatic rim lighting, "
        "saturated jewel tones, stylized mythic character art, "
        "ornate details, detailed anatomy"
    )
    mature_negative_prompt: str = (
        "score_6, score_5, score_4, lowres, blurry, deformed, extra limbs, "
        "bad anatomy, bad hands, watermark, text, signature, jpeg artifacts, "
        "child, childlike, underage, loli, shota"
    )
    # Optional path to a custom ComfyUI workflow JSON (API format). When set it
    # overrides the built-in SDXL txt2img graph. Placeholders are filled in by
    # the client (positive/negative/seed/width/height/steps/checkpoint).
    workflow_path: Optional[str] = None
    steps: int = 25
    cfg_scale: float = 7.0
    sampler: str = "euler"
    scheduler: str = "normal"
    gen_width: int = 1024
    gen_height: int = 1024
    timeout_seconds: int = 180               # generation can be slow on lowvram
    # ----- prompt shaping (operator-controlled) -----
    # House art direction: Hades 2-adjacent (Supergiant) — painterly with bold
    # ink linework, saturated jewel tones, dramatic rim light. Expressed as
    # style descriptors, not the game's name (diffusion models respond to the
    # former; the latter is a trademark, not a style).
    style_prompt: str = (
        "painterly digital illustration, bold ink outlines, high contrast "
        "dramatic rim lighting, saturated jewel tones, stylized mythic "
        "character art, dynamic composition, ornate engraved details, "
        "graphic-novel key art"
    )
    negative_prompt: str = (
        "lowres, blurry, deformed, extra limbs, bad anatomy, watermark, text, "
        "signature, jpeg artifacts"
    )
    # ----- storage / compression -----
    store_width: int = 768                   # canonical images downscaled to this
    thumb_width: int = 256
    webp_quality: int = 82
    max_per_bucket: int = 3                  # 3 images per (subject x context)
    max_total_images: int = 600             # global cap; LRU-evicted beyond this
    # ----- reference-guided scenes (IP-Adapter) -----
    # When on, scene renders pull stored art of named participants (PC
    # portraits, NPC/creature images) as visual references so "Kara casting
    # fireball at the goblin" looks like Kara and that goblin. Requires the
    # ComfyUI_IPAdapter_plus custom nodes + an ip-adapter SDXL model in
    # ComfyUI; when absent/off, scenes render from the text prompt alone.
    use_ipadapter: bool = False
    ipadapter_weight: float = 0.65           # identity strength (0..1)
    ipadapter_preset: str = "STANDARD (medium strength)"
    max_scene_references: int = 2            # participant refs pulled per scene
    # ----- behavior -----
    allow_temp: bool = True                  # player-requested throwaway images
    max_images_per_reply: int = 2            # cap auto-generated visuals per turn
    inject_hook_guidance: bool = True        # teach the DM the [[IMAGE]] hook


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
    dm_guide: DMGuideConfig = field(default_factory=DMGuideConfig)
    session_memory: SessionMemoryConfig = field(default_factory=SessionMemoryConfig)
    imagery: ImageryConfig = field(default_factory=ImageryConfig)

    def to_dict(self) -> dict:
        return _dataclass_to_dict(self)
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
        "dm_guide": {"dc_bias": -2, "difficulty_budget_mult": 1.25},
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
        "dm_guide": {"dc_bias": 1, "difficulty_budget_mult": 0.85},
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
        "dm_guide": {"dc_bias": 2, "difficulty_budget_mult": 0.7},
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
