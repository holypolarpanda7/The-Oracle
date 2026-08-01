import { chromium } from "playwright";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto("http://localhost:4173/", { waitUntil: "networkidle" });
await p.waitForTimeout(1200);
await p.locator("text=Kara Emberfall").first().click();
await p.waitForTimeout(500);
const e = p.locator("button", { hasText: /enter/i }).first();
if (await e.count()) await e.click().catch(()=>{});
await p.waitForTimeout(2800);
console.log(await p.evaluate(() => {
  const g = (s) => { const el=document.querySelector(s); if(!el) return null;
    const r=el.getBoundingClientRect();
    return {top:Math.round(r.top), bottom:Math.round(r.bottom), h:Math.round(r.height)}; };
  const aside = document.querySelector(".play aside");
  return {
    aside: g(".play aside"),
    asideOverflow: aside ? getComputedStyle(aside).overflow : null,
    crest: g(".crestwrap"),
    sheet: g(".sheet"),
    clippedBy: aside && g(".crestwrap")
      ? Math.round(aside.getBoundingClientRect().top - document.querySelector(".crestwrap").getBoundingClientRect().top)
      : null,
  };
}));
await b.close();
