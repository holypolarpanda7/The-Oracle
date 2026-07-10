"""Offline demo of the hazards layer."""
from __future__ import annotations

import random

from game_config import build_config, set_config

from hazards import (
    contract_disease, disease_recovery_check, trap_detect, trap_disarm,
    roll_madness, list_diseases, list_traps,
)


def main() -> None:
    set_config(build_config("normal"))
    rng = random.Random(42)
    print("=== Hazards demo ===\n")

    print(f"Diseases catalogued: {[d['name'] for d in list_diseases()]}")
    print(f"Traps catalogued:    {[t['name'] for t in list_traps()]}\n")

    # --- disease ---
    dz = contract_disease("sewer-plague", world_day=10)
    print(f"Contracted {dz['name']} (DC {dz['save_dc']} {dz['ability']}), onset day {dz['onset_day']}")
    print(f"  Effect: {dz['effect']}")
    r1 = disease_recovery_check("sewer-plague", save_succeeded=True)
    print(f"  Save 1 (success): {r1['note']}")
    r2 = disease_recovery_check("sewer-plague", save_succeeded=True,
                                consecutive_successes=r1['consecutive_successes'])
    print(f"  Save 2 (success): {r2['note']}\n")

    # --- trap ---
    det = trap_detect("poison-darts", passive_perception=14)
    print(f"{det['trap']}: detect DC {det['detect_dc']} -> noticed={det['noticed']}")
    dis = trap_disarm("poison-darts", check_total=16)
    print(f"  Disarm DC {dis['disarm_dc']} with 16 -> {dis['note']}\n")

    # --- madness ---
    for sev in ("short", "long", "indefinite"):
        m = roll_madness(sev, rng=rng)
        print(f"{sev.title()} madness: {m['duration']} — {m['effect']}")


if __name__ == "__main__":
    main()
