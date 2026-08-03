"""Combat bonds: linked creatures roll better, see through walls, and step through.

Offline end to end — fresh scratch DB, no GPU, no LLM. Drives the REAL tracker
and the REAL board, because the whole point of the bond layer is that it
overrides machinery that works correctly without it.
"""
import os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))
db = os.path.join(tempfile.gettempdir(), "oracle_bonds_check.db")
for suffix in ("", "-wal", "-shm"):
    if os.path.exists(db + suffix): os.remove(db + suffix)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"

import random                                          # noqa: E402
from sqlmodel import Session, SQLModel                  # noqa: E402
from combat import CombatTracker, bonds                 # noqa: E402
from combat.models import CombatantKind                 # noqa: E402
from vtt import VttEngine                               # noqa: E402
from vtt.scene import _default_engine                    # noqa: E402

engine = _default_engine()
SQLModel.metadata.create_all(engine)

SID = "guild:chan"
fails = []


def check(ok, msg):
    print(("  ok   " if ok else "  FAIL ") + msg)
    if not ok:
        fails.append(msg)


# --------------------------------------------------------- 1. the bond
print("\n1. granting, replacing and revoking")
with Session(engine) as s:
    made = bonds.grant(s, session_id=SID, kind="atlas", owner_ref="Vex",
                       holders=["Vex", "Kara", "Bram"],
                       initiative_dice="1d4", sees_through_cover=True,
                       rescue_hp=30)
    check(len(made) == 3, f"three creatures carry it ({len(made)})")
    check(sorted(bonds.holders(s, SID, kind="atlas", owner_ref="Vex"))
          == ["bram", "kara", "vex"], "holders read back")

    # Using the feature again replaces it — the old maps vanish.
    bonds.grant(s, session_id=SID, kind="atlas", owner_ref="Vex",
                holders=["Vex", "Kara"], initiative_dice="1d4",
                sees_through_cover=True, rescue_hp=30)
    now = bonds.holders(s, SID, kind="atlas", owner_ref="Vex")
    check(sorted(now) == ["kara", "vex"], f"re-granting replaces, never adds ({now})")
    check(not bonds.sees_through(s, SID, "Bram", "Vex"),
          "the dropped holder is no longer linked")

    check(bonds.sees_through(s, SID, "Vex", "Kara"), "two holders are linked")
    check(bonds.sees_through(s, SID, "Kara", "Vex"), "and it is symmetric")
    check(not bonds.sees_through(s, SID, "Vex", "Vex"),
          "a creature is not linked to itself")
    check(not bonds.sees_through(s, SID, "Vex", "A Wandering Ogre"),
          "and not to a stranger")

    # A second owner's bond is a SEPARATE bond.
    bonds.grant(s, session_id=SID, kind="atlas", owner_ref="Rhys",
                holders=["Rhys", "Bram"], sees_through_cover=True)
    check(not bonds.sees_through(s, SID, "Kara", "Bram"),
          "two different owners' bonds don't join their parties")
    check(bonds.sees_through(s, SID, "Rhys", "Bram"), "...but each links its own")

    # A lever that isn't granted doesn't answer yes.
    bonds.grant(s, session_id=SID, kind="quiet-pact", owner_ref="Nim",
                holders=["Nim", "Sable"], initiative_dice="1d6")
    check(not bonds.sees_through(s, SID, "Nim", "Sable"),
          "a bond without the sight lever grants no sight")
    check(bonds.linked(s, SID, "Nim", "Sable") is not None,
          "though they ARE linked for other purposes")

# --------------------------------------------------- 2. initiative dice
print("\n2. the initiative bonus reaches the real tracker")
with Session(engine) as s:
    check(bonds.initiative_dice_for(s, SID, "Kara") == "1d4",
          "a holder's die is found")
    check(bonds.initiative_dice_for(s, SID, "A Wandering Ogre") == "",
          "a stranger gets none")
    check(bonds.initiative_dice_for(s, SID, "Sable") == "1d6",
          "the other bond's die is its own")

ct = CombatTracker(engine=engine)
enc = ct.start_encounter(SID, name="test")
for nm in ("Ogre", "Kara"):   # stranger first: see the RNG note below
    ct.add_combatant(enc.id, name=nm, kind=CombatantKind.MONSTER,
                     max_hp=30, armor_class=13, dex_mod=0)


def dice_for(name):
    with Session(engine) as s:
        return bonds.initiative_dice_for(s, SID, name)


# Same seed, with and without the bonus. One shared RNG is a sequential stream,
# so a bonus die consumed by an early combatant shifts everyone after it — the
# unbonded Ogre is rolled FIRST so its d20 comes from the same position in both
# runs and stays a fair control.
ct.roll_initiative(enc.id, reroll=True, rng=random.Random(4), reset_turn=True)
plain = {c.name: c.initiative for c in ct.order(enc.id)}
ct.roll_initiative(enc.id, reroll=True, rng=random.Random(4), reset_turn=True,
                   bonus_dice_for=dice_for)
