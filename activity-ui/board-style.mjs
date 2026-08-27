// A/B the board's stylised response. Stages whatever `demo_textures.py --board`
// last staged, then shoots the same frame under two style settings — the only
// honest way to see what a look pass CHANGED rather than what the board was
// already doing. The shadows shipped invisible once because nobody did this.
//
//   npm run build && (cd dist && python3 -m http.server 4190)
//   uv run python scripts/demo_textures.py --board street --seed 3 --size 46x34
//   npx node board-style.mjs http://localhost:4190/ street
//
// Extra arguments are `key=value` overrides applied to the SECOND shot, so a
// single dial can be swept without a rebuild:
//   npx node board-style.mjs http://localhost:4190/ street ink=0.9 saturation=1.6
import { chromium } from "playwright";
import { readFileSync, mkdirSync } from "node:fs";

const BASE = process.argv[2] || "http://localhost:4190/";
const TAG = process.argv[3] || "board";
const OUT = "./vtt-shots";
mkdirSync(OUT, { recursive: true });

const dials = {};
for (const arg of process.argv.slice(4)) {
  const [k, v] = arg.split("=");
  if (!k || v === undefined) continue;
  dials[k] = /^[-\d.]+$/.test(v) ? Number(v) : v;
}

const staged = JSON.parse(readFileSync("dist/demo-surfaces.json", "utf8"));

// ONE page, TWO shots. A fresh load fits the board afresh, so two sessions
// frame it slightly differently and every pixel then "changed" — which is how
// you talk yourself into a look pass that is not doing anything.
async function pair(dials) {
  const browser = await chromium.launch({
    args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  page.on("pageerror", (e) => console.log("PAGE ERROR:", e.message));
  page.on("console", (m) => {
    const t = m.text();
    if (/Shader Error|not valid|Feedback loop/.test(t)) console.log("GL:", t.slice(0, 200));
  });
  await page.addInitScript((x) => { globalThis.__ORACLE_DEMO_SURFACES = x; }, staged);
  await page.addInitScript(() => { globalThis.__ORACLE_BOARD_STYLE = {}; });
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1400);
  await page.locator(".char-card").first().click();
  await page.waitForSelector(".play", { timeout: 15000 });
  await page.waitForTimeout(700);
  await page.fill(".promptbar input", "I shoot the goblin");
  await page.locator(".promptbar input").press("Enter");
  await page.waitForSelector(".vtt-board", { timeout: 15000 });
  await page.waitForTimeout(3500);
  const box = await page.locator(".vtt-board").boundingBox();

  const set = async (s) => {
    const ok = await page.evaluate((v) => {
      const b = globalThis.__ORACLE_BOARD;
      if (!b) return false;
      b.apply(v);
      return true;
    }, s);
    if (!ok) throw new Error("the board published no tuning handle — is the "
                             + "ink pipeline null on this context?");
    await page.waitForTimeout(700);
  };

  await set({ enabled: false });
  const a = await page.screenshot({ clip: box, path: `${OUT}/${TAG}-plain.png` });
  await set({ enabled: true, ...dials });
  const b = await page.screenshot({ clip: box, path: `${OUT}/${TAG}-styled.png` });
  await browser.close();
  return [a, b];
}

const [plain, styled] = await pair(dials);
console.log(`shot ${TAG}-plain.png and ${TAG}-styled.png`);
if (Object.keys(dials).length) console.log("dials:", JSON.stringify(dials));

// A difference the eye can be told about. Two numbers, because they answer
// different questions: how much of the frame the pass touched at all, and how
// much darker its darkest work got — an ink pass that changes everything by a
// little is a grade, and one that changes a few percent by a lot is a line.
const { createCanvas, loadImage } = await import("canvas").catch(() => ({}));
if (!createCanvas) {
  console.log("(install `canvas` for the difference numbers; the shots are the point)");
} else {
  const [a, b] = [await loadImage(plain), await loadImage(styled)];
  const c = createCanvas(a.width, a.height), cx = c.getContext("2d");
  cx.drawImage(a, 0, 0); const pa = cx.getImageData(0, 0, a.width, a.height).data;
  cx.drawImage(b, 0, 0); const pb = cx.getImageData(0, 0, a.width, a.height).data;
  let touched = 0, inked = 0, n = 0;
  for (let i = 0; i < pa.length; i += 4) {
    if (pa[i + 3] < 8 && pb[i + 3] < 8) continue;
    n += 1;
    const la = (pa[i] + pa[i + 1] + pa[i + 2]) / 3;
    const lb = (pb[i] + pb[i + 1] + pb[i + 2]) / 3;
    if (Math.abs(la - lb) > 6) touched += 1;
    if (lb < la * 0.6) inked += 1;
  }
  console.log(`changed:  ${(100 * touched / n).toFixed(1)}% of the board`);
  console.log(`darkened: ${(100 * inked / n).toFixed(1)}% by 40%+ — the lines`);
}
