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

/** A board is one string per row; `extra` carries elevation or landmarks.
 *
 *  Near walls are CUT on every board now — the painted layer that used to
 *  suppress the cutaway is gone — so a wall between the lens and a creature
 *  stops hiding it, and the cases below say so. What still occludes is
 *  furniture, raised ground, upper storeys and landmark meshes. */
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
// A NEAR WALL IS CUT, AND SO IT NO LONGER HIDES ANYTHING. It used not to be,
// wherever a painting was showing — under a painting the wall was a thing in
// the picture, so the geometry stayed whole and occluded. The painted layer is
// gone (a photograph of the room from one place, against a camera that turns),
// so the cutaway is unconditional and this is the standing truth: what hides a
// creature is the FURNITURE, the ground and the far geometry, never the room's
// own near walls. The picture and the board's account of who is hidden agree,
// which is the only invariant that was ever load-bearing here.
const WALL_NEAR = plain([".....", ".....", ".....", "...#.", "....."]);
const WALL_FAR = plain([".....", ".....", ".....", ".....", "....#"]);

check(!occludedAt(WALL_NEAR, 2, 2, 1, 0),
      "a near wall is CUT, so it hides nothing — you can see over the stub");
check(!occludedAt(WALL_FAR, 2, 2, 1, 0),
      "a wall two squares away does not either — the ray has climbed past it");

// THE PITCH IS A THING THE PLAYER MOVES NOW, so the ray's climb is an argument
// rather than a constant. Reading it off the canonical basis left a board
// tilted down to 12 degrees marking creatures hidden behind walls the lens is
// looking straight over, and one tilted up to 78 marking nobody hidden at all.
// A PILLAR, not a wall: walls are cut and hide nothing at any angle, and the
// thing being tested here is the CLIMB rather than the cutaway.
const PIT_LOW = 14, PIT_HIGH = 74;
const PILLAR_FAR = plain([".....", ".....", ".....", ".....", "....O"]);
check(occludedAt(PILLAR_FAR, 2, 2, 1, 0, 45, 0, PIT_LOW),
      "from low down, a pillar two squares off DOES hide a creature");
check(!occludedAt(PILLAR_FAR, 2, 2, 1, 0, 45, 0, PIT_HIGH),
      "...and from nearly overhead it does not");
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
check(!occludedAt(WALL_NEAR, 2, 2, 1, 20), "a wyvern 20 ft up clears it too");

// A landmark's mesh, at the height it declares rather than at its tiles'.
check(occludedAt(board([".....", ".....", ".....", ".....", "....."],
                       { setpieces: [{ x: 3, y: 3, w: 2, d: 2, height_ft: 40 }] }),
                 2, 2, 1, 0),
      "a 40-ft landmark occludes even where it stamps low tiles");

check(!occludedAt(WALL_NEAR, 2, 2, 3, 0),
      "a Huge creature is not hidden by a wall inside its own footprint");

rmSync(dir, { recursive: true, force: true });
// The cutaway's own consequence, stated here so it is not a surprise. On an
// unpainted board the near walls come down, and a wall that has been cut to a
// stub is not in anybody's way — so after a cutaway what hides a creature is
// the FURNITURE and the ground, never the room's own walls. That is the point
// of cutting them, and the two answers agree because `drawnTopFt` applies the
// same reduction the geometry does.
const bare = (rows) => ({ width: rows[0].length, height: rows.length,
                          square_ft: 5, terrain: rows, elevation: {} });
check(!occludedAt(bare([".....", ".....", ".....", "...#.", "....."]), 2, 2, 1, 0),
      "unpainted, a near wall is cut away and hides nothing");
check(occludedAt(bare([".....", ".....", ".....", "...O.", "....."]), 2, 2, 1, 0),
      "...while a pillar, which is never cut, still does");
check(occludedAt(bare([".....", ".....", ".....", ".....", "....."],
                 ), 2, 2, 1, 0) === false, "...and open floor never did");

console.log(fails ? `\n${fails} FAILED` : "\nall good");
process.exit(fails ? 1 : 0);
