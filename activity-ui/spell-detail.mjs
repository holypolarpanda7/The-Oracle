// E2E: the Spells stage tells you what a spell DOES.
//
// The picker's cards carry one sentence of a spell — its first sentence, cut
// at 140 characters — and the panel beside the grid, which carries the whole
// entry for a species, a background and a keepsake, showed nothing at all for a
// spell. Choosing between Sleep and Magic Missile off half a sentence each is
// choosing blind, and that is what a player reported.
//
// What is checked: the pane shows the spell's own row (casting time, range,
// components with the material, duration with concentration folded in, the
// whole description and the upcast rule); pointing at a spell shows it without
// taking it; a card LOCKED because the grid is full can still be read, which is
// the one you most want to read before swapping; and taking a spell shows it.
//
// Run: npm run build && npx vite preview --port 4180  (then npx node this)
import { chromium, devices } from "playwright";
const BASE = process.argv[2] || "http://localhost:4180/";
const OUT = "./ui-shots";

const spell = (slug, name, level, extra = {}) => ({
  slug, name, level, school: "Evocation", concentration: false, ritual: false,
  brief: `${name} — the first sentence, and no more than that`,
  casting_time: "1 action", range: "120 feet", duration: "Instantaneous",
  components: "V, S", ...extra,
});

const MAGIC_MISSILE = spell("magic-missile", "Magic Missile", 1, {
  desc: "You create three glowing darts of magical force. Each dart hits a "
      + "creature of your choice that you can see within range. A dart deals "
      + "1d4 + 1 Force damage to its target. The darts all strike "
      + "simultaneously, and you can direct them to hit one creature or "
      + "several.",
  higher_level: "The spell creates one more dart for each spell slot level "
              + "above 1.",
});
const SLEEP = spell("sleep", "Sleep", 1, {
  school: "Enchantment", concentration: true, duration: "1 minute",
  components: "V, S, M (a pinch of fine sand, rose petals, or a cricket)",
  material: "a pinch of fine sand, rose petals, or a cricket",
  dc_type: "WIS",
  desc: "Each creature of your choice in a 5-foot-radius Sphere centered on a "
      + "point within range must succeed on a Wisdom saving throw or have the "
      + "Incapacitated condition until the end of its next turn, at which point "
      + "it must repeat the save.",
});

const CC = {
  races: [{ slug: "dwarf", name: "Dwarf", ability_bonuses: {}, choose_bonus: [],
    speed: 30, size: "Medium", darkvision: true, languages: "Common, Dwarvish",
    traits: ["Resilience"] }],
  classes: [{ slug: "wizard", name: "Wizard", hit_die: 6, spellcasting_ability: "INT",
    saving_throws: ["INT", "WIS"], skill_choices_n: 2,
    skill_options: ["Arcana", "History", "Investigation", "Insight"],
    spellcasting: { ability: "INT", cantrips: 2, spells: 2, mode: "spellbook" } }],
  feats: [{ slug: "alert", name: "Alert", category: "origin", min_level: 1,
    brief: "+initiative.", choices: null }],
  backgrounds: [{ slug: "sage", name: "Sage", skills: ["Arcana"],
    abilities: ["INT", "WIS", "CHA"], origin_feat: "alert" }],
  ability_methods: { standard_array: [15, 14, 13, 12, 10, 8],
    point_buy: { budget: 27, min: 8, max: 15, costs: { "8": 0 } },
    roll: { expr: "4d6kh3", count: 6 } },
  common_items: [], buyable_items: [], starting_gold: { by_class: { wizard: 80 }, default: 100 },
};
const WIZ = {
  caster: true, class: "wizard", cantrips_n: 2, spells_n: 2, ability: "INT",
  mode: "spellbook",
  cantrips: ["Fire Bolt", "Light", "Ray of Frost"].map((n, i) => spell(`c${i}`, n, 0)),
  spells: [MAGIC_MISSILE, SLEEP, spell("shield", "Shield", 1),
           spell("grease", "Grease", 1)],
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ ...devices["Desktop Chrome"],
                                       viewport: { width: 1280, height: 860 } });
