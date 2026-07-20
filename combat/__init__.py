"""
Combat state tracker for The Oracle's DM brain.

Tracks every creature in an initiative order — PCs, NPCs, and monsters — with the
numbers that change mid-fight (HP, temp HP, AC, conditions, concentration).

    from combat import CombatTracker, Encounter, Combatant, Condition
"""
from .models import Encounter, Combatant, CombatantKind, Condition, CombatLog
from .tracker import CombatTracker
from .engine import CombatEngine, PCProfile, PCWeapon, TurnReport

__all__ = [
    "CombatTracker",
    "CombatEngine",
    "PCProfile",
    "PCWeapon",
    "TurnReport",
    "Encounter",
    "Combatant",
    "CombatantKind",
    "Condition",
    "CombatLog",
]
