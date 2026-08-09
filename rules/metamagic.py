"""Metamagic as a rule the CODE checks, not a note the DM has to remember.

A Metamagic option is three things — a price, a condition, and a change — and
until this module none of them existed anywhere. The option was a word on the
sheet, and whether Quickened Spell could legally be applied to a 1-minute
ritual was whatever the model felt like that turn.

**The vocabulary is declarative and the DATA is not in this file.** Which
option costs what, and which conditions it carries, is book text; it lives in
the gitignored ``owned_books/option_catalog.json`` as ``requires``/``effect``
lists. This module only knows how to READ those lists, which is why it can be
committed. Add an option by describing it, not by editing code.

**What can actually be checked.** ``casting_time``, ``range`` and ``duration``
are populated on every spell in the table, so conditions on those are decided
here and are not negotiable. ``dc_type``/``attack_type``/``damage`` are
populated on about 3% of rows — the bulk parser never filled them — so "forces
a saving throw" and "deals damage" are read out of the description instead,
where ABSENCE counts as evidence provided the description is long enough to be
whole: a complete spell text that never says "saving throw" describes a spell
without one. A truncated row proves nothing and answers **unknown**.

That tri-state is the whole design. A requirement that cannot be evaluated
becomes a line the DM is asked to confirm; it never becomes a silent refusal
(which would make the feat unusable) and never a silent pass (which would make
the rule decorative). ``dm:`` requirements — "targets only one creature", which
no column in the schema records — are declared unverifiable from the start.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Damage types a spell can deal, for `damage_type_in:` conditions.
DAMAGE_TYPES = (
    "acid", "bludgeoning", "cold", "fire", "force", "lightning", "necrotic",
    "piercing", "poison", "psychic", "radiant", "slashing", "thunder",
)

_SAVE_RE = re.compile(r"\bsaving throw\b|\bmust succeed on a\b", re.I)
_ATTACK_RE = re.compile(r"\b(?:ranged|melee)\s+spell attack\b|\bspell attack roll\b", re.I)
_DAMAGE_RE = re.compile(r"\b\d+d\d+\b[^.]{0,40}?\bdamage\b|\btakes?\b[^.]{0,40}?\bdamage\b", re.I)

# A description at least this long is taken as COMPLETE: if it never mentions a
# saving throw, the spell has none. Shorter than this and the row was truncated
# by the parser, so silence proves nothing.
_DESC_TRUSTWORTHY = 120

_DURATION_UNITS = {
    "round": 6, "rounds": 6,
    "second": 1, "seconds": 1,
    "minute": 60, "minutes": 60,
    "hour": 3600, "hours": 3600,
    "day": 86400, "days": 86400,
}


# --------------------------------------------------------------------------
# Reading a spell
# --------------------------------------------------------------------------

@dataclass
class SpellFacts:
    """Everything the engine managed to work out about one spell.

    Tri-state on purpose: ``True``/``False`` are findings, ``None`` is "the
    table doesn't say". Only a finding may refuse a Metamagic option.
    """
    name: str = ""
    level: int = 0
    casting_time_raw: str = ""
    casting_time_kind: str = "other"      # action|bonus|reaction|minutes|hours|other
    range_raw: str = ""
    range_kind: str = "special"           # self|touch|ranged|sight|unlimited|special
    range_ft: Optional[int] = None
    duration_raw: str = ""
    duration_seconds: Optional[int] = None
    instantaneous: bool = False
    concentration: bool = False
    has_save: Optional[bool] = None
    has_attack: Optional[bool] = None
    damage_types: set = field(default_factory=set)
    any_damage: Optional[bool] = None     # dice to reroll, typed or not


def _short(raw: str, words: int = 4) -> str:
    """The head of a book cell. `casting_time` is often a whole sentence
    ("Reaction, which you take when you are..."), and quoting all of it back
    at the table buries the reason inside the rules text."""
    t = re.split(r"[,;.]", str(raw or "").strip(), maxsplit=1)[0].strip()
    parts = t.split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def _casting_time_kind(raw: str) -> str:
    t = (raw or "").strip().lower()
    if "bonus" in t:
        return "bonus"
    if "reaction" in t:
        return "reaction"
    if "action" in t:
        return "action"
    if "minute" in t:
        return "minutes"
    if "hour" in t:
        return "hours"
    return "other"


#: The Range cell is OCR'd like everything else, and it is damaged in exactly
#: the ways `rules.damage` already repairs inside dice: "10 feet" arrives as
#: `l O feet` and "150 feet" as `l SO feet` or `1 SO feet`. Read strictly, ten
#: spells in the corpus have NO readable range at all — which costs Distant
#: Spell its condition and leaves a targeting UI with nothing to enforce.
#: Applied only to a token already sitting in front of "feet"/"mile", so the
#: substitution can't reach ordinary prose.
_RANGE_DIGITS = str.maketrans({"l": "1", "i": "1", "o": "0", "s": "5"})
_RANGE_NUM = r"([\dlios][\dlios\s]*?)\s*"


def _range_number(raw: str) -> Optional[int]:
    """``"l SO"`` -> ``150``. None when what's left isn't a number at all."""
    digits = str(raw or "").replace(" ", "").translate(_RANGE_DIGITS)
    return int(digits) if digits.isdigit() else None


def _parse_range(raw: str) -> Tuple[str, Optional[int]]:
    t = (raw or "").strip().lower()
    if not t:
        return "special", None
    if t.startswith("self"):
        return "self", None
    if t.startswith("touch"):
        return "touch", None
    if "sight" in t:
        return "sight", None
    if "unlimited" in t:
        return "unlimited", None
    m = re.search(_RANGE_NUM + r"(?:feet|foot|ft)\b", t)
    if m:
        n = _range_number(m.group(1))
        if n is not None:
            return "ranged", n
    m = re.search(_RANGE_NUM + r"mile", t)
    if m:
        n = _range_number(m.group(1))
        if n is not None:
            return "ranged", n * 5280
    return "special", None


def _parse_duration(raw: str) -> Tuple[Optional[int], bool]:
    """(seconds, instantaneous). Seconds is None when it can't be read."""
    t = (raw or "").strip().lower()
    if not t:
        return None, False
    # The bulk parser mangles this one badly ("lnstaritaneo•;s"), so match
    # loosely: an 'i', then 'stan', is enough and nothing else looks like it.
    if re.search(r"[il]n?st\w*t\w*n\w*", t) and "minute" not in t and "hour" not in t:
        return None, True
    if "until dispelled" in t or "permanent" in t or "special" in t:
        return None, False
    m = re.search(r"(\d+)\s*([a-z]+)", t)
    if m and m.group(2) in _DURATION_UNITS:
        return int(m.group(1)) * _DURATION_UNITS[m.group(2)], False
    return None, False


