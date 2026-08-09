"""Damage has a TYPE, and a creature can resist it — the one place both live.

The combat engine dealt integers. `PCWeapon.damage` was `"1d8+3"`, a monster's
bite was whatever dice its first damage entry held, and a fireball was a number
— so a fire elemental took full damage from fire, a skeleton shrugged off a
mace exactly as hard as a rapier, and a raging barbarian was resistant to
nothing. Thirteen damage types existed everywhere in the data and nowhere in
the arithmetic.

Three jobs, in the order a blow travels:

* :func:`parse_damage` — pull typed dice out of prose. This exists because the
  spell rows in this project came from a PDF parse, not the SRD JSON: 430
  spells carry a description and 17 carry a structured `damage` dict, so the
  only place a Fireball's "8d6 Fire damage" is written down is the sentence.
  Derived at read time rather than stored beside the prose, exactly as
  :mod:`rules.components` derives a component's price — a stored number and
  the sentence it came from drift the moment a re-parse improves one of them.
* :func:`parse_defenses` — read what a creature resists. The bestiary holds
  this in two incompatible shapes and one of them mixes damage immunities and
  CONDITION immunities into a single string, which is the trap this module
  exists to close: read naively, a skeleton is "immune to exhaustion damage".
* :func:`apply` — halve, double or zero each packet, and say what it did.

Everything here is OCR-tolerant on purpose. The book text this reads was
extracted from PDFs and is damaged in consistent ways — `ldlO` is `1d10`,
`Necr otic` is `Necrotic` — and a parser that only accepts clean input reads
the ~3% of spells that happen to be clean and silently drops the rest.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

#: The thirteen. Ordered so a report reads the same way twice.
DAMAGE_TYPES: Tuple[str, ...] = (
    "acid", "bludgeoning", "cold", "fire", "force", "lightning", "necrotic",
    "piercing", "poison", "psychic", "radiant", "slashing", "thunder",
)
_TYPE_SET = frozenset(DAMAGE_TYPES)

#: The physical three. Half the bestiary's resistance lines name all three at
#: once ("bludgeoning, piercing, and slashing from nonmagical attacks").
PHYSICAL = ("bludgeoning", "piercing", "slashing")

#: Materials that beat a "nonmagical" qualifier on their own.
MATERIALS = ("silvered", "adamantine", "cold-forged iron", "cold iron")

# ---------------------------------------------------------------------------
# Reading damaged book text
# ---------------------------------------------------------------------------

#: OCR confusions that matter INSIDE a dice expression. `l`/`I` for `1` and `O`
#: for `0` are the two that turn "1d10" into "ldlO"; anywhere else they are
#: real letters, so this is only ever applied to a matched dice token.
_DIGIT_FIX = {"l": "1", "I": "1", "i": "1", "O": "0", "o": "0", "S": "5"}

#: "8d6", "ldlO", "2 d 8", "1d10+3" — the token, before it is repaired.
_DICE_RE = re.compile(r"\b([0-9lIiOoS]{1,3})\s*[dD]\s*([0-9lIiOoS]{1,3})"
                      r"(?:\s*([+-])\s*([0-9lIiOoS]{1,3}))?")


def _fix_number(tok: str) -> Optional[int]:
    """Repair a number an OCR pass mangled, or None if it isn't one."""
    out = "".join(_DIGIT_FIX.get(ch, ch) for ch in tok)
    return int(out) if out.isdigit() else None


def normalize_type(raw: Any) -> Optional[str]:
    """A damage type from anything a book or a JSON blob calls it.

    Spaces INSIDE the word are stripped before matching, because the PDF text
    is full of them ("Necr otic", "Bludge oning") and a word split across a
    line break is still that word.
    """
    s = re.sub(r"[^a-z]", "", str(raw or "").lower())
    if not s:
        return None
    if s in _TYPE_SET:
        return s
    for t in DAMAGE_TYPES:
        if s.startswith(t) or t.startswith(s) and len(s) >= 4:
            return t
    return None


