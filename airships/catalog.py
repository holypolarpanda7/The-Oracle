"""What vessels exist, what stations they carry, and the numbers they run on.

The ENGINE is in this package; the NUMBERS are data. Specific vessels, their
crew stations and the tuning constants for piloting, repairs and crashes all
come from a gitignored ``owned_books/airships_overrides.json``, because those
figures are book-derived and CLAUDE.md keeps book-derived data out of the repo.

What ships here is a single self-authored generic vessel and a plain default
tuning table, so a checkout with no books at all still has a working, playable
airship layer — it just has one unremarkable ship instead of a named fleet.

    from airships import catalog
    catalog.vessel("skiff")          # -> dict, or None
    catalog.tuning()["pilot_dc"]     # -> the untrained-piloting DC
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

OWNED_SOURCE = "Curated override (local, book-derived) — never committed"
GENERIC_SOURCE = "The Oracle (generic)"

_OVERRIDES_FILE = "airships_overrides.json"


#: The one vessel the repo carries: deliberately plain, deliberately ours.
#: A table with no setting book still gets to own and fly something.
_GENERIC_VESSELS: List[Dict] = [
    {
        "slug": "skiff", "name": "Sky Skiff", "source": GENERIC_SOURCE,
        "desc": "A small open-decked flier: two crew, a handful of passengers, "
                "no armament to speak of.",
        "armor_class": 15, "hp": 120, "damage_threshold": 5,
        "speed_mph": 6.0, "fly_speed_ft": 60,
        "crew": 2, "passengers": 4, "cargo_tons": 0.5, "cost_gp": 15000,
        "stations": ["helm"],
    },
]

#: Every vessel has a helm; it is the one station that cannot be removed.
_GENERIC_STATIONS: List[Dict] = [
    {
        "slug": "helm", "name": "Helm", "source": GENERIC_SOURCE,
        "size": "Small", "armor_class": 15, "hp": 20, "seats": 1,
        "actions": ["drive", "shift-engine"],
        "desc": "Wheel, levers and the binding controls. One creature at a time.",
    },
]

#: Baseline tuning. Every value is overridable by the local data file.
_DEFAULT_TUNING: Dict = {
    # Piloting without the mark the ship expects.
    "pilot_dc": 20,
    "pilot_skills": ["arcana", "intimidation", "persuasion"],
    # Emergency repairs underway.
    "repair_dc": 16,
    "repair_hours": 1,
    "repair_parts_gp": 100,
    "repair_dice": "2d4+2",
    "repair_tools": ["carpenter's tools", "smith's tools", "tinker's tools"],
    # Crashing: bludgeoning by the size of what was struck.
    "crash_damage_by_size": {"tiny": "2d10", "small": "2d10", "medium": "2d10",
                             "large": "4d10", "huge": "8d10",
                             "gargantuan": "16d10"},
    "crash_onboard_dc": 10,
    "crash_dodge_dc": 15,
    # A suppressed core still hovers, and crawls.
    "suppressed_fly_speed_ft": 5,
    # Tilting the ship over: everything loose falls.
    "tilt_save_dc": 15,
    # Upgrade caps (each application; cost/time live in the data file).
    "max_ac_upgrades": 5,
    "max_hp_upgrades": 5,
    "hp_per_upgrade": 20,
    "ac_per_upgrade": 1,
}

VESSELS: Dict[str, Dict] = {v["slug"]: v for v in _GENERIC_VESSELS}
STATIONS: Dict[str, Dict] = {s["slug"]: s for s in _GENERIC_STATIONS}
_TUNING: Dict = dict(_DEFAULT_TUNING)


def _owned_books_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "owned_books"


def load_overrides(path: Optional[Path] = None) -> Dict[str, int]:
    """Merge the local vessel/station/tuning data. Missing file is fine.

    Shape: ``{"vessels": [...], "stations": [...], "tuning": {...}}``.
    """
    p = path or (_owned_books_dir() / _OVERRIDES_FILE)
    out = {"vessels": 0, "stations": 0, "tuning": 0}
    try:
        if not p.exists():
            return out
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - a bad local file must not stop boot
        print(f"[airships] overrides skipped: {e}")
        return out
    for v in data.get("vessels") or []:
        if v.get("slug"):
            VESSELS[v["slug"]] = {**v, "source": v.get("source", OWNED_SOURCE)}
            out["vessels"] += 1
    for s in data.get("stations") or []:
        if s.get("slug"):
            STATIONS[s["slug"]] = {**s, "source": s.get("source", OWNED_SOURCE)}
            out["stations"] += 1
    tune = data.get("tuning")
    if isinstance(tune, dict):
        _TUNING.update(tune)
        out["tuning"] = len(tune)
    return out


def tuning() -> Dict:
    return _TUNING


def vessel(slug: str) -> Optional[Dict]:
    return VESSELS.get((slug or "").strip().lower())


def station(slug: str) -> Optional[Dict]:
    return STATIONS.get((slug or "").strip().lower())


def vessels_for_budget(gp: float) -> List[Dict]:
    """Everything a buyer with this much coin could actually afford."""
    return sorted((v for v in VESSELS.values() if (v.get("cost_gp") or 0) <= gp),
                  key=lambda v: v.get("cost_gp") or 0)


def find_vessel(text: str) -> Optional[Dict]:
    """Resolve loose DM language ("a lyrandar cruiser") onto a vessel.

    Scored rather than all-or-nothing, for two reasons found the hard way:
    requiring every word of the name to appear misses "a lyrandar cruiser"
    (the name carries an "air" the DM didn't say), and plain substring matching
    resolves "skyskiff" to a generic "Sky Skiff" whose slug happens to sit
    inside the word. Partial matches count, and the most specific one wins.
    """
    t = (text or "").strip().lower()
    if not t:
        return None
    if t in VESSELS:
        return VESSELS[t]
    for v in VESSELS.values():
        if v["name"].lower() == t:
            return v

    squashed = t.replace("-", "").replace(" ", "")
    best, best_score = None, 0.0
    for v in VESSELS.values():
        name = v["name"].lower()
        words = [w for w in name.split() if len(w) > 2]
        hits = sum(1 for w in words if w in t)
        score = float(hits)
        # A whole name or slug appearing verbatim is worth more than the loose
        # word hits that got us here.
        if name in t or v["slug"] in t:
            score += 2.0
        # ...and so is the run-together spelling people actually type.
        if squashed and name.replace(" ", "") in squashed:
            score += 2.0
        if score > best_score or (score == best_score and best is not None
                                  and len(v["name"]) > len(best["name"])):
            if score > 0:
                best, best_score = v, score
    return best


# Merge the local fleet at import, so every consumer sees one catalog.
load_overrides()
