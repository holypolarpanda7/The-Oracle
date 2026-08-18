import { useEffect, useMemo, useRef, useState } from "react";
import type { CCOptions, CCOrigins, CCPayload, CCSpells, CCThreadAnswer,
              CCThreadKind, FeatChoice, FeatPicks,
  FeatSpells, Pantheon, Power, SpellBrief } from "../lib/types";
import { uiTick } from "../lib/sound";
import { speciesPortraitFor } from "../lib/assets";
import { choiceParts, FeatChoiceFields, partActive, partSatisfied, SpellEntry,
  spellBucket } from "./FeatChoices";
import { PortraitStep } from "./PortraitStep";

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
              <span className={p.script ? `script-${p.script}` : undefined}>
                {p.name}{p.title ? ` ${p.title}` : ""}
              </span>
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
  | "spells" | "gear" | "wondrous" | "review" | "portrait";
const STAGES: { id: Stage; label: string }[] = [
  { id: "race", label: "Origin" },
  { id: "class", label: "Class" },
  { id: "background", label: "Background" },
  { id: "abilities", label: "Abilities" },
  { id: "skills", label: "Skills" },
  { id: "spells", label: "Spells" },   // shown only for spellcasters / Magic Initiate
  { id: "gear", label: "Gear" },
  { id: "wondrous", label: "Wonder" },
  // The face is chosen BEFORE the seal. It used to come after, which made it
  // read as a screen bolted on to the end — a character could be sealed with
  // no likeness at all and nothing said so. There is no character row to draw
  // against yet, so the picture is rendered against the wizard's own DRAFT
  // token and adopted by the server when the character is sealed.
  { id: "portrait", label: "Likeness" },
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
  /** Each feat's own answers, keyed by feat slug. A character takes TWO feats
   *  at creation and they ask independently — a background feat wanting a
   *  skill and a Custom Lineage feat wanting a skill are two picks, not one,
   *  and a single flat bucket silently merged them into one. */
  featPicks: Record<string, FeatPicks>;
  /** What the SPECIES asked for — a human's Skillful skill, the languages a
   *  "plus two of your choice" line grants, a Custom Lineage's gift. One
   *  bucket, because a character has exactly one species. */
  speciesPicks: FeatPicks;
  cantrips: string[];     // class cantrip slugs
  spells: string[];       // class level-1 spell slugs
  miClass?: string;       // Magic Initiate: chosen class list
  miCantrips: string[];   // Magic Initiate cantrip slugs
  miSpells: string[];     // Magic Initiate level-1 spell slug
  gearMode: "kit" | "buy";
  cart: Record<string, number>;   // buyable item name -> quantity
  wondrous?: string;              // rules_item slug
  /** The player's own name for that keepsake, and their own words for it —
   *  which is what gets it DRAWN, for this character alone. */
  wondrousName?: string;
  wondrousDesc?: string;
  /** Where they come from: their own words, plus the world ties those words
   *  imply. A tie is a real place or faction — one the world already has, or
   *  one this character brings into it. */
  backstory?: string;
  homeland?: string;
  homelandNew?: boolean;
  faction?: string;
  factionNew?: boolean;
  /** Unfinished business, keyed by thread kind. A kind with no entry was not
   *  answered — every one of these is optional, and a character who walks in
   *  with nothing left open is a legitimate character. */
  threads: Record<string, { summary: string; subject?: string; place?: string;
                            existing?: string }>;
  deity?: string;                 // patron god (esp. clerics/paladins/warlocks)
  gender?: string;                // gender identity (free-form)
  /** The likeness, chosen before the seal. `portraitImage` is what to SHOW (a
   *  data URL, held only in the browser); the server already has the picture
   *  filed under `portraitToken` and adopts it at registration. */
  portraitToken: string;
  portraitImage?: string | null;
  portraitDesc?: string;
  name: string;
}

/** A token for this wizard run, minted once. It is the subject a pre-seal
 *  likeness is filed under, so re-summoning a face is the same draft rather
 *  than a fresh stranger each press. */
const mintToken = (): string => {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID().replace(/-/g, "");
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
};

const freshDraft = (): Draft => ({
  boostMode: "two-one", method: "standard_array", pool: [], assigned: {},
  pointBuy: { STR: 8, DEX: 8, CON: 8, INT: 8, WIS: 8, CHA: 8 },
  skills: [], featPicks: {}, speciesPicks: {}, threads: {},
  cantrips: [], spells: [], miCantrips: [], miSpells: [],
  gearMode: "kit", cart: {}, portraitToken: mintToken(), name: "",
});

/** A payload list, deduplicated — or undefined when nothing was chosen (the
 *  fields are optional and an empty array is noise on the wire). */
const uniq = (xs: string[]): string[] | undefined =>
  xs.length ? [...new Set(xs)] : undefined;

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
function SpellPicker({ title, list, chosen, n, onToggle, onShow }: {
  title: string; list: SpellBrief[]; chosen: string[]; n: number;
  onToggle: (slug: string) => void;
  /** Put a spell in the detail pane. A card has room for one sentence, and a
   *  spell is not a thing anybody can choose off one sentence — so pointing at
   *  one (or taking it) shows the whole entry beside the grid. */
  onShow?: (sp: SpellBrief) => void;
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
                  className={`cf-card ${on ? "picked" : ""} ${
                    !on && chosen.length >= n ? "locked" : ""}`}
                  // A LOCKED card still explains itself: the grid disables
                  // everything once N are picked, and a spell you can no longer
                  // take is exactly the one you want to read before swapping.
                  onMouseEnter={() => onShow?.(sp)}
                  onFocus={() => onShow?.(sp)}
                  onClick={() => {
                    uiTick();
                    onShow?.(sp);
                    if (!on && chosen.length >= n) return;
                    onToggle(sp.slug);
                  }}
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
    ability minimums, spellcasting). Returns null when met, else the reason.

    `waiveLevel` is the Custom Lineage slot: "any feat you qualify for" answers
    to the feat's own prerequisites, not to the level its category is filed
    behind — that gate is the class ASI schedule, and stepping outside it is
    the whole gift. Two feats keep their level whoever is asking: an epic boon,
    because level 19 is what an epic boon IS, and the straight Ability Score
    Improvement, because it is the ASI schedule itself and the slot exists to
    step outside that, not to buy a turn of it. The server re-checks both. */
/** What the character already has, for judging a feat's prerequisites. */
interface Held {
  feats: string[];                 // slugs taken in the other slot
  options: string[];               // named picks those feats made, lowercased
  background?: string;             // slug
  byName: { name: string; slug: string }[];   // the whole feat pool, for name matches
}

function featBlockReason(
  feat: CCOptions["feats"][number],
  finalStats: Partial<Record<Ability, number>>,
  clsSlug?: string,
  waiveLevel?: boolean,
  held?: Held,
): string | null {
  const levelGated = !waiveLevel || feat.category === "epic-boon"
    || feat.slug === "ability-score-improvement";
  if (levelGated && (feat.min_level ?? 1) > 1) return `level ${feat.min_level}+`;
  const pre = (feat.prerequisite ?? "").replace(/\s+/g, " ").trim();
  if (!pre) return null;

  /** One alternative: true met, false unmet, null unparseable (so allowed). */
  const altOk = (c: string): { ok: boolean | null; why?: string } => {
    const m = c.match(/\b(str|dex|con|int|wis|cha)[a-z]*\D{0,12}?(\d+)/);
    if (m) {
      const code = m[1].slice(0, 3).toUpperCase() as Ability;
      return (finalStats[code] ?? 0) >= Number(m[2])
        ? { ok: true } : { ok: false, why: `needs ${code} ${m[2]}+` };
    }
    const lv = c.match(/level\s*(\d+)/);
    if (lv) {
      // The book prints the level twice; a slot that waives it waives both.
      if (waiveLevel && !levelGated) return { ok: true };
      return Number(lv[1]) <= 1
        ? { ok: true } : { ok: false, why: `level ${lv[1]}+` };
    }
    if (c.includes("spellcast") || c.includes("pact magic") || c.includes("cast a spell")) {
      return CASTER_CLASSES.has((clsSlug ?? "").toLowerCase())
        ? { ok: true } : { ok: false, why: "needs a spellcasting class" };
    }
    // A PARENT FEAT, named in words — "Strike of the Giants (Hill Strike)".
    // Without this the giant feats all read as free at level 1 in the Custom
    // Lineage slot: their real gate is the feat they build on, not the level.
    const parent = (held?.byName ?? []).find(
      (f) => f.name.length > 3 && c.includes(f.name.toLowerCase()));
    if (parent) {
      if (!(held?.feats ?? []).includes(parent.slug))
        return { ok: false, why: `needs the ${parent.name} feat` };
      const opt = c.match(/\(([^)]+)\)/)?.[1]?.trim().toLowerCase();
      if (opt && !(held?.options ?? []).includes(opt))
        return { ok: false, why: `needs ${parent.name} (${opt})` };
      return { ok: true };
    }
    if (c.includes("background")) {
      const bg = (held?.background ?? "").replace(/-/g, " ").toLowerCase();
      return bg && c.includes(bg) ? { ok: true }
        : { ok: false, why: "needs a different background" };
    }
    return { ok: null };
  };

  // Prerequisites are REQUIREMENTS (";" / " and ") of ALTERNATIVES ("or") —
  // the same shape the server reads them in. Reading the alternatives as
  // requirements locks people out of feats they qualify for.
  for (const req of pre.split(/;| and /i)) {
    const clause = req.replace(/^\s*prereq[a-z]*\s*:?/i, "").trim().toLowerCase();
    if (!clause) continue;
    const alts = clause.split(/,\s*or\s+|\s+or\s+/).map((a) => altOk(a.trim()));
    if (alts.some((a) => a.ok === true || a.ok === null)) continue;
    return alts.find((a) => a.why)?.why ?? "prerequisite not met";
  }
  return null;
}

