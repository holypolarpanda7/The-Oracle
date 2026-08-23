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
const turn = page.locator(".vtt-icon", { hasText: "⟳" });
if (await turn.count()) {
  await turn.first().click();
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${OUT}/${TAG}-b.png` });
}
await browser.close();
console.log(`shot ${TAG} -> ${OUT}/${TAG}-a.png`);
