"""Operating a vessel: the core, the helm, damage, repairs and crashes.

The engine. Every number it uses comes from :mod:`airships.catalog` (and so,
where a table owns the book, from that table's local data file) — this module
holds the *rules*, not the figures.

Design notes worth keeping:

* **The core is the gate.** Almost everything a ship does needs the elemental
  core engaged. Suppressed, it hovers and crawls; broken, it hovers and never
  moves again. Encoding that in one place stops each station reinventing it.
* **Damage threshold is a filter, not resistance.** A hit under the threshold
  does NOTHING — that's what makes a monster attacking a cruiser pointless and
  a monster attacking its crew sensible.
* **Stations take damage separately from the hull.** A dead turret on a healthy
  ship is the normal, interesting case.
* Nothing here rolls narrative outcomes. It resolves mechanics and returns what
  happened, so the DM can narrate it and the caller can log it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import Session, select

from dice import ability_check, roll as dice_roll

from . import catalog
from .models import Airship, CoreState, CrewStation


@dataclass
class Outcome:
    """What a manoeuvre did. ``ok`` is 'the thing happened', not 'it went well'."""
    ok: bool
    detail: str
    data: dict = field(default_factory=dict)


# ----- building and manning -------------------------------------------------


def build_airship(session: Session, kind: str, *, name: str = "",
                  owner_character_id: Optional[int] = None,
                  session_id: Optional[str] = None,
                  place_slug: Optional[str] = None) -> Optional[Airship]:
    """Instantiate a vessel from the catalog, stations and all."""
    spec = catalog.vessel(kind) or catalog.find_vessel(kind)
    if spec is None:
        return None
    ship = Airship(
        kind=spec["slug"],
        name=name or spec["name"],
        owner_character_id=owner_character_id,
        session_id=session_id,
        place_slug=place_slug,
        armor_class=int(spec.get("armor_class", 15)),
        hp=int(spec.get("hp", 100)), hp_max=int(spec.get("hp", 100)),
        damage_threshold=int(spec.get("damage_threshold", 0)),
        speed_mph=float(spec.get("speed_mph", 8.0)),
        fly_speed_ft=int(spec.get("fly_speed_ft", 80)),
        crew_max=int(spec.get("crew", 4)),
        passengers_max=int(spec.get("passengers", 8)),
        cargo_tons=float(spec.get("cargo_tons", 1.0)),
        upgrades={},
    )
    session.add(ship)
    session.commit()
    session.refresh(ship)

    for slug in spec.get("stations") or ["helm"]:
        install_station(session, ship.id, slug)
    return ship


def install_station(session: Session, airship_id: int, slug: str,
                    *, count: int = 1) -> list[CrewStation]:
    """Add stations to a ship. Unknown slugs are skipped, not invented."""
    spec = catalog.station(slug)
    if spec is None:
        return []
    made: list[CrewStation] = []
    for _ in range(max(1, int(spec.get("count", count)))):
        st = CrewStation(
            airship_id=airship_id, station_slug=spec["slug"],
            name=spec.get("name", spec["slug"].title()),
            armor_class=int(spec.get("armor_class", 15)),
            hp=int(spec.get("hp", 20)), hp_max=int(spec.get("hp", 20)),
            state={},
        )
        session.add(st)
        made.append(st)
    session.commit()
    for st in made:
        session.refresh(st)
    return made


def stations_of(session: Session, airship_id: int) -> list[CrewStation]:
    return list(session.exec(select(CrewStation).where(
        CrewStation.airship_id == airship_id)).all())


def helm_of(session: Session, airship_id: int) -> Optional[CrewStation]:
    for st in stations_of(session, airship_id):
        if st.station_slug == "helm":
            return st
    return None


# ----- the elemental core ---------------------------------------------------


def suppress_core(session: Session, ship: Airship, *, reason: str = "",
                  locked_until_day: Optional[int] = None) -> Outcome:
    """Damp the core: the ship hovers and crawls, and its stations go quiet."""
    if ship.core_state == CoreState.BROKEN:
        return Outcome(False, f"{ship.name}'s core is already shattered.")
    ship.core_state = CoreState.SUPPRESSED
    if locked_until_day is not None:
        ship.core_locked_until_day = int(locked_until_day)
    session.add(ship)
    session.commit()
    tail = f" ({reason})" if reason else ""
    return Outcome(True, f"{ship.name}'s elemental ring gutters out{tail}. "
                         f"She hovers, and crawls.")


def engage_core(session: Session, ship: Airship, *,
                world_day: Optional[int] = None) -> Outcome:
    """Bring the core back up, unless something is holding it down."""
    if ship.core_state == CoreState.BROKEN:
        return Outcome(False, f"{ship.name}'s core is shattered — there is "
                              f"nothing left to wake.")
    lock = ship.core_locked_until_day
    if lock is not None and world_day is not None and world_day < lock:
        return Outcome(False, f"{ship.name}'s core will not answer yet.")
    ship.core_state = CoreState.ENGAGED
    ship.core_locked_until_day = None
    session.add(ship)
    session.commit()
    return Outcome(True, f"The ring flares back to life around {ship.name}.")


def break_core(session: Session, ship: Airship) -> Outcome:
    """Shatter the shard. The ship will never move under its own power again."""
    ship.core_state = CoreState.BROKEN
    session.add(ship)
    session.commit()
    return Outcome(True, f"{ship.name}'s core shard shatters; the bound spirit "
                         f"is loose and the ship is dead in the air.")


def effective_fly_speed(ship: Airship) -> int:
    """Tactical speed right now, given the core's state."""
    if ship.wrecked or ship.core_state == CoreState.BROKEN:
        return 0
    if ship.core_state == CoreState.SUPPRESSED:
        return int(catalog.tuning().get("suppressed_fly_speed_ft", 5))
    return int(ship.fly_speed_ft)


