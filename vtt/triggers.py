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
