// End-to-end check of the reported bug: complete character creation, reach the
// portrait step, hit "Enter the world", and confirm it transitions to play.
// Runs against the offline demo feed (no backend) with /cc/options stubbed.
import { chromium, devices } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE || "http://localhost:4173/";
const OUT = "./mobile-shots";
mkdirSync(OUT, { recursive: true });

const CC_OPTIONS = {
  races: [
    { slug: "dwarf", name: "Dwarf", ability_bonuses: {}, choose_bonus: [],
      speed: 30, size: "Medium", darkvision: true, traits: ["Dwarven Resilience"] },
    { slug: "human", name: "Human", ability_bonuses: {}, choose_bonus: [1, 1, 1],
      speed: 30, size: "Medium", darkvision: false, traits: ["Versatile"], feat_choice: "origin" },
  ],
  classes: [
    { slug: "fighter", name: "Fighter", hit_die: 10, saving_throws: ["STR", "CON"],
      skill_choices_n: 2, skill_options: ["Athletics", "Acrobatics", "Intimidation", "Perception"] },
  ],
  feats: [
    { slug: "alert", name: "Alert", category: "origin", brief: "+ initiative." },
    { slug: "tough", name: "Tough", category: "origin", brief: "+2 HP per level." },
  ],
  backgrounds: [
    { slug: "soldier", name: "Soldier", skills: ["Athletics", "Intimidation"],
      abilities: ["STR", "CON", "CHA"], origin_feat: "alert" },
  ],
  ability_methods: {
    standard_array: [15, 14, 13, 12, 10, 8],
    point_buy: { budget: 27, min: 8, max: 15,
      costs: { "8": 0, "9": 1, "10": 2, "11": 3, "12": 4, "13": 5, "14": 7, "15": 9 } },
    roll: { expr: "4d6kh3", count: 6 },
  },
  common_items: [{ slug: "spellbook", name: "Spellbook", item_type: "Wondrous", attunement: false, brief: "Holds spells." }],
  buyable_items: [{ slug: "torch", name: "Torch", category: "gear", cost_gp: 1 }],
  starting_gold: { by_class: { fighter: 150 }, default: 100 },
};

const browser = await chromium.launch();
const context = await browser.newContext({ ...devices["iPhone 13"] });
await context.route("**/cc/options", (r) =>
  r.fulfill({ contentType: "application/json", body: JSON.stringify(CC_OPTIONS) }));
await context.route("**/cc/roll_abilities", (r) =>
  r.fulfill({ contentType: "application/json", body: JSON.stringify({ rolls: [15, 14, 13, 12, 10, 8] }) }));
const page = await context.newPage();

const results = [];
const check = (name, pass, detail = "") => results.push({ name, pass, detail });
const onward = async () => {
  const b = page.locator(".cf-foot button:not(:disabled)").first();
  await b.click();
  await page.waitForTimeout(250);
};

await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(600);

// landing → create
await page.click(".landing-create");
await page.waitForSelector(".cf-grid .cf-card", { timeout: 5000 });

// race → class → background
await page.getByText("Dwarf", { exact: false }).first().click();
await onward();
await page.getByText("Fighter", { exact: false }).first().click();
await onward();
await page.getByText("Soldier", { exact: false }).first().click();
await onward();

// abilities: point-buy (all-8s is valid) + a +2 and +1 boost
await page.getByRole("button", { name: "Point Buy" }).click();
await page.waitForTimeout(150);
await page.locator(".cf-bonus-row").nth(0).locator(".cf-chip").first().click();     // +2
await page.locator(".cf-bonus-row").nth(1).locator(".cf-chip:not([disabled])").first().click(); // +1
await page.waitForTimeout(150);
await onward();

// skills: pick the two non-granted class skills (two distinct enabled chips) + a feat
await page.locator(".cf-chips .cf-chip.big:not([disabled])").nth(0).click();
await page.waitForTimeout(100);
await page.locator(".cf-chips .cf-chip.big:not([disabled])").nth(1).click();
await page.waitForTimeout(100);
await page.locator(".cf-card:not(.locked)").first().click(); // background origin feat
await page.waitForTimeout(200);
const skillsNext = await page.locator(".cf-foot button:not(:disabled)").count();
check("skills stage satisfied (Onward enabled)", skillsNext > 0);
await onward();

// gear (kit default) → wondrous (optional)
await onward();
await onward();

// review: name + seal
await page.fill(".cf-name", "Testwyn Ironhold");
await page.waitForTimeout(150);
const reachedReview = await page.locator(".cf-review").count();
check("reached the review stage", reachedReview > 0);
await onward(); // Seal the character → cc_register → cc_done → portrait

// portrait step
await page.waitForSelector(".portrait-step", { timeout: 5000 });
check("portrait step appears after sealing", true);
await page.screenshot({ path: `${OUT}/06-portrait.png`, fullPage: true });

// THE BUG: click "Enter the world" and confirm we transition to play
const enterBtn = page.getByRole("button", { name: /Enter the world/ });
check("'Enter the world' button is present", (await enterBtn.count()) > 0);
await enterBtn.click();
let reachedPlay = false;
try {
  await page.waitForSelector(".play", { timeout: 8000 });
  reachedPlay = true;
} catch { /* stayed on portrait */ }
check("clicking 'Enter the world' transitions to play", reachedPlay);
await page.waitForTimeout(1500);
await page.screenshot({ path: `${OUT}/07-entered-play.png`, fullPage: true });

await browser.close();

console.log("\n=== enter-flow E2E (iPhone 13) ===");
let failed = 0;
for (const r of results) {
  console.log(`${r.pass ? "PASS" : "FAIL"}  ${r.name}${r.detail ? "  — " + r.detail : ""}`);
  if (!r.pass) failed++;
}
console.log(`\n${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
