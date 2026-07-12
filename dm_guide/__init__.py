"""AI Dungeon Master guidance + encounter/DC helper toolkit.

Self-authored DM best-practice text (injected into the system prompt) plus small,
config-tunable helpers for suggesting check DCs and estimating/planning encounter
difficulty by XP budget.
"""
from __future__ import annotations

from .guidance import (
    DM_SECTIONS,
    full_guidance,
    brief_guidance,
    guidance_block,
)
from .dc import suggest_dc, dc_scale
from .encounter import (
    count_multiplier,
    party_budgets,
    estimate_encounter,
    build_encounter,
)
from .motifs import MOTIF_TABLES, roll_motifs

__all__ = [
    "DM_SECTIONS",
    "full_guidance",
    "brief_guidance",
    "guidance_block",
    "suggest_dc",
    "dc_scale",
    "count_multiplier",
    "party_budgets",
    "estimate_encounter",
    "build_encounter",
    "MOTIF_TABLES",
    "roll_motifs",
]
