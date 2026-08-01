"""Affixes — the difference between "a longsword" and "the one you kept".

NOPOTIONS' deepest system is gear that drops with prefix slots scaled by
rarity, a fourth slot on legendaries, and "tempering" to swap one. This is that
idea rebuilt for 5e, which matters: an ARPG bolts arbitrary stat soup onto an
item, while 5e already has a precise vocabulary for exactly this — +1/+2/+3,
resistances, a die of extra damage, advantage on a narrow kind of check. So an
affix here reads as a magic-item property a DM would recognise, not a spreadsheet.

All names and effect text are ORIGINAL, written for this project. Nothing here
is copied from any book; the mechanics lean on SRD conventions (a numeric plus,
a damage die, a resistance) that are rules, not text.

Two rules keep this honest:

* **Rarity buys slots, not power.** A legendary is not "a rare with bigger
  numbers"; it has more room for things to be true about it at once.
* **Nothing is hidden.** ``mechanical_bonuses`` is the single place that says
  what an affix actually DOES, so the sheet, the AC calculation and the DM's
  prompt can never disagree about a piece of gear.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class Affix:
    slug: str
    name: str                       # the word itself: "Keen", "of the Ember"
    kind: str                       # "prefix" | "suffix"
    tier: int                       # 1..4 — how good, and which slots it can fill
    applies: tuple                  # "weapon" | "armor" | "shield" | "any"
    text: str                       # own-worded effect, shown to the player
    grants: Dict[str, Any] = field(default_factory=dict)


# How many affixes a piece carries, by catalog rarity. This is the whole
# progression curve: a legendary is not stronger per-affix, it simply has more
# room. Mundane gear carries none — a plain longsword stays a plain longsword,
# which is what makes a rolled one feel like something.
_SLOTS = {
    "": 0, "mundane": 0, "common": 0,
    "uncommon": 1, "rare": 2, "very rare": 3, "legendary": 4, "artifact": 4,
    "varies": 1,
}

# Above this tier an affix needs a rarity that can carry it, so an uncommon
# never rolls a legendary-grade property in its single slot.
_MAX_TIER_BY_SLOTS = {0: 0, 1: 2, 2: 3, 3: 3, 4: 4}

# Grants that are a NUMBER on a d20 roll or on AC. 5e tops these out at +3, so
# a piece never carries two affixes that feed the same one — see roll_affixes.
_NUMERIC_GRANTS = ("attack", "damage_bonus", "ac")
# The ceiling those numbers may reach however they were assembled. A belt to
# the roller's braces: a DM-authored or hand-edited affix set is clamped too.
_NUMERIC_CAP = 3

# How much more likely a piece is to roll a property about ITS OWN kind than a
# generic one. At 1 an armour was half trinket-properties, which read as noise.
_FAMILY_WEIGHT = 3


def slots_for_rarity(rarity: Optional[str]) -> int:
    return _SLOTS.get((rarity or "").strip().lower(), 0)


AFFIXES: List[Affix] = [
    # ---- weapons: prefixes -------------------------------------------------
    Affix("keen", "Keen", "prefix", 1, ("weapon",),
          "Ground to a wicked edge. +1 to attack rolls.", {"attack": 1}),
    Affix("balanced", "Balanced", "prefix", 1, ("weapon",),
          "Weighted true to the hand. +1 to damage rolls.", {"damage_bonus": 1}),
    Affix("cruel", "Cruel", "prefix", 2, ("weapon",),
          "Its wounds are slow to close. On a critical hit, the target cannot "
          "regain hit points until the end of your next turn.",
          {"note": "crit denies healing 1 round"}),
    Affix("unerring", "Unerring", "prefix", 3, ("weapon",),
          "It seems to know where to land. +2 to attack rolls.", {"attack": 2}),
    Affix("dread", "Dread", "prefix", 3, ("weapon",),
          "The first creature struck each turn must succeed on a DC 13 Wisdom "
          "save or be frightened of you until the end of its next turn.",
          {"note": "DC 13 Wis save or frightened"}),
    Affix("sundering", "Sundering", "prefix", 4, ("weapon",),
          "+3 to attack and damage rolls, and it shears through shields and "
          "plate as though they were cloth.",
          {"attack": 3, "damage_bonus": 3}),
    # ---- weapons: suffixes -------------------------------------------------
    Affix("of-the-ember", "of the Ember", "suffix", 1, ("weapon",),
          "Warm to the touch. Deals an extra 1d4 fire damage on a hit.",
          {"damage_dice": "1d4 fire"}),
    Affix("of-hoarfrost", "of Hoarfrost", "suffix", 1, ("weapon",),
          "Rimed even in summer. Deals an extra 1d4 cold damage on a hit.",
          {"damage_dice": "1d4 cold"}),
    Affix("of-the-storm", "of the Storm", "suffix", 2, ("weapon",),
          "It mutters before rain. Deals an extra 1d6 lightning damage on a hit.",
          {"damage_dice": "1d6 lightning"}),
    Affix("of-quiet-hours", "of Quiet Hours", "suffix", 2, ("weapon",),
          "It makes no sound at all. You have advantage on Stealth checks made "
          "while it is drawn.", {"note": "advantage on Stealth while drawn"}),
    Affix("of-the-drowned", "of the Drowned", "suffix", 3, ("weapon",),
          "Salt-pitted and cold. Deals an extra 2d6 cold damage to a creature "
          "that cannot breathe water.", {"damage_dice": "2d6 cold"}),
    Affix("of-the-unlit", "of the Unlit", "suffix", 4, ("weapon",),
          "Light bends away from the blade. Deals an extra 2d8 necrotic damage, "
          "and sheds no light of its own even in torchlight.",
          {"damage_dice": "2d8 necrotic"}),
    # ---- armour & shields --------------------------------------------------
    Affix("sturdy", "Sturdy", "prefix", 1, ("armor", "shield"),
          "Reinforced at every seam. +1 to Armour Class.", {"ac": 1}),
    Affix("warded", "Warded", "prefix", 2, ("armor", "shield"),
          "Sigils worked into the lining. +1 to Armour Class, and advantage on "
          "saves against spells.", {"ac": 1, "note": "advantage on saves vs spells"}),
    Affix("adamant", "Adamant", "prefix", 3, ("armor", "shield"),
          "Layered with a metal that does not care. +2 to Armour Class.", {"ac": 2}),
    Affix("of-the-bulwark", "of the Bulwark", "suffix", 4, ("armor", "shield"),
          "+3 to Armour Class, and critical hits against you become normal hits.",
          {"ac": 3, "note": "crits against you become normal hits"}),
    Affix("of-embers", "of Embers", "suffix", 1, ("armor", "shield"),
          "It never feels cold. You have resistance to fire damage.",
          {"resist": "fire"}),
    Affix("of-the-deep", "of the Deep", "suffix", 2, ("armor", "shield"),
          "Barnacle-flecked. You can breathe water, and have resistance to cold "
          "damage.", {"resist": "cold", "note": "breathe water"}),
    # ---- anything ----------------------------------------------------------
    Affix("of-warning", "of Warning", "suffix", 2, ("any",),
          "It tugs at you before trouble. You cannot be surprised while conscious.",
          {"note": "cannot be surprised"}),
    Affix("of-the-wayfarer", "of the Wayfarer", "suffix", 1, ("any",),
          "Worn smooth by long roads. Your walking speed increases by 5 feet.",
          {"speed": 5}),
    Affix("of-the-silver-tongue", "of the Silver Tongue", "suffix", 2, ("any",),
          "Others find you easier to believe. +1 to Charisma checks.",
          {"note": "+1 to Charisma checks"}),
    Affix("of-the-owl", "of the Owl", "suffix", 1, ("any",),
          "You see a little further into the dark. +1 to Perception checks.",
          {"note": "+1 to Perception checks"}),
]

_BY_SLUG = {a.slug: a for a in AFFIXES}


def affix_by_slug(slug: str) -> Optional[Affix]:
    return _BY_SLUG.get((slug or "").strip().lower())


def _family(item_type: Optional[str], category: Optional[str],
            name: str = "") -> str:
    """Which pool of affixes a piece can draw from."""
    blob = " ".join(x for x in (item_type, category, name) if x).lower()
    if "shield" in blob:
        return "shield"
    if "armor" in blob or "armour" in blob:
        return "armor"
    if any(k in blob for k in ("weapon", "martial", "simple", "sword", "axe",
                               "bow", "dagger", "mace", "spear", "hammer")):
        return "weapon"
    return "any"


def _eligible(family: str, max_tier: int) -> List[Affix]:
    return [a for a in AFFIXES
            if a.tier <= max_tier and (family in a.applies or "any" in a.applies)]


def roll_affixes(item_name: str, rarity: Optional[str], *,
                 item_type: Optional[str] = None,
                 category: Optional[str] = None,
                 seed: Optional[str] = None) -> List[str]:
    """Roll the affixes a dropped piece carries. Returns affix slugs.

    Deterministic per ``seed``, so the same drop re-examined is the same gear —
    the roll happens once, when the world hands it over, and never again by
    accident.
    """
    slots = slots_for_rarity(rarity)
    if slots <= 0:
        return []
    family = _family(item_type, category, item_name)
    pool = _eligible(family, _MAX_TIER_BY_SLOTS.get(slots, 2))
    if not pool:
        return []
    rng = random.Random(seed or f"{item_name}:{rarity}")
    picked: List[Affix] = []
    taken_numeric: set = set()

    def compatible(a: Affix) -> bool:
        if a in picked:
            return False
        # At most ONE prefix. A piece with three of them would still be read
        # aloud as "Keen Longsword" — the other two would be invisible in the
        # only place a name has room for them.
        if a.kind == "prefix" and any(p.kind == "prefix" for p in picked):
            return False
        # Never stack two affixes granting the SAME numeric bonus. 5e tops out
        # at +3, and two +2s would quietly hand out a +4 sword.
        if any(k in taken_numeric for k in a.grants if k in _NUMERIC_GRANTS):
            return False
        return True

    for _ in range(slots):
        choices = [a for a in pool if compatible(a)]
        if not choices:
            break
        # Weight toward the low tiers so a high-tier property stays a find,
        # and toward affixes that are ABOUT this kind of gear: a suit of armour
        # should mostly be armour, not a grab-bag of trinket properties.
        weights = [max(1, 5 - a.tier) * (1 if "any" in a.applies else _FAMILY_WEIGHT)
                   for a in choices]
        chosen = rng.choices(choices, weights=weights, k=1)[0]
        picked.append(chosen)
        taken_numeric.update(k for k in chosen.grants if k in _NUMERIC_GRANTS)
    # A piece reads best with its prefix first and its suffixes after.
    picked.sort(key=lambda a: (a.kind != "prefix", a.tier))
    return [a.slug for a in picked]


def display_name(base_name: str, slugs: Iterable[str]) -> str:
    """"Keen Longsword of the Ember" — the name the world calls it."""
    prefixes = [a.name for s in slugs if (a := affix_by_slug(s)) and a.kind == "prefix"]
    suffixes = [a.name for s in slugs if (a := affix_by_slug(s)) and a.kind == "suffix"]
    out = " ".join(prefixes[:1] + [base_name])
    if suffixes:
        out += " " + suffixes[0]
    return out


def describe_affixes(slugs: Iterable[str]) -> List[dict]:
    """Player-facing rows: what each affix is called and what it does."""
    out = []
    for s in slugs or []:
        a = affix_by_slug(s)
        if a:
            out.append({"slug": a.slug, "name": a.name, "kind": a.kind,
                        "tier": a.tier, "text": a.text})
    return out


def mechanical_bonuses(slugs: Iterable[str]) -> Dict[str, Any]:
    """THE single source of truth for what a set of affixes actually does.

    The sheet, the AC calculation and the DM's prompt all read this, so they
    cannot drift into disagreeing about a piece of gear.
    """
    total: Dict[str, Any] = {}
    notes: List[str] = []
    dice: List[str] = []
    resists: List[str] = []
    for s in slugs or []:
        a = affix_by_slug(s)
        if not a:
            continue
        for key, val in a.grants.items():
            if key == "note":
                notes.append(str(val))
            elif key == "damage_dice":
                dice.append(str(val))
            elif key == "resist":
                resists.append(str(val))
            else:
                total[key] = total.get(key, 0) + int(val)
    for key in _NUMERIC_GRANTS:
        if key in total:
            total[key] = min(int(total[key]), _NUMERIC_CAP)
    if notes:
        total["notes"] = notes
    if dice:
        total["damage_dice"] = dice
    if resists:
        total["resist"] = resists
    return total


def temper_cost_gp(rarity: Optional[str], tier: int) -> int:
    """What a smith charges to reforge one property out of a piece.

    Scaled by what is being replaced, so improving a legendary is a project and
    rerolling a cheap trinket is an afternoon.
    """
    base = {0: 25, 1: 50, 2: 150, 3: 400, 4: 1200}.get(max(0, min(4, tier)), 50)
    return int(base * (1 + 0.25 * slots_for_rarity(rarity)))


def temper_swap(slugs: List[str], replace_slug: str, *,
                item_name: str, rarity: Optional[str],
                item_type: Optional[str] = None,
                category: Optional[str] = None,
                seed: Optional[str] = None) -> List[str]:
    """Reforge ONE property. The replacement is a fresh roll of the same tier —
    it may be worse, which is the whole gamble of the forge.
    """
    old = affix_by_slug(replace_slug)
    if old is None or replace_slug not in slugs:
        return list(slugs)
    family = _family(item_type, category, item_name)
    keep = [s for s in slugs if s != replace_slug]
    cap = _MAX_TIER_BY_SLOTS.get(slots_for_rarity(rarity), 2)
    avail = [a for a in _eligible(family, cap)
             if a.slug not in keep and a.slug != replace_slug]
    # Prefer the same grade; fall back to an adjacent one. With a catalog this
    # size a strict same-tier rule leaves the smith nothing to offer on exactly
    # the pieces a player most wants to reforge.
    pool = [a for a in avail if a.tier == old.tier]
    if not pool:
        pool = [a for a in avail if abs(a.tier - old.tier) == 1]
    if not pool:
        # Nothing of that grade fits this piece at all; the smith hands it back.
        return list(slugs)
    rng = random.Random(seed or f"temper:{item_name}:{replace_slug}")
    new = rng.choice(pool)
    out = keep + [new.slug]
    order = {a.slug: a for a in AFFIXES}
    out.sort(key=lambda s: (order[s].kind != "prefix", order[s].tier))
    return out
