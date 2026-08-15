// E2E: a SPECIES asks its own questions, and the species that grants "any
// feat you qualify for" is not held to a feat's level.
//
// Two bugs this pins, both reported from a real table:
//   · Custom Lineage could only take origin feats — every general feat was
//     greyed out as "level 4+", which is the class ASI schedule the gift
//     exists to step outside of;
//   · the Origin stage asked NOTHING beyond the species card: a human's
//     Skillful skill and every "two languages of your choice" line went
//     unasked, so the sheet never recorded them.
//
// Run: npm run build && npx vite preview --port 4173  (then npx node this)
import { chromium, devices } from "playwright";
const BASE = process.argv[2] || "http://localhost:4173/";
const OUT = "./mobile-shots";
const CC = {
  races: [
    { slug: "custom-lineage", name: "Custom Lineage", ability_bonuses: {},
      choose_bonus: [], speed: 30, size: "Medium", darkvision: false,
      languages: "Common + one extra of your choice", feat_choice: "any",
      traits: ["Darkvision 60 ft OR one skill of your choice", "Any feat"],
      choices: {
        kind: "options", n: 1, tag: "lineage-gift",
        from: ["Darkvision 60 ft", "One extra skill"],
        hint: "Your people's gift — choose one.",
        also: [
          { kind: "skills", n: 1, when: "One extra skill",
            hint: "Choose the skill your people trained you in." },
          { kind: "language", n: 1, from: ["Dwarvish", "Elvish", "Draconic"],
            hint: "Choose 1 more language you speak." },
        ] } },
    { slug: "dwarf", name: "Dwarf", ability_bonuses: {}, choose_bonus: [],
      speed: 30, size: "Medium", darkvision: true, traits: ["Resilience"],
      languages: "Common, Dwarvish", choices: null },
  ],
  classes: [{ slug: "fighter", name: "Fighter", hit_die: 10, saving_throws: ["STR", "CON"],
    skill_choices_n: 2, skill_options: ["Athletics", "Acrobatics", "Perception", "Survival"] }],
  feats: [
    { slug: "alert", name: "Alert", category: "origin", min_level: 1,
      brief: "+initiative.", choices: null },
    { slug: "tough", name: "Tough", category: "general", min_level: 4,
      prerequisite: "Level 4+", brief: "+2 HP per level.", choices: null },
    { slug: "boon-of-bloodshed", name: "Boon of Bloodshed", category: "epic-boon",
      min_level: 19, prerequisite: "Level 19+", brief: "Epic.", choices: null },
  ],
  backgrounds: [{ slug: "soldier", name: "Soldier", skills: ["Athletics"],
    abilities: ["STR", "DEX", "CON"], origin_feat: "alert" }],
  ability_methods: { standard_array: [15, 14, 13, 12, 10, 8],
    point_buy: { budget: 27, min: 8, max: 15, costs: { "8": 0 } }, roll: { expr: "4d6kh3", count: 6 } },
  common_items: [], buyable_items: [], starting_gold: { by_class: { fighter: 150 }, default: 100 },
};
const browser = await chromium.launch();
const ctx = await browser.newContext({ ...devices["iPhone 13"] });
await ctx.route("**/cc/options", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify(CC) }));
const page = await ctx.newPage();
const results = []; const check = (n, ok, d = "") => results.push({ n, ok, d });
const onward = async () => { await page.locator(".cf-foot button:not(:disabled)").first().click(); await page.waitForTimeout(250); };
const canGo = async () => (await page.locator(".cf-foot button:not(:disabled)").count()) > 0;

await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(600);
await page.click(".landing-create");
await page.waitForSelector(".cf-grid .cf-card");

// ---- a species with no questions passes straight through
await page.getByText("Dwarf", { exact: true }).first().click();
await page.waitForTimeout(200);
check("a species that asks nothing doesn't block the stage", await canGo());

// ---- ...and one with questions holds the stage until they're answered
await page.getByText("Custom Lineage", { exact: true }).first().click();
await page.waitForTimeout(250);
check("the species' own questions are on screen",
  (await page.getByText(/your people's gift/i).count()) > 0);
check("Onward gated until the gift is chosen", !(await canGo()));
check("the conditional skill isn't asked yet",
  (await page.getByText(/trained you in/i).count()) === 0);

await page.getByRole("button", { name: "One extra skill" }).click();
await page.waitForTimeout(150);
check("choosing the skill half reveals the skill picker",
  (await page.getByText(/trained you in/i).count()) > 0);
check("...and the language line became a real pick",
  (await page.getByText(/more language/i).count()) > 0);
check("a tongue the species already speaks isn't offered",
  (await page.getByRole("button", { name: "Common", exact: true }).count()) === 0);

await page.getByRole("button", { name: "Perception", exact: true }).first().click();
await page.waitForTimeout(120);
await page.getByRole("button", { name: "Draconic", exact: true }).first().click();
await page.waitForTimeout(150);
check("Onward unlocks once every question is answered", await canGo());
await page.screenshot({ path: `${OUT}/30-species-choices.png`, fullPage: true });

// swapping to the other half of the gift retracts the skill question
await page.getByRole("button", { name: "Darkvision 60 ft" }).click();
await page.waitForTimeout(150);
check("swapping the gift retracts the question it hung off",
  (await page.getByText(/trained you in/i).count()) === 0 && await canGo());
await page.getByRole("button", { name: "One extra skill" }).click();
await page.waitForTimeout(150);

await onward();
await page.getByText("Fighter", { exact: true }).first().click(); await onward();
await page.getByText("Soldier", { exact: true }).first().click(); await onward();
await page.getByRole("button", { name: "Point Buy" }).click(); await page.waitForTimeout(150);
await page.locator(".cf-bonus-row").nth(0).locator(".cf-chip").first().click();
await page.locator(".cf-bonus-row").nth(1).locator(".cf-chip:not([disabled])").first().click();
await onward();

// ---- the Skills stage: the class can't re-buy the species' skill, and the
// species feat slot reaches a level-4 feat.
const perception = page.locator(".cf-chips .cf-chip.big", { hasText: "Perception" }).first();
check("a skill the species granted is off the class list",
  await perception.isDisabled());
const toughCard = page.locator(".cf-card", { hasText: "Tough" }).first();
check("the 'any feat' slot reaches a level-4 feat at level 1",
  !(await toughCard.isDisabled()),
  (await toughCard.innerText()).replace(/\n/g, " "));
const boonCard = page.locator(".cf-card", { hasText: "Boon of Bloodshed" }).first();
check("...but an epic boon stays locked", await boonCard.isDisabled());
await page.screenshot({ path: `${OUT}/31-species-feat-slot.png`, fullPage: true });

await browser.close();
console.log("\n=== species choices + the 'any feat' slot ===");
let f = 0;
for (const r of results) { console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.n}${r.d ? " — " + r.d : ""}`); if (!r.ok) f++; }
console.log(`\n${results.length - f}/${results.length} passed`);
process.exit(f ? 1 : 0);
