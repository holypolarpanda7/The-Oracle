import { useState } from "react";
import type { RepData } from "../lib/types";

/** Prepared casters re-choose their prepared spells (on a long rest). Pre-seeds
 *  with the currently-prepared set; requires exactly `count` picks. */
export function ReprepareOverlay({ data, onApply, onClose }: {
  data: RepData;
  onApply: (spells: string[]) => void;
  onClose: () => void;
}) {
  const valid = new Set(data.options.map((o) => o.slug));
  const [chosen, setChosen] = useState<string[]>(
    data.current.filter((s) => valid.has(s)).slice(0, data.count));
  const left = data.count - chosen.length;
  const toggle = (slug: string) =>
    setChosen((cur) => cur.includes(slug) ? cur.filter((x) => x !== slug) : [...cur, slug]);

  return (
    <div className="levelup-veil" onClick={onClose}>
      <div className="levelup" onClick={(e) => e.stopPropagation()}>
        <div className="levelup-head">
          <span className="lu-title">Prepare Spells</span>
          <span className="lu-arc">{data.class} · choose {data.count}</span>
        </div>
        <ul className="lu-notes">
          <li>On a long rest you may change the spells you have prepared.</li>
        </ul>
        <div className="lu-pick-label">
          Prepared spells{left > 0 ? ` · ${left} left` : " · ✓"}
        </div>
        <div className="lu-options">
          {data.options.map((sp) => {
            const on = chosen.includes(sp.slug);
            return (
              <button
                key={sp.slug}
                className={`lu-option ${on ? "picked" : ""}`}
                disabled={!on && chosen.length >= data.count}
                onClick={() => toggle(sp.slug)}
              >
                <div className="lu-opt-name">{sp.name}</div>
                <div className="lu-opt-feats">
                  {[sp.school, sp.concentration ? "conc." : null,
                    sp.ritual ? "ritual" : null].filter(Boolean).join(" · ")}
                </div>
              </button>
            );
          })}
        </div>
        <div className="lu-actions" style={{ gap: 10 }}>
          <button className="lu-confirm" onClick={onClose}>Cancel</button>
          <button className="lu-confirm" disabled={chosen.length !== data.count}
                  onClick={() => onApply(chosen)}>Prepare</button>
        </div>
      </div>
    </div>
  );
}
