"""Offline demo for the dm_guide toolkit: guidance text, DC ladder, encounters."""
from __future__ import annotations

from game_config import build_config, set_config
from dm_guide import (
    brief_guidance,
    dc_scale,
    suggest_dc,
    estimate_encounter,
    build_encounter,
)


def _line(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def main() -> None:
    _line("DM guidance (brief, injected into the prompt)")
    print(brief_guidance())

    for profile in ("story", "normal", "gritty"):
        set_config(build_config(profile))
        _line(f"DC ladder — profile: {profile}")
        for row in dc_scale():
            print(f"  {row['difficulty']:<18} DC {row['dc']:>2}  (base {row['base_dc']})")
        print("  single check 'hard':", suggest_dc("hard"))

    set_config(build_config("normal"))
    _line("Encounter estimates — party of four level-5 (normal)")
    party = [5, 5, 5, 5]
    scenarios = {
        "one CR-3 brute (700 xp)": [700],
        "four CR-1 (200 xp each)": [200, 200, 200, 200],
        "eight CR-1/2 (100 xp each)": [100] * 8,
    }
    for label, xps in scenarios.items():
        est = estimate_encounter(party, xps)
        print(f"  {label:<32} raw {est['raw_xp']:>5}  "
              f"x{est['multiplier']}  adj {est['adjusted_xp']:>5}  -> {est['difficulty']}")

    _line("Budget planner — party of four level-5, target 'hard'")
    print(" ", build_encounter(party, "hard"))


if __name__ == "__main__":
    main()
