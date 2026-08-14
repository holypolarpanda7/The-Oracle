/* Does a creature standing behind something LOOK like it?
 *
 *  A token is a DOM element over the canvas, so it is drawn in front of the
 *  room by construction — occlusion is computed (`occludedAt` in boardView.ts)
 *  and then DRAWN, and both halves can only be checked by looking. The demo
 *  board puts the Goblin Skulker behind the mill's pillars for exactly this.
 *
 *  Usage: npm run build && node vite/bin/vite.js preview --port 4173,
 *  then `node occlusion-shot.mjs [base]`. Windows node under WSL.
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
const BASE = process.argv[2] || process.env.BASE || "http://localhost:4173/";
const OUT = process.env.OUT || "./vtt-shots";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
page.on("pageerror", (e) => console.log("PAGE ERROR:", e.message));
await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);
await page.locator("text=Kara Emberfall").first().click();
await page.waitForTimeout(600);
const btn = page.locator("button", { hasText: /enter/i }).first();
if (await btn.count()) await btn.click().catch(() => {});
await page.waitForTimeout(2500);

const input = page.locator(".promptbar input");
await input.fill("I shoot the goblin");
await input.press("Enter");
await page.waitForTimeout(4000);

const named = async () => page.evaluate(() =>
  [...document.querySelectorAll(".vtt-token")].map((el) => ({
    who: (el.getAttribute("title") || "").split(" —")[0],
    occluded: el.classList.contains("occluded"),
  })));

const rows = await named();
for (const r of rows) console.log(`${r.occluded ? "hidden " : "in view"}  ${r.who}`);
const skulker = rows.find((r) => /Skulker/.test(r.who));
console.log(`\nskulker behind the pillars: ${skulker ? skulker.occluded : "NOT ON THE BOARD"}`);
console.log(`others in view: ${rows.filter((r) => !/Skulker/.test(r.who)).every((r) => !r.occluded)}`);

await page.locator(".vtt").screenshot({ path: `${OUT}/06-occluded.png` });
// Zoomed in on the pillars, which is where the silhouette has to read.
const box = await page.locator(".vtt-board").boundingBox();
await page.mouse.move(box.x + box.width * 0.4, box.y + box.height * 0.4);
for (let i = 0; i < 4; i++) { await page.mouse.wheel(0, -240); await page.waitForTimeout(120); }
await page.waitForTimeout(500);
await page.locator(".vtt").screenshot({ path: `${OUT}/07-occluded-close.png` });
await browser.close();
