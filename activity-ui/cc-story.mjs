// E2E: the parts of creation a second test round found missing.
//
//   1. A feat whose prerequisite is another FEAT was offered anyway — the
//      giant feats all read as free in the Custom Lineage slot, when what
//      gates them is Strike of the Giants and the strike it chose.
//   2. A background can now be PERSONALISED: a homeland and a people, drawn
//      from what the world already has or invented on the spot, plus the
//      character's own story.
//   3. A keepsake can be named and described — and the panel beside the grid
//      carries its whole text, not 160 characters of it.
//   4. Name & Seal shows the WHOLE character, not four lines of it.
//   5. The likeness is a STAGE of the wizard, and it comes BEFORE the seal:
//      a character could be sealed with no face at all and nothing said so.
//      There is no character row to draw against yet, so it renders against
//      the wizard's own draft token, which the seal adopts.
//
// Run: npm run build && npx vite preview --port 4173  (then npx node this)
import { chromium, devices } from "playwright";
const BASE = process.argv[2] || "http://localhost:4173/";
const OUT = "./mobile-shots";

const CC = {
  races: [{ slug: "custom-lineage", name: "Custom Lineage", ability_bonuses: {},
    choose_bonus: [], speed: 30, size: "Medium", darkvision: false,
    languages: "Common", feat_choice: "any", traits: ["Any feat"], choices: null }],
  classes: [{ slug: "fighter", name: "Fighter", hit_die: 10, saving_throws: ["STR", "CON"],
    skill_choices_n: 2, skill_options: ["Athletics", "Acrobatics", "Perception", "Survival"] }],
  feats: [
    { slug: "alert", name: "Alert", category: "origin", min_level: 1,
      brief: "+initiative.", choices: null },
    { slug: "strike-of-the-giants", name: "Strike of the Giants", category: "giant",
      min_level: 1, brief: "A giant's blow rides your weapon.",
      choices: { kind: "options", n: 1, tag: "giant-strike",
                 from: ["Fire Strike", "Hill Strike"], hint: "Choose your giant strike." } },
    { slug: "vigor-of-the-hill-giant", name: "Vigor of the Hill Giant", category: "giant",
      min_level: 4, prerequisite: "Strike of the Giants (Hill Strike)",
      brief: "A hill giant's stubborn bulk.", choices: null },
  ],
  backgrounds: [{ slug: "soldier", name: "Soldier", skills: ["Athletics"],
    abilities: ["STR", "DEX", "CON"], origin_feat: "alert",
    feature: "Military Rank", items: [{ name: "Insignia of Rank", quantity: 1 }] }],
  ability_methods: { standard_array: [15, 14, 13, 12, 10, 8],
    point_buy: { budget: 27, min: 8, max: 15, costs: { "8": 0 } }, roll: { expr: "4d6kh3", count: 6 } },
  common_items: [{
    slug: "cloak-of-billowing", name: "Cloak of Billowing", item_type: "Wondrous Item",
    attunement: false, brief: "As a Bonus Action you can make the cloak billow",
    desc: "As a Bonus Action you can make the cloak billow dramatically. It is otherwise an ordinary, well-made cloak — and the billowing is entirely, gloriously without mechanical effect.",
  }],
  buyable_items: [], starting_gold: { by_class: { fighter: 150 }, default: 100 },
};
const ORIGINS = {
  homelands: [{ slug: "greenfields", name: "Greenfields", subtype: "region", brief: "" },
              { slug: "millbrook", name: "Millbrook", subtype: "settlement", brief: "" }],
  factions: [],
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ ...devices["iPhone 13"] });
await ctx.route("**/cc/options", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify(CC) }));
await ctx.route("**/cc/origins", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify(ORIGINS) }));
await ctx.route("**/cc/feat_spells/**", (r) => r.fulfill({ contentType: "application/json",
  body: JSON.stringify({ feat: "x", n: 0, spells: [], granted: [], picks: [] }) }));
await ctx.route("**/cc/spells/**", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify({ caster: false }) }));
const page = await ctx.newPage();
const results = []; const check = (n, ok, d = "") => results.push({ n, ok, d });
const onward = async () => { await page.locator(".cf-foot button:not(:disabled)").first().click(); await page.waitForTimeout(250); };

await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(600);
await page.click(".landing-create");
await page.waitForSelector(".cf-grid .cf-card");
await page.getByText("Custom Lineage", { exact: true }).first().click(); await onward();
await page.getByText("Fighter", { exact: true }).first().click(); await onward();

// ---- 2. the background's story panel
await page.getByText("Soldier", { exact: true }).first().click();
await page.waitForTimeout(250);
check("the background offers a story to write",
  (await page.getByText(/your story/i).count()) > 0);
check("...with the homelands the world already has",
  (await page.getByRole("button", { name: "Greenfields", exact: true }).count()) > 0
  && (await page.getByRole("button", { name: "Millbrook", exact: true }).count()) > 0);
await page.getByRole("button", { name: "Greenfields", exact: true }).click();
await page.waitForTimeout(120);
check("...and a people you can invent, since the world has none yet",
  (await page.getByRole("button", { name: /name your people/i }).count()) > 0);
