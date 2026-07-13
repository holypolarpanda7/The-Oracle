"""Offline demo of the economy layer (no network, no DB writes required)."""
from __future__ import annotations

from game_config import get_config, reload_config

from economy import (
    empty_purse,
    add_coins,
    subtract_cost,
    to_cp,
    gp_to_cp,
    format_purse,
    cost_for_days,
    resolve_downtime,
    start_crafting,
    advance_crafting,
)


def main() -> None:
    print("=== Economy demo ===")
    cfg = get_config()
    print(f"Profile: {cfg.profile} | starting gold: {cfg.economy.starting_gold} gp\n")

    # --- purse math ---
    purse = empty_purse()
    purse = add_coins(purse, {"gp": 15, "sp": 8})
    print(f"Purse: {format_purse(purse)}  ({to_cp(purse)} cp)")
    purse = subtract_cost(purse, gp_to_cp(5.5))  # buy a 5 gp 5 sp item
    print(f"After spending 5 gp 5 sp: {format_purse(purse)}\n")

    # --- lifestyle upkeep across difficulty presets ---
    print("Modest lifestyle for 10 days:")
    for profile in ("story", "normal", "hard", "gritty"):
        reload_config()  # reset cache
        from game_config import build_config, set_config

        set_config(build_config(profile))
        cost = cost_for_days("modest", 10)
        print(f"  {profile:7s}: {cost['per_day_gp']:g} gp/day -> {cost['total_gp']:g} gp")
    set_config(build_config("normal"))
    print()

    # --- downtime ---
    dt = resolve_downtime("research", 7, lifestyle_tier="comfortable", extra_cost_gp=10)
    print(dt["summary"])

    # --- crafting a longsword (15 gp) ---
    plan = start_crafting(15)
    print("\n" + plan["summary"])
    step = advance_crafting(target_cost_gp=15, progress_gp=0, days=2)
    print(step["summary"])
    step = advance_crafting(target_cost_gp=15, progress_gp=step["progress_gp"], days=1)
    print(step["summary"])


if __name__ == "__main__":
    main()
