"""
Show the active game config and how difficulty presets change the knobs.

Run:  uv run python -m game_config.demo
"""
from __future__ import annotations

from game_config import build_config, get_config


def main() -> None:
    print("Active config profile:", get_config().profile)
    print()
    for name in ("story", "normal", "hard", "gritty"):
        c = build_config(name)
        print(
            f"{name:7} | xp x{c.progression.xp_multiplier:<4} "
            f"item x{c.economy.item_cost_multiplier:<4} "
            f"sell {c.economy.sell_price_ratio:<4} "
            f"lifestyle x{c.economy.lifestyle_cost_multiplier:<4} "
            f"craft x{c.crafting.progress_multiplier:<4} "
            f"bastion x{c.bastion.cost_multiplier:<4} rest={c.rest.variant}"
        )


if __name__ == "__main__":
    main()
