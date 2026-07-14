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

export interface SheetData {
  name: string;
  subtitle: string; // "Level 3 Ranger (Gloom Stalker) · Custom Lineage"
  hp: number;
  hp_max: number;
  ac: number;
  stats: Record<string, number>; // STR..CHA
  skills: string[];
  inventory: string[];
  gold?: number;
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
  subclass_options: SubclassOption[];
}

export type ServerEvent =
  | { t: "lexicon"; entries: LexEntry[] }
  | { t: "player"; text: string; who?: string }
  | { t: "narration"; text: string }
  | { t: "roll"; roll: RollResult }
  | { t: "sheet"; sheet: SheetData }
  | { t: "party"; members: Ally[] }
  | { t: "scene"; url: string }
  | { t: "levelup"; data: LevelUpData | null }
  | { t: "busy"; on: boolean };

export type ClientEvent =
  | { t: "action"; text: string }
  | { t: "levelup_apply"; subclass?: string };