@dataclass
class Packet:
    """One typed lump of damage: the dice, what kind, and where it came from."""
    dice: str
    type: Optional[str] = None
    #: A magical source ignores a "nonmagical" qualifier on a resistance.
    magical: bool = False
    #: Silvered / adamantine, which beat their own qualifiers.
    materials: Set[str] = field(default_factory=set)
    #: For a report: "Fireball", "Longsword", "Sneak Attack".
    label: str = ""

    def __post_init__(self) -> None:
        self.type = normalize_type(self.type)


_TYPE_WORDS = "|".join(
    # Allow a space anywhere inside the word: "Necr otic", "light ning".
    "".join(f"{c}\\s*" for c in t[:-1]) + t[-1] for t in DAMAGE_TYPES)
#: "8d6 Fire damage", "1d10 Necrotic damage", "2d8 force damage"
_TYPED_DMG_RE = re.compile(
    rf"([0-9lIiOoS]{{1,3}}\s*[dD]\s*[0-9lIiOoS]{{1,3}}"
    rf"(?:\s*[+-]\s*[0-9lIiOoS]{{1,3}})?)\s*(?:points?\s+of\s+)?"
    rf"({_TYPE_WORDS})\s*(?:damage)?", re.I)


def clean_dice(tok: str) -> Optional[str]:
    """"ldlO" -> "1d10", "2 d 8 + 3" -> "2d8+3". None if it isn't dice."""
    m = _DICE_RE.search(tok or "")
    if not m:
        return None
    n, faces = _fix_number(m.group(1)), _fix_number(m.group(2))
    if not n or not faces:
        return None
    out = f"{n}d{faces}"
    if m.group(3) and m.group(4) is not None:
        bonus = _fix_number(m.group(4))
        if bonus:
            out += f"{m.group(3)}{bonus}"
    return out


def parse_damage(text: Optional[str], *, magical: bool = False,
                 label: str = "") -> List[Packet]:
    """Every "<dice> <type> damage" in a piece of prose, in order.

    Deliberately conservative: a sentence it cannot read yields nothing, which
    leaves the caller exactly where it was before this module existed. What it
    must not do is read the dice and miss the TYPE — a typeless packet is
    resisted by nobody, so an unreadable type is safer as no packet at all than
    as untyped damage that quietly ignores every resistance in the game.
    """
    out: List[Packet] = []
    for m in _TYPED_DMG_RE.finditer(text or ""):
        dice = clean_dice(m.group(1))
        dtype = normalize_type(m.group(2))
        if dice and dtype:
            out.append(Packet(dice=dice, type=dtype, magical=magical,
                              label=label))
    return out


#: "half as much damage on a successful one", "half damage on a success"
_HALF_RE = re.compile(r"half\s+(?:as\s+much\s+)?damage|half\s+as\s+much",
                      re.I)


def save_halves(text: Optional[str]) -> bool:
    """Does a successful save halve this spell's damage rather than avoid it?"""
    return bool(_HALF_RE.search(text or ""))


# ---------------------------------------------------------------------------
# What a creature resists
# ---------------------------------------------------------------------------

@dataclass
class Qualifier:
    """When a defence applies. Unqualified is the common case."""
    #: Only against a non-magical source ("from nonmagical attacks").
    nonmagical_only: bool = False
    #: Materials that defeat it anyway ("that aren't silvered").
    beaten_by: Set[str] = field(default_factory=set)

    def applies(self, packet: Packet) -> bool:
        if self.nonmagical_only and packet.magical:
            return False
        if self.beaten_by & {m.lower() for m in packet.materials}:
            return False
        return True


