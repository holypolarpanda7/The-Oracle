"""Revival-spell mechanics — the small, factual SRD table the REVIVE hook resolves.

Death in this world is reversible by magic. Each revival spell restores a different
amount of HP, may leave a fading penalty, and may or may not require a WILLING soul.
That last flag is what a DNR (do-not-resuscitate) wish acts on: for a spell that
needs the soul's consent, a DNR soul simply refuses (the revival fails); for a spell
that drags a body back without consent (Revivify), the DNR can't stop it — the
character returns, furious at whoever pulled them back.

Only mechanical numbers live here (safe to commit); the DM narrates the rest.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class SpellSpec:
    slug: str
    name: str
    hp_rule: str                       # "one" (1 HP) | "full" (to max)
    penalty: Optional[Tuple[int, int]] # (amount, fade_days) applied to d20 rolls, or None
    needs_willing: bool                # the soul must consent (a DNR can refuse)
    reincarnate: bool                  # returns in a new body (DM narrates the form)
    window: str                        # how soon after death it must be cast (flavor)


# Canonical revival spells. Numbers follow the SRD's mechanical shape.
REVIVAL_SPELLS = {
    "revivify": SpellSpec(
        "revivify", "Revivify", "one", None, needs_willing=False,
        reincarnate=False, window="within 1 minute of death"),
    "raise-dead": SpellSpec(
        "raise-dead", "Raise Dead", "one", (4, 4), needs_willing=True,
        reincarnate=False, window="within 10 days of death"),
    "resurrection": SpellSpec(
        "resurrection", "Resurrection", "full", (4, 4), needs_willing=True,
        reincarnate=False, window="within a century of death"),
    "true-resurrection": SpellSpec(
        "true-resurrection", "True Resurrection", "full", None, needs_willing=True,
        reincarnate=False, window="within 200 years of death"),
    "reincarnate": SpellSpec(
        "reincarnate", "Reincarnate", "one", None, needs_willing=True,
        reincarnate=True, window="within 10 days of death"),
}

# A spell we don't recognize is treated as a generic willing-soul resurrection —
# the faithful default (most revival magic needs consent, so a DNR still bites).
_GENERIC = SpellSpec("revival", "revival magic", "one", None, needs_willing=True,
                     reincarnate=False, window="")

_ALIASES = {
    "revive": "revivify", "revivify": "revivify",
    "raise dead": "raise-dead", "raise the dead": "raise-dead", "raisedead": "raise-dead",
    "resurrect": "resurrection", "resurrection": "resurrection",
    "true resurrection": "true-resurrection", "true res": "true-resurrection",
    "greater resurrection": "true-resurrection",
    "reincarnate": "reincarnate", "reincarnation": "reincarnate",
}


def get_spell(ref: str) -> SpellSpec:
    """Resolve a spell name/alias to its spec (generic willing-soul fallback)."""
    r = re.sub(r"[\s_]+", " ", (ref or "").strip().lower())
    if not r:
        return _GENERIC
    key = re.sub(r"[\s]+", "-", r)
    if key in REVIVAL_SPELLS:
        return REVIVAL_SPELLS[key]
    if r in _ALIASES:
        return REVIVAL_SPELLS[_ALIASES[r]]
    for alias, slug in _ALIASES.items():
        if alias in r or r in alias:
            return REVIVAL_SPELLS[slug]
    return _GENERIC


@dataclass
class RevivalPlan:
    spell: str                          # canonical display name
    hp: int                             # HP the target is restored to
    penalty: Optional[Tuple[int, int]]  # (amount, fade_days) or None
    needs_willing: bool
    reincarnate: bool
    window: str
    refuses: bool                       # a willing-soul spell + DNR → soul refuses (fails)
    forced_anger: bool                  # a no-consent spell + DNR → returns furious


def resolve(spell: str, dnr: bool, max_hp: int) -> RevivalPlan:
    """Compute what a given revival attempt does, honoring the target's DNR wish."""
    spec = get_spell(spell)
    hp = max(1, int(max_hp or 1)) if spec.hp_rule == "full" else 1
    return RevivalPlan(
        spell=spec.name,
        hp=hp,
        penalty=spec.penalty,
        needs_willing=spec.needs_willing,
        reincarnate=spec.reincarnate,
        window=spec.window,
        refuses=bool(dnr and spec.needs_willing),
        forced_anger=bool(dnr and not spec.needs_willing),
    )
