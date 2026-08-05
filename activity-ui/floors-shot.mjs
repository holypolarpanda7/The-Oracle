import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
const BASE = process.env.BASE || "http://localhost:4173/";
const OUT = process.env.OUT || "./vtt-shots";
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
page.on("pageerror", (e) => console.log("PAGE ERROR:", e.message));
await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);
await page.locator("text=Kara Emberfall").first().click();
await page.waitForTimeout(600);
const btn = page.locator("button", { hasText: /enter/i }).first();
if (await btn.count()) { await btn.click().catch(() => {}); }
await page.waitForTimeout(2500);
const input = page.locator(".promptbar input");
await input.fill("I shoot the goblin");
await input.press("Enter");
await page.waitForTimeout(4000);
console.log("floors strip:", await page.locator(".vtt-floors").count());
console.log("buttons:", await page.locator(".vtt-floors > button").allTextContents());
await page.locator(".vtt").screenshot({ path: `${OUT}/floor-ground.png` });
const gallery = page.locator('.vtt-floors > button:has-text("Gallery")');
if (await gallery.count()) {
  await gallery.first().click();
  await page.waitForTimeout(600);
  console.log("peek banner:", (await page.locator(".vtt-peeking").allTextContents()).join(" | "));
  await page.locator(".vtt").screenshot({ path: `${OUT}/floor-gallery.png` });
}
await browser.close();
