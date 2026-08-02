"""Getting somewhere by air — legs, hours, and what goes wrong on the way.

Overland travel already exists (``survival/travel.py``) and the world already
has real coordinates and roads (``eight_card_system/mapmaker.py``,
``survival/travel``). This module is the AIR version of the same question, and
it deliberately answers it the same way the routes system does: in hours and
days and what might happen, never in bearings or a rendered path.

Flying changes three things, and only three:

* you go in a straight line, so distance is the great-circle one rather than a
  road's;
* terrain underneath stops mattering for pace — but weather aloft starts to;
* a leg can be interrupted by things that only happen in the air.

Hazards are rolled from a table that lives in the local data file when the
table owns a book, and falls back to a small self-authored set otherwise.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from . import catalog

#: Hours a crew can fly in a day before they must set down or trade shifts.
HOURS_PER_DAY = 8

#: Self-authored fallback hazards, so a bookless checkout still has an
#: interesting sky. Each: (weight, tag, what the DM is handed).
_GENERIC_HAZARDS = [
    (30, "clear", "Nothing but wind and cloud shadow on the country below."),
    (14, "weather", "A squall line closes in; the deck pitches and the wards sing."),
    (12, "traffic", "Another vessel on a converging heading, colours not yet legible."),
    (10, "wildlife", "Something large is pacing the ship, just out of bowshot."),
    (8, "navigation", "The landmarks stop matching the chart."),
    (8, "mechanical", "A binding strut shears; the ring stutters."),
    (6, "cold", "The air thins and bites — altitude is telling on the crew."),
    (6, "sighting", "Smoke on the horizon, from somewhere that should be quiet."),
    (6, "becalmed", "The elemental sulks; way falls off her for an hour."),
]


@dataclass
class Leg:
    """One day's flying, and whatever the sky did about it."""
    day: int
    hours: float
    miles: float
    hazard_tag: str = ""
    hazard: str = ""
    arrived: bool = False


@dataclass
class Journey:
    """A whole passage: how far, how long, and the day-by-day."""
    miles: float
    speed_mph: float
    hours_per_day: float
    days: int = 0
    legs: list[Leg] = field(default_factory=list)
    arrived: bool = False

    @property
    def hours_total(self) -> float:
        return sum(l.hours for l in self.legs)

    def summary(self) -> str:
        d = self.days
        pace = f"{self.speed_mph:g} mph, {self.hours_per_day:g} h/day"
        head = (f"{self.miles:.0f} miles by air — about "
                f"{'a day' if d <= 1 else f'{d} days'} ({pace})")
        if not self.arrived:
            head += " — passage broken off before arrival"
        return head


def hazards() -> list[tuple]:
    """The hazard table: local data if present, else the generic set."""
    table = catalog.tuning().get("sky_hazards")
    if isinstance(table, list) and table:
        out = []
        for row in table:
            try:
                out.append((int(row.get("weight", 1)), str(row.get("tag", "")),
                            str(row.get("text", ""))))
            except Exception:
                continue
        if out:
            return out
    return _GENERIC_HAZARDS


def _roll_hazard(rng: random.Random) -> tuple[str, str]:
    table = hazards()
    total = sum(w for w, _, _ in table)
    pick = rng.uniform(0, total)
    acc = 0.0
    for w, tag, text in table:
        acc += w
        if pick <= acc:
            return tag, text
    return table[-1][1], table[-1][2]


def fly(miles: float, *, speed_mph: float, hours_per_day: float = HOURS_PER_DAY,
        seed: str = "", max_days: int = 60,
        stop_on: Optional[set] = None) -> Journey:
    """Plan and roll a passage. Deterministic for a given ``seed``.

    ``stop_on`` names hazard tags that BREAK the journey — the DM wants to play
    that moment out rather than have the trip narrate over it. The leg where it
    happens is the last one, and ``arrived`` stays False.
    """
    speed_mph = max(0.1, float(speed_mph))
    hours_per_day = max(0.5, float(hours_per_day))
    miles = max(0.0, float(miles))
    rng = random.Random(f"sky:{seed}")
    j = Journey(miles=miles, speed_mph=speed_mph, hours_per_day=hours_per_day)

    remaining = miles
    day = 0
    per_day = speed_mph * hours_per_day
    while remaining > 0 and day < max_days:
        day += 1
        todays = min(remaining, per_day)
        hours = todays / speed_mph
        tag, text = _roll_hazard(rng)
        leg = Leg(day=day, hours=round(hours, 2), miles=round(todays, 1),
                  hazard_tag=tag, hazard=text)
        remaining -= todays
        leg.arrived = remaining <= 0
        j.legs.append(leg)
        if stop_on and tag in stop_on:
            break
    j.days = day
    j.arrived = remaining <= 0
    return j


def eta(miles: float, *, speed_mph: float,
        hours_per_day: float = HOURS_PER_DAY) -> dict:
    """Just the numbers — for a quote, or a "how long would that take?"."""
    speed_mph = max(0.1, float(speed_mph))
    hours = float(miles) / speed_mph
    days = hours / max(0.5, float(hours_per_day))
    return {"miles": round(float(miles), 1), "hours": round(hours, 1),
            "days": max(1, round(days)) if miles > 0 else 0,
            "speed_mph": speed_mph}


def describe(j: Journey) -> str:
    """The passage as the DM should hear it: no bearings, only the going."""
    lines = [j.summary()]
    for leg in j.legs:
        if leg.hazard_tag and leg.hazard_tag != "clear":
            lines.append(f"- day {leg.day}: {leg.hazard}")
    return "\n".join(lines)
