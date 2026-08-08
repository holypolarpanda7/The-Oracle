"""Conjured creatures: one stat block, computed from the spell that made it.

A summoning spell does not name a monster. It describes one whose numbers come
from the CASTER and the SLOT — "AC 11 + the level of the spell", "Hit Points 40
+ 10 for each spell level above 4th", "1d10 + 3 + the spell's level slashing",
"your spell attack modifier to hit", "against your spell save DC". ``Monster``
holds fixed integers and can express none of that, so the DM's only move was
``[[COMBAT: add | Fey Spirit]]``, which found nothing in the bestiary and
seated a 10-HP blob with no AC, no speed, no senses and no attacks. The spell
resolved; the creature it conjured did not exist.

The fix is to MATERIALIZE. Given (spirit, variant, slot level, caster), every
number is computed once and upserted as a concrete ``rules_monster`` row.
Everything downstream already resolves a creature by slug —
``tracker.add_from_monster``, ``bridge.roster_for``, ``MapToken.senses``, the
combat engine's attack routine, the board's size and speed — so a conjured
spirit becomes a first-class creature without a line changing in any of them.
The row is derived and deterministic: the same spell at the same level for the
same caster is the same slug, so identical castings share one row and a stale
one is simply rebuilt identically.

**Every scaling line in every summon block reduces to one shape**, which is why
this is a data slot and not nine special cases: ``base + per_level x (level -
from)``, floored. AC is ``{base: 11, per_level: 1}``; Hit Points are ``{base:
40, per_level: 10, from: 4}``; a damage bonus is ``{base: 3, per_level: 1}``;
"attacks equal to half this spell's level" is ``{per_level: 0.5}``. See
:func:`scaled`.

Same split as ``airships/``: the ENGINE is here and committed, the NUMBERS are
book data and live in the gitignored ``owned_books/summons_overrides.json``
slot (see ``rules/OWNED_IMPORT_FORMAT.md``). One self-authored generic spirit
ships in this file so a bookless checkout can still summon something.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlmodel import Session, SQLModel, select

from .models import Monster

WORKSPACE = Path(__file__).resolve().parent.parent / "owned_books"

#: What a materialized row's ``source`` says. Derived, re-buildable, and not to
#: be confused with an ingested stat block.
DERIVED_SRC = "Conjured (derived from a summoning spell)"

_NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
                 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}


# ----------------------------------------------------------------------
# The one scaling shape
# ----------------------------------------------------------------------
def scaled(term: Any, level: int, *, default: int = 0) -> int:
    """``{base, per_level, from}`` -> ``base + per_level x (level - from)``.

    Floored, and never below ``base`` — a spell cast at its own minimum level
    gets the printed number. A bare int is a constant, which is most of a stat
    block. ``from`` defaults to 0, which is what "11 + the level of the spell"
    means; Hit Points say "40 + 10 for each level above 4th" and set it to 4.
    """
    if term is None:
        return default
    if isinstance(term, (int, float)):
        return int(term)
    if not isinstance(term, dict):
        return default
    base = float(term.get("base", 0) or 0)
    per = float(term.get("per_level", 0) or 0)
    frm = float(term.get("from", 0) or 0)
    return int(math.floor(base + per * max(0.0, float(level) - frm)))


# ----------------------------------------------------------------------
# Catalogue
# ----------------------------------------------------------------------
#: A self-authored generic, so the engine is exercisable and a checkout with no
#: owned-book data can still conjure something rather than raising. Deliberately
#: bland: one shape, one attack, no variants. The book spirits arrive through
#: the override slot.
_GENERIC: List[Dict[str, Any]] = [
    {
        "slug": "conjured-spirit",
        "name": "Conjured Spirit",
        "noun": "spirit",
        "spells": ["conjure-spirit"],
        "min_level": 1,
        "size": "Medium",
        "type": "elemental",
        "alignment": "unaligned",
        "armor_class": {"base": 11, "per_level": 1},
        "ac_desc": "natural armor",
        "hit_points": {"base": 15, "per_level": 10, "from": 1},
        "speed": {"walk": 30},
        "abilities": {"str": 14, "dex": 14, "con": 14,
                      "int": 10, "wis": 10, "cha": 10},
        "senses": {"darkvision": 60, "passive_perception": 10},
        "languages": "understands the languages you speak",
        "multiattack": {"per_level": 0.5},
        "actions": [
            {"name": "Spirit Strike", "kind": "melee spell", "reach_ft": 5,
             "damage_dice": "1d8", "damage_bonus": {"base": 2, "per_level": 1},
             "damage_type": "force"},
        ],
    },
]

_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def catalog(*, workspace: Path = WORKSPACE, reload: bool = False
            ) -> Dict[str, Dict[str, Any]]:
    """Spirit slug -> entry. Owned-book entries override the generic by slug.

    Cached; pass ``reload=True`` (or restart) after editing the slot, exactly
    like the feat option catalogue.
    """
    global _CACHE
    if _CACHE is not None and not reload:
        return _CACHE
    out: Dict[str, Dict[str, Any]] = {e["slug"]: e for e in _GENERIC}
    path = workspace / "summons_overrides.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for entry in (data if isinstance(data, list) else []):
                slug = str(entry.get("slug") or "").strip().lower()
                if slug:
                    out[slug] = entry
        except Exception as e:                       # a bad file is not fatal
            print(f"[summons] {path.name}: {e}")
    _CACHE = out
    return out


def _norm(s: Any) -> str:
    return str(s or "").strip().lower().replace("'", "").replace("’", "")


def spirit_for(ref: str, *, workspace: Path = WORKSPACE
               ) -> Optional[Dict[str, Any]]:
    """The spirit a spell conjures — by spell slug, spell name, or spirit name.

    The DM writes the SPELL ("Summon Fey"), the catalogue is keyed by the
    CREATURE ("fey-spirit"), and a stat block is also worth finding by its own
    name. All three arrive here.
    """
    ref_n = _norm(ref)
    if not ref_n:
        return None
    slug_n = ref_n.replace(" ", "-")
    cat = catalog(workspace=workspace)
    for entry in cat.values():
        spells = {_norm(x).replace(" ", "-") for x in (entry.get("spells") or [])}
        spells |= {_norm(x) for x in (entry.get("spells") or [])}
        if slug_n in spells or ref_n in spells:
            return entry
    return cat.get(slug_n) or next(
        (e for e in cat.values() if _norm(e.get("name")) == ref_n), None)


def variants_for(entry: Dict[str, Any]) -> List[str]:
    """The choices a caster makes at casting time ("Fuming, Mirthful, Tricksy")."""
    return [str(v.get("label") or k).strip()
            for k, v in (entry.get("variants") or {}).items()]


def _variant_key(entry: Dict[str, Any], ref: str) -> Optional[str]:
    variants = entry.get("variants") or {}
    if not variants:
        return None
    ref_n = _norm(ref)
    if not ref_n:
        return next(iter(variants))                  # a spell that must choose
    for key, v in variants.items():                  # picks its first by default
        if _norm(key) == ref_n or _norm(v.get("label")) == ref_n:
            return key
    for key, v in variants.items():                  # "star spawn" ~ "starspawn"
        if ref_n.replace(" ", "") in (_norm(key).replace(" ", ""),
                                      _norm(v.get("label")).replace(" ", "")):
            return key
    return next(iter(variants))


def _applies(item: Dict[str, Any], variant: Optional[str]) -> bool:
    """"Claws (Slaad Only)" — a trait or action gated to some variants.

    The books print the gate on the LINE, not on the variant, so that is where
    it lives here too; a variant patch only carries what genuinely differs as a
    number (a Bestial Air spirit's Hit Points and speed).
    """
    only = item.get("only")
    if not only:
        return True
    return variant is not None and _norm(variant) in {_norm(o) for o in only}


# ----------------------------------------------------------------------
# Building the block
# ----------------------------------------------------------------------
def _fill(text: str, ctx: Dict[str, Any]) -> str:
    """Substitute ``{level}`` / ``{dc}`` / ``{attack}`` into book prose."""
    try:
        return str(text).format(**ctx)
    except Exception:
        return str(text)


def _action_rows(entry: Dict[str, Any], variant: Optional[str], level: int,
                 attack_bonus: int, dc: int) -> List[Dict[str, Any]]:
    """Render actions in the shape ``combat/engine.py`` already reads.

    That reader is strict and undocumented anywhere else, so it is worth
    naming: an action counts as an attack only if it carries an int
    ``attack_bonus`` and a ``damage[0].damage_dice``; ``reach``/``range`` are
    parsed out of ``desc`` prose; and Multiattack is recognised by the word
    ("makes two attacks"), which is why a 1-attack routine omits it entirely
    rather than saying "makes one attack" and being read as a second attack.
    """
    ctx = {"level": level, "dc": dc, "attack": f"+{attack_bonus}"}
    rows: List[Dict[str, Any]] = []

    n = scaled(entry.get("multiattack"), level, default=1)
    if n >= 2:
        rows.append({
            "name": "Multiattack",
            "desc": (f"The {entry.get('noun') or 'creature'} makes "
                     f"{_NUMBER_WORDS.get(n, str(n))} attacks."),
        })

    for a in entry.get("actions") or []:
        if not _applies(a, variant):
            continue
        row: Dict[str, Any] = {"name": a.get("name") or "Attack"}
        kind = _norm(a.get("kind"))                  # "melee weapon", "ranged spell"
        bits: List[str] = []
        if kind:
            bits.append(f"{kind.title()} Attack:")
        if a.get("reach_ft"):
            bits.append(f"reach {int(a['reach_ft'])} ft.,")
        if a.get("range_ft"):
            rng = a["range_ft"]
            bits.append(f"range {rng[0]}/{rng[1]} ft.,"
                        if isinstance(rng, (list, tuple))
                        else f"range {int(rng)} ft.,")
        bits.append(a.get("targets") or "one target.")
        if a.get("desc"):
            bits.append(_fill(a["desc"], ctx))
        row["desc"] = " ".join(b for b in bits if b).strip()

        if a.get("damage_dice"):
            bonus = scaled(a.get("damage_bonus"), level)
            dice = str(a["damage_dice"])
            row["attack_bonus"] = int(attack_bonus)
            row["damage"] = [{
                "damage_dice": f"{dice}+{bonus}" if bonus else dice,
                "damage_type": {"name": str(a.get("damage_type") or "").title()},
            }]
            for extra in a.get("extra_damage") or []:
                row["damage"].append({
                    "damage_dice": str(extra.get("dice") or ""),
                    "damage_type": {"name": str(extra.get("type") or "").title()},
                })
        rows.append(row)
    return rows


def _trait_rows(entry: Dict[str, Any], variant: Optional[str], level: int,
                attack_bonus: int, dc: int) -> List[Dict[str, Any]]:
    ctx = {"level": level, "dc": dc, "attack": f"+{attack_bonus}"}
    out = []
    for t in entry.get("traits") or []:
        if not _applies(t, variant):
            continue
        out.append({"name": t.get("name") or "Trait",
                    "desc": _fill(t.get("desc") or "", ctx)})
    return out


def derived_slug(entry: Dict[str, Any], variant: Optional[str], level: int,
                 attack_bonus: int, dc: int) -> str:
    """The identity of a conjured creature: what it is, how strong the slot was,
    and whose magic is holding it up. All three belong in the key — two casters
    of different skill summoning at the same level really do get different
    creatures, and a slug that hid that would serve one of them the other's."""
    parts = [entry["slug"]]
    if variant:
        parts.append(_norm(variant).replace(" ", ""))
    parts.append(f"l{int(level)}a{int(attack_bonus)}d{int(dc)}")
    return "-".join(parts)


def build(entry: Dict[str, Any], *, level: int, variant: Optional[str] = None,
          attack_bonus: int, save_dc: int, proficiency_bonus: int = 2
          ) -> Dict[str, Any]:
    """Every number of one conjured creature, as a ``Monster``-shaped dict."""
    vkey = _variant_key(entry, variant or "")
    patch = ((entry.get("variants") or {}).get(vkey) or {}) if vkey else {}
    level = max(int(level), int(entry.get("min_level") or 1))

    def field(name, default=None):
        return patch.get(name, entry.get(name, default))

    ab = dict(entry.get("abilities") or {})
    ab.update(patch.get("abilities") or {})
    label = str(patch.get("label") or vkey or "").strip()
    name = f"{entry['name']} ({label})" if label else entry["name"]

    return {
        "index_slug": derived_slug(entry, vkey, level, attack_bonus, save_dc),
        "name": name,
        "size": field("size", "Medium"),
        "type": field("type", "elemental"),
        "alignment": field("alignment", "unaligned"),
        "armor_class": scaled(field("armor_class"), level, default=10),
        "ac_desc": field("ac_desc"),
        "hit_points": max(1, scaled(field("hit_points"), level, default=1)),
        "strength": ab.get("str"), "dexterity": ab.get("dex"),
        "constitution": ab.get("con"), "intelligence": ab.get("int"),
        "wisdom": ab.get("wis"), "charisma": ab.get("cha"),
        # A conjured spirit has no CR — its Proficiency Bonus is the caster's,
        # which is exactly what the stat blocks print in that row.
        "challenge_rating": 0.0,
        "proficiency_bonus": int(proficiency_bonus),
        "xp": 0,
        "languages": field("languages"),
        "speed": field("speed") or {"walk": 30},
        "senses": field("senses") or {},
        "damage_resistances": field("damage_resistances") or [],
        "damage_immunities": field("damage_immunities") or [],
        "condition_immunities": field("condition_immunities") or [],
        "special_abilities": _trait_rows(entry, vkey, level, attack_bonus, save_dc),
        "actions": _action_rows(entry, vkey, level, attack_bonus, save_dc),
        "source": DERIVED_SRC,
        "raw": {"summon": entry["slug"], "variant": vkey, "spell_level": level,
                "spell_attack_bonus": int(attack_bonus), "spell_save_dc": int(save_dc)},
    }


def materialize(ref: str, *, level: int, engine, variant: Optional[str] = None,
                attack_bonus: int, save_dc: int, proficiency_bonus: int = 2,
                workspace: Path = WORKSPACE) -> Optional[Monster]:
    """Compute a conjured creature and upsert it into ``rules_monster``.

    Returns the row, or None when nothing in the catalogue answers to ``ref``.
    Idempotent by :func:`derived_slug`, so re-summoning is a lookup.
    """
    entry = spirit_for(ref, workspace=workspace)
    if entry is None:
        return None
    block = build(entry, level=level, variant=variant, attack_bonus=attack_bonus,
                  save_dc=save_dc, proficiency_bonus=proficiency_bonus)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        row = s.exec(select(Monster).where(
            Monster.index_slug == block["index_slug"])).first()
        if row is None:
            row = Monster(index_slug=block["index_slug"], name=block["name"])
        for k, v in block.items():
            if k != "index_slug":
                setattr(row, k, v)
        s.add(row)
        s.commit()
        s.refresh(row)
        return row


def summon_summary(mon: Monster) -> str:
    """One line for the table: what arrived and what it can do."""
    raw = mon.raw or {}
    lvl = raw.get("spell_level")
    speeds = ", ".join(f"{k} {v} ft." for k, v in (mon.speed or {}).items())
    atks = [a["name"] for a in (mon.actions or [])
            if a.get("attack_bonus") is not None]
    bits = [f"AC {mon.armor_class}", f"{mon.hit_points} HP"]
    if speeds:
        bits.append(speeds)
    if atks:
        bits.append("/".join(atks))
    tail = f" (from a {_ordinal(int(lvl))}-level slot)" if lvl else ""
    return f"{mon.name} — {', '.join(bits)}{tail}"


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }".replace(" ", "")
