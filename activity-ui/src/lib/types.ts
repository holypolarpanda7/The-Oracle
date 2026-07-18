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
}

export interface SheetData {
  name: string;
  subtitle: string; // "Level 3 Ranger (Gloom Stalker) · Custom Lineage"
  hp: number;
  hp_max: number;
  ac: number;
  stats: Record<string, number>; // STR..CHA
  skills: string[];
  inventory: (string | InventoryItem)[]; // strings (legacy) or rich item objects
  gold?: number;
  // ---- v1 additions (all optional; the UI degrades gracefully when absent) ----
  race?: string | null;
  char_class?: string | null;
  subclass?: string | null;
  portrait?: string | null;      // data URL or /path to the stored PC portrait
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
  | { t: "player"; text: string; who?: string }
  | { t: "narration"; text: string }
  | { t: "roll"; roll: RollResult }
  | { t: "sheet"; sheet: SheetData }
  | { t: "party"; members: Ally[] }
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
  | { t: "action"; text: string }
  | { t: "levelup_apply"; subclass?: string }
  | { t: "enter"; character_name?: string; solo?: boolean }
  | { t: "cc_register"; payload: CCPayload }
  | { t: "inspect_item"; name: string }
  | { t: "inscribe_spell"; spell: string; book?: string }
  | { t: "item_action"; name: string; action: string };

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
}

/** GET /cc/options response (deterministic CC data from the rules DB). */
export interface CCOptions {
  races: {
    slug: string; name: string;
    ability_bonuses: Record<string, number>;
    choose_bonus: number[];
    speed: number; size: string; darkvision: boolean;
    languages?: string | null; traits: string[];
  }[];
  classes: {
    slug: string; name: string; hit_die?: number | null;
    primary_ability?: string | null;
    spellcasting_ability?: string | null;
    saving_throws: string[];
    skill_choices_n: number; skill_options: string[];
  }[];
  feats: { slug: string; name: string; prerequisite?: string | null; brief: string }[];
  backgrounds: {
    slug: string; name: string; skills: string[];
    feature?: string | null; abilities?: string[];
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
