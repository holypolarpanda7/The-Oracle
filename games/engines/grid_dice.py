"""Grid Clash — an original two-player dice-placement duel.

Each duelist owns a 3×3 board of three columns. On your turn you roll a die and
drop it into one of your columns (up to three deep). Matching dice in the same
column multiply — two of a kind score fourfold, three of a kind ninefold — so a
column of 5-5-5 is worth 45. The bite: dropping a value into a column smashes every
die of that same value in your opponent's matching column. When either board fills,
the higher total wins. Everything is on the table — the only way to cheat here is
to fix the die you roll, which the cheat layer handles.
"""
from __future__ import annotations

import random
from typing import List

from ..models import MoveResult
from .base import GameEngine

_COLS = 3
_DEPTH = 3
_FACES = 6


class GridClash(GameEngine):
    id = "grid_dice"
    name = "Grid Clash"
    min_players = 2
    max_players = 2

    def start(self, players: List[str], rng: random.Random, *, wager: int = 0) -> dict:
        order = list(players)[:2]
        state = {
            "game": self.id,
            "order": order,
            "turn": 0,
            "grids": {p: [[] for _ in range(_COLS)] for p in order},
            "roll": rng.randint(1, _FACES),
            "wager": int(wager),
            "over": False,
            "winner": None,
        }
        return state

    def legal_moves(self, state: dict, actor: str) -> List[str]:
        if state.get("over") or self.current_actor(state) != actor:
            return []
        grid = state["grids"][actor]
        return [f"place {c + 1}" for c in range(_COLS) if len(grid[c]) < _DEPTH]

    def apply_move(self, state: dict, actor: str, move: str,
                   rng: random.Random) -> MoveResult:
        if state.get("over"):
            return MoveResult.illegal("the game is already over")
        if self.current_actor(state) != actor:
            return MoveResult.illegal(f"it is not {actor}'s turn")
        col = self._parse_col(move)
        if col is None:
            return MoveResult.illegal("say 'place <column 1-3>'")
        grid = state["grids"][actor]
        if len(grid[col]) >= _DEPTH:
            return MoveResult.illegal(f"column {col + 1} is full")
        die = int(state["roll"])
        grid[col].append(die)
        public = [f"{actor} sets a **{die}** in column {col + 1}."]
        # Cross-destruction: clear the opponent's matching column of this value.
        opp = self._opponent(state, actor)
        smashed = [d for d in state["grids"][opp][col] if d == die]
        if smashed:
            state["grids"][opp][col] = [d for d in state["grids"][opp][col] if d != die]
            public.append(
                f"— it shatters {len(smashed)} matching die(s) in {opp}'s column "
                f"{col + 1}!")
        # End if either board is full.
        if self._full(state["grids"][actor]) or self._full(state["grids"][opp]):
            return self._finish(state, public)
        self._advance(state)
        state["roll"] = rng.randint(1, _FACES)
        s = self._score(state)
        public.append(
            f"Score — {' | '.join(f'{p}: {s[p]}' for p in state['order'])}. "
            f"{self.current_actor(state)} rolls a **{state['roll']}**.")
        return MoveResult(ok=True, public=public)

    # ----- internals -----
    @staticmethod
    def _parse_col(move: str):
        for tok in (move or "").lower().replace("place", " ").split():
            if tok.isdigit() and 1 <= int(tok) <= _COLS:
                return int(tok) - 1
        return None

    def _opponent(self, state: dict, actor: str) -> str:
        return next(p for p in state["order"] if p != actor)

    @staticmethod
    def _full(grid) -> bool:
        return all(len(col) >= _DEPTH for col in grid)

    @staticmethod
    def _col_score(col) -> int:
        total = 0
        for v in set(col):
            k = col.count(v)
            total += v * k * k
        return total

    def _score(self, state: dict) -> dict:
        return {p: sum(self._col_score(c) for c in state["grids"][p])
                for p in state["order"]}

    def _finish(self, state: dict, public: List[str]) -> MoveResult:
        s = self._score(state)
        state["over"] = True
        best = max(s.values())
        winners = [p for p in state["order"] if s[p] == best]
        state["winner"] = winners[0] if len(winners) == 1 else None
        public.append(f"Final — {' | '.join(f'{p}: {s[p]}' for p in state['order'])}.")
        public.append("🏆 It's a tie!" if state["winner"] is None
                      else f"🏆 **{state['winner']}** wins.")
        return MoveResult(ok=True, public=public)

    def is_over(self, state: dict) -> bool:
        return bool(state.get("over"))

    def result(self, state: dict) -> dict:
        s = self._score(state)
        ranking = sorted(state["order"], key=lambda p: s[p], reverse=True)
        return {"winner": state.get("winner"), "ranking": ranking, "scores": s}

    def public_view(self, state: dict) -> str:
        s = self._score(state)
        lines = []
        for p in state["order"]:
            cols = " / ".join("-".join(str(d) for d in c) or "·"
                              for c in state["grids"][p])
            lines.append(f"{p} [{s[p]}]: {cols}")
        return (f"Grid Clash. " + "  ".join(lines) +
                f". {self.current_actor(state)} to place a {state['roll']}.")