bonded = {c.name: c.initiative for c in ct.order(enc.id)}
check(bonded["Kara"] > plain["Kara"],
      f"the holder rolls higher ({plain['Kara']} -> {bonded['Kara']})")
check(bonded["Ogre"] == plain["Ogre"],
      f"the stranger is untouched ({plain['Ogre']} -> {bonded['Ogre']})")
check(1 <= bonded["Kara"] - plain["Kara"] <= 4, "and by 1d4, not more")

# ------------------------------------------------ 3. sight through cover
print("\n3. the board's own cover, deliberately overruled")


def linked_fn(session_id, a, b):
    with Session(engine) as s:
        return bonds.sees_through(s, session_id, a, b)


plain_v = VttEngine(engine=engine)                       # no link predicate
bonded_v = VttEngine(engine=engine, linked=linked_fn)

scene = plain_v.open_scene(SID, kind="combat", archetype="dungeon-complex",
                           width=24, height=18, seed=99, render_art=False)
# Put two tokens far apart with the layout between them, and find a pair the
# board genuinely blocks — that is the case worth testing.
pairs = []
w, h = scene.width, scene.height
for (ax, ay), (bx, by) in [((1, 1), (w - 2, h - 2)), ((1, h - 2), (w - 2, 1)),
                           ((2, 2), (w - 3, h - 3))]:
    g = plain_v.grid_of(scene)
    if g.passable(ax, ay) and g.passable(bx, by):
        pairs.append(((ax, ay), (bx, by)))
found = False
for (ax, ay), (bx, by) in pairs:
    for t in plain_v.tokens(scene.id):
        plain_v.remove_token(t.id)
    plain_v.add_token(scene.id, name="Kara", x=ax, y=ay, team="party", speed_ft=30)
    plain_v.add_token(scene.id, name="Ogre", x=bx, y=by, team="foes", speed_ft=30)
    if not plain_v.can_see(scene.id, "Kara", "Ogre"):
        found = True
        break
check(found, "found a pair the board really does block sight between")
if found:
    check(not plain_v.can_see(scene.id, "Kara", "Ogre"),
          "un-linked, the board says they cannot see each other")
    with Session(engine) as s:
        bonds.grant(s, session_id=SID, kind="atlas", owner_ref="Vex",
                    holders=["Kara", "Ogre"], sees_through_cover=True)
    check(bonded_v.can_see(scene.id, "Kara", "Ogre"),
          "linked, they can — regardless of what is between them")
    check(bonded_v.cover_for(scene.id, "Kara", "Ogre") == "none",
          "and neither has cover from the other")
    check(not plain_v.can_see(scene.id, "Kara", "Ogre"),
          "an engine with no link predicate is entirely unaffected")
    with Session(engine) as s:
        bonds.revoke(s, session_id=SID, kind="atlas", owner_ref="Vex")
    check(not bonded_v.can_see(scene.id, "Kara", "Ogre"),
          "revoking puts the wall back")

# --------------------------------------------------------- 4. stepping through
print("\n4. blinking, alone and through an ally")
for t in bonded_v.tokens(scene.id):
    bonded_v.remove_token(t.id)
g = bonded_v.grid_of(scene)
spots = [(x, y) for y in range(scene.height) for x in range(scene.width)
         if g.passable(x, y)]
start = spots[0]
near = next((p for p in spots if 0 < max(abs(p[0]-start[0]), abs(p[1]-start[1])) <= 2), None)
far = max(spots, key=lambda p: max(abs(p[0]-start[0]), abs(p[1]-start[1])))
beside_far = next((p for p in spots
                   if p != far and max(abs(p[0]-far[0]), abs(p[1]-far[1])) == 1), None)

hero = bonded_v.add_token(scene.id, name="Vex", x=start[0], y=start[1],
                          team="party", speed_ft=30)
check(near is not None and far != start, "the board gave us somewhere to jump")

r = bonded_v.blink(hero.id, near[0], near[1])
check(r.get("ok"), f"a short step lands: {r.get('reason','ok')}")
check(r.get("cost_ft") == 15, f"and costs half a 30-ft speed ({r.get('cost_ft')})")
bonded_v.start_turn(scene.id, token_id=hero.id)

r2 = bonded_v.blink(hero.id, far[0], far[1])
check(not r2.get("ok"), f"a long jump alone is refused: {r2.get('reason','')[:60]}")

# Put an UNLINKED ally beside the far spot: still refused.
ally = bonded_v.add_token(scene.id, name="Kara", x=far[0], y=far[1],
                          team="party", speed_ft=30)
if beside_far:
    r3 = bonded_v.blink(hero.id, beside_far[0], beside_far[1])
    check(not r3.get("ok"), "an ally you are NOT linked to is no help")
    with Session(engine) as s:
        bonds.grant(s, session_id=SID, kind="atlas", owner_ref="Vex",
                    holders=["Vex", "Kara"], sees_through_cover=True, rescue_hp=30)
    r4 = bonded_v.blink(hero.id, beside_far[0], beside_far[1],
                        ally_within_ft=10_000)
    check(r4.get("ok"), f"linked, you step through to them: {r4.get('reason','ok')}")
    check(r4.get("through") == "Kara", f"and it names who you came through ({r4.get('through')})")

