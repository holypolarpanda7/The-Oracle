/** Typed events on the session WebSocket (server -> client). */
export type LexKind = "name" | "magic" | "item" | "place";

/** The cultural hand a name is written in (see assets/fonts/ATTRIBUTION.md).
 *  Absent means the house serif, which is the right default for most names. */
export type Script = "celestial" | "dwarven" | "elven" | "draconic"
                   | "infernal" | "fey";

export interface LexEntry {
  text: string;
  kind: LexKind;
  script?: Script;
}

export interface RollResult {
  expr: string;
  label?: string;
  dc?: number;
  total: number;
  detail?: string; // e.g. "d20:14 +5"
  success?: boolean; // undefined when no DC (plain damage roll)
}

/** Someone standing in the same place as you, and how they feel about you. */
export interface Presence {
  name: string;
  kind: string;            // "npc" | "pc"
  role?: string;           // entity subtype — "innkeeper", "guard"
  attitude?: string;       // only for NPCs who have met you
}

/**
 * The "here and now" strip. NOT a map — the server sends only what a
 * character can tell by standing there and looking around (see the note on
 * `_activity_locale`); maps stay in-game artifacts you draft or buy.
 */
export interface Locale {
  place?: string;
  place_kind?: string;
  region?: string;
  date?: string;           // "12 Aestral, 1247 AF"
  time_of_day?: string;    // "morning"
  day?: number;
  weather?: string;
  hazards?: string[];
  present?: Presence[];
}

/** One line of the party's record — a world event they were part of. */
export interface JournalEntry {
  day: number;
  text: string;
  place?: string;
}

/** A thread the party has open (or has closed), from the QUEST scaffold. */
export interface QuestRow {
  name: string;
  state: string;           // offered | active | completed | failed
  tier: string;            // main | side | rumor
  conflict?: string;
  stakes?: string;
  patron?: string;
  reward?: string;
  objectives?: string[];   // only the steps still open
  leads?: string[];
}

/** Someone who has an opinion of you, and why. */
export interface BondRow {
  name: string;
  slug: string;
  role?: string;
  status?: string;         // absent when they're alive and about
  sentiment?: number;
  feeling?: string;        // loathes | hostile | wary | neutral | warm | allied | devoted
  attitude?: string;       // 5e social scale, when trust has been tracked
  reason?: string;         // the deed that most drives how they feel
  companion?: boolean;
}

/** Where you stand with a faction that has noticed you. */
export interface StandingRow {
  faction: string;
  slug: string;
  renown: number;
  standing?: string;
  perks?: string;
  next?: string;
  needed?: number;
  note?: string;
}

/** One thing the world contains, as far as THIS character knows it. */
export interface CodexRow {
  name: string;
  slug: string;
  kind: string;        // deity | place | faction | npc | lore
  subtype?: string;
  status?: string;
  script?: Script;
  note?: string;
  group?: string;      // power family, or region
}

export interface ChronicleData {
  entries: JournalEntry[];
  quests: QuestRow[];
  bonds: BondRow[];
  standing: StandingRow[];
  codex: CodexRow[];
  error?: string;
}

/** One way of getting somewhere. NOT a map: no coordinates, no bearing — only
 *  what a traveller could tell you in a taproom. */
export interface RouteRow {
  id: string;
  label: string;
  destination: string;
  miles: number;
  days: number;
  terrain: string;
  danger: string;
  nav_dc?: number;
  blurb: string;
}

export interface SpellSlotRow { level: number; total: number; used: number; }
export interface ResourceRow { name: string; total: number; used: number; die?: string; }
export interface SheetFeature {
  name: string;
  note?: string;
  kind?: "fire" | "arcane" | "martial" | "other";
}

export interface InventoryItem {
  name: string;
  qty?: number;
  type?: string;
  rarity?: string;
  brief?: string;         // hover tooltip
  interactive?: string;   // family badge: spellbook | charged | consumable | container | attunement
  /** Thumbnail of an already-rendered picture. Absent until the item has been
   *  drawn — the pack never triggers a render just by being opened. */
  art?: string;
  equipped?: boolean;
  attuned?: boolean;
  weight?: number;
  /** The one verb the card offers; the inspector still has the full set. */
  action?: { id: string; label: string };
  /** Rolled properties, when this piece carries any. */
  affixes?: AffixRow[];
}

