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
  race?: string | null;
  creature_type?: string | null;   // "Humanoid" for most; Construct/Undead/etc. for some species
  immunities?: string[];           // condition/effect immunities from species traits
  char_class?: string | null;
  subclass?: string | null;
  deity?: string | null;         // patron god (drives divine PvP retribution)
  portrait?: string | null;      // data URL or /path to the stored PC portrait (active look)
  portrait_looks?: PortraitLook[]; // base + saved gear looks the player can switch between
  active_portrait?: string;      // context key of the currently shown look
  background?: string | null;    // origin / background name for the Origin tab
  spell_slots?: SpellSlotRow[];
  resources?: ResourceRow[];     // class resources (Bardic Inspiration, Ki, …)
  features?: SheetFeature[];
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
}

export type ServerEvent =
  | { t: "hello"; channel: string; characters: CharacterSummary[] }
  | { t: "lexicon"; entries: LexEntry[] }
  | { t: "player"; text: string; who?: string; secret?: boolean }
  | { t: "narration"; text: string; secret?: boolean }
  | { t: "whisper"; text: string }
  | { t: "roll"; roll: RollResult }
  | { t: "sheet"; sheet: SheetData }
  | { t: "party"; members: Ally[] }
  | { t: "combat"; encounter: CombatState | null }
  | { t: "scene"; url: string }
  | { t: "item_detail"; item: ItemDetail }
  | { t: "item_image"; name: string; url: string }
  | { t: "item_error"; detail: string }
  | { t: "item_gone"; name: string }
  | { t: "levelup"; data: LevelUpData | null }
  | { t: "entered"; resumed: boolean }
  | { t: "cc_done"; name: string; detail?: unknown }
  | { t: "cc_error"; detail: string }
  | { t: "join_blocked"; reason: string; travel_days?: number; away_days?: number }
  | { t: "table_invite"; place: string; channel: string }
  | { t: "rate_limited"; wait: number }
  | { t: "busy"; on: boolean };

export type ClientEvent =
  | { t: "action"; text: string; private?: boolean }
  | { t: "levelup_apply"; subclass?: string }
  | { t: "enter"; character_name?: string; solo?: boolean }
  | { t: "cc_register"; payload: CCPayload }
  | { t: "inspect_item"; name: string }
  | { t: "inscribe_spell"; spell: string; book?: string }
  | { t: "item_action"; name: string; action: string; target?: string }
  | { t: "portrait_action"; action: "regear" | "select" | "delete";
      context?: string; replace_context?: string; detail?: string };

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
  }[];
  feats: { slug: string; name: string; category?: string;
           prerequisite?: string | null; min_level?: number; brief: string }[];
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
}
