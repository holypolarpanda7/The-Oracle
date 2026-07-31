// Walk the Proving Grounds against the offline demo feed and shoot each step:
// landing → slots → environments → the Quartermaster → the bout → the result.
//
//   npm run build && npx vite preview --port 4173 &
//   node arena-shot.mjs
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE || "http://localhost:4173/";
const OUT = "./arena-shots";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 820 } });
const shot = async (name) => {
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/${name}.png` });
  console.log(`  shot ${name}`);
};

await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(700);
await shot("1-landing");

await page.click(".landing-arena");
await page.waitForSelector(".arena-slots");
await shot("2-grounds");

await page.click(".arena-slot >> nth=0");
await page.click(".arena-env >> nth=3");            // The Sunlit Shelf (swim)
await page.locator(".arena-dial input").fill("6");
await page.click(".arena-diff >> nth=2");           // hard
await shot("3-chosen");

await page.click(".arena-go .lu-confirm >> nth=1"); // step through the gate
await page.waitForSelector(".levelup", { timeout: 5000 });
await shot("4-levelup");

// The demo level-up asks for a subclass AND a spell — satisfy both, then confirm.
await page.locator(".lu-option", { hasText: "Gloom Stalker" }).click();
await page.locator(".lu-option", { hasText: "Cure Wounds" }).first().click();
await page.click(".lu-confirm:not([disabled])");

// The stall stands between the climb and the sand: buy, wear, step through.
await page.waitForSelector(".quartermaster", { timeout: 5000 });
await shot("5-stall");
await page.locator(".qm-stall .gear-row", { hasText: "Chain Mail" })
  .locator(".gear-qty button").last().click();
await page.locator(".qm-stall .gear-row", { hasText: "Cloak of Protection" })
  .locator(".gear-qty button").last().click();
await page.locator(".qm-packlist .gear-row", { hasText: "Longsword" })
  .locator(".qm-flag").first().click();
await shot("6-stall-loaded");

await page.locator(".quartermaster .lu-confirm", { hasText: "Step through" }).click();
await page.waitForTimeout(900);
await shot("7-bout");

await page.locator(".promptbar input")
  .fill("I swing at the nearest one.");
await page.keyboard.press("Enter");
await page.waitForTimeout(1800);
await shot("8-result");

await browser.close();
console.log("done");
