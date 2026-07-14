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

export type ServerEvent =
  | { t: "lexicon"; entries: LexEntry[] }
  | { t: "player"; text: string; who?: string }
  | { t: "narration"; text: string }
  | { t: "roll"; roll: RollResult }
  | { t: "sheet"; sheet: SheetData }
  | { t: "party"; members: Ally[] }
  | { t: "scene"; url: string }
  | { t: "busy"; on: boolean };

export type ClientEvent = { t: "action"; text: string };
