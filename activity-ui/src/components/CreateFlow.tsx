import { useEffect, useMemo, useRef, useState } from "react";
import type { CCOptions, CCPayload, CCSpells, Pantheon, Power, SpellBrief } from "../lib/types";
import { uiTick } from "../lib/sound";
import { speciesPortraitFor } from "../lib/assets";
import { ChoiceChips, choiceOptions } from "./FeatChoices";

/** Male+female species portraits for a race/lineage card. Each image walks a
 * candidate list (lineage art → base species art) and hides itself only when
 * every candidate 404s, so the strip degrades gracefully when art is absent. */
function SpeciesPortrait({ slug, lineageSlug, large }: {
  slug: string; lineageSlug?: string; large?: boolean;
}) {
  const p = speciesPortraitFor(slug, lineageSlug);
  const [mi, setMi] = useState(0);
  const [fi, setFi] = useState(0);
  useEffect(() => { setMi(0); setFi(0); }, [slug, lineageSlug]);
  const mDone = mi >= p.m.length;
  const fDone = fi >= p.f.length;
  if (mDone && fDone) return null;
  return (
    <div className={`cf-portrait${large ? " cf-portrait-lg" : ""}`}>
      {!mDone && <img key={p.m[mi]} src={p.m[mi]} alt="" loading="lazy"
                      onError={() => setMi((i) => i + 1)} />}
      {!fDone && <img key={p.f[fi]} src={p.f[fi]} alt="" loading="lazy"
                      onError={() => setFi((i) => i + 1)} />}
    </div>
  );
}

/** How mortals deal with a family of powers — shown so a player knows what
 *  naming one actually means. */
const WORSHIP_WORD: Record<string, string> = {
  temples: "prayed to in temples",
  cults: "revered by cults",
  pacts: "bargained with, not worshipped",
  allies: "served as an ally, not a god",
};

/** Choose a patron from the world's LIVING powers — the seeded canon plus
 *  anything that has risen since — with free text as the escape hatch for a
 *  local saint or a power the world has yet to name. */
function DeityPicker({ pantheon, value, onPick }: {
  pantheon: Pantheon;
  value: string;
  onPick: (name: string) => void;
}) {
  const [family, setFamily] = useState<string>("");
  const [q, setQ] = useState("");
  const query = q.trim().toLowerCase();

  const shown = pantheon.powers.filter((p) => {
    if (family && p.family !== family) return false;
    if (!query) return true;
    return [p.name, p.title, p.domains, p.alignment, p.family_label]
      .some((s) => (s ?? "").toLowerCase().includes(query));
  });
  // A patron that isn't one of the world's powers (typed by hand, or carried in
  // from an import) still has to show as chosen.
  const known = pantheon.powers.some((p) => p.name === value);
  const custom = !!value && !known;

  return (
    <div className="cf-deity">
      <div className="cf-chips" style={{ marginBottom: 8 }}>
        <button className={`cf-chip ${family === "" ? "picked" : ""}`}
                onClick={() => { uiTick(); setFamily(""); }}>All powers</button>
        {pantheon.families.map((f) => (
          <button key={f.key} className={`cf-chip ${family === f.key ? "picked" : ""}`}
                  onClick={() => { uiTick(); setFamily(f.key); }}
                  title={f.blurb}>{f.label} ({f.count})</button>
        ))}
        <input className="cf-input" style={{ maxWidth: 190 }} value={q}
               placeholder="search a name or domain…"
               onChange={(e) => setQ(e.target.value)} />
      </div>

      {family && (
        <p className="cf-deity-note">
          {pantheon.families.find((f) => f.key === family)?.blurb}
          {" — "}
          {WORSHIP_WORD[pantheon.families.find((f) => f.key === family)?.worship ?? ""]
            ?? "known to mortals"}.
        </p>
      )}

      <div className="cf-deity-list">
        <button className={`cf-deity-card ${!value ? "picked" : ""}`}
                onClick={() => { uiTick(); onPick(""); }}>
          <div className="cf-card-name">No patron</div>
          <div className="cf-card-sub">You owe nothing to any power.</div>
        </button>
        {shown.map((p, i) => (
          // Index-qualified: two powers should never share a slug, but a
          // duplicate key silently strands a stale card in a filtered list.
          <button key={`${p.slug ?? p.name}-${i}`}
                  className={`cf-deity-card ${value === p.name ? "picked" : ""}`}
                  onClick={() => { uiTick(); onPick(p.name); }}>
            <div className="cf-card-name">
              {p.name}{p.title ? ` ${p.title}` : ""}
              {p.risen && <span className="cf-risen">risen in this age</span>}
            </div>
            <div className="cf-card-sub">{p.domains}</div>
            <div className="cf-deity-meta">
              {p.alignment}
              {p.family_label ? ` · ${p.family_label}` : ""}
            </div>
          </button>
        ))}
        {shown.length === 0 && (
          <p className="cf-deity-note">No power here answers to that.</p>
        )}
      </div>

      <label className="cf-sub-label" style={{ marginTop: 10 }}>
        Or name someone else — a local saint, an unnamed thing in a barrow
      </label>
      <input className="cf-input" value={custom ? value : ""}
             placeholder="only if none of the above fit…"
             onChange={(e) => onPick(e.target.value)} />
    </div>
  );
}

