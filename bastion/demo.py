"""Offline demo of the bastion layer (no network, no DB writes required)."""
from __future__ import annotations

from game_config import build_config, set_config

from bastion import (
    can_own_bastion,
    facilities_for_level,
    facility_cost_gp,
    resolve_bastion_turn,
    min_bastion_level,
)


def main() -> None:
    set_config(build_config("normal"))
    print("=== Bastion demo ===")
    print(f"Minimum level to own a bastion: {min_bastion_level()}")
    print(f"Can a level 3 character own one? {can_own_bastion(3)}")
    print(f"Can a level 5 character own one? {can_own_bastion(5)}\n")

    print("Special facilities available at level 9:")
    for f in facilities_for_level(9):
        print(f"  - {f['name']:22s} (L{f['min_level']}, {f['space']})")
    print(f"\nCost to add a special facility: {facility_cost_gp('gaming-hall'):g} gp\n")

    # Issue a turn with a couple of facilities under orders.
    facilities = [
        {"facility_slug": "gaming-hall", "current_order": "Trade (patrons)"},
        {"facility_slug": "smithy", "current_order": "Craft (weapon/armor)"},
        {"facility_slug": "library", "current_order": "Research"},
    ]
    result = resolve_bastion_turn(facilities, world_day=100, turn_number=1)
    print(result["summary"])
    for ev in result["events"]:
        print(f"  * {ev['description']}")

    # Difficulty scaling: hard preset raises costs / lowers income.
    print("\n-- hard preset --")
    set_config(build_config("hard"))
    print(f"Facility cost: {facility_cost_gp('gaming-hall'):g} gp")
    result = resolve_bastion_turn(facilities, world_day=100, turn_number=2)
    print(result["summary"])


if __name__ == "__main__":
    main()
