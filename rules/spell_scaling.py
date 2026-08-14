"""How a spell grows: with the CASTER's level, or with the SLOT it was cast in.

Both rules were in the data and neither reached a die roll. ``Spell.damage``
carries structured ``damage_at_slot_level`` / ``damage_at_character_level`` rows
and the engine reads them correctly — but only **17 of 430** spells in this
project have that column filled, because everything here came from the
owned-book PDF parse rather than the SRD JSON. Every other spell fell to
``damage.parse_damage(desc)``, which returns the BASE dice and knows nothing
about levels. Two consequences, both silent:

  * A cantrip never grew. A level-17 wizard's Fire Bolt rolled ``1d10`` instead
    of ``4d10`` — a quarter of its damage, on the most-used attack in the game.
  * Upcasting did nothing at all. Fireball from a 5th-level slot dealt exactly
    what it deals from a 3rd.

And ``Spell.higher_level`` — a column that states the upcast rule in so many
words — was read by NOTHING.

The rule is taken from the spell's own prose, which states it exactly:

    "The damage increases by ld6 when you reach levels 5 (2d6), 11 (3d6), and
     17 (4d6)."                                     <- cantrip, explicit table
    "The damage in­ creases by ld6 for each spell slot level above 3."
    "+1d10 force for each slot level above 2nd."     <- the higher_level column

Reading the stated TABLE rather than applying a general "one more die per tier"
is not pedantry, it is the only safe option: **Eldritch Blast scales BEAMS, not
dice** ("the spell creates two beams at level 5"), and Magic Missile creates
"one more dart for each spell slot level above 1". A generic rule turns four
1d10 beams into a single 4d10 hit and quadruples a dart's damage. Both spells
state no dice table, so both are correctly left alone.

OCR damage is handled by ``rules.damage.clean_dice`` — the same repair that
already turns "ldlO" into "1d10" — because this is the same book text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from .damage import clean_dice

#: The character levels a cantrip steps up at (2024 SRD, and 2014's are the same).
CANTRIP_TIERS = (5, 11, 17)

# "levels 5 (2d6), 11 (3d6), and 17 (4d6)" — the parenthesised dice keyed to the
# level in front of them. Tolerant of the OCR's stray spaces and l/O confusion.
_TIER_RX = re.compile(
    r"\b(\d{1,2})\s*\(\s*([0-9lIiOoS]{1,3}\s*[dD]\s*[0-9lIiOoS]{1,3})"
    r"(?:\s*or\s*[0-9lIiOoS]{1,3}\s*[dD]\s*[0-9lIiOoS]{1,3})?\s*\)")

# "increases by ld6 for each spell slot level above 3" / "+1d10 force for each
# slot level above 2nd". The soft hyphen in "in­ creases" is an OCR artifact, so
# the verb is matched loosely.
_UPCAST_RX = re.compile(
    r"(?P<kind>damage|healing|hit points)?[^.]{0,40}?"
    r"(?:incre\W{0,3}ases?\s+by|\+)\s*"
    r"(?P<dice>[0-9lIiOoS]{1,3}\s*[dD]\s*[0-9lIiOoS]{1,3})"
    r"[^.]{0,40}?for\s+(?:each|every)\s+"
    r"(?:(?P<per>\d{1,2}|two|three|four)\s+)?"
    r"(?:spell\s+)?slot\s+levels?\s+above\s+"
    r"(?P<from>\d{1,2})", re.I)

#: "for every TWO slot levels above 3rd" — a step of more than one. Spirit
#: Shroud is written this way and did not scale at all, because the pattern
#: only ever admitted "for each slot level".
_PER_WORDS = {"two": 2, "three": 3, "four": 4}

_DICE_SPLIT = re.compile(r"^\s*(\d+)\s*[dD]\s*(\d+)\s*$")

#: "The damage increases by <something>" — a promise of DICE. If the dice
#: themselves did not survive the PDF ("i<18" for "1d8"), that is a gap the
#: DM has to be told about rather than a number to guess.
_DICE_PROMISED_RX = re.compile(
    r"(?:dam\s*age|da\s*mage|healing|hit points)\s+incre\w*\s+by", re.I)


@dataclass
class Scaling:
    """What a spell's own text says about growing."""
    #: character level -> the whole dice expression at that tier ({5: "2d6"}).
    cantrip_tiers: Dict[int, str] = field(default_factory=dict)
    #: extra dice per step above ``upcast_from`` ("1d6").
    upcast_dice: Optional[str] = None
    upcast_from: Optional[int] = None
    #: Slot levels PER step — 1 for "each slot level", 2 for "every two".
    upcast_per: int = 1
    #: "damage" or "healing" — a cure scales its healing, not its damage.
    upcast_kind: str = "damage"
    #: The spell states an upcast rule whose dice the extractor destroyed.
    upcast_unreadable: bool = False

    def __bool__(self) -> bool:
        return bool(self.cantrip_tiers or self.upcast_dice
                    or self.upcast_unreadable)


