/* Screenshots of the play surface after the layout pass: status bar, the
   "here & now" rail, the narration column, and the roll card. Run against the
   offline demo:  npm run build && npx vite preview --port 4173 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE || "http://localhost:4173/";
const OUT = process.env.OUT || "./play-shots";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();

async function run(name, viewport) {
  const page = await browser.newPage({ viewport });
  page.on("pageerror", (e) => console.log("PAGE ERROR:", e.message));
  page.on("console", (m) => { if (m.type() === "error") console.log("CONSOLE:", m.text()); });
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);

  await page.locator("text=Kara Emberfall").first().click();
  await page.waitForTimeout(500);
  const btn = page.locator("button", { hasText: /enter/i }).first();
  if (await btn.count()) await btn.click().catch(() => {});
  await page.waitForTimeout(1200);
  // Only ONE block may be typing at a time: a reply now arrives as several
  // blocks (prose, dialogue, rolls), and without gating they all animate at once.
  const carets = await page.locator(".scroll .caret").count();
  console.log(name, "carets mid-reveal:", carets, carets <= 1 ? "OK" : "FAIL");
  await page.waitForTimeout(2600);
  // Skip the typewriter so the prose is fully rendered in the shot.
  await page.locator(".scroll .txt").click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/${name}-01-play.png` });

  // A skill check -> the new roll card.
  const input = page.locator(".promptbar input");
  await input.fill("I sneak up to the mill door");
  await input.press("Enter");
  await page.waitForTimeout(2200);
  await page.locator(".scroll .txt").click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/${name}-02-roll.png` });
  await page.locator(".scroll").screenshot({ path: `${OUT}/${name}-03-scroll.png` });

  console.log(name, "speech cards:", await page.locator(".speech").count());
  console.log(name,
    "statusbar:", await page.locator(".statusbar").count(),
    "locale:", await page.locator(".locale").count(),
    "rollcard:", await page.locator(".rollcard").count());
  await page.close();
}

await run("desktop", { width: 1440, height: 900 });
await run("phone", { width: 420, height: 900 });
await browser.close();
