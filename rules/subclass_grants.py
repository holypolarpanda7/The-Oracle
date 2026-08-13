"""What a SUBCLASS grants mechanically, read out of its own feature text.

The project's rule is that the DM narrates and the ENGINE factors — "a number
the model computed is a number nothing checked". Five numbers were escaping
that rule because the code that needed them keyed on CLASS and a subclass had
no way to speak:

  * **Critical range.** ``attack_roll`` hard-coded ``natural == 20``, so the
    SRD Champion's Improved Critical — the entire point of the subclass —
    changed nothing at all.
  * **Extra Attack.** Decided from the class table, so every subclass that
    grants it (Bladesinger, the Valor and Swords bards, and any book one)
    attacked once forever.
  * **Unarmored Defense.** ``_compute_ac`` named ``barbarian`` and ``monk``
    literally, so a subclass that sets its own base AC was worth nothing.
  * **Senses.** A subclass granting Darkvision never reached the board.
  * **Third-caster spellcasting.** Arcane Trickster and Eldritch Knight are
    SRD subclasses whose Spellcasting feature produced no spell slots.

Reading the feature TEXT is the door this project already opens for a species'
damage resistance (``_pc_defenses``), War Caster's somatic waiver
(``_hands_gate``) and the cartography check: a subclass from a book the repo
may not carry needs nothing added to any list, only its own summary. Everything
here is DERIVED at read time and stored nowhere, so it can never disagree with
the feature it came from.

The one exception is senses, which are persisted as a ``sense:`` tag on the
character — the board reads the character row with plain SQL on purpose, so it
never has to know what a subclass is (the same channel an invocation's
Devil's Sight already uses).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

#: Ability words as they appear in a stat block, long or short.
_ABILITY = {
    "str": "strength", "strength": "strength",
    "dex": "dexterity", "dexterity": "dexterity",
    "con": "constitution", "constitution": "constitution",
    "int": "intelligence", "intelligence": "intelligence",
    "wis": "wisdom", "wisdom": "wisdom",
    "cha": "charisma", "charisma": "charisma",
}
_ABIL_RX = "|".join(sorted(_ABILITY, key=len, reverse=True))

# "score a Critical Hit on a roll of 19 or 20", "on an 18-20", "on a d20 roll
# of 7 as well as 20". Captured as the SET of naturals that crit, because the
# third phrasing is not a threshold and treating it as one would make every
# roll of 8 or better a critical hit.
_CRIT_RX = re.compile(r"[Cc]ritical [Hh]it[^.]{0,60}", re.S)
_NUM_RX = re.compile(r"\b(\d{1,2})\b")

_EXTRA_ATTACK_RX = re.compile(r"\battack twice\b|\bExtra Attack\b", re.I)

# "base AC 10 + your DEX + your CHA", "base AC = 10 + DEX + CHA"
_UNARMORED_RX = re.compile(
    r"base\s+A(?:rmor\s+)?C(?:lass)?\s*(?:equals|=|is)?\s*10\s*\+\s*"
    r"(?:your\s+)?(" + _ABIL_RX + r")(?:\s+modifier)?\s*(?:\+|and|,)\s*"
    r"(?:your\s+)?(" + _ABIL_RX + r")", re.I)

# "gain a bonus to AC equal to your CHA modifier"
_AC_BONUS_RX = re.compile(
    r"bonus to (?:your )?AC equal to your\s+(" + _ABIL_RX + r")\s*modifier", re.I)

_DARKVISION_RX = re.compile(r"Darkvision\s+(\d{2,3})\s*ft", re.I)

# A subclass that teaches a non-caster class to cast: the SRD names the feature
# exactly "Spellcasting" for both Arcane Trickster and Eldritch Knight.
_SPELLCASTING_NAMES = {"spellcasting", "pact magic"}

#: Classes that get their slots from their own table. A subclass Spellcasting
#: feature on anything else is a THIRD caster.
FULL_AND_HALF_CASTERS = {
    "bard", "cleric", "druid", "sorcerer", "wizard", "warlock",
    "paladin", "ranger", "artificer",
}

#: Third-caster spell slots by character level (Arcane Trickster / Eldritch
#: Knight / any subclass that teaches an otherwise non-casting class). Index is
#: the character level; the row is slots at spell levels 1..4.
THIRD_CASTER_SLOTS: Dict[int, List[int]] = {
    3: [2], 4: [3], 5: [3], 6: [3],
    7: [4, 2], 8: [4, 2], 9: [4, 2],
    10: [4, 3], 11: [4, 3], 12: [4, 3],
    13: [4, 3, 2], 14: [4, 3, 2], 15: [4, 3, 2],
    16: [4, 3, 3], 17: [4, 3, 3], 18: [4, 3, 3],
    19: [4, 3, 3, 1], 20: [4, 3, 3, 1],
}


@dataclass
class Grants:
    """Everything a subclass hands the ENGINE, as opposed to the narration."""
    #: Lowest natural d20 that is a critical hit (20 = the default rule).
    crit_on: int = 20
    #: Naturals below ``crit_on`` that also crit ("a 7 as well as a 20").
    crit_extra: Set[int] = field(default_factory=set)
    extra_attack: bool = False
    #: (ability, ability) whose modifiers replace armour, e.g. dex + charisma.
    unarmored_ac: Optional[Tuple[str, str]] = None
    #: An ability modifier added to AC while unarmoured.
    ac_bonus_ability: Optional[str] = None
    senses: Dict[str, int] = field(default_factory=dict)
    third_caster: bool = False

    def crit_naturals(self) -> Set[int]:
        return set(range(self.crit_on, 21)) | set(self.crit_extra)


#: "18-20" is a RANGE and means 18, 19 and 20. Written as two numbers it looks
#: identical to "7 or 20", which is a pair — so the hyphen has to be read.
_RANGE_RX = re.compile(r"\b(\d{1,2})\s*[-‐-―]\s*(\d{1,2})\b")


def _crit_from(text: str) -> Optional[Set[int]]:
    """The naturals a sentence says are critical hits, or None if it says none."""
    m = _CRIT_RX.search(text or "")
    if not m:
        return None
    frag = m.group(0)
    nums: Set[int] = set()
    for lo, hi in _RANGE_RX.findall(frag):
        lo_i, hi_i = int(lo), int(hi)
        if 1 <= lo_i <= hi_i <= 20:
            nums |= set(range(lo_i, hi_i + 1))
    frag_wo = _RANGE_RX.sub(" ", frag)
    nums |= {int(n) for n in _NUM_RX.findall(frag_wo)}
    # "on the d20" and "1d20" are not crit ranges; a natural is 1..20.
    nums = {n for n in nums if 1 <= n <= 20}
    return nums or None


def grants_from_features(features: Iterable[Dict[str, Any]], *,
                         class_name: str = "") -> Grants:
    """Read a level-filtered list of ``{level, name, summary}`` into Grants.

    The caller filters by level, so this never has to know what level anybody
    is — it answers "given these features, what does the engine do differently".
    """
    g = Grants()
    naturals: Set[int] = set()
    for f in features or []:
        name = str((f or {}).get("name") or "")
        text = f"{name}. {str((f or {}).get('summary') or '')}"

        got = _crit_from(text)
        if got:
            naturals |= got

        if _EXTRA_ATTACK_RX.search(text):
            g.extra_attack = True

        m = _UNARMORED_RX.search(text)
        if m:
            g.unarmored_ac = (_ABILITY[m.group(1).lower()],
                              _ABILITY[m.group(2).lower()])
        m = _AC_BONUS_RX.search(text)
        if m:
            g.ac_bonus_ability = _ABILITY[m.group(1).lower()]

        for rng in _DARKVISION_RX.findall(text):
            g.senses["darkvision"] = max(g.senses.get("darkvision", 0), int(rng))

        if name.strip().lower() in _SPELLCASTING_NAMES and \
                (class_name or "").strip().lower() not in FULL_AND_HALF_CASTERS:
            g.third_caster = True

    if naturals:
        # A contiguous run ending at 20 is a THRESHOLD (19-20, 18-20). Anything
        # else keeps 20 as the threshold and carries the odd numbers alongside,
        # because "a 7 as well as a 20" read as a threshold would make a roll of
        # 8 a critical hit.
        top = 20
        while top - 1 in naturals:
            top -= 1
        if 20 in naturals and top < 20:
            g.crit_on = top
            g.crit_extra = {n for n in naturals if n < top}
        else:
            g.crit_extra = {n for n in naturals if n < 20}
    return g


def third_caster_slots(level: int) -> List[int]:
    """Slot counts at spell levels 1..4 for a third caster of this level."""
    return list(THIRD_CASTER_SLOTS.get(max(1, min(20, int(level or 1))), []))
