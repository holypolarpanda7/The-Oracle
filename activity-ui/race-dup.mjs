// The species trait list must appear exactly once, on every viewport: the
// inline panel on phones, the side description column on desktop.
import { chromium, devices } from "playwright";
const CC_OPTIONS = {
  races: [
    { slug: "dwarf", name: "Dwarf", ability_bonuses: {}, choose_bonus: [],
      speed: 30, size: "Medium", darkvision: true, languages: "Common, Dwarvish",
      traits: ["Dwarven Resilience: advantage on saves vs poison.",
               "Stonecunning: Tremorsense 60 ft on stone.",
               "Darkvision 120 ft."] },
    { slug: "human", name: "Human", ability_bonuses: {}, choose_bonus: [],
      speed: 30, size: "Medium", darkvision: false, traits: ["Versatile"] },
  ],
  classes: [{ slug: "fighter", name: "Fighter", hit_die: 10, saving_throws: ["STR","CON"],
    skill_choices_n: 2, skill_options: ["Athletics","Perception"] }],
  feats: [], backgrounds: [{ slug: "soldier", name: "Soldier", skills: ["Athletics"],
    abilities: ["STR","CON","CHA"] }],
  ability_methods: { standard_array: [15,14,13,12,10,8],
    point_buy: { budget: 27, min: 8, max: 15, costs: { "8": 0 } },
    roll: { expr: "4d6kh3", count: 6 } },
  common_items: [], buyable_items: [],
  starting_gold: { by_class: {}, default: 100 },
};
const BASE = "http://localhost:4173/";
const browser = await chromium.launch();
const results = [];

for (const [label, opts] of [
  ["phone", { ...devices["iPhone 13"] }],
  ["desktop", { ...devices["Desktop Chrome"] }],
]) {
  const ctx = await browser.newContext(opts);
  await ctx.route("**/cc/options", (r) =>
    r.fulfill({ contentType: "application/json", body: JSON.stringify(CC_OPTIONS) }));
  const page = await ctx.newPage();
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);
  await page.getByText(/forge a new/i).first().click().catch(() => {});
  await page.waitForSelector(".create", { timeout: 8000 });
  await page.waitForTimeout(400);
  await page.locator(".cf-grid .cf-card").first().click();
  await page.waitForTimeout(400);

  const inlineVisible = await page.locator(".cf-inline-detail").isVisible().catch(() => false);
  const asideVisible = await page.locator("aside.cf-detail").isVisible().catch(() => false);
  // Count rendered trait <li> blocks that are actually on screen.
  const shown = await page.evaluate(() => {
    const lists = [...document.querySelectorAll(".cf-trait-list, .cf-detail-body ul")];
    return lists.filter((el) => el.offsetParent !== null && el.children.length > 0).length;
  });
  results.push({ label, inlineVisible, asideVisible, traitLists: shown });
  await page.screenshot({ path: `./ui-shots/race-${label}.png`, fullPage: true });
  await ctx.close();
}

let ok = true;
for (const r of results) {
  const pass = r.traitLists === 1;
  ok &&= pass;
  console.log(pass ? "PASS" : "FAIL", `- ${r.label}: ${r.traitLists} visible trait list(s)`,
    `(inline=${r.inlineVisible} aside=${r.asideVisible})`);
}
await browser.close();
process.exit(ok ? 0 : 1);