def wind_wards_up(ship: Airship) -> bool:
    """Wards ride on the core: lose it and the deck is open to the sky."""
    return ship.core_state == CoreState.ENGAGED and not ship.wrecked


# ----- the helm -------------------------------------------------------------


def pilot_check(*, has_required_mark: bool, modifier: int = 0,
                advantage: bool = False, auto_succeed: bool = False,
                label: str = "command the bound spirit") -> Outcome:
    """Can this pilot make the ship answer?

    A bearer of the mark the vessel expects simply flies it. Anyone else is
    arguing with a bound elemental, and the check is deliberately steep. Charm
    or domination magic is the documented way around it, which the caller
    reports as ``advantage``/``auto_succeed`` rather than this module tracking
    live spell effects.
    """
    if has_required_mark:
        return Outcome(True, "The controls answer at a touch.",
                       {"mark": True})
    if auto_succeed:
        return Outcome(True, "The spirit obeys without argument.",
                       {"dominated": True})
    dc = int(catalog.tuning().get("pilot_dc", 20))
    res = ability_check(modifier, dc=dc, advantage=advantage, label=label)
    return Outcome(bool(res.success), res.detail,
                   {"dc": dc, "total": res.total, "natural": res.natural})


def drive(session: Session, ship: Airship, *, has_required_mark: bool = True,
          modifier: int = 0, advantage: bool = False,
          auto_succeed: bool = False) -> Outcome:
    """Take the Drive action: the ship moves up to its speed, if it will."""
    if ship.wrecked:
        return Outcome(False, f"{ship.name} is a wreck; she answers nothing.")
    helm = helm_of(session, ship.id)
    if helm is not None and not helm.operable:
        return Outcome(False, f"{ship.name}'s helm is smashed — no one can "
                              f"steer her.")
    if ship.core_state == CoreState.BROKEN:
        return Outcome(False, f"{ship.name} hangs where she is; her core is "
                              f"broken.")
    got = pilot_check(has_required_mark=has_required_mark, modifier=modifier,
                      advantage=advantage, auto_succeed=auto_succeed,
                      label="Drive")
    speed = effective_fly_speed(ship)
    if not got.ok:
        return Outcome(False, f"{got.detail} — the spirit ignores the order.",
                       {**got.data, "fly_speed": 0})
    return Outcome(True, f"{got.detail} {ship.name} answers, up to {speed} ft.",
                   {**got.data, "fly_speed": speed})


