// E2E: three things a test round found missing from the Activity.
//
//   1. The background stage showed two skills on a card and nothing else — a
//      player picked a background without seeing the feat, the tool or the
//      gear it grants. The right-hand panel now carries the whole of it.
//   2. An option may ask its OWN questions: Eldritch Adept's Pact of the Tome
//      wants three cantrips AND two level-1 rituals, which is two separate
//      questions on the Spells stage and neither of them existed.
//   3. There was no way OUT. An Activity has no window chrome of its own.
//
// Run: npm run build && npx vite preview --port 4173  (then npx node this)
import { chromium, devices } from "playwright";
const BASE = process.argv[2] || "http://localhost:4173/";
const OUT = "./mobile-shots";

const TOME = "Pact of the Tome";
const CC = {
  races: [{ slug: "dwarf", name: "Dwarf", ability_bonuses: {}, choose_bonus: [],
    speed: 30, size: "Medium", darkvision: true, traits: ["Resilience"],
    languages: "Common, Dwarvish", choices: null }],
  classes: [{ slug: "fighter", name: "Fighter", hit_die: 10, saving_throws: ["STR", "CON"],
    skill_choices_n: 2, skill_options: ["Athletics", "Acrobatics", "Perception", "Survival"] }],
  feats: [{
    slug: "eldritch-adept", name: "Eldritch Adept", category: "origin", min_level: 1,
    brief: "One Eldritch Invocation.",
    choices: {
      kind: "options", n: 1, tag: "invocation",
      from: ["Pact of the Blade", "Pact of the Chain", TOME, "Armor of Shadows"],
      hint: "Eldritch Invocation — choose one.",
      also: [
        { kind: "ability", n: 1, from: ["int", "wis", "cha"], amount: 0,
          hint: "Choose the spellcasting ability for this feat." },
        { kind: "spells", n: 3, level: 0, when: TOME,
          hint: "Book of Shadows — three cantrips from any class's list." },
        { kind: "spells", n: 2, level: 1, ritual: true, when: TOME,
          hint: "Book of Shadows — two level 1 spells with the Ritual tag." },
      ],
    },
  }],
  backgrounds: [{
    slug: "acolyte", name: "Acolyte", skills: ["Insight", "Religion"],
    abilities: ["WIS", "INT", "CHA"], origin_feat: "eldritch-adept",
    feature: "Shelter of the Faithful", tool: "Calligrapher's Supplies",
    items: [{ name: "Holy Symbol", quantity: 1 }, { name: "Incense", quantity: 5 }],
  }],
  ability_methods: { standard_array: [15, 14, 13, 12, 10, 8],
    point_buy: { budget: 27, min: 8, max: 15, costs: { "8": 0 } }, roll: { expr: "4d6kh3", count: 6 } },
  common_items: [], buyable_items: [], starting_gold: { by_class: { fighter: 150 }, default: 100 },
};
const cantrip = (slug, name) => ({ slug, name, level: 0, school: "Evocation" });
const ritual = (slug, name) => ({ slug, name, level: 1, school: "Divination", ritual: true });
const FEAT_SPELLS = {
  feat: "eldritch-adept",
  picks: [
    { idx: 2, n: 3, level: 0, ritual: false, when: TOME,
      hint: "Book of Shadows — three cantrips from any class's list.",
      spells: [cantrip("fire-bolt", "Fire Bolt"), cantrip("light", "Light"),
               cantrip("mage-hand", "Mage Hand"), cantrip("guidance", "Guidance")],
      granted: [] },
    { idx: 3, n: 2, level: 1, ritual: true, when: TOME,
      hint: "Book of Shadows — two level 1 spells with the Ritual tag.",
      spells: [ritual("alarm", "Alarm"), ritual("detect-magic", "Detect Magic"),
               ritual("identify", "Identify")],
      granted: [] },
  ],
  n: 3, level: 0, schools: [], hint: "", spells: [], granted: [],
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ ...devices["iPhone 13"] });
await ctx.route("**/cc/options", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify(CC) }));
await ctx.route("**/cc/feat_spells/**", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify(FEAT_SPELLS) }));
await ctx.route("**/cc/spells/**", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify({ caster: false }) }));
const page = await ctx.newPage();
const results = []; const check = (n, ok, d = "") => results.push({ n, ok, d });
const onward = async () => { await page.locator(".cf-foot button:not(:disabled)").first().click(); await page.waitForTimeout(250); };
const canGo = async () => (await page.locator(".cf-foot button:not(:disabled)").count()) > 0;

