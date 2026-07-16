import { useEffect, useMemo, useState } from "react";
import type { CCOptions, CCPayload } from "../lib/types";
import { uiTick } from "../lib/sound";

const ABILITIES = ["STR", "DEX", "CON", "INT", "WIS", "CHA"] as const;
type Ability = (typeof ABILITIES)[number];
const ABILITY_FULL: Record<Ability, string> = {
  STR: "strength", DEX: "dexterity", CON: "constitution",
  INT: "intelligence", WIS: "wisdom", CHA: "charisma",
};

type Stage = "race" | "class" | "background" | "abilities" | "skills" | "review";
const STAGES: { id: Stage; label: string }[] = [
  { id: "race", label: "Origin" },
  { id: "class", label: "Class" },
  { id: "background", label: "Background" },
  { id: "abilities", label: "Abilities" },
  { id: "skills", label: "Skills" },
  { id: "review", label: "Name & Seal" },
];

interface Draft {
  race?: string;
  bonusPicks: Ability[];     // choose_bonus assignment, in order
  cls?: string;
  background?: string;
  method: "standard_array" | "point_buy" | "roll";
  pool: number[];            // values available to assign (array/roll)
  assigned: Partial<Record<Ability, number>>;
  pointBuy: Record<Ability, number>;
  skills: string[];
  feat?: string;
  name: string;
}

const freshDraft = (): Draft => ({
  bonusPicks: [], method: "standard_array", pool: [], assigned: {},
  pointBuy: { STR: 8, DEX: 8, CON: 8, INT: 8, WIS: 8, CHA: 8 },
  skills: [], name: "",
});

