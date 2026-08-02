"""A bastion that travels — built into a ship, a rail cart, or an airship.

The normal bastion sits still and the party goes away from it. A mobile one
inverts that: the base of operations comes along, which changes surprisingly
little in the rules and a great deal at the table.

Two hard requirements, and this module enforces both:

1. it must be built in a vehicle;
2. one of its special facilities must be able to PROPEL it — a helm of some
   kind. Which facilities qualify is declared by the facility itself
   (``propulsion`` in the catalog), not listed here, so a setting that invents
   its own helm works without touching this file.

The travel order is where it gets interesting. A single helm crews eight hours
a day. Several bastions combined into one vehicle can run in shifts — three
helms means somebody is always at the wheel and the vehicle never stops — so
``daily_hours`` scales with how many helms are being empowered together.

Position lives in the world graph, because a bastion that can move has to be
somewhere the rest of the game already understands. Moving it moves the place
everyone aboard is standing in, and nothing else has to know.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .catalog import get_facility

#: Hours a single helm's hireling can crew in a day.
HOURS_PER_HELM_PER_DAY = 8
#: Even a full watch rotation tops out at a full day.
MAX_HOURS_PER_DAY = 24


@dataclass
class TravelPlan:
    """What a leg of travel would cost, before anyone commits to it."""
    miles: float
    speed_mph: float
    daily_hours: float
    days: int
    arrives: bool
    helms: int = 1
    note: str = ""

    def summary(self) -> str:
        shift = (f" ({self.helms} helms in shifts, {self.daily_hours:g} h/day)"
                 if self.helms > 1 else f" ({self.daily_hours:g} h/day)")
        head = (f"{self.miles:.0f} miles at {self.speed_mph:g} mph{shift}: "
                f"{'about a day' if self.days <= 1 else f'{self.days} days'}")
        return head + ("" if self.arrives else " — and still short of it")


def propulsion_of(facility_slugs) -> Optional[dict]:
    """The first installed facility that can move this bastion, if any."""
    for slug in facility_slugs or []:
        fac = get_facility(slug)
        if fac and fac.get("propulsion"):
            return fac
    return None


def can_travel(facility_slugs, *, vehicle_kind: Optional[str] = None) -> tuple[bool, str]:
    """Both requirements, checked together, with a reason when it's a no."""
    if not vehicle_kind:
        return False, "This bastion isn't built in a vehicle."
    fac = propulsion_of(facility_slugs)
    if fac is None:
        return False, ("Nothing aboard can move her — a mobile bastion needs a "
                       "helm facility.")
    return True, f"{fac['name']} can take her out."


def daily_hours(helms: int = 1) -> float:
    """Eight hours a helm, capped at a full day of watches."""
    return float(min(MAX_HOURS_PER_DAY, max(1, int(helms)) * HOURS_PER_HELM_PER_DAY))


def plan_travel(miles: float, *, facility_slugs, vehicle_kind: Optional[str],
                vehicle_speed_mph: Optional[float] = None,
                helms: int = 1, turn_days: int = 7) -> Optional[TravelPlan]:
    """Cost out a passage for this bastion. None when it simply can't go.

    Speed comes from the propulsion facility when it fixes one (a rail helm
    runs at its line's speed regardless of what it's pulling), otherwise from
    the vehicle itself.
    """
    ok, why = can_travel(facility_slugs, vehicle_kind=vehicle_kind)
    if not ok:
        return None
    fac = propulsion_of(facility_slugs) or {}
    speed = float(fac.get("speed_mph") or vehicle_speed_mph or 0.0)
    if speed <= 0:
        return None
    hours = daily_hours(helms)
    per_day = speed * hours
    days_needed = max(1, int(-(-float(miles) // per_day)))   # ceil
    covered = per_day * min(days_needed, max(1, int(turn_days)))
    return TravelPlan(miles=float(miles), speed_mph=speed, daily_hours=hours,
                      days=days_needed, arrives=covered >= float(miles),
                      helms=max(1, int(helms)), note=why)


def advance(bastion, *, days: int, facility_slugs,
            vehicle_speed_mph: Optional[float] = None,
            helms: int = 1) -> dict:
    """Move a bastion along its current leg by ``days`` of travel.

    Mutates ``miles_remaining``/``underway`` on the row and reports what
    happened; the caller commits and handles arrival (moving the world-graph
    place, logging the event).
    """
    plan_ok, why = can_travel(facility_slugs,
                              vehicle_kind=getattr(bastion, "vehicle_kind", None))
    if not plan_ok:
        return {"moved": 0.0, "arrived": False, "note": why}
    fac = propulsion_of(facility_slugs) or {}
    speed = float(fac.get("speed_mph") or vehicle_speed_mph or 0.0)
    if speed <= 0:
        return {"moved": 0.0, "arrived": False,
                "note": "Nothing is driving her."}
    covered = speed * daily_hours(helms) * max(0, int(days))
    remaining = max(0.0, float(bastion.miles_remaining or 0.0) - covered)
    moved = float(bastion.miles_remaining or 0.0) - remaining
    bastion.miles_remaining = remaining
    arrived = remaining <= 0.0 and bool(bastion.destination_slug)
    if arrived:
        bastion.underway = False
        bastion.place_slug = bastion.destination_slug
        bastion.destination_slug = None
    return {"moved": round(moved, 1), "arrived": arrived,
            "remaining": round(remaining, 1), "speed_mph": speed,
            "note": why}


def suspended_facilities(facility_slugs) -> list[dict]:
    """Facilities whose order can't be issued while the bastion is in transit.

    Declared in the facility data (``mobile_note``) rather than hard-coded: an
    intelligence network and a planar link both need the bastion to STAY
    somewhere, and both say so in their own entry.
    """
    out = []
    for slug in facility_slugs or []:
        fac = get_facility(slug)
        if fac and fac.get("mobile_note"):
            out.append(fac)
    return out


def render(bastion, *, facility_slugs=None, place_name: str = "") -> str:
    """Compact text block for the DM prompt."""
    if not getattr(bastion, "mobile", False):
        return ""
    where = place_name or bastion.place_slug or "somewhere"
    lines = [f"# {bastion.name or 'The bastion'} "
             f"({bastion.vehicle_kind}) — {'underway' if bastion.underway else 'moored'} at {where}"]
    if bastion.underway and bastion.destination_slug:
        lines.append(f"- Bound for {bastion.destination_slug}, "
                     f"{bastion.miles_remaining:.0f} miles to run.")
    fac = propulsion_of(facility_slugs or [])
    if fac:
        lines.append(f"- Driven by her {fac['name']}.")
    if bastion.underway:
        for f in suspended_facilities(facility_slugs or []):
            lines.append(f"- {f['name']} is idle in transit ({f['mobile_note']}).")
    return "\n".join(lines)
