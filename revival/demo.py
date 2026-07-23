"""Smoke test for the revival spell resolver.

Run: ``uv run python -m revival.demo``
"""
from __future__ import annotations

from . import get_spell, resolve


def main() -> None:
    print("=" * 60)
    print("REVIVAL SPELLS — HP, penalty, willing-soul (DNR gate)")
    print("=" * 60)
    for ref in ("Revivify", "Raise Dead", "Resurrection", "True Resurrection",
                "Reincarnate", "some homebrew rite"):
        s = get_spell(ref)
        print(f"{ref:20s} -> {s.name:18s} hp={s.hp_rule:4s} "
              f"penalty={s.penalty} willing={s.needs_willing} reinc={s.reincarnate}")

    print("\n" + "=" * 60)
    print("RESOLVE against a level-appropriate PC (max_hp=40)")
    print("=" * 60)

    # Revivify: 1 HP, no penalty, and NOT willing-gated → a DNR can't stop it.
    p = resolve("Revivify", dnr=False, max_hp=40)
    print(f"\nRevivify (no DNR): hp={p.hp} penalty={p.penalty} "
          f"refuses={p.refuses} anger={p.forced_anger}")
    assert p.hp == 1 and p.penalty is None and not p.refuses and not p.forced_anger

    p = resolve("Revivify", dnr=True, max_hp=40)
    print(f"Revivify (DNR):    refuses={p.refuses} forced_anger={p.forced_anger}")
    assert not p.refuses and p.forced_anger, "no-consent spell forces the DNR soul back, angry"

    # Raise Dead: 1 HP, -4/4-day penalty, willing-gated → a DNR refuses (stays dead).
    p = resolve("Raise Dead", dnr=False, max_hp=40)
    print(f"\nRaise Dead (no DNR): hp={p.hp} penalty={p.penalty}")
    assert p.hp == 1 and p.penalty == (4, 4)

    p = resolve("Raise Dead", dnr=True, max_hp=40)
    print(f"Raise Dead (DNR):    refuses={p.refuses} (soul turns away)")
    assert p.refuses and not p.forced_anger

    # True Resurrection: full HP, no penalty.
    p = resolve("True Resurrection", dnr=False, max_hp=40)
    print(f"\nTrue Resurrection: hp={p.hp} penalty={p.penalty}")
    assert p.hp == 40 and p.penalty is None

    # Reincarnate: new body, willing-gated.
    p = resolve("Reincarnate", dnr=False, max_hp=40)
    print(f"Reincarnate: hp={p.hp} reincarnate={p.reincarnate}")
    assert p.reincarnate

    print("\n" + "=" * 60)
    print("ALL REVIVAL CHECKS PASSED ✅")
    print("=" * 60)


if __name__ == "__main__":
    main()
