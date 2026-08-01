/* The Chronicle overlay + suggested-action chips, against the offline demo.
   npm run build && npx vite preview --port 4173 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
const BASE = process.env.BASE || "http://localhost:4173/";
const OUT = process.env.OUT || "./chronicle-shots";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("pageerror", (e) => console.log("PAGE ERROR:", e.message));
page.on("console", (m) => { if (m.type() === "error") console.log("CONSOLE:", m.text()); });
await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);
await page.locator("text=Kara Emberfall").first().click();
await page.waitForTimeout(500);
const btn = page.locator("button", { hasText: /enter/i }).first();
if (await btn.count()) await btn.click().catch(() => {});
await page.waitForTimeout(3000);
await page.locator(".scroll .txt").click();
await page.waitForTimeout(500);

const chips = await page.locator(".sugg").count();
console.log("suggestion chips:", chips,
            await page.locator(".sugg").allTextContents());
await page.screenshot({ path: `${OUT}/1-chips.png` });

// One tap on a chip must send it as the action.
await page.locator(".sugg", { hasText: "sneak up to the mill door" }).click();
await page.waitForTimeout(2200);
console.log("chips after send:", await page.locator(".sugg").count(),
            await page.locator(".sugg").allTextContents());
await page.screenshot({ path: `${OUT}/2-after-chip.png` });

// The Chronicle.
await page.locator("button", { hasText: /Chronicle/i }).click();
await page.waitForTimeout(800);
console.log("quests:", await page.locator(".chr-quest").count(),
            "entries:", await page.locator(".chr-entry").count());
await page.screenshot({ path: `${OUT}/3-journal.png` });
await page.locator(".chr-tab", { hasText: /Bonds/i }).click();
await page.waitForTimeout(400);
console.log("bonds:", await page.locator(".chr-bond").count());
await page.screenshot({ path: `${OUT}/4-bonds.png` });

await browser.close();
