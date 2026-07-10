"""Encounter difficulty estimator + budget planner for the AI DM.

Uses a self-authored per-character XP-budget curve (NOT any published threshold
table) scaled by character level, and a count multiplier from ``config.dm_guide``
so that a mob of weak monsters reads as more dangerous than its raw XP sum.
"""
from __future__ import annotations

from game_config import GameConfig, get_config

# Difficulty tiers, easy -> hardest, used to order budgets and labels.
_TIERS = ["easy", "medium", "hard", "deadly"]


def _level_factor(level: int) -> float:
    """Self-authored scaling: how much a single character's budget grows by level.

    Roughly linear with a gentle ramp so higher-level parties can absorb bigger
    encounters. Level 1 == 1.0x the tier base.
    """
    lvl = max(1, min(20, int(level)))
    return 1.0 + (lvl - 1) * 1.15


def count_multiplier(n_monsters: int, config: GameConfig | None = None) -> float:
    """Multiplier applied to summed monster XP based on how many there are."""
    cfg = (config or get_config()).dm_guide
    mult = 1.0
    for bound_str, value in sorted(cfg.count_multipliers.items(), key=lambda kv: int(kv[0])):
        if n_monsters >= int(bound_str):
            mult = value
    return mult


def party_budgets(party_levels: list[int], config: GameConfig | None = None) -> dict[str, int]:
    """Total party XP budget per difficulty tier."""
    cfg = (config or get_config()).dm_guide
    budgets: dict[str, int] = {}
    for tier in _TIERS:
        base = cfg.encounter_base_budget.get(tier, 0)
        total = sum(base * _level_factor(lvl) for lvl in party_levels)
        budgets[tier] = int(round(total * cfg.difficulty_budget_mult))
    return budgets


def estimate_encounter(party_levels: list[int], monster_xps: list[int],
                       config: GameConfig | None = None) -> dict:
    """Estimate how hard an encounter is for the given party.

    Returns ``{raw_xp, adjusted_xp, multiplier, difficulty, budgets, party_size}``.
    ``difficulty`` is one of trivial/easy/medium/hard/deadly/lethal.
    """
    cfg = (config or get_config())
    raw = sum(int(x) for x in monster_xps)
    mult = count_multiplier(len(monster_xps), cfg)
    adjusted = int(round(raw * mult))
    budgets = party_budgets(party_levels, cfg)

    if adjusted < budgets["easy"]:
        label = "trivial"
    elif adjusted < budgets["medium"]:
        label = "easy"
    elif adjusted < budgets["hard"]:
        label = "medium"
    elif adjusted < budgets["deadly"]:
        label = "hard"
    elif adjusted < int(budgets["deadly"] * 1.5):
        label = "deadly"
    else:
        label = "lethal"

    return {
        "raw_xp": raw,
        "adjusted_xp": adjusted,
        "multiplier": mult,
        "difficulty": label,
        "budgets": budgets,
        "party_size": len(party_levels),
    }


def build_encounter(party_levels: list[int], target_difficulty: str = "medium",
                    config: GameConfig | None = None) -> dict:
    """Suggest an XP budget (and rough count guidance) for a target difficulty."""
    cfg = (config or get_config())
    budgets = party_budgets(party_levels, cfg)
    tier = (target_difficulty or "medium").strip().lower()
    if tier not in budgets:
        tier = "medium"
    budget = budgets[tier]
    return {
        "target_difficulty": tier,
        "xp_budget": budget,
        "note": (
            "Spend up to this adjusted XP. Remember the count multiplier: adding "
            "more monsters raises effective difficulty, so leave headroom for mobs."
        ),
        "budgets": budgets,
    }
