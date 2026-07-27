// E2E: a prepared caster re-prepares spells from the sheet. Desktop viewport so
// the character sheet (with the Prepare Spells button) is visible beside play.
import { chromium } from "playwright";
const BASE = "http://localhost:4173/";
const OUT = "./mobile-shots";
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1300, height: 950 } });
const page = await ctx.newPage();
const results = [];
const check = (n, ok, d = "") => results.push({ n, ok, d });

await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(700);
await page.locator(".char-card").first().click();
await page.waitForSelector(".play", { timeout: 8000 });
await page.waitForSelector(".sheet-prep", { timeout: 8000 });
check("Prepare Spells button shows for a prepared caster", true);

await page.locator(".sheet-prep").click();
await page.waitForSelector(".levelup", { timeout: 5000 });
await page.waitForTimeout(200);
check("re-prepare overlay opens", (await page.getByText("Prepare Spells").count()) > 0);
check("wizard prepares from the spellbook", (await page.getByText(/from your spellbook/).count()) > 0);

const prepared = () => page.locator(".lu-option.picked");
check("pre-seeds the 4 currently-prepared spells", (await prepared().count()) === 4);
const prepBtn = page.getByRole("button", { name: "Prepare", exact: true });
check("Prepare enabled at the right count", !(await prepBtn.isDisabled()));
await page.screenshot({ path: `${OUT}/17-reprepare.png`, fullPage: true });

// unpick one -> under count -> disabled; re-pick a different one -> enabled
await prepared().first().click();
await page.waitForTimeout(120);
check("Prepare disabled when under the count", await prepBtn.isDisabled());
await page.locator(".lu-option:not(.picked)").first().click();
await page.waitForTimeout(120);
check("Prepare re-enabled at the count", !(await prepBtn.isDisabled()));

await prepBtn.click();
await page.waitForTimeout(400);
check("overlay closes after preparing", (await page.locator(".levelup").count()) === 0);

await browser.close();
console.log("\n=== re-prepare-on-rest E2E ===");
let f = 0;
for (const r of results) { console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.n}${r.d ? " — " + r.d : ""}`); if (!r.ok) f++; }
console.log(`\n${results.length - f}/${results.length} passed`);
process.exit(f ? 1 : 0);
