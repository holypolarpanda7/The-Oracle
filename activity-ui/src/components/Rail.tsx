import type { Ally, SheetData } from "../lib/types";

function mod(v: number): string {
  const m = Math.floor((v - 10) / 2);
  return m >= 0 ? `+${m}` : `${m}`;
}

function hpClass(hp: number, max: number): string {
  const f = hp / Math.max(1, max);
  return f <= 0.25 ? "hp-bar dire" : f <= 0.6 ? "hp-bar hurt" : "hp-bar";
}

export function Rail({ sheet, sceneUrl }: {
  sheet: SheetData | null;
  sceneUrl: string | null;
}) {
  return (
    <aside className="rail">
      <div className="pane scene">
        {sceneUrl
          ? <img src={sceneUrl} alt="Scene" />
          : <div className="empty">no vision yet…</div>}
      </div>
      <div className="pane sheet">
        <div className="pane-title">Character</div>
        {sheet ? (
          <div className="sheet-body">
            <p className="sheet-name">{sheet.name}</p>
            <p className="sheet-sub">{sheet.subtitle}</p>
            <div className="hp-row">
              <span className="hp-num">{sheet.hp}</span>
              <span style={{ color: "var(--text-dim)" }}>/ {sheet.hp_max} HP</span>
              <span style={{ marginLeft: "auto", fontFamily: "var(--mono)" }}>
                AC {sheet.ac}
              </span>
            </div>
            <div className={hpClass(sheet.hp, sheet.hp_max)}>
              <div style={{ width: `${(100 * sheet.hp) / Math.max(1, sheet.hp_max)}%` }} />
            </div>
            <div className="stat-grid">
              {Object.entries(sheet.stats).map(([k, v]) => (
                <div className="stat" key={k}>
                  <div className="k">{k}</div>
                  <div className="v">{v}</div>
                  <div className="m">{mod(v)}</div>
                </div>
              ))}
            </div>
            <p className="inv-line"><b>Skills</b> · {sheet.skills.join(" · ")}</p>
            <p className="inv-line">
              <b>Pack</b> · {sheet.inventory.join(", ")}
              {sheet.gold !== undefined && <> · <b>{sheet.gold} gp</b></>}
            </p>
          </div>
        ) : (
          <div className="sheet-body inv-line">awaiting your character…</div>
        )}
      </div>
    </aside>
  );
}

export function PartyStrip({ members }: { members: Ally[] }) {
  if (!members.length) return null;
  return (
    <div className="pane" style={{ gridColumn: "1 / -1", gridRow: 2 }}>
      <div className="party">
        {members.map((a) => (
          <div className="ally" key={a.name}>
            <div className="nm">{a.name}</div>
            <div className={hpClass(a.hp, a.hp_max)}>
              <div style={{ width: `${(100 * a.hp) / Math.max(1, a.hp_max)}%` }} />
            </div>
            {a.condition && <div className="cond">{a.condition}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
