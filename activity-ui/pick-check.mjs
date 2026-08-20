/* Does a click land on the square you are LOOKING at?
 *
 *  Unprojecting a pixel onto a plane answers "which square would be here if the
 *  board were flat", and the board has not been flat since elevation went in.
 *  On a dais the square you click is not the square you get, and the error grows
 *  with the height — which is exactly backwards, because the whole point of high
 *  ground is that people stand on it.
 *
 *  Pure arithmetic over the camera and the grid, so no browser: project the
 *  CENTRE of a square that is really up in the air, then ask what is under that
 *  pixel and check the answer is the square we started from.
 *
 *  Run: `node pick-check.mjs` (Windows node under WSL). No preview server and
 *  no build — it bundles the modules straight out of src.
 */
import { build } from "esbuild";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const dir = mkdtempSync(join(tmpdir(), "pick-"));
const camOut = join(dir, "isocam.mjs");
const bvOut = join(dir, "boardView.mjs");
await build({ entryPoints: ["src/lib/isocam.ts"], bundle: true, format: "esm",
              platform: "neutral", outfile: camOut, logLevel: "error" });
await build({ entryPoints: ["src/lib/boardView.ts"], bundle: true, format: "esm",
              platform: "neutral", outfile: bvOut, logLevel: "error" });
const cam = await import(pathToFileURL(camOut).href);
const { squareUnderRay } = await import(pathToFileURL(bvOut).href);

let fails = 0;
const check = (ok, what, d = "") => {
  console.log(`${ok ? "  ok  " : "FAIL  "}${what}${d ? "  — " + d : ""}`);
  if (!ok) fails++;
};

const SQ = 5;
/** A 12x12 room, painted (so nothing is cut away), with a dais. */
const board = (elevation = {}) => ({
  width: 12, height: 12, square_ft: SQ, iso_image_id: 7,
  terrain: Array.from({ length: 12 }, (_, z) =>
    (z === 0 || z === 11 ? "############"
      : "#" + "..........".slice(0) + "#")),
  elevation,
});

/** What the OLD code did: unproject onto the storey's floor and floor it. */
const flatPick = (px, py, yaw) => {
  const [wx, wz] = cam.unproject(px, py, 0, yaw);
  return [Math.floor(wx), Math.floor(wz)];
};
/** What the new code does. */
const pick = (scene, px, py, yaw, tallestFt, deepestFt = 0) => {
  const [wx, wz] = cam.unproject(px, py, 0, yaw);
  return squareUnderRay(scene, wx, wz, yaw, tallestFt, deepestFt);
};
/** Where the middle of a square's own FLOOR lands on screen. */
const centreOf = (scene, x, z, yaw) => {
  const ft = scene.elevation?.[`${x},${z}`] ?? 0;
  return cam.project(x + 0.5, ft / SQ, z + 0.5, yaw);
};

console.log("\n1. flat ground was never the problem");
const FLAT = board();
for (const [x, z] of [[1, 1], [5, 6], [8, 9], [3, 8]]) {
  const p = centreOf(FLAT, x, z, cam.YAW_DEG);
  const got = pick(FLAT, p.x, p.y, cam.YAW_DEG, 10);
  check(got[0] === x && got[1] === z,
        `a click on the middle of ${x},${z} picks ${x},${z}`, String(got));
}

console.log("\n2. ...and raised ground was");
// A dais across the north end, ten feet up — a LEDGE, the height the rules
// make you decide about.
const elev = {};
for (let z = 1; z <= 4; z++) for (let x = 1; x <= 10; x++) elev[`${x},${z}`] = 10;
const DAIS = board(elev);
let oldWrong = 0;
for (const [x, z] of [[2, 2], [5, 3], [9, 1], [7, 4]]) {
  const p = centreOf(DAIS, x, z, cam.YAW_DEG);
  const got = pick(DAIS, p.x, p.y, cam.YAW_DEG, 20);
  const was = flatPick(p.x, p.y, cam.YAW_DEG);
  if (was[0] !== x || was[1] !== z) oldWrong++;
  check(got[0] === x && got[1] === z,
        `standing 10 ft up at ${x},${z}, a click there picks ${x},${z}`,
        `got ${got}, the plane would have said ${was}`);
}
check(oldWrong === 4,
      "...and the plane got every one of them wrong, which is the bug",
      `${oldWrong}/4`);