/** A rolled property on a piece of gear (loot/affixes.py). */
export interface AffixRow {
  slug: string;
  name: string;
  kind: string;          // prefix | suffix
  tier: number;
  text: string;
  temper_gp?: number;    // what a smith charges to reforge this one
}

export interface SpellEntry { name: string; level?: number | null; }
export interface ItemAction { id: string; label: string; }
export interface ItemCharges { current: number; max: number; }

export interface ItemDetail {
  name: string;
  type?: string;
  rarity?: string;
  attunement?: boolean;
  description?: string;
  stats?: string[];
  image?: string | null;
  // interactions
  interactive?: string;         // special widget: "spellbook" | "container"
  actions?: ItemAction[];       // quick buttons (equip/attune/expend/use…)
  charges?: ItemCharges;
  equipped?: boolean;
  attuned?: boolean;
  // spellbook widget
  spells?: SpellEntry[];
  can_inscribe?: boolean;
  // container widget
  contents?: { name: string; qty?: number }[];
  // rolled properties + what they add up to
  affixes?: AffixRow[];
  bonuses?: Record<string, unknown>;
}

/** One saved portrait look: the base + up to 3 equipped-gear variants. */
export interface PortraitLook {
  context: string;          // "portrait" (base) or "portrait-gear-*"
  label: string;            // human name (the equipped loadout it was saved under)
  image_id?: number | null; // for a thumbnail via /imagery/image/{id}?thumb=true
  is_base: boolean;
}

export interface SheetData {
  /** Which character this sheet is — identifies your token on the board. */
  character_id?: number | null;
  name: string;
  subtitle: string; // "Level 3 Ranger (Gloom Stalker) · Custom Lineage"
  hp: number;
  hp_max: number;
  temp_hp?: number;   // temporary hit points — shown as a white overhang on the HP bar
  ac: number;
  stats: Record<string, number>; // STR..CHA
  skills: string[];
  inventory: (string | InventoryItem)[]; // strings (legacy) or rich item objects
  gold?: number;
  carried?: number;       // lb carried
  capacity?: number;      // lb you can carry (SRD: Strength score x 15)
  // ---- v1 additions (all optional; the UI degrades gracefully when absent) ----
  gender?: string | null;          // gender identity (free-form)
  race?: string | null;
  /** The cultural hand this character's own name is written in. */
  script?: Script;
  creature_type?: string | null;   // "Humanoid" for most; Construct/Undead/etc. for some species
  immunities?: string[];           // condition/effect immunities from species traits
  char_class?: string | null;
  subclass?: string | null;
  deity?: string | null;         // patron god (drives divine PvP retribution)
  dnr?: boolean;                 // Do-Not-Resuscitate: don't revive this character
  portrait?: string | null;      // data URL or /path to the stored PC portrait (active look)
  portrait_looks?: PortraitLook[]; // base + saved gear looks the player can switch between
  active_portrait?: string;      // context key of the currently shown look
  background?: string | null;    // origin / background name for the Origin tab
  spell_slots?: SpellSlotRow[];
  caster_mode?: string | null;   // "known" | "prepared" | "spellbook" | null
  can_reprepare?: boolean;       // true only right after a long rest (gate)
  resources?: ResourceRow[];     // class resources (Bardic Inspiration, Ki, …)
  features?: SheetFeature[];
}

/** GET reprepare_data — a prepared caster re-choosing spells on a long rest. */
export interface RepData {
  count: number; max_spell_level?: number; class: string;
  current: string[];          // currently-prepared spell slugs (pre-selected)
  options: SpellBrief[];
  source?: "class" | "spellbook";
  no_spellbook?: boolean;     // wizard with no spellbook item — nothing to prepare
}

export interface Ally {
  name: string;
  hp: number;
  hp_max: number;
  condition?: string;
}

