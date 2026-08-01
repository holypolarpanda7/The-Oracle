/* Report what each band of the play surface costs in pixels during a fight.
 *
 * Layout arguments are settled by measuring, not by squinting: this is what
 * showed the initiative carousel eating 197px while the narration sat pinned
 * at its 150px floor. Run it after changing any fixed-height chrome (status
 * bar, carousel, board, prompt bar).
 *
 *   npm run build && npx vite preview --port 4173
 *   npx node layout-measure.mjs
 */
import { chromium } from "playwright";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto("http://localhost:4173/", { waitUntil: "networkidle" });
await p.waitForTimeout(1200);
await p.locator("text=Kara Emberfall").first().click();
await p.waitForTimeout(500);
const e = p.locator("button", { hasText: /enter/i }).first();
if (await e.count()) await e.click().catch(()=>{});
await p.waitForTimeout(2600);
const input = p.locator(".promptbar input");
await input.fill("I shoot the goblin");
await input.press("Enter");
await p.waitForTimeout(4000);
console.log(await p.evaluate(() => {
  const h = (s) => { const el = document.querySelector(s);
    return el ? Math.round(el.getBoundingClientRect().height) : null; };
  const st = document.querySelector(".stage");
  return {
    viewport: window.innerHeight,
    statusbar: h(".statusbar"),
    carousel: h(".combat-strip"),
    carouselCard: h(".cs-card"),
    board: h(".vtt-board"),
    vttWhole: h(".vtt"),
    scroll: h(".scroll"),
    promptbar: h(".promptbar"),
    stageVisible: st ? Math.round(st.getBoundingClientRect().height) : null,
    stageContent: st ? st.scrollHeight : null,
    stageOverflow: st ? st.scrollHeight - Math.round(st.getBoundingClientRect().height) : null,
  };
}));
await b.close();
