# Character creation, feats, subclasses & spellcasting

The 2024 creation and level-up flow, the one feat-choice schema, species
questions, subclass grants that speak to the engine, summons, concentration,
saving throws and spell components. Split out of `CLAUDE.md`; read this
before touching character creation, `_apply_feat`, `rules/subclass_grants.py`,
`rules/summons.py`, `rules/components.py` or `rules/metamagic.py`.

- **Level-up is gated on its choices** the same way the subclass pick is: an ASI
  level returns `asi_required` + `asi_feats` until the player sends either
  `ability_increases` or a `feat` (+ `feat_choices`). `FEAT_CHOICES` is the one
  schema for what a feat asks; `_apply_feat` is the one place it is applied, so
  creation and level-up can never drift. The UI half is `FeatChoices.tsx`.
  `also` carries any FURTHER question — one spec or a LIST, because Skill
  Expert asks three (an ability, a skill, and which skill gets Expertise) and
  a single `also` could only ever hold two.
- **A feat's spell pick is scoped by SCHOOL, and the server owns the filter.**
  `magic_initiate` picks a CLASS first, which cannot say "a level 1 Divination
  or Enchantment spell from anywhere" — so the `spells` kind takes `level` +
  `schools` and `GET /cc/feat_spells/{feat}` serves the pool, keeping creation
  and level-up on the same list. `granted` is what rides along free (Fey
  Touched's Misty Step): it is NOT a choice, so `n: 0` is a legal grant-only
  spec, and creation folds it in server-side via `_feat_granted_spells` —
  the client never sends a grant, so a grant can't be lost in the post.
  (`int(spec.get("n") or 1)` turns an explicit 0 back into 1; don't.)
- **The two feat slots at creation are INDEPENDENT.** A background's Origin
  feat and a species pick (Human's origin feat, Custom Lineage's free choice)
  are separate slots, and two things followed from treating them as one. A
  Giant Foundling could spend the Custom Lineage pick on the Strike of the
  Giants it was already granted — a choice made that buys nothing, since a
  feat's benefit is recorded once (both pickers now hide the other's feat
  unless it is `repeatable`, and `register_character` re-checks). And CC kept
  ONE flat bucket per choice kind, so two feats each wanting a skill, or each
  granting +1 to an ability, silently merged into one answer. Answers are
  keyed by feat slug (`Draft.featPicks`) and rendered by the SHARED
  `FeatChoiceFields`, so creation asks exactly what level-up asks.
- **"Any feat you qualify for" answers to the FEAT, not to the level.** A
  species slot whose pool is `feat_choice="any"` (Custom Lineage) waives the
  level a feat's category is filed behind — that gate is the class ASI
  schedule, and stepping outside it is the entire gift. Everything a character
  can genuinely fail at level 1 still applies: species, spellcasting, ability
  minimums. Two traps. The level is printed TWICE — as `min_level` AND inside
  the prerequisite prose ("Prerequisite: Level 4+, Dexterity 13+") — so a
  waiver that skips only the column waives nothing at all. And two feats keep
  their level whoever is asking (`_keeps_level_gate`): an EPIC BOON, because
  level 19 is what an epic boon is, and the straight ABILITY SCORE IMPROVEMENT,
  because it IS the schedule — a slot that exists to step outside the ASI
  schedule must not be able to buy a turn of it at level 1. The
  slot is told apart server-side by `_species_free_feat`: the background's
  Origin feat is granted and known, so what is left is the species'.
- **A prerequisite that names a FEAT is a prerequisite.** The client's mirror of
  `_feat_prereq_met` judged ability minimums and spellcasting and nothing else,
  so every giant feat read as free in a slot that waives the level — what
  actually gates Vigor of the Hill Giant is Strike of the Giants AND the strike
  it chose. The mirror now reads prerequisites the way the server does:
  REQUIREMENTS (`;` / ` and `) of ALTERNATIVES (`or`), each judged as met /
  unmet / unparseable, with unparseable always allowed. It is a mirror and not
  a second answer — the server still re-checks, and a false BLOCK would be
  worse than the false allow it replaces.
- **The CC payload is built in ONE place (`_cc_request`), because it was built
  in two.** The Activity's own path and the Proving Grounds' each assembled a
  `RegisterCharacterRequest` by hand and each forgot a different half: an
  Activity wizard arrived with no spells, no tools, no languages and no feat
  picks — the screens asked, and nothing carried the answers.
- **A keepsake can be made the player's OWN at creation.** The free wondrous
  item takes a name and a description, which renames it on the sheet (keeping
  `base`, or every stat lookup breaks — the rule that already caught a suit of
  armour once) and draws it for that character alone, in a background thread:
  creation must never wait on a GPU, and the piece is on the sheet with its
  name and words either way. Same path as `describe_item` during play, and the
  shared catalogue art is untouched.
- **An ORIGIN is world state, not prose.** The background stage asks — all of
  it optional — for a HOMELAND and a PEOPLE, offering what the world already
  has first (`GET /cc/origins`), because a second character out of the Ashen
  Coast makes both of them mean more, while a name the player invents becomes a
  real PLACE or FACTION entity with a real edge (`PART_OF` / `MEMBER_OF`) the
  DM can use. The PC's world entity is created at registration when a tie needs
  it; `place_pc` finds that same entity by (owner, name) later, so it is the
  same character rather than a second one. The BACKSTORY beside it is text and
  stays text — `Character.backstory`, shown to the DM with the sheet.
- **The likeness comes BEFORE the seal, and that needed a DRAFT portrait.** It
  used to be the last stage, on the reasoning that a portrait needs a sealed
  character to draw against — true of the ENDPOINT, and it made the face read
  as a screen bolted onto the end: a character could be sealed with no likeness
  at all and nothing said so. So the wizard mints a token per run
  (`Draft.portraitToken`), `/cc/portrait/draft` renders against a
  character-shaped STAND-IN built from the draft (`_draft_character` — never
  added to a session, because `_portrait_base_look` and `_portrait_face` want
  attributes and not a row), the picture is filed under `cc-draft-<token>`, and
  `register_character` ADOPTS it: `ImageStore.adopt_portrait_draft` renames the
  subject onto the character. Two things ride with it and must. A face nobody
  DESCRIBED is rolled off the draft token, which does not survive
  registration — so the rolled clause is PINNED as `Character.appearance`, or
  the next render (a gear look) rolls a different key and hands back a stranger
  in the right armour. And the player's WORDS are kept whether or not a picture
  came back, since a description typed while ComfyUI was down is still what
  every later likeness is built from. Sealing is now the LAST thing creation
  does, so Name & Seal becomes the way into the world and the stage rail locks
  once sealed; a `cc_error` after the seal is the world ENTRY failing.
- **A spell is not choosable off one sentence.** `_spell_brief_dict` sent a
  slug, a name, a school and the description's first sentence cut at 140
  characters, and the detail pane — which carries a species' whole trait list,
  a background's whole grant and a keepsake's whole text — had no branch for a
  spell at all, so the Spells stage showed "The ledger awaits your choices".
  The row now rides along whole (casting time, range, components WITH the
  material, duration, save/attack, description, upcast rule), which costs ~22-64
  KB at creation and ~335 KB for a level-17 wizard's full list — paid once per
  picker, against a round trip per hover. `SpellEntry` lives in
  `FeatChoices.tsx` beside the other pickers creation and level-up share, and
  the level-up overlay (which has no side pane) opens it under its own grid.
  A card the grid has LOCKED is dimmed rather than `disabled`, because a
  disabled button takes no pointer events and the spell you cannot take is the
  one you most want to read before swapping.
- **The seal page shows the WHOLE character, because it is the last look.** It
  listed race/class/background, the six scores, skills and a gear COUNT — a
  receipt, not something a player can check their work against. It now carries
  the level-1 numbers (HP, speed, initiative, proficiency, hit die,
  darkvision), saving throws with their totals, species and lineage traits in
  full, each feat WITH what it does and what it was answered, the background's
  feature and tool, gear itemised, the keepsake's own words, the origin ties,
  the unfinished business and the face. A SKILL's modifier is deliberately
  absent: the skill -> ability table is `rules/checks.py`'s and exists so
  nothing else computes a check, and a copy of it in the browser is a second
  answer waiting to drift.
- **A choice you can't see the whole of isn't a choice.** Two surfaces the CC
  never gave the player. A background card showed two skills, so its Origin
  feat, its tool, its feature and its gear were invisible until after the
  character existed — the detail panel now carries all of it (`/cc/options`
  serves `tool` and `items` for that, and nothing else reads them). And the
  Activity had no EXIT at all: an embedded Activity has no window chrome of its
  own, so closing it is an RPC call on the SDK instance that opened it —
  `session.closeActivity()`, which returns false in a plain browser tab (a page
  a script didn't open can't be closed) so the surface can say so rather than
  appear to ignore the click. The way out is on the landing and in the play
  status bar, and it asks first.
- **A trait that says "of your choice" is a QUESTION.** A human's Skillful
  skill, a Custom Lineage's darkvision-or-skill gift, and every "plus two
  languages of your choice" line were prose nothing asked about, so the sheet
  never recorded them. `SPECIES_CHOICES` uses the FEAT schema and the same
  `FeatChoiceFields`, asked on the Origin stage and gating it. Two halves, like
  the feats: LANGUAGES are derived from the species' own `languages` line (so a
  species with no schema still gets its picks, and the server narrows the pool
  by what it already speaks), everything else is a schema — SRD/house here,
  owned-book ones in `owned_books/species_choices.json`. Two additions to the
  schema: `when` hangs a follow-up off the option chosen above it (an either/or
  whose halves ask different things), and `grants_senses` turns a chosen option
  into the `sense:` tag the BOARD reads — `vtt/` must not have to know what a
  species is. The picks ride the payload's existing `skills`/`tools`/
  `languages`/`feat_options` fields, and a class skill the species already
  granted is struck off the class list.
