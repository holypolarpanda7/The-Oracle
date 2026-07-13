"""Owned hazard content: diseases, traps, and madness.

Self-authored mechanical summaries (OWNED_SOURCE) plus resolution logic and an
``Affliction`` table for persistent diseases/madness on a character.
"""
from .catalog import (
    DISEASES,
    TRAPS,
    MADNESS_TABLES,
    get_disease,
    get_trap,
    list_diseases,
    list_traps,
)
from .models import Affliction
from .engine import (
    disease_save_dc,
    contract_disease,
    disease_recovery_check,
    trap_detect,
    trap_disarm,
    roll_madness,
)

__all__ = [
    "DISEASES",
    "TRAPS",
    "MADNESS_TABLES",
    "get_disease",
    "get_trap",
    "list_diseases",
    "list_traps",
    "Affliction",
    "disease_save_dc",
    "contract_disease",
    "disease_recovery_check",
    "trap_detect",
    "trap_disarm",
    "roll_madness",
]
