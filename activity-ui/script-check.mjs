/* Cultural hands, verified in a real browser.
 *
 * `getComputedStyle().fontFamily` reports the DECLARED stack, so it says
 * "OracleElven" whether or not that face ever loaded — it proves nothing. This
 * measures instead: the same string is rendered in the house serif and in each
 * cultural face, and the widths must differ. A font that failed to load falls
 * back to the serif and measures identical, which is exactly the failure this
 * needs to catch.
 *
 *   npm run build && npx vite preview --port 4173
 *   npx node script-check.mjs
 */
import { chromium } from "playwright";

const BASE = process.env.BASE || "http://localhost:4173/";
const FACES = ["celestial", "dwarven", "elven", "draconic", "infernal", "fey"];

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("pageerror", (e) => console.log("PAGE ERROR:", e.message));
await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);

// Enter play first: the faces load lazily, on first USE, so none of them are
// loaded while the landing screen is up.
await page.locator("text=Kara Emberfall").first().click();
await page.waitForTimeout(500);
const btn = page.locator("button", { hasText: /enter/i }).first();
if (await btn.count()) await btn.click().catch(() => {});
await page.waitForTimeout(3000);
await page.locator(".scroll .txt").click();
await page.waitForTimeout(800);

const result = await page.evaluate(async (faces) => {
  const fam = (f) => `Oracle${f[0].toUpperCase()}${f.slice(1)}`;
  await Promise.all(faces.map((f) => document.fonts.load(`16px "${fam(f)}"`)));
  await document.fonts.ready;

  const probe = document.createElement("span");
  probe.style.cssText =
    "position:absolute;visibility:hidden;white-space:nowrap;font-size:48px";
  probe.textContent = "Handgloves Vashra 1247";
  document.body.appendChild(probe);

  probe.className = "";
  const baseline = probe.getBoundingClientRect().width;
  const widths = {};
  const check = {};
  for (const f of faces) {
    probe.className = `script-${f}`;
    widths[f] = probe.getBoundingClientRect().width;
    check[f] = document.fonts.check(`48px "${fam(f)}"`);
  }
  probe.remove();

  const seen = {};
  const grab = (sel, label) => {
    const el = document.querySelector(sel);
    seen[label] = el
      ? getComputedStyle(el).fontFamily.split(",")[0].replace(/"/g, "") : null;
  };
  grab(".sb-name", "statusName");
  grab(".sp-who", "speakerTab");
  grab(".scroll .script-elven", "elvenInProse");
  grab(".scroll p", "bodyProse");
  return { baseline, widths, check, seen };
}, FACES);

const fails = [];
console.log(`house serif baseline: ${result.baseline.toFixed(1)}px`);
for (const f of FACES) {
  const w = result.widths[f];
  // The script classes also nudge font-size/letter-spacing, so a face that
  // fell back would still differ slightly; require a real difference AND the
  // font registry to agree the face is loaded.
  const differs = Math.abs(w - result.baseline) > 2;
  const ok = differs && result.check[f];
  console.log(`  ${f.padEnd(10)} ${w.toFixed(1).padStart(7)}px  loaded=${result.check[f]}  ${ok ? "rendered" : "FELL BACK"}`);
  if (!ok) fails.push(`${f} did not render (fell back to the house serif)`);
}

console.log("\napplied where intended:", result.seen);
if (!/^Oracle/.test(result.seen.statusName || "")) fails.push("status bar name has no hand");
if (!/^Oracle/.test(result.seen.speakerTab || "")) fails.push("speaker tab has no hand");
if (!/^Oracle/.test(result.seen.elvenInProse || "")) fails.push("elven name in prose has no hand");
// The one that matters most: display faces must never touch body text.
if (/^Oracle/.test(result.seen.bodyProse || "")) fails.push("BODY PROSE picked up a display face");

// The Chronicle's codex names must keep their hands too — a font-family on a
// container rule out-specifies the .script-* class and flattens them silently,
// which has already happened twice.
await page.locator("button", { hasText: /Chronicle/i }).click();
await page.waitForTimeout(700);
await page.locator(".chr-tab", { hasText: /Codex/i }).click();
await page.waitForTimeout(400);
const codex = await page.evaluate(() => {
  const el = document.querySelector(".chr-codex b[class*='script-']");
  return el ? getComputedStyle(el).fontFamily.split(",")[0].replace(/"/g, "") : null;
});
console.log("codex name font:", codex);
if (!/^Oracle/.test(codex || "")) fails.push("codex names lost their hand");

console.log("\nFAILS:", fails.length ? fails : "none");
await browser.close();
process.exit(fails.length ? 1 : 0);
