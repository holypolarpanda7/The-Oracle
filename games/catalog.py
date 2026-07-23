"""The game catalog: id/alias → spec + engine factory, plus the DM prompt guide.

All three games are generic, own-worded folk mechanics (Liar's Dice/Perudo and
shell games are public-domain; Grid Clash and Ante are original rules) — no
verbatim copy of any published game, per the repo's rules-content policy.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from .engines import Ante, GameEngine, GridClash, LiarsDice
from .models import GameSpec

_SPECS: Dict[str, GameSpec] = {
    "liars_dice": GameSpec(
        id="liars_dice", name="Liar's Dice",
        blurb="a cup-and-dice bluffing game of escalating claims and bald-faced lies",
        min_players=2, max_players=6, factory=LiarsDice,
        aliases=["liars", "perudo", "dudo", "bluff dice", "dice bluff"]),
    "grid_dice": GameSpec(
        id="grid_dice", name="Grid Clash",
        blurb="a two-board dice duel where matches multiply and clashes shatter",
        min_players=2, max_players=2, factory=GridClash,
        aliases=["grid", "clash", "knucklebones", "columns", "column clash"]),
    "card_bet": GameSpec(
        id="card_bet", name="Ante",
        blurb="a three-card betting game of hidden hands, raises, and nerve",
        min_players=2, max_players=6, factory=Ante,
        aliases=["ante", "cards", "betting", "poker", "three card"]),
}

def _norm(s: str) -> str:
    return re.sub(r"[\s_-]+", " ", (s or "").strip().lower())


# alias/keyword → canonical id (keys normalized the same way queries are)
_LOOKUP: Dict[str, str] = {}
for _sid, _spec in _SPECS.items():
    for _key in (_sid, _spec.name, *_spec.aliases):
        _LOOKUP[_norm(_key)] = _sid


def list_games() -> List[GameSpec]:
    return list(_SPECS.values())


def get_spec(ref: str) -> Optional[GameSpec]:
    """Resolve a game by id, name, or alias (fuzzy-ish, case-insensitive)."""
    r = _norm(ref)
    if not r:
        return None
    if r in _LOOKUP:
        return _SPECS[_LOOKUP[r]]
    for key, sid in _LOOKUP.items():
        if key in r or r in key:
            return _SPECS[sid]
    return None


def get_engine(ref: str) -> Optional[GameEngine]:
    spec = get_spec(ref)
    return spec.factory() if spec else None


def engine_for_state(state: dict) -> Optional[GameEngine]:
    """Rebuild the right engine for a persisted game-state dict."""
    return get_engine(state.get("game", ""))


# ---- prompt integration (keyword-gated, like _DECK_GUIDE) ----

GAME_GUIDE = (
    "TAVERN GAMES. Players can play games in the world — dice, cards, shell games — "
    "for fun OR for coin. When a game begins, arm it: "
    "[[GAME: start | liars_dice|grid_dice|card_bet | opponents (comma-sep) | wager: N gp OR friendly]]. "
    "The GAME runs the rules and all randomness fairly server-side — you do NOT invent "
    "rolls, deals, or who wins. Each turn, emit the chosen move as "
    "[[GAME: move | who | the move]] and you'll be shown the result (and each player's "
    "PRIVATE hand goes only to them). To settle up: [[GAME: end]] (friendly) or "
    "[[GAME: settle]] (pay out the pot). "
    "CHEATING: a player may try to cheat with a skill check or a spell. Emit "
    "[[GAME: cheat | method | Sleight of Hand 18 OR spell:Detect Thoughts | subtle|risky|brazen]]. "
    "The game rules whether it's noticed — a quiet method can slip by, but a spell with a "
    "verbal, somatic, or material component is SEEN and gives them away. A secret cheat and "
    "its true result are WHISPERED to the cheater; the table sees only an innocent line. "
    "Keep a running winner honest: the more someone wins or cheats, the warier the table "
    "grows and the harder cheating gets — you'll be told the table's temperature privately."
)

GAME_KEYWORDS = (
    "game", "gamble", "gambling", "bet", "betting", "wager", "ante", "pot", "stakes",
    "dice", "liar's dice", "liars dice", "perudo", "knucklebones", "cards", "card game",
    "deal", "shuffle", "cheat", "cheating", "palm", "loaded dice", "marked cards",
    "tavern game", "shell game", "three-card", "poker", "high card", "play a hand",
)
