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

# ------------------------------------- 7. held fast, knocked down, swapped
print("\n7. conditions the BOARD has to enforce")
held = plain_v.open_scene(SID, kind="combat", archetype="open",
                          width=20, height=12, seed=11, render_art=False)
for t in plain_v.tokens(held.id):
    plain_v.remove_token(t.id)
hg = plain_v.grid_of(held)


def clear_run(y, x0, n):
    """A run of n plain (cost-1) squares on row y, or None."""
    for x in range(x0, held.width - n):
        if all(hg.tile_at(x + i, y).code == "g" for i in range(n)):
            return x
    return None


# A row with plenty of ordinary ground: the drag and crawl costs below are
# checked to the foot, so difficult terrain in the path would falsify them.
row_y = next((y for y in range(held.height) if clear_run(y, 1, 12) is not None), 3)
x0 = clear_run(row_y, 1, 12)
ogre = plain_v.add_token(held.id, name="Ogre", x=x0, y=row_y, team="foe",
                         speed_ft=40, reach_ft=10)
hero = plain_v.add_token(held.id, name="Kara", x=x0 + 1, y=row_y,
                         team="party", speed_ft=30)
far = plain_v.add_token(held.id, name="Distant Bram", x=held.width - 2,
                        y=(row_y + 5) % held.height, team="party", speed_ft=30)

r = plain_v.grapple(held.id, "Ogre", "Distant Bram")
check(not r["ok"], f"you can't grapple across the room ({r.get('reason','')[:40]})")
r = plain_v.grapple(held.id, "Ogre", "Kara")
check(r["ok"], f"but you can grapple what you can reach: {r.get('detail','')}")

plain_v.start_turn(held.id, token_id=hero.id)
r = plain_v.move_token(hero.id, x0 + 3, row_y)
check(not r["ok"] and "Speed is 0" in r.get("reason", ""),
      f"a grappled creature can't walk away ({r.get('reason','')[:48]})")
check(plain_v.move_token(hero.id, x0 + 3, row_y, teleport=True).get("ok"),
      "but teleporting out still works — a grapple holds you, not your magic")
check(plain_v.find_token(held.id, "Kara").grappled_by is None,
      "and blinking clear of his reach breaks the hold behind you")

# Back within reach, and taken hold of again.
plain_v.move_token(hero.id, x0 + 1, row_y, teleport=True)
plain_v.grapple(held.id, "Ogre", "Kara")

# The grappler drags his captive, at half speed.
plain_v.start_turn(held.id, token_id=ogre.id)
r = plain_v.move_token(ogre.id, x0 + 3, row_y)          # 15 ft, budget 20
check(r.get("ok") and r.get("dragged") == ["Kara"],
      f"the grappler hauls his captive along ({r.get('dragged')})")
k = plain_v.find_token(held.id, "Kara")
check(max(abs(k.x - (x0 + 3)), abs(k.y - row_y)) <= 1,
      f"who ends up beside him ({k.x},{k.y})")
plain_v.start_turn(held.id, token_id=ogre.id)
r = plain_v.move_token(ogre.id, x0 + 8, row_y)          # 25 ft > halved 20
check(not r.get("ok") and "hauling" in r.get("reason", ""),
      f"and can only go half as far while doing it ({r.get('reason','')[:58]})")

# Dragged stays in reach; SHOVED out of it breaks the hold.
plain_v.shove(hero.id, away_from="Ogre", distance_ft=30)
check(plain_v.find_token(held.id, "Kara").grappled_by is None,
      "shoved beyond his reach, the grapple breaks")

# Restrained: the same Speed 0, a different cause.
k = plain_v.find_token(held.id, "Kara")
plain_v.set_restrained(held.id, "Kara", True)
plain_v.start_turn(held.id, token_id=hero.id)
r = plain_v.move_token(hero.id, k.x, k.y + 1)
check(not r["ok"] and "restrained" in r.get("reason", ""),
      "a restrained creature is going nowhere either")
plain_v.set_restrained(held.id, "Kara", False)
check(plain_v.move_token(hero.id, k.x, k.y + 1).get("ok"),
      "and moves again once it's cut away")

# Prone: crawling costs double; standing costs half your Speed.
plain_v.move_token(hero.id, x0 + 1, row_y, teleport=True)
plain_v.start_turn(held.id, token_id=hero.id)
plain_v.go_prone(held.id, "Kara")
r = plain_v.move_token(hero.id, x0 + 5, row_y)          # 20 ft ground = 40 crawling
check(not r.get("ok") and "double" in r.get("reason", ""),
      f"crawling 20 ft costs 40 and she has 30 ({r.get('reason','')[:52]})")
r = plain_v.move_token(hero.id, x0 + 4, row_y)          # 15 ft ground = 30 crawling
check(r.get("ok") and r.get("cost_ft") == 30,
      f"crawling 15 ft costs 30 ({r.get('cost_ft')})")
