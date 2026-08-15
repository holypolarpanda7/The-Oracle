/* Feat choices, resolved interactively — shared by character creation and the
 * level-up overlay so a feat asks the same questions whenever it is taken.
 *
 * The backend hands each feat a `choices` schema (FEAT_CHOICES in the DM
 * brain); everything here just renders that schema and reports whether the
 * player has finished answering it. A feat may ask two questions (Dragonscarred
 * wants an ability AND a damage resistance), which is what `also` is for. */
import type { FeatChoice, FeatPicks, SpellBrief } from "../lib/types";
import { uiTick } from "../lib/sound";

export const ABILITY_CODES = ["str", "dex", "con", "int", "wis", "cha"] as const;
export const ABILITY_LABEL: Record<string, string> = {
  str: "STR", dex: "DEX", con: "CON", int: "INT", wis: "WIS", cha: "CHA",
};

/** The 18 standard skills — the pool for choice-feats like Skilled. */
export const ALL_SKILLS = [
  "Acrobatics", "Animal Handling", "Arcana", "Athletics", "Deception",
  "History", "Insight", "Intimidation", "Investigation", "Medicine", "Nature",
  "Perception", "Performance", "Persuasion", "Religion", "Sleight of Hand",
  "Stealth", "Survival",
];
export const INSTRUMENTS = [
  "Bagpipes", "Drum", "Dulcimer", "Flute", "Lute", "Lyre", "Horn",
  "Pan Flute", "Shawm", "Viol",
];
export const ARTISAN_TOOLS = [
  "Alchemist's Supplies", "Brewer's Supplies", "Calligrapher's Supplies",
  "Carpenter's Tools", "Cartographer's Tools", "Cobbler's Tools",
  "Cook's Utensils", "Glassblower's Tools", "Jeweler's Tools",
  "Leatherworker's Tools", "Mason's Tools", "Painter's Supplies",
  "Potter's Tools", "Smith's Tools", "Tinker's Tools", "Weaver's Tools",
  "Woodcarver's Tools",
];
export const LANGUAGES = [
  "Common", "Dwarvish", "Elvish", "Giant", "Gnomish", "Goblin", "Halfling",
  "Orc", "Abyssal", "Celestial", "Draconic", "Deep Speech", "Infernal",
  "Primordial", "Sylvan", "Undercommon",
];

/** Every question one feat asks — the primary choice plus any `also`, which
 *  may be a single spec or a list (Skill Expert asks three things). */
export function choiceParts(c?: FeatChoice | null): FeatChoice[] {
  if (!c) return [];
  const { also, ...primary } = c;
  const extra = Array.isArray(also) ? also : also ? [also] : [];
  return [primary as FeatChoice, ...extra];
}

/** The option list a non-spell feat-choice draws from. */
export function choiceOptions(c: FeatChoice): string[] {
  if (c.kind === "tools") {
    if (Array.isArray(c.from)) return c.from;
    if (c.from === "instrument") return INSTRUMENTS;
    if (c.from === "artisan") return ARTISAN_TOOLS;
    return [...INSTRUMENTS, ...ARTISAN_TOOLS];
  }
  // A species' language pick arrives with its pool already narrowed (a
  // dwarf is not offered Dwarvish again) — the server owns that filter.
  if (c.kind === "language") return Array.isArray(c.from) ? c.from : LANGUAGES;
  if (c.kind === "ability") {
    return Array.isArray(c.from) ? c.from : (ABILITY_CODES as readonly string[]).slice();
  }
  if (c.kind === "options") return Array.isArray(c.from) ? c.from : [];
  return Array.isArray(c.from) ? c.from : ALL_SKILLS;   // skills
}

/** Which slot of FeatPicks a choice kind writes into. */
function bucketOf(kind: FeatChoice["kind"]): keyof FeatPicks | null {
  switch (kind) {
    case "skills": return "skills";
    case "tools": return "tools";
    case "language": return "languages";
    case "options": return "options";
    default: return null;
  }
}

/** Is this question being asked at all? A `when` question hangs off the option
 *  chosen above it — Custom Lineage's extra skill exists only if you took the
 *  skill half of its gift, and a conditional question that is always shown is
 *  a choice the rules never offered. */
export function partActive(part: FeatChoice, picks: FeatPicks): boolean {
  return !part.when || (picks.options ?? []).includes(part.when);
}

/** Has one question of a feat been answered? Exported because creation asks
 *  its questions across two stages (proficiencies on Skills, spells on
 *  Spells) and has to judge them a part at a time. */
export function partSatisfied(part: FeatChoice, picks: FeatPicks): boolean {
  if (!partActive(part, picks)) return true;   // not asked → nothing owed
  const n = part.n ?? 1;
  if (part.kind === "ability") return !!picks.ability;
  if (part.kind === "asi") {
    const total = Object.values(picks.ability_increases ?? {})
      .reduce((a, b) => a + b, 0);
    return total === (part.total ?? 2);
  }
  if (part.kind === "magic_initiate") {
    return (picks.cantrips?.length ?? 0) === (part.cantrips ?? 2)
      && (picks.spells?.length ?? 0) === (part.spells ?? 1);
  }
  // A school-scoped pick; n = 0 is a pure grant, so nothing to answer.
  if (part.kind === "spells") return (picks.spells?.length ?? 0) === n;
  const bucket = bucketOf(part.kind);
  return !bucket || (picks[bucket] as string[] | undefined)?.length === n;
}

/** Has the player answered every question this feat asks? */
export function featChoicesSatisfied(c: FeatChoice | null | undefined,
                                     picks: FeatPicks): boolean {
  return choiceParts(c).every((part) => partSatisfied(part, picks));
}

