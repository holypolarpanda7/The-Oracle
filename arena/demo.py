"""Runnable demo: roster a fight in every environment, at a few levels.

    uv run python -m arena.demo
    uv run python -m arena.demo 12 deadly

Reads the rules database for stat blocks (run ``uv run python -m rules.demo``
first if it's empty) and prints what the Grounds would field.
"""
from __future__ import annotations

import os
import random
import sys

from sqlmodel import create_engine

from . import (DIFFICULTIES, MAX_SLOTS, build_roster, environments_by_domain,
               load_cards)

DB = os.getenv("DATABASE_URL", "sqlite:///oracle-dm-backend/oracle.db")


def main() -> int:
    level = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    difficulty = sys.argv[2] if len(sys.argv) > 2 else "medium"
    if difficulty not in DIFFICULTIES:
        print(f"difficulty must be one of {', '.join(DIFFICULTIES)}")
        return 2

    engine = create_engine(DB)
    cards = load_cards(engine)
    print(f"The Proving Grounds — level {level}, {difficulty}, "
          f"{MAX_SLOTS} character slots")
    print(f"{len(cards)} rosterable stat blocks in {DB}\n")
    if not cards:
        print("No monsters in the rules database — run: uv run python -m rules.demo")
        return 1

    rng = random.Random(20260728)
    for domain, envs in environments_by_domain().items():
        print(f"\033[1m{domain.upper()}\033[0m")
        for env in envs:
            roster = build_roster(env, level, cards, difficulty=difficulty, rng=rng)
            if roster is None:
                print(f"  {env.name:24} — nothing to field")
                continue
            mark = " (conjured)" if roster.conjured else ""
            print(f"  {env.name:24} [{env.mode:4}] {roster.summary()}{mark}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
