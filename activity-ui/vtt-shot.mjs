import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
const BASE = process.env.BASE || "http://localhost:4173/";
const OUT = process.env.OUT || "./vtt-shots";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
page.on("pageerror", (e) => console.log("PAGE ERROR:", e.message));
page.on("console", (m) => { if (m.type() === "error") console.log("CONSOLE:", m.text()); });
await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);

// Landing -> enter the demo character
const enter = page.locator("text=Kara Emberfall").first();
await enter.click();
await page.waitForTimeout(600);
// Some landing layouts need an explicit enter button
const btn = page.locator("button", { hasText: /enter/i }).first();
if (await btn.count()) { await btn.click().catch(()=>{}); }
await page.waitForTimeout(2500);
await page.screenshot({ path: `${OUT}/01-play.png` });

// Trigger the fight (and thus the board)
const input = page.locator(".promptbar input");
await input.fill("I shoot the goblin");
await input.press("Enter");
await page.waitForTimeout(4000);
await page.evaluate(() => { const el = document.querySelector(".play"); if (el) el.scrollTop = 0; });
await page.waitForTimeout(400);
await page.screenshot({ path: `${OUT}/02-board.png` });
await page.locator(".vtt").screenshot({ path: `${OUT}/02b-board-only.png` });
console.log("vtt present:", await page.locator(".vtt").count());
console.log("tokens:", await page.locator(".vtt-token").count());
console.log("canvas:", await page.locator(".vtt-board canvas").count());

// Select my token and look at the movement wash
const mine = page.locator(".vtt-token.mine").first();
if (await mine.count()) {
  await mine.click();
  await page.waitForTimeout(900);
  await page.locator(".vtt").screenshot({ path: `${OUT}/03-selected.png` });
  const box = await page.locator(".vtt-board").boundingBox();
  await page.mouse.move(box.x + box.width * 0.42, box.y + box.height * 0.35);
  await page.waitForTimeout(500);
  await page.locator(".vtt").screenshot({ path: `${OUT}/04-path.png` });
  await page.mouse.click(box.x + box.width * 0.42, box.y + box.height * 0.35);
  await page.waitForTimeout(900);
  await page.locator(".vtt").screenshot({ path: `${OUT}/05-moved.png` });
}
await browser.close();
console.log("done");