def facts_for(spell: Any) -> SpellFacts:
    """Read one Spell row into the tri-state facts the conditions run against."""
    f = SpellFacts(
        name=getattr(spell, "name", "") or "",
        level=int(getattr(spell, "level", 0) or 0),
        casting_time_raw=getattr(spell, "casting_time", "") or "",
        range_raw=getattr(spell, "range", "") or "",
        duration_raw=getattr(spell, "duration", "") or "",
        concentration=bool(getattr(spell, "concentration", False)),
    )
    f.casting_time_kind = _casting_time_kind(f.casting_time_raw)
    f.range_kind, f.range_ft = _parse_range(f.range_raw)
    f.duration_seconds, f.instantaneous = _parse_duration(f.duration_raw)

    desc = str(getattr(spell, "desc", "") or "")
    # Structured columns first — they are RIGHT when present, just usually
    # absent. Then the description, where ABSENCE is evidence too: a full
    # spell description that never says "saving throw" is a spell without one,
    # and answering "unknown" there would push Magic Missile at Careful Spell
    # onto the DM every time. Only a SUBSTANTIAL description gets to say no —
    # a truncated or mangled row proves nothing either way, and a wrong refusal
    # makes the feat unusable, which is the worse failure.
    told_enough = len(desc) >= _DESC_TRUSTWORTHY
    if getattr(spell, "dc_type", None):
        f.has_save = True
    elif desc:
        f.has_save = True if _SAVE_RE.search(desc) else (False if told_enough else None)
    if getattr(spell, "attack_type", None):
        f.has_attack = True
    elif desc:
        f.has_attack = True if _ATTACK_RE.search(desc) else (False if told_enough else None)

    dmg = getattr(spell, "damage", None)
    if isinstance(dmg, dict):
        dt = str(dmg.get("damage_type") or dmg.get("type") or "").lower()
        if dt in DAMAGE_TYPES:
            f.damage_types.add(dt)
    if not f.damage_types and desc:
        low = desc.lower()
        for dt in DAMAGE_TYPES:
            if re.search(rf"\b{dt}\s+damage\b", low):
                f.damage_types.add(dt)
    # Untyped damage still counts as damage — Empowered Spell asks only that
    # there ARE dice to reroll — so this is a separate finding from the types.
    if f.damage_types or (desc and _DAMAGE_RE.search(desc)):
        f.any_damage = True
    elif told_enough:
        f.any_damage = False
    return f