plain_v.start_turn(held.id, token_id=hero.id)
r = plain_v.stand_up(held.id, "Kara")
check(r["ok"] and r["cost_ft"] == 15, f"standing costs half her Speed ({r.get('cost_ft')})")
check(not plain_v.find_token(held.id, "Kara").prone, "and she is on her feet")
check(not plain_v.stand_up(held.id, "Kara")["ok"], "standing twice is refused")
plain_v.go_prone(held.id, "Kara")
plain_v.update_token(hero.id, moved_ft=30)
check(not plain_v.stand_up(held.id, "Kara")["ok"],
      "and you can't stand with no movement left")

# Swapping places.
plain_v.update_token(hero.id, prone=False, moved_ft=0)
k = plain_v.find_token(held.id, "Kara")
b = plain_v.find_token(held.id, "Distant Bram")
before = ((k.x, k.y), (b.x, b.y))
r = plain_v.swap(held.id, "Kara", "Distant Bram")
k2 = plain_v.find_token(held.id, "Kara")
b2 = plain_v.find_token(held.id, "Distant Bram")
check(r["ok"] and (k2.x, k2.y) == before[1] and (b2.x, b2.y) == before[0],
      f"two creatures change places ({before} -> {((k2.x, k2.y), (b2.x, b2.y))})"
      + ("" if r["ok"] else f" — {r.get('reason')}"))
check(not plain_v.swap(held.id, "Kara", "Kara")["ok"],
      "a creature can't swap with itself")
check(not plain_v.swap(held.id, "Kara", "Nobody")["ok"],
      "nor with someone who isn't there")

# ------------------------------------ 8. terrain: the grid IS the rule
print("\n8. the board enforces its own terrain, and tells the AI why")
from vtt.terrain import required_mode, tile_rule       # noqa: E402
from vtt.mapgen import generate_map                     # noqa: E402
from vtt.art import build_map_prompt, layout_signature  # noqa: E402
from imagery.models import context_key                  # noqa: E402

terr = plain_v.open_scene(SID, kind="combat", archetype="open",
                          width=12, height=7, seed=1, render_art=False)
for t in plain_v.tokens(terr.id):
    plain_v.remove_token(t.id)
plain_v.set_terrain(terr.id, [(x, y) for x in range(12) for y in range(7)], ".")
plain_v.set_terrain(terr.id, [(2, 3)], "#")     # wall
plain_v.set_terrain(terr.id, [(5, 3)], "W")     # deep water
plain_v.set_terrain(terr.id, [(9, 3)], "x")     # chasm
walker = plain_v.add_token(terr.id, name="Kara", x=1, y=3, team="party", speed_ft=60)

for label, sq, needs in [("a wall", (2, 3), None), ("deep water", (5, 3), "swim"),
                         ("a chasm", (9, 3), "fly")]:
    plain_v.start_turn(terr.id, token_id=walker.id)
    r = plain_v.move_token(walker.id, sq[0], sq[1])
    check(not r["ok"], f"a walker cannot enter {label}")
    check(r.get("needs_mode") == needs,
          f"...and the refusal names the remedy ({r.get('needs_mode')})")
    plain_v.update_token(walker.id, x=1, y=3)

# The medium a square DEMANDS is adopted, so nothing has to be told twice.
plain_v.update_token(walker.id, movement_mode="swim", x=4, y=3)
plain_v.start_turn(terr.id, token_id=walker.id)
r = plain_v.move_token(walker.id, 5, 3)
check(r["ok"], "a swimmer enters the water")
check(plain_v.get_token(walker.id).movement_mode == "swim",
      "and the board keeps them swimming")
plain_v.update_token(walker.id, movement_mode="fly", x=8, y=3)
plain_v.start_turn(terr.id, token_id=walker.id)
check(plain_v.move_token(walker.id, 9, 3)["ok"], "a flier crosses the chasm")
plain_v.update_token(walker.id, movement_mode="fly", x=1, y=3)
plain_v.start_turn(terr.id, token_id=walker.id)
check(not plain_v.move_token(walker.id, 2, 3)["ok"],
      "but nothing flies through a wall")

check(required_mode("W") == "swim" and required_mode("x") == "fly"
      and required_mode(".") is None,
      "squares declare the medium they demand")
board = plain_v.render(terr.id)
check("deep water (swimmers only" in board and "wall (impassable" in board,
      "the DM's board legend carries each tile's RULE, not just its name")
check("Terrain is enforced" in board,
      "and says plainly that the board will hold them to it")

# ---------------------------------- 9. one picture per room per condition
print("\n9. battlemap art is reused until something actually changes")
gen = generate_map("cave", width=20, height=15, seed=42)
sig = layout_signature(gen.grid, gen.archetype, gen.seed)