console.log("\n3. every angle, and the deeper the error the worse it was");
for (const yaw of [0, 45, 90, 137, 180, 270]) {
  let ok = 0, tried = 0;
  for (const [x, z] of [[2, 2], [5, 3], [9, 1], [7, 4], [3, 4]]) {
    const p = centreOf(DAIS, x, z, yaw);
    const got = pick(DAIS, p.x, p.y, yaw, 20);
    tried++;
    if (got[0] === x && got[1] === z) ok++;
  }
  check(ok === tried, `at ${yaw}° a raised square picks itself`, `${ok}/${tried}`);
}

console.log("\n4. what it must not break");
// A SUNKEN square is the same bug the other way round: a reef channel is ten
// feet down, so the view ray reaches it BEYOND the ground plane rather than
// short of it — at a negative `u` the march used not to walk at all.
const low = {};
for (let z = 6; z <= 9; z++) for (let x = 1; x <= 10; x++) low[`${x},${z}`] = -10;
const CHANNEL = board(low);
for (const [x, z] of [[3, 7], [2, 6], [4, 6]]) {
  const p = centreOf(CHANNEL, x, z, cam.YAW_DEG);
  const got = pick(CHANNEL, p.x, p.y, cam.YAW_DEG, 10, -10);
  check(got[0] === x && got[1] === z,
        `a square sunk ten feet at ${x},${z} picks itself`, String(got));
}
// ...and the same arithmetic says something true about a channel that is worth
// keeping: a square down a hole, with the rim between it and the lens, is not
// visible from here at all. The picker must not hand back a square the player
// cannot see — that is the same rule as "you pick what you can see", and it is
// what turning the camera is FOR.
const rimmed = centreOf(CHANNEL, 9, 9, cam.YAW_DEG);
const hiddenPick = pick(CHANNEL, rimmed.x, rimmed.y, cam.YAW_DEG, 10, -10);
check(!(hiddenPick[0] === 9 && hiddenPick[1] === 9),
      "a square down a channel behind its own rim is not clickable from here",
      String(hiddenPick));
const turned = centreOf(CHANNEL, 9, 9, cam.YAW_DEG + 180);
const shown = pick(CHANNEL, turned.x, turned.y, cam.YAW_DEG + 180, 10, -10);
check(shown[0] === 9 && shown[1] === 9,
      "...and turning the camera round is what makes it clickable",
      String(shown));

// "You pick what you can SEE" is the rule, and it has a consequence worth
// asserting: a wall really standing in front of a square takes the click, and
// the floor behind it cannot be reached. That is not a regression — it is what
// the picture shows, and it is why the cutaway matters.
const cornerP = centreOf(FLAT, 10, 10, cam.YAW_DEG);
const behind = pick(FLAT, cornerP.x, cornerP.y, cam.YAW_DEG, 10);
check(behind[0] === 11 && behind[1] === 11,
      "a painted wall in front of a square takes the click, as it looks like it should",
      String(behind));

// Off the board is still off the board: a click on empty space must not walk
// somebody off the map, which is the whole reason squareAt can return null.
const far = cam.project(40, 0, 40, cam.YAW_DEG);
const off = pick(FLAT, far.x, far.y, cam.YAW_DEG, 10);
check(off[0] >= FLAT.width || off[1] >= FLAT.height,
      "a click well off the board still reports a square off the board",
      String(off));

// And the march must terminate on a board with nothing on it at all.
const EMPTY = { width: 4, height: 4, square_ft: SQ, terrain: ["....", "....", "....", "...."], elevation: {} };
const e = cam.project(1.5, 0, 2.5, cam.YAW_DEG);
const eg = pick(EMPTY, e.x, e.y, cam.YAW_DEG, 0);
check(eg[0] === 1 && eg[1] === 2, "flat open ground with no height at all",
      String(eg));

rmSync(dir, { recursive: true, force: true });
console.log(`\n${fails ? `${fails} FAILED` : "a click lands where you are looking"}`);
process.exit(fails ? 1 : 0);