- **A feat nobody can see does nothing.** Class and species features had a
  prompt block from the start; feats never did, and the Activity's Features tab
  listed neither. So a Metamagic Adept's two options, an Eldritch Adept's
  invocation and a Fighting Initiate's style — the entire point of taking those
  feats — lived as tags no reader ever opened. `character_feats()` is the one
  place a sheet's feats and their NAMED picks are read back (picks matched to
  the feat that OFFERED them, by its own `from` list, so two feats' options
  never cross); it feeds the prompt block, the sheet payload and the Features
  tab. Where an option names a feat the rules DB already carries, say
  `options_are_feats` and the pick BECOMES that feat — Fighting Initiate's
  styles are real `fighting-style` rows with their own benefit text, and as a
  bare word they were a label every feat-reader walked past.
- **A SUBCLASS speaks to the ENGINE, and five numbers were escaping the rule
  that the DM narrates and the code factors.** Each was decided from the CLASS
  table, so a subclass had no way to say anything — and three of the five broke
  SRD subclasses, not just book ones. `rules/subclass_grants.py` reads them out
  of the feature TEXT, the same door `_pc_defenses` and War Caster use.
  **Crit range**: `attack_roll` hard-coded `natural == 20`, so the Champion's
  Improved Critical — the entire point of the subclass — changed nothing. It
  takes `crit_on` (the threshold) and `crit_extra`, because **"a 7 as well as a
  20" is not a threshold** and reading it as one makes every roll of 8 a
  critical hit; "18-20" IS a range and must be expanded, which looks identical
  to "7 or 20" until you read the hyphen. A natural 1 still never crits.
  Applied to WEAPON attacks only — the SRD widens the range for "weapons and
  Unarmed Strikes", not for spell attacks.
  **Extra Attack**: granted by Bladesinger, the Valor and Swords bards and any
  book subclass, and every one of them attacked once forever.
  **Unarmored Defense**: `_compute_ac` named `barbarian` and `monk` literally,
  so a subclass setting its own base AC was worth nothing.
  **Third-caster slots**: Spellcasting is a SUBCLASS feature, so keying slots on
  the class gave the SRD's own Arcane Trickster and Eldritch Knight no spell
  slots at all. `THIRD_CASTER_SLOTS` is the table; a full or half caster is
  never demoted onto it.
  **Senses** are the one thing PERSISTED rather than derived — written as a
  `sense:` tag by `_sync_subclass_senses`, because `vtt/scene.py` reads a PC's
  senses off the character row with plain SQL on purpose and must never have to
  know what a subclass is. The better range wins, so a subclass never cancels
  the darkvision a species was born with.
  **Known data gap found doing this**: the Eldritch Knight's row came from the
  book parse, which dropped its Spellcasting feature entirely — repaired in the
  gitignored overrides slot, not in code.
