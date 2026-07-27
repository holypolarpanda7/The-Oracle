import { chromium, devices } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE || "http://localhost:4173/";
const OUT = "./mobile-shots";
mkdirSync(OUT, { recursive: true });

const results = [];
const check = (name, pass, detail = "") =>
  results.push({ name, pass, detail });

async function overflow(page) {
  return page.evaluate(() => {
    const de = document.documentElement;
    return { scrollW: de.scrollWidth, innerW: window.innerWidth };
  });
}

// Minimal-but-valid CCOptions so the creation grid renders without a backend.
const CC_OPTIONS = {
  races: [
    { slug: "human", name: "Human", ability_bonuses: {}, choose_bonus: [1, 1, 1],
      speed: 30, size: "Medium", darkvision: false, traits: ["Versatile", "Skillful"],
      feat_choice: "origin" },
    { slug: "elf", name: "Elf", ability_bonuses: {}, choose_bonus: [],
      speed: 30, size: "Medium", darkvision: true, traits: ["Fey Ancestry", "Trance"],
      lineage_label: "Lineage",
      lineages: [{ slug: "high", name: "High Elf", traits: ["Cantrip"] },
                 { slug: "wood", name: "Wood Elf", traits: ["Fleet of Foot"], speed: 35 }] },
    { slug: "dwarf", name: "Dwarf", ability_bonuses: {}, choose_bonus: [],
      speed: 30, size: "Medium", darkvision: true, traits: ["Dwarven Resilience"] },
    { slug: "dragonborn", name: "Dragonborn", ability_bonuses: {}, choose_bonus: [],
      speed: 30, size: "Medium", darkvision: false, traits: ["Breath Weapon", "Damage Resistance"] },
    { slug: "halfling", name: "Halfling", ability_bonuses: {}, choose_bonus: [],
      speed: 25, size: "Small", darkvision: false, traits: ["Lucky", "Brave"] },
    { slug: "tiefling", name: "Tiefling", ability_bonuses: {}, choose_bonus: [],
      speed: 30, size: "Medium", darkvision: true, traits: ["Hellish Resistance"] },
  ],
  classes: [
    { slug: "fighter", name: "Fighter", hit_die: 10, saving_throws: ["STR", "CON"],
      skill_choices_n: 2, skill_options: ["Athletics", "Acrobatics", "Intimidation", "Perception"] },
    { slug: "wizard", name: "Wizard", hit_die: 6, spellcasting_ability: "INT",
      saving_throws: ["INT", "WIS"], skill_choices_n: 2,
      skill_options: ["Arcana", "History", "Investigation", "Insight"] },
    { slug: "rogue", name: "Rogue", hit_die: 8, saving_throws: ["DEX", "INT"],
      skill_choices_n: 4, skill_options: ["Stealth", "Sleight of Hand", "Deception", "Perception"] },
    { slug: "cleric", name: "Cleric", hit_die: 8, spellcasting_ability: "WIS",
      saving_throws: ["WIS", "CHA"], skill_choices_n: 2,
      skill_options: ["Medicine", "Religion", "Insight", "Persuasion"] },
  ],
  feats: [
    { slug: "alert", name: "Alert", category: "origin", brief: "+ initiative; can't be surprised while conscious." },
    { slug: "tough", name: "Tough", category: "origin", brief: "+2 HP per level." },
    { slug: "magic-initiate", name: "Magic Initiate", category: "origin", brief: "Learn two cantrips and a 1st-level spell." },
  ],
  backgrounds: [
    { slug: "soldier", name: "Soldier", skills: ["Athletics", "Intimidation"],
      abilities: ["STR", "CON", "CHA"], origin_feat: "alert" },
    { slug: "sage", name: "Sage", skills: ["Arcana", "History"],
      abilities: ["INT", "WIS", "CHA"], origin_feat: "magic-initiate" },
    { slug: "acolyte", name: "Acolyte", skills: ["Insight", "Religion"],
      abilities: ["WIS", "INT", "CHA"], origin_feat: "tough" },
  ],
  ability_methods: {
    standard_array: [15, 14, 13, 12, 10, 8],
    point_buy: { budget: 27, min: 8, max: 15,
      costs: { "8": 0, "9": 1, "10": 2, "11": 3, "12": 4, "13": 5, "14": 7, "15": 9 } },
    roll: { expr: "4d6kh3", count: 6 },
  },
  common_items: [
    { slug: "spellbook", name: "Spellbook", item_type: "Wondrous", attunement: false, brief: "Holds your spells." },
  ],
  buyable_items: [
    { slug: "torch", name: "Torch", category: "gear", cost_gp: 1 },
    { slug: "rope", name: "Rope (50 ft)", category: "gear", cost_gp: 1 },
    { slug: "rations", name: "Rations (1 day)", category: "gear", cost_gp: 1 },
  ],
  starting_gold: { by_class: { fighter: 150, wizard: 80, rogue: 100, cleric: 120 }, default: 100 },
};

const browser = await chromium.launch();
const context = await browser.newContext({ ...devices["iPhone 13"] });
await context.route("**/cc/options", (route) =>
  route.fulfill({ contentType: "application/json", body: JSON.stringify(CC_OPTIONS) }));