/** One creature on the initiative tracker (mirrors combat.state()). */
export interface CombatantView {
  id: number;
  name: string;
  kind: string; // "pc" | "npc" | "monster"
  character_id?: number | null;
  initiative: number;
  max_hp: number;
  current_hp: number;
  temp_hp: number;
  armor_class?: number | null;
  cover?: string;            // none | half | three-quarters | total
  position?: string | null;  // spacing band: "melee with <name>" | "near" | "far"
  conditions: string[];
  concentration?: string | null;
  defeated: boolean;
  // per-turn economy (meaningful on the creature whose turn it is)
  action_used?: boolean;
  bonus_used?: boolean;
  reaction_used?: boolean;
  move_left?: number;
  dodging?: boolean;
  disengaging?: boolean;
}

/** Live encounter state for the initiative carousel (null = no fight). */
export interface CombatState {
  id: number;
  name: string;
  round: number;
  current_combatant_id: number | null;
  combatants: CombatantView[];
}

/* ---------------- tactical board (the vtt/ package) ---------------- */

/** A creature or object standing on the grid. */
export interface VttToken {
  id: number;
  name: string;
  kind: string;            // pc | npc | monster | object | marker
  team: string;            // party | foe | neutral
  x: number;               // top-left square of the footprint
  y: number;
  size: string;            // tiny…gargantuan
  squares: number;         // footprint side, in squares
  combatant_id?: number | null;
  character_id?: number | null;
  monster_slug?: string | null;
  image_id?: number | null;   // token art via /imagery/image/{id}?thumb=true
  color?: string | null;
  label?: string | null;
  speed_ft: number;
  reach_ft: number;
  moved_ft: number;
  movement_mode: string;   // walk | fly | swim
  elevation_ft: number;
  hidden: boolean;
  /** How this creature perceives, in feet: {darkvision: 60, blindsight: 10}. */
  senses?: Record<string, number>;
  /** Which floor this creature is standing on. 0 is the ground. */
  level?: number;
  mounted_on?: string | null;
  squeezing?: boolean;
  prone: boolean;
  defeated: boolean;
}

/** A spell area, aura, zone, wall, light or marker — squares are authoritative. */
export interface VttEffect {
  id: number;
  name: string;
  kind: string;            // area | zone | aura | wall | light | hazard | marker
  shape: string;
  x: number;
  y: number;
  radius_ft: number;
  length_ft: number;
  width_ft: number;
  direction_deg: number;
  squares: [number, number][];
  color?: string | null;
  opacity: number;
  icon?: string | null;
  difficult_terrain: boolean;
  blocks_sight: boolean;
  obscured?: string | null;
  /** Which floor this area is on. */
  level?: number;
  damage?: string | null;
  save_ability?: string | null;
  save_dc?: number | null;
  trigger?: string | null;
  source_token_id?: number | null;
  concentration: boolean;
  expires_round?: number | null;
}

/** One floor of a multi-storey board. */
export interface VttLevel {
  name: string;
  base_ft: number;
  terrain: string[];
  /** This floor's own memory, live sight and light — all per storey. */
  fog?: string[] | null;
  sight?: string[] | null;
  light?: string[] | null;
  stairs: { x: number; y: number; to: number; tx: number; ty: number; kind?: string }[];
}

export interface VttDoor { x: number; y: number; state: string; name?: string; dc?: number | null; }

/** A discrete object on its own square: pillar, crate, door, altar… */
export interface VttObject {
  x: number;
  y: number;
  code: string;             // tile code, e.g. "O"
  name: string;             // "pillar"
  label?: string;           // what the board writes on it
  /** For an aperture, which way its wall runs: "ew" | "ns" | "". A door is
   *  drawn as a panel along that line, never as a picture filling the square. */
  axis?: string;
  image_id?: number | null;
}

/** Wreckage left where something broke. */
export interface VttDebris {
  x: number;
  y: number;
  code: string;             // what the square became
  was?: string;             // what it used to be
  material?: string;
  label?: string;
  image_id?: number | null;
}

