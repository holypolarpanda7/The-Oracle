/* The camera turns, and everything that has to agree still agrees.
 *
 *  The board's camera used never to rotate, and the reasoning written in
 *  `isocam.ts` said that offering rotation would cost three things at once: the
 *  closed-form inverse, pan-and-zoom being a plain translate-and-scale, and a
 *  painting staying aligned. Two of those three survive — for any FIXED yaw the
 *  projection is still a plain affine map — and the third is the real price,
 *  which is why the painted layer fades out as the camera leaves the angle it
 *  was baked at.
 *
 *  All of that is arithmetic, so it is checked here with no browser at all. The
 *  LOOK needs one; that is `vtt-shot.mjs`.
 *
 *  Run: `node camera-turn.mjs` (Windows node under WSL). No preview server and
 *  no build — it bundles the modules straight out of src.
 */
import { build } from "esbuild";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const dir = mkdtempSync(join(tmpdir(), "turn-"));
const camOut = join(dir, "isocam.mjs");
const bvOut = join(dir, "boardView.mjs");
await build({ entryPoints: ["src/lib/isocam.ts"], bundle: true, format: "esm",
              platform: "neutral", outfile: camOut, logLevel: "error" });
await build({ entryPoints: ["src/lib/boardView.ts"], bundle: true, format: "esm",
              platform: "neutral", outfile: bvOut, logLevel: "error" });
const cam = await import(pathToFileURL(camOut).href);
const { awayDir, cutAwayAt, cuttingAway, drawnTopFt, occludedAt } =
  await import(pathToFileURL(bvOut).href);

let fails = 0;
const check = (ok, what, detail = "") => {
  console.log(`${ok ? "  ok  " : "FAIL  "}${what}${detail ? "  — " + detail : ""}`);
  if (!ok) fails++;
};
const close = (a, b, eps = 1e-9) => Math.abs(a - b) < eps;

const ANGLES = [0, 17, 45, 90, 123.4, 180, 270, 359.9];

// ---------------------------------------------------------------------------
console.log("\n1. the canonical view is untouched");

// Everything the SERVER makes — every depth map, every painting, the alignment
// gate itself — is at YAW_DEG. Parameterising the yaw must not have moved it by
// a floating-point hair, or every baked picture slides off its geometry.
for (const [x, y, z] of [[0, 0, 0], [5, 3, 7], [12.5, 2.25, 7.75], [-2, 0, 4]]) {
  const a = cam.project(x, y, z);
  const b = cam.project(x, y, z, cam.YAW_DEG);
  check(a.x === b.x && a.y === b.y && a.depth === b.depth,
        `project(${x},${y},${z}) means the same with the angle spelled out`);
}
check(cam.basis().yawDeg === cam.YAW_DEG, "the default basis IS the canonical one");
for (const k of ["rayX", "rayZ", "rayRise"]) {
  check(close(cam.basis()[k], { rayX: cam.RAY_X, rayZ: cam.RAY_Z,
                                rayRise: cam.RAY_RISE }[k]),
        `the exported ${k} is the canonical basis's`);
}

// ---------------------------------------------------------------------------
console.log("\n2. every angle is a real camera");

for (const yaw of ANGLES) {
  const b = cam.basis(yaw);
  const len = (v) => Math.hypot(v[0], v[1], v[2]);
  const dot = (u, v) => u[0] * v[0] + u[1] * v[1] + u[2] * v[2];
  const ok = close(len(b.RIGHT), 1, 1e-12) && close(len(b.UP), 1, 1e-12)
    && close(len(b.FORWARD), 1, 1e-12)
    && close(dot(b.RIGHT, b.UP), 0, 1e-12)
    && close(dot(b.RIGHT, b.FORWARD), 0, 1e-12)
    && close(dot(b.UP, b.FORWARD), 0, 1e-12);
  check(ok, `at ${yaw}° the basis is orthonormal — no roll, no skew`);
}

