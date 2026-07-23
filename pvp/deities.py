"""Which power avenges a slain worshipper.

The victim's own patron deity strikes first; if they named none, an alignment-fitting
power that abhors murder (a god of justice, protection, oaths, or the final gate)
takes up the cause. Alignment/title come from the closed pantheon roster
(``eight_card_system/pantheon.py``) so the smiting god is always canon.
"""
from __future__ import annotations

from typing import Dict, Optional

from eight_card_system import pantheon

# Domains that make a worshipable power a fitting AVENGER of murder.
_AVENGER_DOMAINS = ("justice", "law", "protection", "oaths", "honor", "war",
                    "death", "the final gate", "fate", "courage")
# Reasonable last-resort avengers by name (a just judge; a stern warden of the dead).
_FALLBACK_NAMES = ("Auren", "Nyssa", "Kael")


def _align_distance(a: str, b: str) -> int:
    """Crude alignment distance over the two axes (0 = identical, up to 4)."""
    def axes(s: str):
        s = (s or "").lower()
        law = 1 if "lawful" in s else (-1 if "chaotic" in s else 0)
        good = 1 if "good" in s else (-1 if "evil" in s else 0)
        return law, good
    la, ga = axes(a)
    lb, gb = axes(b)
    return abs(la - lb) + abs(ga - gb)


def retributor_for(deity_name: Optional[str],
                   victim_alignment: Optional[str] = None) -> Dict:
    """The power that avenges the victim. Returns a roster dict (name/title/alignment/
    domains/family). Falls back to an alignment-fitting avenger, then a just judge."""
    if deity_name:
        p = pantheon.power_by_name(deity_name)
        if p:
            return p

    va = victim_alignment or "neutral"
    candidates = [p for p in pantheon.worshipable_powers()
                  if "evil" not in (p.get("alignment") or "").lower()
                  and any(d in (p.get("domains") or "").lower() for d in _AVENGER_DOMAINS)]
    if candidates:
        candidates.sort(key=lambda p: _align_distance(va, p.get("alignment", "")))
        return candidates[0]

    for nm in _FALLBACK_NAMES:
        p = pantheon.power_by_name(nm)
        if p:
            return p
    # Absolute last resort — an unnamed higher power.
    return {"name": "the Unnamed", "title": "", "alignment": "lawful neutral",
            "domains": "justice", "family": "sovereign"}


def wrath_modifier(retributor: Dict) -> int:
    """How wrathful this power is toward murder (added to severity).

    A lawful and/or good power avenges hardest; an evil power cares less about a
    mortal's death than about the tribute owed to it."""
    al = (retributor.get("alignment") or "").lower()
    mod = 0
    if "lawful" in al:
        mod += 8
    if "good" in al:
        mod += 8
    if "evil" in al:
        mod -= 10  # an evil patron is less offended by a kill (it may even approve)
    dom = (retributor.get("domains") or "").lower()
    if any(d in dom for d in ("justice", "law", "oaths", "the final gate", "death")):
        mod += 6
    return mod


def full_name(power: Dict) -> str:
    t = (power.get("title") or "").strip()
    return f"{power.get('name', 'a god')} {t}".strip()