export function CreateFlow({ onDone, onCancel, ccError }: {
  onDone: (payload: CCPayload) => void;
  onCancel: () => void;
  ccError: string | null;
}) {
  const [opts, setOpts] = useState<CCOptions | null>(null);
  const [stage, setStage] = useState<Stage>("race");
  const [d, setD] = useState<Draft>(freshDraft());
  const [detail, setDetail] = useState<string | null>(null);

  useEffect(() => {
    fetch("/cc/options").then((r) => r.json()).then(setOpts)
      .catch(() => setOpts(null));
  }, []);

  const race = opts?.races.find((r) => r.slug === d.race);
  const cls = opts?.classes.find((c) => c.slug === d.cls);
  const bg = opts?.backgrounds.find((b) => b.slug === d.background);
  const needsBonusPicks = (race?.choose_bonus?.length ?? 0) > 0;
  // 2024 rules: every character's background grants an Origin feat, so everyone
  // picks one (not just Custom Lineage-style races) whenever feats are ingested.
  const needsFeat = (opts?.feats.length ?? 0) > 0;

  // ----- final scores -----
  const baseScores = useMemo((): Partial<Record<Ability, number>> => {
    if (d.method === "point_buy") return d.pointBuy;
    return d.assigned;
  }, [d]);

  const bonuses = useMemo((): Partial<Record<Ability, number>> => {
    const out: Partial<Record<Ability, number>> = {};
    if (!race) return out;
    if (needsBonusPicks) {
      race.choose_bonus.forEach((amt, i) => {
        const a = d.bonusPicks[i];
        if (a) out[a] = (out[a] ?? 0) + amt;
      });
    } else {
      for (const [k, v] of Object.entries(race.ability_bonuses)) {
        const a = k.slice(0, 3).toUpperCase() as Ability;
        if (ABILITIES.includes(a)) out[a] = (out[a] ?? 0) + v;
      }
    }
    return out;
  }, [race, d.bonusPicks, needsBonusPicks]);

  const finalScore = (a: Ability) =>
    (baseScores[a] ?? 0) + (bonuses[a] ?? 0) || undefined;

  // ----- stage gating -----
  const abilitiesDone = d.method === "point_buy"
    ? pointBuySpent(d.pointBuy, opts) <= (opts?.ability_methods.point_buy.budget ?? 27)
    : ABILITIES.every((a) => d.assigned[a] !== undefined);
  const skillsNeeded = cls?.skill_choices_n ?? 2;
  const stageDone: Record<Stage, boolean> = {
    race: !!d.race && (!needsBonusPicks
      || d.bonusPicks.length === (race?.choose_bonus.length ?? 0)),
    class: !!d.cls,
    background: !!d.background,
    abilities: abilitiesDone,
    skills: d.skills.length === skillsNeeded && (!needsFeat || !!d.feat),
    review: d.name.trim().length >= 2,
  };
  const stageIdx = STAGES.findIndex((s) => s.id === stage);
  const canNext = stageDone[stage];

  const next = () => {
    uiTick();
    if (stage === "review") {
      const stats: Record<string, number> = {};
      for (const a of ABILITIES) stats[ABILITY_FULL[a]] = finalScore(a) ?? 10;
      onDone({
        name: d.name.trim(),
        race: race!.name, char_class: cls!.name, background: bg!.slug,
        stats, skills: d.skills, feats: d.feat ? [d.feat] : undefined,
      });
      return;
    }
    setStage(STAGES[stageIdx + 1].id);
  };

  if (!opts) {
    return <div className="create"><div className="cf-loading">consulting the ledgers…</div></div>;
  }

  return (
    <div className="create">
      <nav className="cf-stages">
        {STAGES.map((s, i) => (
          <button
            key={s.id}
            className={`cf-stage ${stage === s.id ? "on" : ""} ${stageDone[s.id] ? "done" : ""}`}
            disabled={i > 0 && !STAGES.slice(0, i).every((p) => stageDone[p.id])}
            onClick={() => { uiTick(); setStage(s.id); }}
          >
            <span className="cf-stage-n">{["I", "II", "III", "IV", "V", "VI"][i]}</span>
            {s.label}
          </button>
        ))}
        <button className="cf-cancel" onClick={onCancel}>↩ leave</button>
      </nav>

      <main className="cf-main">
        {stage === "race" && (
          <>
            <div className="cf-grid">
              {opts.races.map((r) => (
                <button
                  key={r.slug}
                  className={`cf-card ${d.race === r.slug ? "picked" : ""}`}
                  onClick={() => {
                    uiTick();
                    setD({ ...d, race: r.slug, bonusPicks: [] });
                    setDetail(r.slug);
                  }}
                >
                  <div className="cf-card-name">{r.name}</div>
                  <div className="cf-card-sub">
                    {r.choose_bonus.length
                      ? `+${r.choose_bonus.join(" / +")} to abilities of your choice`
                      : Object.entries(r.ability_bonuses)
                          .map(([k, v]) => `+${v} ${k.slice(0, 3).toUpperCase()}`)
                          .join(", ") || "—"}
                  </div>
                </button>
              ))}
            </div>
            {needsBonusPicks && (
              <div className="cf-subpanel">
                <div className="cf-sub-label">
                  Assign {race!.choose_bonus.map((b) => `+${b}`).join(" and ")}
                </div>
                {race!.choose_bonus.map((amt, i) => (
                  <div className="cf-bonus-row" key={i}>
                    <span className="cf-bonus-amt">+{amt}</span>
                    {ABILITIES.map((a) => (
                      <button
                        key={a}
                        className={`cf-chip ${d.bonusPicks[i] === a ? "picked" : ""}`}
                        disabled={d.bonusPicks.includes(a) && d.bonusPicks[i] !== a}
                        onClick={() => {
                          uiTick();
                          const picks = [...d.bonusPicks];
                          picks[i] = a;
                          setD({ ...d, bonusPicks: picks });
                        }}
                      >{a}</button>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {stage === "class" && (
          <div className="cf-grid">
            {opts.classes.map((c) => (
              <button
                key={c.slug}
                className={`cf-card ${d.cls === c.slug ? "picked" : ""}`}
                onClick={() => { uiTick(); setD({ ...d, cls: c.slug, skills: [] }); setDetail(c.slug); }}
              >
                <div className="cf-card-name">{c.name}</div>
                <div className="cf-card-sub">
                  d{c.hit_die ?? "?"} hit die
                  {c.primary_ability ? ` · ${c.primary_ability}` : ""}
                  {c.spellcasting_ability ? ` · casts (${c.spellcasting_ability})` : ""}
                </div>
              </button>
            ))}
          </div>
        )}

        {stage === "background" && (
          <div className="cf-grid">
            {opts.backgrounds.map((b) => (
              <button
                key={b.slug}
                className={`cf-card ${d.background === b.slug ? "picked" : ""}`}
                onClick={() => { uiTick(); setD({ ...d, background: b.slug }); }}
              >
                <div className="cf-card-name">{b.name}</div>
                <div className="cf-card-sub">
                  {b.skills.length ? b.skills.join(", ") : "—"}
                </div>
              </button>
            ))}
          </div>
        )}

        {stage === "abilities" && (
          <AbilityStage opts={opts} d={d} setD={setD} bonuses={bonuses} />
        )}

        {stage === "skills" && (
          <>
            <div className="cf-sub-label">
              Choose {skillsNeeded} class skills
              {bg?.skills.length ? ` — your background grants ${bg.skills.join(", ")}` : ""}
            </div>
            <div className="cf-chips">
              {(cls?.skill_options ?? []).map((s) => {
                const on = d.skills.includes(s);
                const granted = bg?.skills.includes(s);
                return (
                  <button
                    key={s}
                    className={`cf-chip big ${on ? "picked" : ""} ${granted ? "granted" : ""}`}
                    disabled={granted || (!on && d.skills.length >= skillsNeeded)}
                    onClick={() => {
                      uiTick();
                      setD({
                        ...d,
                        skills: on ? d.skills.filter((x) => x !== s) : [...d.skills, s],
                      });
                    }}
                  >{s}{granted ? " ◆" : ""}</button>
                );
              })}
            </div>
            {needsFeat && (
              <>
                <div className="cf-sub-label" style={{ marginTop: 18 }}>
                  Your background grants an Origin feat
                </div>
                <div className="cf-grid">
                  {opts.feats.map((f) => (
                    <button
                      key={f.slug}
                      className={`cf-card ${d.feat === f.slug ? "picked" : ""}`}
                      onClick={() => { uiTick(); setD({ ...d, feat: f.slug }); }}
                    >
                      <div className="cf-card-name">{f.name}</div>
                      <div className="cf-card-sub">{f.brief}…</div>
                    </button>
                  ))}
                </div>
              </>
            )}
          </>
        )}

        {stage === "review" && (
          <div className="cf-review">
            <input
              className="cf-name"
              placeholder="Speak your name…"
              value={d.name}
              maxLength={40}
              onChange={(e) => setD({ ...d, name: e.target.value })}
            />
            <div className="cf-summary">
              <p><b>{race?.name}</b> {cls?.name}, {bg?.name}</p>
              <div className="stat-grid">
                {ABILITIES.map((a) => (
                  <div className="stat" key={a}>
                    <div className="k">{a}</div>
                    <div className="v">{finalScore(a) ?? "—"}</div>
                    {bonuses[a] ? <div className="m">+{bonuses[a]}</div> : <div className="m">&nbsp;</div>}
                  </div>
                ))}
              </div>
              <p className="inv-line"><b>Skills</b> · {[...(bg?.skills ?? []), ...d.skills].join(", ")}</p>
              {d.feat && (
                <p className="inv-line"><b>Feat</b> · {opts.feats.find((f) => f.slug === d.feat)?.name}</p>
              )}
            </div>
            {ccError && <p className="cf-error">⚠ {ccError}</p>}
          </div>
        )}
      </main>

      <aside className="cf-detail">
        <DetailPanel opts={opts} stage={stage} raceSlug={d.race} clsSlug={d.cls}
                     hovered={detail} />
      </aside>

      <footer className="cf-foot">
        <button
          className="lu-confirm"
          disabled={!canNext}
          onClick={next}
        >
          {stage === "review" ? "Seal the character" : "Onward ➤"}
        </button>
      </footer>
    </div>
  );
}

function pointBuySpent(pb: Record<Ability, number>, opts: CCOptions | null): number {
  const costs = opts?.ability_methods.point_buy.costs ?? {};
  return ABILITIES.reduce((n, a) => n + (costs[String(pb[a])] ?? 0), 0);
}

function AbilityStage({ opts, d, setD, bonuses }: {
  opts: CCOptions;
  d: Draft; setD: (d: Draft) => void;
  bonuses: Partial<Record<Ability, number>>;
}) {
  const pb = opts.ability_methods.point_buy;
  const spent = pointBuySpent(d.pointBuy, opts);

  const setMethod = (m: Draft["method"]) => {
    uiTick();
    if (m === "standard_array") {
      setD({ ...d, method: m, pool: [...opts.ability_methods.standard_array], assigned: {} });
    } else if (m === "roll") {
      setD({ ...d, method: m, pool: [], assigned: {} });
    } else {
      setD({ ...d, method: m });
    }
  };

  const rollNow = async () => {
    uiTick();
    const r = await fetch("/cc/roll_abilities", { method: "POST" });
    const j = await r.json();
    setD({ ...d, pool: j.rolls.map((x: { total: number }) => x.total), assigned: {} });
  };

  // assignment: click a pool value then an ability (or vice versa)
  const [held, setHeld] = useState<number | null>(null);
  const unassigned = [...d.pool];
  for (const a of ABILITIES) {
    const v = d.assigned[a];
    if (v !== undefined) {
      const i = unassigned.indexOf(v);
      if (i >= 0) unassigned.splice(i, 1);
    }
  }

  return (
    <div>
      <div className="cf-chips" style={{ marginBottom: 14 }}>
        {(["standard_array", "point_buy", "roll"] as const).map((m) => (
          <button key={m} className={`cf-chip big ${d.method === m ? "picked" : ""}`}
                  onClick={() => setMethod(m)}>
            {m === "standard_array" ? "Standard Array"
              : m === "point_buy" ? "Point Buy" : "Roll 4d6"}
          </button>
        ))}
        {d.method === "roll" && (
          <button className="cf-chip big" onClick={rollNow}>🎲 cast the dice</button>
        )}
        {d.method === "point_buy" && (
          <span className="cf-budget">
            {pb.budget - spent} points left
          </span>
        )}
      </div>

      {d.method !== "point_buy" && d.pool.length > 0 && (
        <div className="cf-chips" style={{ marginBottom: 12 }}>
          {unassigned.map((v, i) => (
            <button key={`${v}-${i}`}
                    className={`cf-chip big ${held === v ? "picked" : ""}`}
                    onClick={() => { uiTick(); setHeld(held === v ? null : v); }}>
              {v}
            </button>
          ))}
        </div>
      )}

      <div className="cf-abilities">
        {ABILITIES.map((a) => {
          const base = d.method === "point_buy" ? d.pointBuy[a] : d.assigned[a];
          const bonus = bonuses[a] ?? 0;
          return (
            <div key={a} className="cf-abil">
              <div className="k">{a}</div>
              {d.method === "point_buy" ? (
                <div className="cf-pb">
                  <button onClick={() => {
                    if (d.pointBuy[a] > pb.min)
                      setD({ ...d, pointBuy: { ...d.pointBuy, [a]: d.pointBuy[a] - 1 } });
                  }}>−</button>
                  <span className="v">{base}</span>
                  <button onClick={() => {
                    const nextV = d.pointBuy[a] + 1;
                    const cost = (pb.costs[String(nextV)] ?? 99)
                      - (pb.costs[String(d.pointBuy[a])] ?? 0);
                    if (nextV <= pb.max && spent + cost <= pb.budget)
                      setD({ ...d, pointBuy: { ...d.pointBuy, [a]: nextV } });
                  }}>+</button>
                </div>
              ) : (
                <button
                  className={`cf-slot ${base !== undefined ? "filled" : ""}`}
                  onClick={() => {
                    uiTick();
                    if (held !== null) {
                      setD({ ...d, assigned: { ...d.assigned, [a]: held } });
                      setHeld(null);
                    } else if (base !== undefined) {
                      const cp = { ...d.assigned };
                      delete cp[a];
                      setD({ ...d, assigned: cp });
                    }
                  }}
                >{base ?? "·"}</button>
              )}
              <div className="m">{bonus ? `+${bonus}` : " "}</div>
              <div className="cf-final">{base !== undefined ? base + bonus : "—"}</div>
            </div>
          );
        })}
      </div>
      <p className="cf-hint">
        {d.method === "point_buy"
          ? "Spend the budget; racial bonuses apply on top."
          : "Pick a value, then place it in an ability. Click a filled slot to clear it."}
      </p>
    </div>
  );
}

function DetailPanel({ opts, stage, raceSlug, clsSlug, hovered }: {
  opts: CCOptions; stage: Stage;
  raceSlug?: string; clsSlug?: string; hovered: string | null;
}) {
  if (stage === "race" || hovered) {
    const r = opts.races.find((x) => x.slug === (hovered ?? raceSlug));
    if (r && stage === "race") {
      return (
        <div className="cf-detail-body">
          <h3>{r.name}</h3>
          <p className="cf-detail-meta">
            {r.size} · {r.speed} ft speed{r.darkvision ? " · darkvision" : ""}
          </p>
          {r.languages && <p className="cf-detail-meta">{r.languages}</p>}
          <ul>{r.traits.map((t, i) => <li key={i}>{t}</li>)}</ul>
        </div>
      );
    }
  }
  if (stage === "class") {
    const c = opts.classes.find((x) => x.slug === clsSlug);
    if (c) {
      return (
        <div className="cf-detail-body">
          <h3>{c.name}</h3>
          <p className="cf-detail-meta">
            Hit die d{c.hit_die} · saves {c.saving_throws.join("/")}
          </p>
          <p className="cf-detail-meta">
            Skills ({c.skill_choices_n} of): {c.skill_options.join(", ")}
          </p>
        </div>
      );
    }
  }
  return (
    <div className="cf-detail-body dim">
      <p>The ledger awaits your choices.</p>
    </div>
  );
}