export function CreateFlow({ onDone, onCancel, ccError, sealed, onEnterWorld,
                             entering, enterError }: {
  onDone: (payload: CCPayload) => void;
  onCancel: () => void;
  ccError: string | null;
  /** Set once the character exists. The likeness is chosen two stages EARLIER
   *  (against the wizard's draft token, which the server adopts at
   *  registration), so this now only turns Name & Seal into the way in. */
  sealed?: { name: string; id: number | null } | null;
  onEnterWorld?: () => void;
  entering?: boolean;
  enterError?: string | null;
}) {
  const [opts, setOpts] = useState<CCOptions | null>(null);
  const [stage, setStage] = useState<Stage>("race");
  const [d, setD] = useState<Draft>(freshDraft());
  const [detail, setDetail] = useState<string | null>(null);
  // Which spell the detail pane is showing. Its own state rather than a slug in
  // `detail`, because the pane resolves `detail` against races/backgrounds/
  // items and a spell is none of those.
  const [spellDetail, setSpellDetail] = useState<SpellBrief | null>(null);
  // Spell lists (fetched lazily): the class's own list, and — for Magic
  // Initiate — the feat's chosen-class list. Keyed by slug so we don't refetch.
  const [spellData, setSpellData] = useState<CCSpells | null>(null);
  const [miData, setMiData] = useState<CCSpells | null>(null);
  // Feat spell pools, keyed by feat slug — both feats could ask for one.
  const [featSpellData, setFeatSpellData] =
    useState<Record<string, FeatSpells>>({});
  const [origins, setOrigins] = useState<CCOrigins | null>(null);
  const [threadKinds, setThreadKinds] = useState<CCThreadKind[]>([]);
  // Bring the racial-features + lineage panel into view when a species is
  // picked — on a phone it sits below the card grid and is easy to miss.
  const raceDetailRef = useRef<HTMLDivElement>(null);
  // Same problem one stage along: the keepsake's "make it yours" panel sits
  // under a grid of three dozen cards, so the one thing on the stage a player
  // can make their OWN was below the fold and read as not existing at all.
  const keepsakeRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/cc/options").then((r) => r.json()).then(setOpts)
      .catch(() => setOpts(null));
  }, []);

  // Who and where the world ALREADY has. Offered first, because a second
  // character out of the Ashen Coast makes both of them mean more; inventing
  // one is still a real answer, and becomes a real place.
  useEffect(() => {
    fetch("/cc/origins").then((r) => r.json()).then(setOrigins)
      .catch(() => setOrigins(null));
  }, []);

  // The unfinished-business questions. Served rather than written here so the
  // wizard and the server can never offer different lists.
  // Re-fetched when the SPECIES changes: the server ranks what the world
  // already has by whether it fits this character, so a tiefling isn't shown a
  // wood-elf village's burning as the obvious answer.
  useEffect(() => {
    // Keyed off the draft's own slug, not the resolved `race` object — that
    // is declared further down, and naming it here is a temporal dead zone.
    const name = opts?.races.find((r) => r.slug === d.race)?.name;
    const q = name ? `?species=${encodeURIComponent(name)}` : "";
    fetch(`/cc/threads${q}`).then((r) => r.json())
      .then((j) => setThreadKinds(j?.threads ?? []))
      .catch(() => setThreadKinds([]));
  }, [d.race, opts]);

  // The seal has happened — stay on Name & Seal, which becomes the way in.
  // (The likeness was chosen two stages ago and is already on the character.)
  useEffect(() => {
    if (sealed) setStage("review");
  }, [sealed]);

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

  // Fetch the pool for each chosen feat that asks for a school-scoped spell
  // (Fey Touched). The FILTER is the server's — asking by feat slug is what
  // keeps creation and level-up offering the same list.
  const spellFeatKey = [d.featBg, d.featRace].filter(Boolean).join(",");
  useEffect(() => {
    const slugs = spellFeatKey ? spellFeatKey.split(",") : [];
    if (!slugs.length) { setFeatSpellData({}); return; }
    let live = true;
    Promise.all(slugs.map((slug) =>
      fetch(`/cc/feat_spells/${slug}`).then((r) => r.json())
        .then((j: FeatSpells) => [slug, j] as const)
        .catch(() => null)))
      .then((rows) => {
        if (!live) return;
        setFeatSpellData(Object.fromEntries(
          rows.filter((r): r is readonly [string, FeatSpells] => !!r)));
      });
    return () => { live = false; };
  }, [spellFeatKey]);

  useEffect(() => {
    if (d.race && raceDetailRef.current) {
      raceDetailRef.current.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [d.race]);

  useEffect(() => {
    if (d.wondrous && keepsakeRef.current) {
      keepsakeRef.current.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [d.wondrous]);

  const race = opts?.races.find((r) => r.slug === d.race);
  const lineage = race?.lineages?.find((l) => l.slug === d.lineage);
  const cls = opts?.classes.find((c) => c.slug === d.cls);
  const bg = opts?.backgrounds.find((b) => b.slug === d.background);

  // 2024 feats: the background grants an Origin feat (everyone picks one), and
  // some species grant a second feat — Human an Origin feat, Custom Lineage
  // any feat you qualify for.
  const originFeats = useMemo(
    () => (opts?.feats ?? []).filter((f) => (f.category ?? "origin") === "origin"),
    [opts]);
  // A 2024 background GRANTS one named Origin feat — it is not a free pick.
  // Look it up in the whole pool, not just the origin category: a book
  // background can grant a feat filed elsewhere (Rune Carver → Rune Shaper,
  // which is a "giant" feat and so was missing from the picker entirely).
  const grantedBgFeat = useMemo(
    () => (bg?.origin_feat
      ? opts?.feats.find((f) => f.slug === bg.origin_feat) ?? null
      : null),
    [bg, opts]);
  // Only backgrounds that name no feat (legacy 2014 entries) offer a choice.
  const needsBgFeat = !!bg && !grantedBgFeat && (opts?.feats.length ?? 0) > 0;
  // …and that choice can't be the species feat already taken, for the same
  // reason the species pick can't be the background's.
  const bgFeatPool = useMemo(
    () => (d.featRace
      ? originFeats.filter((f) => f.slug !== d.featRace || f.repeatable)
      : originFeats),
    [originFeats, d.featRace]);
  const raceFeat = race?.feat_choice ?? null;   // "origin" | "any" | null
  // The species pick and the background's Origin feat are two SLOTS, not two
  // copies of one feat: a Giant Foundling already granted Strike of the Giants
  // can't spend the Custom Lineage pick on it again (the benefit is recorded
  // once, so the second slot would buy nothing). The server re-checks this.
  const raceFeatPool = useMemo(() => {
    if (!raceFeat || !opts) return [];
    const pool = raceFeat === "any" ? opts.feats : originFeats;
    return d.featBg ? pool.filter((f) => f.slug !== d.featBg || f.repeatable) : pool;
  }, [raceFeat, opts, originFeats, d.featBg]);

  // The background's Origin feat is granted, not chosen — bind it (and clear
  // the previous one's answers) whenever the background changes.
  useEffect(() => {
    setD((cur) => ({
      ...cur, featBg: grantedBgFeat?.slug,
      // A background that grants what the species pick already spent leaves
      // the species slot holding a duplicate — drop it and let it be re-picked.
      featRace: cur.featRace && cur.featRace === grantedBgFeat?.slug
        ? undefined : cur.featRace,
      featPicks: {},
      miClass: undefined, miCantrips: [], miSpells: [],
    }));
  }, [grantedBgFeat]);

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

  // ----- what the sheet will say (the review page, and the payload) -----
  const nameOfSpell = (slug: string) =>
    spellData?.cantrips.find((x) => x.slug === slug)?.name
    ?? spellData?.spells.find((x) => x.slug === slug)?.name
    ?? miData?.cantrips.find((x) => x.slug === slug)?.name
    ?? miData?.spells.find((x) => x.slug === slug)?.name
    ?? Object.values(featSpellData).flatMap((f) => f.picks ?? [])
        .flatMap((q) => q.spells).find((x) => x.slug === slug)?.name
    ?? slug.replace(/-/g, " ");

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

  // ----- species choices -----
  // A species asks in the same schema a feat does (a skill, its languages, a
  // gift), so the SAME component renders them — and `partActive` is what keeps
  // a conditional question (Custom Lineage's extra skill) off the screen until
  // the option it hangs off is taken.
  const speciesQuestions = useMemo(
    () => choiceParts(race?.choices).filter((p) => partActive(p, d.speciesPicks)),
    [race, d.speciesPicks]);
  const speciesDone = speciesQuestions.every(
    (part) => partSatisfied(part, d.speciesPicks));
  /** Only what an ACTIVE question asked for — a gift swapped from the skill
   *  half to darkvision must not still post the skill it no longer grants. */
  const speciesPicked = (key: "skills" | "tools" | "languages" | "options"): string[] => {
    const wanted = new Set(speciesQuestions.map(
      (p) => (p.kind === "language" ? "languages" : p.kind)));
    return wanted.has(key) ? ((d.speciesPicks[key] as string[] | undefined) ?? []) : [];
  };

  // ----- feat & spell choices -----
  // Choices carried by the chosen origin feats (Skilled → skills, Magic
  // Initiate → a class + cantrips + a spell).
  const chosenFeats = [d.featBg, d.featRace].filter(Boolean) as string[];
  // A feat may ask more than one thing (Dragonscarred wants an ability AND a
  // damage resistance; Skill Expert wants three), so flatten `also` in
  // alongside the primary choice — `choiceParts` is the same flattening the
  // level-up overlay uses, so the two can't drift. Each feat's questions stay
  // ATTACHED to it: two feats asking the same kind are two separate answers.
  // The INDEX rides along: a feat may ask for spells twice (the Book of
  // Shadows wants cantrips and rituals), and the index is how each answer
  // finds its own pool. A `when` question isn't asked until the option it
  // hangs off is taken.
  const featQuestions: { slug: string; part: Choice; idx: number }[] =
    chosenFeats.flatMap((slug) =>
      choiceParts(opts?.feats.find((f) => f.slug === slug)?.choices)
        .map((part, idx) => ({ slug, part: part as Choice, idx }))
        .filter(({ part }) => partActive(part, d.featPicks[slug] ?? {})));
  const picksFor = (slug: string): FeatPicks => d.featPicks[slug] ?? {};
  // What the character holds, for a prerequisite that names another FEAT —
  // the giant feats build on Strike of the Giants and its chosen strike, and
  // without this they all read as free.
  const held: Held = {
    feats: chosenFeats,
    options: chosenFeats.flatMap(
      (s) => (picksFor(s).options ?? []).map((o) => o.toLowerCase())),
    background: d.background,
    byName: (opts?.feats ?? []).map((f) => ({ name: f.name, slug: f.slug })),
  };
  const setPicksFor = (slug: string, next: FeatPicks) =>
    setD({ ...d, featPicks: { ...d.featPicks, [slug]: next } });
  // Spell questions are answered on the SPELLS stage, everything else here.
  const isSpellPart = (c: Choice) =>
    c.kind === "magic_initiate" || c.kind === "spells";
  const featChoicesDone = featQuestions.every(({ slug, part }) =>
    isSpellPart(part) || partSatisfied(part, picksFor(slug)));

  // The Spells stage appears when the class casts OR a feat asks for spells
  // (Magic Initiate's class list, Fey Touched's school-scoped pick).
  const miChoice = featQuestions.find((q) => q.part.kind === "magic_initiate")?.part;
  const featSpellQuestions = featQuestions.filter(
    ({ part }) => part.kind === "spells" && (part.n ?? 0) > 0);
  const needsSpells = !!spellData || !!miChoice || featSpellQuestions.length > 0;
  const classCantripsDone = !spellData || d.cantrips.length === spellData.cantrips_n;
  const classSpellsDone = !spellData || d.spells.length === spellData.spells_n;
  const miDone = !miChoice
    || (!!d.miClass && d.miCantrips.length === (miChoice.cantrips ?? 2)
        && d.miSpells.length === (miChoice.spells ?? 1));
  const featSpellsDone = featSpellQuestions.every(
    ({ slug, part }) => partSatisfied(part, picksFor(slug)));
  const spellsDone = classCantripsDone && classSpellsDone && miDone && featSpellsDone;

  // Everything the sheet will say, gathered once: the review page shows it and
  // the payload sends it, so what a player is shown before sealing is exactly
  // what gets sealed.
  const gatherFeat = (key: "skills" | "tools" | "languages" | "options"
                          | "spells" | "cantrips") =>
    chosenFeats.flatMap((sl) => picksFor(sl)[key] ?? []);
  const allCantripSlugs = [...d.cantrips, ...d.miCantrips, ...gatherFeat("cantrips")];
  const allSpellSlugs = [...d.spells, ...d.miSpells, ...gatherFeat("spells")];
  const allCantripNames = allCantripSlugs.map(nameOfSpell);
  const allSpellNames = allSpellSlugs.map(nameOfSpell);
  const skillsKnown = [...new Set([...(bg?.skills ?? []), ...d.skills,
    ...gatherFeat("skills"), ...speciesPicked("skills")])];
  const toolsKnown = [...new Set([...(bg?.tool ? [bg.tool] : []),
    ...gatherFeat("tools"), ...speciesPicked("tools")])];
  const languagesKnown = [...new Set([...gatherFeat("languages"),
    ...speciesPicked("languages")])];

  // ----- the numbers the review page states -----
  //
  // Only what can be derived from choices already on this screen. A skill's
  // modifier is deliberately NOT here: the skill -> ability table lives in
  // `rules/checks.py` and exists precisely so nothing else computes a check,
  // and a copy of it in the browser is a second answer waiting to drift.
  const abilityMod = (a: Ability) =>
    Math.floor(((finalScore(a) ?? 10) - 10) / 2);
  const signed = (n: number) => (n >= 0 ? `+${n}` : `${n}`);
  const PROFICIENCY = 2;                       // level 1, every class
  const hpAtOne = cls?.hit_die ? cls.hit_die + abilityMod("CON") : null;
  const speedFt = lineage?.speed ?? race?.speed ?? null;
  const savingThrows = cls?.saving_throws ?? [];

  const stageDone: Record<Stage, boolean> = {
    race: !!d.race && (!(race?.lineages?.length) || !!d.lineage) && speciesDone,
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
    review: d.name.trim().length >= 2 && !sealed,
    portrait: true,                       // optional — set one in-world later
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
      // EVERY feat that grants an ability increase folds into the final stats
      // — both slots, not just the first one found. The schema names abilities
      // in lowercase ("wis"); ABILITY_FULL is keyed uppercase, so index it
      // that way or the +1 is silently dropped.
      for (const { slug, part } of featQuestions) {
        const code = picksFor(slug).ability;
        if (part.kind !== "ability" || !code || (part.amount ?? 0) <= 0) continue;
        const full = ABILITY_FULL[code.toUpperCase() as Ability];
        if (full) {
          stats[full] = Math.min(part.max ?? 20,
            (stats[full] ?? 10) + (part.amount ?? 0));
        }
      }
      const feats = [d.featBg, d.featRace].filter(Boolean) as string[];
      const lineageName = race?.lineages?.find((l) => l.slug === d.lineage)?.name;
      // Each feat's proficiency picks, gathered across both slots.
      const gather = gatherFeat;
      // A feat may hand over CANTRIPS of its own (the Book of Shadows), and
      // what a feat GRANTS outright (Fey Touched's Misty Step) is NOT sent —
      // the server folds that in from the feat itself, so it can't be lost.
      const allCantrips = allCantripSlugs;
      const allSpells = allSpellSlugs;
      onDone({
        name: d.name.trim(),
        race: lineageName ? `${race!.name} (${lineageName})` : race!.name,
        char_class: cls!.name, background: bg!.slug,
        deity: d.deity?.trim() || undefined,
        gender: d.gender?.trim() || undefined,
        // Feat-granted skills (Skilled) fold into the skill list; tools/languages
        // ride their own fields.
        // …and what the SPECIES asked for rides the same fields: a skill is a
        // skill however it was granted, and the server files each under its
        // own tag.
        stats, skills: [...d.skills, ...gather("skills"),
                        ...speciesPicked("skills")],
        tools: uniq([...gather("tools"), ...speciesPicked("tools")]),
        languages: uniq([...gather("languages"), ...speciesPicked("languages")]),
        feat_options: uniq([...gather("options"), ...speciesPicked("options")]),
        feats: feats.length ? feats : undefined,
        cantrips: allCantrips.length ? allCantrips : undefined,
        spells: allSpells.length ? allSpells : undefined,
        gear_mode: d.gearMode,
        bought_items: d.gearMode === "buy"
          ? Object.entries(d.cart).map(([name, quantity]) => ({ name, quantity }))
          : undefined,
        wondrous_item: d.wondrous,
        wondrous_name: d.wondrousName?.trim() || undefined,
        wondrous_desc: d.wondrousDesc?.trim() || undefined,
        // The likeness drawn on the Likeness stage. The token is what the
        // server files it under; the words ride along even when no picture was
        // drawn, because they are what every later render is built from.
        portrait_draft: d.portraitImage ? d.portraitToken : undefined,
        appearance: d.portraitDesc?.trim() || undefined,
        backstory: d.backstory?.trim() || undefined,
        // Unfinished business. A kind whose summary is blank was skipped —
        // every one is optional, so it rides only when it was actually filled.
        threads: ((): CCThreadAnswer[] | undefined => {
          const rows = Object.entries(d.threads)
            .filter(([, v]) => (v?.summary ?? "").trim())
            .map(([kind, v]) => ({
              kind,
              summary: v.summary.trim(),
              subject: v.subject?.trim() || undefined,
              place: v.place?.trim() || undefined,
              existing: v.existing || undefined,
            }));
          return rows.length ? rows : undefined;
        })(),
        homeland: d.homeland?.trim() || undefined,
        homeland_new: d.homeland ? !!d.homelandNew : undefined,
        faction: d.faction?.trim() || undefined,
        faction_new: d.faction ? !!d.factionNew : undefined,
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
            // Once the character is SEALED nothing is a choice any more: the
            // rail would offer to change a species that is already written into
            // the world.
            disabled={!!sealed
              || (i > 0 && !visibleStages.slice(0, i).every((p) => stageDone[p.id]))}
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
                    // changing species clears its lineage, its own questions
                    // and any race feat
                    setD({ ...d, race: r.slug, lineage: undefined,
                           featRace: undefined, speciesPicks: {} });
                    setDetail(r.slug);
                  }}
                >
                  <SpeciesPortrait slug={r.slug} />
                  <div className={`cf-card-name${r.script ? ` script-${r.script}` : ""}`}>
                    {r.name}
                  </div>
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

                {/* What the species itself asks — a trait that says "one skill
                    of your choice" is a question, and nothing used to ask it. */}
                {race.choices && (
                  <>
                    <div className="cf-sub-label" style={{ marginTop: 18 }}>
                      {race.name} — your people's gifts
                      {!speciesDone && <span className="cf-req"> · required</span>}
                    </div>
                    <FeatChoiceFields
                      choice={race.choices}
                      picks={d.speciesPicks}
                      onChange={(next) => setD({ ...d, speciesPicks: next })} />
                  </>
                )}
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
                onClick={() => {
                  uiTick();
                  setD({ ...d, background: b.slug });
                  setDetail(b.slug);   // …and show the whole of what it grants
                }}
              >
                <div className="cf-card-name">{b.name}</div>
                <div className="cf-card-sub">
                  {b.skills.length ? b.skills.join(", ") : "—"}
                  {b.origin_feat ? ` · ${b.origin_feat.replace(/-/g, " ")}` : ""}
                </div>
              </button>
            ))}
            {bg && (
              <OriginPanel d={d} setD={setD} origins={origins} race={race}
                           threadKinds={threadKinds} />
            )}
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
              {(() => {
                const held = [...(bg?.skills ?? []), ...speciesPicked("skills")];
                return held.length ? ` — you already have ${held.join(", ")}` : "";
              })()}
            </div>
            <div className="cf-chips">
              {(cls?.skill_options ?? []).map((s) => {
                const on = d.skills.includes(s);
                // Already yours — from the background, or from a species trait
                // answered back on the Origin stage. Spending a class pick on
                // a proficiency you hold buys nothing.
                const granted = bg?.skills.includes(s)
                  || speciesPicked("skills").includes(s);
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
            {grantedBgFeat && (
              <>
                <div className="cf-sub-label" style={{ marginTop: 18 }}>
                  {bg?.name} grants the {grantedBgFeat.name} feat
                </div>
                <div className="cf-granted-feat">
                  <div className="cf-card-name">{grantedBgFeat.name}</div>
                  <div className="cf-card-sub">{grantedBgFeat.brief}</div>
                </div>
              </>
            )}
            {needsBgFeat && (
              <FeatPicker
                title={`Your ${bg?.name ?? "background"} grants an Origin feat`}
                feats={bgFeatPool} finalStats={finalStats} clsSlug={d.cls}
                chosen={d.featBg} held={held}
                onPick={(slug) => setD({ ...d, featBg: slug, featPicks: {},
                  miClass: undefined, miCantrips: [], miSpells: [] })} />
            )}
            {raceFeat && (
              <FeatPicker
                title={raceFeat === "any"
                  ? `${race?.name}: choose ANY feat you qualify for`
                  : `${race?.name} grants an Origin feat`}
                feats={raceFeatPool} finalStats={finalStats} clsSlug={d.cls}
                chosen={d.featRace} held={held}
                waiveLevel={raceFeat === "any"}
                onPick={(slug) => setD({ ...d, featRace: slug, featPicks: {},
                  miClass: undefined, miCantrips: [], miSpells: [] })} />
            )}
            {/* Every question each chosen feat asks, rendered by the SHARED
                component so creation and the level-up overlay ask them the
                same way. Answers are kept per feat: two feats both wanting a
                skill are two picks. Spell questions wait for the Spells
                stage, where the spell lists live. */}
            {chosenFeats.map((slug) => {
              const row = opts?.feats.find((f) => f.slug === slug);
              const parts = choiceParts(row?.choices).filter((c) => !isSpellPart(c as Choice));
              if (!row || !parts.length) return null;
              return (
                <div key={slug} style={{ marginTop: 4 }}>
                  <div className="cf-sub-label" style={{ marginTop: 14 }}>
                    {row.name}
                  </div>
                  <FeatChoiceFields
                    choice={{ ...parts[0], also: parts.slice(1) } as FeatChoice}
                    picks={picksFor(slug)}
                    onChange={(next) => setPicksFor(slug, next)} />
                </div>
              );
            })}
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
                    onShow={setSpellDetail}
                    title={`Cantrips (choose ${spellData.cantrips_n})`}
                    list={spellData.cantrips} chosen={d.cantrips}
                    n={spellData.cantrips_n}
                    onToggle={(slug) => setD({ ...d, cantrips: d.cantrips.includes(slug)
                      ? d.cantrips.filter((x) => x !== slug) : [...d.cantrips, slug] })} />
                )}
                <SpellPicker
                  onShow={setSpellDetail}
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
                      onShow={setSpellDetail}
                      title={`${d.miClass} cantrips (choose ${miChoice.cantrips ?? 2})`}
                      list={miData.cantrips} chosen={d.miCantrips}
                      n={miChoice.cantrips ?? 2}
                      onToggle={(slug) => setD({ ...d, miCantrips: d.miCantrips.includes(slug)
                        ? d.miCantrips.filter((x) => x !== slug) : [...d.miCantrips, slug] })} />
                    <SpellPicker
                      onShow={setSpellDetail}
                      title={`1st-level spell (choose ${miChoice.spells ?? 1})`}
                      list={miData.spells} chosen={d.miSpells} n={miChoice.spells ?? 1}
                      onToggle={(slug) => setD({ ...d, miSpells: d.miSpells.includes(slug)
                        ? d.miSpells.filter((x) => x !== slug) : [...d.miSpells, slug] })} />
                  </>
                )}
              </div>
            )}
            {featSpellQuestions.map(({ slug, part, idx }) => {
              const pool = (featSpellData[slug]?.picks ?? [])
                .find((p) => p.idx === idx);
              if (!pool) return null;
              const bucket = spellBucket(part);
              const chosen = picksFor(slug)[bucket] ?? [];
              const n = part.n ?? 1;
              return (
                <div key={`${slug}#${idx}`}
                     style={{ marginTop: (spellData || miChoice) ? 20 : 0 }}>
                  <SpellPicker
                    onShow={setSpellDetail}
                    title={part.hint || `Feat spell (choose ${n})`}
                    list={pool.spells} chosen={chosen} n={n}
                    onToggle={(sl) => setPicksFor(slug, {
                      ...picksFor(slug),
                      [bucket]: chosen.includes(sl)
                        ? chosen.filter((x) => x !== sl) : [...chosen, sl],
                    })} />
                  {pool.granted.length > 0 && (
                    <p className="cf-hint">
                      Always prepared: {pool.granted.map((g) => g.name).join(", ")}.
                    </p>
                  )}
                </div>
              );
            })}
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
            <p className="cf-hint">
              Whatever you take, you can give it your <b>own name</b> and say
              what it looks like — the Oracle draws that piece for you alone,
              and its rules don't change. The words are asked for once you've
              picked something.
            </p>
            <div className="cf-grid">
              {opts.common_items.map((w) => (
                <button
                  key={w.slug}
                  className={`cf-card ${d.wondrous === w.slug ? "picked" : ""}`}
                  onClick={() => {
                    uiTick();
                    setDetail(w.slug);
                    setD({ ...d, wondrous: d.wondrous === w.slug ? undefined : w.slug,
                           // a different keepsake is a different piece: its
                           // name and description belong to the one you took
                           wondrousName: undefined, wondrousDesc: undefined });
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
            {d.wondrous && (
              <div className="cf-keepsake" ref={keepsakeRef}>
                <label className="cf-sub-label">
                  ✦ Make it yours <span className="cf-req">· optional</span>
                </label>
                <p className="cf-hint">
                  A keepsake is the one thing you start with that nobody else
                  has. Give it a name and say what it looks like, and the Oracle
                  will draw that piece for you — its rules don't change.
                </p>
                <input
                  className="cf-input"
                  placeholder={`a name of your own — or leave it "${
                    opts.common_items.find((w) => w.slug === d.wondrous)?.name ?? ""}"`}
                  maxLength={48}
                  value={d.wondrousName ?? ""}
                  onChange={(e) => setD({ ...d, wondrousName: e.target.value })}
                />
                <textarea
                  className="cf-input cf-textarea"
                  placeholder="what it looks like — the metal, the wear, whose hands it passed through…"
                  maxLength={300}
                  rows={3}
                  value={d.wondrousDesc ?? ""}
                  onChange={(e) => setD({ ...d, wondrousDesc: e.target.value })}
                />
                <p className="cf-hint">
                  {d.wondrousDesc?.trim()
                    ? "✓ Your words are kept. The picture is drawn after you seal "
                      + "the character — it appears on the piece in your pack."
                    : "Leave this empty and you get the catalogue's picture of "
                      + "an ordinary one."}
                </p>
              </div>
            )}
          </>
        )}

        {stage === "review" && (
          <div className="cf-review">
            {sealed ? (
              <div className="cf-sealed">
                <h3>✦ {sealed.name} is written into the world.</h3>
                <p className="cf-hint">
                  Their homeland, their people and everything they left open are
                  real places and real people now. Step through when you're ready.
                </p>
                {enterError && <p className="cf-error">⚠ {enterError}</p>}
              </div>
            ) : (
              <input
                className="cf-name"
                placeholder="Speak your name…"
                value={d.name}
                maxLength={40}
                onChange={(e) => setD({ ...d, name: e.target.value })}
              />
            )}
            <div className="cf-summary">
              {/* THE WHOLE character, because this is the last chance to
                  change any of it. A summary that lists four lines of a sheet
                  is not something a player can check their work against. */}
              <div className="cf-rev-head">
                {d.portraitImage && (
                  <img className="cf-rev-face" src={d.portraitImage}
                       alt="likeness" />
                )}
                <div>
                  <p className="cf-rev-title">
                    <b>{race?.name}{lineage ? ` (${lineage.name})` : ""}</b>{" "}
                    {cls?.name} · {bg?.name}
                  </p>
                  <p className="cf-detail-meta">
                    {[race?.creature_type ?? "Humanoid", race?.size,
                      d.gender?.trim() || null,
                      d.deity?.trim() ? `sworn to ${d.deity.trim()}` : null]
                      .filter(Boolean).join(" · ")}
                  </p>
                </div>
              </div>

              <div className="cf-vitals">
                <div className="vital"><div className="k">Hit points</div>
                  <div className="v">{hpAtOne ?? "—"}</div></div>
                <div className="vital"><div className="k">Speed</div>
                  <div className="v">{speedFt ? `${speedFt} ft` : "—"}</div></div>
                <div className="vital"><div className="k">Initiative</div>
                  <div className="v">{signed(abilityMod("DEX"))}</div></div>
                <div className="vital"><div className="k">Proficiency</div>
                  <div className="v">{signed(PROFICIENCY)}</div></div>
                <div className="vital"><div className="k">Hit die</div>
                  <div className="v">{cls ? `d${cls.hit_die}` : "—"}</div></div>
                <div className="vital"><div className="k">Darkvision</div>
                  <div className="v">
                    {(lineage?.darkvision ?? race?.darkvision) ? "yes" : "—"}</div></div>
              </div>

              <div className="stat-grid">
                {ABILITIES.map((a) => (
                  <div className={`stat ${savingThrows.includes(a) ? "saved" : ""}`}
                       key={a}>
                    <div className="k">{a}</div>
                    <div className="v">{finalScore(a) ?? "—"}</div>
                    <div className="m">{signed(abilityMod(a))}</div>
                  </div>
                ))}
              </div>
              <p className="inv-line"><b>Saving throws</b> · {
                savingThrows.length
                  ? savingThrows.map((a) => `${a} ${signed(
                      abilityMod(a as Ability) + PROFICIENCY)}`).join(", ")
                  : "—"}
                <em className="cf-was"> — every other save is the bare modifier</em>
              </p>
              {bonuses && Object.keys(bonuses).length > 0 && (
                <p className="inv-line"><b>Background boosts</b> · {
                  ABILITIES.filter((a) => bonuses[a])
                    .map((a) => `${a} +${bonuses[a]}`).join(", ")}</p>
              )}

              <p className="inv-line"><b>Skills</b> · {
                skillsKnown.length ? skillsKnown.join(", ") : "—"}</p>
              {toolsKnown.length > 0 && (
                <p className="inv-line"><b>Tools</b> · {toolsKnown.join(", ")}</p>
              )}
              <p className="inv-line"><b>Languages</b> · {
                [race?.languages, ...languagesKnown].filter(Boolean).join(", ")
                  || "—"}</p>

              {/* What the SPECIES is, in full — the traits are the half of a
                  character nobody can look up once the wizard is closed. */}
              {(race?.traits?.length || lineage?.traits?.length) ? (
                <div className="cf-rev-block">
                  <div className="cf-sub-label">
                    {race?.name}{lineage ? ` · ${lineage.name}` : ""}
                  </div>
                  <ul className="cf-rev-list">
                    {(race?.traits ?? []).map((t, i) => <li key={`t${i}`}>{t}</li>)}
                    {(lineage?.traits ?? []).map((t, i) => <li key={`lt${i}`}>{t}</li>)}
                  </ul>
                </div>
              ) : null}
              {race?.immunities?.length ? (
                <p className="inv-line"><b>Immune to</b> · {race.immunities.join(", ")}</p>
              ) : null}

              {/* Feats WITH what they do and what each one was answered — a
                  feat listed as a bare name is a label, not a choice made. */}
              {(d.featBg || d.featRace) && (
                <div className="cf-rev-block">
                  <div className="cf-sub-label">Feats</div>
                  <ul className="cf-rev-list">
                    {([d.featBg, d.featRace].filter(Boolean) as string[]).map((slug) => {
                      const f = opts.feats.find((x) => x.slug === slug);
                      const picks = picksFor(slug);
                      const answers = [
                        ...(picks.options ?? []),
                        ...(picks.skills ?? []),
                        ...(picks.tools ?? []),
                        ...(picks.languages ?? []),
                        ...(picks.ability ? [`+1 ${picks.ability.toUpperCase()}`] : []),
                      ];
                      return (
                        <li key={slug}>
                          <b>{f?.name ?? slug.replace(/-/g, " ")}</b>
                          {f?.brief ? ` — ${f.brief}` : ""}
                          {answers.length ? (
                            <em className="cf-was"> · {answers.join(", ")}</em>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}

              {/* What the BACKGROUND grants, beyond the two skills its card
                  showed. */}
              {bg && (
                <div className="cf-rev-block">
                  <div className="cf-sub-label">{bg.name}</div>
                  <ul className="cf-rev-list">
                    {bg.feature && <li><b>Feature</b> — {bg.feature}</li>}
                    {bg.tool && <li><b>Tool</b> — {bg.tool}</li>}
                    {bg.skills?.length ? (
                      <li><b>Skills</b> — {bg.skills.join(", ")}</li>) : null}
                  </ul>
                </div>
              )}

              {allCantripNames.length > 0 && (
                <p className="inv-line"><b>Cantrips</b> · {allCantripNames.join(", ")}</p>
              )}
              {allSpellNames.length > 0 && (
                <p className="inv-line"><b>Spells</b> · {allSpellNames.join(", ")}</p>
              )}
              {spellData?.ability && (
                <p className="inv-line"><b>Spellcasting</b> · {
                  spellData.ability.toUpperCase()} — save DC {
                  8 + PROFICIENCY + abilityMod(
                    spellData.ability.slice(0, 3).toUpperCase() as Ability)}, attack {
                  signed(PROFICIENCY + abilityMod(
                    spellData.ability.slice(0, 3).toUpperCase() as Ability))}</p>
              )}

              {/* GEAR, itemised. "bought 7 item(s)" is a receipt total, not a
                  pack you can check. */}
              <div className="cf-rev-block">
                <div className="cf-sub-label">
                  {d.gearMode === "buy"
                    ? `Bought — ${(budget - cartCost).toFixed(0)} gp left of ${budget}`
                    : "Standard kit"}
                </div>
                {d.gearMode === "buy" ? (
                  Object.keys(d.cart).length ? (
                    <ul className="cf-rev-list">
                      {Object.entries(d.cart).map(([name, qty]) => (
                        <li key={name}>{qty > 1 ? `${name} ×${qty}` : name}</li>
                      ))}
                    </ul>
                  ) : <p className="cf-hint">Nothing bought — you walk in empty-handed.</p>
                ) : (
                  <ul className="cf-rev-list">
                    <li>The {cls?.name ?? "class"} starting package</li>
                    {bg?.items?.length ? (
                      <li>{bg.name}'s gear — {bg.items.map(
                        (it) => (it.quantity > 1 ? `${it.name} ×${it.quantity}` : it.name),
                      ).join(", ")}</li>
                    ) : null}
                  </ul>
                )}
              </div>

              {d.wondrous && (
                <div className="cf-rev-block">
                  <div className="cf-sub-label">Keepsake</div>
                  <ul className="cf-rev-list">
                    <li>
                      <b>{d.wondrousName?.trim()
                        || opts.common_items.find((w) => w.slug === d.wondrous)?.name}</b>
                      {d.wondrousName?.trim() && (
                        <em className="cf-was"> — a {
                          opts.common_items.find((w) => w.slug === d.wondrous)?.name
                        }, renamed; its rules are unchanged</em>
                      )}
                    </li>
                    {d.wondrousDesc?.trim() ? (
                      <li className="cf-rev-quote">“{d.wondrousDesc.trim()}”
                        <em className="cf-was"> — the Oracle will draw this piece
                        for you alone</em></li>
                    ) : null}
                  </ul>
                </div>
              )}

              {(d.homeland || d.faction) && (
                <p className="inv-line"><b>Origin</b> · {
                  [d.homeland && `of ${d.homeland}${d.homelandNew ? " (new to the world)" : ""}`,
                   d.faction && `${d.faction}${d.factionNew ? " (new to the world)" : ""}`]
                    .filter(Boolean).join(" · ")}</p>
              )}

              {/* Unfinished business — real places and people the seal creates.
                  It belongs on the page that says what sealing does. */}
              {Object.entries(d.threads).filter(([, v]) => (v?.summary ?? "").trim()).length > 0 && (
                <div className="cf-rev-block">
                  <div className="cf-sub-label">Unfinished business</div>
                  <ul className="cf-rev-list">
                    {Object.entries(d.threads)
                      .filter(([, v]) => (v?.summary ?? "").trim())
                      .map(([kind, v]) => (
                        <li key={kind}>
                          <b>{threadKinds.find((k) => k.slug === kind)?.label
                              ?? kind.replace(/-/g, " ")}</b> — {v.summary.trim()}
                          {(v.subject || v.place) && (
                            <em className="cf-was"> · {
                              [v.subject, v.place].filter(Boolean).join(" · ")}</em>
                          )}
                        </li>
                      ))}
                  </ul>
                </div>
              )}

              {d.portraitDesc?.trim() && (
                <p className="inv-line"><b>Likeness</b> · {d.portraitDesc.trim()}</p>
              )}
              {d.backstory?.trim() && (
                <p className="cf-backstory">{d.backstory.trim()}</p>
              )}
            </div>
            {ccError && <p className="cf-error">⚠ {ccError}</p>}
          </div>
        )}
        {stage === "portrait" && (
          <PortraitStep
            name={d.name.trim()}
            draft={{
              token: d.portraitToken,
              race: race
                ? (lineage ? `${race.name} (${lineage.name})` : race.name)
                : "",
              char_class: cls?.name ?? "",
              gender: d.gender ?? "",
            }}
            initial={{ image: d.portraitImage ?? null,
                       description: d.portraitDesc ?? "" }}
            onChange={({ image, description }) =>
              setD({ ...d, portraitImage: image, portraitDesc: description })}
            onDone={() => { /* the wizard footer walks on */ }} />
        )}
      </main>

      {/* On the race stage the inline panel carries the same traits, so the
          narrow layout (where both would stack) hides this one — see
          `.cf-detail.race-dup` in the phone media block. */}
      <aside className={`cf-detail ${stage === "race" ? "race-dup" : ""}`}>
        <DetailPanel opts={opts} stage={stage} raceSlug={d.race} clsSlug={d.cls}
                     bgSlug={d.background} wondrousSlug={d.wondrous}
                     lineageSlug={d.lineage} hovered={detail}
                     spell={spellDetail} />
      </aside>

      {/* One way forward, on every stage including the likeness — which is a
          stage of the wizard now, not a screen after it. Once the character is
          sealed the same footer becomes the way into the world. */}
      <footer className="cf-foot">
        {sealed
          ? <button className="lu-confirm" disabled={entering}
                    onClick={() => { uiTick(); onEnterWorld?.(); }}>
              {entering ? "Entering the world…" : "Enter the world ➤"}
            </button>
          : <button className="lu-confirm" disabled={!canNext} onClick={next}>
              {stage === "review" ? "Seal the character" : "Onward ➤"}
            </button>}
      </footer>
    </div>
  );
}

/** Where a character comes FROM: a homeland, a people, and their own words.
 *
 *  Every part of it is optional — a character with no answers here is a
 *  perfectly good character. What it is NOT is decoration: a homeland and a
 *  faction become real world entities the DM can use, which is why the ones
 *  the world already has are offered first and inventing one says so out loud.
 */
function OriginPanel({ d, setD, origins, race, threadKinds }: {
  d: Draft; setD: (d: Draft) => void;
  origins: CCOrigins | null;
  race?: CCOptions["races"][number];
  threadKinds: CCThreadKind[];
}) {
  const [ownHome, setOwnHome] = useState(false);
  const [ownFaction, setOwnFaction] = useState(false);
  const homelands = origins?.homelands ?? [];
  const factions = origins?.factions ?? [];

  const pickHome = (name: string, invented: boolean) => {
    uiTick();
    setD({ ...d, homeland: d.homeland === name && !invented ? undefined : name,
           homelandNew: invented });
  };
  const pickFaction = (name: string, invented: boolean) => {
    uiTick();
    setD({ ...d, faction: d.faction === name && !invented ? undefined : name,
           factionNew: invented });
  };

  return (
    <div className="cf-faith cf-origin">
      <label className="cf-sub-label">
        Your story <span className="cf-req">· optional</span>
      </label>
      <p className="cf-hint">
        Where does {race?.name ? `this ${race.name.toLowerCase()}` : "your character"}
        {" "}come from, and who claims them? Naming a place or a people that is
        already in the world ties you to it; naming one that isn't puts it there.
      </p>

      <div className="cf-origin-row">
        <span className="cf-origin-label">Homeland</span>
        <div className="cf-chips">
          {homelands.map((h) => (
            <button key={h.slug}
                    className={`cf-chip ${!ownHome && d.homeland === h.name ? "picked" : ""}`}
                    title={h.brief || undefined}
                    onClick={() => { setOwnHome(false); pickHome(h.name, false); }}>
              {h.name}
            </button>
          ))}
          <button className={`cf-chip ${ownHome ? "picked" : ""}`}
                  onClick={() => { uiTick(); setOwnHome(true);
                                   setD({ ...d, homeland: undefined, homelandNew: true }); }}>
            ✎ somewhere else
          </button>
        </div>
        {ownHome && (
          <input className="cf-input" maxLength={48}
                 placeholder="name the place you are of…"
                 value={d.homeland ?? ""}
                 onChange={(e) => setD({ ...d, homeland: e.target.value, homelandNew: true })} />
        )}
      </div>

      <div className="cf-origin-row">
        <span className="cf-origin-label">People or faction</span>
        <div className="cf-chips">
          {factions.map((f) => (
            <button key={f.slug}
                    className={`cf-chip ${!ownFaction && d.faction === f.name ? "picked" : ""}`}
                    title={f.brief || undefined}
                    onClick={() => { setOwnFaction(false); pickFaction(f.name, false); }}>
              {f.name}
            </button>
          ))}
          <button className={`cf-chip ${ownFaction ? "picked" : ""}`}
                  onClick={() => { uiTick(); setOwnFaction(true);
                                   setD({ ...d, faction: undefined, factionNew: true }); }}>
            ✎ {factions.length ? "another people" : "name your people"}
          </button>
        </div>
        {ownFaction && (
          <input className="cf-input" maxLength={48}
                 placeholder="a clan, a tribe, an order, a guild…"
                 value={d.faction ?? ""}
                 onChange={(e) => setD({ ...d, faction: e.target.value, factionNew: true })} />
        )}
      </div>

      <textarea
        className="cf-input cf-textarea"
        rows={4}
        maxLength={2000}
        placeholder="a few lines of their own story — who raised them, what they left, why they walk…"
        value={d.backstory ?? ""}
        onChange={(e) => setD({ ...d, backstory: e.target.value })}
      />

      {threadKinds.length > 0 && (
        <ThreadQuestions d={d} setD={setD} kinds={threadKinds} />
      )}
    </div>
  );
}

/** What the character never finished — the half of a backstory the DM can
 *  actually offer back. Every question is optional and none of them is a
 *  mechanic: answering buys nothing on the sheet, and skipping all of them is
 *  a legitimate character. What an answer DOES buy is a real place in the
 *  world at a real distance, which is why the words matter more than usual. */
function ThreadQuestions({ d, setD, kinds }: {
  d: Draft; setD: (d: Draft) => void; kinds: CCThreadKind[];
}) {
  const [open, setOpen] = useState<string | null>(null);
  const answered = (slug: string) => (d.threads[slug]?.summary ?? "").trim();

  const put = (slug: string, patch: Partial<{ summary: string; subject: string;
                                             place: string; existing: string }>) => {
    const prev = d.threads[slug] ?? { summary: "" };
    setD({ ...d, threads: { ...d.threads, [slug]: { ...prev, ...patch } } });
  };
  const clear = (slug: string) => {
    const next = { ...d.threads };
    delete next[slug];
    setD({ ...d, threads: next });
  };

  return (
    <div className="cf-threads">
      <label className="cf-sub-label" style={{ marginTop: 16 }}>
        Unfinished business <span className="cf-req">· all optional</span>
      </label>
      <p className="cf-hint">
        Anything you leave open here becomes a real place in the world, at a
        real distance — somewhere the DM can point you when you are casting
        about for something to do. Leave them all blank and nothing is lost.
      </p>

      {kinds.map((k) => {
        const isOpen = open === k.slug || !!answered(k.slug);
        const val = d.threads[k.slug];
        return (
          <div key={k.slug} className={`cf-thread ${answered(k.slug) ? "filled" : ""}`}>
            <button
              className="cf-thread-head"
              onClick={() => { uiTick(); setOpen(isOpen && !answered(k.slug) ? null : k.slug); }}
            >
              <span className="cf-thread-label">{k.label}</span>
              <span className="cf-thread-mark">{answered(k.slug) ? "✓" : "+"}</span>
            </button>

            {isOpen && (
              <div className="cf-thread-body">
                <p className="cf-hint">{k.question}</p>

                {/* What the world ALREADY has, offered first — the same order
                    /cc/origins puts existing homelands in, and for a stronger
                    reason: hitching to a village the party actually watched
                    burn costs the world no new place at all. */}
                {k.candidates.length > 0 && (
                  <div className="cf-thread-known">
                    <span className="cf-thread-known-label">
                      Already in the world
                    </span>
                    {k.candidates.map((c) => (
                      <button
                        key={c.slug}
                        className={`cf-known ${val?.existing === c.slug ? "picked" : ""}`
                                   + (c.fit === "outsider" ? " odd" : "")}
                        onClick={() => {
                          uiTick();
                          put(k.slug, {
                            existing: val?.existing === c.slug ? undefined : c.slug,
                            // Their own words still matter; seed them with
                            // something true so the thread is answered.
                            summary: (val?.summary ?? "").trim() || c.why,
                          });
                        }}
                      >
                        <span className="cf-known-name">{c.name}</span>
                        <span className="cf-known-why">{c.why}</span>
                        {c.fit_note && (
                          <span className="cf-known-odd">{c.fit_note}</span>
                        )}
                      </button>
                    ))}
                  </div>
                )}

                <div className="cf-chips">
                  {k.suggestions.map((sug) => (
                    <button
                      key={sug}
                      className={`cf-chip ${val?.summary === sug ? "picked" : ""}`}
                      onClick={() => { uiTick();
                        put(k.slug, { summary: sug, existing: undefined }); }}
                    >{sug}</button>
                  ))}
                </div>
                {/* Each field keeps a LABEL. A placeholder disappears the
                    moment it is filled, so three answered boxes in a row were
                    three unlabelled boxes — "Ashmere" sitting in a field with
                    nothing to say it is the place. */}
                <label className="cf-thread-field">
                  <span>In your words</span>
                  <input
                    className="cf-input"
                    maxLength={200}
                    placeholder="…or say it your own way"
                    value={val?.summary ?? ""}
                    onChange={(e) => put(k.slug, { summary: e.target.value })}
                  />
                </label>
                {k.wants_subject && (
                  <label className="cf-thread-field">
                    <span>{k.subject_prompt ?? "Who or what"}</span>
                    <input
                      className="cf-input"
                      maxLength={60}
                      placeholder="a name, if they have one"
                      value={val?.subject ?? ""}
                      onChange={(e) => put(k.slug, { subject: e.target.value })}
                    />
                  </label>
                )}
                {!val?.existing && (
                  <label className="cf-thread-field">
                    <span>The place, if you know its name <em>· optional</em></span>
                    <input
                      className="cf-input"
                      maxLength={48}
                      placeholder="otherwise the world names it for you"
                      value={val?.place ?? ""}
                      onChange={(e) => put(k.slug, { place: e.target.value })}
                    />
                  </label>
                )}
                {answered(k.slug) && (
                  <button className="cf-thread-clear"
                          onClick={() => { uiTick(); clear(k.slug); setOpen(null); }}>
                    clear this one
                  </button>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** A pool of feat cards, prerequisites enforced: feats you don't qualify for
    are greyed out, non-selectable, and show the reason. */
function FeatPicker({ title, feats, finalStats, clsSlug, chosen, onPick,
                     waiveLevel, held }: {
  title: string;
  feats: CCOptions["feats"];
  finalStats: Partial<Record<Ability, number>>;
  clsSlug?: string;
  chosen?: string;
  onPick: (slug: string) => void;
  /** The species slot that grants ANY feat — its level gate doesn't apply. */
  waiveLevel?: boolean;
  /** What the character already holds, for prerequisites that name a feat. */
  held?: Held;
}) {
  return (
    <>
      <div className="cf-sub-label" style={{ marginTop: 18 }}>{title}</div>
      <div className="cf-grid">
        {feats.map((f) => {
          const blocked = featBlockReason(f, finalStats, clsSlug, waiveLevel, held);
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

function DetailPanel({ opts, stage, raceSlug, clsSlug, bgSlug, wondrousSlug,
                      lineageSlug, hovered, spell }: {
  opts: CCOptions; stage: Stage;
  raceSlug?: string; clsSlug?: string; bgSlug?: string; wondrousSlug?: string;
  lineageSlug?: string;
  hovered: string | null;
  /** The spell the pane is showing, on the Spells stage. */
  spell?: SpellBrief | null;
}) {
  // The spell in hand outranks everything: it is the only thing on this stage
  // the pane can be about, and a spell chosen off a card's one sentence is a
  // spell chosen blind — the entry is the whole point of the panel.
  if (stage === "spells") {
    if (!spell) {
      return (
        <div className="cf-detail-body dim">
          <p>Point at a spell — or take one — to read what it does.</p>
        </div>
      );
    }
    return (
      <div className="cf-detail-body">
        <SpellEntry spell={spell} />
      </div>
    );
  }
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
  if (stage === "background") {
    // Everything a background actually gives, in one place: the boosts, the
    // skills, the tool, the Origin feat WITH what it does, and the gear. The
    // cards show two skills, which is not enough to choose on.
    // `hovered` may still hold the slug of something from an earlier stage;
    // fall back to the chosen background rather than showing nothing.
    const b = opts.backgrounds.find((x) => x.slug === hovered)
      ?? opts.backgrounds.find((x) => x.slug === bgSlug);
    if (b) {
      const feat = b.origin_feat
        ? opts.feats.find((f) => f.slug === b.origin_feat)
        : undefined;
      return (
        <div className="cf-detail-body">
          <h3>{b.name}</h3>
          <p className="cf-detail-meta">
            {b.abilities?.length
              ? `Ability boosts: ${b.abilities.join(" / ")} — +2 and +1, or +1 to each`
              : "Ability boosts: any three abilities"}
          </p>
          <ul>
            <li><b>Skills</b> — {b.skills.length ? b.skills.join(", ") : "—"}</li>
            {b.tool && <li><b>Tool</b> — {b.tool}</li>}
            {feat
              ? <li><b>Origin feat</b> — {feat.name}: {feat.brief}</li>
              : b.origin_feat
                ? <li><b>Origin feat</b> — {b.origin_feat.replace(/-/g, " ")}</li>
                : <li><b>Origin feat</b> — your choice from the origin feats</li>}
            {b.feature && <li><b>Feature</b> — {b.feature}</li>}
            {b.items?.length ? (
              <li><b>Equipment</b> — {b.items.map(
                (it) => (it.quantity > 1 ? `${it.name} ×${it.quantity}` : it.name),
              ).join(", ")}</li>
            ) : null}
          </ul>
          <p className="cf-detail-meta" style={{ opacity: 0.7 }}>
            Its gear comes with the standard kit; choosing to buy your own on
            the Gear stage takes it instead.
          </p>
        </div>
      );
    }
  }
  if (stage === "wondrous") {
    const w = opts.common_items.find((x) => x.slug === (hovered ?? wondrousSlug))
      ?? opts.common_items.find((x) => x.slug === wondrousSlug);
    if (w) {
      return (
        <div className="cf-detail-body">
          <h3>{w.name}</h3>
          <p className="cf-detail-meta">
            {[w.item_type, "Common", w.attunement ? "requires attunement" : null]
              .filter(Boolean).join(" · ")}
          </p>
          <p>{w.desc || w.brief}</p>
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
  if (stage === "portrait") {
    return (
      <div className="cf-detail-body dim">
        <p>
          A face is worth writing down even if you never draw one: your words
          are kept on the sheet, and every later likeness — a portrait in new
          armour, a token on the board — is built from them, so the same person
          comes back rather than a stranger in the right gear.
        </p>
        <p>
          Nothing here is required. Skip it and you can set one in-world later.
        </p>
      </div>
    );
  }
  if (stage === "review") {
    return (
      <div className="cf-detail-body dim">
        <p>
          Read it over — this is the last look you get at any of it. Sealing
          writes this character into the world: the homeland and people you
          named become real places, and everything you left unfinished becomes
          somewhere a DM can send you.
        </p>
      </div>
    );
  }
  return (
    <div className="cf-detail-body dim">
      <p>The ledger awaits your choices.</p>
    </div>
  );
}
