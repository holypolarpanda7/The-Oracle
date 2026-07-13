"""Hazard resolution: disease contraction, trap checks, and madness rolls.

Pure logic returning dicts; the backend persists ``Affliction`` rows and advances
the world calendar. Save DCs default to ``config.hazard`` unless the catalog entry
overrides them.
"""
from __future__ import annotations

import random
from typing import Dict, Optional

from game_config import get_config

from .catalog import get_disease, get_trap, MADNESS_TABLES


def disease_save_dc(slug: str) -> int:
    d = get_disease(slug)
    if d and "dc" in d:
        return int(d["dc"])
    return get_config().hazard.disease_save_dc_default


def contract_disease(slug: str, *, world_day: int = 0) -> Dict:
    """Describe a newly-contracted disease (backend creates the Affliction row)."""
    d = get_disease(slug)
    if not d:
        return {"error": f"Unknown disease '{slug}'."}
    return {
        "kind": "disease",
        "slug": slug,
        "name": d["name"],
        "save_dc": disease_save_dc(slug),
        "ability": d.get("save", "constitution"),
        "onset": d.get("onset"),
        "effect": d["effect"],
        "onset_day": world_day + int(d.get("incubation_days", 0)),
        "description": f"{d['name']}: {d['effect']}",
    }


def disease_recovery_check(slug: str, *, save_succeeded: bool,
                           consecutive_successes: int = 0) -> Dict:
    """Two consecutive successful saves shakes most diseases; a failure resets."""
    if not get_disease(slug):
        return {"error": f"Unknown disease '{slug}'."}
    if save_succeeded:
        successes = consecutive_successes + 1
        cured = successes >= 2
        return {"consecutive_successes": successes, "cured": cured,
                "note": "Cured!" if cured else "One more successful save to recover."}
    return {"consecutive_successes": 0, "cured": False,
            "note": "Failed save — the disease worsens (see its effect)."}


def trap_detect(slug: str, passive_perception: int) -> Dict:
    """Whether passive Perception notices a trap before it triggers."""
    t = get_trap(slug)
    if not t:
        return {"error": f"Unknown trap '{slug}'."}
    dc = t.get("detect_dc", get_config().hazard.trap_detect_dc_default)
    return {
        "trap": t["name"], "detect_dc": dc,
        "noticed": passive_perception >= dc,
        "trigger": t["trigger"], "effect": t["effect"],
        "disarm_dc": t.get("disarm_dc", get_config().hazard.trap_disarm_dc_default),
    }


def trap_disarm(slug: str, *, check_total: int) -> Dict:
    t = get_trap(slug)
    if not t:
        return {"error": f"Unknown trap '{slug}'."}
    dc = t.get("disarm_dc", get_config().hazard.trap_disarm_dc_default)
    ok = check_total >= dc
    return {"trap": t["name"], "disarm_dc": dc, "disarmed": ok,
            "note": "Trap disarmed." if ok else "Failed — the trap may trigger!"}


def roll_madness(severity: str = "short", *, rng: Optional[random.Random] = None) -> Dict:
    """Roll a random madness effect of the given severity."""
    if not get_config().hazard.madness_enabled:
        return {"enabled": False, "note": "Madness is disabled in the current config."}
    severity = severity if severity in MADNESS_TABLES else "short"
    rng = rng or random.Random()
    table = MADNESS_TABLES[severity]
    effect = rng.choice(table)
    if severity == "short":
        duration = f"{rng.randint(1, 10)} minutes"
    elif severity == "long":
        duration = f"{rng.randint(1, 10) * 10} hours"
    else:
        duration = "indefinite (until cured)"
    return {
        "enabled": True,
        "kind": "madness",
        "severity": severity,
        "effect": effect,
        "duration": duration,
        "description": f"{severity.title()} madness ({duration}): {effect}",
    }
