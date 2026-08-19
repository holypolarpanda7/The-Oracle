"""What a spell TARGETS — the one place that question is answered.

`[[CAST]]` has always taken a name and a slot level, so "a creature you can
see within range" was enforced by nobody: the range sat in `Spell.range` as a
string, the area sat in the description as prose, and the board — the only
thing that knows where anyone is standing — was never asked. A player could
Fire Bolt a target sixty feet past the spell's reach, through a wall, and the
only thing standing between that and the table was the DM noticing.

This module reads the spell and says what a targeting UI (and the cast gate)
needs: how far it reaches, whether it wants a creature or a point on the
ground, and — when it throws a template — what shape and how big.

**Derived, never stored.** Same doctrine as `rules/components.py` and
`rules/damage.py`: a number cached beside the sentence it came from drifts the
moment a re-parse improves one of them. Range comes from
`rules.metamagic.facts_for`, which is already the one place a range string is
read; the area comes from the description here.

**OCR tolerance is the job, not a nicety.** Every spell in this project came
out of a PDF, and the shapes arrive as `20-foot -radius Sphere`,
`100-foot -long, 5-foot-wide Line` and `15-foot Ema\xadnation` — a soft hyphen
*inside the word*. A parser that only accepts clean input reads the small
fraction that happens to be clean and silently reports "no area" for the rest,
which is the failure that puts a fireball on one square.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from . import metamagic

#: Shapes the board can actually draw (`vtt.geometry.area_squares`). Anything
#: else is left as no area at all — a template the grid can't resolve is worse
#: than telling the DM to place it, because it would draw a *wrong* one.
SHAPES = ("sphere", "cone", "line", "cube", "cylinder", "emanation")


@dataclass
class TargetSpec:
    """How a spell is aimed.

    ``kind`` drives the UI: ``creature`` lights up tokens to click,
    ``area`` puts a template on the cursor, ``self`` needs no click at all,
    and ``special`` means the text couldn't be read — the DM adjudicates,
    which is the honest answer and never a silent refusal.
    """
    kind: str = "creature"            # creature | area | self | special
    range_ft: Optional[int] = None
    range_kind: str = "ranged"        # self | touch | ranged | sight | unlimited | special
    needs_sight: bool = True
    count: int = 1                    # how many creatures may be picked
    shape: str = ""
    radius_ft: int = 0
    length_ft: int = 0
    width_ft: int = 5
    #: Where the template starts: the caster (a cone from you) or a point you
    #: choose within range (a fireball). Decided by the spell's RANGE, because
    #: "Range: Self" is exactly how the books say a shape originates on you.
    origin: str = "point"             # self | point
    note: str = ""

    @property
    def is_area(self) -> bool:
        return self.kind == "area" and bool(self.shape)

    @property
    def wants_click(self) -> bool:
        """Does aiming this need the player to pick something on the board?

        A cone from yourself still does — the click sets its DIRECTION. An
        emanation or a cube centred on you doesn't: it lands where you stand.
        """
        if self.kind == "self":
            return False
        if self.kind == "area" and self.origin == "self":
            return self.shape in ("cone", "line")
        return self.kind in ("creature", "area")


#: The start of the NEXT spell's entry. Descriptions in this corpus routinely
#: run on into the following spell — Magic Missile's text ends "…above 1.
#: MAGIC MOUTH Level 2 Illusion (Bard, Wizard) Casting…" — and reading the
#: whole cell gave Magic Missile the 20-foot Cube that belongs to Magic Mouth.
#: An area attributed to the wrong spell is the worst outcome this module has:
#: it aims a template that the spell does not have.
_NEXT_ENTRY_RE = re.compile(r"\b[A-Z][A-Z][A-Z '’-]{2,}\s+Level\s+\d")


def _head(text: str) -> str:
    """The part of the cell that belongs to THIS spell."""
    t = str(text or "")
    m = _NEXT_ENTRY_RE.search(t)
    return t[:m.start()] if m else t


def _clean(text: str) -> str:
    """Undo what the PDF extractor does to the words a shape match depends on.

    A soft hyphen (U+00AD) lands *inside* words at the extractor's line breaks
    and is usually followed by a space — `Ema\xad nation`, `simultane\xad
    ously` — so stripping the hyphen alone still leaves two words where the
    text has one. A hard hyphen plus newline does the same job visibly.
    """
    t = str(text or "")
    t = re.sub("[­]\\s*", "", t)          # soft hyphen AND the space after it
    t = t.replace("‐", "-").replace("‑", "-")
    t = re.sub(r"-\s*\n\s*", "", t)            # word broken across a line
    t = re.sub(r"\s+", " ", t)
    return t


def _loose(word: str) -> str:
    """A shape name that tolerates spaces dropped inside it by the extractor.

    Grease's area arrives as `10-foot squ are` and Prone as `Pron ~`. The
    shape vocabulary is small and closed, so spelling each one out letter by
    letter is a bounded tolerance rather than a wildcard — and it is anchored
    by the `N-foot` measurement in front of it, so it cannot drift onto prose.
    """
    return r"\s*".join(word)


_SHAPES_ALT = "|".join(_loose(w) for w in (
    "sphere", "cone", "line", "cube", "cylinder", "emanation", "square",
    "radius"))

#: The same OCR confusions `rules.damage` repairs inside a dice expression,
#: for the same reason and in the same direction: Cone of Cold's area arrives
#: as `6O-foot Cone`, with the letter O standing in for a zero. Read strictly,
#: the spell simply has no area — a 60-foot cone becomes a single square.
_DIGIT_FIX = str.maketrans({"l": "1", "I": "1", "O": "0", "o": "0", "S": "5"})


def _feet(raw: str) -> int:
    """A measurement the extractor may have lettered. ``"6O"`` -> ``60``."""
    return int(str(raw).translate(_DIGIT_FIX))


#: A measured shape. The spacing is deliberately loose: the extractor emits
#: `20-foot -radius Sphere` (space before the second hyphen) and
#: `15-foot Cone` interchangeably, and `foot`/`feet`/`ft` all appear. The
#: digits admit their OCR stand-ins, but only in the SECOND position onward —
#: a leading `l` or `O` would match half the words in the corpus.
_DIM = r"(\d[\dlIOoS]*)\s*-?\s*(?:foot|feet|ft)\b"
_AREA_RE = re.compile(
    _DIM + r"[\s-]*(?:radius|long|wide|tall|high)?[\s,-]*"
    r"(" + _SHAPES_ALT + r")\b",
    re.IGNORECASE)

#: A Line prints both of its dimensions — "100-foot-long, 5-foot-wide Line".
_LINE_RE = re.compile(
    _DIM + r"[\s-]*long[\s,-]*" + _DIM + r"[\s-]*wide", re.IGNORECASE)

#: How far into a description this spell's OWN area can be stated. Measured,
#: not guessed: across the 115 areas in the corpus the median match sits 62
#: characters in and 90% land within 301, while every match past ~500 is a
#: neighbouring spell's text bled into the cell — Plane Shift (a Touch spell)
#: picking up Plant Growth's 100-foot Sphere at offset 1059, Tenser's Floating
#: Disk at 2568. `_head` catches the bleed that announces itself with a
#: header; this catches the bleed that just runs on.
_MAX_AREA_OFFSET = 500

#: An area needs AoE LANGUAGE around it, not merely a measurement and a noun.
#: Teleportation Circle says "you draw a 5-foot-radius circle on the ground" —
#: a circle drawn in chalk, matching the shape pattern perfectly and affecting
#: no area at all.
_AOE_LANGUAGE_RE = re.compile(
    r"each creature|all creatures|any creature in|creatures? in the\s+\w+|"
    r"each target|centered on|originating from|within the (?:sphere|cube|cone|"
    r"line|area|emanation|cylinder)", re.IGNORECASE)

_COUNT_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_COUNT_RE = re.compile(
    r"up to (\w+) creatures?|(\w+) creatures? of your choice", re.IGNORECASE)


def parse_area(desc: str) -> tuple[str, int, int, int]:
    """``(shape, radius_ft, length_ft, width_ft)`` — ``("", 0, 0, 0)`` for none."""
    t = _clean(_head(desc))
    if not t:
        return "", 0, 0, 0

    def _own(m: Optional[re.Match]) -> bool:
        """Is this match this spell's own area, or the next spell's?"""
        return m is not None and m.start() <= _MAX_AREA_OFFSET

    m = _LINE_RE.search(t)
    if _own(m):
        return "line", 0, _feet(m.group(1)), _feet(m.group(2))

    m = _AREA_RE.search(t)
    if _own(m):
        size = _feet(m.group(1))
        word = re.sub(r"\s+", "", m.group(2)).lower()
        if word == "radius":
            # A measurement and the bare word "radius" is not yet an area —
            # it is also how the book describes a circle drawn on the ground.
            return ("sphere", size, 0, 0) if _AOE_LANGUAGE_RE.search(t) else ("", 0, 0, 0)
        if word == "square":
            word = "cube"
        if word in ("sphere", "cylinder"):
            return word, size, 0, 0
        if word == "emanation":
            return "emanation", size, 0, 0
        if word == "cone":
            return "cone", 0, size, 0
        if word == "cube":
            return "cube", 0, size, 0
        if word == "line":
            return "line", 0, size, 5
    # A Line whose width never got printed still has a length worth drawing.
    if re.search(r"\bline\b", t, re.IGNORECASE):
        m = re.search(_DIM + r"[\s-]*long", t, re.IGNORECASE)
        if _own(m):
            return "line", 0, _feet(m.group(1)), 5
    return "", 0, 0, 0


def _target_count(desc: str) -> int:
    m = _COUNT_RE.search(_clean(_head(desc)))
    if not m:
        return 1
    raw = (m.group(1) or m.group(2) or "").strip().lower()
    if raw.isdigit():
        return max(1, min(20, int(raw)))
    return _COUNT_WORDS.get(raw, 1)


def spec_for(spell: Any) -> TargetSpec:
    """Read a `Spell` row into a :class:`TargetSpec`.

    The order matters. A shape in the description wins over everything, and
    the RANGE then says where that shape starts — "Range: Self" plus a Cone is
    a cone from you, "Range: 150 feet" plus a Sphere is a fireball you drop on
    a point. Getting that backwards would centre every Burning Hands on the
    square the player clicked, sixty feet away.
    """
    f = metamagic.facts_for(spell)
    desc = str(getattr(spell, "desc", "") or "")
    spec = TargetSpec(range_ft=f.range_ft, range_kind=f.range_kind)

    shape, radius, length, width = parse_area(desc)
    if shape:
        spec.kind = "area"
        spec.shape = shape
        spec.radius_ft, spec.length_ft, spec.width_ft = radius, length, width or 5
        spec.origin = "self" if f.range_kind == "self" else "point"
        if spec.origin == "self":
            # The template starts on the caster, so there is no range to check
            # against a clicked point — its reach IS the shape's own size.
            spec.range_ft = max(radius, length)
        # You may drop a template into darkness; you only need line of EFFECT.
        spec.needs_sight = False
        return spec

    if f.range_kind == "self":
        spec.kind = "self"
        spec.needs_sight = False
        spec.range_ft = 0
        return spec

    if f.range_kind == "touch":
        spec.range_ft = 5
        spec.note = "touch"
    elif f.range_kind in ("sight", "unlimited"):
        spec.range_ft = None
    elif f.range_kind == "special" and f.range_ft is None:
        # The range couldn't be read. Not a refusal — the DM adjudicates, the
        # same way an unreadable Metamagic condition becomes a line to confirm.
        spec.kind = "special"
        spec.note = f"range reads as {f.range_raw!r} — the DM places this one"
        return spec

    spec.count = _target_count(desc)
    # "a creature you can see within range" is the actual rule, and it is the
    # phrase the books use. Absence is not evidence here: a truncated OCR row
    # proves nothing, and requiring sight is both the common case and the safe
    # direction to err — a target you can see is always legal.
    spec.needs_sight = True
    return spec


# ---------------------------------------------------------------------------
# How a spell RESOLVES: an attack roll, a saving throw, or neither.
# ---------------------------------------------------------------------------
#
# `Spell.attack_type` is populated on 7 rows of 431 and `Spell.dc_type` on 20,
# because this project's spells came out of a PDF and the parser only ever
# filled those columns from the tidy SRD shape. The combat engine keyed its two
# damage branches on exactly those columns — so a spell with neither fell past
# BOTH and went off dealing nothing at all. Eldritch Blast is the plainest
# case: the row says nothing, the description says "Make a ranged spell attack
# against one creature", and casting it did no damage and rolled no attack.
#
# Same doctrine as `rules/damage.py` and `rules/components.py`: the column when
# it has an answer, the spell's own prose when it does not, and never a number
# cached beside the sentence it was read from.

# The extractor drops spaces INSIDE words as readily as it drops them between
# them — Inflict Wounds arrives as "Constit ution saving th row" — so every
# word this depends on is spelled out letter by letter, exactly as the shape
# vocabulary above is. The tolerance is bounded the same way: it is anchored on
# a closed vocabulary (six ability names, two attack ranges) with the loose
# phrase required immediately after, so it cannot drift onto ordinary prose.
_SAVING_THROW = _loose("saving") + r"\s*" + _loose("throw")
_SPELL_ATTACK = _loose("spell") + r"\s*" + _loose("attack")

_ATTACK_RE = re.compile(
    r"\bmake\s+(?:a|an|one)?\s*(" + _loose("ranged") + "|" + _loose("melee")
    + r")\b[^.]{0,40}?\b" + _loose("attack") + r"\b", re.I)
#: The phrasing both editions actually print: "a ranged spell attack".
_ATTACK_RE2 = re.compile(
    r"\b(" + _loose("ranged") + "|" + _loose("melee") + r")\s*"
    + _SPELL_ATTACK + r"\b", re.I)

_ABILITIES = ("strength", "dexterity", "constitution",
              "intelligence", "wisdom", "charisma")
_SAVE_RE = re.compile(
    r"\b(" + "|".join(_loose(a) for a in _ABILITIES) + r")\b\s*"
    + _SAVING_THROW, re.I)
#: The 2024 stat lines abbreviate: "DEX Save".
_SAVE_ABBR_RE = re.compile(
    r"\b(STR|DEX|CON|INT|WIS|CHA)\b\s*(?:" + _SAVING_THROW + r"|save)\b",
    re.I)

_ABBR = {"str": "str", "dex": "dex", "con": "con",
         "int": "int", "wis": "wis", "cha": "cha"}


def attack_kind(spell: Any) -> Optional[str]:
    """``"ranged"`` / ``"melee"`` if this spell is resolved with an attack roll.

    The column first; the description after it. Only the FIRST such phrase is
    read — a spell that mentions an attack roll later on (a rider on somebody
    else's attack) is not itself an attack spell, and `_head` already keeps us
    inside this entry.
    """
    col = str(getattr(spell, "attack_type", "") or "").strip().lower()
    if col:
        return "melee" if "melee" in col else "ranged"
    desc = _clean(_head(str(getattr(spell, "desc", "") or "")))
    m = _ATTACK_RE2.search(desc) or _ATTACK_RE.search(desc)
    return m.group(1).lower() if m else None


def save_ability(spell: Any) -> Optional[str]:
    """The three-letter ability a target saves with, or None.

    Returns the *first* ability named with a saving throw, which is the one the
    spell is resolved by; a later one is a repeat save or a rider.
    """
    col = str(getattr(spell, "dc_type", "") or "").strip().lower()[:3]
    if col in _ABBR:
        return _ABBR[col]
    desc = _clean(_head(str(getattr(spell, "desc", "") or "")))
    m = _SAVE_RE.search(desc)
    if m:
        return re.sub(r"\s+", "", m.group(1)).lower()[:3]
    m = _SAVE_ABBR_RE.search(desc)
    return m.group(1).lower() if m else None


def resolution_for(spell: Any) -> tuple[Optional[str], Optional[str]]:
    """``(attack_kind, save_ability)`` — how this spell is resolved.

    Both may be set (a spell that attacks and then forces a save) and both may
    be None (a buff, a utility, a summon). The ENGINE decides what to do with
    that; this only reads.
    """
    return attack_kind(spell), save_ability(spell)
