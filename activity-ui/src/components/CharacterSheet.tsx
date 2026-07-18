import { useState } from "react";
import { Frame, CORNER, type PanelHandle } from "./Frame";
import { ABILITY_ICON, Icon } from "./icons";
import { crestFor, raceBgFor, parseSubtitle } from "../lib/assets";
import type { SheetData } from "../lib/types";

function mod(v: number): string {
  const m = Math.floor((v - 10) / 2);
  return m >= 0 ? `+${m}` : `${m}`;
}
function hpMood(hp: number, max: number): string {
  const f = hp / Math.max(1, max);
  return f <= 0.25 ? "dire" : f <= 0.6 ? "hurt" : "";
}
const ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX"];
function featIcon(kind?: string): string {
  return kind === "fire" ? "i-fire" : kind === "arcane" ? "i-arcane" : "i-feat";
}

type Tab = "stats" | "inv" | "origin" | "feat";

export function CharacterSheet({ sheet, panel }: { sheet: SheetData | null; panel: PanelHandle }) {
  const [tab, setTab] = useState<Tab>("stats");

  if (!sheet) {
    return (
      <Frame className="sheet" panel={panel}>
        <div className="sheet-in"><p className="csub">awaiting your character…</p></div>
      </Frame>
    );
  }

  const parsed = parseSubtitle(sheet.subtitle);
  const race = sheet.race ?? parsed.race;
  const cls = sheet.char_class ?? parsed.charClass;
  const sub = sheet.subclass ?? parsed.subclass;
  const crest = crestFor(cls, sub);
  const raceBg = raceBgFor(race);
  const bgStyle = raceBg
    ? { backgroundImage: `linear-gradient(rgba(8,12,20,.74),rgba(6,9,16,.84)), url(${raceBg})` }
    : undefined;

  return (
    <Frame className="sheet" panel={panel}>
      <div className={`sheet-in ${raceBg ? "race-bg" : ""}`} style={bgStyle}>
        {crest && (
          <div className="crestwrap">
            <img className="cbadge" src={crest} alt="" />
            <img className="ec tl" src={CORNER} alt="" /><img className="ec tr" src={CORNER} alt="" />
            <img className="ec bl" src={CORNER} alt="" /><img className="ec br" src={CORNER} alt="" />
          </div>
        )}

        <div className="pwrap">
          <div className="pframe">
            <img className="pc tl" src={CORNER} alt="" /><img className="pc tr" src={CORNER} alt="" />
            <img className="pc bl" src={CORNER} alt="" /><img className="pc br" src={CORNER} alt="" />
            {sheet.portrait
              ? <img src={sheet.portrait} alt={sheet.name} />
              : <div className="noportrait">no portrait yet</div>}
          </div>
        </div>

        <div className="cname">{sheet.name}</div>
        <div className="csub">{sheet.subtitle}</div>

        <div className={`bar ${hpMood(sheet.hp, sheet.hp_max)}`}>
          <span style={{ width: `${(100 * sheet.hp) / Math.max(1, sheet.hp_max)}%` }} />
          <div className="t">HP {sheet.hp} / {sheet.hp_max} · AC {sheet.ac}</div>
        </div>

        {(sheet.spell_slots?.length || sheet.resources?.length) ? (
          <div className="res">
            {sheet.spell_slots?.map((s, i) => (
              <div className="rrow" key={`ss${s.level}`}>
                <span className="rl">{i === 0 ? "Spell Slots" : ""}</span>
                <span className="lv">{ROMAN[s.level] ?? s.level}</span>
                {Array.from({ length: s.total }).map((_, j) => (
                  <i key={j} className={`pip ${j < s.total - s.used ? "on" : ""}`} />
                ))}
              </div>
            ))}
            {sheet.resources?.map((r) => (
              <div className="rrow" key={r.name}>
                <span className="rl">{r.name}</span>
                {Array.from({ length: r.total }).map((_, j) => (
                  <i key={j} className={`pip ${j < r.total - r.used ? "pon" : ""}`} />
                ))}
                {r.die && (
                  <span style={{ fontFamily: "var(--mono)", fontSize: ".6rem", color: "var(--text-dim)", marginLeft: 4 }}>
                    {r.die}
                  </span>
                )}
              </div>
            ))}
          </div>
        ) : null}

        <div className="tabs">
          {(["stats", "inv", "origin", "feat"] as Tab[]).map((t) => (
            <button key={t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>
              {t === "stats" ? "Stats" : t === "inv" ? "Inventory" : t === "origin" ? "Origin" : "Features"}
            </button>
          ))}
        </div>

        <div className={`tabpane ${tab === "stats" ? "on" : ""}`}>
          <div className="stats">
            {Object.entries(sheet.stats).map(([k, v]) => (
              <div className="stat" key={k}>
                <Icon id={ABILITY_ICON[k.toUpperCase()] ?? "i-str"} className="si" />
                <div className="k">{k.toUpperCase()}</div>
                <div className="v">{v}</div>
                <div className="m">{mod(v)}</div>
              </div>
            ))}
          </div>
        </div>

        <div className={`tabpane ${tab === "inv" ? "on" : ""}`}>
          <div className="inv">
            {sheet.inventory.length
              ? sheet.inventory.map((it, i) => (
                  <div className="item" key={i}><span className="gem" />{it}</div>
                ))
              : <p className="lore">Your pack is empty.</p>}
            {sheet.gold !== undefined && (
              <div className="item">
                <span className="gem" style={{ color: "var(--gold)" }} />Gold
                <span className="q">{sheet.gold} gp</span>
              </div>
            )}
          </div>
        </div>

        <div className={`tabpane ${tab === "origin" ? "on" : ""}`}>
          <div className="lore">
            {sheet.background && <p><b>Background · {sheet.background}.</b></p>}
            <p><b>Skills.</b> {sheet.skills.join(" · ") || "—"}</p>
            {(race || cls) && (
              <p>{[race, cls, sub && `(${sub})`].filter(Boolean).join(" ")}</p>
            )}
          </div>
        </div>

        <div className={`tabpane ${tab === "feat" ? "on" : ""}`}>
          {sheet.features?.length
            ? sheet.features.map((f, i) => (
                <div className="feat" key={i}>
                  <div className="tile"><Icon id={featIcon(f.kind)} className="ico" /></div>
                  <div className="fx"><b>{f.name}</b>{f.note && <small>{f.note}</small>}</div>
                </div>
              ))
            : <p className="lore">Features appear here as you gain them.</p>}
        </div>
      </div>
    </Frame>
  );
}
