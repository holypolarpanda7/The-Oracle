"""DC suggestion helper for the AI DM.

Maps a described difficulty to a suggested check DC using the tunable
``config.dm_guide.dc_by_difficulty`` table plus a global ``dc_bias`` nudge.
"""
from __future__ import annotations

from game_config import GameConfig, get_config

# Human-friendly aliases mapping onto the canonical difficulty keys.
_ALIASES = {
    "very easy": "trivial", "trivial": "trivial",
    "easy": "easy",
    "medium": "medium", "moderate": "medium", "average": "medium",
    "hard": "hard", "difficult": "hard",
    "very hard": "very_hard", "very_hard": "very_hard",
    "nearly impossible": "nearly_impossible",
    "nearly_impossible": "nearly_impossible", "impossible": "nearly_impossible",
}


def _normalize(difficulty: str) -> str:
    key = (difficulty or "").strip().lower().replace("-", " ")
    return _ALIASES.get(key, _ALIASES.get(key.replace(" ", "_"), "medium"))


def suggest_dc(difficulty: str, config: GameConfig | None = None) -> dict:
    """Return a suggested DC for a named difficulty.

    Result: ``{difficulty, dc, base_dc, bias}``.
    """
    cfg = (config or get_config()).dm_guide
    key = _normalize(difficulty)
    base = cfg.dc_by_difficulty.get(key, cfg.dc_by_difficulty.get("medium", 15))
    dc = max(1, base + cfg.dc_bias)
    return {"difficulty": key, "dc": dc, "base_dc": base, "bias": cfg.dc_bias}


def dc_scale(config: GameConfig | None = None) -> list[dict]:
    """The full difficulty -> DC ladder (bias applied), low to high."""
    cfg = (config or get_config()).dm_guide
    order = ["trivial", "easy", "medium", "hard", "very_hard", "nearly_impossible"]
    rows = []
    for key in order:
        if key in cfg.dc_by_difficulty:
            base = cfg.dc_by_difficulty[key]
            rows.append({"difficulty": key, "dc": max(1, base + cfg.dc_bias),
                         "base_dc": base})
    return rows
