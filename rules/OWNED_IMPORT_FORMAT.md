# Owned-content import format (paste-and-translate)

The preferred way to add owned book content is **not** a per-book parser but a
one-time translation: paste the book text into a session, get back JSON in the
schema below, and drop it into the matching file under `owned_books/` (which is
**gitignored** — book-derived data never enters the repo, per CLAUDE.md).

Each `owned_books/<type>_overrides.json` is a JSON **array** of entries. Loaders
apply them with **top precedence** at backend startup (and at the end of
`uv run python -m rules.owned_ingest`), so they win over anything the bulk
parsers produced. Only keys **present** in an entry are written, so a partial
entry (e.g. correcting one monster's AC) leaves every other field intact. Every
entry needs a stable `slug` (kebab-case). `source` is optional (defaults to a
"local, book-derived — never committed" tag).

Bulk, SRD-covered content (spells, monsters, magic items) is still parsed by
`rules/owned_ingest.py`; these override files are the home for the **long tail**
(species, subclasses, backgrounds, book-specific feats) and for **stragglers**
the parsers miss.

---

## `species_overrides.json` → `rules_race`  (loader: `ingest_species_overrides`)
```json
{
  "slug": "changeling", "name": "Changeling",
  "size": "Medium", "speed": 30, "darkvision": false,
  "languages": "Common plus two more of your choice",
  "traits": ["Shape-Shifter: as an Action, ...", "..."],
  "lineage_label": "Shifter Lineage",
  "lineages": [{"slug": "beasthide", "name": "Beasthide", "traits": ["..."]}],
  "feat_choice": "any"
}
```
`lineages`/`lineage_label`/`feat_choice` optional. Species grant NO ability
bonuses (2024 model) — the loader forces `ability_bonuses={}`.

## `feats_overrides.json` → `rules_feat`  (loader: `ingest_feats_overrides`)
```json
{
  "slug": "harper-agent", "name": "Harper Agent",
  "category": "origin",           // origin | general | epic-boon | fighting-style
  "min_level": 1,                  // origin=1, general=4, epic-boon=19
  "prerequisite": "Level 4+; Harper Agent feat",   // or null
  "repeatable": false,
  "benefit": "Own-worded mechanical summary (terse; not book prose)."
}
```

## `backgrounds_overrides.json` → backend `_BACKGROUND_KITS`  (read directly, no DB)
```json
{
  "slug": "harper", "name": "Harper",
  "abilities": ["dex", "int", "cha"],     // ordered 3 (the +2/+1 or +1/+1/+1 spread)
  "feat": "Harper Agent", "origin_feat": "harper-agent",
  "skills": ["Performance", "Sleight of Hand"],
  "tool": "Disguise Kit",
  "items": [["Disguise Kit", 1], ["Rope", 1]]   // [name, qty]; option A gear
}
```
`origin_feat` must match a feat slug (add it to `feats_overrides.json` too).

## `subclasses_overrides.json` → `rules_subclass`  (loader: `ingest_subclasses_overrides`)
```json
{
  "slug": "bladesinger", "name": "Bladesinger",
  "class": "Wizard",                // class_slug auto-derived if omitted
  "description": "One-line concept.",
  "features": [
    {"level": 3, "name": "Bladesong", "summary": "Terse mechanical summary."},
    {"level": 6, "name": "Extra Attack", "summary": "..."}
  ]
}
```

## `spells_overrides.json` → `rules_spell`  (loader: `ingest_spells_overrides`)
```json
{
  "slug": "spray-of-cards", "name": "Spray of Cards",
  "level": 2, "school": "Conjuration",
  "casting_time": "1 action", "range": "Self (15-foot cone)",
  "duration": "Instantaneous",
  "components": ["V", "S", "M"], "material": "a deck of cards",
  "classes": ["bard", "sorcerer", "warlock", "wizard"],
  "concentration": false, "ritual": false,
  "desc": "Terse mechanical summary.",
  "higher_level": "+1d10 force per slot level above 2nd."
}
```
For book spells outside the PHB parser's reach.

## `monsters_overrides.json` → `rules_monster`  (loader: `ingest_monsters_overrides`)
```json
{
  "slug": "flameskull", "name": "Flameskull",
  "size": "Small", "type": "undead", "alignment": "Lawful Evil",
  "armor_class": 13, "hit_points": 40, "hit_dice": "9d6+9",
  "strength": 1, "dexterity": 17, "constitution": 13,
  "intelligence": 16, "wisdom": 10, "charisma": 11,
  "challenge_rating": 4, "xp": 1100,
  "speed": {"walk": 0, "fly": 40}, "senses": {"darkvision": 60},
  "damage_immunities": ["cold", "fire", "poison"],
  "condition_immunities": ["charmed", "frightened", "prone"],
  "special_abilities": [{"name": "Rejuvenation", "desc": "..."}],
  "actions": [{"name": "Fire Ray", "desc": "...", "attack": "+5", "damage": "3d6 fire"}]
}
```
Any `rules_monster` field may be set; list fields hold `{name, desc, ...}` objects.

## `items_overrides.json` → `rules_item`  (loader: `ingest_items_overrides`)
```json
{
  "slug": "flame-tongue", "name": "Flame Tongue",
  "category": "magic-item", "item_type": "Weapon (any sword)",
  "rarity": "rare", "requires_attunement": true,
  "cost_gp": 5000, "weight": 3,
  "damage_dice": "1d8", "damage_type": "slashing",
  "properties": ["versatile"],
  "desc": "Terse mechanical summary (own words for owned-book items)."
}
```
Weapons/armor may add the number fields (`two_handed_damage_dice`,
`range_normal`/`range_long`, `armor_class_base`, `armor_dex_bonus`,
`armor_max_dex_bonus`, `str_minimum`, `stealth_disadvantage`).

## `puzzles_overrides.json` → `rules_puzzle`  (loader: `ingest_puzzles_overrides`)
```json
{
  "slug": "perfect-hand", "name": "The Perfect Hand",
  "puzzle_type": "sorting",          // riddle | mechanism | pattern | sequence | deduction | environmental | trap | social
  "setting_tags": ["dungeon", "haunted-manor", "secret-passage"],
  "difficulty": "easy",              // free text ("easy", "deadly (levels 5-10)", ...)
  "check_dc": 15,                     // optional: DC of a check that substitutes for solving
  "premise": "What the DM reads aloud — the player-facing setup and any spoken clue.",
  "solution": "PRIVATE answer key — how it's solved. Never shown verbatim to players.",
  "hints": ["Skill (Investigation) DC 10: graded nudge #1.", "..."],
  "fail_state": "What happens on a wrong answer / giving up.",
  "reward": "What solving it yields (door opens, treasure, passage, lore)."
}
```
The library the DM brain draws puzzles from. The DM is fed `premise` + a private
`solution`; the backend holds the answer/hints and reveals `hints` one per failed
attempt so the LLM can't leak or forget the answer. Location gating (a world-graph
puzzle site whose `setting_tags` match) + a `[[PUZZLE: slug]]` hook decide *when*
one fires; live attempt/hint/solved state lives in the session, not this table.
