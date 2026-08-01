import { useState } from "react";
import type { ItemDetail } from "../lib/types";

export interface ItemView {
  name: string;
  detail?: ItemDetail;
  loading: boolean;
  error?: string;
  /** What the server says about this item's picture:
   *  "pending"  — an ordinary catalog item the batch pre-render has not reached
   *  "describe" — nothing in the catalog matches, so only you can say what it is */
  artState?: "pending" | "describe";
  /** True while a described render is in flight. */
  drawing?: boolean;
  /** A rename is in flight, so the reply will carry a new name. */
  renaming?: boolean;
}

const LEVELS = ["Cantrip", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th"];
function spellLevel(l?: number | null): string {
  if (l === undefined || l === null) return "—";
  return LEVELS[l] ?? `L${l}`;
}

export function ItemInspector({ view, onClose, onInscribe, onAction, onDescribe,
                                onTemper, inventory }: {
  view: ItemView | null;
  onClose: () => void;
  onInscribe: (book: string, spell: string) => void;
  onAction: (name: string, action: string, target?: string) => void;
  /** Make a piece yours: your name for it, and what it looks like. */
  onDescribe: (name: string, text: string, title?: string) => void;
  /** Pay a smith to reforge ONE rolled property into a fresh one. */
  onTemper: (name: string, affix: string) => void;
  inventory?: string[];
}) {
  const [spellInput, setSpellInput] = useState("");
  const [storeSel, setStoreSel] = useState("");
  const [naming, setNaming] = useState(false);
  const [title, setTitle] = useState("");
  const [look, setLook] = useState("");
  if (!view) return null;
  const d = view.detail;
  const isBook = d?.interactive === "spellbook";
  const isContainer = d?.interactive === "container";
  const storable = (inventory ?? []).filter((n) => n !== view.name);

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
            : <div className="item-art-ph">
                {view.drawing ? "conjuring likeness…"
                  : view.artState === "describe" ? "no likeness — describe it below"
                  : view.artState === "pending" ? "not yet drawn"
                  : view.loading ? "conjuring likeness…" : "✦"}
              </div>}
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

          {/* Rolled properties. Rarity buys SLOTS, so this list is the real
              measure of a piece — and each line can be reforged at a price the
              server sets. */}
          {d?.affixes?.length ? (
            <div className="affixes">
              <div className="sb-title">Properties</div>
              {d.affixes.map((a) => (
                <div className={`affix t${a.tier}`} key={a.slug}>
                  <div className="af-head">
                    <b>{a.name}</b>
                    <em className="af-tier">tier {a.tier}</em>
                    {a.temper_gp !== undefined && (
                      <button
                        className="af-temper"
                        disabled={view.loading}
                        title={`Reforge this property — the replacement is a fresh roll and may be worse`}
                        onClick={() => onTemper(view.name, a.slug)}
                      >reforge · {a.temper_gp} gp</button>
                    )}
                  </div>
                  <p className="af-text">{a.text}</p>
                </div>
              ))}
            </div>
          ) : null}

          {/* Make it yours. An ordinary item shares one picture with every
              other copy in the world; naming and describing a piece gives it
              its own, and keeps its stats. Always available — a bought
              longsword can become Dawnbreaker — and required for anything the
              catalog has never heard of. */}
          <div className="namer">
            {naming ? (
              <>
                <div className="sb-title">Make it yours</div>
                <input
                  className="nm-title"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder={`Name it (or keep "${view.name}")`}
                  maxLength={60}
                />
                <textarea
                  className="nm-look"
                  value={look}
                  onChange={(e) => setLook(e.target.value)}
                  placeholder="What does it look like? Its make, its metal, its scars…"
                  rows={3}
                  maxLength={300}
                />
                <div className="nm-row">
                  <button
                    className="iact use"
                    disabled={look.trim().length < 8 || view.drawing}
                    onClick={() => {
                      onDescribe(view.name, look.trim(), title.trim() || undefined);
                      setNaming(false);
                      setTitle("");
                      setLook("");
                    }}
                  >{view.drawing ? "Conjuring…" : "Have it drawn"}</button>
                  <button className="iact" onClick={() => setNaming(false)}>Cancel</button>
                </div>
                <p className="nm-note">
                  This draws a picture that belongs to your piece alone.
                </p>
              </>
            ) : (
              <button className="iact" onClick={() => { setNaming(true); setTitle(""); }}>
                {view.artState === "describe" ? "✦ Describe it" : "✦ Name & describe it"}
              </button>
            )}
          </div>

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

          {isContainer && (
            <div className="container-w">
              <div className="sb-title">Contents</div>
              {d!.contents?.length ? (
                <ul className="cw-list">
                  {d!.contents!.map((cs, i) => (
                    <li key={i}>
                      <span className="cw-name">
                        {cs.name}{cs.qty && cs.qty > 1 ? ` ×${cs.qty}` : ""}
                      </span>
                      <button className="cw-take" disabled={view.loading}
                        onClick={() => onAction(view.name, "take_out", cs.name)}>
                        Take out
                      </button>
                    </li>
                  ))}
                </ul>
              ) : <p className="item-dim">Empty.</p>}
              {storable.length > 0 && (
                <div className="cw-store">
                  <select value={storeSel} onChange={(e) => setStoreSel(e.target.value)}>
                    <option value="">Store an item…</option>
                    {storable.map((nm) => <option key={nm} value={nm}>{nm}</option>)}
                  </select>
                  <button disabled={!storeSel || view.loading}
                    onClick={() => { if (storeSel) { onAction(view.name, "store", storeSel); setStoreSel(""); } }}>
                    Store
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
