import { useEffect, useState } from "react";
import type { AsiFeat, FeatPicks, FeatSpells, LevelUpData, SpellBrief } from "../lib/types";
import {
  ABILITY_CODES, AsiSpread, FeatChoiceFields, featChoicesSatisfied,
} from "./FeatChoices";
import { uiTick } from "../lib/sound";

/** Ability scores come off the sheet keyed by full name; the pickers speak
 *  3-letter codes. */
function byCode(scores?: Record<string, number>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const code of ABILITY_CODES) {
    const full = { str: "strength", dex: "dexterity", con: "constitution",
      int: "intelligence", wis: "wisdom", cha: "charisma" }[code];
    const v = scores?.[full] ?? scores?.[code] ?? scores?.[code.toUpperCase()];
    if (typeof v === "number") out[code] = v;
  }
  return out;
}

/** The Ability Score Improvement step: raise scores, or take a feat and answer
 *  whatever that feat asks. The level doesn't land until one of them is done. */
function AsiStep({ data, mode, setMode, increases, setIncreases,
                   feat, setFeat, picks, setPicks }: {
  data: LevelUpData;
  mode: "scores" | "feat";
  setMode: (m: "scores" | "feat") => void;
  increases: FeatPicks;
  setIncreases: (p: FeatPicks) => void;
  feat: string | null;
  setFeat: (s: string | null) => void;
  picks: FeatPicks;
  setPicks: (p: FeatPicks) => void;
}) {
  const scores = byCode(data.abilities);
  const feats = data.asi_feats ?? [];
  const chosen = feats.find((f) => f.slug === feat);
  // A feat with a school-scoped spell pick (Fey Touched) needs its pool, and
  // the server owns the filter — ask by feat slug, exactly as creation does.
  const [featSpells, setFeatSpells] = useState<FeatSpells | null>(null);
  useEffect(() => {
    if (!feat) { setFeatSpells(null); return; }
    let live = true;
    fetch(`/cc/feat_spells/${feat}`).then((r) => r.json())
      .then((j: FeatSpells) => { if (live) setFeatSpells(j.n > 0 ? j : null); })
      .catch(() => { if (live) setFeatSpells(null); });
    return () => { live = false; };
  }, [feat]);
  return (
    <>
      <div className="lu-pick-label">Ability Score Improvement</div>
      <div className="lu-asi-modes">
        <button className={`lu-option ${mode === "scores" ? "picked" : ""}`}
                onClick={() => { uiTick(); setMode("scores"); }}>
          <div className="lu-opt-name">Raise your scores</div>
          <div className="lu-opt-feats">+2 to one ability, or +1 to two</div>
        </button>
        <button className={`lu-option ${mode === "feat" ? "picked" : ""}`}
                onClick={() => { uiTick(); setMode("feat"); }}>
          <div className="lu-opt-name">Take a feat</div>
          <div className="lu-opt-feats">{feats.length} available</div>
        </button>
      </div>

      {mode === "scores" && (
        <AsiSpread
          choice={{ kind: "asi", total: 2, max: 20, hint: "Spend your points" }}
          scores={scores} picks={increases} onChange={setIncreases} />
      )}

      {mode === "feat" && (
        <>
          <div className="lu-options lu-featgrid">
            {feats.map((f: AsiFeat) => (
              <button
                key={f.slug}
                className={`lu-option ${feat === f.slug ? "picked" : ""} ${f.eligible ? "" : "blocked"}`}
                disabled={!f.eligible}
                title={f.eligible ? f.prerequisite || "" : f.blocked_reason || ""}
                onClick={() => { uiTick(); setFeat(f.slug === feat ? null : f.slug); setPicks({}); }}
              >
                <div className="lu-opt-name">{f.name}</div>
                <div className="lu-opt-feats">
                  {f.eligible ? f.brief.slice(0, 110) : `locked — ${f.blocked_reason}`}
                </div>
              </button>
            ))}
          </div>
          {chosen?.choices && (
            <FeatChoiceFields
              choice={chosen.choices} picks={picks} onChange={setPicks}
              spellPicker={(c) => (c.kind !== "spells" || !featSpells) ? null : (
                <>
                  <LuSpellPick
                    label={c.hint || `Feat spell (choose ${c.n ?? 1})`}
                    list={featSpells.spells} chosen={picks.spells ?? []}
                    n={c.n ?? 1}
                    onToggle={(slug) => setPicks({
                      ...picks,
                      spells: (picks.spells ?? []).includes(slug)
                        ? (picks.spells ?? []).filter((x) => x !== slug)
                        : [...(picks.spells ?? []), slug],
                    })} />
                  {featSpells.granted.length > 0 && (
                    <div className="lu-opt-feats">
                      Always prepared: {featSpells.granted.map((g) => g.name).join(", ")}.
                    </div>
                  )}
                </>
              )} />
          )}
        </>
      )}
    </>
  );
}

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
  onApply: (opts: { subclass?: string; cantrips?: string[]; spells?: string[];
    swap_out?: string; swap_in?: string;
    ability_increases?: Record<string, number>;
    feat?: string; feat_choices?: FeatPicks }) => void;
}) {
  const [picked, setPicked] = useState<string | null>(null);
  const [cantrips, setCantrips] = useState<string[]>([]);
  const [spells, setSpells] = useState<string[]>([]);
  // Optional level-up swap (known casters): replace one spell with another.
  const [swapOut, setSwapOut] = useState<string | null>(null);   // current spell NAME
  const [swapIn, setSwapIn] = useState<string | null>(null);     // replacement SLUG
  // Ability Score Improvement: scores or a feat, never both.
  const [asiMode, setAsiMode] = useState<"scores" | "feat">("scores");
  const [asiSpread, setAsiSpread] = useState<FeatPicks>({});
  const [asiFeat, setAsiFeat] = useState<string | null>(null);
  const [asiFeatPicks, setAsiFeatPicks] = useState<FeatPicks>({});
  const needsPick = !!data.subclass_required;
  const due = data.spells_due || null;
  const canSwap = !!due?.can_swap && (due.current_spells?.length ?? 0) > 0;
  const toggle = (set: React.Dispatch<React.SetStateAction<string[]>>) =>
    (slug: string) =>
      set((cur) => cur.includes(slug) ? cur.filter((x) => x !== slug) : [...cur, slug]);

  const spellsOk = !due
    || (cantrips.length === due.cantrips && spells.length === due.spells);
  // A swap is optional, but if started it must be completed (both ends chosen).
  const swapOk = !swapOut === !swapIn;
  const asiFeatRow = (data.asi_feats ?? []).find((f) => f.slug === asiFeat);
  const asiOk = !data.asi_due || (asiMode === "scores"
    ? Object.values(asiSpread.ability_increases ?? {}).reduce((a, b) => a + b, 0) === 2
    : !!asiFeat && featChoicesSatisfied(asiFeatRow?.choices, asiFeatPicks));
  const canConfirm = (!needsPick || picked !== null) && spellsOk && swapOk && asiOk;

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

        {data.asi_due && (
          <AsiStep data={data}
                   mode={asiMode} setMode={setAsiMode}
                   increases={asiSpread} setIncreases={setAsiSpread}
                   feat={asiFeat} setFeat={setAsiFeat}
                   picks={asiFeatPicks} setPicks={setAsiFeatPicks} />
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

        {canSwap && (
          <>
            <div className="lu-pick-label">
              Replace a known spell (optional){swapOut ? " · pick a replacement ↓" : ""}
              {swapOut && (
                <button className="lu-swap-clear"
                  onClick={() => { setSwapOut(null); setSwapIn(null); }}>✕ clear</button>
              )}
            </div>
            <div className="lu-options">
              {(due!.current_spells ?? []).map((s) => (
                <button
                  key={s.slug}
                  className={`lu-option ${swapOut === s.name ? "picked" : ""}`}
                  onClick={() => { setSwapOut(s.name); setSwapIn(null); }}
                >
                  <div className="lu-opt-name">{s.name}</div>
                  <div className="lu-opt-feats">drop this</div>
                </button>
              ))}
            </div>
            {swapOut && (
              <LuSpellPick label={`Replace “${swapOut}” with`} list={due!.spell_options}
                chosen={swapIn ? [swapIn] : []} n={1}
                onToggle={(slug) => setSwapIn((cur) => cur === slug ? null : slug)} />
            )}
          </>
        )}

        <div className="lu-actions">
          <button
            className="lu-confirm"
            disabled={!canConfirm}
            onClick={() => onApply({
              subclass: picked ?? undefined,
              cantrips: cantrips.length ? cantrips : undefined,
              spells: spells.length ? spells : undefined,
              swap_out: swapOut ?? undefined,
              swap_in: swapIn ?? undefined,
              // Exactly one of these when an ASI is due; neither otherwise.
              ability_increases: data.asi_due && asiMode === "scores"
                ? asiSpread.ability_increases : undefined,
              feat: data.asi_due && asiMode === "feat" && asiFeat
                ? asiFeat : undefined,
              feat_choices: data.asi_due && asiMode === "feat" && asiFeat
                ? asiFeatPicks : undefined,
            })}
          >
            {!canConfirm ? "choose above…" : "Take the level"}
          </button>
        </div>
      </div>
    </div>
  );
}
