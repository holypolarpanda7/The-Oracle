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

**Two phrasings in `summary` are READ BY THE ENGINE, not just shown to the DM.**
Write them deliberately — the same "read the feature text" door `_pc_defenses`
opens for a species' resistance and `_hands_gate` opens for War Caster.

* **Always-prepared spells.** `"Always-prepared domain spells: L3 Bane, Silence;
  L5 Dispel Magic"` reaches `_castable_lists`, which is the list telling the DM
  the PC may cast ONLY what is on it. Tiers are filtered by character level, so
  a level-3 cleric gets the L3 row and nothing above it. `"You always have X
  prepared"`, `"Add X as always-prepared …"`, `"You know the X spell"` and
  `"Cast X as a ritual"` are read the same way. `" and "` is both a separator
  and part of a name, so names are resolved longest-match against the spell
  table — "Augury and Detect Poison and Disease" is two spells, not three.
* **Damage defences.** `"Resistance to Cold damage"` becomes a real resistance
  in combat. Two traps, both measured: the sheet keeps only the **first 90
  characters** of a summary, so put the mechanical clause FIRST or it silently
  does nothing; and the matcher needs the keyword before EACH type, so
  "Resistance to Force and Radiant" grants Force alone — write "Resistance to
  Force damage and Resistance to Radiant damage". A CONDITIONAL defence ("while
  your rage is active") is correctly *not* picked up; state it in the text for
  the DM and let a condition carry it, the way Rage already does.

`scripts/subclass_overrides_smoke.py` checks all of this against whatever is in
the slot — it derives its expectations from the file, so it never carries book
data itself.

### Companion stat blocks in `summons_overrides.json`

A class-feature COMPANION (a familiar-like guardian a subclass grants) is the
same shape as a summoned spirit and lives in the same slot — omit `spells`,
since nothing casts it, and summon it by name with
`[[SUMMON: <name> | | <owner's class level>]]`. Its `level` is the owner's CLASS
level rather than a slot level; `materialize()` does not care which.

A printed "AC 13 + your Wisdom modifier" is `{"base": 13, "caster_mod": 1}`.
The modifier is never passed in — it is recovered from the save DC the caller
already computed (`DC = 8 + PB + ability`), so there is exactly one place it can
be wrong.

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

## `spell_lists_overrides.json` → `rules_spell.classes`  (loader: `ingest_spell_lists_overrides`)
```json
{
  "_note": "class slug -> spell slugs already in rules_spell",
  "artificer": ["cure-wounds", "detect-magic", "faerie-fire", "..."]
}
```
The odd one out: **additive, not an upsert.** A class list says which of the
spells *already in the DB* a class may cast, so the loader APPENDS the class to
each named spell's `classes` and leaves the rest alone. Setting `classes`
through `spells_overrides.json` instead would REPLACE the list — adding the
artificer to Cure Wounds would take it from the cleric — and would need one
full entry per spell to say one field. Slugs the DB doesn't carry are reported,
never created: a missing spell is a gap in the spell tables, and inventing an
empty row would hide it. Re-running is a no-op.

Use it for a class whose spell list the parsers never built. The artificer had
exactly ONE spell in the whole database until this existed, so an artificer PC
could not pick spells at creation and the Artificer Initiate feat had nothing
to offer.

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

## `summons_overrides.json` → `rules.summons` catalogue  (engine: `rules/summons.py`)
```json
{
  "slug": "fey-spirit", "name": "Fey Spirit", "noun": "fey",
  "spells": ["summon-fey"], "min_level": 3,
  "size": "Small", "type": "fey",
  "armor_class": {"base": 12, "per_level": 1},
  "hit_points": {"base": 30, "per_level": 10, "from": 3},
  "speed": {"walk": 40},
  "abilities": {"str": 13, "dex": 16, "con": 14, "int": 14, "wis": 11, "cha": 16},
  "senses": {"darkvision": 60, "passive_perception": 10},
  "condition_immunities": ["charmed"],
  "multiattack": {"per_level": 0.5},
  "traits": [{"name": "Fey Step", "desc": "...DC {dc}...", "only": ["mirthful"]}],
  "actions": [
    {"name": "Shortsword", "kind": "melee weapon", "reach_ft": 5,
     "damage_dice": "1d6", "damage_bonus": {"base": 3, "per_level": 1},
     "damage_type": "piercing",
     "extra_damage": [{"dice": "1d6", "type": "force"}]}
  ],
  "variants": {"fuming": {"label": "Fuming"}, "mirthful": {"label": "Mirthful"}}
}
```
The odd one out in the other direction: it is **not upserted into a table at
all**. A summoning spell's creature has no fixed numbers — "AC 11 + the level
of the spell", "your spell attack modifier to hit", "against your spell save
DC" — so the entry is a RECIPE, and `rules.summons.materialize()` writes a
concrete `rules_monster` row per (spirit, variant, slot level, caster) when the
spell is actually cast.

Every scaling line in every printed summon block is the same expression:
`{base, per_level, from}` → `base + per_level x (level - from)`, floored, and
never below `base`. A bare number is a constant. `{level}`, `{dc}` and
`{attack}` are substituted into trait and action prose.

`only` gates a trait or action to some variants ("Claws (Slaad Only)") —
that is where the books put the gate, so it is where it goes here; a `variants`
patch carries only what genuinely differs as a NUMBER (a Bestial Air spirit's
hit points and speed). `kind` must name the attack the way a stat block does
(`melee weapon`, `ranged spell`) because the combat engine parses reach and
range out of the rendered prose.

## `weapon_masteries_overrides.json` → nothing  (engine: `rules/mastery.py`)
```json
{
  "weapons": {
    "greatsword": "graze", "longsword": "sap", "dagger": "nick",
    "shortsword": "vex", "maul": "topple", "greataxe": "cleave"
  },
  "classes": {
    "fighter": {"1": 3, "4": 4, "10": 5, "16": 6},
    "barbarian": {"1": 2, "4": 3}, "ranger": {"1": 2, "9": 3},
    "paladin": {"1": 2, "9": 3}, "rogue": {"1": 2}, "monk": {"1": 2}
  },
  "tuning": {"push": {"distance_ft": 10}, "topple": {"save": "con"}}
}
```
The 2024 Weapon Mastery table. Like `summons_overrides.json` it is **not
upserted into a table** — it is read at runtime and cached once (restart after
editing). The MECHANISMS are committed in `rules/mastery.py`: the eight names
are structural (an on-miss rider, a save, an economy change), and the engine
applies them. What is book data — and therefore lives only here — is **which
mastery each weapon carries** and **how many a class may have active**.

**With this file absent, Weapon Mastery is simply OFF**, which is the correct
state for an SRD-only checkout: the open SRD carries no masteries at all. A
character then needs the numbers you supply here plus their own `mastery:`
tags, because choosing which ones you have is the player's, not the table's —
a class with the feature and no recorded picks gets none rather than a guess.

`nick` is the one that changes the action economy rather than an outcome: it
moves the Light property's extra attack out of the Bonus Action and into the
Attack action, which is the difference between two swings a turn and three.

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
