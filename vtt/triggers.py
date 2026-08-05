"""
When is a board worth putting out?

The VTT is a spotlight, not a stage. Most of a session is conversation, travel
and description, where a grid would only slow the table down and eat tokens in
the DM prompt. A board earns its place when *position and timing change the
outcome*: a fight, a room that is trying to kill you, a puzzle you solve by
standing somewhere, the moment a chase reaches broken ground.

This module holds that policy in one place so the backend doesn't scatter
"should I open a map?" checks through the chat path — and so a DM can retune it
from ``game_config`` without touching the wiring.

    kind = should_open_scene(combat_started=True)
    if kind:
        vtt.open_scene(session_id, kind=kind, archetype=archetype_for(place))
"""
from __future__ import annotations

import re
from typing import Optional

from .models import SceneKind

#: Words in the narration that suggest the ground itself matters right now.
_TACTICAL_HINTS = (
    "ambush", "surround", "flank", "charge", "collapse", "cave in",
    "the floor", "ledge", "chasm", "pressure plate", "portcullis",
    "arrow slit", "murder hole", "high ground", "barricade", "choke",
)

_KIND_WORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("fight", "combat", "battle", "attack", "initiative"), SceneKind.COMBAT),
    (("puzzle", "riddle", "mechanism", "lever", "glyph"), SceneKind.PUZZLE),
    (("chase", "pursuit", "flee", "run down"), SceneKind.CHASE),
    (("trap", "hazard", "collapsing", "flooding"), SceneKind.HAZARD),
    (("explore", "dungeon", "delve", "map"), SceneKind.EXPLORE),
    (("standoff", "parley", "negotiat"), SceneKind.SOCIAL),
)


def scene_kind_for(text: Optional[str], default: str = SceneKind.COMBAT) -> str:
    t = (text or "").strip().lower()
    if t in SceneKind.ALL:
        return t
    for words, kind in _KIND_WORDS:
        if any(w in t for w in words):
            return kind
    return default


# ---------------------------------------------------------------- board size
#
# How much board the table gets is a policy question, so it lives here beside
# "does a board open at all". Until this it was one number per scene KIND: every
# fight was 24x18 whether it was two goblins in a cellar or a cavalry charge.
# Two rules were quietly unreachable as a result — a dashing warhorse crosses a
# 120-ft board in a single turn, and a longbow's 150-ft normal range is longer
# than the whole battlefield, so long-range disadvantage could never fire.

#: Archetypes bounded by ARCHITECTURE rather than by the horizon. A tavern is
#: sixteen squares because the tavern is sixteen squares; being outranged by a
#: longbow indoors is not a sizing bug, it is what a building is. Only open
#: ground grows to fit the speeds and ranges in play.
ENCLOSED = frozenset({
    "tavern", "dungeon-room", "dungeon-complex", "crypt", "sewer", "cave",
    "ship", "skyship", "arena",
})

#: Named scales a DM can force when the fiction wants more room than the
#: roster implies — the charge that starts with two riders and ends with forty.
SCALES: dict[str, tuple[int, int]] = {
    "duel":     (16, 12),
    "skirmish": (20, 16),
    "battle":   (30, 24),
    "pitched":  (44, 34),
    "mounted":  (48, 36),
}

#: Hard limits. The floor is a board you can still manoeuvre on; the ceiling is
#: what ``mapgen`` will generate and what a phone can still read.
MIN_SIDE, MAX_SIDE = 8, 60