const ABILITIES = ["STR", "DEX", "CON", "INT", "WIS", "CHA"] as const;
type Ability = (typeof ABILITIES)[number];
const ABILITY_FULL: Record<Ability, string> = {
  STR: "strength", DEX: "dexterity", CON: "constitution",
  INT: "intelligence", WIS: "wisdom", CHA: "charisma",
};

type Stage = "race" | "class" | "background" | "abilities" | "skills"
  | "spells" | "gear" | "wondrous" | "review";
const STAGES: { id: Stage; label: string }[] = [
  { id: "race", label: "Origin" },
  { id: "class", label: "Class" },
  { id: "background", label: "Background" },
  { id: "abilities", label: "Abilities" },
  { id: "skills", label: "Skills" },
  { id: "spells", label: "Spells" },   // shown only for spellcasters / Magic Initiate
  { id: "gear", label: "Gear" },
  { id: "wondrous", label: "Wonder" },
  { id: "review", label: "Name & Seal" },
];
const NUMERALS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX"];

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
  featSkills: string[];   // skills granted by a chosen feat (e.g. Skilled)
  featTools: string[];    // tools granted by a feat (Musician/Crafter)
  featLanguages: string[];// languages granted by a feat
  featAbility?: string;   // ability chosen by a feat (3-letter code)
  featOptions: string[];  // named feat picks (a damage resistance, a giant strike)
  cantrips: string[];     // class cantrip slugs
  spells: string[];       // class level-1 spell slugs
  miClass?: string;       // Magic Initiate: chosen class list
  miCantrips: string[];   // Magic Initiate cantrip slugs
  miSpells: string[];     // Magic Initiate level-1 spell slug
  gearMode: "kit" | "buy";
  cart: Record<string, number>;   // buyable item name -> quantity
  wondrous?: string;              // rules_item slug
  deity?: string;                 // patron god (esp. clerics/paladins/warlocks)
  gender?: string;                // gender identity (free-form)
  name: string;
}

const freshDraft = (): Draft => ({
  boostMode: "two-one", method: "standard_array", pool: [], assigned: {},
  pointBuy: { STR: 8, DEX: 8, CON: 8, INT: 8, WIS: 8, CHA: 8 },
  skills: [], featSkills: [], featTools: [], featLanguages: [], featOptions: [],
  cantrips: [], spells: [], miCantrips: [], miSpells: [],
  gearMode: "kit", cart: {}, name: "",
});

const CASTER_CLASSES = new Set([
  "bard", "cleric", "druid", "paladin", "ranger", "sorcerer", "warlock",
  "wizard", "artificer",
]);

// Skill / tool / language pools and the chip row live in FeatChoices so the
// level-up overlay resolves a feat's choices exactly the way creation does.
type Choice = NonNullable<CCOptions["feats"][number]["choices"]>;

/** A "choose N" chip picker (skills / tools / languages / an ability). */
/** A "choose N spells" grid (cantrips or level-1). Cards toggle; the grid locks
 *  once N are picked. Shared by class spellcasting and Magic Initiate. */