await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(600);

// ---- 3. the way out, from the landing
check("the landing offers a way out",
  (await page.getByRole("button", { name: /leave the oracle/i }).count()) > 0);
await page.getByRole("button", { name: /leave the oracle/i }).click();
await page.waitForTimeout(200);
check("...which asks before it closes anything",
  (await page.getByText(/leave the oracle\?/i).count()) > 0
  && (await page.getByRole("button", { name: /leave the table/i }).count()) > 0);
await page.screenshot({ path: `${OUT}/32-exit-confirm.png`, fullPage: true });
await page.getByRole("button", { name: /^stay$/i }).click();
await page.waitForTimeout(200);
check("...and staying puts you back", (await page.getByText(/leave the oracle\?/i).count()) === 0);

await page.click(".landing-create");
await page.waitForSelector(".cf-grid .cf-card");
await page.getByText("Dwarf", { exact: true }).first().click(); await onward();
await page.getByText("Fighter", { exact: true }).first().click(); await onward();

// ---- 1. the background panel
await page.getByText("Acolyte", { exact: true }).first().click();
await page.waitForTimeout(250);
const panel = page.locator(".cf-detail-body").first();
const text = (await panel.innerText()).replace(/\s+/g, " ");
check("the panel names the Origin feat it grants", /Eldritch Adept/.test(text), text.slice(0, 90));
check("...its ability boosts", /WIS \/ INT \/ CHA/.test(text));
check("...the tool it trains", /Calligrapher/.test(text));
check("...its feature", /Shelter of the Faithful/.test(text));
check("...and the gear it hands over", /Holy Symbol/.test(text) && /Incense ×5/.test(text));
await page.screenshot({ path: `${OUT}/33-background-panel.png`, fullPage: true });

await onward();
await page.getByRole("button", { name: "Point Buy" }).click(); await page.waitForTimeout(150);
await page.locator(".cf-bonus-row").nth(0).locator(".cf-chip").first().click();
await page.locator(".cf-bonus-row").nth(1).locator(".cf-chip:not([disabled])").first().click();
await onward();

// ---- 2. the pact's own questions
await page.locator(".cf-chips .cf-chip.big:not([disabled])").nth(0).click(); await page.waitForTimeout(80);
await page.locator(".cf-chips .cf-chip.big:not([disabled])").nth(1).click(); await page.waitForTimeout(80);
check("the granted feat's invocation list is on screen",
  (await page.getByRole("button", { name: TOME }).count()) > 0);
await page.getByRole("button", { name: "Armor of Shadows" }).click();
await page.waitForTimeout(150);
await page.getByRole("button", { name: "CHA", exact: true }).click();
await page.waitForTimeout(200);
check("an invocation that asks nothing more lets you on", await canGo());

await page.getByRole("button", { name: TOME }).click();
await page.waitForTimeout(250);
await onward();   // the Spells stage
await page.waitForTimeout(400);
const heads = (await page.locator(".cf-sub-label").allInnerTexts()).join(" | ");
check("the Book of Shadows asks BOTH of its questions",
  /three cantrips/i.test(heads) && /ritual/i.test(heads), heads.slice(0, 120));
check("Onward gated until both are answered", !(await canGo()));
for (const name of ["Fire Bolt", "Light", "Mage Hand"]) {
  await page.locator(".cf-card", { hasText: name }).first().click();
  await page.waitForTimeout(80);
}
check("still gated with only the cantrips chosen", !(await canGo()));
for (const name of ["Alarm", "Detect Magic"]) {
  await page.locator(".cf-card", { hasText: name }).first().click();
  await page.waitForTimeout(80);
}
check("Onward unlocks once the rituals are chosen too", await canGo());
await page.screenshot({ path: `${OUT}/34-pact-of-the-tome.png`, fullPage: true });

await browser.close();
console.log("\n=== background panel · pact questions · the way out ===");
let f = 0;
for (const r of results) { console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.n}${r.d ? " — " + r.d : ""}`); if (!r.ok) f++; }
console.log(`\n${results.length - f}/${results.length} passed`);
process.exit(f ? 1 : 0);
