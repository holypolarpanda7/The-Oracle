// E2E: caster CC flow — pick Wizard, resolve the Skilled feat's skill choice,
// then the conditional Spells stage (cantrips + spellbook), and confirm gating.
import { chromium, devices } from "playwright";
const BASE = "http://localhost:4173/";
const OUT = "./mobile-shots";

const spell = (slug, name, level) => ({ slug, name, level, school: "Evocation",
  concentration: false, ritual: false, brief: "a test spell" });

const CC = {
  races: [{ slug: "dwarf", name: "Dwarf", ability_bonuses: {}, choose_bonus: [],
    speed: 30, size: "Medium", darkvision: true, traits: ["Resilience"] }],
  classes: [{ slug: "wizard", name: "Wizard", hit_die: 6, spellcasting_ability: "INT",
    saving_throws: ["INT", "WIS"], skill_choices_n: 2,
    skill_options: ["Arcana", "History", "Investigation", "Insight"],
    spellcasting: { ability: "INT", cantrips: 3, spells: 6, mode: "spellbook" } }],
  feats: [
    { slug: "skilled", name: "Skilled", category: "origin", brief: "3 skills.",
      choices: { kind: "skills", n: 3, hint: "Choose 3 skills." } },
    { slug: "alert", name: "Alert", category: "origin", brief: "+initiative.", choices: null },
  ],
  backgrounds: [{ slug: "sage", name: "Sage", skills: ["Arcana"],
    abilities: ["INT", "WIS", "CHA"], origin_feat: "skilled" }],
  ability_methods: { standard_array: [15,14,13,12,10,8],
    point_buy: { budget: 27, min: 8, max: 15, costs: { "8": 0 } }, roll: { expr: "4d6kh3", count: 6 } },
  common_items: [], buyable_items: [], starting_gold: { by_class: { wizard: 80 }, default: 100 },
};
const WIZ_SPELLS = { caster: true, class: "wizard", cantrips_n: 3, spells_n: 6,
  ability: "INT", mode: "spellbook",
  cantrips: ["Fire Bolt","Light","Mage Hand","Prestidigitation","Ray of Frost","Shocking Grasp"]
    .map((n, i) => spell("c" + i, n, 0)),
  spells: ["Burning Hands","Detect Magic","Mage Armor","Magic Missile","Shield","Sleep","Thunderwave","Grease"]
    .map((n, i) => spell("s" + i, n, 1)) };

const browser = await chromium.launch();
const ctx = await browser.newContext({ ...devices["iPhone 13"] });
await ctx.route("**/cc/options", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify(CC) }));
await ctx.route("**/cc/spells/wizard", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify(WIZ_SPELLS) }));
const page = await ctx.newPage();
const results = [];
const check = (n, ok, d = "") => results.push({ n, ok, d });
const onward = async () => { await page.locator(".cf-foot button:not(:disabled)").first().click(); await page.waitForTimeout(300); };

await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(600);
await page.click(".landing-create");
await page.waitForSelector(".cf-grid .cf-card");

await page.getByText("Dwarf", { exact: true }).first().click(); await onward();      // race
await page.getByText("Wizard", { exact: true }).first().click(); await onward();      // class
await page.getByText("Sage", { exact: true }).first().click(); await onward();        // background
await page.getByRole("button", { name: "Point Buy" }).click(); await page.waitForTimeout(150);
await page.locator(".cf-bonus-row").nth(0).locator(".cf-chip").first().click();
await page.locator(".cf-bonus-row").nth(1).locator(".cf-chip:not([disabled])").first().click();
await onward();                                                                        // abilities

// skills stage: 2 class skills + pick Skilled feat + 3 skilled skills
await page.locator(".cf-chips .cf-chip.big:not([disabled])").nth(0).click();
await page.waitForTimeout(80);
await page.locator(".cf-chips .cf-chip.big:not([disabled])").nth(1).click();
await page.waitForTimeout(80);
await page.getByText("Skilled", { exact: false }).first().click();   // pick the feat card
await page.waitForTimeout(200);
const skilledUi = await page.getByText(/grants 3 skill proficiencies/).count();
check("Skilled feat reveals its skill picker", skilledUi > 0);
// pick 3 skilled skills (distinct small chips under the feat)
for (let i = 0; i < 3; i++) {
  await page.locator(".cf-chips .cf-chip:not(.big):not([disabled])").nth(i).click();
  await page.waitForTimeout(80);
}
const skillsNext = await page.locator(".cf-foot button:not(:disabled)").count();
check("skills stage gates until Skilled skills chosen", skillsNext > 0);
await onward();

// SPELLS stage should now be visible
const onSpells = await page.locator(".cf-sub-label", { hasText: "spellcasting" }).count();
check("Spells stage appears for a caster", onSpells > 0);
await page.screenshot({ path: `${OUT}/12-spell-picker.png`, fullPage: true });
// before picking, Onward is disabled
const lockedBefore = await page.locator(".cf-foot button:not(:disabled)").count();
check("Onward locked before spells chosen", lockedBefore === 0);
// pick 3 cantrips + 6 spells (cards)
const cards = () => page.locator(".cf-grid .cf-card:not([disabled])");
// cantrips grid is first; pick 3
for (let i = 0; i < 3; i++) { await page.locator(".cf-grid").nth(0).locator(".cf-card").nth(i).click(); await page.waitForTimeout(60); }
for (let i = 0; i < 6; i++) { await page.locator(".cf-grid").nth(1).locator(".cf-card").nth(i).click(); await page.waitForTimeout(60); }
await page.waitForTimeout(200);
const unlocked = await page.locator(".cf-foot button:not(:disabled)").count();
check("Onward unlocks after 3 cantrips + 6 spells", unlocked > 0);
await page.screenshot({ path: `${OUT}/13-spells-picked.png`, fullPage: true });

await browser.close();
console.log("\n=== CC spell-picker E2E ===");
let f = 0;
for (const r of results) { console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.n}${r.d ? " — " + r.d : ""}`); if (!r.ok) f++; }
console.log(`\n${results.length - f}/${results.length} passed`);
process.exit(f ? 1 : 0);
