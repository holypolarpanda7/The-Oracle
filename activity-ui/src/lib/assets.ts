/* Themeable art assets keyed by a character's race / class / subclass.
 *
 * Only Bard + Dragonborn art exists today; everything else falls back
 * gracefully (null -> the CSS solid-panel / no-crest look). To grow the
 * library, drop a file into public/assets/{crests,backgrounds} and add a line
 * to the maps below — no other code changes needed. */

const CRESTS: Record<string, string> = {
  bard: "/assets/crests/bard.webp",
};

const RACE_BG: Record<string, string> = {
  dragonborn: "/assets/backgrounds/race-dragonborn.jpg",
};

const norm = (s?: string | null): string => (s || "").trim().toLowerCase();

/** Class emblem for the character-sheet top edge (subclass flavor later). */
export function crestFor(charClass?: string | null, subclass?: string | null): string | null {
  const cls = norm(charClass);
  // Prefer a class+subclass-specific crest when one exists, else the class one.
  return CRESTS[`${cls}/${norm(subclass)}`] ?? CRESTS[cls] ?? null;
}

/** Character-sheet background texture for a race (else a solid panel). */
export function raceBgFor(race?: string | null): string | null {
  return RACE_BG[norm(race)] ?? null;
}

/** Best-effort race/class from a sheet subtitle when structured fields are
 * absent, e.g. "Level 5 · Dragonborn Bard" or "Level 3 Ranger (Gloom Stalker)
 * · Custom Lineage". Only used as a fallback for asset theming. */
export function parseSubtitle(subtitle?: string | null): {
  race?: string; charClass?: string; subclass?: string;
} {
  const out: { race?: string; charClass?: string; subclass?: string } = {};
  if (!subtitle) return out;
  const sub = subtitle.match(/\(([^)]+)\)/);
  if (sub) out.subclass = sub[1].trim();
  // Known class names we might see; scan the string for one.
  const CLASSES = ["barbarian", "bard", "cleric", "druid", "fighter", "monk",
    "paladin", "ranger", "rogue", "sorcerer", "warlock", "wizard", "artificer",
    "blood hunter", "illrigger", "gunslinger"];
  const lower = subtitle.toLowerCase();
  out.charClass = CLASSES.find((c) => lower.includes(c));
  // Race guess: the word right before the class token ("Dragonborn Bard").
  if (out.charClass) {
    const re = new RegExp(`([A-Za-z-]+)\\s+${out.charClass}`, "i");
    const m = subtitle.match(re);
    if (m && m[1].toLowerCase() !== "level") out.race = m[1].trim();
  }
  return out;
}
