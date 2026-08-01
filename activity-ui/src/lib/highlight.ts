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

// Spoken lines inside the narration. Voice-coding dialogue is the single
// biggest legibility win in a wall of prose, but a quote usually CONTAINS
// lexicon matches, and spans are flat — so speech is tracked as character
// ranges and folded onto whatever class each span already earned, rather
// than being a span of its own that would swallow the highlights inside it.
const SPEECH_RE = /[“"]([^”"]{2,})[”"]/g;

function speechRanges(text: string): [number, number][] {
  const out: [number, number][] = [];
  for (const m of text.matchAll(SPEECH_RE)) {
    out.push([m.index!, m.index! + m[0].length]);
  }
  return out;
}

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
  // The cultural hand a name is set in, alongside its semantic colour.
  const scriptOf = new Map(
    byLen.filter((e) => e.script).map((e) => [e.text.toLowerCase(), e.script!]));

  // Collect all matches (lexicon + damage numbers), resolve overlaps
  // by earliest start, then longest.
  interface M { start: number; end: number; cls: string; }
  const ms: M[] = [];
  if (lexRe) {
    for (const m of text.matchAll(lexRe)) {
      const base = m[1].toLowerCase();
      const kind = kindOf.get(base) ?? kindOf.get(base.replace(/s$/, ""));
      const script = scriptOf.get(base) ?? scriptOf.get(base.replace(/s$/, ""));
      if (kind) {
        const cls = KIND_CLS[kind] + (script ? ` script-${script}` : "");
        ms.push({ start: m.index!, end: m.index! + m[0].length, cls });
      }
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

  // Speech boundaries are cut points too, so a span never straddles the
  // opening or closing quote and end up half-spoken.
  const quotes = speechRanges(text);
  const inSpeech = (i: number) => quotes.some(([a, b]) => i >= a && i < b);
  const cuts = new Set<number>();
  for (const [a, b] of quotes) { cuts.add(a); cuts.add(b); }

  const spans: Span[] = [];
  const push = (start: number, end: number, cls?: string) => {
    // Split plain runs at every quote boundary they cross.
    const inner = [...cuts].filter((c) => c > start && c < end).sort((a, b) => a - b);
    let from = start;
    for (const c of [...inner, end]) {
      if (c <= from) continue;
      const speech = inSpeech(from);
      const full = cls ? (speech ? `${cls} in-speech` : cls)
                       : (speech ? "hl-speech" : undefined);
      spans.push({ text: text.slice(from, c), cls: full });
      from = c;
    }
  };

  let pos = 0;
  for (const m of ms) {
    if (m.start < pos) continue; // overlapped by an earlier, longer match
    if (m.start > pos) push(pos, m.start);
    push(m.start, m.end, m.cls);
    pos = m.end;
  }
  if (pos < text.length) push(pos, text.length);
  return spans;
}
