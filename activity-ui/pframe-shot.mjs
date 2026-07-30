// The four corner ornaments must sit AT the portrait frame's corners, not be
// stretched to fill it (a `.pframe img` rule used to win over `.pc`).
import { chromium, devices } from "playwright";
const browser = await chromium.launch();
const ctx = await browser.newContext({ ...devices["Desktop Chrome"] });
const page = await ctx.newPage();
await page.goto("http://localhost:4173/", { waitUntil: "networkidle" });
await page.waitForTimeout(600);
await page.locator(".char-card").first().click();
await page.waitForSelector(".play .pframe", { timeout: 8000 });
await page.waitForTimeout(600);

const m = await page.evaluate(() => {
  const frame = document.querySelector(".play .pframe").getBoundingClientRect();
  const corners = [...document.querySelectorAll(".play .pc")].map((el) => {
    const r = el.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height) };
  });
  return { frameW: Math.round(frame.width), corners };
});
const oversized = m.corners.filter((c) => c.w > m.frameW * 0.4);
console.log(m.corners.length === 4 && oversized.length === 0 ? "PASS" : "FAIL",
  `- 4 corners at ~34px inside a ${m.frameW}px frame:`, JSON.stringify(m.corners));
const box = await page.locator(".play .pwrap").boundingBox();
await page.screenshot({ path: "./ui-shots/pframe.png", clip: {
  x: box.x - 30, y: box.y - 30, width: box.width + 60, height: box.height + 60 } });
await browser.close();
process.exit(m.corners.length === 4 && oversized.length === 0 ? 0 : 1);
