import { useState } from "react";
import type { LevelUpData, SpellBrief } from "../lib/types";

/** A compact "choose N" spell list for the level-up overlay. */
function LuSpellPick({ label, list, chosen, n, onToggle }: {
  label: string; list: SpellBrief[]; chosen: string[]; n: number;
  onToggle: (slug: string) => void;
}) {
  const left = n - chosen.length;
  return (
    <>
      <div className="lu-pick-label">
        {label}{left > 0 ? ` · ${left} left` : " · ✓"}
      </div>
      <div className="lu-options">
        {list.map((sp) => {
          const on = chosen.includes(sp.slug);
          return (
            <button
              key={sp.slug}
              className={`lu-option ${on ? "picked" : ""}`}
              disabled={!on && chosen.length >= n}
              onClick={() => onToggle(sp.slug)}
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
    </>
  );
}

export function LevelUpOverlay({ data, onApply }: {
  data: LevelUpData;
  onApply: (opts: { subclass?: string; cantrips?: string[]; spells?: string[] }) => void;
}) {
  const [picked, setPicked] = useState<string | null>(null);
  const [cantrips, setCantrips] = useState<string[]>([]);
  const [spells, setSpells] = useState<string[]>([]);
  const needsPick = !!data.subclass_required;
  const due = data.spells_due || null;
  const toggle = (set: React.Dispatch<React.SetStateAction<string[]>>) =>
    (slug: string) =>
      set((cur) => cur.includes(slug) ? cur.filter((x) => x !== slug) : [...cur, slug]);

  const spellsOk = !due
    || (cantrips.length === due.cantrips && spells.length === due.spells);
  const canConfirm = (!needsPick || picked !== null) && spellsOk;

  return (
    <div className="levelup-veil">
      <div className="levelup">
        <div className="levelup-head">
          <span className="lu-title">Level Up</span>
          <span className="lu-arc">
            {data.class} {data.current_level} <span className="lu-arrow">➤</span>{" "}
            {data.next_level}
            {data.subclass ? ` · ${data.subclass}` : ""}
          </span>
        </div>

        <ul className="lu-notes">
          {data.notes.map((n, i) => <li key={i}>{n}</li>)}
          {data.class_features.map((f, i) => (
            <li key={`cf${i}`}>
              <b className="hl-name">{f.name}</b>
              {f.summary ? ` — ${f.summary.slice(0, 180)}${f.summary.length > 180 ? "…" : ""}` : ""}
            </li>
          ))}
          {(data.race_features ?? []).map((f, i) => (
            <li key={`rf${i}`}>
              <b className="hl-name">✦ {f.name}</b>
              {f.summary ? ` — ${f.summary.slice(0, 180)}${f.summary.length > 180 ? "…" : ""}` : ""}
            </li>
          ))}
        </ul>

        {needsPick && (
          <>
            <div className="lu-pick-label">
              Choose your {data.subclass_label || "subclass"}
            </div>
            <div className="lu-options">
              {data.subclass_options.map((o) => (
                <button
                  key={o.slug}
                  className={`lu-option ${picked === o.slug ? "picked" : ""}`}
                  onClick={() => setPicked(o.slug)}
                >
                  <div className="lu-opt-name">{o.name}</div>
                  {o.source?.includes("2024") && <div className="lu-opt-tag">PHB 2024</div>}
                  <div className="lu-opt-feats">
                    {(o.features || []).slice(0, 3).map((f) => f.name).join(" · ")}
                  </div>
                </button>
              ))}
            </div>
          </>
        )}

        {due && due.cantrips > 0 && (
          <LuSpellPick label={`New cantrips (choose ${due.cantrips})`}
            list={due.cantrip_options} chosen={cantrips} n={due.cantrips}
            onToggle={toggle(setCantrips)} />
        )}
        {due && due.spells > 0 && (
          <LuSpellPick
            label={`New ${due.mode === "prepared" ? "prepared spells"
              : due.mode === "spellbook" ? "spellbook spells" : "spells"} (choose ${due.spells})`}
            list={due.spell_options} chosen={spells} n={due.spells}
            onToggle={toggle(setSpells)} />
        )}

        <div className="lu-actions">
          <button
            className="lu-confirm"
            disabled={!canConfirm}
            onClick={() => onApply({
              subclass: picked ?? undefined,
              cantrips: cantrips.length ? cantrips : undefined,
              spells: spells.length ? spells : undefined,
            })}
          >
            {!canConfirm ? "choose above…" : "Take the level"}
          </button>
        </div>
      </div>
    </div>
  );
}