/** The whole board, as the overlay draws it. */
export interface VttScene {
  id: number;
  session_id: string;
  encounter_id?: number | null;
  name: string;
  kind: string;            // combat | puzzle | chase | hazard | explore | social
  archetype: string;
  width: number;           // squares
  height: number;
  square_ft: number;
  lighting: string;        // bright | dim | dark
  revision: number;
  active: boolean;
  round: number;
  current_token_id?: number | null;
  terrain: string[];       // one string per row, one tile code per square
  fog?: string[] | null;   // "1" seen / "0" unseen, or null for no fog
  /** Live line of sight, same shape as fog: "1" under someone's eye RIGHT NOW.
   *  Fog is memory and never dims; this is the second tier, and the difference
   *  is what closing a door behind you changes. Null when there's no fog. */
  sight?: string[] | null;
  /** Light level per square: "b" bright, "d" dim, "x" dark. Ambient plus any
   *  light sources, minus obscurement — the board's own answer, not the art's. */
  light?: string[] | null;
  /** Floors, ground first. A single-storey board reports exactly one. */
  levels?: VttLevel[];
  doors: VttDoor[];
  elevation: Record<string, number>;
  /** Discrete things standing on squares — read off the grid by the server. */
  objects?: VttObject[];
  /** What broke, and what it left. */
  debris?: VttDebris[];
  background_image_id?: number | null;
  art_status: string;      // none | pending | ready | offline
  description?: string;
  tokens: VttToken[];
  effects: VttEffect[];
  legend?: string;
}

/** Reachable squares for the selected token (server-costed). */
export interface VttOptions {
  token_id: number;
  budget_ft: number;
  squares: { x: number; y: number; cost: number }[];
}

export interface SubclassFeature {
  level: number;
  name: string;
  summary?: string;
}

export interface SubclassOption {
  name: string;
  slug: string;
  source?: string;
  features?: SubclassFeature[];
}

/** New cantrips/spells to pick when a caster gains a level (or null). */
export interface SpellsDue {
  cantrips: number; spells: number;
  mode?: string | null; max_spell_level?: number;
  cantrip_options: SpellBrief[]; spell_options: SpellBrief[];
  // Known casters may replace one known spell each level.
  can_swap?: boolean;
  current_spells?: { name: string; slug: string }[];
}

export interface LevelUpData {
  character_id: number;
  current_level: number;
  next_level: number;
  class: string;
  subclass?: string | null;
  subclass_required?: boolean;
  subclass_label?: string | null;
  notes: string[];
  class_features: { name: string; summary?: string }[];
  race_features?: { name: string; summary?: string }[];
  subclass_options: SubclassOption[];
  spells_due?: SpellsDue | null;
  /** True at an Ability Score Improvement level: the player owes either a
   *  score spread or a feat before the level lands. */
  asi_due?: boolean;
  /** The feats takeable at this ASI, prerequisites already judged. */
  asi_feats?: AsiFeat[];
  /** Current ability scores, so the picker can show and cap them. */
  abilities?: Record<string, number>;
}

/** A feat offered at an ASI level, with its eligibility already decided. */
export interface AsiFeat {
  slug: string; name: string; category?: string;
  prerequisite?: string | null; min_level?: number; brief: string;
  eligible: boolean; blocked_reason?: string | null;
  choices?: FeatChoice | null;
}

export interface CharacterSummary {
  id: number;
  name: string;
  race?: string | null;
  char_class?: string | null;
  subclass?: string | null;
  level: number;
  alive: boolean;
  resume_session?: string | null;
  /** Days of downtime commitment left before this PC is playable again. */
  returns_in?: number | null;
  /** Revived while its owner was away → DM-controlled; entering reclaims it. */
  reclaim?: boolean;
}

// ---- the Proving Grounds (practice bouts, outside the world) ----

export interface ArenaSlotChar {
  id: number; name: string; race?: string | null; char_class?: string | null;
  subclass?: string | null; level: number; hp: number; hp_max: number;
}

export interface ArenaSlot {
  slot: number;
  character: ArenaSlotChar | null;
  /** A copy already advanced to some level — "fight on" instead of re-climbing. */
  leveled: { id: number; name: string; level: number } | null;
}

