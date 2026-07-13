"""
Central tunable game configuration (difficulty presets + editable JSON overrides).

    from game_config import get_config, reload_config
    cfg = get_config()
    cfg.economy.item_cost_multiplier
"""
from .config import (
    GameConfig,
    ProgressionConfig,
    EconomyConfig,
    CraftingConfig,
    BastionConfig,
    RestConfig,
    EncumbranceConfig,
    SurvivalConfig,
    HazardConfig,
    ReputationConfig,
    DMGuideConfig,
    SessionMemoryConfig,
    ImageryConfig,
    DIFFICULTY_PRESETS,
    build_config,
    load_config,
    get_config,
    reload_config,
    set_config,
    default_config_path,
)

__all__ = [
    "GameConfig",
    "ProgressionConfig",
    "EconomyConfig",
    "CraftingConfig",
    "BastionConfig",
    "RestConfig",
    "EncumbranceConfig",
    "SurvivalConfig",
    "HazardConfig",
    "ReputationConfig",
    "DMGuideConfig",
    "SessionMemoryConfig",
    "ImageryConfig",
    "DIFFICULTY_PRESETS",
    "build_config",
    "load_config",
    "get_config",
    "reload_config",
    "set_config",
    "default_config_path",
]
