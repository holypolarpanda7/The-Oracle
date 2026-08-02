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


#: Setting-specific facilities live OUTSIDE this file, in the gitignored
#: ``owned_books/bastion_facilities_overrides.json``. The catalog above is the
#: baseline every table gets; a facility that only exists in one campaign
#: setting is book-derived data, and CLAUDE.md keeps that out of the repo. Same
#: paste-and-translate shape as every other override slot: a JSON array of
#: entries with the keys used above, plus optional ``prerequisite``,
#: ``hirelings``, ``propulsion`` and ``notes``.
_OVERRIDES_FILE = "bastion_facilities_overrides.json"


def _owned_books_dir():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent / "owned_books"


def load_facility_overrides(path=None) -> int:
    """Merge local facility overrides into ``FACILITIES``. Returns how many.

    Missing file is the normal case (a table with no setting books), so it is a
    silent no-op rather than an error.
    """
    import json
    p = path or (_owned_books_dir() / _OVERRIDES_FILE)
    try:
        if not p.exists():
            return 0
        entries = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - a bad local file must not stop boot
        print(f"[bastion] facility overrides skipped: {e}")
        return 0
    n = 0
    for e in entries if isinstance(entries, list) else []:
        slug = (e or {}).get("slug")
        if not slug:
            continue
        FACILITIES[slug] = {**e, "type": e.get("type", "special"),
                            "source": e.get("source", OWNED_SOURCE)}
        n += 1
    return n


def propulsion_facilities() -> List[Dict]:
    """Facilities that let a bastion MOVE (see bastion/mobile.py).

    Declared by the facility itself rather than by a hard-coded list here, so a
    setting that adds its own kind of helm works without touching this file.
    """
    return [f for f in FACILITIES.values() if f.get("propulsion")]


def get_facility(slug: str) -> Optional[Dict]:
    return FACILITIES.get(slug)


def facilities_for_level(level: int) -> List[Dict]:
    """All special facilities a character of ``level`` may add."""
    return [f for f in FACILITIES.values() if level >= f["min_level"]]