def tilt(session: Session, ship: Airship) -> Outcome:
    """Roll the ship over. Everything not tied down falls."""
    if not ship.can_move:
        return Outcome(False, f"{ship.name} cannot manoeuvre.")
    dc = int(catalog.tuning().get("tilt_save_dc", 15))
    return Outcome(True,
                   f"{ship.name} rolls on her axis. Every creature and loose "
                   f"object aboard falls unless it makes a DC {dc} Dexterity "
                   f"save to catch hold of something fixed.",
                   {"save_dc": dc, "ability": "dexterity",
                    "cost_ft": effective_fly_speed(ship) // 2})


# ----- taking damage --------------------------------------------------------


def damage_ship(session: Session, ship: Airship, amount: int,
                *, source: str = "") -> Outcome:
    """Apply damage to the HULL, honouring the damage threshold.

    The threshold is a filter, not resistance: anything under it is shrugged
    off entirely. That single rule is what makes a wyvern worrying at a cruiser
    futile — and why it should go for the crew instead.
    """
    amount = max(0, int(amount))
    thr = int(ship.damage_threshold or 0)
    if amount < thr:
        return Outcome(False,
                       f"{amount} damage washes off {ship.name}'s hull "
                       f"(threshold {thr}).",
                       {"absorbed": True, "threshold": thr})
    ship.hp = max(0, ship.hp - amount)
    session.add(ship)
    session.commit()
    tail = f" from {source}" if source else ""
    if ship.hp == 0:
        return Outcome(True, f"{ship.name} takes {amount}{tail} and comes "
                             f"apart in the air.",
                       {"hp": 0, "wrecked": True})
    return Outcome(True, f"{ship.name} takes {amount}{tail} "
                         f"({ship.hp}/{ship.hp_max}).",
                   {"hp": ship.hp, "wrecked": False})


def damage_station(session: Session, st: CrewStation, amount: int) -> Outcome:
    """Stations have no threshold — they are the soft targets on a hard ship."""
    amount = max(0, int(amount))
    st.hp = max(0, st.hp - amount)
    session.add(st)
    session.commit()
    if st.hp == 0:
        return Outcome(True, f"The {st.name} is wrecked and inoperable.",
                       {"hp": 0, "operable": False})
    return Outcome(True, f"The {st.name} takes {amount} ({st.hp}/{st.hp_max}).",
                   {"hp": st.hp, "operable": True})


def emergency_repair(session: Session, ship: Airship, *,
                     station: Optional[CrewStation] = None,
                     modifier: int = 0, has_parts: bool = True,
                     has_tools: bool = True) -> Outcome:
    """An hour's work underway to claw back hit points. Once per docking.

    Note it never revives a wreck: the target must still have a hit point left.
    A ship at 0 is a crash, not a repair job.
    """
    t = catalog.tuning()
    target = station or ship
    if target.hp <= 0:
        return Outcome(False, "There is nothing left to repair in the air.")
    if not has_parts:
        return Outcome(False, f"No spare parts — {t.get('repair_parts_gp', 100)} "
                              f"GP worth are needed.")
    if not has_tools:
        return Outcome(False, "No suitable tools for the work.")
    if ship.emergency_repaired:
        return Outcome(False, f"{ship.name} has already been patched once since "
                              f"her last docking; she needs a yard now.")
    dc = int(t.get("repair_dc", 16))
    res = ability_check(modifier, dc=dc, label="Emergency repair")
    if not res.success:
        return Outcome(False, f"{res.detail} — the parts survive another try.",
                       {"dc": dc})
    healed = dice_roll(str(t.get("repair_dice", "2d4+2"))).total
    target.hp = min(target.hp_max, target.hp + healed)
    ship.emergency_repaired = True
    session.add(target)
    session.add(ship)
    session.commit()
    what = station.name if station else ship.name
    return Outcome(True, f"{res.detail} — {what} regains {healed} "
                         f"({target.hp}/{target.hp_max}).",
                   {"healed": healed, "hp": target.hp})


def dock(session: Session, ship: Airship, where: str = "") -> Outcome:
    """Put in. Clears the once-per-docking emergency repair."""
    ship.docked_at = where or "a dock"
    ship.emergency_repaired = False
    session.add(ship)
    session.commit()
    return Outcome(True, f"{ship.name} puts in at {ship.docked_at}.")


# ----- crashing -------------------------------------------------------------


def crash(session: Session, ship: Airship, *, struck_size: str = "medium",
          struck: str = "") -> Outcome:
    """Fly into something. Both parties take it, and so does everyone aboard."""
    t = catalog.tuning()
    table = t.get("crash_damage_by_size") or {}
    expr = table.get((struck_size or "medium").strip().lower(), "2d10")
    ship_dmg = dice_roll(expr).total
    hull = damage_ship(session, ship, ship_dmg, source="the impact")
    stops = (struck_size or "").strip().lower() in ("huge", "gargantuan") or \
        struck_size.strip().lower() == "large" and ship.fly_speed_ft <= 60
    return Outcome(True,
                   f"{ship.name} strikes {struck or 'something'} — {expr} to "
                   f"both. {hull.detail}",
                   {"damage_expr": expr, "ship_damage": ship_dmg,
                    "onboard_save_dc": int(t.get("crash_onboard_dc", 10)),
                    "onboard_save_abilities": ["strength", "dexterity"],
                    "onboard_damage_expr": expr,
                    "dodge_dc": int(t.get("crash_dodge_dc", 15)),
                    "ship_stops": stops,
                    "wrecked": ship.hp <= 0})


# ----- upgrades -------------------------------------------------------------


def upgrade(session: Session, ship: Airship, kind: str) -> Outcome:
    """Apply an ``ac`` or ``hp`` upgrade, capped by the catalog.

    Docked-only and the cost/time are the caller's business — this enforces the
    caps and moves the numbers, so a ship can't be upgraded past what the rules
    allow by asking twice.
    """
    t = catalog.tuning()
    kind = (kind or "").strip().lower()
    used = dict(ship.upgrades or {})
    if kind == "ac":
        cap = int(t.get("max_ac_upgrades", 5))
        if used.get("ac", 0) >= cap:
            return Outcome(False, f"{ship.name}'s wards will take no more "
                                  f"(cap {cap}).")
        step = int(t.get("ac_per_upgrade", 1))
        ship.armor_class += step
        used["ac"] = used.get("ac", 0) + 1
        detail = f"{ship.name}'s AC rises to {ship.armor_class}."
    elif kind == "hp":
        cap = int(t.get("max_hp_upgrades", 5))
        if used.get("hp", 0) >= cap:
            return Outcome(False, f"{ship.name}'s hull will take no more "
                                  f"(cap {cap}).")
        step = int(t.get("hp_per_upgrade", 20))
        ship.hp_max += step
        ship.hp += step
        used["hp"] = used.get("hp", 0) + 1
        detail = f"{ship.name}'s hull rises to {ship.hp}/{ship.hp_max}."
    else:
        return Outcome(False, f"No such upgrade: {kind!r} (try 'ac' or 'hp').")
    ship.upgrades = used
    session.add(ship)
    session.commit()
    return Outcome(True, detail, {"upgrades": used})


def summary(session: Session, ship: Airship) -> dict:
    """Everything the DM prompt and the UI need about a vessel, in one dict."""
    sts = stations_of(session, ship.id)
    return {
        "id": ship.id, "name": ship.name, "kind": ship.kind,
        "ac": ship.armor_class, "hp": ship.hp, "hp_max": ship.hp_max,
        "damage_threshold": ship.damage_threshold,
        "core": ship.core_state, "wards": wind_wards_up(ship),
        "fly_speed_ft": effective_fly_speed(ship),
        "speed_mph": ship.speed_mph,
        "crew_max": ship.crew_max, "passengers_max": ship.passengers_max,
        "cargo_tons": ship.cargo_tons,
        "docked_at": ship.docked_at, "wrecked": ship.wrecked,
        "place_slug": ship.place_slug,
        "stations": [{"slug": s.station_slug, "name": s.name,
                      "hp": s.hp, "hp_max": s.hp_max, "ac": s.armor_class,
                      "operable": s.operable, "operator": s.operator}
                     for s in sts],
    }


def render(session: Session, ship: Airship) -> str:
    """Compact text block for the DM prompt."""
    s = summary(session, ship)
    core = {"engaged": "ring lit", "suppressed": "core damped",
            "broken": "core shattered"}.get(s["core"], s["core"])
    lines = [f"# Aboard {s['name']} ({s['kind']})",
             f"- Hull {s['hp']}/{s['hp_max']}, AC {s['ac']}, "
             f"damage threshold {s['damage_threshold']}; {core}; "
             f"speed {s['fly_speed_ft']} ft ({s['speed_mph']:g} mph)"]
    if not s["wards"]:
        lines.append("- Wind wards are DOWN: open sky, wind and cold on deck.")
    live = [f"{x['name']} ({x['hp']}/{x['hp_max']})" for x in s["stations"] if x["operable"]]
    dead = [x["name"] for x in s["stations"] if not x["operable"]]
    if live:
        lines.append("- Stations: " + ", ".join(live))
    if dead:
        lines.append("- Wrecked stations: " + ", ".join(dead))
    return "\n".join(lines)
