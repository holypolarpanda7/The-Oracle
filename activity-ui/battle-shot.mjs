// E2E: the fight gets the screen.
//
// A tactical fight used to be a panel inside the play surface — the board
// shared a column with the narration, under a status bar, an initiative strip,
// a "here & now" rail and a full character sheet — so the one thing that
// decides the outcome was about a fifth of the window. A player reported
// exactly that, with a screenshot.
//
// What is checked: the battle page replaces the play surface when a board is
// out; the board is the majority of the screen; the order is one tight rail
// rather than a row of cards; the page says WHOSE TURN it is (which nothing
// did — "Cultist 1's turn" appeared in six small places and never once said
// what the player should do about it); the sheet is available without living
// on screen; the log can be folded away; and Reset Layout does NOT reload the
// page, which used to drop the socket and strand a running fight.
//
// Run against a server with no backend (the demo feed only engages when the
// socket goes unanswered — `vite preview` proxies /ws, so use a static server):
//   npm run build && (cd dist && python3 -m http.server 4190)
//   npx node battle-shot.mjs http://localhost:4190/
import { chromium, devices } from "playwright";
import { mkdirSync } from "node:fs";
const BASE = process.argv[2] || "http://localhost:4190/";
const OUT = "./vtt-shots";
mkdirSync(OUT, { recursive: true });

const results = [];
const check = (n, ok, d = "") => results.push({ n, ok, d });

const browser = await chromium.launch();

async function toFight(page) {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1200);
  await page.locator(".char-card").first().click();
  await page.waitForSelector(".play", { timeout: 10000 });
  await page.waitForTimeout(600);
  await page.fill(".promptbar input", "I shoot the goblin");
  await page.locator(".promptbar input").press("Enter");
  await page.waitForSelector(".battle", { timeout: 10000 });
  await page.waitForTimeout(1200);
}

// ---------- desktop ----------
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
page.on("pageerror", (e) => console.log("PAGE ERROR:", e.message));
await toFight(page);

check("a board out puts the fight on its own page",
  (await page.locator(".battle").count()) === 1
  && (await page.locator(".play").count()) === 0);

const vp = page.viewportSize();
const board = await page.locator(".bt-board").boundingBox();
const share = (board.width * board.height) / (vp.width * vp.height);
// Not 100%: the app's own gilded frame insets everything inside it, and that
// is the frame's business rather than the battle page's.
check("the board IS the page — everything else floats on it", share > 0.9,
  `${Math.round(share * 100)}% of the viewport`);
// The MAP inside the panel is what matters; the panel also carries a title
// bar, a floor strip and the action bar.
const mapBox = await page.locator(".vtt-board").boundingBox();
check("...and the map itself gets most of it",
  (mapBox.width * mapBox.height) / (vp.width * vp.height) > 0.5,
  `${Math.round((mapBox.width * mapBox.height) / (vp.width * vp.height) * 100)}%`);
check("the page itself never scrolls",
  await page.evaluate(() =>
    document.documentElement.scrollHeight <= window.innerHeight + 1));
// The wheel is the zoom and only the zoom: left to bubble it scrolls whatever
// the board sits in, and the map walks away underneath the cursor.
await page.locator(".vtt-board").hover();
const scrolledBefore = await page.evaluate(() => window.scrollY);
await page.mouse.wheel(0, 400);
await page.waitForTimeout(200);
check("...and the wheel over the board zooms rather than scrolling",
  (await page.evaluate(() => window.scrollY)) === scrolledBefore);
const logBox = await page.locator(".bt-log").boundingBox();
check("the log floats over the board rather than taking a column from it",
  logBox.x + logBox.width > board.x + board.width - 40,
  `log right edge ${Math.round(logBox.x + logBox.width)} vs board ${Math.round(board.x + board.width)}`);
check("...and is wide enough to read prose in", logBox.width >= 300,
  `${Math.round(logBox.width)}px`);

check("the order is one tight rail, not a row of cards",
  (await page.locator(".bt-order .bt-pip").count()) > 1
  && (await page.locator(".bt-top").boundingBox()).height < 90,
  `${(await page.locator(".bt-top").boundingBox()).height}px tall`);

const turn = (await page.locator(".bt-turn").innerText()).toLowerCase();
check("the page says whose turn it is, in words",
  /your turn|is acting|resolving|set out/.test(turn), turn.slice(0, 60));

// The engine's own record, on its own, arriving per resolved turn.
const eng = (await page.locator(".bt-eng").innerText()).replace(/\s+/g, " ");
check("the engine has a log of its own, apart from the narration",
  (await page.locator(".bt-turnlog").count()) > 1, eng.slice(0, 70));