await context.route("**/cc/roll_abilities", (route) =>
  route.fulfill({ contentType: "application/json", body: JSON.stringify({ rolls: [15, 14, 13, 12, 10, 8] }) }));
const page = await context.newPage();

// 1) Landing --------------------------------------------------------------
await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(700); // demo `hello`
await page.screenshot({ path: `${OUT}/01-landing.png`, fullPage: true });

const coarse = await page.evaluate(() => matchMedia("(pointer: coarse)").matches);
check("emulated pointer is coarse (touch)", coarse);

let ov = await overflow(page);
check("landing: no horizontal overflow", ov.scrollW <= ov.innerW + 1,
  `scrollW=${ov.scrollW} innerW=${ov.innerW}`);

const frameCols = await page.evaluate(() => {
  const f = document.querySelector(".frame");
  return f ? getComputedStyle(f).gridTemplateColumns : "n/a";
});
check("frame collapses to a single column", !/\s\d/.test(frameCols.trim()),
  `grid-template-columns: ${frameCols}`);

// 2) Character creation ----------------------------------------------------
await page.click(".landing-create");
await page.waitForSelector(".cf-grid .cf-card", { timeout: 5000 });
await page.waitForTimeout(300);
await page.screenshot({ path: `${OUT}/02-create-race.png`, fullPage: true });

ov = await overflow(page);
check("create: no horizontal overflow", ov.scrollW <= ov.innerW + 1,
  `scrollW=${ov.scrollW} innerW=${ov.innerW}`);

// pick the first race card so the detail panel populates below the grid
await page.locator(".cf-card").first().click();
await page.waitForTimeout(400);
await page.screenshot({ path: `${OUT}/03-create-picked.png`, fullPage: true });

// walk forward to the abilities stage (6-col grid → should be 3-col on phone)
let reachedAbilities = false;
for (let i = 0; i < 5 && !reachedAbilities; i++) {
  const next = page.locator(".cf-foot button:not(:disabled)").first();
  if (!(await next.count())) break;
  await next.click();
  await page.waitForTimeout(350);
  if (await page.locator(".cf-abilities").count()) {
    reachedAbilities = true;
    await page.screenshot({ path: `${OUT}/03b-create-abilities.png`, fullPage: true });
    const abilCols = await page.evaluate(() => {
      const g = document.querySelector(".cf-abilities");
      return g ? getComputedStyle(g).gridTemplateColumns : "n/a";
    });
    const nCols = abilCols.trim().split(/\s+/).length;
    check("abilities grid drops to 3 columns on phone", nCols === 3,
      `${nCols} cols (${abilCols})`);
    ov = await overflow(page);
    check("abilities stage: no horizontal overflow", ov.scrollW <= ov.innerW + 1,
      `scrollW=${ov.scrollW} innerW=${ov.innerW}`);
  } else {
    // pick the first option on intermediate stages to unlock Next
    const c = page.locator(".cf-card").first();
    if (await c.count()) await c.click();
    await page.waitForTimeout(200);
  }
}
check("reached the abilities stage", reachedAbilities);

// 3) Enter play ------------------------------------------------------------
await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(700);
const charCard = page.locator(".char-card").first();
check("landing shows a demo character to enter", (await charCard.count()) > 0);
if (await charCard.count()) {
  await charCard.click();
  await page.waitForSelector(".play", { timeout: 8000 });
  await page.waitForTimeout(3200); // demo opening blocks stream in
  await page.screenshot({ path: `${OUT}/04-play-viewport.png`, fullPage: false });
  await page.screenshot({ path: `${OUT}/05-play-full.png`, fullPage: true });

  ov = await overflow(page);
  check("play: no horizontal overflow", ov.scrollW <= ov.innerW + 1,
    `scrollW=${ov.scrollW} innerW=${ov.innerW}`);

  const inputPx = await page.evaluate(() => {
    const el = document.querySelector(".promptbar input");
    return el ? parseFloat(getComputedStyle(el).fontSize) : 0;
  });
  check("prompt input >= 16px (no iOS focus-zoom)", inputPx >= 16,
    `font-size=${inputPx}px`);

  const gripHidden = await page.evaluate(() => {
    const g = document.querySelector(".grip");
    if (!g) return true; // none rendered at all is also fine
    return getComputedStyle(g).display === "none";
  });
  check("resize grips hidden on touch", gripHidden);

  // scene panel must not exceed the viewport width
  const sceneW = await page.evaluate(() => {
    const s = document.querySelector(".play .cframe");
    return s ? s.getBoundingClientRect().width : 0;
  });
  check("scene panel fits viewport width", sceneW <= 390,
    `sceneW=${sceneW}px`);
}

await browser.close();

// Report ------------------------------------------------------------------
console.log("\n=== iPhone smoke test (iPhone 13 · 390x844) ===");
let failed = 0;
for (const r of results) {
  console.log(`${r.pass ? "PASS" : "FAIL"}  ${r.name}${r.detail ? "  — " + r.detail : ""}`);
  if (!r.pass) failed++;
}
console.log(`\n${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
