"""The Oracle airship layer: flying vessels, their crew stations, and passages.

Three pieces, in the project's usual split:

  ``catalog``  — what vessels and stations exist, and the numbers they run on.
                 Book-specific fleets load from a gitignored local data file;
                 a plain self-authored vessel ships in the repo so a checkout
                 with no books still flies.
  ``flight``   — the engine: the elemental core, the helm, damage, repairs,
                 crashes, upgrades.
  ``journey``  — passages: hours, days, and what the sky does on the way.

A vessel is also a PLACE in the world graph (``Airship.place_slug``). That is
what makes the rest of the game work aboard it for free — the party is
``located_in`` the ship, arrival art draws its deck, and moving the ship moves
everyone standing on it without the world layer learning anything new.

The tactical layer needs nothing new either: ``vtt`` already generates
``skyship`` and ``sky-islands`` boards in the ``fly`` medium.
"""
from .models import Airship, CrewStation, CoreState, get_engine
from .catalog import (
    VESSELS,
    STATIONS,
    vessel,
    station,
    find_vessel,
    vessels_for_budget,
    tuning,
    load_overrides,
)
from .flight import (
    Outcome,
    build_airship,
    install_station,
    stations_of,
    helm_of,
    suppress_core,
    engage_core,
    break_core,
    effective_fly_speed,
    wind_wards_up,
    pilot_check,
    drive,
    tilt,
    damage_ship,
    damage_station,
    emergency_repair,
    dock,
    crash,
    upgrade,
    summary,
    render,
)
from .journey import Journey, Leg, fly, eta, describe, hazards, HOURS_PER_DAY

__all__ = [
    "Airship", "CrewStation", "CoreState", "get_engine",
    "VESSELS", "STATIONS", "vessel", "station", "find_vessel",
    "vessels_for_budget", "tuning", "load_overrides",
    "Outcome", "build_airship", "install_station", "stations_of", "helm_of",
    "suppress_core", "engage_core", "break_core", "effective_fly_speed",
    "wind_wards_up", "pilot_check", "drive", "tilt", "damage_ship",
    "damage_station", "emergency_repair", "dock", "crash", "upgrade",
    "summary", "render",
    "Journey", "Leg", "fly", "eta", "describe", "hazards", "HOURS_PER_DAY",
]