def bucket(**kw):
    _, _, ctx = build_map_prompt(gen, name="Sunken Shrine", **kw)
    return context_key(ctx)


summer = bucket(biome="woodland", lighting="bright", conditions="summer")
check(bucket(biome="woodland", lighting="bright", conditions="summer") == summer,
      "the same room in the same conditions reuses one picture")
check(bucket(biome="woodland", lighting="bright", conditions="winter, snow") != summer,
      "the same room in snow earns its own")
check(bucket(biome="woodland", lighting="dark", conditions="summer") != summer,
      "and by night, its own again")
check(bucket(biome="woodland", lighting="dim", conditions="indoors")
      == bucket(biome="woodland", lighting="dim", conditions="indoors"),
      "an interior's bucket is stable — no repaint when the weather turns")

# Terrain changing IS the thing that invalidates the picture.
before = layout_signature(gen.grid, gen.archetype, gen.seed)
gen.grid.set(5, 5, "#")
check(layout_signature(gen.grid, gen.archetype, gen.seed) != before,
      "a changed square changes the layout signature, so the art regenerates")

# ------------------------------------------- 10. height is a real distance
print("\n10. the board is three-dimensional")
sky = plain_v.open_scene(SID, kind="combat", archetype="open",
                         width=16, height=10, seed=2, render_art=False)
for t in plain_v.tokens(sky.id):
    plain_v.remove_token(t.id)
foot = plain_v.add_token(sky.id, name="Fighter", x=5, y=5, team="party",
                         speed_ft=30, reach_ft=5)
drake = plain_v.add_token(sky.id, name="Dragon", x=5, y=5, team="foe",
                          speed_ft=80, movement_mode="fly", elevation_ft=100)
check(plain_v.measure(sky.id, "Fighter", "Dragon") == 100,
      f"a dragon 100 ft overhead is 100 ft away "
      f"({plain_v.measure(sky.id, 'Fighter', 'Dragon')} ft)")
plain_v.update_token(drake.id, elevation_ft=0)
check(plain_v.measure(sky.id, "Fighter", "Dragon") <= 10,
      "and adjacent again once it lands")

# Height alone puts a mover out of reach.
plain_v.remove_token(drake.id)
wy = plain_v.add_token(sky.id, name="Wyvern", x=4, y=5, team="foe",
                       speed_ft=80, movement_mode="fly", elevation_ft=60)
plain_v.start_turn(sky.id, token_id=wy.id)
r = plain_v.move_token(wy.id, 10, 5)
check(r.get("ok") and not r.get("opportunity"),
      f"flying 60 ft overhead provokes nothing ({[o['name'] for o in r.get('opportunity', [])]})")

# Areas are flat squares, so height has to exclude what they'd otherwise catch.
plain_v.remove_token(wy.id)
roc = plain_v.add_token(sky.id, name="Roc", x=6, y=5, team="foe",
                        movement_mode="fly", elevation_ft=60)
burst = plain_v.add_effect(sky.id, "Fireball", kind="area", shape="sphere",
                           x=5, y=5, radius_ft=20)
caught = {t.name for t in plain_v.tokens_in_effect(burst.id)}
check("Fighter" in caught and "Roc" not in caught,
      f"a fireball on the ground misses a roc 60 ft up ({sorted(caught)})")
plain_v.update_token(roc.id, elevation_ft=10)
caught = {t.name for t in plain_v.tokens_in_effect(burst.id)}
check("Roc" in caught, f"but catches it at 10 ft ({sorted(caught)})")

# Grapple reach is 3D too.
plain_v.update_token(roc.id, elevation_ft=60)
check(not plain_v.grapple(sky.id, "Fighter", "Roc")["ok"],
      "you can't grapple something sixty feet above you")
# Put it genuinely adjacent: add_token relocates off a blocked square, and
# update_token refuses x/y on purpose, so the position comes from the board.
plain_v.update_token(roc.id, elevation_ft=0)
ft_tok = plain_v.find_token(sky.id, "Fighter")
for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
    if plain_v.move_token(roc.id, ft_tok.x + dx, ft_tok.y + dy,
                          teleport=True).get("ok"):
        break
res = plain_v.grapple(sky.id, "Fighter", "Roc")
check(res["ok"], f"but you can once it's on the ground beside you "
                 f"({res.get('reason', '')})")

# And the pure geometry, so the rule itself is pinned.
from vtt import geometry as _geo                        # noqa: E402
check(_geo.distance_ft((0, 0), (0, 0), dz_ft=100) == 100,
      "straight up is a real distance")
check(_geo.distance_ft((0, 0), (3, 0)) == 15
      and _geo.distance_ft((0, 0), (3, 0), dz_ft=5) == 15,
      "and folds into the horizontal one rather than adding to it")

print("\nFAILS:", fails or "none")
sys.exit(1 if fails else 0)