// tan(pitch) is how steeply the view ray climbs, and turning the camera about
// the vertical axis cannot change that. If this ever varies, occlusion is
// keying off the wrong thing.
const rise0 = cam.basis(0).rayRise;
check(ANGLES.every((a) => close(cam.basis(a).rayRise, rise0, 1e-12)),
      "the ray's CLIMB is tan(pitch) and does not depend on yaw",
      rise0.toFixed(6));
check(ANGLES.some((a) => !close(cam.basis(a).rayX, cam.basis(0).rayX, 1e-6)),
      "...while which way it runs across the floor certainly does");

// ---------------------------------------------------------------------------
console.log("\n3. it still inverts in closed form");

for (const yaw of ANGLES) {
  let worst = 0;
  for (const [x, z] of [[0, 0], [5, 3], [12.5, 7.75], [-2, 4], [30, 24]]) {
    for (const wy of [0, 1.6, 4]) {
      const p = cam.project(x, wy, z, yaw);
      const [rx, rz] = cam.unproject(p.x, p.y, wy, yaw);
      worst = Math.max(worst, Math.abs(rx - x), Math.abs(rz - z));
    }
  }
  check(worst < 1e-9, `at ${yaw}° project -> unproject is the identity`,
        worst.toExponential(1));
}

// ---------------------------------------------------------------------------
console.log("\n4. turning actually turns something");

const b45 = cam.boundsOf(30, 20, 3, 0, 45);
const b135 = cam.boundsOf(30, 20, 3, 0, 135);
check(!close(b45.minX, b135.minX, 1e-6) || !close(b45.maxY, b135.maxY, 1e-6),
      "a rectangular board frames differently from a different corner");
const full = cam.project(7, 1, 3, 45);
const round = cam.project(7, 1, 3, 405);
check(close(full.x, round.x, 1e-9) && close(full.y, round.y, 1e-9),
      "...and a whole turn comes back to exactly where it started");
check(cam.wrapYaw(-315) === 45 && cam.wrapYaw(405) === 45 && cam.wrapYaw(45) === 45,
      "an angle is normalised to [0, 360)");

// The whole point of turning: what is standing in front of you changes.
// PAINTED, so the cutaway is off and the wall is a wall — under a painting the
// wall is a thing in the picture, and the geometry is a depth-only proxy for
// exactly this question. (Unpainted, the same wall is cut away and correctly
// stops hiding anything; that is section 6.)
const board = (rows) => ({ width: rows[0].length, height: rows.length,
                           square_ft: 5, terrain: rows, elevation: {},
                           iso_image_id: 7 });
// A wall on the +x/+z side of the creature at 2,2 — in the way at 45°, and
// behind it once the camera has come round to the far corner.
const WALL = board([".....", ".....", ".....", "...#.", "....."]);
check(occludedAt(WALL, 2, 2, 1, 0, 45),
      "a wall between the creature and the lens hides it");
check(!occludedAt(WALL, 2, 2, 1, 0, 225),
      "...and the same wall, seen from the other side, hides nothing");
check(occludedAt(WALL, 2, 2, 1, 0) === occludedAt(WALL, 2, 2, 1, 0, 45),
      "the default answer is still the canonical one");
// Turned the other way the ray leaves by the NEAR edges, which the march used
// not to test for at all.
check(ANGLES.every((a) => typeof occludedAt(WALL, 2, 2, 1, 0, a) === "boolean"),
      "the march terminates whichever way the ray runs off the board");

// ---------------------------------------------------------------------------
console.log("\n5. the painting knows it is a photograph of one place");

check(cam.paintOpacity(cam.YAW_DEG) === 1, "at the baked angle it is all there");
check(cam.paintOpacity(cam.YAW_DEG + cam.PAINT_HOLD_DEG) === 1,
      "...and a nudge does not touch it — a picture that flickers reads as a bug");
