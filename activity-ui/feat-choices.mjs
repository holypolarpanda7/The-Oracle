// E2E: a tool-proficiency feat (Crafter) reveals its 3-artisan-tool picker in
// the Skills stage and gates Onward until three are chosen.
import { chromium, devices } from "playwright";
const BASE = "http://localhost:4173/";
const OUT = "./mobile-shots";
const CC = {
  races: [{ slug: "dwarf", name: "Dwarf", ability_bonuses: {}, choose_bonus: [],
    speed: 30, size: "Medium", darkvision: true, traits: ["Resilience"] }],
  classes: [{ slug: "fighter", name: "Fighter", hit_die: 10, saving_throws: ["STR","CON"],
    skill_choices_n: 2, skill_options: ["Athletics","Acrobatics","Perception","Survival"] }],
  feats: [
    { slug: "crafter", name: "Crafter", category: "origin", brief: "Tools + discount.",
      choices: { kind: "tools", n: 3, from: "artisan", hint: "Choose 3 artisan's tools." } },
    { slug: "alert", name: "Alert", category: "origin", brief: "+initiative.", choices: null },
  ],
  backgrounds: [{ slug: "guild-artisan", name: "Guild Artisan", skills: ["Insight"],
    abilities: ["STR","DEX","CON"], origin_feat: "crafter" }],
  ability_methods: { standard_array: [15,14,13,12,10,8],
    point_buy: { budget: 27, min: 8, max: 15, costs: { "8": 0 } }, roll: { expr: "4d6kh3", count: 6 } },
  common_items: [], buyable_items: [], starting_gold: { by_class: { fighter: 150 }, default: 100 },
};
const browser = await chromium.launch();
const ctx = await browser.newContext({ ...devices["iPhone 13"] });
await ctx.route("**/cc/options", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify(CC) }));
const page = await ctx.newPage();
const results = []; const check = (n, ok, d = "") => results.push({ n, ok, d });
const onward = async () => { await page.locator(".cf-foot button:not(:disabled)").first().click(); await page.waitForTimeout(250); };

await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(600);
await page.click(".landing-create");
await page.waitForSelector(".cf-grid .cf-card");
await page.getByText("Dwarf", { exact: true }).first().click(); await onward();
await page.getByText("Fighter", { exact: true }).first().click(); await onward();
await page.getByText("Guild Artisan", { exact: true }).first().click(); await onward();
await page.getByRole("button", { name: "Point Buy" }).click(); await page.waitForTimeout(150);
await page.locator(".cf-bonus-row").nth(0).locator(".cf-chip").first().click();
await page.locator(".cf-bonus-row").nth(1).locator(".cf-chip:not([disabled])").first().click();
await onward();

// skills: 2 class skills, then pick Crafter -> tool picker appears
await page.locator(".cf-chips .cf-chip.big:not([disabled])").nth(0).click(); await page.waitForTimeout(80);
await page.locator(".cf-chips .cf-chip.big:not([disabled])").nth(1).click(); await page.waitForTimeout(80);
await page.getByText("Crafter", { exact: false }).first().click(); await page.waitForTimeout(200);
check("Crafter reveals its artisan-tool picker", (await page.getByText(/artisan.?s tools/i).count()) > 0);
check("Onward gated before tools chosen", (await page.locator(".cf-foot button:not(:disabled)").count()) === 0);

// the tool chips are the small (non-.big) chips under the feat
for (let i = 0; i < 3; i++) {
  await page.locator(".cf-chips .cf-chip:not(.big):not([disabled])").nth(i).click();
  await page.waitForTimeout(80);
}
check("Onward unlocks after 3 tools chosen", (await page.locator(".cf-foot button:not(:disabled)").count()) > 0);
await page.screenshot({ path: `${OUT}/18-feat-tools.png`, fullPage: true });

await browser.close();
console.log("\n=== feat tool-choice E2E ===");
let f = 0;
for (const r of results) { console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.n}${r.d ? " — " + r.d : ""}`); if (!r.ok) f++; }
console.log(`\n${results.length - f}/${results.length} passed`);
process.exit(f ? 1 : 0);
