// Does the canopy actually open a hole, and does it close again?
//
// A tree is drawn eighteen feet tall with a crown about as wide, which is the
// one place on this board where the picture deliberately overruns the grid: a
// canopy covers squares that are open, walkable and shootable. `vttScene3d`
// bores a view-aligned shaft through the leaves toward anything standing under
// them so the board stays readable. That hole only appears when a tree happens
// to stand between the camera and a creature, which is not a thing you can
// photograph on demand — and "it looked fine" is exactly how the first shadow
// pass shipped casting nothing at all.
//
// So this widens the lens until the aperture is unmissable and takes the SAME
// frame twice, closed and open, from ONE page load. Two loads would refit the
// board and the difference would be framing.
//
//   uv run python scripts/demo_textures.py --stage --board forest --seed 3 --size 46x34
//   npm run build && (cd dist && python3 -m http.server 4190)
//   npx node canopy-lens.mjs http://localhost:4190/
import { chromium } from "playwright";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";

const BASE = process.argv[2] || "http://localhost:4190/";
const OUT = "./vtt-shots";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ args: ["--use-gl=swiftshader",
                                               "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
page.on("pageerror", (e) => console.log("PAGE ERROR:", e.message));
await page.addInitScript(() => { globalThis.__ORACLE_CANOPY_PROBE = 1; });
if (existsSync("dist/demo-surfaces.json")) {
  await page.addInitScript((x) => { globalThis.__ORACLE_DEMO_SURFACES = x; },
                           JSON.parse(readFileSync("dist/demo-surfaces.json", "utf8")));
}
await page.goto(BASE, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1500);
await page.locator(".char-card").first().click();
await page.waitForSelector(".play", { timeout: 15000 });
await page.waitForTimeout(800);
await page.fill(".promptbar input", "I shoot the goblin");
await page.locator(".promptbar input").press("Enter");
await page.waitForSelector(".vtt-board", { timeout: 15000 });
await page.waitForTimeout(2500);

const seam = await page.evaluate(() => Boolean(globalThis.__ORACLE_CANOPY));
if (!seam) {
  console.log("FAIL  no canopy seam — the board built no canopy material, so "
              + "either this board has no trees or the patch is not applied");
  await browser.close();
  process.exit(1);
}
const board = await page.locator(".vtt-board canvas").first().boundingBox();
const shoot = async (r, tag) => {
  await page.evaluate((rr) => globalThis.__ORACLE_CANOPY.setRadius(rr), r);
  await page.waitForTimeout(700);
  const buf = await page.screenshot({ clip: board });
  writeFileSync(`${OUT}/canopy-${tag}.png`, buf);
  return buf.toString("base64");
};
const shut = await shoot(0, "shut");
const open = await shoot(9, "open");

// How much of the board changed, and in which direction. A lens REMOVES leaves,
// so the open frame must be the one that lost green.
const diff = await page.evaluate(async ([a, b]) => {
  const load = async (s) => {
    const img = new Image();
    img.src = "data:image/png;base64," + s;
    await img.decode();
    const c = document.createElement("canvas");
    c.width = img.width; c.height = img.height;
    c.getContext("2d").drawImage(img, 0, 0);
    return c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
  };
  const A = await load(a), B = await load(b);
  let changed = 0, greener = 0, n = 0;
  for (let i = 0; i < A.length; i += 4) {
    n++;
    const d = Math.abs(A[i] - B[i]) + Math.abs(A[i + 1] - B[i + 1])
            + Math.abs(A[i + 2] - B[i + 2]);
    if (d > 24) {
      changed++;
      const ga = A[i + 1] - (A[i] + A[i + 2]) / 2;
      const gb = B[i + 1] - (B[i] + B[i + 2]) / 2;
      if (ga > gb) greener++;
    }
  }
  return { pct: (changed * 100) / n, lostGreen: (greener * 100) / Math.max(changed, 1) };
}, [shut, open]);

console.log(`lens opened: ${diff.pct.toFixed(2)}% of the board changed; `
            + `${diff.lostGreen.toFixed(0)}% of that lost green`);
if (diff.pct < 0.05) {
  console.log("FAIL  widening the lens changed nothing — the shaft is not "
              + "being cut (check uLensN, and that a token is on this level)");
  process.exitCode = 1;
} else if (diff.lostGreen < 50) {
  console.log("FAIL  the board changed but did not lose leaves — that is not "
              + "a hole through a canopy");
  process.exitCode = 1;
} else {
  console.log("PASS  the canopy opens along the view ray and closes again");
}
await browser.close();