export interface ArenaEnv {
  slug: string; name: string; domain: "land" | "sea" | "air";
  mode: "walk" | "swim" | "fly"; blurb: string; archetype: string;
}

export interface ArenaRun {
  slot: number;
  character_id: number;
  target_level: number;
  environment: string;
  difficulty: string;
  /** leveling → the climb; outfitting → the Quartermaster's stall;
   *  fighting → a live bout; resolved → it's over. */
  phase: "leveling" | "outfitting" | "fighting" | "resolved" | "idle";
  result?: "victory" | "defeat" | "over" | null;
  roster?: string;
  roster_reads?: string;
  roster_xp?: number;
  fights?: number;
  wins?: number;
}

/** One line on the Quartermaster's board. */
export interface ArenaStockItem {
  slug: string; name: string; cost_gp: number;
  kind: "gear" | "magic";
  category?: string | null; item_type?: string | null;
  rarity?: string; attunement?: boolean; brief?: string;
  /** Worn or wielded rather than stowed — offered with an "on you" toggle. */
  equippable: boolean;
}

/** A line in the cart, priced by the server (its prices are the real ones). */
export interface ArenaCartLine {
  slug: string; name: string; quantity: number;
  cost_gp: number; line_gp: number;
  equipped: boolean; attuned: boolean;
  kind: "gear" | "magic"; rarity?: string | null;
  attunement: boolean; equippable: boolean;
}

/** Gear the fighter already owns that can be strapped on before the bout. */
export interface ArenaPackItem {
  name: string; quantity: number;
  equipped: boolean; attuned: boolean; attunement: boolean;
  rarity?: string | null;
}

/** The stall: a stipend for the level being fought at, and what it buys. */
export interface ArenaShop {
  level: number;
  purse: number;
  spent: number;
  remaining: number;
  attunement_limit: number;
  attuned: number;
  items: ArenaStockItem[];
  cart: ArenaCartLine[];
  pack: ArenaPackItem[];
  rejected: string[];
}

/** What the client asks the stall for — the server prices it, not us. */
export interface ArenaOutfitLine {
  slug: string; name: string; quantity: number;
  equipped?: boolean; attuned?: boolean;
}

/** How a fighter's own gear should be worn walking into the bout. */
export interface ArenaEquipLine {
  name: string; equipped: boolean; attuned: boolean;
}

export interface ArenaState {
  slots: ArenaSlot[];
  environments: ArenaEnv[];
  difficulties: string[];
  max_level: number;
  max_slots: number;
  run: ArenaRun | null;
  /** Only present while the stall is open (it carries the whole catalog). */
  shop?: ArenaShop | null;
}

export type ServerEvent =
  | { t: "hello"; channel: string; characters: CharacterSummary[] }
  | { t: "arena"; state: ArenaState }
  | { t: "lexicon"; entries: LexEntry[] }
  | { t: "player"; text: string; who?: string; secret?: boolean }
  | { t: "narration"; text: string; secret?: boolean }
  | { t: "speech"; text: string; who?: string; portrait?: string;
      script?: Script; secret?: boolean }
  | { t: "whisper"; text: string }
  | { t: "roll"; roll: RollResult }
  | { t: "sheet"; sheet: SheetData }
  | { t: "locale"; locale: Locale }
  | { t: "suggest"; actions: string[] }
  | { t: "routes"; routes: RouteRow[] }
  | ({ t: "chronicle_data" } & ChronicleData)
  | ({ t: "reprepare_data" } & RepData)
  | { t: "party"; members: Ally[] }
  | { t: "combat"; encounter: CombatState | null }
  | { t: "vtt"; scene: VttScene | null }
  | ({ t: "vtt_options" } & VttOptions)
  | { t: "vtt_preview"; token_id: number; ok: boolean; path?: [number, number][];
      cost_ft?: number; remaining_ft?: number; within_budget?: boolean;
      opportunity?: string[]; reason?: string }
  | { t: "vtt_ping"; x: number; y: number; label?: string }
  | { t: "vtt_error"; detail: string }
  | { t: "scene"; url: string }
  | { t: "item_detail"; item: ItemDetail }
  | { t: "item_image"; name: string; url: string }
  | { t: "item_art_state"; name: string; state: "pending" | "describe" }
  | { t: "item_error"; detail: string }
  | { t: "item_gone"; name: string }
  | { t: "levelup"; data: LevelUpData | null }
  | { t: "entered"; resumed: boolean; arena?: boolean }
  | { t: "cc_done"; name: string; detail?: unknown }
  | { t: "cc_error"; detail: string }
  | { t: "join_blocked"; reason: string; travel_days?: number; away_days?: number }
  | { t: "table_invite"; place: string; channel: string }
  | { t: "rate_limited"; wait: number }
  | { t: "busy"; on: boolean };

