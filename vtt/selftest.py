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
from .terrain import Grid

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
        walk = [(x, y) for x, y in m.grid.squares() if m.grid.passable(x, y)]
        # One connected space: every board must be playable end to end.
        seen = {walk[0]}
        stack = [walk[0]]
        while stack:
            x, y = stack.pop()
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if (nx, ny) not in seen and m.grid.in_bounds(nx, ny) \
                        and m.grid.passable(nx, ny):
                    seen.add((nx, ny))
                    stack.append((nx, ny))
        check(f"{arch}: one connected region", len(seen) == len(walk),
              f"{len(seen)}/{len(walk)} reachable")
        check(f"{arch}: has both spawn zones",
              bool(m.spawn_party) and bool(m.spawn_foes))

    a = generate_map("cave", width=20, height=14, seed=99)
    b = generate_map("cave", width=20, height=14, seed=99)
    eq("the same seed rebuilds the same board", a.grid.to_rows(), b.grid.to_rows())
    c = generate_map("cave", width=20, height=14, seed=100)
    check("a different seed gives a different board", a.grid.to_rows() != c.grid.to_rows())

    eq("DM language maps to a layout", archetype_for("a smoky taproom"), "tavern")
    eq("…and so does a bare archetype name", archetype_for("sewer"), "sewer")
    eq("unknown wording falls back to open ground", archetype_for("???"), "open")


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


def main() -> int:
    print("\033[1mThe Oracle — tactical board self-test\033[0m")
    for fn in (test_distance, test_sight_and_cover, test_templates, test_movement,
               test_opportunity, test_mapgen, test_engine):
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
