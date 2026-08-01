/* Setting out: the roads you know, costed by the code. */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
const OUT = "./route-shots"; mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("pageerror", (e) => console.log("PAGE ERROR:", e.message));
await page.goto(process.env.BASE || "http://localhost:4173/", { waitUntil: "networkidle" });
await page.waitForTimeout(1200);
await page.locator("text=Kara Emberfall").first().click();
await page.waitForTimeout(500);
const b = page.locator("button", { hasText: /enter/i }).first();
if (await b.count()) await b.click().catch(() => {});
await page.waitForTimeout(2800);
const input = page.locator(".promptbar input");
await input.fill("We set out for Millbrook");
await input.press("Enter");
await page.waitForTimeout(2500);
await page.locator(".scroll .txt").click();
await page.waitForTimeout(400);
console.log("roads:", await page.locator(".route").count(),
            await page.locator(".route b").allTextContents());
await page.screenshot({ path: `${OUT}/1-roads.png` });
await page.locator(".routes").screenshot({ path: `${OUT}/2-roads-only.png` });
// Picking one must send it as an action and clear the offer.
await page.locator(".route", { hasText: "the shortcut" }).click();
await page.waitForTimeout(1500);
console.log("roads after picking:", await page.locator(".route").count());
await browser.close();