def board_size_for(base: tuple[int, int], *, archetype: str = "open",
                   creatures: Optional[list] = None,
                   scale: Optional[str] = None,
                   longest_range_ft: int = 0) -> tuple[int, int]:
    """How big this board should be. ``(width, height)`` in squares.

    ``base`` is the scene kind's default — the answer when nothing else is
    known, and the floor for everything below. ``creatures`` is a list of
    ``(size_squares, speed_ft)``, usually the encounter's roster.

    Three things can make a board bigger, and the largest wins:

    * **room to stand** — a dozen combatants in a 24x18 room is a scrum, so the
      board grows with the footprint actually on it;
    * **room to move** — nobody should cross the whole battlefield in one turn,
      so the width tracks the FASTEST creature present. This is the one that
      makes mounted combat playable at all;
    * **room to shoot** — a bow whose normal range exceeds the board can never
      be at long range, which silently disables a rule the engine enforces.

    A named ``scale`` overrides all of it, because a DM describing a cavalry
    charge knows something the roster doesn't yet.
    """
    if scale and scale.lower() in SCALES:
        w, h = SCALES[scale.lower()]
        return (_clamp(w), _clamp(h))

    w, h = int(base[0]), int(base[1])
    mob = list(creatures or [])

    # Room to stand: total occupied squares, given air to move in. The 8x is
    # the Oracle's own tuning — enough that a line can form and be flanked.
    if mob:
        occupied = sum(max(1, int(sq)) ** 2 for sq, _sp in mob)
        want_area = occupied * 8
        while w * h < want_area and (w < MAX_SIDE or h < MAX_SIDE):
            if w / max(1, h) < 4 / 3:
                w += 2
            else:
                h += 2
            if w >= MAX_SIDE and h >= MAX_SIDE:
                break

    # Room to move and room to shoot apply OUTDOORS only: a corridor does not
    # widen because someone brought a longbow.
    if (archetype or "open").lower() not in ENCLOSED:
        if mob:
            fastest_sq = max(int(sp or 0) for _sq, sp in mob) / 5.0
            # Three moves to cross it: enough for a charge to be a decision
            # rather than the whole encounter.
            w = max(w, int(round(fastest_sq * 3)))
        if longest_range_ft:
            # A little past the range band, so "at long range" is a place you
            # can actually stand rather than a theoretical one.
            w = max(w, int(round(longest_range_ft / 5.0 * 1.2)))
        h = max(h, int(round(w * 0.72)))       # keep a usable aspect

    return (_clamp(w), _clamp(h))


def _clamp(v: int) -> int:
    return max(MIN_SIDE, min(MAX_SIDE, int(v)))


def should_open_scene(*, combat_started: bool = False,
                      puzzle_started: bool = False,
                      chase_started: bool = False,
                      hazard_triggered: bool = False,
                      explicit: Optional[str] = None,
                      narration: str = "",
                      config=None) -> Optional[str]:
    """The scene kind to open, or ``None`` to stay in theater of the mind.

    Order matters: an explicit DM hook always wins, then the mechanical
    triggers (a fight really has started), then a soft read of the narration —
    which only fires for scenes the config has opted into.
    """
    cfg = _cfg(config)
    if not getattr(cfg, "enabled", True):
        return None

    if explicit:
        return scene_kind_for(explicit)
    if combat_started and getattr(cfg, "auto_open_combat", True):
        return SceneKind.COMBAT
    if chase_started and getattr(cfg, "auto_open_chase", True):
        return SceneKind.CHASE
    if hazard_triggered and getattr(cfg, "auto_open_hazard", False):
        return SceneKind.HAZARD
    if puzzle_started and getattr(cfg, "auto_open_puzzle", True):
        # Only spatial puzzles want a board; a riddle spoken aloud does not.
        if _looks_spatial(narration):
            return SceneKind.PUZZLE
    return None


def should_close_scene(kind: str, *, combat_active: bool = False,
                       puzzle_active: bool = False,
                       chase_active: bool = False) -> bool:
    """A board closes when the thing that justified it is over."""
    if kind == SceneKind.COMBAT:
        return not combat_active
    if kind == SceneKind.PUZZLE:
        return not puzzle_active
    if kind == SceneKind.CHASE:
        return not chase_active
    # Hazard/explore/social boards are closed explicitly by the DM.
    return False


def _looks_spatial(narration: str) -> bool:
    t = (narration or "").lower()
    if not t:
        return False
    if any(h in t for h in _TACTICAL_HINTS):
        return True
    # Distances, room dimensions and directional language are decent tells.
    if re.search(r"\b\d+\s*(?:ft|feet|foot|paces|squares)\b", t):
        return True
    return bool(re.search(r"\b(north|south|east|west|far side|opposite wall|"
                          r"across the (?:room|chamber|hall))\b", t))


def _cfg(config=None):
    if config is not None:
        return config
    try:
        from game_config import get_config
        return get_config().vtt
    except Exception:
        class _Fallback:
            enabled = True
            auto_open_combat = True
            auto_open_chase = True
            auto_open_hazard = False
            auto_open_puzzle = True
        return _Fallback()
