"""In-game games: tavern dice, card betting, and grid duels the players play
*inside* the world — for fun or for coin — with server-held fair RNG, a hidden
'table heat' suspicion model, and skill/spell-based cheating that only works when
it isn't obvious.

Game state lives as plain dicts in the backend's per-session meta (no new DB
table), the same way fate decks and active puzzles do. Engines are pure state
machines; randomness comes from a seeded ``random.Random`` the backend passes in.
"""
from .catalog import (
    GAME_GUIDE,
    GAME_KEYWORDS,
    engine_for_state,
    get_engine,
    get_spec,
    list_games,
)
from .cheat import adjudicate
from .models import (
    CheatAttempt,
    CheatOutcome,
    CheatRuling,
    Detectability,
    GameSpec,
    MoveResult,
    SpellComponents,
)
from . import suspicion

__all__ = [
    "GAME_GUIDE",
    "GAME_KEYWORDS",
    "list_games",
    "get_spec",
    "get_engine",
    "engine_for_state",
    "adjudicate",
    "suspicion",
    "CheatAttempt",
    "CheatOutcome",
    "CheatRuling",
    "Detectability",
    "GameSpec",
    "MoveResult",
    "SpellComponents",
]
