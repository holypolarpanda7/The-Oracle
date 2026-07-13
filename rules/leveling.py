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