@dataclass
class Defenses:
    """One creature's damage defences, plus the condition immunities that were
    tangled up with them in the same database column."""
    resist: Dict[str, Qualifier] = field(default_factory=dict)
    immune: Dict[str, Qualifier] = field(default_factory=dict)
    vulnerable: Dict[str, Qualifier] = field(default_factory=dict)
    #: Split out of the same strings — see :func:`parse_defenses`.
    condition_immunities: Set[str] = field(default_factory=set)

    @property
    def empty(self) -> bool:
        return not (self.resist or self.immune or self.vulnerable)

    def describe(self) -> str:
        bits = []
        for label, table in (("immune", self.immune), ("resists", self.resist),
                             ("vulnerable", self.vulnerable)):
            if not table:
                continue
            names = []
            for t in DAMAGE_TYPES:
                q = table.get(t)
                if q is None:
                    continue
                names.append(t + (" (nonmagical)" if q.nonmagical_only else ""))
            if names:
                bits.append(f"{label} {', '.join(names)}")
        return "; ".join(bits)


#: Condition names that turn up in the mixed immunity strings. Used only to
#: recognise them; the canonical list lives with the conditions themselves.
_CONDITIONS = {
    "blinded", "charmed", "deafened", "exhaustion", "frightened", "grappled",
    "incapacitated", "invisible", "paralyzed", "petrified", "poisoned",
    "prone", "restrained", "stunned", "unconscious",
}


def _as_condition(text: str) -> Optional[str]:
    """A condition name, read tolerantly, or None.

    Tolerantly because the source is OCR: the bestiary contains "Petrifed" for
    Petrified, and a lookup that only accepts the correct spelling silently
    drops a real immunity. Same first five letters and a length within two is
    enough to separate a misspelt condition from a different word, and every
    candidate has already failed to be a damage type.
    """
    s = re.sub(r"[^a-z]", "", str(text or "").lower())
    if not s:
        return None
    if s in _CONDITIONS:
        return s
    for c in _CONDITIONS:
        if s[:5] == c[:5] and abs(len(s) - len(c)) <= 2:
            return c
    return None


def _qualifier_from(text: str) -> Qualifier:
    q = Qualifier()
    low = text.lower()
    if "nonmagical" in low or "non-magical" in low or "not magical" in low:
        q.nonmagical_only = True
    for mat in MATERIALS:
        if mat in low:
            q.beaten_by.add(mat)
            # "from nonmagical attacks that aren't silvered" — the silvering is
            # the exception to the qualifier, so the qualifier is a real one.
            q.nonmagical_only = True
    return q