def deals_damage(f: SpellFacts, desc: str = "") -> Optional[bool]:
    if f.damage_types:
        return True
    if desc and _DAMAGE_RE.search(desc):
        return True
    return None


# --------------------------------------------------------------------------
# Conditions
# --------------------------------------------------------------------------

@dataclass
class Verdict:
    ok: bool = True
    met: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    unverified: List[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """Why it was refused, phrased as the requirement that went unmet.

        The conditions read as statements ("it forces a saving throw"), so a
        bare join produced "Careful Spell can't touch Magic Missile — it forces
        a saving throw", which states the opposite of the reason. The caller
        supplies "needs".
        """
        if self.failed:
            return "it needs a spell where " + "; ".join(self.failed)
        return "; ".join(self.unverified) if self.unverified else "legal"


def _check_one(req: str, f: SpellFacts) -> Tuple[Optional[bool], str]:
    """(verdict, phrase). None = the table can't say, so the DM is asked."""
    req = (req or "").strip()
    key, _, arg = req.partition(":")
    key, arg = key.strip().lower(), arg.strip()

    if key == "dm":
        return None, arg or "the DM must confirm this"
    if key == "save":
        return f.has_save, "it forces a saving throw"
    if key == "attack":
        return f.has_attack, "it uses a spell attack roll"
    if key == "damage":
        return f.any_damage, "it deals damage"
    if key == "damage_type_in":
        wanted = {w.strip().lower() for w in arg.split(",") if w.strip()}
        if not f.damage_types:
            # No TYPES found is ambiguous on its own; no damage at all is not.
            if f.any_damage is False:
                return False, "it deals damage of a type that can be changed"
            return None, f"its damage is one of {', '.join(sorted(wanted))}"
        return (bool(f.damage_types & wanted),
                f"its damage ({', '.join(sorted(f.damage_types))}) is one of "
                f"{', '.join(sorted(wanted))}")
    if key == "casting_time":
        return (f.casting_time_kind == arg.lower(),
                f"its casting time is {arg} "
                f"(this one is {_short(f.casting_time_raw) or 'unstated'})")
    if key == "not_instantaneous":
        return (not f.instantaneous,
                f"its duration is not instantaneous (it is {f.duration_raw or 'unstated'})")
    if key == "duration_min_seconds":
        want = int(arg or 0)
        need = f"it lasts at least {_fmt_seconds(want)}"
        if f.duration_seconds is None:
            return (False if f.instantaneous else None,
                    f"{need} (this one is {_short(f.duration_raw, 5) or 'unstated'})")
        return (f.duration_seconds >= want,
                f"{need} (this one lasts {_short(f.duration_raw, 5)})")
    if key == "range_min_ft":
        want = int(arg or 0)
        if f.range_kind == "touch":
            return False, f"its range is at least {want} ft (it is Touch)"
        if f.range_kind in ("self",):
            return False, f"its range is at least {want} ft (it is Self)"
        if f.range_ft is None:
            return None, f"its range is at least {want} ft (it is {f.range_raw})"
        return f.range_ft >= want, f"its range is at least {want} ft (it is {f.range_raw})"
    if key == "range_touch_or_min_ft":
        want = int(arg or 0)
        if f.range_kind == "touch":
            return True, "its range is Touch"
        if f.range_kind == "self":
            return False, f"its range is at least {want} ft or Touch (it is Self)"
        if f.range_ft is None:
            return None, f"its range is at least {want} ft or Touch (it is {f.range_raw})"
        return (f.range_ft >= want,
                f"its range is at least {want} ft or Touch (it is {f.range_raw})")
    if key == "not_range_self":
        return f.range_kind != "self", f"its range is not Self (it is {f.range_raw})"
    if key == "concentration":
        return f.concentration, "it requires concentration"
    if key == "level_min":
        return f.level >= int(arg or 0), f"it is at least level {arg}"
    # An unknown condition is reported, never silently ignored: a typo in the
    # catalogue must be visible, not permissive.
    return None, f"unrecognised condition {req!r}"


def check(option: Dict[str, Any], f: SpellFacts) -> Verdict:
    """Can this option be applied to this spell?"""
    v = Verdict()
    for req in (option.get("requires") or []):
        ok, phrase = _check_one(str(req), f)
        if ok is True:
            v.met.append(phrase)
        elif ok is False:
            v.failed.append(phrase)
            v.ok = False
        else:
            v.unverified.append(phrase)
    return v


# --------------------------------------------------------------------------
# Effects
# --------------------------------------------------------------------------

def _fmt_seconds(sec: int) -> str:
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if sec >= size and sec % size == 0:
            n = sec // size
            return f"{n} {unit}{'s' if n != 1 else ''}"
    return f"{sec} seconds"


def apply(option: Dict[str, Any], f: SpellFacts) -> List[str]:
    """What the option CHANGES, as human-readable 'was -> now' lines."""
    eff = option.get("effect") or {}
    out: List[str] = []
    if eff.get("casting_time"):
        out.append(f"casting time {f.casting_time_raw or '?'} → {eff['casting_time']}")
    if eff.get("range_x2"):
        if f.range_kind == "touch" and eff.get("range_touch_becomes_ft"):
            out.append(f"range Touch → {eff['range_touch_becomes_ft']} feet")
        elif f.range_ft:
            out.append(f"range {f.range_ft} → {f.range_ft * 2} feet")
        else:
            out.append(f"range doubled (from {f.range_raw or '?'})")
    elif eff.get("range_touch_becomes_ft") and f.range_kind == "touch":
        out.append(f"range Touch → {eff['range_touch_becomes_ft']} feet")
    if eff.get("duration_x2"):
        cap = int(eff.get("duration_cap_hours") or 0) * 3600
        if f.duration_seconds:
            new = f.duration_seconds * 2
            capped = cap and new > cap
            new = min(new, cap) if cap else new
            out.append(f"duration {f.duration_raw} → {_fmt_seconds(new)}"
                       + (" (capped)" if capped else ""))
        else:
            out.append(f"duration doubled (from {f.duration_raw or '?'})")
    for note in (eff.get("notes") or ([eff["note"]] if eff.get("note") else [])):
        out.append(str(note))
    return out


def describe(option_name: str, option: Dict[str, Any], f: SpellFacts,
             verdict: Verdict) -> str:
    """One line for the table: what was checked, and what changed."""
    if not verdict.ok:
        return f"{option_name} can't touch {f.name} — {verdict.summary}."
    bits = apply(option, f)
    head = f"{option_name} on {f.name}"
    body = "; ".join(bits) if bits else "applied"
    if verdict.unverified:
        body += f" — DM: confirm {', '.join(verdict.unverified)}"
    return f"{head}: {body}"
