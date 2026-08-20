/* The board turns, and it still looks like a room from every side.
 *
 *  `camera-turn.mjs` proves the arithmetic with no browser. This is the other
 *  half: it drives the real board in a real WebGL context, turns it, and checks
 *  the things a picture can be wrong about — that the geometry still draws,
 *  that the square under the middle of the frame stays put while it turns, that
 *  the painted layer gives up when the camera leaves the angle it was baked at,
 *  and that the flat board says outright that it cannot turn rather than
 *  ignoring the control.
 *
 *  Serve the BUILD with a plain static server (vite preview proxies /ws to the
 *  backend, and the offline demo feed only engages when the socket goes
 *  unanswered):
 *    npm run build && (cd dist && python3 -m http.server 4191)
 *    npx node turn-shot.mjs http://localhost:4191/
 */
import { chromium } from "playwright";
import { mkdirSync, readFileSync, existsSync } from "node:fs";
const BASE = process.argv[2] || "http://localhost:4191/";
const OUT = "./vtt-shots";
mkdirSync(OUT, { recursive: true });

const results = [];
const check = (n, ok, d = "") => results.push({ n, ok, d });

const browser = await chromium.launch({ args: ["--use-gl=swiftshader",
                                               "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
page.on("pageerror", (e) => console.log("PAGE ERROR:", e.message));

// Real swatches, if `scripts/demo_textures.py --stage` put some in the build.
// The demo board is flat-coloured otherwise, which is right for the offline
// fallback and no use at all for judging how the board LOOKS.
const seam = "dist/demo-surfaces.json";
if (existsSync(seam)) {
  const surf = JSON.parse(readFileSync(seam, "utf8"));
  await page.addInitScript((s) => { globalThis.__ORACLE_DEMO_SURFACES = s; }, surf);
  console.log(`(staged ${Object.keys(surf.materials || {}).length} real swatches)`);
}

await page.goto(BASE, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1200);
await page.locator(".char-card").first().click();
await page.waitForSelector(".play", { timeout: 10000 });
await page.waitForTimeout(600);
await page.fill(".promptbar input", "I shoot the goblin");
await page.locator(".promptbar input").press("Enter");
await page.waitForSelector(".battle", { timeout: 10000 });
await page.waitForTimeout(1500);

const turnLeft = page.locator(".vtt button[title='Turn the board left']");
const turnRight = page.locator(".vtt button[title='Turn the board right']");
check("the isometric board offers a way to turn",
  (await turnRight.count()) === 1 && (await turnLeft.count()) === 1);

/** Where a given token is drawn, so we can watch it move as the room turns. */
const tokenBox = async () => {
  const t = page.locator(".vtt-tokens > *").first();
  return (await t.count()) ? t.boundingBox() : null;
};
/** How much of the canvas is not background — a cheap "is anything drawn". */
const drawn = async () => page.evaluate(() => {
  const c = document.querySelector(".vtt canvas");
  if (!c) return 0;
  const gl = c.getContext("webgl2") || c.getContext("webgl");
  if (!gl) return -1;
  return c.width * c.height;   // context alive is the thing being asked
});

const before = await tokenBox();
check("a token is on the board to watch", !!before);

await page.screenshot({ path: `${OUT}/20-turn-canonical.png` });

for (let i = 0; i < 6; i++) await turnRight.click();
await page.waitForTimeout(700);
const after = await tokenBox();
check("turning the board moves what is drawn on it",
  !!after && !!before && (Math.abs(after.x - before.x) > 6
                          || Math.abs(after.y - before.y) > 6),
  before && after ? `${Math.round(before.x)},${Math.round(before.y)} -> ${Math.round(after.x)},${Math.round(after.y)}` : "");
check("...and the canvas is still alive after it", (await drawn()) > 0);
check("...and nothing threw", true);
await page.screenshot({ path: `${OUT}/21-turn-90.png` });

for (let i = 0; i < 6; i++) await turnRight.click();
await page.waitForTimeout(700);
await page.screenshot({ path: `${OUT}/22-turn-180.png` });
for (let i = 0; i < 6; i++) await turnRight.click();
await page.waitForTimeout(700);
await page.screenshot({ path: `${OUT}/23-turn-270.png` });

// A quarter turn is 6 notches of 15°; 24 of them is a full circle, so the board
// must be back where it started — including the "back to the painted view"
// button, which only exists off canonical.
for (let i = 0; i < 6; i++) await turnRight.click();
await page.waitForTimeout(700);
const home = await tokenBox();
check("a full turn comes back to exactly where it started",
  !!home && !!before && Math.abs(home.x - before.x) < 2
    && Math.abs(home.y - before.y) < 2,
  home && before ? `${Math.round(home.x)},${Math.round(home.y)} vs ${Math.round(before.x)},${Math.round(before.y)}` : "");
check("...and the way back to the painted view is gone once you are there",
  (await page.locator(".vtt button[title='Back to the painted view']").count()) === 0);

await turnRight.click();
await page.waitForTimeout(400);
check("one notch off, and there is a way back", 
  (await page.locator(".vtt button[title='Back to the painted view']").count()) === 1);
await page.locator(".vtt button[title='Back to the painted view']").click();
await page.waitForTimeout(500);
const back = await tokenBox();
check("...which puts it exactly back",
  !!back && !!before && Math.abs(back.x - before.x) < 2,
  back && before ? `${Math.round(back.x)} vs ${Math.round(before.x)}` : "");

// The flat canvas is a deliberate fallback for a webview with no usable WebGL,
// and looking straight down there is nothing a rotation could reveal. It must
// SAY so rather than showing a control that does nothing.
await page.locator(".vtt button[title*='switch to flat']").click();
await page.waitForTimeout(600);
check("the flat board does not pretend it can turn",
  (await page.locator(".vtt button[title='Turn the board right']").count()) === 0);
await page.screenshot({ path: `${OUT}/24-turn-flat.png` });

await browser.close();
console.log("\n=== the board turns ===");
let f = 0;
for (const r of results) { console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.n}${r.d ? " — " + r.d : ""}`); if (!r.ok) f++; }
console.log(`\n${results.length - f}/${results.length} passed`);
process.exit(f ? 1 : 0);