await page.getByRole("button", { name: /name your people/i }).click();
await page.waitForTimeout(120);
await page.getByPlaceholder(/a clan, a tribe/i).fill("The Hollow Kettle");
await page.getByPlaceholder(/who raised them/i).fill("Raised by the tinkers of the Kettle, and left when they would not follow the road.");
await page.waitForTimeout(150);
await page.screenshot({ path: `${OUT}/35-origin-panel.png`, fullPage: true });
await onward();

await page.getByRole("button", { name: "Point Buy" }).click(); await page.waitForTimeout(150);
await page.locator(".cf-bonus-row").nth(0).locator(".cf-chip").first().click();
await page.locator(".cf-bonus-row").nth(1).locator(".cf-chip:not([disabled])").first().click();
await onward();

// ---- 1. the parent-feat gate
await page.locator(".cf-chips .cf-chip.big:not([disabled])").nth(0).click(); await page.waitForTimeout(80);
await page.locator(".cf-chips .cf-chip.big:not([disabled])").nth(1).click(); await page.waitForTimeout(120);
const vigor = page.locator(".cf-card", { hasText: "Vigor of the Hill Giant" }).first();
check("a feat that builds on another is LOCKED without it",
  await vigor.isDisabled(), (await vigor.innerText()).replace(/\n/g, " "));
check("...and says which feat it needs",
  /Strike of the Giants/i.test(await vigor.innerText()));
await page.locator(".cf-card", { hasText: "Strike of the Giants" }).first().click();
await page.waitForTimeout(200);
check("taking the parent still doesn't unlock it without the right strike",
  await vigor.isDisabled());
await page.getByRole("button", { name: "Hill Strike", exact: true }).click();
await page.waitForTimeout(200);
check("the right strike is what opens it",
  !(await page.locator(".cf-card", { hasText: "Vigor of the Hill Giant" }).first().isDisabled()));
await page.screenshot({ path: `${OUT}/36-parent-feat-gate.png`, fullPage: true });
await onward();   // gear (the standard kit is fine as it stands)
await onward();   // the keepsake

// ---- 3. the keepsake: whole text, and words of your own
await page.getByText("Cloak of Billowing", { exact: true }).first().click();
await page.waitForTimeout(250);
const panel = (await page.locator(".cf-detail-body").first().innerText()).replace(/\s+/g, " ");
check("the panel carries the keepsake's WHOLE text",
  /gloriously without mechanical effect/.test(panel), panel.slice(0, 80));
check("...and it can be made yours", (await page.getByText(/make it yours/i).count()) > 0);
await page.getByPlaceholder(/a name of your own/i).fill("Kettle-Wind");
await page.getByPlaceholder(/what it looks like/i).fill("Sooty grey wool, mended at the shoulder with copper wire.");
await page.waitForTimeout(150);
await page.screenshot({ path: `${OUT}/37-keepsake.png`, fullPage: true });
check("...and the panel says the words are what get it drawn",
  /drawn|picture/i.test(await page.locator(".cf-keepsake").innerText()));
await onward();

// ---- 5. the likeness comes BEFORE the seal
check("the Likeness stage is reached before Name & Seal",
  (await page.locator(".portrait-step").count()) > 0,
  (await page.locator(".cf-stage.on").innerText()).replace(/\n/g, " "));
check("...and it is a stage of the wizard, not a screen with its own way out",
  (await page.locator(".portrait-step .ps-foot").count()) === 0);
await page.getByPlaceholder(/weathered half-elf/i)
  .fill("Sun-dark, close-cropped grey hair, a soldier's broken nose.");
await page.waitForTimeout(150);
await page.screenshot({ path: `${OUT}/39-likeness.png`, fullPage: true });
await onward();

// ---- 4. Name & Seal shows the whole character
await page.locator(".cf-name").fill("Vashti Kettleborn");
await page.waitForTimeout(200);
const review = (await page.locator(".cf-summary").innerText()).replace(/\s+/g, " ");
check("the review names the keepsake by its new name AND its base",
  /Kettle-Wind/.test(review) && /Cloak of Billowing/.test(review), review.slice(0, 100));
check("...the origin ties", /Greenfields/.test(review) && /Hollow Kettle/.test(review));
check("...the feats taken", /Strike of the Giants/.test(review));
check("...and the story in their own words", /tinkers of the Kettle/.test(review));
check("...the species' own traits", /Any feat/i.test(review));
check("...the numbers a level-1 sheet has", /Hit points/i.test(review)
  && /Proficiency/i.test(review) && /Saving throws/i.test(review));
check("...the gear, itemised rather than counted",
  /Standard kit|Bought/i.test(review) && /Fighter starting package/i.test(review));
check("...and the face they described",
  /soldier's broken nose/i.test(review), review.slice(0, 60));
await page.screenshot({ path: `${OUT}/38-review.png`, fullPage: true });

await browser.close();
console.log("\n=== parent feats · story · keepsake · review · likeness ===");
let f = 0;
for (const r of results) { console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.n}${r.d ? " — " + r.d : ""}`); if (!r.ok) f++; }
console.log(`\n${results.length - f}/${results.length} passed`);
process.exit(f ? 1 : 0);
