"""Assert the isometric camera means the same thing in Python and TypeScript.

``vtt/isocam.py`` rasterizes the depth map the painted board layer is
conditioned on; ``activity-ui/src/lib/isocam.ts`` draws the geometry the player
actually sees. They are two implementations of one camera, and if they drift by
a degree the painting stops sitting on the walls — every shadow lands beside
the thing casting it, and nothing in either program is individually wrong.

So this is the gate. It runs the SAME points through both and compares:

    uv run python scripts/iso_alignment_check.py

Runs node through npx, as the other harnesses do — plain `node` is not on the
WSL PATH here.
Add ``--depth`` to also write a depth map and its wireframe to ``material-probe/``
so the rasterizer can be looked at, which the numbers cannot tell you.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: Points chosen to exercise every axis independently, plus a few that would
#: still agree under a sign error if you only tested the origin and a diagonal.
SAMPLES = [
    (0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1),
    (5, 0, 3), (3, 0, 5), (12.5, 2.25, 7.75), (-2, 1, 4), (30, 0, 24),
]

#: (screen x, screen y, plane height) for the inverse.
UNPROJECTS = [(0, 0, 0), (3.5, -1.25, 0), (-7, 2, 2), (11.1, 6.6, 0.5)]

#: (board w, board h, tallest) for the canonical framing.
BOUNDS = [(20, 14, 2.0), (30, 24, 2.0), (8, 8, 0.0), (60, 40, 3.0)]

#: Squares to run through the per-instance rules. Includes big coordinates on
#: purpose: the hash multiplies, and the two languages only diverge once the
#: product passes 2^32 and JavaScript's bitwise ops wrap it.
SQUARES = [(0, 0), (1, 0), (0, 1), (3, 7), (12, 5), (59, 39), (40, 40), (7, 123)]

#: Every way a square's floor can meet the outside — all sixteen, because the
#: outline is worked out from four booleans and there is no reason to sample.
#: The mitred bottoms are the interesting half: a per-edge offset and a mitred
#: one differ at every turn, and this is hand-written twice.
ENDS = [(bool(m & 1), bool(m & 2), bool(m & 4), bool(m & 8)) for m in range(16)]

#: A solid part put through every quarter turn. Rotation used to be four
#: numbers; a prismatoid rotates every vertex of two polygons, and getting the
#: map wrong in one language turns a leaning post the other way.
SOLID = [[[0.1, 0.2], [0.8, 0.25], [0.7, 0.9], [0.15, 0.85]],
         [[0.3, 0.4], [0.6, 0.4], [0.6, 0.6], [0.3, 0.6]], 0.0, 1.0]

#: An awkward taper for the mitre: not a round number, and big enough that a
#: per-edge offset and a mitred one are visibly different at a cut corner.
SKIRT_INSET_PROBE = 0.37

#: (x, z, degrees) for a landmark's quarter turn.
#:
#: Gated because the two languages turn OPPOSITE WAYS by default and it cost a
#: real bug: three.js `rotation.y` sends (x,z) to (z,-x) where the tiles — and
#: therefore the cover — go to (-z,x). Off-axis points are here because a turn
#: applied to (1,0) alone still agrees under a transpose.
SPIN = [(1, 0, 90), (0, 1, 90), (1, 0, 180), (2, 3, 90), (2, 3, 270),
        (-1.5, 0.5, 90), (3, -2, 180), (1, 1, 0)]

_TS = r"""
import { project, unproject, boundsOf, YAW_DEG, PITCH_DEG } from "./isocam.js";
import {
  variantOf, yawOf, heightScale, hullFootprint, outAxis, rotatePart, sameBody,
  setpieceRotate, cornerLiftFt,
} from "./boardView.js";
const samples = %s, unprojects = %s, bounds = %s, squares = %s;
const ends = %s, solid = %s, inset = %s, spin = %s;
const groundCases = %s;
console.log(JSON.stringify({
  yaw: YAW_DEG, pitch: PITCH_DEG,
  project: samples.map(([x, y, z]) => { const p = project(x, y, z); return [p.x, p.y, p.depth]; }),
  unproject: unprojects.map(([sx, sy, wy]) => unproject(sx, sy, wy)),
  bounds: bounds.map(([w, h, t]) => { const b = boundsOf(w, h, t); return [b.minX, b.maxX, b.minY, b.maxY]; }),
  variant: squares.map(([x, z]) => variantOf(x, z, 3)),
  yaws: squares.map(([x, z]) => yawOf(x, z)),   // NB not "yaw": that key is the camera constant
  // "A" quotes a cover height and must never jitter; "#" is free to.
  heightA: squares.map(([x, z]) => heightScale("A", x, z)),
  heightW: squares.map(([x, z]) => heightScale("#", x, z)),
  foot: ends.map(([w, e, n, s]) => {
    const f = hullFootprint(w, e, n, s, inset);
    return [f.pts, f.ends, f.low];
  }),
  // Which way is the outdoors, for every way a square can be enclosed.
  outward: ends.map(([w, e, n, s]) => outAxis((ax, az) => {
    if (ax === -1 && az === 0) return !w;
    if (ax === 1 && az === 0) return !e;
    if (ax === 0 && az === -1) return !n;
    if (ax === 0 && az === 1) return !s;
    return true;
  }, 0, 0)),
  turned: [0, 1, 2, 3].map((t) => rotatePart(solid, t)),
  // A landmark's quarter turn. Must move the mesh the way it moves the tiles.
  spin: spin.map(([x, z, d]) => setpieceRotate(x, z, d)),
  // A deck, the rail round it and the mast through it are one hull; the water
  // beside them is not.
  bodies: [sameBody("sea-deck", "railing"), sameBody("sea-deck", "mast"),
           sameBody("sea-deck", ""), sameBody("cliff", "coral")],
  // The GROUND's height at a corner. Two squares share every corner, so the
  // two programs disagreeing there is a seam torn down the middle of a
  // hillside — and the painting is then baked over the wrong one.
  ground: groundCases.map(([rows, elev, x, z, cx, cz]) =>
    cornerLiftFt({ width: rows[0].length, height: rows.length, square_ft: 5,
                   terrain: rows, elevation: elev }, x, z, cx, cz, 0)),
}));
"""


#: Ground corners the two programs must agree about, as
#: ``(rows, elevation, square, corner)``. Every case is a distinction the rule
#: turns on: a knoll seen from both of the squares that share its corner, a
#: LEDGE (which must stay a cliff), something LAID beside soft ground, and the
#: board's own edge where fewer than four squares meet.
GROUND_CASES = [
    (["ggg", "ggg", "ggg"], {"1,1": 5}, 1, 1, 1, 1),
    (["ggg", "ggg", "ggg"], {"1,1": 5}, 0, 0, 1, 1),
    (["ggg", "ggg", "ggg"], {"1,1": 10}, 1, 1, 1, 1),
    (["ggg", "ggg", "ggg"], {"1,1": 10}, 0, 0, 1, 1),
    (["g.g", "ggg", "ggg"], {"1,0": 5}, 1, 0, 1, 1),
    (["ggg", "ggg", "ggg"], {"0,0": 5}, 0, 0, 0, 0),
    (["gg", "gg"], {"0,0": 5, "1,1": 5}, 0, 0, 1, 1),
    (["mmm", "m~m", "mmm"], {"1,1": -5}, 1, 1, 1, 1),
]


def _run_ts() -> dict:
    """Run the TS camera through node and hand back what it computed.

    `tsc` is already a dependency, so the honest route is to compile the one
    module and import it. Everything stays RELATIVE to activity-ui and inside
    it: node and tsc here are Windows processes, and a WSL path like /tmp/xyz
    means nothing to them — that is the same namespace split CLAUDE.md warns
    about for ComfyUI, in a quieter disguise.
    """
    import shutil

    ui = ROOT / "activity-ui"
    work = ui / ".isocam-check"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        r = subprocess.run(
            ["npx", "tsc", "src/lib/isocam.ts", "src/lib/boardView.ts",
             "--outDir", ".isocam-check",
             "--module", "es2022", "--target", "es2022", "--skipLibCheck"],
            cwd=ui, capture_output=True, text=True, shell=(sys.platform == "win32"))
        if r.returncode != 0:
            raise RuntimeError(f"tsc failed:\n{r.stdout}\n{r.stderr}")
        # tsc emits extensionless relative specifiers ("./boardShapes.generated")
        # and Node's ESM loader insists on the extension. Nothing in the project
        # hits this because vite resolves imports itself; only this probe runs
        # the emitted JS directly.
        for js in work.glob("*.js"):
            js.write_text(
                re.sub(r'(from\s+"\./[^"]+)"',
                       lambda m: m.group(1) + '.js"', js.read_text(encoding="utf8")),
                encoding="utf8")

        (work / "probe.mjs").write_text(
            _TS % (json.dumps(SAMPLES), json.dumps(UNPROJECTS), json.dumps(BOUNDS),
                   json.dumps(SQUARES), json.dumps(ENDS), json.dumps(SOLID),
                   json.dumps(SKIRT_INSET_PROBE), json.dumps(SPIN),
                   json.dumps(GROUND_CASES)),
            encoding="utf8")
        r = subprocess.run(["npx", "node", ".isocam-check/probe.mjs"], cwd=ui,
                           capture_output=True, text=True,
                           shell=(sys.platform == "win32"))
        if r.returncode != 0:
            raise RuntimeError(f"node failed:\n{r.stdout}\n{r.stderr}")
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--depth", action="store_true",
                    help="also write a sample depth map to material-probe/")
    ap.add_argument("--tol", type=float, default=1e-9)
    a = ap.parse_args(argv)

    from vtt import isocam as py

    print("running the TypeScript camera through node ...")
    ts = _run_ts()

    bad = 0

    def check(label: str, got, want, tol: float) -> None:
        nonlocal bad
        ok = abs(got - want) <= tol
        if not ok:
            bad += 1
        print(f"  {'OK ' if ok else 'DRIFT'} {label:<34} py={got!r:<24} ts={want!r}")

    # The camera is only half of what the two sides have to agree about. Once
    # objects stopped being plain boxes, their SHAPES became just as
    # load-bearing: the depth map is rasterized from them and the geometry is
    # built from them, and a mismatch paints furniture the player is not
    # looking at. Python owns them and the TypeScript is generated, so the
    # check is simply whether the generated file is current.
    print("shapes")
    from scripts.gen_board_shapes import main as gen_shapes
    if gen_shapes(["--check"]) != 0:
        bad += 1
    else:
        print("  OK  generated shape tables match the Python source")

    print("\nconstants")
    check("yaw", py.YAW_DEG, ts["yaw"], 0)
    check("pitch", py.PITCH_DEG, ts["pitch"], 0)

    print("\nproject(world) -> screen + depth")
    for (x, y, z), t in zip(SAMPLES, ts["project"]):
        p = py.project(x, y, z)
        check(f"({x},{y},{z}).x", p.x, t[0], a.tol)
        check(f"({x},{y},{z}).y", p.y, t[1], a.tol)
        check(f"({x},{y},{z}).depth", p.depth, t[2], a.tol)

    print("\nunproject(screen, plane) -> world")
    for (sx, sy, wy), t in zip(UNPROJECTS, ts["unproject"]):
        wx, wz = py.unproject(sx, sy, wy)
        check(f"({sx},{sy}|{wy}).x", wx, t[0], a.tol)
        check(f"({sx},{sy}|{wy}).z", wz, t[1], a.tol)

    print("\nbounds_of(board) -> canonical framing")
    for (w, h, tall), t in zip(BOUNDS, ts["bounds"]):
        b = py.bounds_of(w, h, tall)
        for name, got, want in (("minX", b.min_x, t[0]), ("maxX", b.max_x, t[1]),
                                ("minY", b.min_y, t[2]), ("maxY", b.max_y, t[3])):
            check(f"{w}x{h}+{tall} {name}", got, want, a.tol)

    # Per-instance rules. Same square must pick the same arrangement, the same
    # turn and the same height on both sides, or the painter conditions on one
    # room and the player looks at another.
    print("\nper-instance variety (variant / yaw / height)")
    from vtt.terrain import cover_height_ft
    for (x, z), v, y, ha, hw in zip(SQUARES, ts["variant"], ts["yaws"],
                                    ts["heightA"], ts["heightW"]):
        check(f"({x},{z}) variant", py.variant_of(x, z, 3), v, 0)
        check(f"({x},{z}) yaw", py.yaw_of(x, z), y, 0)
        check(f"({x},{z}) height A (rules-quoted)",
              py.height_scale("A", x, z, cover_height_ft("A")), ha, 1e-12)
        check(f"({x},{z}) height #", py.height_scale("#", x, z, cover_height_ft("#")),
              hw, 1e-12)

    # A landmark's quarter turn. The two languages turn opposite ways by
    # default, so this is gated rather than trusted: if the mesh turns one way
    # and the tiles the other, the picture rotates and the cover does not, and
    # a creature takes three-quarters cover from a face now behind it.
    print("\nsetpiece spin (mesh must turn the way the tiles turn)")
    from vtt import setpieces as _sp
    for (x, z, deg), t in zip(SPIN, ts["spin"]):
        gx, gz = _sp.rotate_xz(x, z, deg)
        check(f"({x},{z}) turned {deg} .x", gx, t[0], a.tol)
        check(f"({x},{z}) turned {deg} .z", gz, t[1], a.tol)

    # The floor's OUTLINE. This is the one that cuts a vessel's stair-stepped
    # deck into a continuous diagonal, and unlike the shape tables it is a
    # function rather than data — so it cannot be generated and is written
    # twice. All sixteen ways a square can meet the outside, because there are
    # only sixteen.
    print("\nfootprint (which sides are closed, and where their bottoms sit)")
    for (e_w, e_e, e_n, e_s), t, turns in zip(ENDS, ts["foot"], ts["outward"]):
        pts, ends, low = py.footprint(e_w, e_e, e_n, e_s, SKIRT_INSET_PROBE)
        tag = "".join(c for c, on in zip("WENS", (e_w, e_e, e_n, e_s)) if on) or "-"
        check(f"[{tag}] vertices", len(pts), len(t[0]), 0)
        for i, ((px, pz), tp) in enumerate(zip(pts, t[0])):
            check(f"[{tag}] p{i}.x", px, tp[0], a.tol)
            check(f"[{tag}] p{i}.z", pz, tp[1], a.tol)
        for i, (e, te) in enumerate(zip(ends, t[1])):
            check(f"[{tag}] edge{i} closed", int(e), int(te), 0)
        # The mitre. A per-edge offset and a mitred one agree along a straight
        # run and differ at every turn, which is exactly where a hull opens up.
        for i, ((px, pz), tp) in enumerate(zip(low, t[2])):
            check(f"[{tag}] bottom{i}.x", px, tp[0], a.tol)
            check(f"[{tag}] bottom{i}.z", pz, tp[1], a.tol)
        # ...and which way this square calls the outdoors.
        def inside(ax: int, az: int, _f=(e_w, e_e, e_n, e_s)) -> bool:
            w, e, n, s = _f
            return {(-1, 0): not w, (1, 0): not e,
                    (0, -1): not n, (0, 1): not s}.get((ax, az), True)
        check(f"[{tag}] outward turns", py.out_axis(inside, 0, 0), turns, 0)

    print("\nrotate_part on a SOLID (every vertex, every quarter turn)")
    for turns, t in enumerate(ts["turned"]):
        bottom, top, y0, y1 = py.rotate_part(
            (tuple(map(tuple, SOLID[0])), tuple(map(tuple, SOLID[1])),
             SOLID[2], SOLID[3]), turns)
        for label, got, want in (("bottom", bottom, t[0]), ("top", top, t[1])):
            for i, ((gx, gz), w) in enumerate(zip(got, want)):
                check(f"turn {turns} {label}[{i}].x", gx, w[0], a.tol)
                check(f"turn {turns} {label}[{i}].z", gz, w[1], a.tol)
        check(f"turn {turns} y0", y0, t[2], a.tol)
        check(f"turn {turns} y1", y1, t[3], a.tol)

    print("\nsame body (which squares are one THING, so grow no side between)")
    from vtt.skins import same_body
    for (lhs, rhs), t in zip((("sea-deck", "railing"), ("sea-deck", "mast"),
                              ("sea-deck", ""), ("cliff", "coral")),
                             ts["bodies"]):
        check(f"{lhs} / {rhs or '(none)'}", int(same_body(lhs, rhs)), int(t), 0)

    # Round-trip: projecting a point on the ground and unprojecting it must
    # return the same square. Catches a self-consistent but wrong pair.
    print("\nthe GROUND at a corner (two squares share every one of them)")
    for (rows, elev, x, z, cx, cz), t in zip(GROUND_CASES, ts["ground"]):
        check(f"{''.join(rows)} {elev} sq({x},{z}) corner({cx},{cz})",
              py.corner_lift_ft(rows, elev, x, z, cx, cz), t, a.tol)

    print("\nround trip (project -> unproject, ground plane)")
    for x, _y, z in SAMPLES:
        p = py.project(x, 0, z)
        wx, wz = py.unproject(p.x, p.y, 0)
        check(f"({x},{z}) x", wx, float(x), 1e-9)
        check(f"({x},{z}) z", wz, float(z), 1e-9)

    if a.depth:
        from vtt.mapgen import generate_map
        from vtt.terrain import tile_height_ft

        out = ROOT / "material-probe"
        out.mkdir(exist_ok=True)
        gen = generate_map("dungeon-room", width=24, height=18, seed=7)
        png = py.depth_image(gen.grid.rows, height_ft=tile_height_ft,
                             square_ft=5, structure={"#", "R"})
        (out / "iso-depth.png").write_bytes(png)
        print(f"\nwrote {(out / 'iso-depth.png').relative_to(ROOT)} "
              f"({len(png) // 1024} kB) — near is white")

    print()
    if bad:
        print(f"{bad} value(s) DRIFTED — the painting will not line up. "
              "vtt/isocam.py and activity-ui/src/lib/isocam.ts must agree.")
        return 1
    print("the two cameras agree exactly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
