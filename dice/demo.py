"""
Runnable demo + sanity checks for the dice roller.

    uv run python -m dice.demo

Uses a seeded RNG so output is deterministic and a few invariants are asserted.
"""
from __future__ import annotations

import random

from .roller import roll, double_dice
from .mechanics import ability_check, attack_roll, damage_roll


def _rule(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def main() -> None:
    rng = random.Random(42)

    _rule("Basic rolls")
    for expr in ["2d6+3", "1d20+5", "4d6kh3", "1d8+1d6+2", "d20"]:
        print(roll(expr, rng=rng))

    _rule("Critical damage doubling")
    print("1d6+2 ->", double_dice("1d6+2"))
    print(damage_roll("1d6+2", crit=True, rng=rng))

    _rule("Ability check (Stealth +5, DC 15, advantage)")
    print(ability_check(5, dc=15, advantage=True, label="Stealth", rng=rng))

    _rule("Attack roll (+4 vs AC 13) then damage")
    atk = attack_roll(4, target_ac=13, label="Scimitar", rng=rng)
    print(atk)
    if atk.hit:
        print(damage_roll("1d6+2", crit=atk.is_crit, rng=rng))

    _rule("Invariants")
    r = random.Random(1)
    # 4d6kh3 keeps 3 dice, drops 1
    res = roll("4d6kh3", rng=r)
    assert len(res.rolls) == 3 and len(res.dropped) == 1, res
    # crit doubles dice count
    assert double_dice("2d8+1d6+3") == "4d8+2d6+3"
    # advantage never lowers the kept die below a single roll's minimum
    for _ in range(200):
        c = ability_check(0, advantage=True, rng=r)
        assert 1 <= c.natural <= 20
    print("All invariants passed.")


if __name__ == "__main__":
    main()
