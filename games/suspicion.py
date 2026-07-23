"""The hidden 'table heat' model — server-held, never shown as a raw number.

A single ``heat`` value (0–100) per game tracks how warily the other players eye
the table. It climbs when someone wins improbably often and when a cheat leaves a
tell, and it bleeds off slowly as ordinary hands pass. The higher the heat, the
harder it is to pull off the next cheat unseen (it adds to the cheat DC). Like a
puzzle's answer key, this lives server-side: the DM is told the qualitative
temperature so the fiction can reflect it, but players only ever feel it.
"""
from __future__ import annotations

from typing import Any, Dict

_HEAT_MAX = 100
# How much a run of wins stokes suspicion.
_STREAK_START = 2       # wins before a streak begins to draw eyes
_STREAK_STEP = 9        # heat added per win beyond the threshold
_IMPROBABLE_BUMP = 6    # extra heat when a win looks statistically lucky


def new_suspicion() -> Dict[str, Any]:
    """A fresh, cool table."""
    return {"heat": 0, "wins": {}, "streak": {}}


def _clamp(v: int) -> int:
    return max(0, min(_HEAT_MAX, int(v)))


def rise(susp: Dict[str, Any], amount: int) -> None:
    susp["heat"] = _clamp(susp.get("heat", 0) + int(amount))


def decay(susp: Dict[str, Any], amount: int) -> None:
    susp["heat"] = _clamp(susp.get("heat", 0) - int(amount))


def dc_modifier(heat: int) -> int:
    """How much the current heat stiffens a cheat's DC (0 → +12)."""
    return _clamp(heat) // 8


def record_win(susp: Dict[str, Any], winner: str, *, improbable: bool = False) -> int:
    """Log a win, extend/reset streaks, and stoke heat. Returns the heat added.

    A player who keeps winning builds a streak; once it passes the threshold each
    further win adds heat (more if the win looked lucky). Everyone else's streak
    resets — suspicion is about *one* person's improbable run.
    """
    if not winner:
        return 0
    streaks = susp.setdefault("streak", {})
    wins = susp.setdefault("wins", {})
    for p in list(streaks):
        if p != winner:
            streaks[p] = 0
    streaks[winner] = streaks.get(winner, 0) + 1
    wins[winner] = wins.get(winner, 0) + 1
    added = 0
    if streaks[winner] >= _STREAK_START:
        added += _STREAK_STEP
    if improbable:
        added += _IMPROBABLE_BUMP
    if added:
        rise(susp, added)
    return added


def describe(heat: int) -> str:
    """The qualitative tell the DM narrates — never the raw number."""
    heat = _clamp(heat)
    if heat < 12:
        return "The table is easy and unguarded."
    if heat < 30:
        return "A flicker of watchfulness — nothing pointed yet."
    if heat < 55:
        return "Eyes drift your way a beat too often; the mood has cooled."
    if heat < 80:
        return "The table is tense — hands are watched, winnings counted twice."
    return "Open suspicion — one wrong move and knives, or fists, come out."
