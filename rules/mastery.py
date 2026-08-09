"""Weapon Mastery: what a weapon does to you BESIDES damage.

The 2024 rules hang a second property off every weapon — Cleave, Graze, Nick,
Push, Sap, Slow, Topple, Vex — and gate it behind a class feature that says how
many of them a character may use at a time. It is the one 2024 combat system
this project had no answer for at all, which showed up first in two-weapon
fighting: **Nick** moves the Light property's extra attack out of the Bonus
Action and into the Attack action, so a build that gets a third swing a turn
was reading as a build that gets two.

Same split as ``airships/`` and ``rules/summons.py``: the ENGINE is here and
committed, the ASSIGNMENTS are book data and live in the gitignored
``owned_books/weapon_masteries_overrides.json`` slot. Nothing in this file says
which mastery a longsword has, because that sentence is the book's. With no
file present, mastery is simply OFF — which is the correct state for an
SRD-only checkout, since the open SRD carries no masteries.

The effects are declarative so the engine stays readable and the numbers stay
local. Each mastery says what it DOES in terms the combat engine already
speaks: a rider on a hit, a saving throw, a condition, an extra attack, or a
change to the action economy.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

WORKSPACE = Path(__file__).resolve().parent.parent / "owned_books"
_OVERRIDE_FILE = WORKSPACE / "weapon_masteries_overrides.json"

#: The eight masteries, by what the ENGINE has to do about each. Only the
#: mechanism is here — the rules text is the book's, and the assignment of a
#: mastery to a weapon is the book's too.
#:
#:   on_miss     — something happens even when the attack misses
#:   rider       — a rider applied on a hit, needing no roll
#:   save        — the target makes a saving throw or suffers a condition
#:   economy     — it changes the action economy rather than the outcome
#:   extra       — it grants an attack against a DIFFERENT creature
MECHANISMS: Dict[str, Dict[str, Any]] = {
    "cleave":  {"kind": "extra",   "needs": "hit"},
    "graze":   {"kind": "on_miss", "needs": "miss"},
    "nick":    {"kind": "economy", "needs": None},
    "push":    {"kind": "rider",   "needs": "hit"},
    "sap":     {"kind": "rider",   "needs": "hit"},
    "slow":    {"kind": "rider",   "needs": "hit"},
    "topple":  {"kind": "save",    "needs": "hit"},
    "vex":     {"kind": "rider",   "needs": "hit"},
}

#: Conditions this project already models, so a mastery that applies one can be
#: enforced rather than narrated. Anything absent is reported to the DM instead.
_CONDITION_FOR = {"topple": "prone"}


@dataclass
class Mastery:
    """One weapon's mastery, resolved for one attack."""
    name: str                       #: lowercase slug ("nick", "topple")
    label: str                      #: how it is shown ("Nick")
    kind: str                       #: mechanism class from MECHANISMS
    #: For a save mastery: which ability, and how the DC is built.
    save_ability: Optional[str] = None
    #: For Push: how far, in feet.
    distance_ft: int = 0
    #: For Sap/Slow/Vex: what it does in one phrase, for the log.
    note: str = ""


@dataclass
class MasteryRules:
    """The whole local table: weapon -> mastery, and who may use them."""
    #: normalized weapon name -> mastery slug
    weapons: Dict[str, str] = field(default_factory=dict)
    #: class slug -> how many masteries that class may have active
    class_counts: Dict[str, Dict[str, int]] = field(default_factory=dict)
    #: per-mastery tuning the book supplies (Push's 10 ft, Topple's save)
    tuning: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(self.weapons)


_CACHE: Optional[MasteryRules] = None


def _norm(s: Any) -> str:
    return " ".join(str(s or "").strip().lower().split())


def load_rules(*, refresh: bool = False) -> MasteryRules:
    """Read the local mastery table. Absent file = the system is off.

    Cached once like the option catalogue: restart after editing the file.
    """
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    out = MasteryRules()
    try:
        raw = json.loads(_OVERRIDE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _CACHE = out
        return out
    except Exception as e:                                   # malformed file
        print(f"[mastery] {_OVERRIDE_FILE.name}: {e}")
        _CACHE = out
        return out
    for name, mast in (raw.get("weapons") or {}).items():
        slug = _norm(mast)
        if slug in MECHANISMS:
            out.weapons[_norm(name)] = slug
    for cls, spec in (raw.get("classes") or {}).items():
        if isinstance(spec, dict):
            out.class_counts[_norm(cls)] = {str(k): int(v)
                                            for k, v in spec.items()}
    out.tuning = {k: v for k, v in (raw.get("tuning") or {}).items()
                  if isinstance(v, dict)}
    _CACHE = out
    return out


def mastery_for(weapon_name: str, base_name: Optional[str] = None) -> Optional[str]:
    """The mastery slug a weapon carries, or None.

    Looked up by the BASE name as well as the display one, because an affixed
    or player-named piece keeps its base and every mechanical lookup in this
    project has to go through it.
    """
    rules = load_rules()
    if not rules.enabled:
        return None
    for key in (_norm(base_name), _norm(weapon_name)):
        if key and key in rules.weapons:
            return rules.weapons[key]
    return None


def masteries_known(char_class: Optional[str], level: int,
                    extra: Optional[Set[str]] = None) -> int:
    """How many masteries this character may have active at once.

    The count is a class feature and therefore book data; a class the local
    table says nothing about gets 0, so mastery stays off for it rather than
    being guessed at. ``extra`` is for anything that grants more (a feat).
    """
    rules = load_rules()
    spec = rules.class_counts.get(_norm(char_class)) or {}
    best = 0
    for at_level, n in spec.items():
        try:
            if level >= int(at_level):
                best = max(best, int(n))
        except (TypeError, ValueError):
            continue
    return best + len(extra or ())


def resolve(weapon_name: str, *, base_name: Optional[str] = None,
            active: Optional[Set[str]] = None) -> Optional[Mastery]:
    """The mastery in play for this attack, or None.

    ``active`` is the set of mastery slugs this character has chosen. Holding a
    weapon whose mastery you have not chosen gives you nothing — that gate is
    the whole reason the feature is not simply "every weapon does this".
    """
    slug = mastery_for(weapon_name, base_name)
    if slug is None:
        return None
    if active is not None and slug not in active:
        return None
    mech = MECHANISMS.get(slug) or {}
    tune = (load_rules().tuning.get(slug) or {})
    return Mastery(
        name=slug,
        label=str(tune.get("label") or slug.title()),
        kind=str(mech.get("kind") or "rider"),
        save_ability=(tune.get("save") or ("str" if slug == "topple" else None)),
        distance_ft=int(tune.get("distance_ft") or (10 if slug == "push" else 0)),
        note=str(tune.get("note") or ""),
    )


def condition_for(slug: str) -> Optional[str]:
    """The condition a mastery applies, when this project models one."""
    return _CONDITION_FOR.get(_norm(slug))


def nick_active(weapons: List[Any], active: Optional[Set[str]] = None) -> bool:
    """True when one of the held weapons has Nick and it is chosen.

    Nick is the mastery that changes the action ECONOMY rather than an
    outcome: the extra attack the Light property grants is made as part of the
    Attack action instead of costing the Bonus Action. That is the difference
    between two swings a turn and three, so it cannot be left to narration.
    """
    for w in weapons or []:
        if getattr(w, "grip", None) not in ("main", "off"):
            continue
        m = resolve(getattr(w, "name", ""),
                    base_name=getattr(w, "base_name", None), active=active)
        if m is not None and m.name == "nick":
            return True
    return False
