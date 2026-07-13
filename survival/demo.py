"""Offline demo of the survival layer."""
from __future__ import annotations

from game_config import build_config, set_config

from survival import (
    consume_day, describe_exhaustion, short_rest, long_rest, encumbrance_status,
    generate_weather, hazards_from_weather, resolve_hazard, travel, navigation_dc,
    forage, source_spec, burn, effective_vision,
)


def main() -> None:
    set_config(build_config("normal"))
    print("=== Survival demo (normal) ===\n")

    # --- provisions running out ---
    print("Days on the road with only 2 rations / 1 water:")
    state = {"rations": 2, "water": 1, "days_without_food": 0,
             "days_without_water": 0, "exhaustion": 0}
    for day in range(1, 7):
        state = consume_day(**state)
        print(f"  Day {day}: {state['summary']}")
        state = {k: state[k] for k in
                 ("rations", "water", "days_without_food", "days_without_water", "exhaustion")}
    print("  " + describe_exhaustion(state["exhaustion"]) + "\n")

    # --- rest ---
    sr = short_rest(current_hp=12, max_hp=30, hit_die="d10",
                    hit_dice_remaining=3, con_mod=2, spend=2)
    print(f"Short rest: {sr['note']}")
    lr = long_rest(current_hp=sr["current_hp"], max_hp=30, hit_dice_total=5,
                   hit_dice_remaining=sr["hit_dice_remaining"], exhaustion=2,
                   ate_and_drank=True)
    print(f"Long rest: {lr['note']}\n")

    # --- encumbrance (variant on via hard preset) ---
    set_config(build_config("hard"))
    enc = encumbrance_status(14, 80)
    print(f"Encumbrance (Str 14, 80 lb): {enc['status']} — {enc['note']}\n")

    # --- weather + hazards (arctic winter) ---
    set_config(build_config("gritty"))
    print("Arctic winter week (gritty preset):")
    for day in range(300, 305):
        w = generate_weather(day, climate="arctic", month=1)
        hz = hazards_from_weather(w, has_cold_gear=False)
        tags = ", ".join(h["hazard"] for h in hz) or "none"
        print(f"  Day {day}: {w['summary']} | hazards: {tags}")
    # resolve one failed cold save
    cold = [h for h in hazards_from_weather(generate_weather(300, climate='arctic', month=1))
            if h["hazard"] == "extreme_cold"]
    if cold:
        out = resolve_hazard(cold[0], save_succeeded=False)
        print(f"  Failed cold save -> {out['note']}\n")

    # --- travel / navigation / forage ---
    set_config(build_config("normal"))
    t = travel(24, pace="normal", terrain="forest")
    print(f"Travel: {t['summary']}")
    print(f"Navigation: DC {navigation_dc('forest')['dc']} Survival in forest")
    f = forage("forest", foragers=2)
    print(f"Forage: DC {f['dc']} -> {f['note']}\n")

    # --- light ---
    spec = source_spec("torch")
    print(f"Torch: {spec['bright_radius']}/{spec['dim_radius']} ft, {spec['minutes']} min")
    b = burn("torch", spec["minutes"], 50)
    print(f"  After 50 min: {b['note']}")
    print(f"  Vision in the dark (no darkvision): {effective_vision('dark')['sees']}")
    print(f"  Vision in the dark (darkvision): {effective_vision('dark', has_darkvision=True)['sees']}")


if __name__ == "__main__":
    main()