export type ClientEvent =
  | { t: "action"; text: string; private?: boolean }
  | { t: "levelup_apply"; subclass?: string; cantrips?: string[]; spells?: string[];
      swap_out?: string; swap_in?: string;
      // ASI levels: exactly one of these two.
      ability_increases?: Record<string, number>;
      feat?: string; feat_choices?: FeatPicks }
  | { t: "reprepare" }
  | { t: "chronicle" }
  | { t: "describe_item"; name: string; text: string; title?: string }
  | { t: "temper_item"; name: string; affix: string }
  | { t: "reprepare_apply"; spells: string[] }
  | { t: "enter"; character_name?: string; solo?: boolean }
  | { t: "cc_register"; payload: CCPayload }
  // ---- the Proving Grounds ----
  | { t: "arena_state" }
  | { t: "arena_create"; slot: number; payload: CCPayload }
  | { t: "arena_delete"; slot: number }
  | { t: "arena_begin"; slot: number; environment: string; level: number;
      difficulty: string; reuse?: boolean }
  | { t: "arena_fight"; environment?: string; difficulty?: string }
  | { t: "arena_shop"; environment?: string; difficulty?: string }
  | { t: "arena_outfit"; cart: ArenaOutfitLine[]; equip: ArenaEquipLine[] }
  | { t: "arena_leave" }
  | { t: "inspect_item"; name: string }
  | { t: "inscribe_spell"; spell: string; book?: string }
  | { t: "item_action"; name: string; action: string; target?: string }
  | { t: "portrait_action"; action: "regear" | "select" | "delete";
      context?: string; replace_context?: string; detail?: string }
  | { t: "set_dnr"; dnr: boolean }
  // ---- tactical board ----
  | { t: "vtt_options"; token_id: number; dash?: boolean }
  | { t: "vtt_preview"; token_id: number; x: number; y: number }
  | { t: "vtt_move"; token_id: number; x: number; y: number }
  /** Use the connector under my token. No square: you take the stair you are
   *  standing on, and the server checks that you are standing on one. */
  | { t: "vtt_stairs" }
  | { t: "vtt_ping"; x: number; y: number; label?: string };

export interface CCPayload {
  name: string;
  race: string;
  char_class: string;
  background: string;
  stats: Record<string, number>;
  skills: string[];
  feats?: string[];
  gear_mode?: "kit" | "buy";
  bought_items?: { name: string; quantity: number }[];
  wondrous_item?: string;
  deity?: string;
  gender?: string;
  // Spell slugs chosen at creation (class list + Magic Initiate). cantrips =
  // level-0 picks, spells = level-1 picks.
  cantrips?: string[];
  spells?: string[];
  // Feat-choice proficiencies (Musician/Crafter tools, faction-feat languages).
  tools?: string[];
  languages?: string[];
  // Named feat picks that aren't proficiencies (a damage resistance, a giant
  // strike). Filed under the choice's own tag prefix on the sheet.
  feat_options?: string[];
}

/** Level-1 spellcasting info for a class (null for non-casters). */
export interface Spellcasting {
  ability: string; cantrips: number; spells: number;
  mode: "known" | "prepared" | "spellbook";
}

/** A feat's choice, asked at creation or when taken at an ASI level (null when
 *  the feat needs none). */