- **A SUBCLASS's always-prepared spells are enforced, and its stated damage
  defences are real.** A domain/oath/circle/patron list is "you thereafter
  always have these prepared", and `_castable_lists` — the list that tells the
  DM the PC may cast ONLY what is on it — read `char.spells` and nothing else,
  so a Blood Domain cleric's Vampiric Touch was a spell the DM was instructed to
  REFUSE. `subclass_granted_spells` reads it out of the feature TEXT, the same
  door `_pc_defenses` opens for a species' resistance and `_hands_gate` opens
  for War Caster, so a subclass from a book the repo may not carry needs nothing
  added to a list. Tiers are filtered by character level, so a level-3 cleric
  gets the L3 row and nothing above it. **`" and "` is both a separator and part
  of a name** — names resolve longest-match against the spell table, or "Augury
  and Detect Poison and Disease" becomes three spells, one of them "Disease".
  Two traps on the DEFENCE half, both measured: the sheet keeps only the FIRST
  90 CHARACTERS of a feature summary, so a resistance written past that point
  silently does nothing; and the matcher wants the keyword before EACH type, so
  "Resistance to Force and Radiant" granted Force alone. A CONDITIONAL defence
  ("while your Innate Sorcery is active") is deliberately NOT picked up —
  granting it permanently would be a straight buff the book never gave, and the
  Rage precedent (a condition, not a trait) is how one is meant to work.
