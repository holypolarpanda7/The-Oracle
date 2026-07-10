"""
Combat state tracker for The Oracle's DM brain.

Tracks every creature in an initiative order — PCs, NPCs, and monsters — with the
numbers that change mid-fight (HP, temp HP, AC, conditions, concentration).

    from combat import CombatTracker, Encounter, Combatant, Condition
"""
from .models import Encounter, Combatant, CombatantKind, Condition
from .tracker import CombatTracker

__all__ = [
    "CombatTracker",
    "Encounter",
    "Combatant",
    "CombatantKind",
    "Condition",
]
