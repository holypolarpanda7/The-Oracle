/* The scene pane renders only when there IS a picture (an empty frame used to
 * eat ~40% of the surface). That made it invisible to every other harness, and
 * a `margin: 0 auto` collapsed it to 4x2px unnoticed for a whole session.
 * This asserts it fills the column and the picture is actually painted.
 *
 *   npm run build && npx vite preview --port 4173 && npx node scene-check.mjs
 */
import { chromium } from "playwright";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto("http://localhost:4173/", { waitUntil: "networkidle" });
await p.waitForTimeout(1200);
await p.locator("text=Kara Emberfall").first().click();
await p.waitForTimeout(500);
const e = p.locator("button", { hasText: /enter/i }).first();
if (await e.count()) await e.click().catch(()=>{});
await p.waitForTimeout(2800);
const m = await p.evaluate(() => {
  const s = document.querySelector(".scene");
  const img = document.querySelector(".scene .in img");
  const st = document.querySelector(".stage");
  const r = s && s.getBoundingClientRect();
  return {
    stageWidth: st ? Math.round(st.getBoundingClientRect().width) : null,
    sceneW: r ? Math.round(r.width) : null,
    sceneH: r ? Math.round(r.height) : null,
    imgW: img ? Math.round(img.getBoundingClientRect().width) : null,
    imgH: img ? Math.round(img.getBoundingClientRect().height) : null,
  };
});
console.log(m);
const fails = [];
if (!m.sceneW || m.sceneW < m.stageWidth - 40) {
  fails.push(`the scene pane did not fill the column (${m.sceneW} of ${m.stageWidth})`);
}
if (!m.sceneH || m.sceneH < 120) fails.push(`the scene pane is too short: ${m.sceneH}px`);
if (!m.imgW || !m.imgH) fails.push("the picture itself has no size");
console.log("\nFAILS:", fails.length ? fails : "none");
await b.close();
process.exit(fails.length ? 1 : 0);