function SpellPicker({ title, list, chosen, n, onToggle }: {
  title: string; list: SpellBrief[]; chosen: string[]; n: number;
  onToggle: (slug: string) => void;
}) {
  const left = n - chosen.length;
  return (
    <div style={{ marginBottom: 14 }}>
      <div className="cf-sub-label">
        {title}{left > 0 ? <span className="cf-req"> · {left} left</span> : null}
      </div>
      {list.length === 0
        ? <p className="cf-hint">No spells available — check the rules import.</p>
        : (
          <div className="cf-grid">
            {list.map((sp) => {
              const on = chosen.includes(sp.slug);
              return (
                <button
                  key={sp.slug}
                  className={`cf-card ${on ? "picked" : ""}`}
                  disabled={!on && chosen.length >= n}
                  onClick={() => { uiTick(); onToggle(sp.slug); }}
                >
                  <div className="cf-card-name">{sp.name}</div>
                  <div className="cf-card-sub">
                    {[sp.school, sp.concentration ? "conc." : null,
                      sp.ritual ? "ritual" : null].filter(Boolean).join(" · ")}
                    {sp.brief ? ` — ${sp.brief}` : ""}
                  </div>
                </button>
              );
            })}
          </div>
        )}
    </div>
  );
}

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
  // Spell lists (fetched lazily): the class's own list, and — for Magic
  // Initiate — the feat's chosen-class list. Keyed by slug so we don't refetch.
  const [spellData, setSpellData] = useState<CCSpells | null>(null);
  const [miData, setMiData] = useState<CCSpells | null>(null);
  // Bring the racial-features + lineage panel into view when a species is
  // picked — on a phone it sits below the card grid and is easy to miss.
  const raceDetailRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/cc/options").then((r) => r.json()).then(setOpts)
      .catch(() => setOpts(null));
  }, []);

  // Fetch the class spell list when a caster class is (re)chosen.
  useEffect(() => {
    if (!d.cls) { setSpellData(null); return; }
    let live = true;
    fetch(`/cc/spells/${d.cls}`).then((r) => r.json())
      .then((j: CCSpells) => { if (live) setSpellData(j.caster ? j : null); })
      .catch(() => { if (live) setSpellData(null); });
    return () => { live = false; };
  }, [d.cls]);

  // Fetch the Magic Initiate class list when its class is chosen.
  useEffect(() => {
    if (!d.miClass) { setMiData(null); return; }
    let live = true;
    fetch(`/cc/spells/${d.miClass}`).then((r) => r.json())
      .then((j: CCSpells) => { if (live) setMiData(j); })
      .catch(() => { if (live) setMiData(null); });
    return () => { live = false; };
  }, [d.miClass]);

  useEffect(() => {
    if (d.race && raceDetailRef.current) {
      raceDetailRef.current.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [d.race]);

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

  // ----- feat & spell choices -----
  // Choices carried by the chosen origin feats (Skilled → skills, Magic
  // Initiate → a class + cantrips + a spell).
  const chosenFeats = [d.featBg, d.featRace].filter(Boolean) as string[];
  // A feat may ask two things (Dragonscarred wants an ability AND a damage
  // resistance), so flatten `also` in alongside the primary choice.
  const featChoices = chosenFeats
    .flatMap((slug) => {
      const c = opts?.feats.find((f) => f.slug === slug)?.choices;
      if (!c) return [];
      const { also, ...primary } = c;
      return also ? [primary as Choice, also as Choice] : [primary as Choice];
    });
  const skilledChoice = featChoices.find((c) => c.kind === "skills");
  const toolsChoice = featChoices.find((c) => c.kind === "tools");
  const abilityChoice = featChoices.find((c) => c.kind === "ability");
  const languageChoice = featChoices.find((c) => c.kind === "language");
  const optionsChoice = featChoices.find((c) => c.kind === "options");
  const miChoice = featChoices.find((c) => c.kind === "magic_initiate");
  const featSkillsDone = !skilledChoice || d.featSkills.length === (skilledChoice.n ?? 3);
  const featToolsDone = !toolsChoice || d.featTools.length === (toolsChoice.n ?? 1);
  const featLangDone = !languageChoice || d.featLanguages.length === (languageChoice.n ?? 1);
  const featAbilityDone = !abilityChoice || !!d.featAbility;
  const featOptionsDone = !optionsChoice || d.featOptions.length === (optionsChoice.n ?? 1);
  const featChoicesDone = featSkillsDone && featToolsDone && featLangDone
    && featAbilityDone && featOptionsDone;

  // The Spells stage appears when the class casts OR Magic Initiate was taken.
  const needsSpells = !!spellData || !!miChoice;
  const classCantripsDone = !spellData || d.cantrips.length === spellData.cantrips_n;
  const classSpellsDone = !spellData || d.spells.length === spellData.spells_n;
  const miDone = !miChoice
    || (!!d.miClass && d.miCantrips.length === (miChoice.cantrips ?? 2)
        && d.miSpells.length === (miChoice.spells ?? 1));
  const spellsDone = classCantripsDone && classSpellsDone && miDone;

  const stageDone: Record<Stage, boolean> = {
    race: !!d.race && (!(race?.lineages?.length) || !!d.lineage),
    class: !!d.cls,
    background: !!d.background,
    abilities: abilitiesDone,
    skills: d.skills.length === skillsNeeded
      && (!needsBgFeat || !!d.featBg)
      && (!raceFeat || !!d.featRace)
      && featChoicesDone,
    spells: !needsSpells || spellsDone,
    gear: d.gearMode === "kit" || cartCost <= budget,  // buy is fine even empty
    wondrous: true,                                     // optional — always ok
    review: d.name.trim().length >= 2,
  };
  // The Spells stage is hidden for non-casters without Magic Initiate.
  const visibleStages = STAGES.filter((s) => s.id !== "spells" || needsSpells);
  const visIdx = visibleStages.findIndex((s) => s.id === stage);
  const canNext = stageDone[stage];

  const next = () => {
    uiTick();
    if (stage === "review") {
      const stats: Record<string, number> = {};
      for (const a of ABILITIES) stats[ABILITY_FULL[a]] = finalScore(a) ?? 10;
      // A feat that grants an ability increase folds into the final stats.
      if (abilityChoice && d.featAbility && (abilityChoice.amount ?? 0) > 0) {
        const full = ABILITY_FULL[d.featAbility as Ability];
        if (full) stats[full] = (stats[full] ?? 10) + (abilityChoice.amount ?? 0);
      }
      const feats = [d.featBg, d.featRace].filter(Boolean) as string[];
      const lineageName = race?.lineages?.find((l) => l.slug === d.lineage)?.name;
      const allCantrips = [...d.cantrips, ...d.miCantrips];
      const allSpells = [...d.spells, ...d.miSpells];
      onDone({
        name: d.name.trim(),
        race: lineageName ? `${race!.name} (${lineageName})` : race!.name,
        char_class: cls!.name, background: bg!.slug,
        deity: d.deity?.trim() || undefined,
        gender: d.gender?.trim() || undefined,
        // Feat-granted skills (Skilled) fold into the skill list; tools/languages
        // ride their own fields.
        stats, skills: [...d.skills, ...d.featSkills],
        tools: d.featTools.length ? d.featTools : undefined,
        languages: d.featLanguages.length ? d.featLanguages : undefined,
        feat_options: d.featOptions.length ? d.featOptions : undefined,
        feats: feats.length ? feats : undefined,
        cantrips: allCantrips.length ? allCantrips : undefined,
        spells: allSpells.length ? allSpells : undefined,
        gear_mode: d.gearMode,
        bought_items: d.gearMode === "buy"
          ? Object.entries(d.cart).map(([name, quantity]) => ({ name, quantity }))
          : undefined,
        wondrous_item: d.wondrous,
      });
      return;
    }
    setStage(visibleStages[visIdx + 1].id);
  };

  if (!opts) {
    return <div className="create"><div className="cf-loading">consulting the ledgers…</div></div>;
  }

  return (
    <div className="create">
      <nav className="cf-stages">
        {visibleStages.map((s, i) => (
          <button
            key={s.id}
            className={`cf-stage ${stage === s.id ? "on" : ""} ${stageDone[s.id] ? "done" : ""}`}
            disabled={i > 0 && !visibleStages.slice(0, i).every((p) => stageDone[p.id])}
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
                  <SpeciesPortrait slug={r.slug} />
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

            {race && (
              <div className="cf-race-detail" ref={raceDetailRef}>
                {/* Full racial features. Shown inline on phones, where the side
                    description column is stacked far below the card grid. */}
                <div className="cf-inline-detail">
                  <div className="cf-sub-label" style={{ marginTop: 18 }}>
                    {race.name} — racial features
                  </div>
                  <SpeciesPortrait slug={race.slug} lineageSlug={d.lineage} large />
                  <p className="cf-detail-meta">
                    {(race.creature_type ?? "Humanoid")} · {race.size} · {race.speed} ft
                    {race.darkvision ? " · darkvision" : ""}
                  </p>
                  {race.languages && <p className="cf-detail-meta">{race.languages}</p>}
                  <ul className="cf-trait-list">
                    {race.traits.map((t, i) => <li key={i}>{t}</li>)}
                  </ul>
                </div>

                {race.lineages?.length ? (
                  <>
                    <div className="cf-sub-label" style={{ marginTop: 18 }}>
                      {race.lineage_label ?? "Lineage"} — choose your{" "}
                      {race.name.toLowerCase()} heritage
                      {!d.lineage && <span className="cf-req"> · required</span>}
                    </div>
                    <div className="cf-grid">
                      {race.lineages.map((l) => (
                        <button
                          key={l.slug}
                          className={`cf-card ${d.lineage === l.slug ? "picked" : ""}`}
                          onClick={() => { uiTick(); setD({ ...d, lineage: l.slug }); }}
                        >
                          <SpeciesPortrait slug={race.slug} lineageSlug={l.slug} />
                          <div className="cf-card-name">{l.name}</div>
                          <div className="cf-card-sub">
                            {l.traits.length ? l.traits.join(" · ") : "—"}
                          </div>
                        </button>
                      ))}
                    </div>
                  </>
                ) : null}
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
                onClick={() => { uiTick();
                  // Changing class invalidates class skills + spell picks.
                  setD({ ...d, cls: c.slug, skills: [], cantrips: [], spells: [] });
                  setDetail(c.slug); }}
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
            <div className="cf-faith">
              <label className="cf-sub-label">Gender</label>
              <div className="cf-chips" style={{ marginBottom: 10 }}>
                {["Male", "Female", "Nonbinary"].map((g) => (
                  <button
                    key={g}
                    className={`cf-chip ${d.gender === g ? "picked" : ""}`}
                    onClick={() => { uiTick(); setD({ ...d, gender: g }); }}
                  >{g}</button>
                ))}
                <input
                  className="cf-input"
                  style={{ maxWidth: 180 }}
                  value={["Male", "Female", "Nonbinary"].includes(d.gender ?? "") ? "" : (d.gender ?? "")}
                  placeholder="or type your own…"
                  onChange={(e) => setD({ ...d, gender: e.target.value })}
                />
              </div>
              <label className="cf-sub-label">
                Patron deity{/cleric|paladin|warlock|druid/i.test(cls?.name ?? "")
                  ? " — your class draws its power from one"
                  : " (optional)"}
              </label>
              {opts.deities && opts.deities.powers.length > 0 ? (
                <DeityPicker
                  pantheon={opts.deities}
                  value={d.deity ?? ""}
                  onPick={(name) => setD({ ...d, deity: name })}
                />
              ) : (
                <input
                  className="cf-input"
                  value={d.deity ?? ""}
                  placeholder="e.g. Serath the Dawnmother — or leave blank"
                  onChange={(e) => setD({ ...d, deity: e.target.value })}
                />
              )}
            </div>
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
                onPick={(slug) => setD({ ...d, featBg: slug,
                  featSkills: [], featTools: [], featLanguages: [], featAbility: undefined, featOptions: [], miClass: undefined, miCantrips: [], miSpells: [] })} />
            )}
            {raceFeat && (
              <FeatPicker
                title={raceFeat === "any"
                  ? `${race?.name}: choose ANY feat you qualify for`
                  : `${race?.name} grants an Origin feat`}
                feats={raceFeatPool} finalStats={finalStats} clsSlug={d.cls}
                chosen={d.featRace}
                onPick={(slug) => setD({ ...d, featRace: slug,
                  featSkills: [], featTools: [], featLanguages: [], featAbility: undefined, featOptions: [], miClass: undefined, miCantrips: [], miSpells: [] })} />
            )}
            {skilledChoice && (
              <ChoiceChips
                label={skilledChoice.hint || `Your feat grants ${skilledChoice.n ?? 3} skills`}
                options={choiceOptions(skilledChoice)} chosen={d.featSkills}
                n={skilledChoice.n ?? 3}
                onToggle={(v) => setD({ ...d, featSkills: d.featSkills.includes(v)
                  ? d.featSkills.filter((x) => x !== v) : [...d.featSkills, v] })} />
            )}
            {toolsChoice && (
              <ChoiceChips
                label={toolsChoice.hint || `Choose ${toolsChoice.n ?? 1} tools`}
                options={choiceOptions(toolsChoice)} chosen={d.featTools}
                n={toolsChoice.n ?? 1}
                onToggle={(v) => setD({ ...d, featTools: d.featTools.includes(v)
                  ? d.featTools.filter((x) => x !== v) : [...d.featTools, v] })} />
            )}
            {languageChoice && (
              <ChoiceChips
                label={languageChoice.hint || `Choose ${languageChoice.n ?? 1} languages`}
                options={choiceOptions(languageChoice)} chosen={d.featLanguages}
                n={languageChoice.n ?? 1}
                onToggle={(v) => setD({ ...d, featLanguages: d.featLanguages.includes(v)
                  ? d.featLanguages.filter((x) => x !== v) : [...d.featLanguages, v] })} />
            )}
            {abilityChoice && (
              <ChoiceChips single
                label={abilityChoice.hint || "Choose an ability"}
                options={choiceOptions(abilityChoice)}
                chosen={d.featAbility ? [d.featAbility] : []} n={1}
                onToggle={(v) => setD({ ...d,
                  featAbility: d.featAbility === v ? undefined : v })} />
            )}
            {optionsChoice && (
              <ChoiceChips
                label={optionsChoice.hint || `Choose ${optionsChoice.n ?? 1}`}
                options={choiceOptions(optionsChoice)} chosen={d.featOptions}
                n={optionsChoice.n ?? 1}
                onToggle={(v) => setD({ ...d, featOptions: d.featOptions.includes(v)
                  ? d.featOptions.filter((x) => x !== v) : [...d.featOptions, v] })} />
            )}
          </>
        )}

        {stage === "spells" && (
          <>
            {spellData && (
              <>
                <div className="cf-sub-label">
                  {cls?.name} spellcasting — {spellData.ability} · {spellData.mode}
                </div>
                {spellData.cantrips_n > 0 && (
                  <SpellPicker
                    title={`Cantrips (choose ${spellData.cantrips_n})`}
                    list={spellData.cantrips} chosen={d.cantrips}
                    n={spellData.cantrips_n}
                    onToggle={(slug) => setD({ ...d, cantrips: d.cantrips.includes(slug)
                      ? d.cantrips.filter((x) => x !== slug) : [...d.cantrips, slug] })} />
                )}
                <SpellPicker
                  title={`${spellData.mode === "spellbook" ? "Spellbook"
                    : "1st-level spells"} (choose ${spellData.spells_n})`}
                  list={spellData.spells} chosen={d.spells} n={spellData.spells_n}
                  onToggle={(slug) => setD({ ...d, spells: d.spells.includes(slug)
                    ? d.spells.filter((x) => x !== slug) : [...d.spells, slug] })} />
              </>
            )}
            {miChoice && (
              <div style={{ marginTop: spellData ? 20 : 0 }}>
                <div className="cf-sub-label">Magic Initiate — choose a spell class</div>
                <div className="cf-chips" style={{ marginBottom: 12 }}>
                  {(miChoice.classes ?? []).map((c) => (
                    <button
                      key={c}
                      className={`cf-chip big ${d.miClass === c ? "picked" : ""}`}
                      onClick={() => { uiTick();
                        setD({ ...d, miClass: c, miCantrips: [], miSpells: [] }); }}
                    >{c[0].toUpperCase() + c.slice(1)}</button>
                  ))}
                </div>
                {d.miClass && miData && (
                  <>
                    <SpellPicker
                      title={`${d.miClass} cantrips (choose ${miChoice.cantrips ?? 2})`}
                      list={miData.cantrips} chosen={d.miCantrips}
                      n={miChoice.cantrips ?? 2}
                      onToggle={(slug) => setD({ ...d, miCantrips: d.miCantrips.includes(slug)
                        ? d.miCantrips.filter((x) => x !== slug) : [...d.miCantrips, slug] })} />
                    <SpellPicker
                      title={`1st-level spell (choose ${miChoice.spells ?? 1})`}
                      list={miData.spells} chosen={d.miSpells} n={miChoice.spells ?? 1}
                      onToggle={(slug) => setD({ ...d, miSpells: d.miSpells.includes(slug)
                        ? d.miSpells.filter((x) => x !== slug) : [...d.miSpells, slug] })} />
                  </>
                )}
              </div>
            )}
          </>
        )}

        {stage === "gear" && (
          <GearStage opts={opts} d={d} setD={setD} budget={budget} spent={cartCost} />
        )}

        {stage === "wondrous" && (
          <>
            <div className="cf-sub-label">
              Choose one free <b>common wondrous item</b> to start with — or none.
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
              <p className="cf-hint">No common wondrous items are ingested yet — skip onward.</p>
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

      {/* On the race stage the inline panel carries the same traits, so the
          narrow layout (where both would stack) hides this one — see
          `.cf-detail.race-dup` in the phone media block. */}
      <aside className={`cf-detail ${stage === "race" ? "race-dup" : ""}`}>
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
          <SpeciesPortrait slug={r.slug} lineageSlug={lin ? lineageSlug : undefined} large />
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