/** A "choose N of these" chip row. */
export function ChoiceChips({ label, options, chosen, n, single, onToggle }: {
  label: string; options: string[]; chosen: string[]; n: number;
  single?: boolean; onToggle: (v: string) => void;
}) {
  const left = n - chosen.length;
  return (
    <div style={{ marginTop: 14 }}>
      <div className="cf-sub-label">
        {label}{left > 0 ? <span className="cf-req"> · {left} left</span> : null}
      </div>
      <div className="cf-chips">
        {options.map((o) => {
          const on = chosen.includes(o);
          return (
            <button
              key={o}
              className={`cf-chip ${on ? "picked" : ""}`}
              disabled={!on && !single && chosen.length >= n}
              onClick={() => { uiTick(); onToggle(o); }}
            >{o}</button>
          );
        })}
      </div>
    </div>
  );
}

/** Every question a feat asks, rendered as chip rows. Spell picks (Magic
 *  Initiate) need a spell list the caller owns, so they're passed in. */
export function FeatChoiceFields({ choice, picks, onChange, spellPicker }: {
  choice?: FeatChoice | null;
  picks: FeatPicks;
  onChange: (next: FeatPicks) => void;
  /** Renders the Magic Initiate cantrip/spell pickers, if the caller has a list. */
  spellPicker?: (c: FeatChoice) => React.ReactNode;
}) {
  const parts = choiceParts(choice);
  if (!parts.length) return null;

  const toggleList = (bucket: keyof FeatPicks, n: number) => (v: string) => {
    const cur = ((picks[bucket] as string[] | undefined) ?? []);
    // A choose-ONE row REPLACES: greying out the other half of an either/or
    // (Custom Lineage's gift) means changing your mind takes two taps, and the
    // second one is on a chip that looks disabled.
    const next = cur.includes(v) ? cur.filter((x) => x !== v)
      : cur.length < n ? [...cur, v]
      : n === 1 ? [v] : cur;
    onChange({ ...picks, [bucket]: next });
  };

  return (
    <>
      {parts.map((c, i) => {
        if (!partActive(c, picks)) return null;
        if (c.kind === "magic_initiate" || c.kind === "spells") {
          return <div key={i}>{spellPicker?.(c) ?? null}</div>;
        }
        if (c.kind === "asi") {
          return <AsiSpread key={i} choice={c} picks={picks} onChange={onChange} />;
        }
        if (c.kind === "ability") {
          const opts = choiceOptions(c);
          return (
            <ChoiceChips
              key={i} single
              label={c.hint || "Choose an ability"}
              options={opts.map((o) => ABILITY_LABEL[o] ?? o.toUpperCase())}
              chosen={picks.ability ? [ABILITY_LABEL[picks.ability] ?? picks.ability.toUpperCase()] : []}
              n={1}
              onToggle={(label) => {
                const code = opts.find(
                  (o) => (ABILITY_LABEL[o] ?? o.toUpperCase()) === label) ?? label;
                onChange({ ...picks, ability: picks.ability === code ? undefined : code });
              }} />
          );
        }
        const bucket = bucketOf(c.kind);
        if (!bucket) return null;
        const n = c.n ?? 1;
        return (
          <ChoiceChips
            key={i} single={n === 1}
            label={c.hint || `Choose ${n}`}
            options={choiceOptions(c)}
            chosen={(picks[bucket] as string[] | undefined) ?? []}
            n={n}
            onToggle={toggleList(bucket, n)} />
        );
      })}
    </>
  );
}

/** The +2 / +1+1 spread of an Ability Score Improvement. Each ability is a
 *  stepper capped by its own ceiling, and the row locks once the points are
 *  spent — so an illegal spread can't be submitted in the first place. */
export function AsiSpread({ choice, picks, onChange, scores }: {
  choice: FeatChoice;
  picks: FeatPicks;
  onChange: (next: FeatPicks) => void;
  /** Current scores, keyed by 3-letter code — shown, and used for the cap. */
  scores?: Record<string, number>;
}) {
  const total = choice.total ?? 2;
  const cap = choice.max ?? 20;
  const inc = picks.ability_increases ?? {};
  const spent = Object.values(inc).reduce((a, b) => a + b, 0);
  const left = total - spent;

  const bump = (code: string, delta: number) => {
    const cur = inc[code] ?? 0;
    const next = cur + delta;
    if (next < 0 || next > 2 || (delta > 0 && left <= 0)) return;
    const out = { ...inc };
    if (next === 0) delete out[code]; else out[code] = next;
    uiTick();
    onChange({ ...picks, ability_increases: out });
  };

  return (
    <div style={{ marginTop: 14 }}>
      <div className="cf-sub-label">
        {choice.hint || "Ability Score Improvement"}
        {left > 0 ? <span className="cf-req"> · {left} point{left > 1 ? "s" : ""} left</span> : null}
      </div>
      <div className="asi-row">
        {(ABILITY_CODES as readonly string[]).map((code) => {
          const base = scores?.[code];
          const add = inc[code] ?? 0;
          const atCap = base !== undefined && base + add >= cap;
          return (
            <div key={code} className={`asi-cell ${add ? "on" : ""}`}>
              <div className="asi-name">{ABILITY_LABEL[code]}</div>
              <div className="asi-score">
                {base !== undefined ? base + add : add ? `+${add}` : "—"}
                {base !== undefined && add > 0 && <span className="asi-delta"> +{add}</span>}
              </div>
              <div className="asi-steps">
                <button disabled={add <= 0} onClick={() => bump(code, -1)}>−</button>
                <button disabled={left <= 0 || add >= 2 || atCap}
                        onClick={() => bump(code, +1)}>+</button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
