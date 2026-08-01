/* The rolled properties on a piece of gear, and the forge price on each. */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
const BASE = process.env.BASE || "http://localhost:4173/";
const OUT = "./affix-shots";
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("pageerror", (e) => console.log("PAGE ERROR:", e.message));
await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);
await page.locator("text=Kara Emberfall").first().click();
await page.waitForTimeout(500);
const btn = page.locator("button", { hasText: /enter/i }).first();
if (await btn.count()) await btn.click().catch(() => {});
await page.waitForTimeout(2800);
await page.locator(".tab", { hasText: /Inventory/i }).click();
await page.waitForTimeout(400);
await page.locator(".ic-name", { hasText: /Keen Rapier/i }).click();
await page.waitForTimeout(900);
console.log("affix rows:", await page.locator(".affix").count(),
            "forge buttons:", await page.locator(".af-temper").count(),
            "labels:", await page.locator(".af-temper").allTextContents());
await page.screenshot({ path: `${OUT}/1-affixes.png` });
await page.locator(".item-modal").screenshot({ path: `${OUT}/2-modal.png` });
await browser.close();
