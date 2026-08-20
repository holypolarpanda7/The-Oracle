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
const { occludedAt } = await import(pathToFileURL(bvOut).href);

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
const board = (rows) => ({ width: rows[0].length, height: rows.length,
                           square_ft: 5, terrain: rows, elevation: {} });
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

rmSync(dir, { recursive: true, force: true });
console.log(`\n${fails ? `${fails} FAILED` : "the camera turns, and everything that must agree still agrees"}`);
process.exit(fails ? 1 : 0);
