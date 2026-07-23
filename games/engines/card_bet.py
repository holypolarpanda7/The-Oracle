"""Ante — an original three-card betting game with hidden hands.

Every player antes into the pot and is dealt three cards, face-down and private.
One betting round goes around the table: check, call, raise, or fold. When the
betting settles, the survivors show their cards and the highest three-card total
takes the pot. The hidden hand is the whole point — peeking at a rival's cards or
swapping one of your own is exactly the kind of quiet cheat the cheat layer rules on.
"""
from __future__ import annotations

import random
from typing import List

from ..models import MoveResult
from .base import GameEngine

_HAND = 3
_MIN_CARD, _MAX_CARD = 1, 10
_COPIES = 4


class Ante(GameEngine):
    id = "card_bet"
    name = "Ante"
    min_players = 2
    max_players = 6

    def start(self, players: List[str], rng: random.Random, *, wager: int = 0) -> dict:
        order = list(players)
        ante = max(0, int(wager))
        deck = [v for v in range(_MIN_CARD, _MAX_CARD + 1) for _ in range(_COPIES)]
        rng.shuffle(deck)
        hands = {p: sorted(deck.pop() for _ in range(_HAND)) for p in order}
        return {
            "game": self.id,
            "order": order,
            "turn": 0,
            "hands": hands,
            "deck": deck,
            "ante": ante,
            "pot": ante * len(order),
            "bets": {p: 0 for p in order},
            "to_match": 0,
            "acted": [],
            "folded": [],
            "wager": ante,
            "over": False,
            "winner": None,
            "revealed": False,
        }

    def legal_moves(self, state: dict, actor: str) -> List[str]:
        if state.get("over") or self.current_actor(state) != actor:
            return []
        owed = state["to_match"] - state["bets"][actor]
        first = ["call"] if owed > 0 else ["check"]
        return first + ["raise <amount>", "fold"]

    def apply_move(self, state: dict, actor: str, move: str,
                   rng: random.Random) -> MoveResult:
        if state.get("over"):
            return MoveResult.illegal("the hand is already settled")
        if self.current_actor(state) != actor:
            return MoveResult.illegal(f"it is not {actor}'s turn")
        m = (move or "").strip().lower()
        owed = state["to_match"] - state["bets"][actor]

        if m.startswith("fold"):
            state["folded"].append(actor)
            self._mark(state, actor)
            public = [f"{actor} folds."]
        elif m.startswith("check"):
            if owed > 0:
                return MoveResult.illegal(
                    f"{actor} owes {owed} to stay in — call, raise, or fold")
            self._mark(state, actor)
            public = [f"{actor} checks."]
        elif m.startswith("call"):
            if owed <= 0:
                return MoveResult.illegal("nothing to call — check or raise")
            self._commit(state, actor, owed)
            self._mark(state, actor)
            public = [f"{actor} calls {owed} (pot {state['pot']})."]
        elif m.startswith("raise") or m.startswith("bet"):
            amt = self._parse_amount(m)
            if amt is None or amt <= 0:
                return MoveResult.illegal("say 'raise <amount>' with a positive number")
            self._commit(state, actor, owed + amt)   # match then raise by amt
            state["to_match"] = state["bets"][actor]
            state["acted"] = [actor]                  # a raise reopens the round
            public = [f"{actor} raises {amt} — it's {state['to_match']} to stay "
                      f"(pot {state['pot']})."]
        else:
            return MoveResult.illegal("check, call, raise <amount>, or fold")

        # One player left standing → they scoop it, no showdown.
        active = [p for p in state["order"] if p not in state["folded"]]
        if len(active) == 1:
            return self._award(state, active[0], public, showdown=False)
        if self._round_settled(state, active):
            return self._showdown(state, active, public)
        self._advance(state)
        while self.current_actor(state) in state["folded"]:
            self._advance(state)
        public.append(f"— {self.current_actor(state)} to act.")
        return MoveResult(ok=True, public=public)

    # ----- internals -----
    @staticmethod
    def _parse_amount(m: str):
        for tok in m.replace("raise", " ").replace("bet", " ").split():
            if tok.isdigit():
                return int(tok)
        return None

    def _commit(self, state: dict, actor: str, amount: int) -> None:
        amount = max(0, int(amount))
        state["bets"][actor] += amount
        state["pot"] += amount

    def _mark(self, state: dict, actor: str) -> None:
        if actor not in state["acted"]:
            state["acted"].append(actor)

    def _round_settled(self, state: dict, active: List[str]) -> bool:
        matched = all(state["bets"][p] == state["to_match"] for p in active)
        everyone_acted = all(p in state["acted"] for p in active)
        return matched and everyone_acted

    @staticmethod
    def _hand_total(hand) -> int:
        return sum(hand)

    def _award(self, state: dict, winner: str, public: List[str],
               showdown: bool) -> MoveResult:
        state["over"] = True
        state["winner"] = winner
        if showdown:
            state["revealed"] = True
        public.append(f"🏆 **{winner}** takes the pot of {state['pot']}.")
        return MoveResult(ok=True, public=public)

    def _showdown(self, state: dict, active: List[str],
                  public: List[str]) -> MoveResult:
        state["revealed"] = True
        totals = {p: self._hand_total(state["hands"][p]) for p in active}
        reveal = ", ".join(f"{p}: {state['hands'][p]} ({totals[p]})" for p in active)
        public.append(f"Showdown — {reveal}.")
        best = max(totals.values())
        winners = [p for p in active if totals[p] == best]
        if len(winners) == 1:
            return self._award(state, winners[0], public, showdown=True)
        state["over"] = True
        state["winner"] = None
        public.append(f"🏆 Split pot ({state['pot']}) between {', '.join(winners)}.")
        return MoveResult(ok=True, public=public)

    def is_over(self, state: dict) -> bool:
        return bool(state.get("over"))

    def result(self, state: dict) -> dict:
        active = [p for p in state["order"] if p not in state["folded"]]
        ranking = sorted(active, key=lambda p: self._hand_total(state["hands"][p]),
                         reverse=True)
        return {"winner": state.get("winner"), "ranking": ranking,
                "pot": state.get("pot", 0)}

    def public_view(self, state: dict) -> str:
        rows = []
        for p in state["order"]:
            if p in state["folded"]:
                rows.append(f"{p}: folded")
            else:
                rows.append(f"{p}: in ({state['bets'][p]})")
        return (f"Ante — pot {state['pot']}, {state['to_match']} to stay. "
                + ", ".join(rows) + f". Turn: {self.current_actor(state)}.")

    def private_view(self, state: dict, actor: str) -> str:
        hand = state.get("hands", {}).get(actor)
        if not hand or actor in state.get("folded", []):
            return ""
        return f"Your hand: {hand} (total {self._hand_total(hand)})."
