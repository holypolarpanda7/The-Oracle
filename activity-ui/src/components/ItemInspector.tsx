import { useState } from "react";
import type { ItemDetail } from "../lib/types";

export interface ItemView {
  name: string;
  detail?: ItemDetail;
  loading: boolean;
  error?: string;
}

const LEVELS = ["Cantrip", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th"];
function spellLevel(l?: number | null): string {
  if (l === undefined || l === null) return "—";
  return LEVELS[l] ?? `L${l}`;
}

export function ItemInspector({ view, onClose, onInscribe, onAction }: {
  view: ItemView | null;
  onClose: () => void;
  onInscribe: (book: string, spell: string) => void;
  onAction: (name: string, action: string) => void;
}) {
  const [spellInput, setSpellInput] = useState("");
  if (!view) return null;
  const d = view.detail;
  const isBook = d?.interactive === "spellbook";

  const submit = () => {
    const s = spellInput.trim();
    if (!s) return;
    onInscribe(view.name, s);
    setSpellInput("");
  };

  return (
    <div className="item-veil" onClick={onClose}>
      <div className="item-modal" onClick={(e) => e.stopPropagation()}>
        <button className="item-close" onClick={onClose} aria-label="Close">✕</button>

        <div className="item-art">
          {d?.image
            ? <img src={d.image} alt={view.name} />
            : <div className="item-art-ph">{view.loading ? "conjuring likeness…" : "✦"}</div>}
        </div>

        <div className="item-body">
          <div className="item-name">{view.name}</div>
          <div className="item-tags">
            {d?.type && <span className="item-tag">{d.type}</span>}
            {d?.rarity && (
              <span className={`item-tag rarity ${d.rarity.replace(/\s+/g, "-").toLowerCase()}`}>
                {d.rarity}
              </span>
            )}
            {d?.attunement && <span className="item-tag attune">requires attunement</span>}
          </div>

          {view.error && <p className="item-err">{view.error}</p>}
          {view.loading && !d && <p className="item-dim">unfurling the record…</p>}
          {d?.description && <p className="item-desc">{d.description}</p>}

          {d?.stats?.length ? (
            <ul className="item-stats">
              {d.stats.map((s, i) => <li key={i}>{s}</li>)}
            </ul>
          ) : null}

          {(d?.equipped || d?.attuned) && (
            <div className="item-state">
              {d?.equipped && <span className="state-badge on">Equipped</span>}
              {d?.attuned && <span className="state-badge att">Attuned</span>}
            </div>
          )}

          {d?.charges && (
            <div className="charges">
              <span className="ch-label">Charges</span>
              <span className="ch-pips">
                {Array.from({ length: d.charges.max }).map((_, i) => (
                  <i key={i} className={`ch-pip ${i < d!.charges!.current ? "on" : ""}`} />
                ))}
              </span>
              <span className="ch-num">{d.charges.current}/{d.charges.max}</span>
            </div>
          )}

          {d?.actions?.length ? (
            <div className="item-actions">
              {d.actions.map((a) => (
                <button
                  key={a.id}
                  className={`iact ${a.id}`}
                  disabled={view.loading}
                  onClick={() => onAction(view.name, a.id)}
                >
                  {a.label}
                </button>
              ))}
            </div>
          ) : null}

          {isBook && (
            <div className="spellbook">
              <div className="sb-title">Inscribed Spells</div>
              {d!.spells?.length ? (
                <ul className="sb-list">
                  {d!.spells!.map((sp, i) => (
                    <li key={i}><span className="sb-lvl">{spellLevel(sp.level)}</span>{sp.name}</li>
                  ))}
                </ul>
              ) : <p className="item-dim">No spells written yet.</p>}

              {d!.can_inscribe ? (
                <div className="sb-inscribe">
                  <input
                    value={spellInput}
                    onChange={(e) => setSpellInput(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && submit()}
                    placeholder="Inscribe a spell by name…"
                  />
                  <button onClick={submit} disabled={!spellInput.trim()}>Inscribe</button>
                </div>
              ) : (
                <p className="item-dim sb-note">
                  Only a wizard — or one trained to keep a spellbook — may inscribe here.
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
