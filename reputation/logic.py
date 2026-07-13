"""Reputation logic: map renown to a standing, adjust it, and describe perks.

Thresholds are tunable via ``config.reputation.thresholds`` (name -> min renown).
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from game_config import get_config


# Concise, self-authored perks per standing tier (owned mechanical flavour).
_STANDING_PERKS = {
    "unknown": "No standing; the faction has never heard of you.",
    "known": "Recognised by rank-and-file; minor favours and rumours.",
    "accepted": "A trusted associate; access to a safehouse, gear at fair prices.",
    "respected": "A valued agent; introductions to leaders, loaned resources.",
    "honored": "A hero of the cause; sanctuary, magic-item requisitions, allies in a crisis.",
}


def _sorted_thresholds() -> List[Tuple[str, int]]:
    thresholds = get_config().reputation.thresholds
    return sorted(thresholds.items(), key=lambda kv: kv[1])


def standing_for_renown(renown: int) -> str:
    """Return the highest standing whose threshold the renown meets."""
    standing = "unknown"
    for name, minimum in _sorted_thresholds():
        if renown >= minimum:
            standing = name
    return standing


def next_standing(renown: int) -> Dict:
    """Return the next standing above the current renown and how far away it is."""
    for name, minimum in _sorted_thresholds():
        if renown < minimum:
            return {"standing": name, "at_renown": minimum, "needed": minimum - renown}
    return {"standing": None, "at_renown": None, "needed": 0}


def describe_standing(renown: int) -> Dict:
    if not get_config().reputation.enabled:
        return {"enabled": False, "note": "Reputation is disabled in the current config."}
    standing = standing_for_renown(renown)
    return {
        "enabled": True,
        "renown": renown,
        "standing": standing,
        "perks": _STANDING_PERKS.get(standing, ""),
        "next": next_standing(renown),
    }


def adjust_renown(current: int, delta: int) -> Dict:
    """Apply a renown change, reporting any standing transition."""
    before = standing_for_renown(current)
    new = max(0, current + delta)
    after = standing_for_renown(new)
    changed = before != after
    if changed:
        direction = "risen to" if new > current else "fallen to"
        note = f"Standing has {direction} '{after}'."
    else:
        note = f"Renown {'+' if delta >= 0 else ''}{delta} (now {new}); still '{after}'."
    return {"renown": new, "standing": after, "standing_changed": changed, "note": note}