check("...one entry per creature's turn, each labelled with whose it was",
  (await page.locator(".bt-turnlog .bt-tl-who").count()) > 1);
check("...carrying the certified lines and no prose",
  /ATTACK:/.test(eng) && /TURN OVER/.test(eng));
check("...and a hit reads differently from a miss",
  (await page.locator(".bt-tl-line.hit").count()) > 0
  && (await page.locator(".bt-tl-line.miss").count()) > 0);

// Fighting without the prose: the engine has already finished by the time a
// local model gets round to describing the turn.
const proseBtn = page.locator(".bt-icon", { hasText: "✒" });
check("the prose can be turned off without leaving the fight",
  (await proseBtn.count()) === 1
  && (await proseBtn.getAttribute("class")).includes("on"));
await proseBtn.click();
await page.waitForTimeout(250);
check("...and the log says so", /prose off/i.test(
  await page.locator(".bt-eng-head").innerText()));
await proseBtn.click();
await page.waitForTimeout(200);

check("nothing else is competing for the space",
  (await page.locator(".statusbar").count()) === 0
  && (await page.locator(".here-now, .locale-rail").count()) === 0);

await page.screenshot({ path: `${OUT}/10-battle.png` });

// the sheet is a thing you look UP
await page.locator(".bt-icon", { hasText: "📜" }).click();
await page.waitForTimeout(300);
check("your sheet is one tap away", (await page.locator(".bt-sheet").count()) === 1);
await page.screenshot({ path: `${OUT}/11-battle-sheet.png` });
await page.locator(".bt-sheet").click({ position: { x: 8, y: 8 } });
await page.waitForTimeout(200);
check("...and does not stay in the way",
  (await page.locator(".bt-sheet").count()) === 0);

// the log folds away entirely
const before = logBox.width;
await page.locator(".bt-logtab").click();
await page.waitForTimeout(300);
const after = (await page.locator(".bt-log").boundingBox()).width;
check("the log folds away when the board is all you want",
  after < before / 3, `${Math.round(before)}px → ${Math.round(after)}px`);
await page.locator(".bt-logtab").click();
await page.waitForTimeout(200);

// ---------- Reset Layout must not reload ----------
// It called location.reload(), which drops the socket — and a fresh socket is
// bound to no session, so pressing it mid-fight put the player on the landing
// with a bout still running behind them.
const p2 = await browser.newPage({ viewport: { width: 1400, height: 900 } });
await p2.goto(BASE, { waitUntil: "domcontentloaded" });
await p2.waitForTimeout(1200);
await p2.locator(".char-card").first().click();
await p2.waitForSelector(".play", { timeout: 10000 });
await p2.waitForTimeout(500);
await p2.evaluate(() => { window.__stillHere = true; });
await p2.locator(".mbtn", { hasText: /Reset Layout/i }).click();
await p2.waitForTimeout(700);
check("Reset Layout does not throw the table away",
  (await p2.evaluate(() => window.__stillHere === true)) === true);
check("...and leaves you where you were",
  (await p2.locator(".play").count()) === 1);
await p2.close();

// ---------- phone ----------
const ctx = await browser.newContext({ ...devices["iPhone 13"] });
const m = await ctx.newPage();
await toFight(m);
const mb = await m.locator(".bt-board").boundingBox();
check("on a phone the board panel still leads", mb.height > 200,
  `${Math.round(mb.height)}px tall`);
// The panel is not the map: it carries a title bar, a floor strip, a movement
// line and the action bar, and sizing the CELL as a fraction of the viewport is
// exactly how the map got squeezed to a sliver at this width.
const mmap = await m.locator(".vtt-board").boundingBox();
check("...and so does the MAP inside it", mmap.height > 260,
  `${Math.round(mmap.height)}px of map`);
// The log is a bottom DRAWER at this width, and open by default it sits on top
// of the action bar — the one thing on the page you act with.
check("the log starts folded away on a phone",
  (await m.locator(".bt-log.shut").count()) === 1);
const bar = await m.locator(".action-bar, .abar, .vtt-foot").first().boundingBox();
const drawer = await m.locator(".bt-log").boundingBox();
check("...so nothing covers the acts you can take",
  !bar || drawer.y >= bar.y + bar.height - 2 || drawer.height < 40,
  `drawer y=${Math.round(drawer.y)} h=${Math.round(drawer.height)}`);
await m.screenshot({ path: `${OUT}/12-battle-phone.png` });

await browser.close();
console.log("\n=== the fight gets the screen ===");
let f = 0;
for (const r of results) { console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.n}${r.d ? " — " + r.d : ""}`); if (!r.ok) f++; }
console.log(`\n${results.length - f}/${results.length} passed`);
process.exit(f ? 1 : 0);
