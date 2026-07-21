import { useEffect, useMemo, useState } from "react";
import type { CCOptions, CCPayload } from "../lib/types";
import { uiTick } from "../lib/sound";

const ABILITIES = ["STR", "DEX", "CON", "INT", "WIS", "CHA"] as const;
type Ability = (typeof ABILITIES)[number];
const ABILITY_FULL: Record<Ability, string> = {
  STR: "strength", DEX: "dexterity", CON: "constitution",
  INT: "intelligence", WIS: "wisdom", CHA: "charisma",
};

type Stage = "race" | "class" | "background" | "abilities" | "skills"
  | "gear" | "wondrous" | "review";
const STAGES: { id: Stage; label: string }[] = [
  { id: "race", label: "Origin" },
  { id: "class", label: "Class" },
  { id: "background", label: "Background" },
  { id: "abilities", label: "Abilities" },
  { id: "skills", label: "Skills" },
  { id: "gear", label: "Gear" },
  { id: "wondrous", label: "Wonder" },
  { id: "review", label: "Name & Seal" },
];
const NUMERALS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII"];

interface Draft {
  race?: string;
  lineage?: string;   // chosen sub-species/ancestry slug (races that have them)
  cls?: string;
  background?: string;
  // 2024 ability boosts come from the background: +2/+1 to two of its abilities,
  // or +1 to each of three. (boost2/boost1 for "two-one" mode.)
  boostMode: "two-one" | "spread";
  boost2?: Ability;
  boost1?: Ability;
  method: "standard_array" | "point_buy" | "roll";
  pool: number[];            // values available to assign (array/roll)
  assigned: Partial<Record<Ability, number>>;
  pointBuy: Record<Ability, number>;
  skills: string[];
  featBg?: string;    // the background's Origin feat
  featRace?: string;  // a species-granted feat (Human origin, Custom Lineage any)
  gearMode: "kit" | "buy";
  cart: Record<string, number>;   // buyable item name -> quantity
  wondrous?: string;              // rules_item slug
  name: string;
}

const freshDraft = (): Draft => ({
  boostMode: "two-one", method: "standard_array", pool: [], assigned: {},
  pointBuy: { STR: 8, DEX: 8, CON: 8, INT: 8, WIS: 8, CHA: 8 },
  skills: [], gearMode: "kit", cart: {}, name: "",
});

const CASTER_CLASSES = new Set([
  "bard", "cleric", "druid", "paladin", "ranger", "sorcerer", "warlock",
  "wizard", "artificer",
]);

/** Client-side mirror of the backend feat-prerequisite check (level minimum,
    ability minimums, spellcasting). Returns null when met, else the reason. */
