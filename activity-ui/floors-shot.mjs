/* Looking at a floor is not standing on it.
 *
 *  A board can have real STOREYS — `TacticalMap.levels`, one terrain grid each
 *  with its own `base_ft` — and only ONE is ever drawn, because a gallery drawn
 *  over the hall it overlooks is unreadable. So a player needs to be able to
 *  peek upstairs before deciding to climb, to see which storey they are ON
 *  separately from which is DRAWN, and to find the one button that actually
 *  moves them.
 *
 *  This harness rotted: it clicked `text=Kara Emberfall` on the landing, which
 *  has not been there for some time, and it asserted nothing at all — it
 *  printed and screenshotted, so it exited 0 whether the storey switcher worked
 *  or not. Both halves are fixed here: it enters the way every other harness
 *  enters, and it CHECKS.
 *
 *  Serve the BUILD with a plain static server — `vite preview` proxies /ws to
 *  the backend, so the offline demo feed only engages when the socket goes
 *  unanswered:
 *    npm run build && (cd dist && python3 -m http.server 4191)
 *    npx node floors-shot.mjs http://localhost:4191/
 */
import { chromium } from "playwright";
import { mkdirSync, readFileSync, existsSync } from "node:fs";
const BASE = process.argv[2] || process.env.BASE || "http://localhost:4191/";
const OUT = process.env.OUT || "./vtt-shots";
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

// `domcontentloaded`, not `networkidle`: the demo feed keeps talking, so a
// page waiting for the network to go quiet waits for something that never
// happens.
await page.goto(BASE, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1200);
await page.locator(".char-card").first().click();
await page.waitForSelector(".play", { timeout: 10000 });
await page.waitForTimeout(600);
await page.fill(".promptbar input", "I shoot the goblin");
await page.locator(".promptbar input").press("Enter");
await page.waitForSelector(".battle", { timeout: 10000 });
await page.waitForTimeout(1500);

const strip = page.locator(".vtt-floors");
const buttons = page.locator(".vtt-floors > button");
check("a board with two storeys shows the floor strip",
  (await strip.count()) === 1);
const names = (await buttons.allTextContents()).map((t) => t.replace(/\s+/g, " ").trim());
check("...with a button per storey", names.length >= 2, names.join(" | "));
// A building reads upward, and a list that puts the cellar above the roof
// takes a moment to parse every single time.
check("...top floor first", /gallery/i.test(names[0] ?? ""), names[0]);
check("...each saying how high it is", /\d+\s*ft/i.test(names[0] ?? ""), names[0]);

const on = page.locator(".vtt-floors > button.on");
const here = page.locator(".vtt-floors > button.here");
check("the strip says which storey is DRAWN", (await on.count()) === 1);
check("...and which one you are ON, separately", (await here.count()) === 1);
check("...and they start as the same one",
  (await on.first().innerText()) === (await here.first().innerText()));

await page.locator(".vtt").screenshot({ path: `${OUT}/40-floor-ground.png` });

// ---- peeking ------------------------------------------------------------
const gallery = page.locator('.vtt-floors > button:has-text("Gallery")');
check("there is a gallery to look at", (await gallery.count()) > 0);
await gallery.first().click();
await page.waitForTimeout(600);

const banner = page.locator(".vtt-peeking");
check("looking at another storey says so, in words",
  (await banner.count()) === 1,
  (await banner.allTextContents()).join(" ").replace(/\s+/g, " ").trim());
const bannerText = (await banner.first().innerText()).replace(/\s+/g, " ");
check("...naming both the floor you are looking at and the one you are on",
  /looking at/i.test(bannerText) && /you are on/i.test(bannerText), bannerText);
check("the DRAWN floor moved", /gallery/i.test(await on.first().innerText()),
  await on.first().innerText());
check("...and the one you are STANDING on did not — looking is not climbing",
  /mill/i.test(await here.first().innerText()), await here.first().innerText());
// Peeking must not conjure a way to climb: the button that moves you exists
// only where a connector is under your feet, and the server re-checks it.
check("...and peeking does not offer a way up that isn't under your feet",
  (await page.locator(".vtt-take-stairs").count()) === 0);

await page.locator(".vtt").screenshot({ path: `${OUT}/41-floor-gallery.png` });

await banner.locator("button").first().click();
await page.waitForTimeout(500);
check("there is a way back, and it goes back",
  (await page.locator(".vtt-peeking").count()) === 0
  && /mill/i.test(await on.first().innerText()),
  await on.first().innerText());

await browser.close();
console.log("\n=== looking at a floor is not standing on it ===");
let f = 0;
for (const r of results) { console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.n}${r.d ? " — " + r.d : ""}`); if (!r.ok) f++; }
console.log(`\n${results.length - f}/${results.length} passed`);
process.exit(f ? 1 : 0);
