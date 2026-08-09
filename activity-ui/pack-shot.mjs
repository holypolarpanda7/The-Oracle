/* The inventory grid. npm run build && npx vite preview --port 4173 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
const BASE = process.env.BASE || "http://localhost:4173/";
const OUT = process.env.OUT || "./pack-shots";
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();

async function run(name, viewport) {
  const page = await browser.newPage({ viewport });
  page.on("pageerror", (e) => console.log("PAGE ERROR:", e.message));
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  await page.locator("text=Kara Emberfall").first().click();
  await page.waitForTimeout(500);
  const btn = page.locator("button", { hasText: /enter/i }).first();
  if (await btn.count()) await btn.click().catch(() => {});
  await page.waitForTimeout(2800);
  await page.locator(".tab", { hasText: /Inventory/i }).click();
  await page.waitForTimeout(500);
  console.log(name, "cards:", await page.locator(".icard").count(),
              "verbs:", await page.locator(".ic-act").count(),
              "worn:", await page.locator(".ic-worn").count());
  // Worn & wielded: the hands are shown above the pack, and a held piece's
  // badge names its grip rather than saying "worn" like everything else.
  console.log(name, "loadout:", await page.locator(".ld-slot").allTextContents());
  console.log(name, "badges:", await page.locator(".ic-worn").allTextContents());
  await page.screenshot({ path: `${OUT}/${name}-1-pack.png` });
  await page.locator(".ph-find").fill("potion");
  await page.waitForTimeout(400);
  console.log(name, "filtered to:", await page.locator(".ic-name").allTextContents());
  await page.screenshot({ path: `${OUT}/${name}-2-filtered.png` });
  await page.close();
}
await run("desktop", { width: 1440, height: 900 });
await run("phone", { width: 420, height: 900 });
await browser.close();