check(cam.paintOpacity(cam.YAW_DEG + cam.PAINT_FADE_DEG) === 0
      && cam.paintOpacity(cam.YAW_DEG - cam.PAINT_FADE_DEG) === 0,
      "...gone by the fade angle, on both sides");
const mid = cam.paintOpacity(
  cam.YAW_DEG + (cam.PAINT_HOLD_DEG + cam.PAINT_FADE_DEG) / 2);
check(mid > 0 && mid < 1, "...and it DISSOLVES rather than switching off",
      mid.toFixed(3));
check(cam.paintOpacity(cam.YAW_DEG + 7) === cam.paintOpacity(cam.YAW_DEG - 7),
      "the fade is symmetric about the angle it was baked at");
check(cam.paintOpacity(cam.YAW_DEG + 360) === 1
      && cam.paintOpacity(cam.YAW_DEG - 359) === cam.paintOpacity(cam.YAW_DEG + 1),
      "...and measured the SHORT way round, so 359° off is 1° off");
check(cam.paintOpacity(cam.YAW_DEG + 180) === 0,
      "from behind, there is no painting at all");

// ---------------------------------------------------------------------------
console.log("\n6. the near walls come down");

// A room is a box and the camera looks into it over a corner, so the two walls
// nearest the lens stand between the viewer and the fight. Turning made that
// unignorable: swing a quarter and the wall that used to be the far one is a
// ten-foot slab across the front of the board.
const room = (rows, extra = {}) => ({
  width: rows[0].length, height: rows.length, square_ft: 5,
  terrain: rows, elevation: {}, ...extra,
});
const BOX = room(["########", "#......#", "#......#", "#......#", "########"]);
const marks = (scene, yaw) => BOX.terrain.map((r, z) =>
  [...r].map((c, x) => (cutAwayAt(scene, x, z, yaw) ? "c" : c)).join("")).join("\n");

check(cuttingAway(BOX, 45), "an unpainted board cuts away — the geometry IS the picture");
check(cutAwayAt(BOX, 7, 2, 45) && cutAwayAt(BOX, 4, 4, 45),
      "at 45° the walls at greater x and z are the near ones");
check(!cutAwayAt(BOX, 0, 2, 45) && !cutAwayAt(BOX, 4, 0, 45),
      "...and the far walls stay up, or the room has no back to read against");
check(cutAwayAt(BOX, 0, 2, 225) && !cutAwayAt(BOX, 7, 2, 225),
      "turn round and it is the other two",
      String(awayDir(225)));
const counts = ANGLES.map((yaw) => BOX.terrain.reduce((acc, r, z) =>
  acc + [...r].filter((_, x) => cutAwayAt(BOX, x, z, yaw)).length, 0));
check(counts.every((n) => n > 0 && n < 20),
      "at every angle some walls come down and not all of them",
      counts.join(", "));

// Structure only — which is the same thing as "never vary a height the rules
// quote", arrived at from the other side: a crate, a low wall, a table and an
// altar are OBJECTS, and every one of them has a quoted cover height.
const CLUTTER = room(["########", "#.oww.n#", "#..AA..#", "#......#", "########"]);
check(!"ownA".split("").some((c) => {
  for (let z = 0; z < CLUTTER.terrain.length; z++)
    for (let x = 0; x < CLUTTER.terrain[z].length; x++)
      if (CLUTTER.terrain[z][x] === c && cutAwayAt(CLUTTER, x, z, 45)) return true;
  return false;
}), "nothing whose height the RULES quote is ever cut");

// A MASS is not a wall in front of the room; it is the edge of the world, and
// slicing the top off it reads as a mountain someone has been at with a knife.
// The track is at the far corner; the near corner is solid rock for more than
// CUTAWAY_DEPTH squares in every direction.
const MASS = room(["R..RRRRR", "R..RRRRR", "RRRRRRRR", "RRRRRRRR",
                   "RRRRRRRR", "RRRRRRRR", "RRRRRRRR", "RRRRRRRR"]);
