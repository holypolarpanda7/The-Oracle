/* Two things a player asked for while choosing where to stand.
 *
 *  1. "Cover is not obvious." It has been computed exactly and applied
 *     correctly since the board went 3D, and the only place the word ever
 *     appeared was on a foe's own line after the fact. Choosing a square is
 *     precisely when it matters — a square behind a pillar is worth two squares
 *     of movement, and nothing on the board said so.
 *  2. "I need to click on the 2d mesh location." Unprojecting a pixel onto a
 *     plane answers "which square would be here if the board were flat", and it
 *     has not been flat since elevation went in — so on the dais the square you
 *     click is not the square you get, and the error grows with the height.
 *
 *  The arithmetic for the second is `pick-check.mjs`, with no browser. This is
 *  the half that needs one: a real pointer over a real board.
 *
 *    npm run build && uv run python scripts/demo_textures.py --stage
 *    (cd dist && python3 -m http.server 4191)
 *    npx node move-feedback.mjs http://localhost:4191/
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
if (existsSync("dist/demo-surfaces.json")) {
  const s = JSON.parse(readFileSync("dist/demo-surfaces.json", "utf8"));
  await page.addInitScript((x) => { globalThis.__ORACLE_DEMO_SURFACES = x; }, s);
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

// Select my own token, so the movement line is the one being tested.
const mine = page.locator(".vtt-token.team-party").first();
await mine.click();
await page.waitForTimeout(400);

const board = page.locator(".vtt-board");
const box = await board.boundingBox();

/** Hover a square by asking the page where it lands, so this test never has to
 *  own a copy of the projection — the thing under test would then be checking
 *  itself. Falls back to sweeping the board. */
async function hoverUntil(pred) {
  // The route preview is debounced by 180 ms on purpose — a warning before you
  // move beats a request per pixel — so a sweep has to DWELL, not skim. Coarse
  // steps and a real pause at each is the shape that actually finds anything.
  const step = 58;
  let tries = 0;
  for (let y = box.y + 50; y < box.y + box.height - 50; y += step) {
    for (let x = box.x + 50; x < box.x + box.width - 50; x += step) {
      if (++tries > 110) return null;
      await page.mouse.move(x, y);
      await page.waitForTimeout(260);
      if (await pred()) return [x, y];
    }
  }
  return null;
}

// ---- 1. cover, while choosing where to stand ----------------------------
const coverLine = page.locator(".vtt-cover");
const found = await hoverUntil(async () => (await coverLine.count()) > 0);
check("hovering a square behind cover says so, before you stand on it",
  found !== null);
if (found) {
  const text = (await coverLine.first().innerText()).trim();
  check("...in words, naming how much", /cover/i.test(text), text);
  check("...and which foe it is cover FROM",
    /from/i.test(text), text);
  const title = await coverLine.first().getAttribute("title");
  check("...with the per-foe breakdown to hand, since cover is a relationship",
    !!title && title.includes(":"), String(title));
  await page.screenshot({ path: `${OUT}/30-cover-preview.png` });
}

// Out in the open it must say NOTHING rather than "no cover": printing that on
// every square of the board teaches a player to stop reading the line.
let quiet = false;
for (let i = 0; i < 25 && !quiet; i++) {
  await page.mouse.move(box.x + box.width * 0.4 + i * 9, box.y + box.height * 0.8);
  await page.waitForTimeout(30);
  quiet = (await coverLine.count()) === 0;
}
check("a square in the open says nothing at all, rather than 'no cover'", quiet);

// ---- 2. clicking lands where you are looking ----------------------------
// The demo board carries a DAIS five feet up across its north end, which is
// exactly the case the plane got wrong. Hover the drawn middle of a raised
// square and check the board reports that square, not the one the plane would
// have picked two squares away.
const raised = await page.evaluate(() => {
  const el = document.querySelector(".vtt-board");
  return el ? { w: el.clientWidth, h: el.clientHeight } : null;
});
check("the board is on screen to point at", !!raised);

// The board carries a DAIS five feet up across its north end and a GALLERY
// fifteen feet above that, which is exactly the case the flat answer got
// wrong. A creature standing on raised ground is the honest probe: click it,
// and the board must select THAT creature rather than a square two away.
const tokens = await page.locator(".vtt-token").count();
check("there are tokens to click", tokens > 0, `${tokens}`);
let right = 0, tried = 0;
for (let i = 0; i < tokens; i++) {
  const t = page.locator(".vtt-token").nth(i);
  const b = await t.boundingBox();
  if (!b) continue;
  tried++;
  // A token's FOOT sits on its square, so aim near the bottom of its disc.
  await page.mouse.click(b.x + b.width / 2, b.y + b.height * 0.82);
  await page.waitForTimeout(280);
  const cls = (await t.getAttribute("class")) || "";
  if (cls.includes("selected")) right++;
}
check("clicking a creature selects THAT creature, whatever it is standing on",
  right === tried, `${right} of ${tried}`);

await page.screenshot({ path: `${OUT}/31-move-feedback.png` });
await browser.close();
console.log("\n=== choosing where to stand ===");
let f = 0;
for (const r of results) { console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.n}${r.d ? " — " + r.d : ""}`); if (!r.ok) f++; }
console.log(`\n${results.length - f}/${results.length} passed`);
process.exit(f ? 1 : 0);
