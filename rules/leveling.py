"""
SRD level-up guidance (structured, numbers-only — no book prose).

Characters are *created* at level 1; advancement happens one level at a time via
this module + the backend's ``/level_up`` flow. Everything here is SRD-safe
mechanical scaffolding (proficiency bonus, ASI levels, hit-point gain, and *when*
a subclass is chosen / which subclass features unlock at a level).

    from rules.leveling import level_up_report
    report = level_up_report(
        class_name="Monk", hit_die=8, subclass_level=3,
        subclass_name=None, subclass_features=None, con_mod=2,
        old_level=2, new_level=3,
    )
"""
from __future__ import annotations

from typing import Optional

from dice import proficiency_bonus_for_level

# Standard Ability Score Improvement levels (SRD, most classes).
ASI_LEVELS = {4, 8, 12, 16, 19}
# Classes that gain extra ASIs beyond the standard schedule.
EXTRA_ASI_LEVELS = {
    "fighter": {6, 14},
    "rogue": {10},
}

MAX_LEVEL = 20


def asi_at_level(class_name: Optional[str], level: int) -> bool:
    """True if an Ability Score Improvement (or feat) is granted at ``level``."""
    if level in ASI_LEVELS:
        return True
    extra = EXTRA_ASI_LEVELS.get((class_name or "").lower(), set())
    return level in extra


