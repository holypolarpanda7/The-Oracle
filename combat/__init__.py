"""
Combat state tracker for The Oracle's DM brain.

Tracks every creature in an initiative order — PCs, NPCs, and monsters — with the
numbers that change mid-fight (HP, temp HP, AC, conditions, concentration).

    from combat import CombatTracker, Encounter, Combatant, Condition
"""
from .models import Encounter, Combatant, CombatantKind, Condition, CombatLog
from . import bonds
from .bonds import CombatBond
from .tracker import CombatTracker
from .engine import (CombatEngine, PCProfile, PCWeapon, TurnReport,
                     monster_save_mod)

__all__ = [
    "CombatTracker",
    "CombatEngine",
    "monster_save_mod",
    "PCProfile",
    "PCWeapon",
    "TurnReport",
    "Encounter",
    "Combatant",
    "CombatantKind",
    "Condition",
    "CombatLog",
    "bonds",
    "CombatBond",
]
