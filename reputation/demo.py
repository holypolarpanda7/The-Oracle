"""Offline demo of the reputation layer."""
from __future__ import annotations

from game_config import build_config, set_config

from reputation import describe_standing, adjust_renown


def main() -> None:
    set_config(build_config("normal"))
    print("=== Reputation demo ===\n")

    renown = 0
    print(describe_standing(renown)["standing"], "->", describe_standing(renown)["perks"])
    for delta in (3, 5, 3, 15, 25):
        res = adjust_renown(renown, delta)
        renown = res["renown"]
        print(f"  +{delta} -> renown {renown} ({res['standing']}): {res['note']}")

    d = describe_standing(renown)
    print(f"\nFinal: {d['standing']} — {d['perks']}")
    print(f"Next: {d['next']}")


if __name__ == "__main__":
    main()
