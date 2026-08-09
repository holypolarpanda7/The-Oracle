import { chromium } from "playwright";
const BASE = process.env.BASE || "http://localhost:4176/";
const OUT = process.env.OUT || "./vtt-shots";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);
await page.locator("text=Kara Emberfall").first().click();
await page.waitForTimeout(600);
const btn = page.locator("button", { hasText: /enter/i }).first();
if (await btn.count()) await btn.click().catch(() => {});
await page.waitForTimeout(2500);

await page.locator(".promptbar input").fill("I shoot the goblin");
await page.locator(".promptbar input").press("Enter");
await page.waitForTimeout(4000);

const toggle = page.locator('.vtt button[title*="board"]').first();
console.log("toggle found:", await toggle.count(), "| title:", await toggle.getAttribute("title"));

// Flip to flat and back, screenshotting each.
await toggle.click();
await page.waitForTimeout(1200);
console.log("after 1st click:", await toggle.getAttribute("title"));
await page.locator(".vtt").screenshot({ path: `${OUT}/toggle-flat.png` });

await toggle.click();
await page.waitForTimeout(1200);
console.log("after 2nd click:", await toggle.getAttribute("title"));
await page.locator(".vtt").screenshot({ path: `${OUT}/toggle-iso.png` });

console.log("tokens still drawn:", await page.locator(".vtt-token").count());
console.log(errors.length ? `ERRORS:\n${errors.join("\n")}` : "no page errors");
await browser.close();