export interface FeatChoice {
  kind: "skills" | "tools" | "ability" | "language" | "magic_initiate"
      | "options" | "asi";
  n?: number; cantrips?: number; spells?: number;
  classes?: string[]; hint?: string;
  // skills/ability/options: an explicit subset; tools: a group ("instrument"|
  // "artisan"|"any") or an explicit list.
  from?: string | string[];
  amount?: number;   // ability: +N added to the chosen ability's score
  max?: number;      // ability: the score ceiling (20, or 30 for epic boons)
  total?: number;    // asi: points to spend (2)
  save_proficiency?: boolean;   // ability: also grants that save (Resilient)
  /** A second choice the same feat asks for (e.g. Dragonscarred's resistance). */
  also?: FeatChoice | null;
}

/** The picks a player has made for one feat, sent back with the level-up. */
export interface FeatPicks {
  skills?: string[];
  tools?: string[];
  languages?: string[];
  options?: string[];
  ability?: string;
  ability_increases?: Record<string, number>;
  cantrips?: string[];
  spells?: string[];
}

/** One spell in a pick list (GET /cc/spells/{class}). */
export interface SpellBrief {
  slug: string; name: string; level: number; school?: string | null;
  concentration?: boolean; ritual?: boolean; brief?: string;
}

/** GET /cc/spells/{class} response. */
export interface CCSpells {
  caster: boolean; class: string;
  cantrips_n: number; spells_n: number;
  ability?: string | null; mode?: string | null;
  cantrips: SpellBrief[]; spells: SpellBrief[];
}

/** GET /cc/options response (deterministic CC data from the rules DB). */
export interface CCOptions {
  races: {
    slug: string; name: string;
    ability_bonuses: Record<string, number>;
    choose_bonus: number[];
    speed: number; size: string; darkvision: boolean;
    creature_type?: string; immunities?: string[];
    languages?: string | null; traits: string[];
    // 2024: flavor sub-species (no ASI) and any species-granted feat choice.
    lineages?: { slug: string; name: string; traits: string[];
                 darkvision?: boolean; speed?: number }[];
    lineage_label?: string | null;
    /** The cultural hand this species' names are written in. */
    script?: Script;
    feat_choice?: "origin" | "any" | null;
  }[];
  classes: {
    slug: string; name: string; hit_die?: number | null;
    primary_ability?: string | null;
    spellcasting_ability?: string | null;
    saving_throws: string[];
    skill_choices_n: number; skill_options: string[];
    spellcasting?: Spellcasting | null;
  }[];
  feats: { slug: string; name: string; category?: string;
           prerequisite?: string | null; min_level?: number; brief: string;
           choices?: FeatChoice | null }[];
  backgrounds: {
    slug: string; name: string; skills: string[];
    feature?: string | null; abilities?: string[];
    origin_feat?: string | null;
  }[];
  ability_methods: {
    standard_array: number[];
    point_buy: { budget: number; min: number; max: number; costs: Record<string, number> };
    roll: { expr: string; count: number };
  };
  common_items: {
    slug: string; name: string; item_type?: string | null;
    attunement: boolean; brief: string;
  }[];
  buyable_items: { slug: string; name: string; category?: string | null; cost_gp: number }[];
  starting_gold: { by_class: Record<string, number>; default: number };
  /** The world's living powers, by family. Absent on older backends — the
   *  patron field falls back to free text when it is. */
  deities?: Pantheon;
}

/** A god, archfey, archdevil, demon prince or elder power a character may name. */
export interface Power {
  slug?: string | null;
  script?: Script;
  name: string;
  title?: string;
  alignment?: string;
  domains?: string;
  symbol?: string;
  blurb?: string;
  family: string;
  family_label?: string;
  power_class?: string;
  /** temples (prayed to) · cults · pacts (bargained with) · allies. */
  worship?: string;
  plane?: string;
  /** Risen in play through a divine event rather than seeded with the world. */
  risen?: boolean;
  born_day?: number | null;
}

export interface PowerFamily {
  key: string; label: string; plane?: string; power_class?: string;
  worship?: string; blurb?: string; count: number; script?: Script;
}

export interface Pantheon {
  families: PowerFamily[];
  powers: Power[];
}
