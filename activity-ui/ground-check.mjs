/* What is the ground under a crate made of?
 *
 *  THE GROUND UNDER AN OBJECT IS NOT MADE OF THE OBJECT. The renderer had one
 *  mesh builder per square, chosen from that square's own tile code, and the
 *  floor fan went into it — so a crate square was drawn in the crate's
 *  material right out to its edges, and every crate came with a square yard of
 *  pine floor around it. `groundSlot` is the answer and it is pure arithmetic
 *  over the grid, so it can be checked without a browser. The LOOK is
 *  `board-look.mjs`, which needs one.
 *
 *  Run: `node ground-check.mjs` (Windows node under WSL). No preview server
 *  and no build — it bundles the module straight out of src.
 */
import { build } from "esbuild";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const dir = mkdtempSync(join(tmpdir(), "ground-"));
const out = join(dir, "boardView.mjs");
await build({ entryPoints: ["src/lib/boardView.ts"], bundle: true, format: "esm",
              platform: "neutral", outfile: out, logLevel: "error" });
const { groundSlot, dominantFloor } = await import(pathToFileURL(out).href);

const board = (rows, extra = {}) => ({
  width: rows[0].length, height: rows.length, square_ft: 5,
  terrain: rows, elevation: {}, ...extra,
});

let fails = 0;
const eq = (got, want, what) => {
  const ok = got === want;
  console.log(`${ok ? "  ok  " : "FAIL  "}${what}${ok ? "" : ` — ${got} != ${want}`}`);
  if (!ok) fails++;
};

// A crate standing in a road takes the ROAD, not its own timber.
const street = board([
  "=====",
  "==o==",
  "=====",
]);
eq(groundSlot(street, 2, 1), "=", "a crate in a road stands on the road");
eq(groundSlot(street, 0, 0), "=", "...and a road square is still itself");

// The local truth beats the average: this board is mostly grass, and the
// crate is on the road all the same.
const verge = board([
  "ggggggggg",
  "ggg=o=ggg",
  "ggggggggg",
  "ggggggggg",
]);
eq(groundSlot(verge, 4, 1), "=", "a crate on a road through a meadow is on the road");
eq(dominantFloor(verge), "g", "...even though the board is mostly grass");

// A TREE IN A STAND has no floor touching it at all — measured, that is 28.6%
// of every object square in the game and 796 of 861 of them are on a
// clearing, which is a green carpet under every wood on every wooded board.
const wood = board([
  "gggggggg",
  "gTTTTTTg",
  "gTTTTTTg",
  "gTTTTTTg",
  "gggggggg",
]);
eq(groundSlot(wood, 3, 2), "g", "a tree ringed by trees stands on the wood's own floor");
eq(groundSlot(wood, 1, 1), "g", "...and one at the edge of the stand on the grass beside it");

// A skin travels with the floor, or a taproom's crate stands on bare boards
// that belong to no room.
const inn = board([
  "......",
  "..o...",
  "......",
], { skins: { codes: { ".": "taproom-floor" } } });
eq(groundSlot(inn, 2, 1), ".@taproom-floor",
   "the ground under a crate keeps the floor's own SKIN");

// A wall covers its own ground, so nothing there is visible to get wrong and
// the buried-face rules stay as they were.
eq(groundSlot(board(["###", "###", "###"]), 1, 1), "#",
   "a wall is left alone — it fills its square");

// A board with no floor anywhere falls back to the object's own material,
// which is exactly where this started and is the right answer for it.
eq(groundSlot(board(["ooo", "ooo", "ooo"]), 1, 1), "o",
   "with no floor anywhere, a crate is still a crate");

rmSync(dir, { recursive: true, force: true });
console.log(fails ? `\n${fails} failed` : "\nthe ground under everything is ground");
process.exit(fails ? 1 : 0);