#: The PDF extractor leaves SOFT HYPHENS where it broke a word across lines, so
#: "increases" arrives as "in\u00ad creases". Nothing downstream can match a verb
#: split in the middle, and it is the verb this module keys on — Fireball's
#: entire upcast rule was invisible for exactly this reason.
_SOFTBREAK_RX = re.compile(r"\u00ad\s*")
_LINEBREAK_RX = re.compile(r"(?<=\w)-\s*\n\s*(?=\w)")


def _dehyphenate(text: str) -> str:
    return _LINEBREAK_RX.sub("", _SOFTBREAK_RX.sub("", text or ""))


def _sources(spell: Any) -> str:
    return _dehyphenate(" ".join(
        str(x) for x in (getattr(spell, "higher_level", None) or "",
                         getattr(spell, "desc", None) or "") if x))


def parse_scaling(spell: Any) -> Scaling:
    """Read a spell's growth rule out of ``higher_level`` and its description."""
    text = _sources(spell)
    out = Scaling()
    if not text.strip():
        return out

    if int(getattr(spell, "level", 0) or 0) == 0:
        # A cantrip states its whole table. Only the canonical tiers are taken,
        # so a stray "(2d6)" elsewhere in the prose cannot invent a step.
        for lvl_s, dice_s in _TIER_RX.findall(text):
            lvl = int(lvl_s)
            if lvl not in CANTRIP_TIERS:
                continue
            dice = clean_dice(dice_s)
            if dice:
                out.cantrip_tiers[lvl] = dice
        return out

    m = _UPCAST_RX.search(text)
    if m:
        dice = clean_dice(m.group("dice"))
        if dice:
            out.upcast_dice = dice
            out.upcast_from = int(m.group("from"))
            per_raw = (m.group("per") or "1").strip().lower()
            out.upcast_per = max(1, _PER_WORDS.get(per_raw)
                                 or (int(per_raw) if per_raw.isdigit() else 1))
            # The `kind` group is optional and can match EMPTY at the start of
            # the sentence, so it cannot be trusted on its own — "The healing
            # increases by 2d8" came back as damage. Read the sentence the
            # match sits in instead.
            start = text.rfind(".", 0, m.start()) + 1
            sentence = text[start:m.end()].lower()
            out.upcast_kind = ("healing"
                               if ("healing" in sentence or "hit points" in sentence)
                               else "damage")
    else:
        # The book SAYS it upcasts and the dice are unreadable (the extractor
        # turns "1d8" into things like "i<18"). Guessing a die here would inject
        # a wrong number into a damage roll, which is worse than not scaling —
        # so it is flagged for the DM instead, the same way an unevaluable
        # metamagic condition is.
        # Precisely: the book says a QUANTITY OF DICE increases and no dice
        # survived the extraction. Magic Missile ("creates one more dart") and
        # Eldritch Blast ("creates two beams") state their scaling perfectly
        # well and must not be flagged — they simply do not scale in dice.
        if _DICE_PROMISED_RX.search(text):
            out.upcast_unreadable = True
    return out


def _add_dice(base: Optional[str], extra: str, times: int) -> Optional[str]:
    """``8d6`` + 2 x ``1d6`` -> ``10d6``; different die sizes are appended."""
    if times <= 0 or not extra:
        return base
    em = _DICE_SPLIT.match(extra)
    if not base:
        return f"{int(em.group(1)) * times}d{em.group(2)}" if em else base
    if not em:
        return base
    e_n, e_d = int(em.group(1)), int(em.group(2))
    # Only the LEADING dice term is grown; a trailing "+3" modifier is kept.
    bm = re.match(r"^\s*(\d+)\s*[dD]\s*(\d+)\s*(.*)$", base.strip())
    if not bm:
        return f"{base}+{e_n * times}d{e_d}"
    b_n, b_d, rest = int(bm.group(1)), int(bm.group(2)), bm.group(3).strip()
    if b_d == e_d:
        return f"{b_n + e_n * times}d{b_d}{rest}"
    return f"{b_n}d{b_d}+{e_n * times}d{e_d}{rest}"


