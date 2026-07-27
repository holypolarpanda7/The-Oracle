// E2E: level-up overlay gates on BOTH the subclass pick and the new-spell pick,
// then unlocks. Drives the offline demo (Ranger 2→3: subclass + 1 new spell).
import { chromium, devices } from "playwright";
const BASE = "http://localhost:4173/";
const OUT = "./mobile-shots";
const browser = await chromium.launch();
const ctx = await browser.newContext({ ...devices["iPhone 13"] });
const page = await ctx.newPage();
const results = [];
const check = (n, ok, d = "") => results.push({ n, ok, d });

await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(700);
await page.locator(".char-card").first().click();
await page.waitForSelector(".play", { timeout: 8000 });
await page.waitForTimeout(500);
await page.fill(".promptbar input", "level up");
await page.locator(".promptbar input").press("Enter");
await page.waitForSelector(".levelup", { timeout: 8000 });
await page.waitForTimeout(300);

const confirm = page.locator(".lu-confirm");
check("overlay shows a new-spell picker", (await page.getByText(/New .*spells \(choose 1\)/).count()) > 0);
check("confirm disabled at first", await confirm.isDisabled());

// pick a subclass (first .lu-options group), confirm still gated on the spell
await page.locator(".lu-options").nth(0).locator(".lu-option").first().click();
await page.waitForTimeout(150);
check("confirm still disabled after only subclass", await confirm.isDisabled());
await page.screenshot({ path: `${OUT}/14-levelup-spell-gate.png`, fullPage: true });

// pick the 1 required spell (second .lu-options group)
await page.locator(".lu-options").nth(1).locator(".lu-option").first().click();
await page.waitForTimeout(200);
check("confirm unlocks after subclass + spell chosen", !(await confirm.isDisabled()));
await page.screenshot({ path: `${OUT}/15-levelup-ready.png`, fullPage: true });

// optional swap: choosing a spell to drop but no replacement re-gates confirm
check("swap section present for a known caster", (await page.getByText(/Replace a known spell/).count()) > 0);
await page.locator(".lu-option").filter({ hasText: "drop this" }).first().click();
await page.waitForTimeout(150);
check("confirm re-disabled with an incomplete swap", await confirm.isDisabled());
// pick a replacement (the newly-appeared last .lu-options group)
const groups = await page.locator(".lu-options").count();
await page.locator(".lu-options").nth(groups - 1).locator(".lu-option").first().click();
await page.waitForTimeout(150);
check("confirm re-enabled once swap completed", !(await confirm.isDisabled()));
await page.screenshot({ path: `${OUT}/16-levelup-swap.png`, fullPage: true });

// applying clears the overlay (demo)
await confirm.click();
await page.waitForTimeout(600);
check("overlay closes on Take the level", (await page.locator(".levelup").count()) === 0);

await browser.close();
console.log("\n=== level-up spell-pick E2E ===");
let f = 0;
for (const r of results) { console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.n}${r.d ? " — " + r.d : ""}`); if (!r.ok) f++; }
console.log(`\n${results.length - f}/${results.length} passed`);
process.exit(f ? 1 : 0);
