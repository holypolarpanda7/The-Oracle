"""End-to-end demo / smoke test for the games subsystem.

Run: ``uv run python -m games.demo``

Drives each engine through a full game (friendly and wagered), then exercises the
cheat + suspicion layers: a quiet cheat that slips by, an obvious spell-component
cheat that's caught cold, and a winning streak that raises the table's heat and
stiffens the DC for the next cheat. Asserts along the way so a regression is loud.
"""
from __future__ import annotations

import random

from . import suspicion
from .catalog import get_engine, list_games
from .cheat import adjudicate
from .models import CheatAttempt, CheatOutcome, Detectability, SpellComponents


def _liar_move(engine, state, _actor):
    bid = state.get("bid")
    total = sum(state["counts"][p] for p in state["order"] if p not in state["out"])
    if bid is None:
        return "bid 1 2"
    if bid["qty"] >= total:            # can't be more dice than exist → it's a lie
        return "challenge"
    if bid["face"] < 6:
        return f"bid {bid['qty']} {bid['face'] + 1}"
    return f"bid {bid['qty'] + 1} 2"


def _grid_move(engine, state, actor):
    return engine.legal_moves(state, actor)[0]  # first open column


def _ante_move(engine, state, actor):
    owed = state["to_match"] - state["bets"][actor]
    return "call" if owed > 0 else "check"


_STRATS = {"liars_dice": _liar_move, "grid_dice": _grid_move, "card_bet": _ante_move}


def _play_out(game_id: str, wager: int, seed: int) -> dict:
    engine = get_engine(game_id)
    rng = random.Random(seed)
    state = engine.start(["Kael", "Mira"], rng, wager=wager)
    strat = _STRATS[game_id]
    guard = 0
    while not engine.is_over(state):
        guard += 1
        assert guard < 500, f"{game_id} did not terminate"
        actor = engine.current_actor(state)
        res = engine.apply_move(state, actor, strat(engine, state, actor), rng)
        assert res.ok, f"illegal move in {game_id}: {res.error}"
    return state


def demo_engines() -> None:
    print("=" * 66)
    print("ENGINES — full games, fair server-side RNG")
    print("=" * 66)
    for spec in list_games():
        engine = get_engine(spec.id)
        # Friendly (no stakes) and wagered both must complete.
        friendly = _play_out(spec.id, wager=0, seed=7)
        wagered = _play_out(spec.id, wager=10, seed=7)
        res = engine.result(wagered)
        print(f"\n{spec.name} ({spec.id}) — {spec.blurb}")
        print(f"  friendly game → winner={engine.result(friendly)['winner']} "
              f"(wager=0, no coin moved)")
        print(f"  wagered game  → {engine.public_view(wagered)}")
        print(f"  result: {res}")
        # Private views really are private (one player's hidden info only).
        p0, p1 = wagered["order"]
        pv0 = engine.private_view(_play_out(spec.id, 10, 3), p0)
        print(f"  {p0}'s private view sample: {pv0 or '(none — public board)'}")
        assert engine.is_over(wagered)


def demo_cheating() -> None:
    print("\n" + "=" * 66)
    print("CHEATING — quiet slips by, obvious spells get caught")
    print("=" * 66)
    susp = suspicion.new_suspicion()

    # 1) A quiet, well-rolled palm at a cool table → clean.
    r1 = adjudicate(CheatAttempt("palm a loaded die", Detectability.SUBTLE, 22),
                    susp["heat"])
    print(f"\n1. Subtle palm, rolled 22, heat 0 → {r1.outcome.value} "
          f"(DC {r1.effective_dc})")
    print(f"   table sees: {r1.public or '(nothing)'}")
    print(f"   whispered : {r1.private}")
    assert r1.outcome is CheatOutcome.CLEAN and r1.outcome.succeeded

    # 2) A spell with verbal + somatic components → seen, caught regardless of roll.
    spell = SpellComponents(verbal=True, somatic=True)
    r2 = adjudicate(CheatAttempt("read a rival's mind", Detectability.SUBTLE, 30,
                                 spell=spell), susp["heat"])
    print(f"\n2. Detect Thoughts (V,S) mid-game, rolled 30 → {r2.outcome.value}")
    print(f"   table sees: {r2.public}")
    print(f"   reason    : {r2.reason}")
    assert r2.outcome is CheatOutcome.CAUGHT and not r2.outcome.succeeded

    # 3) The SAME spell cast subtly (no perceptible components) → judged on the roll.
    subtle = SpellComponents(verbal=True, somatic=True, subtle=True)
    r3 = adjudicate(CheatAttempt("read a rival's mind (subtle)", Detectability.RISKY,
                                 25, spell=subtle), susp["heat"])
    print(f"\n3. Same spell cast SUBTLY, rolled 25 → {r3.outcome.value} "
          f"(DC {r3.effective_dc})")
    assert r3.outcome.succeeded


def demo_suspicion() -> None:
    print("\n" + "=" * 66)
    print("SUSPICION — a winning streak heats the table and stiffens the DC")
    print("=" * 66)
    susp = suspicion.new_suspicion()
    base = adjudicate(CheatAttempt("swap a card", Detectability.RISKY, 18),
                      susp["heat"])
    print(f"\nCold table: cheat DC = {base.effective_dc}, heat = {susp['heat']}")
    for i in range(1, 6):
        added = suspicion.record_win(susp, "Kael", improbable=(i >= 3))
        print(f"  Kael wins hand {i} (improbable={i >= 3}) → +{added} heat, "
              f"now {susp['heat']}: {suspicion.describe(susp['heat'])}")
    hot = adjudicate(CheatAttempt("swap a card", Detectability.RISKY, 18),
                     susp["heat"])
    print(f"\nHot table: same cheat, same roll (18) → {hot.outcome.value}, "
          f"DC rose {base.effective_dc} → {hot.effective_dc}")
    assert hot.effective_dc > base.effective_dc, "suspicion must raise the DC"
    assert suspicion.dc_modifier(susp["heat"]) > 0


def main() -> None:
    demo_engines()
    demo_cheating()
    demo_suspicion()
    print("\n" + "=" * 66)
    print("ALL GAMES-SUBSYSTEM CHECKS PASSED ✅")
    print("=" * 66)


if __name__ == "__main__":
    main()
