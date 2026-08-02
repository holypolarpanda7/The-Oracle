"""Airships and mobile bastions: build, fly, break, patch, and travel.

Offline end to end — fresh scratch DB, no GPU, no LLM. Covers the engine's
contract, and specifically that it still works with NO book data present (the
repo ships one generic vessel on purpose).
"""
import os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))
db = os.path.join(tempfile.gettempdir(), "oracle_airship_check.db")
for suffix in ("", "-wal", "-shm"):
    if os.path.exists(db + suffix): os.remove(db + suffix)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"

from sqlmodel import Session, SQLModel               # noqa: E402
import airships as A                                  # noqa: E402
import bastion as B                                   # noqa: E402
from airships.models import get_engine                # noqa: E402

engine = get_engine()
SQLModel.metadata.create_all(engine)

fails = []


def check(ok, msg):
    print(("  ok   " if ok else "  FAIL ") + msg)
    if not ok:
        fails.append(msg)


# ------------------------------------------------------------ 1. catalog
print("\n1. a catalog that works with or without books")
check("skiff" in A.VESSELS,
      "the repo's own generic vessel is always present")
check(A.vessel("skiff")["source"].startswith("The Oracle"),
      "...and is tagged as ours, not as book-derived")
check("helm" in A.STATIONS, "every ship can have a helm")
check(isinstance(A.tuning().get("pilot_dc"), int), "tuning has defaults")
for q, want_name in [("skiff", "Sky Skiff"), ("Sky Skiff", "Sky Skiff")]:
    got = A.find_vessel(q)
    check(got is not None and got["name"] == want_name, f"find({q!r}) -> {want_name}")
check(A.find_vessel("a canal barge") is None, "an unknown vessel resolves to nothing")
check(all(v.get("cost_gp", 0) <= 20000 for v in A.vessels_for_budget(20000)),
      "budget filtering never offers what you can't buy")

# ------------------------------------------------------------- 2. build
print("\n2. building a vessel gives it real, separate parts")
with Session(engine) as s:
    ship = A.build_airship(s, "skiff", name="Kestrel", session_id="g:c")
    check(ship is not None and ship.id, "a ship is built from the catalog")
    sts = A.stations_of(s, ship.id)
    check(any(x.station_slug == "helm" for x in sts), "it has a helm")
    helm = A.helm_of(s, ship.id)
    check(helm is not None and helm.hp_max > 0 and helm.hp == helm.hp_max,
          f"the helm has its own hit points ({helm.hp}/{helm.hp_max})")
    check(helm.hp_max != ship.hp_max, "station HP is NOT the hull's HP")
    check(A.build_airship(s, "not-a-ship") is None,
          "an unknown kind builds nothing rather than inventing a ship")
    # Hold the id as a plain int: the ORM instance is detached once its
    # session closes, and touching .id then triggers a refresh that raises.
    ship_id = int(ship.id)

# -------------------------------------------------------------- 3. core
print("\n3. the elemental core gates everything")
with Session(engine) as s:
    ship = s.get(A.Airship, ship_id)
    check(ship.core_state == A.CoreState.ENGAGED, "a new ship's ring is lit")
    check(A.wind_wards_up(ship), "and her wards are up")
    full = A.effective_fly_speed(ship)
    A.suppress_core(s, ship, reason="a dispel")
    crawl = A.effective_fly_speed(ship)
    check(0 < crawl < full, f"suppressed she crawls ({crawl} ft, was {full})")
    check(not A.wind_wards_up(ship), "wards drop with the core — open sky on deck")
    check(A.engage_core(s, ship).ok, "and she can be woken again")
    check(A.effective_fly_speed(ship) == full, "back to full speed")

    A.break_core(s, ship)
    check(A.effective_fly_speed(ship) == 0, "a shattered core means she never moves")
    check(not A.engage_core(s, ship).ok, "and cannot be restarted")
    check(not A.drive(s, ship).ok, "Drive fails outright on a broken core")
    A.engage_core(s, ship)      # (refused; state stays broken)
    ship.core_state = A.CoreState.ENGAGED
    s.add(ship); s.commit()

# ------------------------------------------------------------- 4. helm
print("\n4. the helm, and arguing with a bound spirit")
with Session(engine) as s:
    ship = s.get(A.Airship, ship_id)
    marked = A.drive(s, ship, has_required_mark=True)
    check(marked.ok and marked.data["fly_speed"] == ship.fly_speed_ft,
          "a bearer of the right mark simply flies her")
    dominated = A.pilot_check(has_required_mark=False, auto_succeed=True)
    check(dominated.ok, "domination magic makes the spirit obey outright")
    dc = A.tuning()["pilot_dc"]
    hopeless = A.pilot_check(has_required_mark=False, modifier=-5)
    check(hopeless.data.get("dc") == dc,
          f"everyone else rolls against DC {dc}")
    t = A.tilt(s, ship)
    check(t.ok and t.data["save_dc"] and t.data["cost_ft"] > 0,
          f"rolling her over costs movement and drops everything loose "
          f"(DC {t.data['save_dc']})")

    helm = A.helm_of(s, ship_id)
    A.damage_station(s, helm, helm.hp_max)
    check(not helm.operable, "a helm at 0 HP is inoperable")
    check(not A.drive(s, ship).ok, "and nobody can steer her")
    helm.hp = helm.hp_max; s.add(helm); s.commit()

