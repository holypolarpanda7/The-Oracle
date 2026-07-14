import type { LexEntry, LexKind } from "./types";

export interface Span {
  text: string;
  cls?: string; // hl-* class, undefined = plain
}

const KIND_CLS: Record<LexKind, string> = {
  name: "hl-name",
  magic: "hl-magic",
  item: "hl-item",
  place: "hl-place",
};

// Damage/healing numbers are structural, not lexicon-driven:
// "7 fire damage", "takes 12 damage", "regains 9 hit points".
const DMG_RE =
  /\b(\d+)(?=\s+(?:\w+\s+)?damage\b)|\b(\d+)(?=\s+(?:hit points?|HP)\b)/gi;

const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Split narration text into styled spans using the session lexicon.
    Longest names first so "Gloom Stalker" beats "Gloom". */
export function markText(text: string, lexicon: LexEntry[]): Span[] {
  const byLen = [...lexicon]
    .filter((e) => e.text.length >= 3)
    .sort((a, b) => b.text.length - a.text.length);
  const lexRe = byLen.length
    ? new RegExp(`\\b(${byLen.map((e) => esc(e.text)).join("|")})s?\\b`, "gi")
    : null;
  const kindOf = new Map(byLen.map((e) => [e.text.toLowerCase(), e.kind]));

  // Collect all matches (lexicon + damage numbers), resolve overlaps
  // by earliest start, then longest.
  interface M { start: number; end: number; cls: string; }
  const ms: M[] = [];
  if (lexRe) {
    for (const m of text.matchAll(lexRe)) {
      const base = m[1].toLowerCase();
      const kind = kindOf.get(base) ?? kindOf.get(base.replace(/s$/, ""));
      if (kind) ms.push({ start: m.index!, end: m.index! + m[0].length, cls: KIND_CLS[kind] });
    }
  }
  for (const m of text.matchAll(DMG_RE)) {
    const heal = m[2] !== undefined;
    const g = m[1] ?? m[2];
    ms.push({
      start: m.index!,
      end: m.index! + g.length,
      cls: heal ? "hl-heal" : "hl-damage",
    });
  }
  ms.sort((a, b) => a.start - b.start || b.end - a.end);

  const spans: Span[] = [];
  let pos = 0;
  for (const m of ms) {
    if (m.start < pos) continue; // overlapped by an earlier, longer match
    if (m.start > pos) spans.push({ text: text.slice(pos, m.start) });
    spans.push({ text: text.slice(m.start, m.end), cls: m.cls });
    pos = m.end;
  }
  if (pos < text.length) spans.push({ text: text.slice(pos) });
  return spans;
}