check(!cutAwayAt(MASS, 7, 7, 45) && !cutAwayAt(MASS, 6, 6, 45),
      "a rock MASS deeper than a wall is left alone — it is the edge of the "
      + "world, not a wall in front of the room");
check(cutAwayAt(MASS, 3, 2, 45),
      "...but the rock actually leaning over the track does come down");

// Where a painting is showing, the wall is a thing in that PICTURE, and not
// drawing the geometry removes nothing anybody can see — the geometry there is
// a depth-only proxy, so cutting it would only delete the occlusion.
const PAINTED = room(["########", "#......#", "#......#", "#......#", "########"],
                     { iso_image_id: 7 });
check(!cuttingAway(PAINTED, cam.YAW_DEG) && !cutAwayAt(PAINTED, 7, 2, cam.YAW_DEG),
      "a painted board at its baked angle cuts nothing");
check(cuttingAway(PAINTED, cam.YAW_DEG + 90),
      "...and starts cutting exactly when the painting has gone");

// The board's own account of who is hidden has to follow what it DREW, or the
// cutaway reveals a creature the board still calls hidden — which is the same
// disagreement between picture and grid that the whole occlusion march exists
// to avoid, arriving from the other side.
const NEARWALL = room(["....", "....", "..#.", "...."]);
check(cutAwayAt(NEARWALL, 2, 2, 45) && !occludedAt(NEARWALL, 1, 1, 1, 0, 45),
      "a wall the cutaway took down stops being reported as in the way");
// So what hides a creature after a cutaway is the FURNITURE, not the room —
// which is the right answer: a pillar has a quoted height and is never cut.
const PILLAR = room([".....", ".....", ".....", "...O.", "....."]);
check(!cutAwayAt(PILLAR, 3, 3, 45) && occludedAt(PILLAR, 2, 2, 1, 0, 45),
      "...and a pillar, which is never cut, still does");
// A painted board cuts nothing, so a painted wall hides exactly what it did.
const PAINTWALL = room(["....", "....", "..#.", "...."], { iso_image_id: 7 });
check(occludedAt(PAINTWALL, 1, 1, 1, 0, cam.YAW_DEG),
      "and under a painting the wall is in the picture, so it hides as it always did");

// A board with STOREYS asks the same question per floor, and `scene.terrain`
// is the ground floor and always has been. Reading it for an upper storey cuts
// the gallery to the plan of the hall underneath — walls missing where the
// gallery has them, walls standing where it does not.
const TWO = {
  width: 6, height: 6, square_ft: 5, elevation: {},
  terrain: ["######", "#....#", "#....#", "#....#", "#....#", "######"],
  levels: [
    { name: "Hall", base_ft: 0,
      terrain: ["######", "#....#", "#....#", "#....#", "#....#", "######"] },
    // A gallery is the strip you build; everywhere else is open to the hall.
    { name: "Gallery", base_ft: 15,
      terrain: ["      ", " #### ", " #..# ", " #..# ", " #### ", "      "] },
  ],
};
check(cutAwayAt(TWO, 4, 5, 45, 0), "the hall's own near wall is cut on the hall");
check(!cutAwayAt(TWO, 4, 5, 45, 1),
      "...and the SAME square is not, on the gallery, where there is no wall");
check(cutAwayAt(TWO, 4, 3, 45, 1),
      "...while the gallery's own near wall is cut");
check(drawnTopFt(TWO, 4, 5, 45, 1) === -Infinity,
      "open air on an upper storey is a hole, not the hall's masonry",
      String(drawnTopFt(TWO, 4, 5, 45, 1)));

rmSync(dir, { recursive: true, force: true });
console.log(`\n${fails ? `${fails} FAILED` : "the camera turns, and everything that must agree still agrees"}`);
process.exit(fails ? 1 : 0);
