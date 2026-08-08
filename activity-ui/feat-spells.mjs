// E2E: the two feat slots at creation are INDEPENDENT.
//  - a background's granted feat can't be re-picked as the Custom Lineage one
//  - each feat's own questions are answered separately (two abilities, two
//    skill picks) instead of sharing one flat bucket
//  - a school-scoped spell pick (Fey Touched) appears on the Spells stage,
//    gates Onward, and shows what the feat grants outright
import { chromium, devices } from "playwright";
const BASE = "http://localhost:4173/";
const OUT = "./ui-shots";

const plusOne = (from, hint) => ({ kind: "ability", n: 1, from, amount: 1, max: 20, hint });
const CC = {
  races: [{ slug: "human", name: "Human", ability_bonuses: {}, choose_bonus: [],
    speed: 30, size: "Medium", darkvision: false, traits: ["Resourceful"],
    feat_choice: "any" }],
  classes: [{ slug: "fighter", name: "Fighter", hit_die: 10, saving_throws: ["STR","CON"],
    skill_choices_n: 2, skill_options: ["Athletics","Acrobatics","Perception","Survival"] }],
  feats: [
    { slug: "strike-of-the-giants", name: "Strike of the Giants", category: "giant",
      brief: "A giant's blow rides your weapon.", repeatable: false,
      choices: { kind: "options", n: 1, tag: "giant-strike",
                 from: ["Fire Strike", "Frost Strike", "Storm Strike"],
                 hint: "Choose your giant strike." } },
    // Filed as an ORIGIN feat here on purpose: the real Fey-Touched is level
    // 4+, so at creation only the level-up overlay ever offers it. The schema
    // is what's under test, and creation has to handle one if a book ships it.
    { slug: "fey-touched", name: "Fey-Touched", category: "origin", min_level: 1,
      brief: "Feywild magic.", repeatable: false,
      choices: { ...plusOne(["int","wis","cha"], "+1 to Intelligence, Wisdom, or Charisma."),
                 also: { kind: "spells", n: 1, level: 1,
                         schools: ["Divination","Enchantment"],
                         granted: ["misty-step"],
                         hint: "Fey Magic — choose a level 1 Divination or Enchantment spell." } } },
    { slug: "alert", name: "Alert", category: "origin", brief: "+initiative.", choices: null },
  ],
  backgrounds: [{ slug: "giant-foundling", name: "Giant Foundling", skills: ["Survival"],
    abilities: ["STR","CON","WIS"], origin_feat: "strike-of-the-giants" }],
  ability_methods: { standard_array: [15,14,13,12,10,8],
    point_buy: { budget: 27, min: 8, max: 15, costs: { "8": 0 } }, roll: { expr: "4d6kh3", count: 6 } },
  common_items: [], buyable_items: [], starting_gold: { by_class: { fighter: 150 }, default: 100 },
};
const FEY_SPELLS = {
  feat: "fey-touched", n: 1, level: 1, schools: ["Divination", "Enchantment"],
  hint: "Fey Magic — choose a level 1 Divination or Enchantment spell.",
  spells: [
    { slug: "bless", name: "Bless", level: 1, school: "Enchantment" },
    { slug: "charm-person", name: "Charm Person", level: 1, school: "Enchantment" },
    { slug: "detect-evil-and-good", name: "Detect Evil and Good", level: 1, school: "Divination" },
  ],
  granted: [{ slug: "misty-step", name: "Misty Step", level: 2, school: "Conjuration" }],
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ ...devices["iPhone 13"] });
await ctx.route("**/cc/options", (r) =>
  r.fulfill({ contentType: "application/json", body: JSON.stringify(CC) }));
// Playwright matches the LAST registered route first, so the catch-all
// (every other feat asks for no spells) has to go in before the specific one.
await ctx.route("**/cc/feat_spells/**", (r) => r.fulfill({
  contentType: "application/json",
  body: JSON.stringify({ feat: "x", n: 0, spells: [], granted: [] }) }));
await ctx.route("**/cc/feat_spells/fey-touched", (r) =>
  r.fulfill({ contentType: "application/json", body: JSON.stringify(FEY_SPELLS) }));
