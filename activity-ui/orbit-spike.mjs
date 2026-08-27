/* THE ORBIT SPIKE: what breaks when the camera comes down?
 *
 *  Several of the board's drawing rules were justified by a FIXED 40-degree
 *  pitch and are expected to fail at a low one — a wall is drawn as a thin
 *  skin because a ring of full cubes read as a tray from up here, and a rock
 *  face's buried sides are not drawn at all. Both are invisible at 40 and
 *  holes at 20. The point is to find the ones nobody predicted.
 *
 *  Stage a board first, then:
 *    npx node orbit-spike.mjs http://localhost:4190/ street
 */
import { chromium } from "playwright";
import { existsSync, mkdirSync, readFileSync } from "node:fs";

const BASE = process.argv[2] || "http://localhost:4190/";
const TAG = process.argv[3] || "orbit";
const PITCHES = [60, 40, 25, 12];
const OUT = "./vtt-shots";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ args: ["--use-gl=swiftshader",
                                               "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
page.on("pageerror", (e) => console.log("PAGE ERROR:", e.message));
if (existsSync("dist/demo-surfaces.json"))
  await page.addInitScript((x) => { globalThis.__ORACLE_DEMO_SURFACES = x; },
    JSON.parse(readFileSync("dist/demo-surfaces.json", "utf8")));
await page.goto(BASE, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1500);
await page.locator(".char-card").first().click();
await page.waitForSelector(".play", { timeout: 15000 });
await page.waitForTimeout(800);
await page.fill(".promptbar input", "I shoot the goblin");
await page.locator(".promptbar input").press("Enter");
await page.waitForSelector(".vtt-board", { timeout: 15000 });
await page.waitForTimeout(2500);

const board = await page.locator(".vtt-board canvas").first().boundingBox();
for (const [i, pitch] of PITCHES.entries()) {
  await page.evaluate((p) => { globalThis.__ORACLE_ORBIT = { pitch: p, yaw: 45 }; },
                      pitch);
  // A real viewport change, because the board only repaints when something
  // asks it to and a synthetic resize event does not reach its observer.
  await page.setViewportSize({ width: 1400 + (i % 2 ? 1 : 2), height: 900 });
  await page.waitForTimeout(900);
  const b = await page.locator(".vtt-board canvas").first().boundingBox();
  await page.screenshot({ path: `${OUT}/${TAG}-pitch${pitch}.png`, clip: b });
  console.log(`shot pitch ${pitch}`);
}
await browser.close();
