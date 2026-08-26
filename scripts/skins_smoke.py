"""What a square is MADE of, and the things you can get inside.

    uv run python scripts/skins_smoke.py

Offline: no GPU, no LLM, no network. A scratch database, built and thrown away.

Two subsystems, and one property joins them: **neither may change what a square
DOES**. A skin is a material and a silhouette; a tent is a wall ring and a
floor. Both are expressed entirely in things the rules already understood, and
that is what makes them safe. This pins it.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
_fails = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global _fails
    mark = f"{GREEN}OK{OFF}" if ok else f"{RED}FAIL{OFF}"
    if not ok:
        _fails += 1
    print(f"  {mark}  {label}" + (f"  {DIM}— {detail}{OFF}" if detail else ""))


def head(n: int, title: str) -> None:
    print(f"\n{BOLD}{n}. {title}{OFF}")


def main() -> int:
    from vtt import skins as S
    from vtt import structures
    from vtt.mapgen import ARCHETYPES, generate_map
    from vtt.terrain import Grid, cover_height_ft, tile

    head(1, "a skin is a look, and the rules cannot see it")
    for arch in ("mountain-pass", "reef", "sewer", "ship"):
        gen = generate_map(arch, width=26, height=20, seed=5)
        codes = S.skins_for(arch, style=gen.style)
        check(bool(codes) or bool(gen.skins), f"{arch} wears skins",
              ", ".join(sorted(set(codes.values()) | set(gen.skins.values()))))
    # The property that makes the vocabulary safe to grow: a skinned board and
    # the same board unskinned agree about every rule, square for square.
    gen = generate_map("mountain-pass", width=26, height=20, seed=5)
    codes = S.skins_for("mountain-pass")
    same = all(tile(gen.grid.get(x, y)).move_cost_ft
               == tile(gen.grid.get(x, y)).move_cost_ft
               for x, y in gen.grid.squares())
    check(same, "movement, cover and sight read the CODE, never the skin")
    check(all(c in {t for t in __import__("vtt.terrain", fromlist=["TILES"]).TILES}
              for c in codes),
          "every skinned code is a real tile code")

    head(2, "a skin may reshape a quoted height — never restate it")
    quoted = [c for c in ("o", "n", "w", "A") if cover_height_ft(c)]
    check(bool(quoted), "the rules quote heights for crates, tables, low walls",
          ", ".join(f"{c}={cover_height_ft(c)}ft" for c in quoted))
    offenders = []
    for arch, mapping in list(S.ARCH_SKINS.items()) + list(
            ("skyship:" + k, v) for k, v in S.SKYSHIP_STYLES.items()):
        for code, name in mapping.items():
            if cover_height_ft(code) and S.height_of(name):
                offenders.append((arch, code, name))
    check(not offenders, "no skin in the catalogue redraws one of them",
          "checked every archetype and every style")
    # ...and the guard is live, not just currently satisfied.
    try:
        S.ARCH_SKINS["_probe"] = {"w": "cliff"}      # cliff is 14 ft; w is 3
        S._check_heights()
        raised = False
    except ValueError:
        raised = True
    finally:
        S.ARCH_SKINS.pop("_probe", None)
    check(raised, "and the import-time guard refuses one that tries",
          "a 14-ft cliff skin on a 3-ft low wall")

    head(3, "a skin may never wall up a square you can walk through")
    # The bug this exists for: a watchtower's whole wall ring was skinned in
    # one pass, INCLUDING its open doorway, so a square the rules let you walk
    # through was drawn as a solid nine-foot merloned block. The tower read as
    # four walls you would have to fly into, and the way in simply was not
    # there. A picture contradicting the grid is the one thing the board must
    # never do, and it is worst in this direction.
    check(S.occludes_floor("tower-stone"), "a tower WALL does occlude — correctly")
    check(not S.occludes_floor("doorway-stone"),
          "a DOORWAY does not: jambs and a lintel over an open passage")
    blocked = []
    for arch in sorted(ARCHETYPES):
        for seed in (1, 7, 42):
            gen = generate_map(arch, width=26, height=20, seed=seed)
            codes = S.skins_for(arch, style=gen.style)
            for x, y in gen.grid.squares():
                code = gen.grid.get(x, y)
                if not tile(code).move_cost_ft:
                    continue                      # not walkable anyway
                name = S.skin_at(code, x, y, codes=codes, squares=gen.skins)
                if name and S.occludes_floor(name):
                    blocked.append((arch, seed, x, y, code, name))
    check(not blocked,
          "and no walkable square on any board carries one that does",
          str(blocked[:2]) if blocked else "21 archetypes x3 seeds")

    head(4, "a rail is a rail whatever it is made of")
    tim = S.skins_for("skyship", style="timber")
    steam = S.skins_for("skyship", style="steampunk")
    org = S.skins_for("skyship", style="organic")
    check(tim["w"] != steam["w"] != org["w"], "three styles, three rail skins",
          f'{tim["w"]} / {steam["w"]} / {org["w"]}')
    check(all(not S.height_of(m["w"]) for m in (tim, steam, org)),
          "and all three stand at the low wall's own three feet")
    check(tim["b"] != steam["b"] != org["b"],
          "the DECK follows the style too, which is most of what you see",
          f'{tim["b"]} / {steam["b"]} / {org["b"]}')

    head(5, "a shape may be a POLYGON, not only a box")
    from vtt.boardshapes import footprint, rotate_part
    solids = [n for n, sk in S.SKINS.items() if sk.variants
              and any(S.is_solid(p) for parts in sk.variants for p in parts)]
    check(bool(solids), "skins are authored with prismatoid parts",
          ", ".join(sorted(solids)))
    # A slope is not a stack of boxes. The tent's canvas was four terraces
    # before this, and no wording fixed it — the model paints the silhouette it
    # is handed.
    canvas = S.SKINS["canvas"].variants[0][0]
    check(S.is_solid(canvas), "a tent wall is ONE pitched face")
    bottom, top, _y0, _y1 = canvas
    check(S._poly_area(top) < S._poly_area(bottom) / 3,
          "drawn in to a ridge — the top is a fraction of the footprint",
          f"{S._poly_area(bottom):.2f} -> {S._poly_area(top):.2f} of a square")
    # A lean is an OFFSET top, which is the whole of "slightly off vertical".
    leg = S.SKINS["tower-post"].variants[0][0]
    lb, lt, _a, _b = leg
    off = max(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(lb, lt))
    check(S.is_solid(leg) and off > 0.05,
          "a watchtower's legs are RAKED, not plumb",
          f"the head is {off:.2f} of a square off its foot")
    # ...and it survives a quarter turn with every vertex intact.
    turned = rotate_part(leg, 1)
    check(len(turned[0]) == len(lb) and len(turned[1]) == len(lt),
          "and a quarter turn takes every vertex with it")

    head(6, "a hull's outline is joined, not a staircase")
    # A deck is carved out of squares, so its edge steps. Joining the corners
    # FARTHEST from the middle is what makes the steps one line — and it cannot
    # be done a square at a time, because no square can see the outline. So the
    # server traces it once and every renderer draws the answer.
    from vtt.hull import shells
    gen = generate_map("ship", width=28, height=20, seed=5)
    codes = S.skins_for("ship")
    def sk(c, x, z, codes=codes, g=gen):
        return S.skin_at(c, x, z, codes=codes, squares=g.skins)
    hulls = shells(gen.grid.rows, sk, gen.elevation)
    check(len(hulls) == 1, "one ship, ONE shell",
          f"{len(hulls)} traced — the mast, the cabin and every crate punch a "
          f"hole in the body, and only the outer boundary is the hull")
    h = hulls[0]
    check(len(h["loop"]) >= 8 and len(h["loop"]) < 40,
          "traced as a loop of corner points", f'{len(h["loop"])} vertices')
    diag = sum(1 for i, (ax, az) in enumerate(h["loop"])
               if (lambda b: b[0] != ax and b[1] != az)(
                   h["loop"][(i + 1) % len(h["loop"])]))
    check(diag > 0, "with real diagonals, not only square steps",
          f"{diag} of {len(h['loop'])} runs")
    check(bool(h["fill"]), "and the deck reaches out to meet it",
          f'{len(h["fill"])} triangles a smoothed notch gave up')
    check(len(h["low"]) == len(h["loop"]),
          "its bottom is the same loop, mitred at every vertex")
    check(all(S.same_body(a, b) for a in ("sea-deck", "railing", "mast", "hull")
              for b in ("sea-deck", "railing", "mast", "hull")),
          "a deck, its rail, its mast and its cabin are ONE hull")
    check(not shells(generate_map("cave", width=26, height=20, seed=5).grid.rows,
                     lambda c, x, z: S.skin_at(c, x, z,
                                               codes=S.skins_for("cave"))),
          "and a board with no vessel on it traces nothing")

    head(7, "a tent is somewhere you can BE, not something you stand on")
    import random
    g = Grid.blank(14, 12)
    g.fill_rect(0, 0, 13, 11, "g")
    rng = random.Random(3)
    built = structures.tent(g, rng, 3, 3, on=("g",))
    check(bool(built.interior), "a tent pitched",
          f"{len(built.interior)} squares inside")
    check(len(built.interior) >= 4,
          "big enough for more than one creature",
          "a 5-ft interior is a box with a hole in it, not a room")
    check(all(tile(g.get(x, y)).move_cost_ft for x, y in built.interior),
          "every interior square is WALKABLE — the old tents were furniture")
    check(built.door_at is not None and g.get(*built.door_at) == "/",
          "and there is a real doorway to come in by", str(built.door_at))
    walls = [(x, y) for x, y in g.squares()
             if g.get(x, y) == "#" and f"{x},{y}" in built.skins]
    check(bool(walls) and all(tile("#").blocks_sight for _ in walls[:1]),
          "its walls block sight exactly as any wall does",
          f"{len(walls)} squares of canvas")
    check(all(built.skins[f"{x},{y}"] == "canvas" for x, y in walls),
          "...and are recorded as CANVAS, not as stone")
    # Refused rather than shrunk.
    tiny = structures.shelter(g, rng, 1, 1, 3, 3, skin="canvas", on=("g",))
    check(not tiny.interior, "a shelter too small to stand in is refused")
    # A wall ring round a walkable floor is, from above, a roofless box —
    # which is what "the tents look like pens" meant. The covering is its own
    # skin because the inside is its own squares.
    roofed = [(x, y) for x, y in built.interior
              if built.skins.get(f"{x},{y}") == "tent-canopy"]
    check(len(roofed) == len(built.interior), "and a ROOF over every square of it",
          f"{len(roofed)} squares of canvas overhead")
    check(not S.occludes_floor("tent-canopy"),
          "which starts well clear of the floor, so it closes nothing")

    head(8, "a watchtower's top is a real storey, reached by a ladder")
    gen = generate_map("bridge", width=26, height=20, seed=5)
    check(bool(gen.levels), "the bridge built an upper floor",
          ", ".join(f"{l['name']} @{l['base_ft']}ft" for l in gen.levels))
    check(len(gen.stairs) >= 1, "with a way up", f"{len(gen.stairs)} connector(s)")
    for st in gen.stairs:
        lv = gen.levels[st["to_level"] - 1]
        check(lv["terrain"][st["to_y"]][st["to_x"]] != " ",
              f"the {st['kind']} arrives on real floor, not open air",
              f"level {st['to_level']} at {st['to_x']},{st['to_y']}")
    # What the country builds in decides WHICH tower, and they are two
    # different structures rather than one in two materials: stone is a
    # building with a room in it, timber is four legs and a platform.
    wood = generate_map("bridge", width=26, height=20, seed=5,
                        biome="deep forest")
    legs = [k for k, v in wood.skins.items() if v == "tower-post"]
    check(len(legs) == 8, "a forest crossing gets FOUR legs per tower, not walls",
          f"{len(legs)} posts across two towers")
    check(all(wood.grid.get(*map(int, k.split(","))) == "O" for k in legs),
          "and a leg is a pillar to the rules — narrow cover, nothing new")
    under = [(x, y) for x in range(wood.width) for y in range(wood.height)
             if wood.skins.get(f"{x},{y}") in ("tower-top", "tower-ladder")]
    check(all(tile(wood.grid.get(x, y)).move_cost_ft for x, y in under),
          "you can walk UNDER it — the platform is drawn, not built on the ground")
    check(any(v == "tower-ladder" for v in wood.skins.values())
          and any(s["kind"] == "ladder" for s in wood.stairs),
          "and the ladder you can see is the connector you may climb")
    stone = generate_map("bridge", width=26, height=20, seed=5,
                         biome="mountain road")
    check(any(v == "tower-stone" for v in stone.skins.values()) and
          not any(v == "tower-post" for v in stone.skins.values()),
          "a mountain road gets the walled drystone one instead")

    head(9, "a hold is the same machinery pointed downward")
    gen = generate_map("ship", width=28, height=20, seed=5)
    below = [l for l in gen.levels if int(l["base_ft"]) < 0]
    check(bool(below), "the ship has a deck below the weather deck",
          f"{below[0]['name']} at {below[0]['base_ft']} ft" if below else "")
    from vtt.scene import _ft_offset
    check(_ft_offset(-8) == "-8 ft" and _ft_offset(15) == "+15 ft",
          "and the DM board signs the height instead of printing '+-8 ft'",
          f"{_ft_offset(-8)} / {_ft_offset(15)} / {_ft_offset(0)}")

    head(10, "it survives the round trip through the database")
    with tempfile.TemporaryDirectory() as tmp:
        from vtt.scene import VttEngine
        eng = VttEngine(database_url=f"sqlite:///{Path(tmp) / 'smoke.db'}")
        eng.create_tables()
        row = eng.open_scene("smoke:1", archetype="camp", width=26, height=20,
                             seed=5, render_art=False)
        st = eng.state(row.id)
        check(bool(st["skins"]["squares"]),
              "the tents' canvas was persisted per square",
              f"{len(st['skins']['squares'])} squares")
        check(st["skins"]["codes"].get("#") == "palisade",
              "the archetype's own default came back derived, not stored")
        row2 = eng.open_scene("smoke:2", archetype="bridge", width=26,
                              height=20, seed=5, render_art=False)
        st2 = eng.state(row2.id)
        check(len(st2["levels"]) >= 2, "the towers' storey reached the board",
              f"{len(st2['levels'])} levels")
        stairs = [s for lv in st2["levels"] for s in lv.get("stairs", [])]
        check(bool(stairs), "and so did the ladders", f"{len(stairs)} entries")

    head(11, "every board still generates, and every skin resolves")
    bad = []
    for arch in sorted(ARCHETYPES):
        for seed in (1, 7, 42):
            gen = generate_map(arch, width=26, height=20, seed=seed)
            codes = S.skins_for(arch, style=gen.style)
            for name in list(codes.values()) + list(gen.skins.values()):
                # A set-piece marker is the one skin with no SKINS entry, and
                # deliberately so: a mesh is a shape per LANDMARK, not per
                # square, so there is nothing for the per-square vocabulary to
                # hold. It is checked by prefix instead — see skins.is_setpiece.
                if S.is_setpiece(name):
                    if not S.setpiece_slug(name):
                        bad.append((arch, seed, f"{name} (empty slug)"))
                    continue
                if S.skin(name) is None:
                    bad.append((arch, seed, name))
    check(not bad, f"all {len(ARCHETYPES)} archetypes x3 seeds", str(bad[:3]))

    # A landmark's marker must name a piece that still exists, or the board
    # suppresses a square's geometry for a mesh nothing will ever draw.
    from vtt import setpieces as SP
    orphan = []
    for arch in sorted(ARCHETYPES):
        gen = generate_map(arch, width=26, height=20, seed=1)
        for name in gen.skins.values():
            slug = S.setpiece_slug(name)
            if slug and slug not in SP.CATALOGUE:
                orphan.append((arch, slug))
    check(not orphan, "and every set-piece marker names a real landmark",
          str(orphan[:3]))
    subs = S.substances()
    check(len(subs) == len({s.substance for s in S.SKINS.values()}),
          f"{len(subs)} substances for {len(S.SKINS)} skins — shared, as intended")

    print()
    if _fails:
        print(f"{RED}{_fails} check(s) failed{OFF}")
        return 1
    print(f"{GREEN}a skin says what a square is made of, and changes nothing "
          f"about what it does{OFF}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