const page = await ctx.newPage();
const results = []; const check = (n, ok, d = "") => results.push({ n, ok, d });
const onward = async () => {
  await page.locator(".cf-foot button:not(:disabled)").first().click();
  await page.waitForTimeout(250);
};

await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(600);
await page.click(".landing-create");
await page.waitForSelector(".cf-grid .cf-card");
await page.getByText("Human", { exact: true }).first().click(); await onward();
await page.getByText("Fighter", { exact: true }).first().click(); await onward();
await page.getByText("Giant Foundling", { exact: true }).first().click(); await onward();
await page.getByRole("button", { name: "Point Buy" }).click(); await page.waitForTimeout(150);
await page.locator(".cf-bonus-row").nth(0).locator(".cf-chip").first().click();
await page.locator(".cf-bonus-row").nth(1).locator(".cf-chip:not([disabled])").first().click();
await onward();

// --- Skills stage: 2 class skills, then the two feat slots. -----------------
await page.locator(".cf-chips .cf-chip.big:not([disabled])").nth(0).click();
await page.waitForTimeout(80);
await page.locator(".cf-chips .cf-chip.big:not([disabled])").nth(1).click();
await page.waitForTimeout(120);

check("the background grants Strike of the Giants",
  (await page.getByText(/grants the Strike of the Giants feat/i).count()) > 0);
// The species pick is a free "any" pool — and must not offer the feat the
// background already spent.
const lineagePool = page.locator(".cf-feat-card, .cf-card").filter(
  { hasText: "Strike of the Giants" });
check("the granted feat is not offered again for the species slot",
  (await page.getByRole("button", { name: /Strike of the Giants/i }).count()) === 0,
  `found ${await lineagePool.count()} card(s)`);

await page.getByText("Fey-Touched", { exact: true }).first().click();
await page.waitForTimeout(200);
check("both feats' questions are on screen at once",
  (await page.getByText(/Choose your giant strike/i).count()) > 0
  && (await page.getByText(/\+1 to Intelligence, Wisdom, or Charisma/i).count()) > 0);
check("Onward gated before either is answered",
  (await page.locator(".cf-foot button:not(:disabled)").count()) === 0);

await page.getByRole("button", { name: "Frost Strike", exact: true }).click();
await page.waitForTimeout(120);
check("still gated with only the giant strike chosen",
  (await page.locator(".cf-foot button:not(:disabled)").count()) === 0);
await page.getByRole("button", { name: "WIS", exact: true }).click();
await page.waitForTimeout(150);
check("Onward unlocks once BOTH feats are answered",
  (await page.locator(".cf-foot button:not(:disabled)").count()) > 0);
await page.screenshot({ path: `${OUT}/feat-two-slots.png`, fullPage: true });
await onward();

// --- Spells stage: a non-caster reaches it only because a feat asked. -------
check("the Spells stage opens for a non-caster with Fey-Touched",
  (await page.getByText(/Fey Magic/i).count()) > 0);
check("the spell pool is school-scoped",
  (await page.getByText("Charm Person", { exact: true }).count()) > 0
  && (await page.getByText("Detect Evil and Good", { exact: true }).count()) > 0);
check("what the feat grants outright is shown, not offered",
  (await page.getByText(/Always prepared: Misty Step/i).count()) > 0);
check("Onward gated before the feat spell is chosen",
  (await page.locator(".cf-foot button:not(:disabled)").count()) === 0);
await page.getByText("Charm Person", { exact: true }).first().click();
await page.waitForTimeout(150);
check("Onward unlocks once the feat spell is chosen",
  (await page.locator(".cf-foot button:not(:disabled)").count()) > 0);
await page.screenshot({ path: `${OUT}/feat-spell-pick.png`, fullPage: true });

await browser.close();
console.log("\n=== feat slots + school-scoped spell pick E2E ===");
let f = 0;
for (const r of results) {
  console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.n}${r.d ? " — " + r.d : ""}`);
  if (!r.ok) f++;
}
console.log(`\n${results.length - f}/${results.length} passed`);
process.exit(f ? 1 : 0);
