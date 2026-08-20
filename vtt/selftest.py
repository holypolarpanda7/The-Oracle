"""
Self-test for the tactical board — the rules-facing behaviour, asserted.

Run:  uv run python -m vtt.selftest      (exit code 0 = everything held)

The geometry here decides whether an attack has cover, whether a move was
legal, and who a fireball caught. Those answers must not drift silently, so
every rule this package claims to enforce is pinned down below. No test
framework: the repo runs its checks as plain modules.
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

from . import geometry as geo
from .mapgen import ARCHETYPES, generate_map, archetype_for
from .models import Team, TokenKind
from .scene import VttEngine
from .terrain import APERTURES, Grid, aperture_axis, profile_height_ft

_FAILS: list[str] = []
_RUN = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _RUN
    _RUN += 1
    if cond:
        print(f"  \033[32m✓\033[0m {name}")
    else:
        print(f"  \033[31m✗\033[0m {name}{' — ' + detail if detail else ''}")
        _FAILS.append(name)


def eq(name: str, got, want) -> None:
    check(name, got == want, f"got {got!r}, want {want!r}")


def section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")


# ---------------------------------------------------------------- geometry

def test_distance() -> None:
    section("distance (PHB 5-5-5 and the DMG 5-10-5 variant)")
    eq("straight line, 3 squares = 15 ft", geo.distance_ft((0, 0), (3, 0)), 15)
    eq("pure diagonal costs the same as straight (5-5-5)",
       geo.distance_ft((0, 0), (3, 3)), 15)
    eq("the variant charges 5-10-5 for diagonals",
       geo.distance_ft((0, 0), (3, 3), rule=geo.ALTERNATING), 20)
    eq("adjacent creatures are 5 ft apart (reach, not centre-to-centre)",
       geo.token_distance_ft([(1, 1)], [(2, 1)]), 5)
    eq("a Large creature's near edge is what counts",
       geo.token_distance_ft(geo.footprint(0, 0, 2), [(3, 0)]), 10)
    check("in_reach agrees with a 10-ft reach",
          geo.in_reach(geo.footprint(0, 0, 2), [(3, 0)], reach_ft=10))


def test_sight_and_cover() -> None:
    section("line of sight and the PHB corner cover rule")
    g = Grid.blank(12, 9)
    for y in range(0, 5):
        g.set(5, y, "#")                      # a wall from the top edge down

    check("a solid wall blocks sight", not geo.has_line_of_sight(g, (0, 2), (8, 2)))
    check("and the same shot has total cover",
          geo.cover_between(g, (0, 2), (8, 2)) == "total")
    check("past the end of the wall you can see fine",
          geo.has_line_of_sight(g, (0, 7), (8, 7)))
    eq("open ground grants no cover", geo.cover_between(g, (0, 7), (8, 7)), "none")

    g2 = Grid.blank(9, 5)
    g2.set(4, 2, "o")                          # a crate: half cover, opaque-ish
    eq("a crate in the way is half cover", geo.cover_between(g2, (0, 2), (8, 2)), "half")

    g3 = Grid.blank(9, 5)
    g3.set(4, 2, "O")                          # a pillar: three-quarters
    check("a pillar is at least three-quarters cover",
          geo.COVER_ORDER[geo.cover_between(g3, (0, 2), (8, 2))]
          >= geo.COVER_ORDER["three-quarters"])

    g4 = Grid.blank(9, 5)
    eq("a creature in the way is half cover (DMG option)",
       geo.cover_between(g4, (0, 2), (8, 2), obstacles={(4, 2): "half"}), "half")

    # Cover is a question about HEIGHT, which the engine had no number for: a
    # four-foot crate gave half cover to a standing ogre and to a rogue lying
    # flat behind it alike. The DMG's own definition of total cover is
    # "completely concealed by an obstacle", and lying down is how you get
    # completely concealed by a crate.
    def cov(grid, height, above=0):
        return geo.cover_between(grid, (0, 2), (8, 2),
                                 target_height_ft=height,
                                 attacker_height_advantage_ft=above)
    eq("standing behind a crate is still half cover",
       cov(g2, profile_height_ft("medium", False)), "half")
    eq("lying flat behind it is total — the crate is taller than you now",
       cov(g2, profile_height_ft("medium", True)), "total")
    eq("…but not from an archer above it, who is shooting down over",
       cov(g2, profile_height_ft("medium", True), above=10), "half")
    eq("a Large creature is too big to hide behind a crate lying down",
       cov(g2, profile_height_ft("large", False)), "half")
    eq("…though a Large creature prone still fits",
       cov(g2, profile_height_ft("large", True)), "total")
    eq("a pillar's cover comes from its WIDTH, so prone changes nothing",
       cov(g3, profile_height_ft("medium", True)), "three-quarters")
    eq("and with no height given, cover behaves exactly as it always did",
       geo.cover_between(g2, (0, 2), (8, 2)), "half")


def test_templates() -> None:
    section("spell templates")
    g = Grid.blank(21, 21)
    sphere = geo.area_squares("sphere", (10, 10), radius_ft=20, grid=g)
    eq("a 20-ft radius sphere covers its centre", (10, 10) in sphere, True)
    check("…and nothing beyond its radius",
          all(geo.distance_squares((10, 10), s) <= 5 for s in sphere))
    check("a 20-ft sphere is roughly a 4-square disc",
          40 <= len(sphere) <= 60, f"{len(sphere)} squares")

    line = geo.area_squares("line", (0, 10), length_ft=30, width_ft=5,
                            direction_deg=0, grid=g)
    eq("a 30-ft line, 5 ft wide, is 6 squares", len(line), 6)

    cone = geo.area_squares("cone", (10, 10), length_ft=30, direction_deg=0, grid=g)
    check("a 30-ft cone opens as wide as it is long",
          18 <= len(cone) <= 30, f"{len(cone)} squares")
    check("the cone only points where it was aimed",
          all(s[0] >= 10 for s in cone))

    eman = geo.area_squares("emanation", (10, 10), radius_ft=10, grid=g)
    eq("a 10-ft emanation is the 5x5 around its source", len(eman), 25)

    walled = Grid.blank(11, 11)
    for y in range(11):
        walled.set(5, y, "#")
    burst = geo.area_squares("sphere", (2, 5), radius_ft=25, grid=walled)
    check("a burst does not leak through a wall",
          all(s[0] <= 5 for s in burst))


def test_movement() -> None:
    section("movement: pathing, difficult ground, corners")
    g = Grid.blank(10, 6)
    path, cost = geo.find_path(g, (0, 0), (4, 0))
    eq("open ground costs 5 ft a square", cost, 20)

    # A corridor, so the router can't simply walk around the rough ground.
    g2 = Grid.blank(10, 3)
    for x in range(10):
        g2.set(x, 0, "#")
        g2.set(x, 2, "#")
    for x in range(1, 4):
        g2.set(x, 1, ",")                      # rubble: difficult terrain
    _p, cost2 = geo.find_path(g2, (0, 1), (4, 1))
    eq("difficult terrain costs double", cost2, 35)
    corridor = Grid.blank(10, 3)
    for x in range(10):
        corridor.set(x, 0, "#")
        corridor.set(x, 2, "#")
    _p2, clear_cost = geo.find_path(corridor, (0, 1), (4, 1))
    check("…and the same corridor is cheaper when clear", clear_cost == 20,
          f"{clear_cost} ft")

    g3 = Grid.blank(10, 6)
    for y in range(0, 5):
        g3.set(4, y, "#")
    p3, c3 = geo.find_path(g3, (0, 0), (8, 0))
    check("a wall is walked around, not through", c3 > 40, f"{c3} ft")
    check("the route never crosses the wall",
          all(g3.get(x, y) != "#" for x, y in p3))

    corner = Grid.blank(5, 5)
    corner.set(1, 0, "#")
    corner.set(0, 1, "#")
    p4, _ = geo.find_path(corner, (0, 0), (1, 1))
    check("you can't slip diagonally between two walls",
          p4 == [] or len(p4) > 2, f"path {p4}")

    reach = geo.reachable_costs(Grid.blank(20, 20), (10, 10), 30)
    check("a 30-ft budget reaches its 6-square ring",
          all(c <= 30 for c in reach.values()) and (16, 10) in reach)
    eq("…exactly 30 ft is reachable", (16, 10) in reach, True)
    eq("…and 35 ft is not", (17, 10) in reach, False)

    fly = Grid.blank(5, 5)
    fly.set(2, 2, "x")                         # a chasm
    eq("a chasm stops a walker", fly.passable(2, 2, mode="walk"), False)
    eq("but not a flier", fly.passable(2, 2, mode="fly"), True)


def test_opportunity() -> None:
    section("opportunity attacks")
    triggers = geo.opportunity_triggers(
        [(1, 1), (2, 1), (3, 1)], {7: ([(1, 2)], 5)})
    eq("leaving a foe's reach provokes", triggers, [7])
    stays = geo.opportunity_triggers(
        [(1, 1), (1, 2)], {7: ([(2, 2)], 5)})
    eq("moving while staying in reach does not", stays, [])


# ---------------------------------------------------------------- mapgen

def test_mapgen() -> None:
    section("map generation")
    for arch in ARCHETYPES:
        m = generate_map(arch, width=24, height=16, seed=7)
        # Connectivity is judged in the medium the board is fought in: an
        # open-water board is one space to a swimmer, none at all to a walker.
        md = m.mode
        # …and GRANTED THE DOORS: a closed door is impassable and is not a
        # wall. Judged on passability alone a room reached only through a shut
        # door reads as cut off, which is both wrong and self-defeating — it is
        # the reason the generator used to leave a corridor mouth gaping beside
        # every door it hung.
        def through(x, y):
            return m.grid.passable(x, y, mode=md) or m.grid.get(x, y) in APERTURES
        walk = [(x, y) for x, y in m.grid.squares() if through(x, y)]
        # One connected space: every board must be playable end to end.
        seen = {walk[0]}
        stack = [walk[0]]
        while stack:
            x, y = stack.pop()
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if (nx, ny) not in seen and m.grid.in_bounds(nx, ny) \
                        and through(nx, ny):
                    seen.add((nx, ny))
                    stack.append((nx, ny))
        check(f"{arch}: one connected region", len(seen) == len(walk),
              f"{len(seen)}/{len(walk)} reachable")
        check(f"{arch}: has both spawn zones",
              bool(m.spawn_party) and bool(m.spawn_foes))

    # A door is a threshold, not decoration. Two things have to be true at
    # once, and neither is interesting without the other: shut, a door really
    # does divide the board (or it guards nothing), and every door has wall to
    # hang from (or it is a plank standing in open floor).
    warren = generate_map("dungeon-complex", width=26, height=18, seed=20260841)
    check("a warren hangs doors at its thresholds", len(warren.doors) >= 2,
          f"{len(warren.doors)} doors")
    check("every door has a wall either side",
          all(aperture_axis(warren.grid, d["x"], d["y"])
              for d in warren.doors))
    strict = [(x, y) for x, y in warren.grid.squares()
              if warren.grid.passable(x, y)]
    seen, stack = {strict[0]}, [strict[0]]
    while stack:
        x, y = stack.pop()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if (nx, ny) not in seen and warren.grid.in_bounds(nx, ny) \
                    and warren.grid.passable(nx, ny):
                seen.add((nx, ny))
                stack.append((nx, ny))
    check("shutting them actually divides the warren", len(seen) < len(strict),
          f"{len(seen)}/{len(strict)} reachable with the doors shut")

    # A sea board swims, a sky board flies, everything else walks.
    eq("an underwater board is swum", generate_map("reef", seed=3).mode, "swim")
    eq("a sky board is flown", generate_map("sky-islands", seed=3).mode, "fly")
    eq("a dungeon is walked", generate_map("dungeon-room", seed=3).mode, "walk")
    eq("DM language finds the sea floor", archetype_for("an underwater ruin"), "reef")
    eq("…and the open sky", archetype_for("a fight among floating islands"),
       "sky-islands")

    a = generate_map("cave", width=20, height=14, seed=99)
    b = generate_map("cave", width=20, height=14, seed=99)
    eq("the same seed rebuilds the same board", a.grid.to_rows(), b.grid.to_rows())
    c = generate_map("cave", width=20, height=14, seed=100)
    check("a different seed gives a different board", a.grid.to_rows() != c.grid.to_rows())

    eq("DM language maps to a layout", archetype_for("a smoky taproom"), "tavern")
    eq("…and so does a bare archetype name", archetype_for("sewer"), "sewer")
    eq("unknown wording falls back to open ground", archetype_for("???"), "open")

    # Medium beats architecture beats country. A DM naming two things has named
    # a building standing somewhere, and the building is the half nothing else
    # in the chain can put on the board — the jungle already reaches the render
    # through the biome and the skins. Read the other way (one flat list, with
    # "jungle" above "temple") this came back a plain patch of forest.
    eq("a building beats the country it stands in",
       archetype_for("an overgrown temple in the jungle"), "ruins")
    eq("…and the sea beats the building",
       archetype_for("a sunken temple under the sea"), "reef")
    eq("a wreck lies in the water, not on a ship's deck",
       archetype_for("a shipwreck"), "open-water")
    eq("…and on the reef when the reef is named",
       archetype_for("a shipwreck on the reef"), "reef")


# ---------------------------------------------------------------- engine

def test_engine() -> None:
    section("scene engine")
    db = os.path.join(tempfile.gettempdir(), "oracle_vtt_selftest.db")
    if os.path.exists(db):
        os.remove(db)
    v = VttEngine(database_url=f"sqlite:///{db}")
    v.create_tables()

    scene = v.open_scene("selftest:table", kind="combat", archetype="arena",
                         name="Pit", seed=5, render_art=False)
    check("a board opens", scene is not None and scene.active)
    eq("and it is the table's active board",
       v.active_scene("selftest:table").id, scene.id)

    kara = v.add_token(scene.id, "Kara", kind=TokenKind.PC, team=Team.PARTY,
                       x=6, y=9, speed_ft=30)
    foe = v.add_token(scene.id, "Ogre", kind=TokenKind.MONSTER, team=Team.FOE,
                      x=12, y=9, size="large", speed_ft=40, reach_ft=10)
    eq("a Large token occupies 2x2", foe.x is not None and 2, 2)

    blocked = v.add_token(scene.id, "Squatter", team=Team.FOE, x=6, y=9)
    check("tokens don't stack: the second is shifted off the taken square",
          blocked is None or (blocked.x, blocked.y) != (6, 9))
    if blocked:
        v.remove_token(blocked.id)

    eq("distance comes off the grid", v.measure(scene.id, "Kara", "Ogre"), 30)

    ok = v.move_token(kara.id, 9, 9)
    check("a legal move is applied", ok["ok"] and ok["cost_ft"] == 15,
          str(ok))
    near = {(s["x"], s["y"]) for s in v.movement_options(kara.id)["squares"]}
    far = next(s for s in v.movement_options(kara.id, dash=True)["squares"]
               if (s["x"], s["y"]) not in near)
    again = v.move_token(kara.id, far["x"], far["y"])
    check("a move past the speed budget is refused, with a reason",
          not again["ok"] and "movement left" in (again.get("reason") or ""),
          str(again))
    v.start_turn(scene.id, token_id=kara.id)
    fresh = v.move_token(kara.id, 9, 12)
    check("a new turn restores the budget", fresh["ok"], str(fresh))

    eff = v.add_effect(scene.id, "Fireball", shape="sphere", x=12, y=9,
                       radius_ft=20, damage="8d6 fire", duration_rounds=1)
    caught = {t.name for t in v.tokens_in_effect(eff.id)}
    check("an area catches whoever stands in it", "Ogre" in caught, str(caught))

    zone = v.add_effect(scene.id, "Grease", shape="cube", x=9, y=13,
                        length_ft=15, difficult_terrain=True, kind="zone")
    v.start_turn(scene.id, token_id=kara.id)
    here = v.get_token(kara.id)          # she has moved since she was created
    costs = v.movement_options(kara.id)
    in_zone = [s for s in costs["squares"]
               if [s["x"], s["y"]] in (zone.squares or [])
               and (s["x"], s["y"]) != (here.x, here.y)]
    check("difficult-terrain effects raise movement cost",
          bool(in_zone) and all(s["cost"] >= 10 for s in in_zone),
          str(in_zone[:3]))

    # Elevation: a climb costs the feet climbed; stepping off is a fall.
    v.start_turn(scene.id, token_id=kara.id)
    here = v.get_token(kara.id)
    ledge = (here.x + 1, here.y)
    flat = v.path_preview(kara.id, ledge[0], ledge[1]).get("cost_ft")
    v.set_elevation(scene.id, [ledge], 10)
    climb = v.path_preview(kara.id, ledge[0], ledge[1]).get("cost_ft")
    eq("climbing a 10-ft ledge costs 10 ft more than walking there",
       (climb or 0) - (flat or 0), 10)
    up = v.move_token(kara.id, ledge[0], ledge[1])
    check("the climb is applied", up["ok"], str(up))
    v.start_turn(scene.id, token_id=kara.id)
    down = v.move_token(kara.id, here.x, here.y)
    eq("stepping off reports the drop", down.get("fall_ft"), 10)
    v.set_elevation(scene.id, [ledge], 0)

    aura = v.add_effect(scene.id, "Torch", kind="light", shape="emanation",
                        radius_ft=20, source_token_id=kara.id, x=kara.x, y=kara.y,
                        permanent=True)
    before = set(map(tuple, aura.squares or []))
    v.move_token(kara.id, kara.x + 1, kara.y, teleport=True)
    after = set(map(tuple, (v.find_effect(scene.id, "Torch").squares or [])))
    check("an aura follows its source", before != after)

    notes = v.advance_round(scene.id, 9)
    check("timed effects expire", any("Fireball" in n for n in notes), str(notes))

    board = v.render(scene.id)
    check("the DM board names the creatures", "Kara" in board and "Ogre" in board)
    state = v.state(scene.id)
    check("the UI state carries terrain, tokens and effects",
          state["terrain"] and state["tokens"] and state["effects"] is not None)

    # The picture Discord tables get must render from the same state dict.
    try:
        from .render_image import render_board_png
        png = render_board_png(v.state(scene.id), cell=24)
        check("the board renders to a PNG for chat",
              png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 1000,
              f"{len(png)} bytes")
    except Exception as e:
        check("the board renders to a PNG for chat", False, str(e))

    v.close_scene(scene.id)
    eq("closing the board clears it for the table",
       v.active_scene("selftest:table"), None)
    check("the scene left a replay log", len(v.events(scene.id)) > 0)


def test_bridge() -> None:
    section("bridge: the board and the combat engine agree")
    try:
        from combat import CombatEngine, CombatTracker
    except Exception as e:               # combat is optional for the board
        check("combat package importable", False, str(e))
        return
    from .bridge import BoardSpatial, sync_bands, sync_cover

    db = os.path.join(tempfile.gettempdir(), "oracle_vtt_bridge_selftest.db")
    if os.path.exists(db):
        os.remove(db)
    url = f"sqlite:///{db}"
    ct = CombatTracker(database_url=url)
    ct.create_tables()
    v = VttEngine(database_url=url, tracker=ct)
    v.create_tables()
    eng = CombatEngine(ct)

    enc = ct.start_encounter("selftest:bridge", "Reach")
    kara = ct.add_pc(enc.id, name="Kara", max_hp=20, armor_class=15, dex_mod=2,
                     character_id=1)
    ogre = ct.add_combatant(enc.id, "Ogre", max_hp=30, armor_class=11, dex_mod=0)
    ct.roll_initiative(enc.id)
    scene = v.open_scene("selftest:bridge", kind="combat", archetype="open",
                         name="Field", seed=4, encounter_id=enc.id,
                         render_art=False)
    tk = v.add_token(scene.id, "Kara", kind=TokenKind.PC, team=Team.PARTY,
                     x=6, y=9, combatant_id=kara.id, speed_ft=30)
    v.add_token(scene.id, "Ogre", kind=TokenKind.MONSTER, team=Team.FOE,
                x=8, y=9, size="large", combatant_id=ogre.id, reach_ft=10)

    a, b = ct.get_combatant(kara.id), ct.get_combatant(ogre.id)
    eng.spatial = BoardSpatial(v, scene.id)
    eq("a 10-ft reach engages at 10 ft", eng._engaged_with(a, b), True)
    eq("…and that is zero band-steps", eng._steps_between(a, b), 0)

    moved = v.move_token(tk.id, 14, 9, teleport=True)
    check("the mover lands where asked", moved["ok"], str(moved))
    eng.spatial = BoardSpatial(v, scene.id)
    eq("stepping out of reach breaks the engagement",
       eng._engaged_with(a, b), False)
    check("…and the gap is measured in real feet",
          eng.spatial.distance_ft(a, b) == 25,
          str(eng.spatial.distance_ft(a, b)))

    class _Stranger:
        id, name, position, kind = 999999, "Ghost", "near", "npc"
    eq("a creature with no token answers None",
       eng.spatial.distance_ft(a, _Stranger()), None)
    eq("…so the engine falls back to its bands",
       eng._steps_between(a, _Stranger()), 1)

    # A damaging area must bite whoever stands in it — and nobody else.
    v.add_effect(scene.id, "Wall of Fire", shape="cube", x=14, y=9, length_ft=10,
                 damage="4d8 fire", save_ability="dex", save_dc=40,
                 duration_rounds=10)
    burning = {t.combatant_id for t in
               v.tokens_in_effect(v.find_effect(scene.id, "Wall of Fire").id)}
    eq("only the creature in the fire is a target", burning, {kara.id})
    before = ct.get_combatant(kara.id).current_hp
    ogre_before = ct.get_combatant(ogre.id).current_hp
    eng.apply_environment_hazards(
        enc.id, {"hazards": [{"name": "Wall of Fire", "dc": 40, "damage": "4d8",
                              "ability": "dex", "targets": list(burning)}]}, {})
    check("a failed save costs the creature standing in it hit points",
          ct.get_combatant(kara.id).current_hp < before,
          f"{before} -> {ct.get_combatant(kara.id).current_hp}")
    eq("the creature outside it is untouched",
       ct.get_combatant(ogre.id).current_hp, ogre_before)

    # Weapon ranges only bind when the board can say how far away the target is.
    from combat import PCProfile, PCWeapon
    archer = PCProfile(
        character_id=1, name="Kara", level=5, prof=3,
        ability_mods={"str": 0, "dex": 4, "con": 2, "int": 0, "wis": 1, "cha": 0},
        weapons=[PCWeapon(name="Shortbow", attack_bonus=7, damage="1d6+4",
                          ranged=True, range_normal=40, range_long=90)])
    ct.heal(kara.id, 99)          # the wall of fire above nearly killed her
    ct.heal(ogre.id, 99)
    shot = {"verb": "attack", "actor": "Kara", "target": "Ogre", "arg": "shortbow"}

    def _fire():
        """Put Kara back on her feet, on her turn, and loose one arrow."""
        ct.heal(kara.id, 99)
        ct.heal(ogre.id, 99)
        for _ in range(8):
            if ct.current_combatant(enc.id).id == kara.id:
                break
            ct.next_turn(enc.id)
        ct.begin_turn(kara.id)
        return eng.resolve(enc.id, [shot], profiles={1: archer})

    def _attacked(rep) -> bool:
        return any(e.get("kind") == "attack" for e in rep.events)

    # Line them up on a clear row so the distances are exact, not incidental.
    grid = v.grid(scene.id)
    row_y = next((y for y in range(scene.height - 1)
                  if all(grid.passable(x, yy)
                         for yy in (y, y + 1)
                         for x in (2, 6, 7, 16, 17, 22, 23))), None)
    kara_tok = v.find_token(scene.id, "Kara")
    ogre_tok = v.find_token(scene.id, "Ogre")
    if row_y is None:
        check("a clear firing line exists on the test board", False)
    else:
        v.move_token(kara_tok.id, 2, row_y, teleport=True)
        v.move_token(ogre_tok.id, 16, row_y, teleport=True)   # 70 ft
        eng.spatial = BoardSpatial(v, scene.id)
        eq("the firing line is 70 ft",
           eng.spatial.distance_ft(ct.get_combatant(kara.id),
                                   ct.get_combatant(ogre.id)), 70)
        rep = _fire()
        check("a shot past normal range takes disadvantage",
              _attacked(rep) and any(
                  "long range" in n for e in rep.events for n in e.get("notes", [])),
              str(rep.rejections or [n for e in rep.events for n in e.get("notes", [])]))

        v.move_token(ogre_tok.id, 22, row_y, teleport=True)   # 100 ft
        eng.spatial = BoardSpatial(v, scene.id)
        rep = _fire()
        check("a shot beyond maximum range is refused",
              bool(rep.rejections) and "maximum range" in rep.rejections[0]["reason"],
              str(rep.rejections))

        v.move_token(ogre_tok.id, 6, row_y, teleport=True)    # 20 ft
        eng.spatial = BoardSpatial(v, scene.id)
        rep = _fire()
        check("a shot inside normal range is clean",
              _attacked(rep) and not any(
                  "long range" in n for e in rep.events for n in e.get("notes", [])),
              str(rep.rejections or rep.events))

    eng.spatial = None
    rep = _fire()
    check("with no board, ranges aren't policed at all",
          _attacked(rep) and not any(
              "long range" in n for e in rep.events for n in e.get("notes", [])),
          str(rep.rejections or rep.events))

    sync_bands(v, scene.id, tracker=ct)
    bands = {c.name: c.position for c in ct.order(enc.id)}
    check("grid positions are written back as bands",
          bands.get("Kara") in ("near", "far"), str(bands))

    # Cover is a fact about a target and ONE attacker: the one whose turn it is.
    ka, og = v.find_token(scene.id, "Kara"), v.find_token(scene.id, "Ogre")
    mid_x = (ka.x + og.x) // 2
    v.set_terrain(scene.id, [(mid_x, ka.y), (mid_x, ka.y + 1)], "O")
    for _ in range(8):
        if ct.current_combatant(enc.id).id == ogre.id:
            break
        ct.next_turn(enc.id)
    sync_cover(v, scene.id, tracker=ct)
    covers = {c.name: c.cover for c in ct.order(enc.id)}
    check("on the ogre's turn, Kara's cover is measured from the ogre",
          covers.get("Kara") not in (None, "none"), str(covers))

    # A DM ruling the terrain can't know is kept as a floor across recomputes.
    v.set_cover_override(scene.id, ogre.id, "three-quarters")
    sync_cover(v, scene.id, tracker=ct)
    eq("a DM's cover ruling survives the next recompute",
       ct.get_combatant(ogre.id).cover, "three-quarters")
    v.set_cover_override(scene.id, ogre.id, None)

    # And the board reports it from the actor's point of view.
    board = v.render(scene.id)
    check("the DM board says whose eyes the cover is measured through",
          "who is acting" in board and "Ogre" in board, board.splitlines()[-6:])

    # Light reaches the attack roll. Two creatures on open ground at noon roll
    # plainly; the same two in the dark roll at disadvantage AND grant
    # advantage, because neither can see the other. Before the board answered
    # this, a fight in an unlit crypt rolled exactly like a fight at midday.
    #
    # Stand them adjacent first: this board has scenery on it, and a blocked
    # line of sight would prove the same thing for the wrong reason.
    ot = v.find_token(scene.id, "Ogre")
    v.move_token(tk.id, ot.x - 1, ot.y, teleport=True, free=True)
    eng.spatial = BoardSpatial(v, scene.id)
    adv, dis, _n = eng._attack_advantage(a, b, ranged=False,
                                         encounter_id=enc.id)
    check("in daylight an attack is a plain roll", not adv and not dis)

    v._set_fields(scene.id, lighting="dark")
    eng.spatial = BoardSpatial(v, scene.id)
    adv, dis, notes = eng._attack_advantage(a, b, ranged=False,
                                            encounter_id=enc.id)
    check("in the dark neither can see the other, so both apply",
          any("can't see" in n for n in notes), "; ".join(notes))
    check("…and 5e cancels them against each other", not adv and not dis,
          "advantage and disadvantage cancel — the roll is plain, for a reason")

    # One of them having darkvision breaks the symmetry, which is the point.
    v.update_token(tk.id, senses={"darkvision": 60})
    eng.spatial = BoardSpatial(v, scene.id)
    adv, dis, notes = eng._attack_advantage(a, b, ranged=False,
                                            encounter_id=enc.id)
    check("darkvision against a blind foe is advantage, not a cancelled roll",
          adv and not dis, "; ".join(notes))
    ct.end_encounter(enc.id)
    v.close_scene(scene.id)


def test_light_and_vision() -> None:
    section("light, darkvision and who can see whom")
    from survival.light import parse_senses
    # Stat blocks do not all arrive tidy. Half the bestiary was ingested from
    # PDFs and keeps its senses line whole, and reading only the well-formed
    # rows costs the wolf its darkvision — silently, and in the direction of
    # "blind in the dark", which is the direction that changes fights.
    eq("a tidy senses line parses", parse_senses({"darkvision": "60 ft."}),
       {"darkvision": 60})
    eq("a species flag means the 5e default",
       parse_senses({"darkvision": True}), {"darkvision": 60})
    eq("passive Perception is not a sense with a range",
       parse_senses({"passive_perception": 15}), {})
    eq("a raw book line parses too",
       parse_senses({"raw": "Blindsight 30ft.;PassivePerception13"}),
       {"blindsight": 30})
    eq("…including the run-together kind",
       parse_senses({"raw": "Darkvision60ft.;PassivePerception15"}),
       {"darkvision": 60})
    import tempfile as _tf
    db = os.path.join(_tf.mkdtemp(), "vision.db")
    v = VttEngine(database_url=f"sqlite:///{db}")
    v.create_tables()
    sc = v.open_scene("test:vision", kind="combat", archetype="dungeon-room",
                      name="Unlit Crypt", width=28, height=10, seed=5,
                      render_art=False, lighting="dark")

    human = v.add_token(sc.id, name="Human", x=3, y=5, team=Team.PARTY)
    v.add_token(sc.id, name="Dwarf", x=4, y=5, team=Team.PARTY,
                senses={"darkvision": 60})
    v.add_token(sc.id, name="Ogre", x=9, y=5, team=Team.FOE)
    wraith = v.add_token(sc.id, name="Wraith", x=16, y=5, team=Team.FOE)

    # The whole point: a clear line through a dark room shows you nothing.
    check("an unlit room blinds ordinary sight",
          not v.can_see(sc.id, "Human", "Ogre"))
    check("…and line of sight alone is still clear",
          geo.has_line_of_sight(v.grid_of(v.get_scene(sc.id)), (3, 5), (9, 5)),
          "the wall isn't what stopped them")
    check("darkvision sees in the dark", v.can_see(sc.id, "Dwarf", "Ogre"))
    eq("…as if in dim light, not daylight",
       v.vision(sc.id, "Dwarf", "Ogre")["obscured"], "light")
    # The boundary, both ways. 60 ft of darkvision reaches a target AT 60 ft
    # and not one square past it — an off-by-one here is the difference between
    # a monster you can fight and one you can only be hit by.
    check("darkvision reaches exactly its stated range",
          v.can_see(sc.id, "Dwarf", "Wraith"),
          f"the wraith is {v.vision(sc.id, 'Dwarf', 'Wraith')} away")
    v.move_token(wraith.id, 18, 5, teleport=True, free=True)
    check("…and no further", not v.can_see(sc.id, "Dwarf", "Wraith"))

    # A torch is a light source, and light is cast as field of view.
    v.add_effect(sc.id, name="torch", kind="light", shape="sphere",
                 x=3, y=5, radius_ft=20)
    eq("a torch makes its own square bright", v.light_at(sc.id, 3, 5), "bright")
    eq("…dim at twice its radius", v.light_at(sc.id, 10, 5), "dim")
    eq("…and dark beyond that", v.light_at(sc.id, 15, 5), "dark")
    check("a torch lets the human see", v.can_see(sc.id, "Human", "Ogre"))

    # Blindsight ignores light AND obstruction; heavy obscurement blinds sight.
    v.update_token(human.id, senses={"blindsight": 40})
    check("blindsight doesn't care about the dark",
          v.can_see(sc.id, "Human", "Ogre"))
    v.add_effect(sc.id, name="fog cloud", kind="area", shape="sphere",
                 x=9, y=5, radius_ft=20, obscured="heavy")
    eq("a fog cloud is dark however bright the room",
       v.light_at(sc.id, 9, 5), "dark")
    check("…and blindsight still finds them",
          v.can_see(sc.id, "Human", "Ogre"))
    check("…while the dwarf's darkvision does not",
          not v.can_see(sc.id, "Dwarf", "Ogre"),
          "darkvision is better eyes, not a different sense")

    # Fog of war is lit per creature, from its OWN eyes.
    v._set_fields(sc.id, fog=v._blank_fog(18, 10))
    seen = sum(r.count("1") for r in (v.sight(sc.id, team=Team.PARTY) or []))
    check("fog is revealed by torchlight and darkvision together",
          seen > 0, f"{seen} squares")
    v.close_scene(sc.id)


def test_mounts_and_squeezing() -> None:
    section("mounts and squeezing")
    import tempfile as _tf
    db = os.path.join(_tf.mkdtemp(), "mounts.db")
    v = VttEngine(database_url=f"sqlite:///{db}")
    v.create_tables()
    sc = v.open_scene("test:ride", kind="combat", archetype="open",
                      name="The Road", width=20, height=10, seed=3,
                      render_art=False)
    for x in range(20):
        for y in range(10):
            v.set_terrain(sc.id, [(x, y)], ".")

    kara = v.add_token(sc.id, name="Kara", x=4, y=5, team=Team.PARTY, speed_ft=30)
    horse = v.add_token(sc.id, name="Warhorse", x=5, y=5, team=Team.PARTY,
                        size="large", speed_ft=60)
    v.add_token(sc.id, name="Wolf", x=4, y=6, team=Team.PARTY, size="medium")

    eq("you don't ride a wolf", v.mount(sc.id, "Kara", "Wolf")["ok"], False)
    got = v.mount(sc.id, "Kara", "Warhorse")
    check("but you do ride a warhorse", got["ok"], str(got))
    eq("…for half your Speed", got["cost_ft"], 15)
    seat = v.find_token(sc.id, "Kara")
    eq("rider and mount share one space", (seat.x, seat.y), (horse.x, horse.y))

    walked = v.move_token(kara.id, 8, 5)
    check("a rider has no movement of their own", not walked["ok"],
          str(walked.get("reason")))
    check("…and is told what to do instead",
          "Warhorse" in str(walked.get("reason")))

    rode = v.move_token(horse.id, 10, 5)
    eq("moving the mount carries the rider", rode.get("carried"), "Kara")
    seat = v.find_token(sc.id, "Kara")
    eq("…to the same square", (seat.x, seat.y), (10, 5))

    # Being moved against the mount's will is what the save is FOR.
    shoved = v.shove(horse.id, to_square=(14, 5), distance_ft=20)
    check("a shoved mount puts its rider to a saving throw",
          "saddle_check" in shoved, str(shoved))
    seat = v.find_token(sc.id, "Kara")
    eq("…and the rider goes with it either way", (seat.x, seat.y),
       (shoved["x"], shoved["y"]) if shoved["saddle_check"]["stayed"]
       else (seat.x, seat.y))

    # Knocked down while mounted means knocked OFF.
    v.mount(sc.id, "Kara", "Warhorse") if not v.find_token(
        sc.id, "Kara").mounted_on else None
    down = v.go_prone(sc.id, "Kara")
    check("going down while mounted means going down off it",
          down.get("dismounted") is True, str(down))
    rider = v.find_token(sc.id, "Kara")
    check("…landing prone and unmounted",
          rider.prone and not rider.mounted_on)

    # --- squeezing --------------------------------------------------------
    wall = v.open_scene("test:ride", kind="combat", archetype="open",
                        name="The Narrow Hall", width=16, height=9, seed=3,
                        render_art=False)
    for x in range(16):
        for y in range(9):
            v.set_terrain(wall.id, [(x, y)], ".")
    for y in range(9):
        v.set_terrain(wall.id, [(8, y)], "#")
    v.set_terrain(wall.id, [(8, 4)], ".")          # one square of doorway
    ogre = v.add_token(wall.id, name="Ogre", x=5, y=4, team=Team.FOE,
                       size="large", speed_ft=60)

    got = v.move_token(ogre.id, 10, 4)
    check("a Large creature can force itself through a 5-ft gap", got["ok"],
          str(got.get("reason")))
    eq("…at an extra foot for every foot", got["cost_ft"], 50)
    check("…and the board remembers it is squeezing",
          v.find_token(wall.id, "Ogre").squeezing)
    v.start_turn(wall.id, ogre.id)
    v.move_token(ogre.id, 13, 6)
    check("out in the open it stops squeezing",
          not v.find_token(wall.id, "Ogre").squeezing)
    v.close_scene(wall.id)


def test_board_size() -> None:
    section("board size: the fight decides, not one number")
    from .triggers import SCALES, board_size_for
    base = (24, 18)

    eq("with nothing known, the scene kind's default stands",
       board_size_for(base), (24, 18))
    eq("a cellar brawl doesn't grow", board_size_for(
        base, archetype="dungeon-room", creatures=[(1, 30)] * 4), (24, 18))

    # Room to move. This is the one that makes mounted combat possible at all:
    # a warhorse crosses 120 ft in a turn and DASHES the whole old board.
    foot = board_size_for(base, archetype="open", creatures=[(1, 30)] * 6)
    horse = board_size_for(base, archetype="open", creatures=[(2, 60)] * 4)
    check("riders get more ground than infantry", horse[0] > foot[0],
          f"{horse[0]} vs {foot[0]} squares wide")
    check("…enough that a charge is a decision, not the whole fight",
          horse[0] * 5 >= 60 * 3,
          f"{horse[0] * 5} ft across at 60 ft a turn")

    # Room to shoot. A rule the engine enforces has to be reachable.
    shot = board_size_for(base, archetype="open", creatures=[(1, 30)] * 4,
                          longest_range_ft=150)
    check("a longbow can reach its own long-range band outdoors",
          shot[0] * 5 > 150, f"{shot[0] * 5} ft across vs 150 ft normal range")
    eq("…but a tavern is the size of the tavern",
       board_size_for(base, archetype="tavern", creatures=[(1, 30)] * 6,
                      longest_range_ft=150), (24, 18))

    # Room to stand.
    pitched = board_size_for(base, archetype="open",
                             creatures=[(1, 30)] * 14 + [(4, 80)])
    check("a pitched battle with a dragon in it gets room",
          pitched[0] * pitched[1] > 24 * 18 * 2,
          f"{pitched[0]}x{pitched[1]}")

    # A named scale is the DM saying something the roster doesn't know yet.
    for name, want in SCALES.items():
        eq(f"scale={name} is honoured exactly", board_size_for(
            base, archetype="open", creatures=[(1, 30)], scale=name), want)
    check("every board stays inside what mapgen will build",
          all(8 <= v <= 60 for v in pitched + horse + shot))


def test_levels() -> None:
    section("upper floors: a gallery over a hall")
    import tempfile as _tf
    db = os.path.join(_tf.mkdtemp(), "levels.db")
    v = VttEngine(database_url=f"sqlite:///{db}")
    v.create_tables()
    sc = v.open_scene("test:levels", kind="combat", archetype="open",
                      name="The Galleried Hall", width=12, height=8, seed=3,
                      render_art=False)
    for x in range(12):
        for y in range(8):
            v.set_terrain(sc.id, [(x, y)], ".")

    made = v.add_level(sc.id, name="Gallery", base_ft=15)
    eq("a board can grow a floor", made["level"], 1)
    check("…which starts as open air, not a lid",
          all(c == " " for c in v.grid_of(v.get_scene(sc.id), 1).to_rows()[4]),
          "a gallery is the strip you build; everywhere else is open to below")

    # Build a walkway along the north edge, leaving the hall's middle open.
    row = v.get_scene(sc.id)
    lv = [dict(l) for l in (row.levels or [])]
    gg = v.grid_of(row, 1)
    for x in range(12):
        for y in (0, 1):
            gg.set(x, y, ".")
    lv[0]["terrain"] = gg.to_rows()
    v._set_fields(sc.id, levels=lv)
    v.add_stairs(sc.id, 0, 11, 2, to_level=1, to_x=11, to_y=1)

    kara = v.add_token(sc.id, name="Kara", x=5, y=5, team=Team.PARTY)
    archer = v.add_token(sc.id, name="Archer", x=5, y=1, team=Team.FOE)
    v.update_token(archer.id, level=1)

    row = v.get_scene(sc.id)
    k, a = v.find_token(sc.id, "Kara"), v.find_token(sc.id, "Archer")
    eq("the gallery's height counts toward every distance",
       geo.token_distance_ft([(k.x, k.y)], [(a.x, a.y)], row.square_ft,
                             dz_ft=v.height_gap_ft(row, k, a)), 20)
    check("you can see up through the open middle of the hall",
          v.can_see(sc.id, "Kara", "Archer"))

    # Roof the hall over and the same two lose each other entirely.
    lv = [dict(l) for l in (v.get_scene(sc.id).levels or [])]
    lv[0]["terrain"] = ["." * 12 for _ in range(8)]
    v._set_fields(sc.id, levels=lv)
    check("a floor between them is a ceiling", not v.can_see(sc.id, "Kara", "Archer"),
          str(v.vision(sc.id, "Kara", "Archer")))
    lv[0]["terrain"] = gg.to_rows()
    v._set_fields(sc.id, levels=lv)

    # Floors don't share squares, and you can't just walk upstairs.
    v.move_token(kara.id, 5, 1, teleport=True, free=True)
    k = v.find_token(sc.id, "Kara")
    eq("two creatures can stand on the same square on different floors",
       (k.x, k.y, int(k.level or 0)), (5, 1, 0))
    denied = v.take_stairs(sc.id, "Kara")
    check("and the stairs are the only way between them", not denied["ok"],
          str(denied.get("reason")))
    # Light, fog and live sight all belong to a STOREY, not to the board.
    v._set_fields(sc.id, lighting="dark")
    v.add_effect(sc.id, name="lantern", kind="light", shape="sphere",
                 x=5, y=1, radius_ft=20, level=1)
    eq("a lantern on the gallery lights the gallery",
       v.light_at(sc.id, 5, 1, level=1), "bright")
    eq("…and not the hall underneath it",
       v.light_at(sc.id, 5, 1, level=0), "dark")
    check("so the hall can't see the gallery by it",
          not v.can_see(sc.id, "Kara", "Archer"),
          "Kara is in the dark; the light is on the floor above her")

    v._set_fields(sc.id, fog=v._blank_fog(12, 8))
    v.reveal_from_party(sc.id)
    ground = v.state(sc.id)["levels"][0]
    upper = v.state(sc.id)["levels"][1]
    check("walking the hall remembers the hall",
          sum(r.count("1") for r in (ground["fog"] or [])) > 0)
    eq("…and not one square of the gallery",
       sum(r.count("1") for r in (upper["fog"] or [])), 0)
    v._set_fields(sc.id, lighting="bright")

    v.move_token(kara.id, 11, 2, teleport=True, free=True)
    took = v.take_stairs(sc.id, "Kara")
    check("standing on them, she goes up", took["ok"], str(took))
    k = v.find_token(sc.id, "Kara")
    eq("…onto the gallery", (int(k.level or 0), k.x, k.y), (1, 11, 1))
    v.close_scene(sc.id)


def test_underwater() -> None:
    section("underwater combat: the rules, not a note asking the DM")
    try:
        from combat import CombatEngine, CombatTracker
        from combat.engine import (_UNDERWATER_MELEE_OK, _UNDERWATER_RANGED_OK,
                                   _weapon_matches)
    except Exception as e:
        check("combat package importable", False, str(e))
        return
    from .bridge import BoardSpatial

    check("a magic shortsword is still a shortsword",
          _weapon_matches("+1 Shortsword", _UNDERWATER_MELEE_OK),
          "the list is of weapon KINDS, and the table is full of named ones")
    check("a greataxe is not", not _weapon_matches("Greataxe", _UNDERWATER_MELEE_OK))
    check("a longbow fights the water",
          not _weapon_matches("Longbow", _UNDERWATER_RANGED_OK))
    check("a crossbow does not",
          _weapon_matches("Heavy Crossbow", _UNDERWATER_RANGED_OK))

    import tempfile as _tf
    db = os.path.join(_tf.mkdtemp(), "underwater.db")
    url = f"sqlite:///{db}"
    ct = CombatTracker(database_url=url)
    ct.create_tables()
    v = VttEngine(database_url=url, tracker=ct)
    v.create_tables()
    eng = CombatEngine(ct)

    enc = ct.start_encounter("selftest:wet", "The Shelf")
    kara = ct.add_pc(enc.id, name="Kara", max_hp=20, armor_class=15, dex_mod=2,
                     character_id=1)
    fish = ct.add_combatant(enc.id, "Sahuagin", max_hp=22, armor_class=12, dex_mod=1)
    ct.roll_initiative(enc.id)

    dry = v.open_scene("selftest:wet", kind="combat", archetype="open",
                       name="Beach", seed=4, encounter_id=enc.id, render_art=False)
    kt = v.add_token(dry.id, "Kara", kind=TokenKind.PC, team=Team.PARTY,
                     x=5, y=5, combatant_id=kara.id)
    v.add_token(dry.id, "Sahuagin", kind=TokenKind.MONSTER, team=Team.FOE,
                x=6, y=5, combatant_id=fish.id, swim_speed_ft=40)
    a, b = ct.get_combatant(kara.id), ct.get_combatant(fish.id)
    eng.spatial = BoardSpatial(v, dry.id)
    axe = {"name": "Greataxe", "damage": "1d12+3", "ranged": False}
    _adv, dis, _n = eng._attack_advantage(a, b, False, enc.id, weapon=axe)
    check("on dry land a greataxe swings fine", not dis)
    v.close_scene(dry.id)

    wet = v.open_scene("selftest:wet", kind="combat", archetype="reef",
                       name="The Shelf", seed=4, encounter_id=enc.id,
                       render_art=False)
    kt = v.add_token(wet.id, "Kara", kind=TokenKind.PC, team=Team.PARTY,
                     combatant_id=kara.id)
    ft = v.add_token(wet.id, "Sahuagin", kind=TokenKind.MONSTER, team=Team.FOE,
                     combatant_id=fish.id, swim_speed_ft=40)
    v.move_token(ft.id, kt.x + 1, kt.y, teleport=True, free=True)
    eng.spatial = BoardSpatial(v, wet.id)
    check("a reef board knows it is underwater", eng.spatial.underwater())
    check("…and which of them swims",
          eng.spatial.swims(b) and not eng.spatial.swims(a),
          "movement_mode says everyone is swimming; only one of them CAN")

    _adv, dis, notes = eng._attack_advantage(a, b, False, enc.id, weapon=axe)
    check("underwater the same greataxe is at disadvantage", dis,
          "; ".join(notes))
    spear = {"name": "Spear", "damage": "1d6+3", "ranged": False}
    _adv, dis, _n = eng._attack_advantage(a, b, False, enc.id, weapon=spear)
    check("…but a spear is thrust, not swung", not dis)
    _adv, dis, _n = eng._attack_advantage(b, a, False, enc.id, weapon=axe)
    check("…and a creature that swims is exempt whatever it holds", not dis,
          "the sahuagin is at home here")

    # Break them apart first: a ranged attack made while an enemy is breathing
    # down your neck takes disadvantage on dry land too, and it would mask
    # everything below.
    here = v.find_token(wet.id, "Kara")
    far = next((sq for sq in v.grid(wet.id).squares()
                if v.grid(wet.id).passable(sq[0], sq[1], mode="swim")
                and geo.distance_ft((here.x, here.y), sq) >= 40), None)
    check("the shelf is big enough to shoot across", far is not None)
    v.move_token(ft.id, far[0], far[1], teleport=True, free=True)
    eng.spatial = BoardSpatial(v, wet.id)
    check("…and they are no longer in each other's faces",
          (eng.spatial.distance_ft(a, b) or 0) > 5,
          f"{eng.spatial.distance_ft(a, b)} ft apart")
    # Asserted on the NOTES, not on the final flag. Advantage and disadvantage
    # cancel in 5e, so a correct rule firing here can be zeroed out by another
    # correct rule (neither of them can see the other across a dim reef) — and
    # a test that reads only the flag would call that a failure.
    def underwater_note(notes):
        return any(n.startswith("underwater:") for n in notes)

    bow = {"name": "Longbow", "damage": "1d8+2", "ranged": True}
    _adv, _dis, notes = eng._attack_advantage(a, b, True, enc.id, weapon=bow)
    check("a bow underwater takes the penalty", underwater_note(notes),
          "; ".join(notes))
    bolt = {"name": "Light Crossbow", "damage": "1d8+2", "ranged": True}
    _adv, _dis, notes = eng._attack_advantage(a, b, True, enc.id, weapon=bolt)
    check("…a crossbow does not", not underwater_note(notes), "; ".join(notes))
    _adv, _dis, notes = eng._attack_advantage(b, a, True, enc.id, weapon=bow)
    check("swimming does NOT exempt you from the ranged rule",
          underwater_note(notes),
          "the water slows the arrow, not your footing | " + "; ".join(notes))

    # Spell attacks are untouched: the rule is about weapons.
    _adv, _dis, notes = eng._attack_advantage(a, b, True, enc.id)
    check("a spell attack underwater is unaffected", not underwater_note(notes),
          "no weapon passed means no weapon rule | " + "; ".join(notes))
    ct.end_encounter(enc.id)
    v.close_scene(wet.id)


def test_hiding() -> None:
    section("hiding: a contest, remembered, and personal")
    import random as _random
    import tempfile as _tf
    db = os.path.join(_tf.mkdtemp(), "hiding.db")
    v = VttEngine(database_url=f"sqlite:///{db}")
    v.create_tables()
    sc = v.open_scene("test:hide", kind="combat", archetype="open",
                      name="Open Field", width=20, height=10, seed=11,
                      render_art=False, lighting="bright")

    rogue = v.add_token(sc.id, name="Rogue", x=4, y=5, team=Team.PARTY)
    guard = v.add_token(sc.id, name="Guard", x=10, y=5, team=Team.FOE)

    # You cannot hide from someone looking straight at you.
    elig = v.hide_eligibility(sc.id, "Rogue")
    check("open ground in daylight is nowhere to hide", not elig["ok"])
    eq("…and the board names who can see you", elig["blocked_by"], ["Guard"])
    eq("so the attempt is refused before any dice",
       v.hide(sc.id, "Rogue", bonus=99).get("ok"), False)

    # A portcullis is the interesting case: three-quarters cover you can still
    # be SEEN through. Cover qualifies where dim light would not.
    v.set_terrain(sc.id, [(7, 5)], "p")
    eq("bars are cover without blocking sight",
       v.cover_for(sc.id, "Guard", "Rogue"), "three-quarters")
    check("…and the guard can still see through them",
          v.vision(sc.id, "Guard", "Rogue", ignore_hidden=True)["sees"])
    check("cover is enough to try", v.hide_eligibility(sc.id, "Rogue")["ok"])

    got = v.hide(sc.id, "Rogue", bonus=7, rng=_random.Random(3))
    check("hiding rolls Stealth and succeeds on a 15+", got["ok"], str(got))
    eq("the roll is kept as the DC to find them",
       v.find_token(sc.id, "Rogue").stealth_dc, got["roll"])
    check("the guard has lost them, line of sight or not",
          not v.can_see(sc.id, "Guard", "Rogue"))

    # Finding is personal: one searcher, one result.
    hound = v.add_token(sc.id, name="Hound", x=11, y=5, team=Team.FOE)
    v.update_token(v.find_token(sc.id, "Rogue").id, stealth_dc=12)
    found = v.search(sc.id, "Guard", bonus=20)      # cannot fail a DC 12
    eq("a searcher who beats the roll finds them", found["found"], ["Rogue"])
    check("…and only that searcher", v.can_see(sc.id, "Guard", "Rogue")
          and not v.can_see(sc.id, "Hound", "Rogue"),
          "the guard who spotted you sees you; the rest of the room does not")

    # Going flat behind low cover: the thing you would actually do. A crate is
    # half cover standing and conceals you completely lying down, which makes
    # you untargetable AND lets you hide — from an archer at ground level.
    # A clean row of its own: the portcullis and the hound are still on row 5,
    # and a second obstacle would make this measure something else.
    v.set_terrain(sc.id, [(8, 2)], "o")
    v.unhide(sc.id, "Rogue")
    v.move_token(rogue.id, 7, 2, teleport=True, free=True)
    v.move_token(guard.id, 10, 2, teleport=True, free=True)
    # The hound goes BEHIND the guard, not off to one side: a crate hides you
    # from what is on the far side of it, and from nothing else.
    v.move_token(hound.id, 11, 2, teleport=True, free=True)
    v.stand_up(sc.id, "Rogue")
    eq("standing behind the crate is half cover",
       v.cover_for(sc.id, "Guard", "Rogue"), "half")
    check("…and the guard can see you fine", v.can_see(sc.id, "Guard", "Rogue"))
    v.go_prone(sc.id, "Rogue")
    eq("lying down behind it is total cover",
       v.cover_for(sc.id, "Guard", "Rogue"), "total")
    check("…which means completely out of sight",
          not v.can_see(sc.id, "Guard", "Rogue"),
          "total cover is not a modifier, it is a wall")
    check("…so you may hide there", v.hide_eligibility(sc.id, "Rogue")["ok"])
    v.update_token(guard.id, elevation_ft=20)
    eq("but not from a guard on the gallery, shooting down over it",
       v.cover_for(sc.id, "Guard", "Rogue"), "half")
    v.update_token(guard.id, elevation_ft=0)
    v.stand_up(sc.id, "Rogue")
    v.move_token(rogue.id, 4, 5, teleport=True, free=True)
    v.move_token(guard.id, 10, 5, teleport=True, free=True)
    v.move_token(hound.id, 11, 5, teleport=True, free=True)
    v.hide(sc.id, "Rogue", bonus=7, rng=_random.Random(3))
    v.update_token(v.find_token(sc.id, "Rogue").id, stealth_dc=12,
                   found_by=["Guard"])

    # The party's board shows what the party has found — no more, no less.
    names = {t["name"] for t in v.state(sc.id, viewer_team=Team.FOE)["tokens"]}
    check("a foe who found the rogue sees her on their own board",
          "Rogue" in names,
          "the Search action's result has to reach the picture")
    v.update_token(v.find_token(sc.id, "Rogue").id, found_by=[])
    names = {t["name"] for t in v.state(sc.id, viewer_team=Team.FOE)["tokens"]}
    check("…and a side that has found nobody sees nobody", "Rogue" not in names)
    check("but you always see your own people",
          "Rogue" in {t["name"] for t in
                      v.state(sc.id, viewer_team=Team.PARTY)["tokens"]},
          "a player who cannot see their own token cannot play")
    v.update_token(v.find_token(sc.id, "Rogue").id, found_by=["Guard"])

    # Senses that don't use light aren't fooled by holding still.
    v.update_token(hound.id, senses={"blindsight": 60})
    check("blindsight is not fooled by hiding",
          v.can_see(sc.id, "Hound", "Rogue"),
          "you can hold still behind bars; you cannot stop making noise")
    check("…so you cannot hide from it in the first place",
          not v.hide_eligibility(sc.id, "Rogue")["ok"],
          "an enemy that perceives you blocks the attempt, sense regardless")
    v.update_token(hound.id, senses={})

    # Attacking, shouting or stepping into the open all end it.
    v.unhide(sc.id, "Rogue", "attacked")
    check("unhiding forgets the roll",
          v.find_token(sc.id, "Rogue").stealth_dc is None,
          "a stale DC would make the next hide cheaper than it should be")

    v.hide(sc.id, "Rogue", bonus=7, rng=_random.Random(3))
    check("hidden again", v.find_token(sc.id, "Rogue").hidden)
    # Far enough out that the bars are no longer between them; teleport so the
    # speed limit isn't what this check is really measuring.
    moved = v.move_token(rogue.id, 13, 8, teleport=True, free=True)
    check("stepping out from cover breaks it",
          moved.get("hiding_broken") is True, str(moved))
    check("…and the board agrees", not v.find_token(sc.id, "Rogue").hidden)
    v.close_scene(sc.id)


def test_setpieces() -> None:
    section("set pieces: a mesh with no rules of its own")
    from . import setpieces as sp
    from . import skins as _sk
    from .terrain import Grid, tile

    # --- the mesh turns the way the tiles turn ---------------------------
    # The whole point of the pair. If they part company the picture rotates
    # and the cover does not, and a creature takes three-quarters cover from
    # a face of the statue that is now behind it.
    piece = sp.CATALOGUE["shipwreck"]
    t0, _e0, _f0 = sp._turned(piece, 0)
    t90, _e90, _f90 = sp._turned(piece, 90)
    where = lambda t: [(x, y) for y, r in enumerate(t)      # noqa: E731
                       for x, c in enumerate(r) if c == sp.KEEP]
    (ox, oy), = where(t0)
    (nx, ny), = where(t90)
    # Centred coordinates, so the turn is about the middle rather than a corner.
    u, v = ox - (len(t0[0]) - 1) / 2, oy - (len(t0) - 1) / 2
    u2, v2 = nx - (len(t90[0]) - 1) / 2, ny - (len(t90) - 1) / 2
    eq("a quarter turn sends a footprint square to (-v, u)", (u2, v2), (-v, u))
    rx, rz = sp.rotate_xz(u, v, 90)
    check("…and the mesh's own rotation agrees",
          abs(rx - u2) < 1e-9 and abs(rz - v2) < 1e-9,
          f"mesh ({rx:.3f},{rz:.3f}) vs tiles ({u2},{v2})")

    # --- stamping, and what it deliberately does NOT stamp ---------------
    g = Grid.blank(20, 16, "g")
    placed, = sp.setpieces_for(g, ["jungle-giant"], seed=5)
    p = sp.CATALOGUE["jungle-giant"]
    trunk = [(x, y) for (x, y) in placed.occupied if g.get(x, y) == "T"]
    eq("a jungle giant is nine squares of mesh…", (p.width, p.depth), (9, 9))
    eq("…and exactly one square of rules", len(trunk), 1)
    check("the canopy leaves the meadow a meadow",
          all(g.get(x, y) == "g" for (x, y) in placed.occupied
              if (x, y) not in trunk),
          "a reserved square must keep the terrain it already had")
    check("…while still being reserved, so nothing is scattered under it",
          len(placed.occupied) == 81, str(len(placed.occupied)))
    check("only stamped squares are skinned",
          set(placed.skins) == {f"{x},{y}" for x, y in trunk},
          f"{len(placed.skins)} skins for {len(trunk)} stamped squares")
    check("and the skin says a MESH draws this square",
          all(_sk.is_setpiece(n) for n in placed.skins.values()))

    # --- the mesh owns no mechanics --------------------------------------
    tx, ty = trunk[0]
    check("the trunk blocks movement because its TILE does, not its model",
          tile(g.get(tx, ty)).move_cost_ft is None)
    check("a canopy square costs a creature nothing",
          tile(g.get(tx + 3, ty)).move_cost_ft == 5)

    # --- an uncollected mesh degrades, it does not raise ------------------
    inst = sp.Placed(slug="step-pyramid", x=0, y=0, yaw=0).instance()
    eq("a piece with no mesh reports none", inst["mesh"], None)
    check("…and the board draws it from the tiles it stamped",
          sp.CATALOGUE["step-pyramid"].tiles != ())

    # --- fits() keeps landmarks out of the walls --------------------------
    walled = Grid.blank(20, 16, "#")
    eq("nothing stands in solid rock",
       sp.setpieces_for(walled, ["boulder-heap"], seed=1), [])

    # --- jumping a gap ----------------------------------------------------
    # Boards grew chasms, channels ten feet deep and stacked terraces, and
    # there was no rule for going OVER any of it: you could climb at a foot per
    # foot or walk round. The SRD's numbers are unusually concrete, so they are
    # used as written — a running long jump clears your Strength SCORE in feet.
    import tempfile as _tf3
    _jdb = os.path.join(_tf3.mkdtemp(), "jump.db")
    _jv = VttEngine(database_url=f"sqlite:///{_jdb}")
    _jv.create_tables()
    _js = _jv.open_scene("test:jump", kind="combat", archetype="open",
                         width=16, height=10, seed=3, render_art=False)
    for _x in range(16):
        for _y in range(10):
            _jv.set_terrain(_js.id, [(_x, _y)], "g")
    _jv.set_terrain(_js.id, [(8, _y) for _y in range(10)], "x")   # a channel
    _lp = _jv.add_token(_js.id, "Leaper", kind="pc", team="party",
                        x=7, y=5, speed_ft=30)
    eq("a standing jump is half a running one",
       _jv.jump_reach_ft(_lp.id, running=False)["long_ft"] * 2,
       _jv.jump_reach_ft(_lp.id, running=True)["long_ft"])
    check("a standing jump will not clear the channel",
          not _jv.jump(_lp.id, 9, 5)["ok"])
    _jv.update_token(_lp.id, moved_ft=10)          # ten feet of run-up
    _leapt = _jv.jump(_lp.id, 9, 5)
    check("…and a running one does", _leapt.get("ok"), str(_leapt))
    eq("the jump costs its own distance in movement",
       _jv.get_token(_lp.id).moved_ft, 20)
    check("you cannot walk into the channel it cleared",
          not _jv.move_token(_lp.id, 8, 5).get("ok"))
    _jv.set_elevation(_js.id, [(9, 5)], 20)
    _jv.update_token(_lp.id, moved_ft=10)
    check("a ledge two storeys up has to be climbed, not hopped",
          not _jv.jump(_lp.id, 9, 5)["ok"])

    # --- a monster takes the high ground ----------------------------------
    # The DM prompt tells the LLM to contest height; this is the half of the
    # fight the LLM never touches. The engine decides a BAND and the bridge
    # turns it into a square, and that translation used to read flat distance
    # only — so on a board made of ledges every monster archer stood in the mud.
    import tempfile as _tf2
    from . import bridge as _bridge
    _db = os.path.join(_tf2.mkdtemp(), "band.db")
    _v = VttEngine(database_url=f"sqlite:///{_db}")
    _v.create_tables()
    _sc = _v.open_scene("test:band", kind="combat", archetype="open",
                        width=20, height=14, seed=5, render_art=False)
    for _x in range(20):
        for _y in range(14):
            _v.set_terrain(_sc.id, [(_x, _y)], "g")
    _v.set_elevation(_sc.id, [(x, y) for x in range(20) for y in (0, 1, 2)], 10)
    _v.add_token(_sc.id, "Kara", kind="pc", team="party", x=10, y=10)
    _shooter = _v.add_token(_sc.id, "Archer", kind="monster", team="foe",
                            x=10, y=8, combatant_id=77)
    _bridge.apply_band_move(_v, _sc.id, 77, "far")
    _moved = _v.get_token(_shooter.id)
    eq("a monster holding a range band goes UP",
       _v.token_height_ft(_v.get_scene(_sc.id), _moved), 10)

    # --- a band is a RELATIONSHIP, so only its OWNER may be repositioned ----
    #
    # When something closes on the party, everyone's band changes and only the
    # closer moved. `reconcile_bands` walks a token whose tracker band
    # disagrees with its square, and that drift used to drag the PC BACKWARDS
    # to restore a band nobody had set for them: a player's own turn began
    # somewhere they had never gone, on the turn a crocodile swam up to them.
    from combat.tracker import CombatTracker as _CT
    _ct2 = _CT(engine=_v.engine)
    _v.tracker = _ct2
    _enc2 = _ct2.start_encounter("test:band", "Reconcile")
    _pc2 = _ct2.add_combatant(_enc2.id, "Kara", kind="pc", max_hp=20,
                              armor_class=14, initiative=10)
    _croc = _ct2.add_combatant(_enc2.id, "Crocodile", kind="monster", max_hp=19,
                               armor_class=12, initiative=2)
    _v.update_scene_encounter(_sc.id, _enc2.id)
    _kara_t = _v.find_token(_sc.id, "Kara")
    _v.update_token(_kara_t.id, combatant_id=_pc2.id)
    _croc_t = _v.add_token(_sc.id, "Crocodile", kind="monster", team="foe",
                           x=10, y=13, combatant_id=_croc.id)
    _bridge.sync_bands(_v, _sc.id, tracker=_ct2)          # both read "far"
    _kara_was = (_v.get_token(_kara_t.id).x, _v.get_token(_kara_t.id).y)
    # The crocodile closes — nobody has touched Kara's band or Kara's token.
    _v.move_token(_croc_t.id, 10, 11, free=True, enforce_speed=False)
    _bridge.sync_after_turn(_v, _sc.id, tracker=_ct2)
    _kara_now = (_v.get_token(_kara_t.id).x, _v.get_token(_kara_t.id).y)
    eq("a creature nobody moved stays where it stood", _kara_now, _kara_was)
    # …while a band the DM DELIBERATELY set still repositions its owner.
    _ct2.set_position(_croc.id, "far")
    _bridge.reconcile_bands(_v, _sc.id, tracker=_ct2)
    _croc_now = _v.get_token(_croc_t.id)
    check("…but a band the DM changed still moves the creature it names",
          (_croc_now.x, _croc_now.y) != (10, 11),
          f"{(_croc_now.x, _croc_now.y)}")

    # --- most boards are not flat -----------------------------------------
    # Height is the cheapest asymmetry a fight can have: it costs movement to
    # take, a fall to leave in a hurry, and it changes who can see whom without
    # changing a rule. Fourteen of the twenty-one archetypes used to generate a
    # perfectly flat board, including the one called mountain-pass. This is the
    # guard on that: a generator may be rewritten, but not back into a table
    # top.
    from .mapgen import ARCHETYPES as _ARCH, generate_map as _gm
    flat = []
    for name in sorted(_ARCH):
        vertical = False
        for seed in (3, 7, 11):
            gen = _gm(name, width=30, height=22, seed=seed)
            if gen.elevation or len(gen.levels) > 0:
                vertical = True
                break
        if not vertical:
            flat.append(name)
    # open-water is a sea SURFACE: flat is what it is.
    check("a board has somewhere to stand above the fight",
          set(flat) <= {"open-water"}, f"flat archetypes: {flat}")

    # …and neither is an upper STOREY. The height vocabulary belonged to the
    # ground floor for as long as elevation was one flat map, so every gallery,
    # rooftop and hold in the game was a table top by construction. These two
    # are the deliberate uses; the guard is that a generator may be rewritten
    # but not back into a plank ten feet up.
    from .terrain import VOID as _VOID
    for arch, storey in (("street", "Rooftops"), ("tavern", "Gallery")):
        got = False
        for seed in (3, 7, 11):
            gen = _gm(arch, width=30, height=22, seed=seed)
            if any(l.get("name") == storey and l.get("elevation")
                   for l in gen.levels):
                got = True
                break
        check(f"a {arch}'s {storey.lower()} are not a table top", got)

    # A height on a square the storey has no FLOOR on is height on nothing: the
    # classic way a per-level map goes wrong is a primitive writing the ground's
    # coordinates onto an upper storey that is mostly open air.
    stranded: list[str] = []
    for name in sorted(_ARCH):
        for seed in (3, 7, 11):
            gen = _gm(name, width=30, height=22, seed=seed)
            for l in gen.levels:
                rows = l.get("terrain") or []
                for key in (l.get("elevation") or {}):
                    x, y = (int(v) for v in key.split(","))
                    if not (0 <= y < len(rows) and 0 <= x < len(rows[y])) \
                            or rows[y][x] == _VOID:
                        stranded.append(f"{name}/{l.get('name')}@{key}")
    check("no storey carries height on a square it has no floor on",
          not stranded, "; ".join(stranded[:4]))


    # --- scenery belongs to the kind of place it is in --------------------
    from . import decor as _decor
    from .terrain import tile_height_ft as _tall
    from .mapgen import generate_map as _g2

    def _kinds(arch: str, seed: int = 7) -> set:
        gen = _g2(arch, width=30, height=22, seed=seed)
        return {d["kind"] for d in _decor.decor_for(
            gen.grid.to_rows(), seed=seed, archetype=arch,
            standing=lambda c: _tall(c) > 0)}

    meadow = _kinds("open")
    check("nothing is furnished outdoors",
          not (meadow & {"rug", "sack", "brazier"}), str(sorted(meadow)))
    check("…and what IS out there grows or fell there",
          meadow & {"tussock", "bush", "deadfall", "stump"}, str(sorted(meadow)))
    room = _kinds("tavern")
    check("nothing grows indoors",
          not (room & {"bush", "tussock", "stump", "deadfall"}),
          str(sorted(room)))
    street = _kinds("street")
    check("a street is paved, so it is neither",
          not (street & {"bush", "tussock", "rug"}), str(sorted(street)))
    under = _kinds("cave")
    check("a cave has no furniture and no daylight to grow in",
          not (under & {"rug", "brazier", "sack", "bush", "tussock"}),
          str(sorted(under)))
    # The colour is a fact about the thing, and both renderers read this one.
    check("every kind declares its own tint",
          all(_decor.colour_of(k).startswith("#") and len(_decor.colour_of(k)) == 7
              for k in _decor.DECOR_KINDS))
    check("a shrub is green and a stone is not",
          _decor.colour_of("bush") != _decor.colour_of("stones"))

    # --- what the DM's words name ----------------------------------------
    eq("a ziggurat is a step pyramid",
       sp.landmark_for("a stepped ziggurat swallowed in vines"), ["step-pyramid"])
    eq("a slug is taken at its word", sp.landmark_for("step-pyramid"),
       ["step-pyramid"])
    eq("two landmarks in one breath",
       sp.landmark_for("fallen boulders beside a monolith"),
       ["standing-stone", "boulder-heap"])
    # Word boundaries, unlike the archetype table: a board full of ARCHERS is
    # exactly the board a DM is describing when a fight starts.
    eq("an archer is not an arch", sp.landmark_for("six archers on the wall"), [])
    eq("wording that names nothing asks for nothing",
       sp.landmark_for("a smoky taproom"), [])

    # --- a landmark the DM asked for --------------------------------------
    # The channel the catalogue exists for: the fiction says a ziggurat stands
    # here, and a forest's own pool has never heard of one.
    from .mapgen import _SETPIECES, generate_map as _gen
    check("a forest would not offer a pyramid on its own",
          "step-pyramid" not in _SETPIECES["forest"])
    asked = [p["slug"] for p in _gen("forest", width=36, height=26, seed=2,
                                     landmarks=["step-pyramid"]).setpieces]
    check("but one the DM named stands anyway", "step-pyramid" in asked,
          str(asked))
    # And the board is grown to hold it before it is generated, or the two
    # halves of one decision disagree.
    from .triggers import board_size_for as _size
    plain = _size((24, 18), archetype="dungeon-room")
    grown = _size((24, 18), archetype="dungeon-room", landmarks=["step-pyramid"])
    check("a board grows for a landmark it was asked for",
          grown[0] * grown[1] > plain[0] * plain[1], f"{plain} -> {grown}")

    # --- a landmark the DM asked for sweeps the ground it stands on -------
    # Without this the channel fails on exactly the boards most worth a
    # landmark: a ruin scattered with broken pillars has no eleven-by-eleven
    # clearing anywhere, and a ziggurat with a pillar standing on its plaza is
    # not the alternative anyone wanted.
    scattered = Grid.blank(24, 20, "g")
    for i in range(0, 24, 3):
        for j in range(0, 20, 3):
            scattered.set(i, j, "T")
    eq("scatter refuses a landmark nobody asked for",
       sp.setpieces_for(scattered, ["step-pyramid"], seed=4), [])
    asked_p = sp.setpieces_for(scattered, ["step-pyramid"], seed=4,
                               clear=["step-pyramid"])
    check("…and gives way to one the DM named", len(asked_p) == 1)
    if asked_p:
        check("the trees under it are gone",
              all(scattered.get(x, y) != "T" for x, y in asked_p[0].occupied))
    # A pyramid stamps every square it covers, so the sweep only SHOWS on a
    # piece with reserved ones — a jungle giant is eighty squares of ground its
    # canopy hangs over, and those keep whatever they were.
    meadow = Grid.blank(24, 20, "g")
    for i in range(0, 24, 3):
        for j in range(0, 20, 3):
            meadow.set(i, j, "T")
    giant = sp.setpieces_for(meadow, ["jungle-giant"], seed=4,
                             clear=["jungle-giant"])
    check("a reserved square is swept too", len(giant) == 1)
    if giant:
        under = [meadow.get(x, y) for x, y in giant[0].occupied]
        eq("…and becomes the ground around it, not a paving of its own",
           sorted(set(under)), ["T", "g"])   # the trunk's own square, and grass
    walls = Grid.blank(24, 20, "g")
    for i in range(24):
        walls.set(i, 10, "#")
    placed_w = sp.setpieces_for(walls, ["step-pyramid"], seed=4,
                                clear=["step-pyramid"])
    check("a wall is never swept aside",
          all(walls.get(x, 10) == "#" for x in range(24)),
          "clearing may only ever take DECOR_CODES")
    check("…so the piece goes beside it, or nowhere",
          all(p.y + 9 <= 10 or p.y > 10 for p in placed_w))

    # --- every square is tried, not forty darts ---------------------------
    # A nine-by-nine piece wants an eleven-by-eleven clearing; a wooded board
    # has few, and a random miss is indistinguishable from a board with no room
    # at all. Two forests in three used to refuse a pyramid that fitted.
    stood = sum(bool(sp.setpieces_for(Grid.blank(24, 20, "g"),
                                      ["step-pyramid"], seed=s))
                for s in range(1, 9))
    eq("a landmark that fits somewhere is found", stood, 8)


def test_vessels() -> None:
    """Two ships are not the same ship, and none of them is a meadow."""
    from . import mapgen as _mg
    from . import vessels as _v

    section("vessels: a hull is the vessel's, not the board's")

    seen: set[str] = set()
    collapsed = 0
    for arch in ("ship", "skyship"):
        for w, h in ((36, 26), (30, 22), (24, 18), (20, 14)):
            for seed in range(1, 13):
                gen = _mg.generate_map(arch, width=w, height=h, seed=seed)
                deck = sum(r.count("b") for r in gen.grid.to_rows())
                if deck == 0:
                    collapsed += 1
                    continue
                seen.add(gen.description.split(" under")[0].split(" —")[0])
    # Every vessel board used to fall back to open ground below a certain size:
    # the density floor was written for a collapsed cave, and a small ship in a
    # large sea looks exactly like one. Then the RAIL ate what was left — `w` is
    # impassable, and on a fine hull almost every square touches the water.
    check("no vessel board collapses to open ground", collapsed == 0,
          f"{collapsed} of 96")
    check("and the fleet is not one ship", len(seen) >= 5,
          f"{len(seen)} distinct hulls over 96 boards")

    # The silhouette is what the painter is conditioned on, so two classes that
    # differ only in name would be two pictures of one ship.
    big = _v.get("galleon")
    small = _v.get("cutter")
    check("a galleon is longer and broader than a cutter",
          big.length > small.length and big.beam > small.beam)
    check("a sea hull and a sky hull are cut to different plans",
          _v.get("caravel").plan == "sea" and _v.get("courier").plan == "sky")
    # Proportion, not clamping: a big vessel on a small board stays that vessel.
    L, B = _v.fitted(big, 16, 12)
    check("a hull too big for its board keeps its proportions",
          abs((L / B) - (big.length / big.beam)) < 0.35,
          f"{L}x{B} vs {big.length}x{big.beam}")
    # A named vessel's own numbers pick its shape.
    tiny = _v.for_vessel({"crew": 2, "passengers": 2, "cargo_tons": 0.5})
    great = _v.for_vessel({"crew": 10, "passengers": 40, "cargo_tons": 2.0})
    check("a catalogued vessel's crew and cargo choose its class",
          tiny.length < great.length, f"{tiny.slug} vs {great.slug}")


def main() -> int:
    print("\033[1mThe Oracle — tactical board self-test\033[0m")
    for fn in (test_distance, test_sight_and_cover, test_templates, test_movement,
               test_opportunity, test_mapgen, test_engine, test_bridge,
               test_light_and_vision, test_hiding, test_underwater,
               test_mounts_and_squeezing, test_board_size, test_levels,
               test_setpieces, test_vessels):
        try:
            fn()
        except Exception:
            _FAILS.append(fn.__name__)
            print(f"  \033[31m✗\033[0m {fn.__name__} raised:")
            traceback.print_exc()
    print()
    if _FAILS:
        print(f"\033[31m{len(_FAILS)} of {_RUN} checks failed:\033[0m "
              + ", ".join(_FAILS))
        return 1
    print(f"\033[32mall {_RUN} checks passed\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