# No speed, no stepping.
bonded_v.update_token(hero.id, speed_ft=0)
check(not bonded_v.blink(hero.id, near[0], near[1]).get("ok"),
      "at 0 speed there is nothing to spend")
bonded_v.update_token(hero.id, speed_ft=30, moved_ft=30)
check(not bonded_v.blink(hero.id, near[0], near[1]).get("ok"),
      "and none left once it is spent")

# ------------------------------------------------------------ 5. rescue
print("\n5. burning the link to save someone")
with Session(engine) as s:
    bonds.grant(s, session_id=SID, kind="atlas", owner_ref="Vex",
                holders=["Vex", "Kara", "Bram"], sees_through_cover=True,
                rescue_hp=30)
    spent = bonds.spend_rescue(s, SID, "Kara")
    check(spent is not None and spent.rescue_hp == 30,
          f"a downed holder can burn their share for {spent.rescue_hp if spent else 0} HP")
    check(bonds.spend_rescue(s, SID, "Kara") is None,
          "and only once — the map is gone")
    check(not bonds.sees_through(s, SID, "Kara", "Vex"),
          "burning it ends their link")
    check(bonds.sees_through(s, SID, "Vex", "Bram"),
          "but the others keep theirs")
    partners = bonds.rescue_partners(s, SID, "Kara", spent)
    check("bram" in partners and "vex" in partners and "kara" not in partners,
          f"and they may be pulled to another holder ({partners})")

    # A bond with no rescue lever cannot be burned for one.
    check(bonds.spend_rescue(s, SID, "Sable") is None,
          "a bond that grants no rescue offers none")

    lines = bonds.describe(s, SID)
    check(any("atlas" in l for l in lines), f"the DM block lists live bonds ({len(lines)})")

# The owner dying ends everything they granted.
with Session(engine) as s:
    n = bonds.revoke_all_for_owner(s, session_id=SID, owner_ref="Vex")
    check(n > 0 and not bonds.sees_through(s, SID, "Vex", "Bram"),
          f"when the maker falls, their bonds go with them ({n} ended)")

# ------------------------------------------------- 6. forced movement
print("\n6. being shoved is not the same as moving")
open_scene = plain_v.open_scene(SID, kind="combat", archetype="open",
                                width=20, height=12, seed=5, render_art=False)
for t in plain_v.tokens(open_scene.id):
    plain_v.remove_token(t.id)
pusher = plain_v.add_token(open_scene.id, name="Kara", x=5, y=6,
                           team="party", speed_ft=30)
victim = plain_v.add_token(open_scene.id, name="Gruk", x=7, y=6,
                           team="foe", speed_ft=30)

r = plain_v.shove(victim.id, away_from="Kara", distance_ft=15)
check(r["ok"] and (r["x"], r["y"]) == (10, 6) and r["moved_ft"] == 15,
      f"pushed 15 ft in a straight line ({r['x']},{r['y']})")
r = plain_v.shove(victim.id, toward="Kara", distance_ft=10)
check(r["ok"] and (r["x"], r["y"]) == (8, 6), f"and pulled back ({r['x']},{r['y']})")

# The target pays nothing: forced movement ignores their speed entirely.
check(plain_v.get_token(victim.id).moved_ft == 0,
      "a shove costs the target no movement of their own")
plain_v.update_token(victim.id, moved_ft=30)   # spent everything
r = plain_v.shove(victim.id, away_from="Kara", distance_ft=10)
check(r["ok"] and r["moved_ft"] == 10,
      "and works on someone who has already used all of theirs")

# It stops at the first thing in the way rather than pathing around it.
r = plain_v.shove(victim.id, away_from="Kara", distance_ft=500)
check(r["hit_something"] and "edge" in r["stopped_by"],
      f"a long push stops at the map edge ({r['stopped_by']})")
blocker = plain_v.add_token(open_scene.id, name="Wall of Meat", x=6, y=6,
                            team="foe")
r = plain_v.shove(victim.id, to_square=(0, 6), distance_ft=500)
check(r["stopped_by"] == "Wall of Meat",
      f"and against a creature standing in the way ({r['stopped_by']})")
check(r["x"] == 7 and r["y"] == 6,
      f"stopping BEFORE its square, not on it ({r['x']},{r['y']})")

r = plain_v.shove(victim.id, away_from="Nobody At All", distance_ft=10)
check(not r["ok"], "a push with nothing to push from is refused")

# Token state the board has to know about mid-fight.
plain_v.update_token(victim.id, size="large", movement_mode="fly", hidden=True)
t = plain_v.get_token(victim.id)
check(t.size == "large" and t.movement_mode == "fly" and t.hidden,
      "a creature can grow, take to the air and go unseen mid-fight")

print("\nFAILS:", fails or "none")
sys.exit(1 if fails else 0)