def average_hp_gain(hit_die: Optional[int], con_mod: int) -> int:
    """SRD fixed HP-per-level: (hit_die / 2 + 1) + Con modifier, min 1."""
    die = hit_die or 8
    return max(1, die // 2 + 1 + con_mod)


def hp_roll_expr(hit_die: Optional[int], con_mod: int) -> str:
    """Dice expression for rolling this level's HP (alternative to fixed average)."""
    die = hit_die or 8
    if con_mod:
        return f"1d{die}{con_mod:+d}"
    return f"1d{die}"


# ===================== spellcasting progression =============================
# Single source of truth for how many cantrips + leveled spells a caster
# knows/prepares at a level — used by both character creation and level-up so
# the two never drift. SRD-safe counts (mechanical facts, not book prose).
#
# Two paradigms the game (and the user) distinguish:
#   * "memorized"/KNOWN casters (bard, sorcerer, warlock, ranger) learn a fixed
#     set of spells that grows on a per-class table; the wizard is a variant
#     that learns into a SPELLBOOK (+2/level).
#   * PREPARED casters (cleric, druid, paladin, artificer) prepare a number of
#     spells = spellcasting modifier + a level factor, re-choosable on a rest.

# Cantrips known: (min_level, count) tiers — the count is the last tier at or
# below the level. Classes absent here have no cantrips (paladin, ranger).
_CANTRIPS_KNOWN = {
    "bard":      [(1, 2), (4, 3), (10, 4)],
    "cleric":    [(1, 3), (4, 4), (10, 5)],
    "druid":     [(1, 2), (4, 3), (10, 4)],
    "sorcerer":  [(1, 4), (4, 5), (10, 6)],
    "warlock":   [(1, 2), (4, 3), (10, 4)],
    "wizard":    [(1, 3), (4, 4), (10, 5)],
    "artificer": [(1, 2), (10, 3), (14, 4)],
}

# Leveled spells the character has by level (index = level-1). Values verified
# against the 2024 SRD 5.2 class tables (5e-bits/5e-database src/2024, the
# `prepared_spells` column — 2024 uses fixed per-level counts, no ability-mod
# term). The wizard is the exception (spellbook, computed below). Artificer is
# NOT in the 2024 SRD (owned book) — kept as a half-caster mirror of paladin.
_SPELLS_BY_LEVEL = {
    # memorized / known lists
    "bard":     [4, 5, 6, 7, 9, 10, 11, 12, 14, 15, 16, 16, 17, 17, 18, 18, 19, 20, 21, 22],
    "sorcerer": [2, 4, 6, 7, 9, 10, 11, 12, 14, 15, 16, 16, 17, 17, 18, 18, 19, 20, 21, 22],
    "warlock":  [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 11, 11, 12, 12, 13, 13, 14, 14, 15, 15],
    "ranger":   [2, 3, 4, 5, 6, 6, 7, 7, 9, 9, 10, 10, 11, 11, 12, 12, 14, 14, 15, 15],
    # prepared counts (full: cleric/druid; half: paladin/artificer)
    "cleric":   [4, 5, 6, 7, 9, 10, 11, 12, 14, 15, 16, 16, 17, 17, 18, 18, 19, 20, 21, 22],
    "druid":    [4, 5, 6, 7, 9, 10, 11, 12, 14, 15, 16, 16, 17, 17, 18, 18, 19, 20, 21, 22],
    "paladin":  [2, 3, 4, 5, 6, 6, 7, 7, 9, 9, 10, 10, 11, 11, 12, 12, 14, 14, 15, 15],
    "artificer":[2, 3, 4, 5, 6, 6, 7, 7, 9, 9, 10, 10, 11, 11, 12, 12, 14, 14, 15, 15],
}

# How each caster manages its leveled spells (drives the picker's wording).
CASTER_MODE = {
    "bard": "known", "sorcerer": "known", "warlock": "known", "ranger": "known",
    "cleric": "prepared", "druid": "prepared", "paladin": "prepared",
    "artificer": "prepared", "wizard": "spellbook",
}


def is_caster(class_name: Optional[str]) -> bool:
    return (class_name or "").strip().lower() in CASTER_MODE


def caster_mode(class_name: Optional[str]) -> Optional[str]:
    return CASTER_MODE.get((class_name or "").strip().lower())


def cantrips_known(class_name: Optional[str], level: int) -> int:
    tiers = _CANTRIPS_KNOWN.get((class_name or "").strip().lower())
    if not tiers:
        return 0
    n = 0
    for lv, cnt in tiers:
        if level >= lv:
            n = cnt
    return n


def spells_count(class_name: Optional[str], level: int) -> int:
    """Leveled spells the character knows/prepares at this level (fixed per-class
    per-level; wizard = spellbook size)."""
    cls = (class_name or "").strip().lower()
    level = max(1, min(MAX_LEVEL, int(level or 1)))
    if cls == "wizard":
        return 6 + 2 * (level - 1)            # spellbook size
    table = _SPELLS_BY_LEVEL.get(cls)
    return table[level - 1] if table else 0


def spell_progression(class_name: Optional[str], level: int) -> Optional[dict]:
    """{mode, cantrips_known, spells_count} at a level, or None for non-casters."""
    if not is_caster(class_name):
        return None
    return {"mode": caster_mode(class_name),
            "cantrips_known": cantrips_known(class_name, level),
            "spells_count": spells_count(class_name, level)}


def spells_gained(class_name: Optional[str], old_level: int, new_level: int) -> dict:
    """How many NEW cantrips + leveled spells to pick when advancing a level.
    Prepared casters re-prepare freely, but a growing prepared count still means
    the player adds (new-old) more prepared spells."""
    return {
        "cantrips": max(0, cantrips_known(class_name, new_level)
                        - cantrips_known(class_name, old_level)),
        "spells": max(0, spells_count(class_name, new_level)
                      - spells_count(class_name, old_level)),
    }


def features_gained_at(subclass_features: Optional[list], level: int) -> list[dict]:
    """Subclass features (from a Subclass row's ``features`` JSON) unlocked at ``level``."""
    if not subclass_features:
        return []
    return [f for f in subclass_features if int(f.get("level", 0)) == level]


def level_up_report(
    *,
    class_name: str,
    hit_die: Optional[int],
    subclass_level: int,
    subclass_name: Optional[str],
    subclass_features: Optional[list],
    con_mod: int = 0,
    old_level: int,
    new_level: int,
) -> dict:
    """Summarize what changes when a character goes from ``old_level`` to ``new_level``.

    Returns a structured dict (also carries a human-readable ``text`` block for the
    DM/player). Does not mutate anything — the caller decides whether to apply it.
    """
    notes: list[str] = []

    if new_level > MAX_LEVEL:
        return {"ok": False, "error": f"Max level is {MAX_LEVEL}.", "notes": notes}
    if new_level != old_level + 1:
        return {
            "ok": False,
            "error": "Level up advances exactly one level at a time.",
            "notes": notes,
        }

    prof_before = proficiency_bonus_for_level(old_level)
    prof_after = proficiency_bonus_for_level(new_level)
    prof_changed = prof_after != prof_before
    if prof_changed:
        notes.append(f"Proficiency bonus increases to +{prof_after}.")

    hp_avg = average_hp_gain(hit_die, con_mod)
    notes.append(
        f"Gain hit points: roll {hp_roll_expr(hit_die, con_mod)} or take the "
        f"fixed average of {hp_avg}."
    )

    asi = asi_at_level(class_name, new_level)
    if asi:
        notes.append(
            "Ability Score Improvement: raise one ability by 2 or two by 1 "
            "(or take a feat)."
        )

    # Subclass timing.
    subclass_choice_due = (new_level == subclass_level) and not subclass_name
    if subclass_choice_due:
        notes.append(
            f"You reach the level where your class chooses its subclass "
            f"(level {subclass_level}). Pick one now."
        )
    elif new_level < subclass_level and not subclass_name:
        notes.append(
            f"No subclass yet — your class selects one at level {subclass_level}."
        )

    gained = features_gained_at(subclass_features, new_level)
    for f in gained:
        notes.append(f"Subclass feature — {f.get('name')}: {f.get('summary', '')}".rstrip())

    lines = [f"# Level up: {class_name} {old_level} \u2192 {new_level}"]
    if subclass_name:
        lines[0] += f" ({subclass_name})"
    lines += [f"- {n}" for n in notes]
    text = "\n".join(lines)

    return {
        "ok": True,
        "class_name": class_name,
        "old_level": old_level,
        "new_level": new_level,
        "proficiency_bonus_before": prof_before,
        "proficiency_bonus_after": prof_after,
        "proficiency_bonus_changed": prof_changed,
        "hp_gain_average": hp_avg,
        "hp_roll_expr": hp_roll_expr(hit_die, con_mod),
        "asi_or_feat": asi,
        "subclass_choice_due": subclass_choice_due,
        "subclass_features_gained": gained,
        "notes": notes,
        "text": text,
    }
