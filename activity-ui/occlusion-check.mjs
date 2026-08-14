/* Does the board know when something is standing in front of a creature?
 *
 *  `occludedAt` is pure grid arithmetic over the isometric camera, so it can be
 *  checked without a browser — which is the point of it being arithmetic. The
 *  LOOK is checked by `occlusion-shot.mjs`, which needs one.
 *
 *  Run: `node occlusion-check.mjs` (Windows node under WSL). No preview server
 *  and no build — it bundles the module straight out of src.
 */
import { build } from "esbuild";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const dir = mkdtempSync(join(tmpdir(), "occl-"));
const out = join(dir, "boardView.mjs");
await build({ entryPoints: ["src/lib/boardView.ts"], bundle: true, format: "esm",
              platform: "neutral", outfile: out, logLevel: "error" });
const { occludedAt } = await import(pathToFileURL(out).href);

/** A board is one string per row; `extra` carries elevation or landmarks. */
const board = (rows, extra = {}) => ({
  width: rows[0].length, height: rows.length, square_ft: 5,
  terrain: rows, elevation: {}, ...extra,
});

let fails = 0;
const check = (ok, what) => {
  console.log(`${ok ? "  ok  " : "FAIL  "}${what}`);
  if (!ok) fails++;
};

// The camera sits over the +x/+z corner, so anything in the way is at GREATER
// x and z. Every board below puts the creature at 2,2.
const plain = (rows, extra) => board(rows, extra);
const WALL_NEAR = plain([".....", ".....", ".....", "...#.", "....."]);
const WALL_FAR = plain([".....", ".....", ".....", ".....", "....#"]);

check(occludedAt(WALL_NEAR, 2, 2, 1, 0),
      "a 10-ft wall one square toward the camera hides a creature");
check(!occludedAt(WALL_FAR, 2, 2, 1, 0),
      "the same wall two squares away does not — the ray has climbed past it");
check(!occludedAt(plain([".....", ".....", "...o.", ".....", "....."]), 2, 2, 1, 0),
      "a 4-ft crate never hides a standing creature");
check(!occludedAt(plain([".....", ".#...", ".....", ".....", "....."]), 2, 2, 1, 0),
      "a wall BEHIND the creature, away from the camera, does not");
check(!occludedAt(plain([".....", ".....", "..#..", ".....", "....."]), 2, 2, 1, 0),
      "a creature is not in its own way");
check(!occludedAt(plain([".....", ".....", "...#.", ".....", "....."]), 2, 2, 1, 0),
      "a wall beside the diagonal covers half a figure, not the figure");

// A hole is not a low wall.
check(!occludedAt(board([".....", ".....", ".....", "...^.", "....."],
                        { elevation: { "2,2": -10 } }), 2, 2, 1, -10),
      "a chasm in front of a creature down a channel hides nothing");

// Elevation is DRAWN, so raised ground is an occluder like anything else.
const LEDGE = board([".....", ".....", ".....", ".....", "....."],
                    { elevation: { "3,3": 15 } });
check(occludedAt(LEDGE, 2, 2, 1, 0), "raised GROUND occludes");
check(!occludedAt(LEDGE, 3, 3, 1, 15), "the creature standing ON it sees out");
check(!occludedAt(WALL_NEAR, 2, 2, 1, 20), "a wyvern 20 ft up clears the wall");

// A landmark's mesh, at the height it declares rather than at its tiles'.
check(occludedAt(board([".....", ".....", ".....", ".....", "....."],
                       { setpieces: [{ x: 3, y: 3, w: 2, d: 2, height_ft: 40 }] }),
                 2, 2, 1, 0),
      "a 40-ft landmark occludes even where it stamps low tiles");

check(!occludedAt(WALL_NEAR, 2, 2, 3, 0),
      "a Huge creature is not hidden by a wall inside its own footprint");

rmSync(dir, { recursive: true, force: true });
console.log(fails ? `\n${fails} FAILED` : "\nall good");
process.exit(fails ? 1 : 0);
