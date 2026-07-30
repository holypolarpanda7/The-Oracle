/** Typed events on the session WebSocket (server -> client). */
export type LexKind = "name" | "magic" | "item" | "place";

export interface LexEntry {
  text: string;
  kind: LexKind;
}

export interface RollResult {
  expr: string;
  label?: string;
  dc?: number;
  total: number;
  detail?: string; // e.g. "d20:14 +5"
  success?: boolean; // undefined when no DC (plain damage roll)
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
  // ---- v1 additions (all optional; the UI degrades gracefully when absent) ----
  gender?: string | null;          // gender identity (free-form)
  race?: string | null;
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
  damage?: string | null;
  save_ability?: string | null;
  save_dc?: number | null;
  trigger?: string | null;
  source_token_id?: number | null;
  concentration: boolean;
  expires_round?: number | null;
}

export interface VttDoor { x: number; y: number; state: string; name?: string; dc?: number | null; }

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
  doors: VttDoor[];
  elevation: Record<string, number>;
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
  /** leveling → the climb; fighting → a live bout; resolved → it's over. */
  phase: "leveling" | "fighting" | "resolved" | "idle";
  result?: "victory" | "defeat" | "over" | null;
  roster?: string;
  roster_reads?: string;
  roster_xp?: number;
  fights?: number;
  wins?: number;
}

export interface ArenaState {
  slots: ArenaSlot[];
  environments: ArenaEnv[];
  difficulties: string[];
  max_level: number;
  max_slots: number;
  run: ArenaRun | null;
}

export type ServerEvent =
  | { t: "hello"; channel: string; characters: CharacterSummary[] }
  | { t: "arena"; state: ArenaState }
  | { t: "lexicon"; entries: LexEntry[] }
  | { t: "player"; text: string; who?: string; secret?: boolean }
  | { t: "narration"; text: string; secret?: boolean }
  | { t: "whisper"; text: string }
  | { t: "roll"; roll: RollResult }
  | { t: "sheet"; sheet: SheetData }
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
  worship?: string; blurb?: string; count: number;
}

export interface Pantheon {
  families: PowerFamily[];
  powers: Power[];
}
