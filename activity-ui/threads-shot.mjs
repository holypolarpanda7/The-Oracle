// E2E: the unfinished-business questions on the background stage.
//
// A backstory is only useful to the DM if it left something OPEN. These are
// the questions that ask for that, and what they must do here is stay OUT of
// the way: six of them under an already-long stage, all optional, collapsed
// until somebody opens one.
//
// Run: npm run build && npx vite preview --port 4173  (then npx node this)
import { chromium, devices } from "playwright";
const BASE = process.argv[2] || "http://localhost:4173/";
const OUT = "./mobile-shots";

const CC = {
  races: [{ slug: "human", name: "Human", ability_bonuses: {}, choose_bonus: [],
    speed: 30, size: "Medium", darkvision: false, languages: "Common",
    traits: ["Resourceful"], choices: null }],
  classes: [{ slug: "fighter", name: "Fighter", hit_die: 10, saving_throws: ["STR", "CON"],
    skill_choices_n: 2, skill_options: ["Athletics", "Perception"] }],
  feats: [{ slug: "alert", name: "Alert", category: "origin", min_level: 1,
    brief: "+initiative.", choices: null }],
  backgrounds: [{ slug: "soldier", name: "Soldier", skills: ["Athletics"],
    abilities: ["STR", "DEX", "CON"], origin_feat: "alert", feature: "Military Rank",
    items: [{ name: "Insignia of Rank", quantity: 1 }] }],
  ability_methods: { standard_array: [15, 14, 13, 12, 10, 8],
    point_buy: { budget: 27, min: 8, max: 15, costs: { "8": 0 } },
    roll: { expr: "4d6kh3", count: 6 } },
  common_items: [], buyable_items: [],
  starting_gold: { by_class: { fighter: 150 }, default: 100 },
};
const ORIGINS = { homelands: [{ slug: "greenfields", name: "Greenfields", subtype: "region", brief: "" }], factions: [] };
const THREADS = { threads: [
  { slug: "lost-home", label: "Somewhere you can't go back to",
    question: "Is there a place you can't return to — burned, drowned, taken, or simply closed to you?",
    subject_prompt: null, wants_subject: false, reach: "far",
    suggestions: ["the village burned while I was away", "the holding my family lost to a debt"] },
  { slug: "vengeance", label: "A wrong done to you or yours",
    question: "Did somebody do something to you or your people that is still unanswered?",
    subject_prompt: "who did it — a name, a title, or a band", wants_subject: true, reach: "distant",
    suggestions: ["the captain who put my village to the torch"] },
  { slug: "missing", label: "Someone you're looking for",
    question: "Is there somebody you're trying to find?",
    subject_prompt: "who you're looking for", wants_subject: true, reach: "far",
    suggestions: ["my sister, who went to the capital and stopped writing"] },
]};

const browser = await chromium.launch();
const ctx = await browser.newContext({ ...devices["iPhone 13"] });
const json = (b) => (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify(b) });
await ctx.route("**/cc/options", json(CC));
await ctx.route("**/cc/origins", json(ORIGINS));
await ctx.route("**/cc/threads", json(THREADS));
await ctx.route("**/cc/feat_spells/**", json({ feat: "x", n: 0, spells: [], granted: [], picks: [] }));
await ctx.route("**/cc/spells/**", json({ caster: false }));

const page = await ctx.newPage();
const results = []; const check = (n, ok, d = "") => results.push({ n, ok, d });
const onward = async () => { await page.locator(".cf-foot button:not(:disabled)").first().click(); await page.waitForTimeout(250); };

await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(600);
await page.click(".landing-create");
await page.waitForSelector(".cf-grid .cf-card");
await page.getByText("Human", { exact: true }).first().click(); await onward();
await page.getByText("Fighter", { exact: true }).first().click(); await onward();

await page.getByText("Soldier", { exact: true }).first().click();
await page.waitForTimeout(300);

check("the stage asks about unfinished business",
  (await page.getByText(/unfinished business/i).count()) > 0);
check("...all three questions are offered", (await page.locator(".cf-thread").count()) === 3,
  `${await page.locator(".cf-thread").count()} found`);
check("...and every one is COLLAPSED, so the stage stays short",
  (await page.locator(".cf-thread-body").count()) === 0);
await page.screenshot({ path: `${OUT}/40-threads-collapsed.png`, fullPage: true });

// Open one and answer it from a suggestion.
await page.locator(".cf-thread-head", { hasText: "Somewhere you can't go back to" }).click();
await page.waitForTimeout(200);
check("opening one shows its question",
  (await page.getByText(/is there a place you can't return to/i).count()) > 0);
check("...with suggestions to choose from, not a blank box",
  (await page.locator(".cf-thread-body .cf-chip").count()) >= 2);
await page.locator(".cf-thread-body .cf-chip").first().click();
await page.waitForTimeout(200);
check("choosing one marks the thread answered",
  (await page.locator(".cf-thread.filled").count()) === 1);
check("...and a place can be named, but need not be",
  (await page.getByPlaceholder(/otherwise the world names it/i).count()) === 1);
await page.getByPlaceholder(/otherwise the world names it/i).fill("Ashmere");

// A kind that wants a subject asks for one; a kind that doesn't, doesn't.
await page.locator(".cf-thread-head", { hasText: "A wrong done to you or yours" }).click();
await page.waitForTimeout(200);
const vengeanceBody = page.locator(".cf-thread", { hasText: "A wrong done to you or yours" });
check("a thread that needs a person asks who",
  (await vengeanceBody.getByPlaceholder(/a name, if they have one/i).count()) === 1);
await vengeanceBody.getByPlaceholder(/a name, if they have one/i).fill("Captain Vurn");
await vengeanceBody.locator(".cf-thread-body .cf-chip").first().click();
await page.waitForTimeout(250);
await page.screenshot({ path: `${OUT}/41-threads-answered.png`, fullPage: true });

check("two answered, one still untouched",
  (await page.locator(".cf-thread.filled").count()) === 2);

// Clearing one puts it back — every question is optional, in both directions.
await page.locator(".cf-thread", { hasText: "Somewhere you can't go back to" })
  .getByRole("button", { name: /clear this one/i }).click();
await page.waitForTimeout(250);
check("clearing an answer really removes it",
  (await page.locator(".cf-thread.filled").count()) === 1);

// And the stage is still passable having answered nothing mandatory.
check("Onward is available — none of it is required",
  await page.locator(".cf-foot button:not(:disabled)").first().isEnabled());

console.log();
let bad = 0;
for (const r of results) {
  console.log(`  ${r.ok ? "\x1b[32m✓\x1b[0m" : "\x1b[31m✗\x1b[0m"} ${r.n}${r.d ? `  \x1b[2m— ${r.d}\x1b[0m` : ""}`);
  if (!r.ok) bad++;
}
console.log(bad ? `\n\x1b[31m${bad} failed\x1b[0m` : "\n\x1b[32mthe questions stay out of the way until they are wanted\x1b[0m");
await browser.close();
process.exit(bad ? 1 : 0);
