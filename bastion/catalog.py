"""Curated Bastion special-facility catalog.

Bastions are 2024-era player-stronghold content the user owns. These entries are
concise, self-authored *mechanical* summaries (level gate, space, the special
orders a facility can issue, and whether it produces gold income) — NOT verbatim
book prose. All facilities are tagged ``OWNED_SOURCE``.

Numbers here are baseline; the live cost/income knobs are scaled at runtime by
``game_config.bastion`` (cost_multiplier / gold_income_multiplier).
"""
from __future__ import annotations

from typing import Dict, List, Optional

try:  # keep the tag consistent with the rules package
    from rules import OWNED_SOURCE
except Exception:  # pragma: no cover - standalone import fallback
    OWNED_SOURCE = "Owned (non-SRD)"


# Space sizes (relative footprint of a special facility).
SPACES = ("cramped", "roomy", "vast")

# Character level at which each tier of special facility unlocks.
FACILITY_TIER_LEVELS = (5, 9, 13, 17)


# Each facility: slug, name, min_level, space, orders (special actions it can
# take on a bastion turn), and an optional flat ``income_gp`` produced per turn.
_FACILITIES: List[Dict] = [
    # --- Level 5 tier ---
    {"slug": "arcane-study", "name": "Arcane Study", "min_level": 5, "space": "cramped",
     "orders": ["Craft (arcana/spell scroll)"],
     "desc": "A study for arcane work; can craft spell scrolls or arcana over time."},
    {"slug": "armory", "name": "Armory", "min_level": 5, "space": "cramped",
     "orders": ["Trade", "Maintain (equip defenders)"],
     "desc": "Stores arms and armor; keeps bastion defenders equipped."},
    {"slug": "barrack", "name": "Barrack", "min_level": 5, "space": "roomy",
     "orders": ["Recruit (defenders)"],
     "desc": "Quarters for hirelings; houses and recruits bastion defenders."},
    {"slug": "garden", "name": "Garden", "min_level": 5, "space": "cramped",
     "orders": ["Harvest"], "income_gp": 0,
     "desc": "Cultivated plot; harvests herbs, food, or decorative goods."},
    {"slug": "library", "name": "Library", "min_level": 5, "space": "cramped",
     "orders": ["Research"],
     "desc": "Collected lore; supports research to answer questions or find leads."},
    {"slug": "sanctuary", "name": "Sanctuary", "min_level": 5, "space": "cramped",
     "orders": ["Craft (holy water/relic)"],
     "desc": "A consecrated space for faith-based crafting and quiet recovery."},
    {"slug": "smithy", "name": "Smithy", "min_level": 5, "space": "roomy",
     "orders": ["Craft (weapon/armor)"],
     "desc": "A forge for crafting mundane weapons and armor."},
    {"slug": "storehouse", "name": "Storehouse", "min_level": 5, "space": "roomy",
     "orders": ["Trade"], "income_gp": 0,
     "desc": "Warehouse for goods; enables buying and selling in bulk."},
    {"slug": "workshop", "name": "Workshop", "min_level": 5, "space": "roomy",
     "orders": ["Craft (adventuring gear)"],
     "desc": "General workshop for crafting tools and adventuring gear."},

    # --- Level 9 tier ---
    {"slug": "gaming-hall", "name": "Gaming Hall", "min_level": 9, "space": "vast",
     "orders": ["Trade (patrons)"], "income_gp": 100,
     "desc": "A hall of games and drink; draws paying patrons for steady coin."},
    {"slug": "greenhouse", "name": "Greenhouse", "min_level": 9, "space": "roomy",
     "orders": ["Harvest (rare plants)"],
     "desc": "Climate-controlled beds for rare and magical plant cultivation."},
    {"slug": "laboratory", "name": "Laboratory", "min_level": 9, "space": "cramped",
     "orders": ["Craft (poison/alchemy)"],
     "desc": "Alchemical lab for brewing potions, poisons, and reagents."},
    {"slug": "stable", "name": "Stable", "min_level": 9, "space": "roomy",
     "orders": ["Recruit (mounts)"],
     "desc": "Houses and breeds mounts and beasts of burden."},
    {"slug": "teleportation-circle", "name": "Teleportation Circle", "min_level": 9,
     "space": "roomy", "orders": ["Empower (travel)"],
     "desc": "A permanent circle enabling rapid long-distance travel."},

    # --- Level 13 tier ---
    {"slug": "archive", "name": "Archive", "min_level": 13, "space": "roomy",
     "orders": ["Research (deep lore)"],
     "desc": "A vast record hall for deep research and secret-keeping."},
    {"slug": "war-room", "name": "War Room", "min_level": 13, "space": "roomy",
     "orders": ["Recruit (soldiers)", "Empower (defense)"],
     "desc": "Command center for organizing defenders and campaigns."},
    {"slug": "guildhall", "name": "Guildhall", "min_level": 13, "space": "vast",
     "orders": ["Trade (guild)"], "income_gp": 250,
     "desc": "Seat of a guild; generates substantial recurring income."},

    # --- Level 17 tier ---
    {"slug": "demiplane", "name": "Demiplane", "min_level": 17, "space": "vast",
     "orders": ["Empower (extradimensional)"],
     "desc": "A pocket dimension anchored to the bastion for storage or refuge."},
    {"slug": "sanctum", "name": "Sanctum", "min_level": 17, "space": "vast",
     "orders": ["Empower (blessing)"],
     "desc": "A seat of great power granting potent blessings to its master."},
]

# Indexed by slug for fast lookup, with source tag applied.
FACILITIES: Dict[str, Dict] = {
    f["slug"]: {**f, "type": "special", "source": OWNED_SOURCE} for f in _FACILITIES
}


def get_facility(slug: str) -> Optional[Dict]:
    return FACILITIES.get(slug)


def facilities_for_level(level: int) -> List[Dict]:
    """All special facilities a character of ``level`` may add."""
    return [f for f in FACILITIES.values() if level >= f["min_level"]]
