"""What a spell costs to cast besides the slot: V, S and M — and what the M is.

The database has carried ``Spell.components`` (``["V","S","M"]``) and
``Spell.material`` (the prose in the brackets) since the first ingest, and
exactly one thing ever read them: the card-game cheating check, which asks
whether a casting is *perceptible*. Nothing asked what it cost. Revivify's 300
GP diamond was free and infinite, and the DM was never even shown that it
existed, because the spell brief prints casting time, range and duration and
has never printed components.

Everything here is DERIVED from the prose rather than stored beside it. Two
reasons, both learned elsewhere in this project: a stored number and the
sentence it came from drift apart the moment a re-parse improves one of them,
and a column costs a migration on every database for a regex over a string
that is already in memory.

The parse is deliberately conservative. A cost it cannot read is 0, which
means "a focus covers it" — the same answer the game gave before this module
existed — so a bad parse never invents a price a player has to pay. What it
must not miss is the opposite direction, and the ingest was fixed alongside
this so that every spell with an M component actually has its material text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

#: Coin names -> value in gold. Book equipment lists write costs in GP almost
#: always, but a 1 SP melee weapon (Booming Blade) is a real material component.
_COIN_GP = {"pp": 10.0, "gp": 1.0, "ep": 0.5, "sp": 0.1, "cp": 0.01}

_COST_RE = re.compile(r"(\d[\d,]*)\s*\+?\s*(pp|gp|ep|sp|cp)\b", re.I)
#: "which the spell consumes", "(consumed)", "the spell consumes it"
_CONSUMED_RE = re.compile(r"\bconsum(?:e|es|ed|ing)\b", re.I)
#: Split a material list into the things it names. "a diamond worth 1,000+ GP,
#: which the spell consumes, AND a sealable vessel worth 2,000+ GP" is two
#: objects with two prices and only one of them destroyed.
_SPLIT_RE = re.compile(r",?\s+and\s+", re.I)


@dataclass
class SpellComponents:
    """The V/S/M of one spell, with the material priced and read."""
    verbal: bool = False
    somatic: bool = False
    material: bool = False
    #: The prose exactly as the book prints it, or "" when there is none.
    text: str = ""
    #: Total value the caster must HOLD, in gold. 0 means nothing priced.
    cost_gp: float = 0.0
    #: The part of that value the casting DESTROYS.
    consumed_gp: float = 0.0
    #: Whether any part of the component is destroyed by the casting.
    consumed: bool = False
    #: Priced pieces, as (description, gold, destroyed).
    items: List[Tuple[str, float, bool]] = field(default_factory=list)

    @property
    def costly(self) -> bool:
        return self.cost_gp > 0

    @property
    def focus_ok(self) -> bool:
        """A spellcasting focus or component pouch stands in for this.

        The line is the PRICE, not the prose: a focus replaces any material
        component with no cost, however specific the book's description of it,
        and replaces none that has one.
        """
        return self.material and not self.costly

    @property
    def perceptible(self) -> bool:
        """Someone watching would know a spell was being cast."""
        return self.verbal or self.somatic or self.material


def _to_gp(amount: str, coin: str) -> float:
    try:
        return float(amount.replace(",", "")) * _COIN_GP.get(coin.lower(), 1.0)
    except ValueError:
        return 0.0


def parse_material(text: Optional[str]) -> SpellComponents:
    """Price a material-component line. Costs sum; only the named part burns."""
    out = SpellComponents(text=(text or "").strip())
    if not out.text:
        return out
    for part in _SPLIT_RE.split(out.text):
        m = _COST_RE.search(part)
        if not m:
            continue
        gp = _to_gp(m.group(1), m.group(2))
        if gp <= 0:
            continue
        burns = bool(_CONSUMED_RE.search(part))
        out.items.append((part.strip(), gp, burns))
        out.cost_gp += gp
        if burns:
            out.consumed_gp += gp
    # A material can be destroyed without being priced ("a pinch of soot, which
    # the spell consumes"), which costs nothing but is still gone.
    out.consumed = bool(out.consumed_gp) or bool(_CONSUMED_RE.search(out.text))
    return out


def components_of(spell: Any) -> SpellComponents:
    """Read a ``rules_spell`` row (or anything with the same two fields).

    A spell the library doesn't have is verbal + somatic: the overwhelming
    majority are, so an unknown casting is assumed to be seen and heard rather
    than assumed to be silent, which is the safe direction for every caller —
    the cheating check, the DM's board, and the casting gate alike.
    """
    if spell is None:
        return SpellComponents(verbal=True, somatic=True)
    raw = getattr(spell, "components", None) or []
    if isinstance(raw, str):
        raw = [c for c in re.split(r"[,\s]+", raw) if c]
    codes = {str(c).strip().upper()[:1] for c in raw}
    out = parse_material(getattr(spell, "material", None))
    out.verbal = "V" in codes
    out.somatic = "S" in codes
    out.material = ("M" in codes) or bool(out.text)
    return out


def format_cost(gp: float) -> str:
    """Gold as a book would print it: 300 GP, 1 SP, 25,000 GP."""
    if gp >= 1:
        whole = int(gp) if float(gp).is_integer() else round(gp, 2)
        return f"{whole:,} GP"
    if gp >= 0.1:
        return f"{round(gp * 10)} SP"
    return f"{round(gp * 100)} CP"


def describe(c: SpellComponents) -> str:
    """"V, S, M (a diamond worth 300+ GP — 300 GP, consumed)" — one line."""
    codes = [k for k, on in (("V", c.verbal), ("S", c.somatic),
                             ("M", c.material)) if on]
    if not codes:
        return "none"
    line = ", ".join(codes)
    if not c.material or not c.text:
        return line
    tail = c.text
    if c.costly:
        note = format_cost(c.cost_gp)
        if c.consumed:
            note += ", consumed" if c.consumed_gp >= c.cost_gp else \
                f", {format_cost(c.consumed_gp)} of it consumed"
        tail += f" — {note}"
    elif c.consumed:
        tail += " — consumed"
    return f"{line} ({tail})"