function featBlockReason(
  feat: CCOptions["feats"][number],
  finalStats: Partial<Record<Ability, number>>,
  clsSlug?: string,
): string | null {
  if ((feat.min_level ?? 1) > 1) return `level ${feat.min_level}+`;
  const pre = (feat.prerequisite ?? "").trim();
  if (!pre) return null;
  for (const clause of pre.split(/[;,]| and /)) {
    const c = clause.trim().toLowerCase();
    if (!c) continue;
    const m = c.match(/(str|dex|con|int|wis|cha)[a-z]*\D*(\d+)/);
    if (m) {
      const code = m[1].slice(0, 3).toUpperCase() as Ability;
      if ((finalStats[code] ?? 0) < Number(m[2]))
        return `needs ${m[1].slice(0, 3).toUpperCase()} ${m[2]}+`;
      continue;
    }
    if (c.includes("spellcast") || c.includes("cast a spell")) {
      if (!CASTER_CLASSES.has((clsSlug ?? "").toLowerCase()))
        return "needs a spellcasting class";
    }
  }
  return null;
}

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

  // 2024 feats: the background grants an Origin feat (everyone picks one), and
  // some species grant a second feat — Human an Origin feat, Custom Lineage
  // any feat you qualify for.
  const originFeats = useMemo(
    () => (opts?.feats ?? []).filter((f) => (f.category ?? "origin") === "origin"),
    [opts]);
  const needsBgFeat = (opts?.feats.length ?? 0) > 0;
  const raceFeat = race?.feat_choice ?? null;   // "origin" | "any" | null
  const raceFeatPool = useMemo(() => {
    if (!raceFeat || !opts) return [];
    return raceFeat === "any" ? opts.feats : originFeats;
  }, [raceFeat, opts, originFeats]);

  // 2024 ability boosts come from the background's listed abilities (3 of them;
  // a legacy background with none falls back to "any ability"). +1/+1/+1 is only
  // offered when there are exactly three to spread across.
  const boostPool: Ability[] = useMemo(() => {
    const codes = (bg?.abilities ?? []).filter(
      (a): a is Ability => (ABILITIES as readonly string[]).includes(a));
    return codes.length ? codes : [...ABILITIES];
  }, [bg]);
  const canSpread = boostPool.length === 3;

  // ----- final scores -----
  const baseScores = useMemo((): Partial<Record<Ability, number>> => {
    if (d.method === "point_buy") return d.pointBuy;
    return d.assigned;
  }, [d]);

  const bonuses = useMemo((): Partial<Record<Ability, number>> => {
    const out: Partial<Record<Ability, number>> = {};
    if (d.boostMode === "spread" && canSpread) {
      for (const a of boostPool) out[a] = 1;
    } else {
      if (d.boost2) out[d.boost2] = 2;
      if (d.boost1) out[d.boost1] = (out[d.boost1] ?? 0) + 1;
    }
    return out;
  }, [d.boostMode, d.boost2, d.boost1, boostPool, canSpread]);

  const boostDone = (d.boostMode === "spread" && canSpread)
    || (!!d.boost2 && !!d.boost1 && d.boost2 !== d.boost1);

  const finalScore = (a: Ability) =>
    (baseScores[a] ?? 0) + (bonuses[a] ?? 0) || undefined;

  // Final ability scores for feat-prerequisite gating.
  const finalStats = useMemo((): Partial<Record<Ability, number>> => {
    const out: Partial<Record<Ability, number>> = {};
    for (const a of ABILITIES) out[a] = (baseScores[a] ?? 0) + (bonuses[a] ?? 0);
    return out;
  }, [baseScores, bonuses]);

  // ----- stage gating -----
  const abilitiesBase = d.method === "point_buy"
    ? pointBuySpent(d.pointBuy, opts) <= (opts?.ability_methods.point_buy.budget ?? 27)
    : ABILITIES.every((a) => d.assigned[a] !== undefined);
  const abilitiesDone = abilitiesBase && boostDone;
  const skillsNeeded = cls?.skill_choices_n ?? 2;

  // Buy-mode gear budget (per-class starting gold) + running cart cost.
  const budget = cls
    ? (opts?.starting_gold.by_class[cls.slug] ?? opts?.starting_gold.default ?? 0)
    : (opts?.starting_gold.default ?? 0);
  const cartCost = useMemo(() => Object.entries(d.cart).reduce((sum, [name, qty]) => {
    const it = opts?.buyable_items.find((b) => b.name === name);
    return sum + (it ? it.cost_gp * qty : 0);
  }, 0), [d.cart, opts]);

  const stageDone: Record<Stage, boolean> = {
    race: !!d.race && (!(race?.lineages?.length) || !!d.lineage),
    class: !!d.cls,
    background: !!d.background,
    abilities: abilitiesDone,
    skills: d.skills.length === skillsNeeded
      && (!needsBgFeat || !!d.featBg)
      && (!raceFeat || !!d.featRace),
    gear: d.gearMode === "kit" || cartCost <= budget,  // buy is fine even empty
    wondrous: true,                                     // optional — always ok
    review: d.name.trim().length >= 2,
  };
  const stageIdx = STAGES.findIndex((s) => s.id === stage);
  const canNext = stageDone[stage];

  const next = () => {
    uiTick();
    if (stage === "review") {
      const stats: Record<string, number> = {};
      for (const a of ABILITIES) stats[ABILITY_FULL[a]] = finalScore(a) ?? 10;
      const feats = [d.featBg, d.featRace].filter(Boolean) as string[];
      const lineageName = race?.lineages?.find((l) => l.slug === d.lineage)?.name;
      onDone({
        name: d.name.trim(),
        race: lineageName ? `${race!.name} (${lineageName})` : race!.name,
        char_class: cls!.name, background: bg!.slug,
        stats, skills: d.skills, feats: feats.length ? feats : undefined,
        gear_mode: d.gearMode,
        bought_items: d.gearMode === "buy"
          ? Object.entries(d.cart).map(([name, quantity]) => ({ name, quantity }))
          : undefined,
        wondrous_item: d.wondrous,
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
            <span className="cf-stage-n">{NUMERALS[i]}</span>
            {s.label}
          </button>
        ))}
        <button className="cf-cancel" onClick={onCancel}>↩ leave</button>
      </nav>

      <main className="cf-main">
        {stage === "race" && (
          <>
            <div className="cf-sub-label">
              Your species shapes body and blood — ability boosts come from your
              background (2024 rules).
            </div>
            <div className="cf-grid">
              {opts.races.map((r) => (
                <button
                  key={r.slug}
                  className={`cf-card ${d.race === r.slug ? "picked" : ""}`}
                  onClick={() => {
                    uiTick();
                    // changing species clears its lineage + any race feat
                    setD({ ...d, race: r.slug, lineage: undefined, featRace: undefined });
                    setDetail(r.slug);
                  }}
                >
                  <div className="cf-card-name">{r.name}</div>
                  <div className="cf-card-sub">
                    {r.creature_type && r.creature_type !== "Humanoid"
                      ? `${r.creature_type} · ` : ""}
                    {r.size} · {r.speed} ft{r.darkvision ? " · darkvision" : ""}
                    {r.lineages?.length ? ` · ${r.lineages.length} lineages` : ""}
                    {r.feat_choice ? " · feat" : ""}
                  </div>
                </button>
              ))}
            </div>

            {race?.lineages?.length ? (
              <>
                <div className="cf-sub-label" style={{ marginTop: 18 }}>
                  {race.lineage_label ?? "Lineage"} — pick your{" "}
                  {race.name.toLowerCase()} heritage
                </div>
                <div className="cf-grid">
                  {race.lineages.map((l) => (
                    <button
                      key={l.slug}
                      className={`cf-card ${d.lineage === l.slug ? "picked" : ""}`}
                      onClick={() => { uiTick(); setD({ ...d, lineage: l.slug }); }}
                    >
                      <div className="cf-card-name">{l.name}</div>
                      <div className="cf-card-sub">
                        {(l.traits[0] ?? "").slice(0, 60)}
                        {(l.traits[0]?.length ?? 0) > 60 ? "…" : ""}
                      </div>
                    </button>
                  ))}
                </div>
              </>
            ) : null}
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
          <AbilityStage opts={opts} d={d} setD={setD} bonuses={bonuses}
                        bg={bg} boostPool={boostPool} canSpread={canSpread} />
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
            {needsBgFeat && (
              <FeatPicker
                title={`Your ${bg?.name ?? "background"} grantS an Origin feat`
                  .replace("grantS", "grants")}
                feats={originFeats} finalStats={finalStats} clsSlug={d.cls}
                chosen={d.featBg}
                onPick={(slug) => setD({ ...d, featBg: slug })} />
            )}
            {raceFeat && (
              <FeatPicker
                title={raceFeat === "any"
                  ? `${race?.name}: choose ANY feat you qualify for`
                  : `${race?.name} grants an Origin feat`}
                feats={raceFeatPool} finalStats={finalStats} clsSlug={d.cls}
                chosen={d.featRace}
                onPick={(slug) => setD({ ...d, featRace: slug })} />
            )}
          </>
        )}

        {stage === "gear" && (
          <GearStage opts={opts} d={d} setD={setD} budget={budget} spent={cartCost} />
        )}

        {stage === "wondrous" && (
          <>
            <div className="cf-sub-label">
              Choose one free <b>common magic item</b> to start with — or none.
            </div>
            <div className="cf-grid">
              {opts.common_items.map((w) => (
                <button
                  key={w.slug}
                  className={`cf-card ${d.wondrous === w.slug ? "picked" : ""}`}
                  onClick={() => {
                    uiTick();
                    setD({ ...d, wondrous: d.wondrous === w.slug ? undefined : w.slug });
                  }}
                >
                  <div className="cf-card-name">{w.name}{w.attunement ? " ✦" : ""}</div>
                  <div className="cf-card-sub">
                    {w.item_type ? `${w.item_type} · ` : ""}{w.brief}…
                  </div>
                </button>
              ))}
            </div>
            {opts.common_items.length === 0 && (
              <p className="cf-hint">No common items are ingested yet — skip onward.</p>
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
              {(d.featBg || d.featRace) && (
                <p className="inv-line"><b>Feats</b> · {
                  [d.featBg, d.featRace].filter(Boolean)
                    .map((s) => opts.feats.find((f) => f.slug === s)?.name)
                    .join(", ")}</p>
              )}
              <p className="inv-line"><b>Gear</b> · {d.gearMode === "buy"
                ? `bought ${Object.keys(d.cart).length} item(s), ${(budget - cartCost).toFixed(0)} gp left`
                : "standard class & background kit"}</p>
              {d.wondrous && (
                <p className="inv-line"><b>Item</b> · {
                  opts.common_items.find((w) => w.slug === d.wondrous)?.name}</p>
              )}
            </div>
            {ccError && <p className="cf-error">⚠ {ccError}</p>}
          </div>
        )}
      </main>

      <aside className="cf-detail">
        <DetailPanel opts={opts} stage={stage} raceSlug={d.race} clsSlug={d.cls}
                     lineageSlug={d.lineage} hovered={detail} />
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

/** A pool of feat cards, prerequisites enforced: feats you don't qualify for
    are greyed out, non-selectable, and show the reason. */
function FeatPicker({ title, feats, finalStats, clsSlug, chosen, onPick }: {
  title: string;
  feats: CCOptions["feats"];
  finalStats: Partial<Record<Ability, number>>;
  clsSlug?: string;
  chosen?: string;
  onPick: (slug: string) => void;
}) {
  return (
    <>
      <div className="cf-sub-label" style={{ marginTop: 18 }}>{title}</div>
      <div className="cf-grid">
        {feats.map((f) => {
          const blocked = featBlockReason(f, finalStats, clsSlug);
          return (
            <button
              key={f.slug}
              className={`cf-card ${chosen === f.slug ? "picked" : ""} ${blocked ? "locked" : ""}`}
              disabled={!!blocked}
              title={blocked ? `Locked — ${blocked}` : undefined}
              onClick={() => { if (!blocked) { uiTick(); onPick(f.slug); } }}
            >
              <div className="cf-card-name">
                {f.name}{blocked ? " 🔒" : ""}
              </div>
              <div className="cf-card-sub">
                {blocked ? blocked : `${f.brief}…`}
              </div>
            </button>
          );
        })}
      </div>
    </>
  );
}

function pointBuySpent(pb: Record<Ability, number>, opts: CCOptions | null): number {
  const costs = opts?.ability_methods.point_buy.costs ?? {};
  return ABILITIES.reduce((n, a) => n + (costs[String(pb[a])] ?? 0), 0);
}

function AbilityStage({ opts, d, setD, bonuses, bg, boostPool, canSpread }: {
  opts: CCOptions;
  d: Draft; setD: (d: Draft) => void;
  bonuses: Partial<Record<Ability, number>>;
  bg?: CCOptions["backgrounds"][number];
  boostPool: Ability[]; canSpread: boolean;
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

  const setBoost2 = (a: Ability) =>
    setD({ ...d, boost2: a, boost1: d.boost1 === a ? undefined : d.boost1 });
  const setBoost1 = (a: Ability) => setD({ ...d, boost1: a });

  return (
    <div>
      {/* 2024 background ability boosts */}
      <div className="cf-subpanel" style={{ marginBottom: 14 }}>
        <div className="cf-sub-label">
          {bg ? bg.name : "Background"} boosts{" "}
          {boostPool.length === 3 ? `(${boostPool.join(", ")})` : "(choose any)"}
        </div>
        <div className="cf-chips" style={{ marginBottom: 8 }}>
          <button className={`cf-chip ${d.boostMode === "two-one" ? "picked" : ""}`}
                  onClick={() => { uiTick(); setD({ ...d, boostMode: "two-one" }); }}>+2 / +1</button>
          {canSpread && (
            <button className={`cf-chip ${d.boostMode === "spread" ? "picked" : ""}`}
                    onClick={() => { uiTick(); setD({ ...d, boostMode: "spread" }); }}>+1 to each</button>
          )}
        </div>
        {d.boostMode === "spread" && canSpread ? (
          <p className="cf-hint">+1 to {boostPool.join(", ")}.</p>
        ) : (
          <>
            <div className="cf-bonus-row">
              <span className="cf-bonus-amt">+2</span>
              {boostPool.map((a) => (
                <button key={a} className={`cf-chip ${d.boost2 === a ? "picked" : ""}`}
                        onClick={() => { uiTick(); setBoost2(a); }}>{a}</button>
              ))}
            </div>
            <div className="cf-bonus-row">
              <span className="cf-bonus-amt">+1</span>
              {boostPool.map((a) => (
                <button key={a} className={`cf-chip ${d.boost1 === a ? "picked" : ""}`}
                        disabled={d.boost2 === a}
                        onClick={() => { uiTick(); setBoost1(a); }}>{a}</button>
              ))}
            </div>
          </>
        )}
      </div>

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
          ? "Spend the budget; your background's boosts apply on top."
          : "Pick a value, then place it in an ability. Click a filled slot to clear it."}
      </p>
    </div>
  );
}

function GearStage({ opts, d, setD, budget, spent }: {
  opts: CCOptions;
  d: Draft; setD: (d: Draft) => void;
  budget: number; spent: number;
}) {
  const [filter, setFilter] = useState("");
  const remaining = budget - spent;
  const setQty = (name: string, qty: number) => {
    const cart = { ...d.cart };
    if (qty <= 0) delete cart[name];
    else cart[name] = qty;
    setD({ ...d, cart });
  };
  const q = filter.trim().toLowerCase();
  const items = opts.buyable_items
    .filter((b) => !q || b.name.toLowerCase().includes(q))
    .slice(0, 80);

  return (
    <div>
      <div className="cf-chips" style={{ marginBottom: 14 }}>
        {(["kit", "buy"] as const).map((m) => (
          <button key={m} className={`cf-chip big ${d.gearMode === m ? "picked" : ""}`}
                  onClick={() => { uiTick(); setD({ ...d, gearMode: m }); }}>
            {m === "kit" ? "Standard kit" : "Buy your own"}
          </button>
        ))}
      </div>

      {d.gearMode === "kit" ? (
        <p className="cf-hint">
          You'll walk out with your class's standard kit and your background's
          gear — ready for the road, no accounting required.
        </p>
      ) : (
        <>
          <div className="gear-budget">
            <span>Purse <b>{budget} gp</b></span>
            <span className={remaining < 0 ? "over" : ""}>
              Remaining <b>{remaining.toFixed(2)} gp</b>
            </span>
          </div>
          <input className="gear-search" placeholder="search gear…"
                 value={filter} onChange={(e) => setFilter(e.target.value)} />
          <div className="gear-list">
            {items.map((b) => {
              const qty = d.cart[b.name] ?? 0;
              const canAdd = spent + b.cost_gp <= budget;
              return (
                <div key={b.slug} className={`gear-row ${qty ? "in" : ""}`}>
                  <span className="gear-name">{b.name}</span>
                  <span className="gear-cost">{b.cost_gp} gp</span>
                  <div className="gear-qty">
                    <button disabled={qty <= 0} onClick={() => { uiTick(); setQty(b.name, qty - 1); }}>−</button>
                    <span>{qty}</span>
                    <button disabled={!canAdd} onClick={() => { uiTick(); setQty(b.name, qty + 1); }}>+</button>
                  </div>
                </div>
              );
            })}
          </div>
          {opts.buyable_items.length > 80 && !q && (
            <p className="cf-hint">Showing 80 of {opts.buyable_items.length} — search to narrow.</p>
          )}
        </>
      )}
    </div>
  );
}

function DetailPanel({ opts, stage, raceSlug, clsSlug, lineageSlug, hovered }: {
  opts: CCOptions; stage: Stage;
  raceSlug?: string; clsSlug?: string; lineageSlug?: string;
  hovered: string | null;
}) {
  if (stage === "race" || hovered) {
    const r = opts.races.find((x) => x.slug === (hovered ?? raceSlug));
    if (r && stage === "race") {
      // Show the picked lineage's traits (only when viewing the selected race).
      const lin = (hovered ?? raceSlug) === raceSlug
        ? r.lineages?.find((l) => l.slug === lineageSlug)
        : undefined;
      return (
        <div className="cf-detail-body">
          <h3>{r.name}{lin ? ` · ${lin.name}` : ""}</h3>
          <p className="cf-detail-meta">
            {(r.creature_type ?? "Humanoid")} · {r.size} · {(lin?.speed ?? r.speed)} ft speed
            {(lin?.darkvision ?? r.darkvision) ? " · darkvision" : ""}
          </p>
          {r.immunities && r.immunities.length > 0 && (
            <p className="cf-detail-meta">Immune to: {r.immunities.join(", ")}</p>
          )}
          {r.languages && <p className="cf-detail-meta">{r.languages}</p>}
          <ul>{r.traits.map((t, i) => <li key={i}>{t}</li>)}</ul>
          {lin && (
            <>
              <p className="cf-detail-meta"><b>{r.lineage_label ?? "Lineage"}: {lin.name}</b></p>
              <ul>{lin.traits.map((t, i) => <li key={`l${i}`}>{t}</li>)}</ul>
            </>
          )}
          {r.lineages?.length && !lin ? (
            <p className="cf-detail-meta" style={{ opacity: 0.7 }}>
              Pick a {(r.lineage_label ?? "lineage").toLowerCase()} below.
            </p>
          ) : null}
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