await ctx.route("**/cc/options", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify(CC) }));
await ctx.route("**/cc/origins", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify({ homelands: [], factions: [] }) }));
await ctx.route("**/cc/threads*", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify({ threads: [] }) }));
await ctx.route("**/cc/spells/wizard", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify(WIZ) }));
const page = await ctx.newPage();
const results = []; const check = (n, ok, d = "") => results.push({ n, ok, d });
const onward = async () => {
  await page.locator(".cf-foot button:not(:disabled)").first().click();
  await page.waitForTimeout(250);
};
const pane = async () => (await page.locator(".cf-detail-body").first().innerText())
  .replace(/\s+/g, " ");

await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(500);
await page.click(".landing-create");
await page.waitForSelector(".cf-grid .cf-card");
await page.getByText("Dwarf", { exact: true }).first().click(); await onward();
await page.getByText("Wizard", { exact: true }).first().click(); await onward();
await page.getByText("Sage", { exact: true }).first().click(); await onward();
await page.getByRole("button", { name: "Point Buy" }).click(); await page.waitForTimeout(120);
await page.locator(".cf-bonus-row").nth(0).locator(".cf-chip").first().click();
await page.locator(".cf-bonus-row").nth(1).locator(".cf-chip:not([disabled])").first().click();
await onward();
// skills stage — two of the class list
await page.locator(".cf-chips .cf-chip.big:not([disabled])").nth(0).click();
await page.locator(".cf-chips .cf-chip.big:not([disabled])").nth(1).click();
await page.waitForTimeout(150);
await onward();

// ---- the pane, before anything is pointed at
check("the stage says how to read a spell",
  /point at a spell/i.test(await pane()), (await pane()).slice(0, 60));

// ---- pointing at one shows the whole entry, and does NOT take it
const mm = page.locator(".cf-card", { hasText: "Magic Missile" }).first();
await mm.hover();
await page.waitForTimeout(150);
let p = await pane();
check("pointing at a spell shows its entry", /Magic Missile/.test(p));
check("...the whole description, not the card's one sentence",
  /direct them to hit one creature or several/i.test(p), p.slice(0, 90));
check("...casting time, range and components", /1 action/.test(p)
  && /120 feet/.test(p) && /V, S/.test(p));
check("...and how it grows when upcast",
  /one more dart for each spell slot level/i.test(p));
check("pointing at a spell does not TAKE it",
  !(await mm.getAttribute("class")).includes("picked"));

// ---- the material component and concentration, which are the two facts a
//      one-sentence card can never carry
await page.locator(".cf-card", { hasText: "Sleep" }).first().hover();
await page.waitForTimeout(150);
p = await pane();
check("a costly/named material component is shown",
  /rose petals/.test(p), p.slice(0, 90));
check("...and concentration is folded into the duration",
  /Concentration, 1 minute/.test(p));
await page.screenshot({ path: `${OUT}/cc-spell-detail.png` });

// ---- a full grid still lets you READ what you cannot take
await page.locator(".cf-card", { hasText: "Magic Missile" }).first().click();
await page.locator(".cf-card", { hasText: "Shield" }).first().click();
await page.waitForTimeout(200);
const grease = page.locator(".cf-card", { hasText: "Grease" }).first();
check("a spell you can no longer take is locked, not dead",
  (await grease.getAttribute("class")).includes("locked")
  && !(await grease.isDisabled()));
await grease.click();
await page.waitForTimeout(150);
p = await pane();
check("...and reading it still works", /Grease/.test(p), p.slice(0, 60));
check("...without taking it over the limit",
  await page.locator(".cf-card.picked", { hasText: "Grease" }).count() === 0);

await browser.close();
console.log("\n=== the spells stage explains a spell ===");
let f = 0;
for (const r of results) { console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.n}${r.d ? " — " + r.d : ""}`); if (!r.ok) f++; }
console.log(`\n${results.length - f}/${results.length} passed`);
process.exit(f ? 1 : 0);
