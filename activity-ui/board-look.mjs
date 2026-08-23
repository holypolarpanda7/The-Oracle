// Look at a REAL generated board in the browser, with its own materials.
//
// `scripts/demo_textures.py --board <archetype>` stages a generated layout and
// its swatches over the offline demo; this opens it, gets a fight going so the
// board comes out, and photographs it. Everything else in this directory looks
// at the demo's own mill, which is one small dungeon room and has never had a
// pool, a terrace of houses or a floating island on it.
//
//   uv run python scripts/demo_textures.py --board swamp --seed 3 --size 46x34
//   npm run build && (cd dist && python3 -m http.server 4190)
//   npx node board-look.mjs http://localhost:4190/ swamp
//
// NB the stage must be re-run AFTER a build: `npm run build` empties dist/.
// And run `--clear` when you are done: every other harness in here reads the
// same seam, and a staged street has no gallery for `floors-shot` to climb.
import { chromium } from "playwright";
import { existsSync, mkdirSync, readFileSync } from "node:fs";

const BASE = process.argv[2] || "http://localhost:4190/";
const TAG = process.argv[3] || "board";
const OUT = "./vtt-shots";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ args: ["--use-gl=swiftshader",
                                               "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
page.on("pageerror", (e) => console.log("PAGE ERROR:", e.message));
if (existsSync("dist/demo-surfaces.json")) {
  const staged = JSON.parse(readFileSync("dist/demo-surfaces.json", "utf8"));
  await page.addInitScript((x) => { globalThis.__ORACLE_DEMO_SURFACES = x; },
                           staged);
  console.log("staged:", Object.keys(staged).join(", "));
  console.log("water squares:", Object.keys(staged.water ?? {}).length);
}
await page.goto(BASE, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1500);
await page.locator(".char-card").first().click();
await page.waitForSelector(".play", { timeout: 15000 });
await page.waitForTimeout(800);
// The demo feed opens a board on a line that reads as a fight.
await page.fill(".promptbar input", "I shoot the goblin");
await page.locator(".promptbar input").press("Enter");
await page.waitForSelector(".vtt-board", { timeout: 15000 });
await page.waitForTimeout(2500);
console.log("canvases:", await page.locator(".vtt-board canvas").count());
await page.screenshot({ path: `${OUT}/${TAG}-a.png` });

// SCENERY IS NOT WHITE. `reshade` rewrites every vertex colour from the
// mesh's single `base`, which is right for terrain merged per material slot
// and wrong for the one builder that carries a colour PER PIECE: bushes,
// tussocks, deadfall, stumps and stones all went in with their own tints and
// came out white on the first shading pass. It had looked right for exactly
// one frame since fog shading went in, and nothing was ever going to notice —
// which is why this is a pixel count and not a unit test.
const board = await page.locator(".vtt-board canvas").first().boundingBox();
const shot = await page.screenshot({ clip: board });
const white = await page.evaluate(async (b64) => {
  const img = new Image();
  img.src = "data:image/png;base64," + b64;
  await img.decode();
  const c = document.createElement("canvas");
  c.width = img.width; c.height = img.height;
  const g = c.getContext("2d");
  g.drawImage(img, 0, 0);
  const d = g.getImageData(0, 0, c.width, c.height).data;
  let n = 0;
  for (let i = 0; i < d.length; i += 4) {
    if (d[i] > 228 && d[i + 1] > 228 && d[i + 2] > 228) n++;
  }
  return (n * 10000) / (d.length / 4) / 100;
}, shot.toString("base64"));
console.log(`near-white: ${white.toFixed(2)}% of the board`);
if (white > 0.15) {
  console.log("FAIL  the scenery is being painted white — see reshade/tints");
  process.exitCode = 1;
} else {
  console.log("PASS  scenery keeps its own colours through shading");
}
const turn = page.locator(".vtt-icon", { hasText: "⟳" });
if (await turn.count()) {
  await turn.first().click();
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${OUT}/${TAG}-b.png` });
}
await browser.close();
console.log(`shot ${TAG} -> ${OUT}/${TAG}-a.png`);