- **A class-feature COMPANION is a summon, and `caster_mod` is what let it be
  one.** A guardian whose block reads "AC 13 + your Wisdom modifier, HP 5 +
  five times your Ranger level" is exactly the recipe shape `rules/summons.py`
  already materializes — except that `scaled()` could express the level term and
  not the ability one. It takes `{"base": 13, "caster_mod": 1}` now, and the
  modifier is never passed in: it is recovered from the save DC the caller
  already computed (`DC = 8 + PB + ability`), so there is one place it can be
  wrong instead of one per caller. The companion goes in the summons slot with
  no `spells` key — nothing casts it — and its `level` is the owner's CLASS
  level, which `materialize()` does not distinguish from a slot level.
- **An OPTION may ask its own questions, and the Pacts are why.** In the 2024
  book the three Pact invocations carry NO prerequisite, so Eldritch Adept
  reaches them — and Pact of the Tome asks for three cantrips from any list AND
  two level-1 RITUALS, which is two questions hanging off one option. Three
  things made that expressible: `when` (the species-choice mechanism — a spec
  is skipped by `_apply_feat`, `_feat_choice_satisfied` and
  `_feat_granted_spells` unless its option was taken, so picking the Chain owes
  the Tome's questions nothing); `ritual: true` on `spells_by_school`, because
  "a level 1 ritual from any class's list" is sayable no other way; and a
  level-0 spell question answering into the **cantrips** bucket, so one feat's
  two answers stay two answers. `/cc/feat_spells/{feat}` returns a LIST of
  `picks` keyed by the spec's own index — the client matches an answer to its
  pool by that, never by position — with the old top-level fields repeating the
  first pick. What a pact GRANTS instead of asking (the Chain's Find Familiar,
  at will) is an `option_catalog.json` entry, which already reaches
  `_castable_lists`; nothing new was needed for it.
- **A named feat OPTION is a proper noun until something says what it does.**
  `owned_books/option_catalog.json` (gitignored, cached once — restart after
  editing) maps `tag -> option -> {cost, resource, grants_spell, at_will,
  desc}`, and `character_feats()` attaches it to every pick as `picks_detail`.
  Two consequences make the difference between a label and a rule: a feat may
  declare `grants_resource` and get a real pool in `_class_resources_for` —
  same key MERGES, so a sorcerer with Metamagic Adept has level+2 points in ONE
  pool, and a fighter with it has 2 points that `[[USE: Sorcery Points]]`
  actually spends; and an option granting an at-will spell reaches
  `_castable_lists`, because that list is the ENFORCEMENT ("the PC may cast
  ONLY these") and an at-will grant that never arrived read as a spell to
  refuse. `uv run python scripts/feats_smoke.py` pins all of it.
- **Metamagic is enforced, and `rules/metamagic.py` is where it is decided.**
  An option is a price, a CONDITION and a CHANGE; `[[METAMAGIC: Option |
  Spell]]` checks the PC knows it, that the spell qualifies, and that the
  points are there, then spends them and reports what changed — emitted BEFORE
  the `[[CAST]]`, so a refusal hasn't already burned the slot. Only one option
  to a casting unless the option declares `stacks`. **Known options are read
  off the `metamagic:` TAGS, never off the feats** — a sorcerer's options come
  from their class, and keying it to feats made the class feature the one way
  of having Metamagic that didn't work. The conditions are DECLARATIVE
  (`requires`/`effect` in the catalogue), so the engine is committable and the
  book's numbers are not. `casting_time`/`range`/`duration` are populated on
  every spell so those conditions are decided outright; save/attack/damage are
  populated on ~3% and are read from the description, where absence counts
  only if the description is long enough to be whole. **A condition that can't
  be evaluated becomes a line the DM confirms — never a silent refusal (which
  makes the feat unusable) and never a silent pass (which makes the rule
  decoration).**
- **An invocation grants what it declares.** `grants_skills`/`grants_tools`
  become proficiency tags, `uses` becomes a counted per-option pool, and
  `grants_senses` becomes a `sense:` tag the BOARD reads off the character row
  — `vtt/` must not have to know what an invocation is, or be able to read the
  owned-book catalogue. `devils_sight` is a real sense in `survival/light.py`
  rather than darkvision with a bigger number, because it beats MAGICAL
  darkness and darkvision explicitly cannot; the caller says which kind of
  heavy obscurement it is, and anything that doesn't know still blinds.
- **A summoning spell has no monster to add — it has a RECIPE.** "AC 11 + the
  level of the spell", "1d10 + 3 + the spell's level", "your spell attack
  modifier to hit": `Monster` holds fixed integers and can express none of it,
  so `[[COMBAT: add | Fey Spirit]]` found nothing in the bestiary and seated a
  10-HP blob with no AC, speed, senses or attacks — the spell resolved and the
  creature it conjured did not exist. `rules/summons.py` MATERIALIZES instead:
  given (spirit, variant, slot level, caster) it computes every number once and
  upserts a concrete `rules_monster` row, so the tracker, the board, senses and
  the combat engine — all of which already resolve creatures by slug — need no
  change at all. The rows are derived and deterministic, so an identical
  casting is a lookup and a stale one rebuilds identically. **Every scaling
  line in every printed block is one shape** (`base + per_level x (level -
  from)`, floored), which is why this is a data slot and not nine special
  cases. The caster's numbers are IN the slug: two casters of different skill
  summoning at the same level really do get different creatures. Engine
  committed, numbers gitignored (`owned_books/summons_overrides.json`), one
  self-authored generic in the repo — the `airships/` split. The hook is
  `[[SUMMON: spell | choice]]`, emitted AFTER `[[CAST]]`, which now records
  what actually went off and at what level: a spell that sputtered out conjures
  nothing, and the block is built from the slot really spent, never from a
  number the DM typed.
- **Concentration is now ROLLED, and a summon ends with it.** The DC has
  always been computed in `tracker.apply_damage` and the save was never made —
  it was reported as `[concentration check DC n pending]` and the flag stayed
  set forever, so a spell held through anything short of the caster dropping.
  The save is rolled where the DC already lived, because that is the one place
  all EIGHT of the engine's damage paths and the DM's own `[[COMBAT: damage]]`
  hook meet; the ability modifier — the only thing a Combatant row cannot
  supply — arrives as `con_save_mod_for`, the `bonus_dice_for` precedent, and
  with no callback installed the check is still merely reported, so nothing
  regresses. `set_concentration` is then the ONE place a conjured creature
  dies: every ending (a failed save, a drop, moving to another spell,
  re-casting the same one, the summoner going to 0) routes through it, so the
  dismissal is written once instead of at each. A spirit is marked DEFEATED,
  not deleted — that is already the state a spirit at 0 HP is in, and it is
  already mirrored to the board and skipped by the engine.
- **A saving throw is ability modifier PLUS proficiency, and `_save_mod` is the
  one place that is decided.** Every save site called `_ability_mod`, which
  leaves proficiency out — a level-11 sorcerer's Constitution save was rolled
  at +2 instead of +6, four points off on every concentration check, every Hold
  Person and every hazard. A PC's proficient abilities come from
  `rules_class.saving_throws` plus any `save:` TAG (Resilient has written that
  tag since feats were built and nothing ever read it, so the feat bought a
  bonus that reached no roll). A MONSTER uses the number its stat block PRINTS
  — that total already includes proficiency, so adding it again would double
  it — and falls back to the bare modifier where none is listed, which is the
  rule and not a gap. **Known gap:** the MM-2024 parser captured skills only,
  so book-parsed monsters carry no save proficiencies at all and roll
  unproficient; the 58 SRD-shaped stat blocks do get theirs. Recovering the
  rest means re-parsing, and the 2024 format folds the save into the ability
  line ("CON 20 +5 +10") rather than printing a Saving Throws row.
- **`Combatant.side` is how an ally exists at all.** The engine's rule was "PCs
  are one side, everything else the other", and a conjured spirit is a monster
  row that fights for the party — without the column a summoner's own creature
  provoked opportunity attacks from the party and counted as an enemy for
  flanking and Help. Unset still derives from `kind`, so every fight without
  allies plays exactly as before, and `vtt/bridge.py` reads the same field for
  a token's team so the board and the tracker cannot disagree. A summon takes
  the summoner's initiative AND dex tiebreak, which leaves `order()`'s last key
  (the row id) to place it immediately after — that is the rule ("shares your
  initiative count, but takes its turn immediately after yours"), not a hack
  around it.
- **A component is a COST, and `rules/components.py` is the one place it is
  read.** `Spell.components` and `Spell.material` were in the database from the
  first ingest and exactly one thing ever read them — the card-game cheating
  check, which asks whether a casting is *perceptible*. Nothing asked what it
  cost, and the spell brief printed casting time, range and duration but never
  components, so Revivify's 300 GP diamond was free, infinite, AND invisible.
  Three layers, all shipped: the brief and the DM's board now PRINT the
  component and its price; `[[CAST]]` REFUSES a costly component that isn't in
  the pack and destroys it when the spell says "which the spell consumes"; and
  a casting gate refuses a caster who can't act or can't speak. Cost and
  consumption are DERIVED from the book's prose, never stored beside it — a
  stored number and the sentence it came from drift the moment a re-parse
  improves one of them. The line for a focus is the PRICE, not the prose: a
  focus or component pouch replaces any material with no cost, however specific
  the description, and replaces none that has one.
- **An EMPTY inventory is unknown, not empty-handed.** The one deliberate hole
  in component enforcement: imported sheets routinely arrive with no pack, and
  a false refusal stops play dead where a missed enforcement only makes the
  game slightly generous. A pack with *something* in it is taken at its word.
