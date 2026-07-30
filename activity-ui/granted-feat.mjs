// E2E: a background whose granted Origin feat lives OUTSIDE the origin
// category (Rune Carver -> Rune Shaper, a 'giant' feat) still offers it, and
// both of that feat's choices are answerable.
import { chromium, devices } from "playwright";
const BASE = "http://localhost:4173/";
const OUT = "./ui-shots";
const CC = {
  races: [{ slug: "dwarf", name: "Dwarf", ability_bonuses: {}, choose_bonus: [],
    speed: 30, size: "Medium", darkvision: true, traits: ["Resilience"] }],
  classes: [{ slug: "fighter", name: "Fighter", hit_die: 10, saving_throws: ["STR","CON"],
    skill_choices_n: 2, skill_options: ["Athletics","Acrobatics","Perception","Survival"] }],
  feats: [
    { slug: "rune-shaper", name: "Rune Shaper", category: "giant",
      prerequisite: "a Spellcasting or Pact Magic feature, or the Rune Carver background",
      brief: "Comprehend languages, plus runes you inscribe after a long rest.",
      choices: { kind: "options", n: 1,
                 from: ["Cloud", "Death", "Dragon", "Fire", "Frost", "Storm"],
                 hint: "Choose a rune you know.",
                 also: { kind: "ability", n: 1, from: ["int", "wis", "cha"],
                         amount: 0, hint: "Choose the spellcasting ability." } } },
    { slug: "alert", name: "Alert", category: "origin", brief: "+initiative.", choices: null },
  ],
  backgrounds: [{ slug: "rune-carver", name: "Rune Carver", skills: ["History"],
    abilities: ["INT","WIS","CHA"], origin_feat: "rune-shaper" }],
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
await page.getByText("Rune Carver", { exact: true }).first().click(); await onward();
await page.getByRole("button", { name: "Point Buy" }).click(); await page.waitForTimeout(150);
await page.locator(".cf-bonus-row").nth(0).locator(".cf-chip").first().click();
await page.locator(".cf-bonus-row").nth(1).locator(".cf-chip:not([disabled])").first().click();
await onward();

// skills: 2 class skills. Rune Shaper is granted by the background even though
// it is a "giant" feat, not an origin one — the pool it isn't in never mattered.
await page.locator(".cf-chips .cf-chip.big:not([disabled])").nth(0).click(); await page.waitForTimeout(80);
await page.locator(".cf-chips .cf-chip.big:not([disabled])").nth(1).click(); await page.waitForTimeout(80);
check("a non-origin granted feat is still offered",
  (await page.getByText(/grants the Rune Shaper feat/i).count()) > 0);
check("both of its choices render",
  (await page.getByText(/Choose a rune you know/i).count()) > 0
  && (await page.getByText(/Choose the spellcasting ability/i).count()) > 0);
check("Onward gated before both are answered",
  (await page.locator(".cf-foot button:not(:disabled)").count()) === 0);

await page.getByRole("button", { name: "Fire", exact: true }).click(); await page.waitForTimeout(120);
check("still gated with only the rune chosen",
  (await page.locator(".cf-foot button:not(:disabled)").count()) === 0);
await page.getByRole("button", { name: "WIS", exact: true }).click(); await page.waitForTimeout(150);
check("Onward unlocks once both are answered",
  (await page.locator(".cf-foot button:not(:disabled)").count()) > 0);
await page.screenshot({ path: `${OUT}/rune-carver.png`, fullPage: true });

await browser.close();
console.log("\n=== granted-feat (Rune Carver) E2E ===");
let f = 0;
for (const r of results) { console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.n}${r.d ? " — " + r.d : ""}`); if (!r.ok) f++; }
console.log(`\n${results.length - f}/${results.length} passed`);
process.exit(f ? 1 : 0);