def _entries(raw: Any) -> List[str]:
    """Flatten whatever shape the column is in into comparable strings."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, dict):
        return [str(v) for v in raw.values()]
    out: List[str] = []
    for item in raw:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            out.append(str(item.get("name") or item.get("type") or ""))
    return out


def parse_defenses(resistances: Any = None, immunities: Any = None,
                   vulnerabilities: Any = None,
                   condition_immunities: Any = None) -> Defenses:
    """Read a bestiary row's defence columns, in either shape they come in.

    The bestiary holds this two ways, because half of it was ingested from the
    open SRD JSON and half was parsed out of a PDF:

    * a tidy list — ``["acid", "cold", "bludgeoning, piercing, and slashing
      from nonmagical weapons"]``;
    * one string with a SEMICOLON in it — ``["Fire,Poison;Exhaustion,Grappled"]``
      — where everything after the semicolon is a CONDITION immunity that the
      parser dropped into the damage column.

    That second shape is the trap. Read without splitting it, a skeleton comes
    out immune to "exhaustion damage" and a golem to "charmed damage" — both
    harmless-looking and both meaning the real immunities beside them are
    parsed from a string nobody validated. Anything recognised as a condition
    is moved to :attr:`Defenses.condition_immunities` instead.
    """
    out = Defenses()

    def load(raw: Any, table: Dict[str, Qualifier]) -> None:
        for entry in _entries(raw):
            damage_part = entry
            if ";" in entry:
                damage_part, _, cond_part = entry.partition(";")
                for c in re.split(r"[,/]", cond_part):
                    cond = _as_condition(c)
                    if cond:
                        out.condition_immunities.add(cond)
            qual = _qualifier_from(damage_part)
            # Strip the qualifying clause before naming types, or "attacks"
            # and "weapons" get matched as though they were damage types.
            head = re.split(r"\bfrom\b|\bthat\b|\bnot\b", damage_part,
                            maxsplit=1)[0]
            for part in re.split(r",|/|\band\b", head):
                t = normalize_type(part)
                if t:
                    table[t] = qual
                    continue
                # Not a damage type — but the same column is where a PDF parse
                # put bare CONDITION immunities with no separator at all, so
                # rescue those rather than dropping them on the floor.
                cond = _as_condition(part)
                if cond:
                    out.condition_immunities.add(cond)

    load(resistances, out.resist)
    load(immunities, out.immune)
    load(vulnerabilities, out.vulnerable)
    for entry in _entries(condition_immunities):
        for c in re.split(r"[,;/]", entry):
            c = re.sub(r"[^a-z ]", "", c.strip().lower()).strip()
            if c in _CONDITIONS:
                out.condition_immunities.add(c)
    return out


def defenses_of(monster: Any) -> Defenses:
    """Read a ``rules_monster`` row (or anything with the same four fields)."""
    if monster is None:
        return Defenses()
    return parse_defenses(
        getattr(monster, "damage_resistances", None),
        getattr(monster, "damage_immunities", None),
        getattr(monster, "damage_vulnerabilities", None),
        getattr(monster, "condition_immunities", None))


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------

@dataclass
class Applied:
    """What the defences did to one blow."""
    total: int
    notes: List[str] = field(default_factory=list)
    #: The per-type breakdown, for a log: [(type, before, after)].
    parts: List[Tuple[Optional[str], int, int]] = field(default_factory=list)


def reduce_one(defenses: Optional[Defenses], amount: int,
               packet: Optional[Packet] = None) -> Tuple[int, str]:
    """Apply immunity / resistance / vulnerability to one typed lump.

    The rules, in the order they resolve: immunity zeroes it; resistance halves
    it ROUNDING DOWN; vulnerability doubles it; and multiple instances of the
    same one count once, which falls out of storing them in a dict rather than
    a list. A creature both resistant and vulnerable to the same type takes
    normal damage — the two are applied in turn and cancel.

    An UNTYPED packet is never reduced. That is the safe direction and it is
    also the honest one: damage the engine could not type is damage nobody can
    prove they resist.
    """
    amount = max(0, int(amount))
    if defenses is None or packet is None or not packet.type or amount == 0:
        return amount, ""
    t = packet.type
    q = defenses.immune.get(t)
    if q is not None and q.applies(packet):
        return 0, f"immune to {t}"
    res = defenses.resist.get(t)
    vul = defenses.vulnerable.get(t)
    resisting = res is not None and res.applies(packet)
    vulnerable = vul is not None and vul.applies(packet)
    if resisting and vulnerable:
        return amount, f"resistant AND vulnerable to {t} — they cancel"
    if resisting:
        return amount // 2, f"resists {t} ({amount} halved)"
    if vulnerable:
        return amount * 2, f"vulnerable to {t} ({amount} doubled)"
    return amount, ""


def apply(defenses: Optional[Defenses],
          rolled: Sequence[Tuple[Packet, int]]) -> Applied:
    """Reduce a whole blow — several typed lumps at once.

    A flame tongue is 1d8 slashing AND 2d6 fire, and they meet a creature's
    defences SEPARATELY: a fire elemental takes the slashing and none of the
    fire. Summing first and reducing once is the mistake this signature exists
    to make impossible.
    """
    out = Applied(total=0)
    for packet, amount in rolled:
        after, note = reduce_one(defenses, amount, packet)
        out.total += after
        out.parts.append((packet.type, amount, after))
        if note:
            label = f"{packet.label}: " if packet.label else ""
            out.notes.append(f"{label}{note}")
    return out
