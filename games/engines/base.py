"""The engine contract every in-game game implements.

An engine is a pure state machine over a JSON-serializable ``state`` dict. It never
touches the DB, the session, or the RNG seed itself — the backend owns persistence
and passes a *seeded* ``random.Random`` so draws/rolls are fair and reproducible
(the same seeded-RNG discipline as the fate decks). The crucial split is
``public_view`` vs ``private_view``: hidden information (your own dice, your own
hand) is only ever rendered to its owner, so it can be whispered rather than
broadcast and never leaks into the shared DM prompt.
"""
from __future__ import annotations

import random
from abc import ABC, abstractmethod
from typing import Dict, List

from ..models import MoveResult


class GameEngine(ABC):
    id: str = "game"
    name: str = "Game"
    min_players: int = 2
    max_players: int = 2

    # ----- lifecycle -----
    @abstractmethod
    def start(self, players: List[str], rng: random.Random, *, wager: int = 0) -> dict:
        """Create the initial state for ``players`` (turn order = list order).

        ``wager`` is the per-player stake in gp (0 = friendly, no stakes). The
        returned dict must be JSON-serializable and carry everything the other
        methods need — engines are otherwise stateless.
        """

    @abstractmethod
    def legal_moves(self, state: dict, actor: str) -> List[str]:
        """The moves ``actor`` may legally make right now (empty if not their turn)."""

    @abstractmethod
    def apply_move(self, state: dict, actor: str, move: str,
                   rng: random.Random) -> MoveResult:
        """Validate + apply ``move`` in place on ``state``. Illegal → MoveResult.illegal."""

    @abstractmethod
    def is_over(self, state: dict) -> bool:
        ...

    @abstractmethod
    def result(self, state: dict) -> dict:
        """Final standings once :meth:`is_over` — ``{"winner": id|None, "ranking": [...]}``."""

    # ----- views -----
    @abstractmethod
    def public_view(self, state: dict) -> str:
        """The board as everyone at the table sees it (no hidden info)."""

    def private_view(self, state: dict, actor: str) -> str:
        """What ``actor`` alone sees (their hidden dice/hand). Default: nothing."""
        return ""

    # ----- helpers shared by engines -----
    @staticmethod
    def current_actor(state: dict) -> str:
        order = state.get("order") or []
        idx = int(state.get("turn", 0)) % max(1, len(order))
        return order[idx] if order else ""

    @staticmethod
    def _advance(state: dict, players: List[str] | None = None) -> None:
        """Advance the turn pointer, skipping anyone in ``state['out']``."""
        order = state.get("order") or []
        if not order:
            return
        out = set(state.get("out", []))
        n = len(order)
        for step in range(1, n + 1):
            nxt = (int(state.get("turn", 0)) + step) % n
            if order[nxt] not in out:
                state["turn"] = nxt
                return
        state["turn"] = (int(state.get("turn", 0)) + 1) % n