def scaled_dice(spell: Any, base: Optional[str], *,
                character_level: int = 1,
                slot_level: Optional[int] = None) -> Optional[str]:
    """``base`` grown by whichever rule this spell states. Never shrinks it.

    A spell that states no rule — and every spell whose growth is in beams or
    darts rather than dice — comes back exactly as it went in.
    """
    if not base:
        return base
    sc = parse_scaling(spell)
    if not sc:
        return base
    if sc.cantrip_tiers:
        best = None
        for tier in sorted(sc.cantrip_tiers):
            if int(character_level or 1) >= tier:
                best = sc.cantrip_tiers[tier]
        return best or base
    if sc.upcast_dice and sc.upcast_from is not None and slot_level is not None:
        steps = (int(slot_level) - int(sc.upcast_from)) // max(1, sc.upcast_per)
        if steps > 0:
            return _add_dice(base, sc.upcast_dice, steps)
    return base


def scaling_note(spell: Any) -> str:
    """One short line for the DM board, or "" when a spell doesn't grow."""
    sc = parse_scaling(spell)
    if sc.cantrip_tiers:
        parts = ", ".join(f"L{k} {v}" for k, v in sorted(sc.cantrip_tiers.items()))
        return f"scales: {parts}"
    if sc.upcast_dice and sc.upcast_from is not None:
        what = "healing" if sc.upcast_kind == "healing" else "damage"
        every = ("slot level" if sc.upcast_per == 1
                 else f"{sc.upcast_per} slot levels")
        return (f"upcast: +{sc.upcast_dice} {what} per {every} above "
                f"{sc.upcast_from}")
    if sc.upcast_unreadable:
        return "upcast: this spell scales, but the book text is damaged — rule it"
    return ""


# "deals an extra 1d8 damage", "deal an extra ld6 Force damage" — the phrasing
# every per-attack rider spell uses. The type may sit between the dice and the
# word "damage", or in a whole separate sentence (Spirit Shroud and Conjure
# Minor Elementals both name it later), which is why `damage.parse_damage`
# cannot find these: it wants the type adjacent.
#: The extractor also splits words in the MIDDLE — "dam age", "da mage" — and
#: 13 spells write the word that way. A pattern that only accepts "damage"
#: silently drops every one of them.
_DAMAGE_WORD = r"(?:dam\s*age|da\s*mage)"

_RIDER_RX = re.compile(
    r"extra\s+(?P<dice>[0-9lIiOoS]{1,3}\s*[dD]\s*[0-9lIiOoS]{1,3})"
    r"(?:\s+(?P<type>[A-Za-z]+))?\s+" + _DAMAGE_WORD, re.I)

#: Words that follow the dice but are not a damage type.
_NOT_A_TYPE = {"damage", "of", "when", "to", "on", "against", "per", "and"}


def rider_dice(spell: Any, *, slot_level: Optional[int] = None,
               character_level: int = 1) -> Optional[str]:
    """The per-attack damage a buff spell adds, already grown for the slot.

    Spirit Shroud, Conjure Minor Elementals, Hunter's Mark and their kin do not
    deal damage themselves — they add dice to YOUR attacks for the duration.
    Nothing could read that: the number sits in prose with its type in another
    sentence, so ``parse_damage`` returns nothing and the spell's whole effect
    was invisible to the engine.
    """
    m = _RIDER_RX.search(_dehyphenate(str(getattr(spell, "desc", None) or "")))
    if not m:
        return None
    base = clean_dice(m.group("dice"))
    if not base:
        return None
    return scaled_dice(spell, base, character_level=character_level,
                       slot_level=slot_level)


def rider_type(spell: Any) -> Optional[str]:
    """The rider's damage type when the text names one adjacent to the dice.

    Returns None when the spell lets the caster choose (Conjure Minor
    Elementals: "Acid, Cold, Fire, or Lightning (your choice)") — the caller
    then treats it as untyped, which the damage layer passes through unreduced
    rather than guessing a resistance interaction.
    """
    m = _RIDER_RX.search(_dehyphenate(str(getattr(spell, "desc", None) or "")))
    if not m:
        return None
    word = (m.group("type") or "").strip().lower()
    if not word or word in _NOT_A_TYPE:
        return None
    from .damage import normalize_type
    return normalize_type(word)

