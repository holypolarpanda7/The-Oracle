/* The board/chat split: dragging the grip must really resize the board AND
   repaint the canvas at the new size, and the choice must survive a reload. */
import { chromium } from "playwright";
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
const p = await ctx.newPage();
p.on("pageerror", (e) => console.log("PAGE ERROR:", e.message));
async function intoFight(page) {
  await page.goto("http://localhost:4173/", { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  await page.locator("text=Kara Emberfall").first().click();
  await page.waitForTimeout(500);
  const e = page.locator("button", { hasText: /enter/i }).first();
  if (await e.count()) await e.click().catch(() => {});
  await page.waitForTimeout(2600);
  const i = page.locator(".promptbar input");
  await i.fill("I shoot the goblin");
  await i.press("Enter");
  await page.waitForTimeout(4000);
}
const dims = async (page) => page.evaluate(() => {
  const bd = document.querySelector(".vtt-board");
  const cv = document.querySelector(".vtt-board canvas");
  const sc = document.querySelector(".scroll");
  return {
    board: Math.round(bd.getBoundingClientRect().height),
    canvas: Math.round(cv.getBoundingClientRect().height),
    chat: Math.round(sc.getBoundingClientRect().height),
  };
});
await intoFight(p);
const before = await dims(p);
console.log("before drag:", before);

const grip = p.locator(".vtt-grip");
const box = await grip.boundingBox();
await p.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
await p.mouse.down();
await p.mouse.move(box.x + box.width / 2, box.y + box.height / 2 - 260, { steps: 16 });
await p.mouse.up();
await p.waitForTimeout(600);
const after = await dims(p);
console.log("after dragging up 260px:", after);

const fails = [];
if (after.board >= before.board - 150) fails.push("dragging did not shrink the board");
if (Math.abs(after.canvas - after.board) > 3) fails.push("the canvas did not follow the board");
if (after.chat <= before.chat) fails.push(
  `the chat did not gain the room the board gave up (${before.chat} -> ${after.chat})`);

// Persisted?
const p2 = await ctx.newPage();
await intoFight(p2);
const reloaded = await dims(p2);
console.log("after reload:", reloaded);
if (Math.abs(reloaded.board - after.board) > 4) fails.push("the split did not persist");

console.log("\nFAILS:", fails.length ? fails : "none");
await b.close();
process.exit(fails.length ? 1 : 0);
