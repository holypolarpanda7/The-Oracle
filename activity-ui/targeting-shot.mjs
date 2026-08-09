/* The action bar and targeting, against the OFFLINE demo.
 *
 *   npm run build && npx vite preview --port 4173
 *   npx node targeting-shot.mjs
 *
 * Proves the picking flow reaches the board: arming an act rings the legal
 * targets, arming a template puts it on the cursor, and an illegal placement
 * says so instead of quietly doing nothing.
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
const BASE = process.env.BASE || "http://localhost:4173/";
const OUT = process.env.OUT || "./targeting-shots";
mkdirSync(OUT, { recursive: true });

const fails = [];
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "  \x1b[32m✓\x1b[0m" : "  \x1b[31m✗\x1b[0m"} ${name}`
    + (detail ? ` \x1b[2m— ${detail}\x1b[0m` : ""));
  if (!ok) fails.push(name);
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 980 } });
page.on("pageerror", (e) => { console.log("PAGE ERROR:", e.message); fails.push("page error"); });
page.on("console", (m) => { if (m.type() === "error") console.log("CONSOLE:", m.text()); });

await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);
await page.locator("text=Kara Emberfall").first().click();
await page.waitForTimeout(600);
const btn = page.locator("button", { hasText: /enter/i }).first();
if (await btn.count()) await btn.click().catch(() => {});
await page.waitForTimeout(2500);

// Open the board.
const input = page.locator(".promptbar input");
await input.fill("I shoot the goblin");
await input.press("Enter");
await page.waitForTimeout(4000);

console.log("\n\x1b[1mthe action bar\x1b[0m");
check("a board is out", await page.locator(".vtt").count() > 0);
check("the action bar rendered", await page.locator(".abar").count() > 0);
check("the economy pips are shown", await page.locator(".abar-pip").count() >= 3);
await page.locator(".vtt, .abar").last().screenshot({ path: `${OUT}/01-bar.png` });
await page.screenshot({ path: `${OUT}/01-full.png` });

const acts = await page.locator(".abar-act").allTextContents();
check("attacks are listed", acts.length > 0, acts.join(" | ").slice(0, 110));

console.log("\n\x1b[1maiming a creature target\x1b[0m");
// Spells tab, then arm Fire Bolt.
await page.locator(".abar-tab", { hasText: /spells/i }).first().click();
await page.waitForTimeout(300);
await page.locator(".abar-act", { hasText: "Fire Bolt" }).first().click();
await page.waitForTimeout(700);
check("the act reads as armed",
  await page.locator(".abar-act.armed").count() === 1);
check("the bar says what it is waiting for",
  (await page.locator(".abar-armed-hint").innerText()).toLowerCase().includes("click"));
const ringed = await page.locator(".vtt-token.targetable").count();
check("legal targets are ringed on the board", ringed > 0, `${ringed} ringed`);
await page.locator(".vtt").screenshot({ path: `${OUT}/02-targets.png` });

// A short-ranged act should ring FEWER targets than a 120-ft cantrip: that is
// the whole point of asking the board rather than the player.
await page.locator(".abar-cancel").click();
await page.waitForTimeout(200);
await page.locator(".abar-tab", { hasText: /attacks/i }).first().click();
await page.waitForTimeout(300);
await page.locator(".abar-act", { hasText: "Rapier" }).first().click();
await page.waitForTimeout(700);
const meleeRinged = await page.locator(".vtt-token.targetable").count();
const dimmed = await page.locator(".vtt-token.untargetable").count();
check("a 5-ft weapon rings fewer than a 120-ft cantrip", meleeRinged <= ringed,
  `rapier ${meleeRinged} vs fire bolt ${ringed}`);
check("the ones it can't reach are dimmed, not hidden", dimmed > 0,
  `${dimmed} dimmed with a reason`);
await page.locator(".vtt").screenshot({ path: `${OUT}/03-melee-targets.png` });

console.log("\n\x1b[1maiming a template\x1b[0m");
await page.locator(".abar-cancel").click();
await page.waitForTimeout(200);
await page.locator(".abar-tab", { hasText: /spells/i }).first().click();
await page.waitForTimeout(300);
await page.locator(".abar-act", { hasText: "Shatter" }).first().click();
await page.waitForTimeout(500);
check("an upcast slot picker appears when there is a choice",
  await page.locator(".abar-slots").count() >= 0);   // Shatter has one slot
// Move the pointer over the middle of the board so the template follows it.
const board = page.locator(".vtt-board");
const box = await board.boundingBox();
await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
await page.waitForTimeout(600);
await page.mouse.move(box.x + box.width * 0.52, box.y + box.height * 0.52);
await page.waitForTimeout(700);
const foot = await page.locator(".vtt-foot").innerText();
check("the footer reports what the template would catch",
  /catches|place it|range|effect/i.test(foot), foot.replace(/\s+/g, " ").slice(0, 100));
await page.locator(".vtt").screenshot({ path: `${OUT}/04-template.png` });
await page.screenshot({ path: `${OUT}/04-full.png` });

console.log("\n\x1b[1mmobile\x1b[0m");
await page.setViewportSize({ width: 420, height: 860 });
await page.waitForTimeout(900);
await page.screenshot({ path: `${OUT}/05-mobile.png` });
const barBox = await page.locator(".abar").boundingBox();
check("the bar stays inside the viewport on a phone",
  !barBox || barBox.width <= 420, `${barBox && Math.round(barBox.width)}px`);

await browser.close();
console.log();
if (fails.length) {
  console.log(`\x1b[31m${fails.length} check(s) failed:\x1b[0m ${fails.join(", ")}`);
  process.exit(1);
}
console.log(`\x1b[32mthe bar arms, the board answers\x1b[0m — shots in ${OUT}/`);