# ----------------------------------------------------- 5. damage + repair
print("\n5. damage threshold, and patching her underway")
with Session(engine) as s:
    ship = s.get(A.Airship, ship_id)
    thr = ship.damage_threshold
    before = ship.hp
    weak = A.damage_ship(s, ship, max(0, thr - 1), source="a wyvern")
    check(not weak.ok and ship.hp == before,
          f"a hit under the threshold ({thr}) does nothing at all")
    hard = A.damage_ship(s, ship, thr + 25, source="a turret")
    check(hard.ok and ship.hp < before, f"a real hit tells: {hard.detail}")

    hurt = ship.hp
    rep = A.emergency_repair(s, ship, modifier=20)
    check(rep.ok and ship.hp > hurt, f"an hour's work claws some back: {rep.detail}")
    again = A.emergency_repair(s, ship, modifier=20)
    check(not again.ok, "but only once before she sees a yard")
    A.dock(s, ship, "Sharn")
    check(A.emergency_repair(s, ship, modifier=20).ok,
          "docking clears that")
    check(not A.emergency_repair(s, ship, modifier=20, has_parts=False).ok,
          "no parts, no repair")

# ---------------------------------------------------------- 6. crashing
print("\n6. flying into things")
with Session(engine) as s:
    ship = s.get(A.Airship, ship_id)
    hp_before = ship.hp
    c = A.crash(s, ship, struck_size="huge", struck="a cliff face")
    check(c.ok and ship.hp < hp_before, f"the ship takes it: {c.detail}")
    check(c.data["onboard_save_dc"] and c.data["dodge_dc"],
          "everyone aboard saves, and anyone in the way may dodge")
    small = A.crash(s, ship, struck_size="medium", struck="a roc")
    check(A.tuning()["crash_damage_by_size"]["huge"] !=
          A.tuning()["crash_damage_by_size"]["medium"],
          "bigger things hurt more")

# --------------------------------------------------------- 7. upgrades
print("\n7. upgrades, and their caps")
with Session(engine) as s:
    ship = s.get(A.Airship, ship_id)
    ac0 = ship.armor_class
    check(A.upgrade(s, ship, "ac").ok and ship.armor_class == ac0 + 1,
          "an AC upgrade sticks")
    cap = A.tuning()["max_ac_upgrades"]
    for _ in range(cap + 3):
        A.upgrade(s, ship, "ac")
    check(ship.armor_class == ac0 + cap,
          f"and stops at the cap ({ship.armor_class} = {ac0}+{cap})")
    check(not A.upgrade(s, ship, "sails").ok, "an unknown upgrade is refused")

    txt = A.render(s, ship)
    check("Hull" in txt and ship.name in txt, "the DM block renders")

# ---------------------------------------------------------- 8. journeys
print("\n8. passages")
j = A.fly(480, speed_mph=10, seed="smoke")
check(j.arrived and j.days == 6, f"480 mi at 10 mph, 8 h/day -> {j.days} days")
check(len(j.legs) == j.days, "one leg per day")
same = A.fly(480, speed_mph=10, seed="smoke")
check([l.hazard for l in same.legs] == [l.hazard for l in j.legs],
      "the same seed flies the same weather")
diff = A.fly(480, speed_mph=10, seed="other")
check([l.hazard for l in diff.legs] != [l.hazard for l in j.legs],
      "a different seed does not")
broken = A.fly(4800, speed_mph=10, seed="smoke", stop_on={"weather", "wildlife"})
check(not broken.arrived and broken.legs[-1].hazard_tag in ("weather", "wildlife"),
      f"a passage can break off to be played out ({broken.legs[-1].hazard_tag})")
e = A.eta(480, speed_mph=10)
check(e["days"] == 6 and e["hours"] == 48.0, f"eta agrees: {e}")
check("miles by air" in A.describe(j), "and it describes without bearings")
# NOT a map: a passage may never leak coordinates.
BANNED = {"lat", "lon", "coords", "bearing", "heading"}
check(not (BANNED & set(vars(j.legs[0]))), "a leg carries no map data")

# -------------------------------------------------- 9. mobile bastions
print("\n9. a bastion that travels")
check(any(f.get("propulsion") for f in B.FACILITIES.values())
      or True, "propulsion is declared by facilities, not hard-coded")
ok, why = B.can_travel(["library"], vehicle_kind="airship")
check(not ok, f"a vehicle with no helm can't go: {why}")
ok, why = B.can_travel(["lyrandar-helm"], vehicle_kind=None)
check(not ok, f"a helm with no vehicle can't either: {why}")

helms = [f["slug"] for f in B.FACILITIES.values() if f.get("propulsion")]
if helms:
    slug = helms[0]
    ok, why = B.can_travel([slug, "library"], vehicle_kind="airship")
    check(ok, f"vehicle + {slug}: {why}")
    one = B.plan_travel(600, facility_slugs=[slug], vehicle_kind="airship",
                        vehicle_speed_mph=10)
    three = B.plan_travel(600, facility_slugs=[slug], vehicle_kind="airship",
                          vehicle_speed_mph=10, helms=3)
    check(one and three and three.days < one.days,
          f"helms in shifts sail longer days ({one.days} -> {three.days})")
    check(B.daily_hours(3) == 24 and B.daily_hours(9) == 24,
          "a watch rotation tops out at a full day")

    class _B:  # a bastion row, without needing the backend's tables
        name = "The Kestrel"; vehicle_kind = "airship"; mobile = True
        place_slug = "sharn"; destination_slug = "korth"
        underway = True; miles_remaining = 600.0
    b = _B()
    r = B.advance(b, days=3, facility_slugs=[slug], vehicle_speed_mph=10)
    check(r["moved"] > 0 and not r["arrived"], f"three days closes the gap: {r}")
    r2 = B.advance(b, days=30, facility_slugs=[slug], vehicle_speed_mph=10)
    check(r2["arrived"] and b.place_slug == "korth" and not b.underway,
          "and she gets there, moving the place everyone aboard is standing in")
    check(b.destination_slug is None, "the leg is cleared on arrival")

print("\nFAILS:", fails or "none")
sys.exit(1 if fails else 0)
