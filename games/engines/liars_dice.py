"""Liar's Dice (a.k.a. Perudo) — a public-domain folk bluffing game.

Each player keeps a cup of hidden dice. On your turn you either raise the standing
bid — a claim about how many of a face-value show across *everyone's* dice — or
call the previous bidder a liar. On a challenge every cup lifts: if the table holds
at least the claimed amount the challenger loses a die, otherwise the bidder does.
Ones are wild and count as any face. Lose your last die and you're out; last cup
standing wins. Hidden dice make this the private-info showcase — your cup is only
ever rendered to you.
"""
from __future__ import annotations

import random
import re
from typing import List

from ..models import MoveResult
from .base import GameEngine

_START_DICE = 5
_DIE_FACES = 6


class LiarsDice(GameEngine):
    id = "liars_dice"
    name = "Liar's Dice"
    min_players = 2
    max_players = 6

    # ----- lifecycle -----
    def start(self, players: List[str], rng: random.Random, *, wager: int = 0) -> dict:
        order = list(players)
        state = {
            "game": self.id,
            "order": order,
            "turn": 0,
            "counts": {p: _START_DICE for p in order},
            "dice": {},
            "bid": None,
            "out": [],
            "round": 1,
            "wager": int(wager),
            "over": False,
            "winner": None,
        }
        self._roll_all(state, rng)
        return state

    # ----- moves -----
    def legal_moves(self, state: dict, actor: str) -> List[str]:
        if state.get("over") or self.current_actor(state) != actor:
            return []
        moves = ["bid <count> <face 1-6>  (must raise the standing bid)"]
        if state.get("bid"):
            moves.append("challenge  (call the last bid a lie)")
        return moves

    def apply_move(self, state: dict, actor: str, move: str,
                   rng: random.Random) -> MoveResult:
        if state.get("over"):
            return MoveResult.illegal("the game is already over")
        if self.current_actor(state) != actor:
            return MoveResult.illegal(f"it is not {actor}'s turn")
        m = (move or "").strip().lower()
        if re.match(r"^(challenge|call|liar|bluff)", m):
            return self._challenge(state, actor, rng)
        bid = self._parse_bid(m)
        if not bid:
            return MoveResult.illegal(
                "say 'bid <count> <face>' (e.g. 'bid 3 5') or 'challenge'")
        return self._bid(state, actor, bid)

    # ----- internals -----
    @staticmethod
    def _parse_bid(m: str):
        mt = re.search(r"(?:bid|raise|call it|say)?\s*(\d+)\s*(?:x|of)?\s*(\d)", m)
        if not mt:
            return None
        qty, face = int(mt.group(1)), int(mt.group(2))
        if qty < 1 or not (1 <= face <= _DIE_FACES):
            return None
        return {"qty": qty, "face": face}

    @staticmethod
    def _outbids(new: dict, cur: dict) -> bool:
        if cur is None:
            return True
        if new["qty"] != cur["qty"]:
            return new["qty"] > cur["qty"]
        return new["face"] > cur["face"]

    def _bid(self, state: dict, actor: str, bid: dict) -> MoveResult:
        cur = state.get("bid")
        if not self._outbids(bid, cur):
            return MoveResult.illegal(
                "a bid must raise the standing one (more dice, or the same count "
                "at a higher face)")
        bid = {**bid, "by": actor}
        state["bid"] = bid
        self._advance(state)
        return MoveResult(ok=True, public=[
            f"{actor} bids **{bid['qty']} × {bid['face']}s**."])

    def _tally(self, state: dict, face: int) -> int:
        total = 0
        for dice in state["dice"].values():
            for d in dice:
                if d == face or (face != 1 and d == 1):  # ones are wild
                    total += 1
        return total

    def _challenge(self, state: dict, actor: str, rng: random.Random) -> MoveResult:
        bid = state.get("bid")
        if not bid:
            return MoveResult.illegal("there is no bid to challenge yet")
        bidder = bid["by"]
        face, qty = bid["face"], bid["qty"]
        actual = self._tally(state, face)
        reveal = ", ".join(f"{p}: {sorted(state['dice'][p])}"
                           for p in state["order"] if p not in state["out"])
        if actual >= qty:                     # the bid held up
            loser, verdict = actor, f"the table shows {actual} — the bid was true"
        else:                                  # the bid was a lie
            loser, verdict = bidder, f"the table shows only {actual} — a lie"
        public = [
            f"{actor} calls **liar** on {bidder}'s {qty} × {face}s!",
            f"Cups lift — {reveal}.",
            f"{verdict}; **{loser}** loses a die.",
        ]
        state["counts"][loser] -= 1
        if state["counts"][loser] <= 0:
            state["out"].append(loser)
            public.append(f"**{loser}** is out of the game.")
        # Winner check.
        alive = [p for p in state["order"] if p not in state["out"]]
        if len(alive) <= 1:
            state["over"] = True
            state["winner"] = alive[0] if alive else None
            public.append(f"🏆 **{state['winner']}** takes the game.")
            return MoveResult(ok=True, public=public)
        # New round: loser (if still in) leads, else the next live player.
        state["round"] += 1
        state["bid"] = None
        lead = loser if loser not in state["out"] else alive[0]
        state["turn"] = state["order"].index(lead)
        if lead in state["out"]:
            self._advance(state)
        self._roll_all(state, rng)
        public.append(f"— Round {state['round']} — new dice all around; "
                      f"{self.current_actor(state)} leads.")
        return MoveResult(ok=True, public=public)

    def _roll_all(self, state: dict, rng: random.Random) -> None:
        state["dice"] = {
            p: sorted(rng.randint(1, _DIE_FACES) for _ in range(state["counts"][p]))
            for p in state["order"] if p not in state["out"]
        }

    # ----- status/views -----
    def is_over(self, state: dict) -> bool:
        return bool(state.get("over"))

    def result(self, state: dict) -> dict:
        ranking = sorted(state["order"], key=lambda p: state["counts"].get(p, 0),
                         reverse=True)
        return {"winner": state.get("winner"), "ranking": ranking}

    def public_view(self, state: dict) -> str:
        bid = state.get("bid")
        bid_s = (f"{bid['qty']} × {bid['face']}s (by {bid['by']})" if bid
                 else "no bid yet")
        cups = ", ".join(f"{p}: {state['counts'][p]} dice"
                         for p in state["order"] if p not in state["out"])
        return (f"Liar's Dice — round {state['round']}. Cups: {cups}. "
                f"Standing bid: {bid_s}. Turn: {self.current_actor(state)}.")

    def private_view(self, state: dict, actor: str) -> str:
        dice = state.get("dice", {}).get(actor)
        if not dice:
            return ""
        return f"Your cup holds: {dice} (ones are wild)."
