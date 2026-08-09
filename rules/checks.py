"""What a d20 check is worth — the skill table and the modifier arithmetic.

The DM model was computing these. A `[[ROLL: 1d20+5 | Stealth | DC 15]]` hook
carries a `+5` that the LLM worked out by reading the sheet, which means the
one number that decides the outcome is the one number nothing verified: it
drifts with proficiency, expertise, exhaustion, a curse penalty and the
character's own Dexterity, and a model that gets it wrong is indistinguishable
from a model that got it right.

Nothing here reads a database. It is the table (which ability each skill uses)
and the arithmetic (how proficiency, expertise and penalties combine), so the
backend can hand it a sheet's numbers and get one answer, and the DM can name
a check instead of computing one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

#: The six, by their short keys.
ABILITIES: Tuple[str, ...] = ("str", "dex", "con", "int", "wis", "cha")

ABILITY_NAMES: Dict[str, str] = {
    "str": "strength", "dex": "dexterity", "con": "constitution",
    "int": "intelligence", "wis": "wisdom", "cha": "charisma",
}

#: The eighteen skills and the ability each one keys off. This did not exist
#: anywhere in the project, which is exactly why the model was being asked for
#: the modifier — there was nothing to ask instead.
SKILL_ABILITY: Dict[str, str] = {
    "acrobatics": "dex",
    "animal handling": "wis",
    "arcana": "int",
    "athletics": "str",
    "deception": "cha",
    "history": "int",
    "insight": "wis",
    "intimidation": "cha",
    "investigation": "int",
    "medicine": "wis",
    "nature": "int",
    "perception": "wis",
    "performance": "cha",
    "persuasion": "cha",
    "religion": "wis",
    "sleight of hand": "dex",
    "stealth": "dex",
    "survival": "wis",
}

_ALIASES = {
    "animal-handling": "animal handling", "sleight-of-hand": "sleight of hand",
    "sleight of hands": "sleight of hand", "perc": "perception",
    "str": "str", "dex": "dex", "con": "con", "int": "int", "wis": "wis",
    "cha": "cha", "strength": "str", "dexterity": "dex",
    "constitution": "con", "intelligence": "int", "wisdom": "wis",
    "charisma": "cha",
}


def normalize(name: Optional[str]) -> Optional[str]:
    """A skill name, an ability key, or None.

    Returns a SKILL name when it is one, otherwise a three-letter ability key —
    both are things a check can be made with, and the caller wants to know
    which it got.
    """
    s = re.sub(r"\s+", " ", str(name or "").strip().lower())
    s = s.replace("_", " ")
    if not s:
        return None
    s = _ALIASES.get(s, s)
    if s in SKILL_ABILITY or s in ABILITIES:
        return s
    # "a Stealth check", "Dexterity (Acrobatics)" — find the skill inside.
    for skill in SKILL_ABILITY:
        if skill in s:
            return skill
    for key, ab in _ALIASES.items():
        if re.search(rf"\b{re.escape(key)}\b", s):
            return ab
    return None


def ability_for(name: str) -> Optional[str]:
    """Which ability a named check uses — a skill's, or the ability itself."""
    n = normalize(name)
    if n is None:
        return None
    return SKILL_ABILITY.get(n, n if n in ABILITIES else None)


@dataclass
class CheckModifier:
    """One check's total, and the parts it was built from."""
    total: int = 0
    ability: str = ""
    ability_mod: int = 0
    proficiency: int = 0
    penalty: int = 0
    proficient: bool = False
    expertise: bool = False
    #: "Dexterity (Stealth) +7" — what the table should be shown.
    label: str = ""

    def describe(self) -> str:
        bits = [f"{ABILITY_NAMES.get(self.ability, self.ability).title()} "
                f"{self.ability_mod:+d}"]
        if self.proficiency:
            bits.append(f"proficiency {self.proficiency:+d}"
                        + (" (expertise)" if self.expertise else ""))
        if self.penalty:
            bits.append(f"penalty {self.penalty:+d}")
        return f"{self.label} {self.total:+d} — " + ", ".join(bits)


def check_modifier(name: str, *, ability_mod: int, proficiency_bonus: int,
                   proficient: bool = False, expertise: bool = False,
                   penalty: int = 0) -> CheckModifier:
    """Add a check up. Expertise doubles proficiency; a penalty is flat.

    Deliberately dumb arithmetic in one place, so the DM's board, the roll
    hook and the character sheet cannot disagree about what a Stealth check is
    worth — which they will the moment two of them compute it separately.
    """
    n = normalize(name) or "str"
    ability = SKILL_ABILITY.get(n, n if n in ABILITIES else "str")
    prof = proficiency_bonus * (2 if expertise else 1) if proficient else 0
    label = (f"{ABILITY_NAMES.get(ability, ability).title()} ({n.title()})"
             if n in SKILL_ABILITY
             else ABILITY_NAMES.get(ability, ability).title())
    return CheckModifier(
        total=ability_mod + prof + penalty, ability=ability,
        ability_mod=ability_mod, proficiency=prof, penalty=penalty,
        proficient=proficient, expertise=expertise, label=label)


# ---------------------------------------------------------------------------
# Damage the rules compute for you
# ---------------------------------------------------------------------------

#: Falling: 1d6 bludgeoning per 10 feet, to a maximum of 20d6. A number the DM
#: should never be inventing, and the commonest one they were asked to.
FALL_DICE_CAP = 20


def falling_damage(distance_ft: int) -> Tuple[str, str]:
    """``("6d6", "bludgeoning")`` for a 60-foot drop. ``("", "")`` under 10 ft."""
    dice = min(FALL_DICE_CAP, max(0, int(distance_ft)) // 10)
    return (f"{dice}d6", "bludgeoning") if dice else ("", "")
