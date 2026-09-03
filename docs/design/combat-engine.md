# The combat engine, damage and equipment

Turn pacing and narration, damage types and resistance, legendary creatures,
spell scaling and riders, the loadout (what is worn, what is held, in which
hand), weapon mastery, and the OCR hazards in book-parsed rules data. Split
out of `CLAUDE.md`; read this before touching `combat/`, `rules/damage.py`,
`rules/equipment.py`, `rules/mastery.py` or `rules/spell_scaling.py`.

- **The music follows the TRACKER, not the DM remembering to mention it.**
  Nothing but the model's own `[[MUSIC:]]` cue ever moved a table's playlist,
  so initiative could be rolled, a board could come out and six creatures could
  start swinging over the same tavern lute — and on a lean or muted combat turn
  there is no cue at all. `_sync_combat_music` compares "is a fight live"
  against what the channel last heard and speaks only when the answer CHANGES,
  so it is safe to call from anywhere that already refreshes state. **A fight
  owns the music while it lasts**: a scene cue arriving mid-combat is
  remembered as what to go back to (`_set_activity_music(scene=True)` stores
  it) rather than played over the fight — without that rule the DM's own cue,
  landing one line after the encounter opened, put the lute straight back on.
  The backend never names a PLAYLIST: it sends words, and
  `music_control.mood_for_query` on the bot snaps them to a mood it has audio
  for, because that side is the only one that knows which moods exist.
  **That matcher tested bare substrings**, which scored "a WARm tavern" as
  combat and "a BARe arena" as tavern — both real cues. A keyword must now
  start a word, and a short one must end one too; the long entries are
  deliberate stems ("celebrat", "bustle", "settlement") and still catch their
  own endings. Same lesson as `setpieces.landmark_for` and `threads`' word
  boundaries.
- **The ENGINE and the NARRATION are two things at two speeds, and bundling
  them made the fast one wait.** A resolved turn takes milliseconds and a local
  model takes seconds, and they used to arrive together — so an Eldritch Blast
  that had already hit sat invisible until the prose caught up, which reads at
  the table as the spell not working. `_ACTIVITY_COMBAT` is a sink contextvar
  beside `_ACTIVITY_ROLLS` and `_ACTIVITY_STREAM`: when it is set, every
  resolved TURN is pushed the moment it lands (`_combat_step`), and the socket's
  `with_combat_log` refreshes the tracker and the board behind each one so
  tokens move WITH the log instead of jumping to the end. The Activity shows it
  in a pane of its own, in the engine's own certified text — no model wrote a
  word of it. **One creature per push, with a beat between**
  (`COMBAT_STEP_PAUSE_S`, paid only when somebody is watching): six monsters
  resolving into one frame is a diff, not a round of combat. Where a watcher
  exists the arena's opening narration DROPS the engine text it used to carry,
  or the round prints twice — once as it happened and once as history; a
  Discord table has no such pane, so there it stays in the narration.
  **`combat_narration` mutes the prose per TABLE** — the story is a commons and
  half a table reading a scene the other half never sees is not one table. Off
  by default, because a fight narrated well is most of why this exists.
- **A turn the ENGINE settled is a description job, and it gets a narrator's
  prompt rather than the Dungeon Master's.** Measured on a real Eldritch Blast
  through the action bar: **45,158 chars / ~11,300 tokens**, of which about
  4,000 was the board, the certified result and the narration contract — and
  the other **91% was instruction for things that turn's own contract
  forbids**. Ten thousand characters of it was the tactical hook vocabulary,
  teaching the model to move tokens and open boards on a turn where it may
  change nothing at all; 3,405 more listed spell slots the engine had already
  spent. Ingestion is roughly linear in length, so that was most of the wait a
  player reads as "the spell didn't work". `generate_dm_reply(lean_combat=)`
  short-circuits the whole assembly — everything below that branch exists so
  the model can DECIDE things, and here it decides nothing. Result: **4,759
  chars / ~1,190 tokens, an 89% cut**, and output is capped
  (`_COMBAT_NARRATION_MAX_TOKENS`) because generation is the other half of the
  wait and a local model left unbounded writes past the point the fight moved
  on. What a lean turn carries is decided in ONE place (`lean_ctx` in
  `chat_endpoint`), by adding blocks deliberately — never by filtering
  `ctx_texts` on their first line, which would break the moment somebody
  retitled one. **`_COMBAT_NARRATOR_SYSTEM` is a statement of ROLE and VOICE,
  not a second rulebook**: the narration contract travelling with the
  resolution block stays the authority on what to do with a REFUSED line, a
  frozen reaction or a still-open turn, and two prompts giving overlapping
  orders is the same fault as sending the player's sentence twice — which is
  the other thing fixed here. `_append_turn` records the player's line BEFORE
  narration is asked for, and `generate_dm_reply` then appended it again as
  the new user message, so every prompt carried it twice.
- **A spell with no `attack_type` and no `dc_type` went off dealing NOTHING.**
  The engine's two damage branches keyed on those columns, and they are
  populated on 7 and 20 rows of 431 — every spell here came out of a PDF, and
  the parser only ever filled them from the tidy SRD shape. A spell with
  neither fell past both branches: the slot was spent, the narration said
  something happened, and the target's hit points never moved.
  `rules.targeting.resolution_for` reads the column first and the spell's own
  prose after (the `rules/damage.py` doctrine again — derived, never stored),
  which takes it from 27 spells to **209**. OCR tolerance is the job here too:
  Inflict Wounds arrives as "Constit ution saving th row", so "saving throw"
  and "spell attack" are spelled out letter by letter, anchored on the closed
  vocabulary of six ability names and two attack ranges so it cannot drift onto
  ordinary prose.
- **A BAND is a relationship, so only its OWNER may be repositioned for it.**
  `reconcile_bands` walks any token whose tracker band disagrees with its
  square, which is right for a band the DM changed in narration and catastrophic
  for one that merely DRIFTED — when a crocodile closes on the party everyone's
  band changes and only the crocodile moved. The PC was dragged backwards two
  squares to restore a band nobody had set for them, and their own turn began
  somewhere they had never gone. `MapToken.band_synced` records the band the
  BOARD last wrote (in `sync_bands`), so a band somebody deliberately changed
  is told apart from one that drifted. Pinned in the selftest, which fails by
  exactly two squares without it.
- **Harm outside a fight lands too, and needs no initiative order.** A dog
  bites in a market street; that is not an encounter and nobody is going to
  roll for one. Every COMBAT verb but `start` used to be DROPPED when no
  encounter was live (`if enc is None: continue`), so the bite drew blood in
  the narration and changed nothing on the sheet — the DM's only honest
  options were to open a full tracker for one bite or to let the wound be
  imaginary. `damage`/`heal`/`temp` now reach the character sheet directly,
  with the SAME arithmetic: temp HP absorbs first, resistances apply, and
  dropping to 0 starts dying.
- **The DM narrates; the ENGINE factors. A number the model computed is a
  number nothing checked.** Three places were still asking the LLM for
  arithmetic, and all three are closed. `[[ROLL: Stealth | DC 15]]` names the
  check and `rules/checks.py` works out the modifier off the sheet — ability,
  proficiency, expertise (which DOUBLES it), 2024 exhaustion at -2 per level,
  a curse penalty. **The skill→ability table did not exist anywhere in the
  project**, which is exactly why the model was being asked: there was nothing
  to ask instead. The old `[[ROLL: 1d20+5 | Stealth | DC 15]]` still resolves
  so a table mid-session doesn't break. Deciding THAT a check happens and how
  hard it is stays the DM's; what a character's Stealth is worth does not.
- **`[[COMBAT: damage]]` takes a SOURCE, and the code supplies every number.**
  Name a spell or an attack of a creature in the fight (`| Fire Bolt`,
  `| Bite`) and the dice AND the damage type come off the row — the DM
  supplies only the fiction. `fall 30` is priced by the rules themselves (1d6
  bludgeoning per 10 ft, capped at 20d6). Dice are rolled; the field used to
  have its non-digits stripped, so a DM who wrote `2d6` dealt TWENTY-SIX, a
  failure that reads at the table as a hard fight rather than as a bug. A flat
  total is still accepted — reading a number off a page is transcription, not
  arithmetic — and a total WITH a type is resisted normally. What is never
  resisted is damage with NO TYPE, so that case is now reported to the DM
  rather than applied silently.
- **A foe the bestiary lacks is BASED on a real one.**
  `[[COMBAT: add | Cult Fanatic | like acolyte | tough]]` runs the bestiary row
  through `rules/templates.py`'s `scale_monster` — which is what that module
  was always for — and keeps the chassis' SLUG, so the new creature's
  resistances, senses and attacks all still resolve. Failing that, the name is
  searched against the bestiary (whole name first, then its significant words,
  because a search is a containment match and "Acolyte of the Deep" never
  matches the row called "Acolyte"). The creature is still SEATED when nothing
  matches — the fiction has already happened and refusing mid-fight stops play
  dead — but the invented numbers are named out loud with the fix beside them,
  where the old code seated a silent flat 10 HP and no AC.
- **Damage has a TYPE, and `rules/damage.py` is the one place it is reduced.**
  The engine dealt integers: a fire elemental took full damage from fire, a
  skeleton shrugged off a mace exactly as hard as a rapier, and a raging
  barbarian resisted nothing. Thirteen damage types were in the data and in
  none of the arithmetic. Reduction happens inside `tracker.apply_damage`,
  beside the concentration save and for the same reason — it is the one place
  all of the engine's damage paths and the DM's own hook meet. **`rolled` is a
  LIST of `(Packet, amount)`**, because a flame tongue's slashing and its fire
  meet a creature's defences separately and summing first is the mistake the
  signature exists to prevent. An UNTYPED amount passes through untouched,
  which is exactly what every caller got before types existed.
  A PC's defences come through `defenses_for`, the `con_save_mod_for`
  precedent: species trait TEXT is read for "Resistance to Fire damage" (the
  same door War Caster and the cartography check open), `resist:`/`immune:`/
  `vulnerable:` tags are the explicit record, and **Rage is neither** — it is a
  condition, resistance to the physical three while it lasts.
- **The bestiary holds defences in two incompatible shapes, and one of them is
  a trap.** Half was ingested from the open SRD (a tidy list, with prose
  qualifiers like "from nonmagical weapons"); half was parsed out of a PDF into
  ONE string with a semicolon in it — `"Fire,Poison;Exhaustion,Grappled"` —
  where everything after the semicolon is a CONDITION immunity sitting in the
  damage column. Read naively a skeleton comes out immune to "exhaustion
  damage". `parse_defenses` splits them, rescues bare condition lists that have
  no separator at all, and matches condition names tolerantly because the
  source contains `Petrifed`. 363 of 366 rows with defence data now parse; the
  three that don't are genuinely garbage (`'Damag'`, `'fre'`).
- **A boss was not a boss: legendary actions and Legendary Resistance were both
  invisible.** 59 monsters carry `legendary_actions` and the combat engine
  contained NO reference to the column — and `format_monster_brief` never
  printed it either, so a CR 14 dragon both fought like a brute and gave the DM
  nothing to run one with. Legendary RESISTANCE was worse because it changes
  outcomes rather than flavour: it lives as a sentence inside
  `special_abilities` ("Legendary Resistance (3/Day, or 4/Day in Lair)") and
  nothing read it, so every save-or-suck landed first try — Hold Monster simply
  worked on an ancient dragon. `rules/legendary.py` parses both out of the stat
  block's own OCR-damaged prose (the extractor writes "1f" for "If" and strips
  the space out of "3/Day,or"). The two halves are split ON PURPOSE:
  **resistance is ENFORCED** — `_legendary_rescue` turns a failed save into a
  success and spends a use, because that is arithmetic — while **actions are
  SURFACED**, because they are narrative options taken between other creatures'
  turns and the DM spends them through the ordinary hooks. Uses are tracked as
  a `legres:<spent>` condition, the mastery-rider precedent: a condition is
  already a string the tracker persists. The per-round action count is NOT in
  this bestiary's parse, so it defaults to 3 and says so rather than pretending
  to have read it.
- **A spell's LEVEL is printed as a GLYPH, and one glyph is ambiguous.** `l`
  and `I` are always a 1 in this extraction; **`J` is both 1 and 3** (Jump and
  Divine Smite are level 1, Slow and Clairvoyance are level 3), and reading it
  as a 1 filed Slow — a level 3 spell — on the level 1 list, where a fresh
  wizard could take it. Nothing downstream would ever notice: it has a level,
  the level is a number, and it is wrong. `_level_from_token` resolves an
  ambiguous glyph against the SRD's own clean text (`srd_spell_levels`, not an
  OCR of a scan) and only guesses when nothing can answer — out loud.
  `scripts/repair_spell_levels.py` is the standing audit over every row
  (`--apply` writes); across 292 checkable spells exactly one was wrong.
- **The extractor splits WORDS, and it splits spell NAMES — 20 spells were
  uncastable because nothing could find them.** The PDF pass writes "dam age"
  for damage (13 spells) and "He X" for Hex, "Witc H B O Lt" for Witch Bolt,
  "Chrom At Ic Orb" for Chromatic Orb. A player cannot cast a spell whose name
  the game does not recognise, and `get_spell("Hex")` returned None. Six of the
  twenty also exist correctly named — those mangled rows are DUPLICATES from a
  second parse pass and are deliberately left alone, since renaming one would
  put two spells of the same name in front of the player; the other fourteen
  are renamed in the gitignored overrides slot. Any pattern that reads book
  prose has to tolerate the split word: `_DAMAGE_WORD` accepts "dam age" and
  "da mage", or every rider written that way is silently dropped. The scan for
  these is cheap — a spell name with a one- or two-letter token in the middle,
  or a capital letter mid-word, is almost always mangled.
  `scripts/repair_book_names.py` is the tool (audit by default, `--apply` to
  write), and it needed THREE lessons from the data. The extractor SUBSTITUTES
  letters as well as inserting spaces — "brgbyshand" for "bigbyshand" — so
  subsequence matching finds nothing and similarity is the only thing that
  works. It corrupts the SCHOOL too (Prayer of Healing came back Abjuration),
  so a spaced-apart row may be matched on its LEVEL alone, with the MARGIN as
  the discriminator: every true twin scored 0.81-0.93 while its runner-up
  scored 0.42-0.53. And similarity ALONE is unsafe — "Invisibility" and "See
  Invisibility" are 0.889 alike and are two different spells — so a merely
  MIS-SPELLED duplicate ("Protection From Evil And Goon", which trips no
  spacing heuristic at all) is matched under a stricter rule that also requires
  the school to agree. Nothing is ever deleted unless the row that stays is
  proved at least as complete on every field AND longer in description —
  `rules_*` is not disposable, since the owned-book half only comes back from
  a full re-parse of the user's PDF library.
- **A buff spell that adds damage to your ATTACKS did nothing at all.** Spirit
  Shroud, Conjure Minor Elementals, Hunter's Mark, Hex, Divine Favor and
  Elemental Weapon deal no damage themselves — they add dice to your attacks
  for the duration, and the engine had no concept of "an active spell that
  modifies a later attack". Casting one from the action bar spent the slot, set
  concentration, and changed nothing: the whole point of the spell landed only
  if the DM remembered it. The machinery was already there and unused —
  `blessed` proves the shape (a condition on the attacker, read inside
  `_do_attack`), and a condition already carries STATE (`sapped:4`,
  `legres:3`). A rider now rides as `rider:<spell>:<dice>[:<type>][:<target>]`
  on the caster and is added as its OWN typed lump beside Sneak Attack and
  Smite. **No dice are written in the registry** — `_SPELL_EFFECTS` says only
  THAT a spell rides and what cannot be derived (an emanation's radius, whether
  it marks one creature); the numbers come from the spell row via
  `spell_scaling.rider_dice`, so an upcast scales them and a house rule in the
  overrides slot changes them. `parse_damage` cannot find these numbers, which
  is why they were invisible: it wants the damage TYPE adjacent to the dice,
  and every one of these spells names its type in a separate sentence.
  Re-casting REPLACES rather than stacks, a marking rider pays out only against
  its quarry, an emanation rider is checked against the board when there is one
  and allowed when there is not, and `set_concentration` — already the one
  place a summon dies — is the one place a rider ends.
- **A spell GROWS, and neither growth rule reached a die roll.** The engine read
  the structured `damage_at_slot_level` / `damage_at_character_level` rows
  correctly — and only 17 of 430 spells here have them, so almost every spell
  took the description-parse branch, which returned the BASE dice forever. A
  level-17 Fire Bolt rolled 1d10 instead of 4d10, a quarter of its damage on the
  most-used attack in the game, and upcasting did nothing at all.
  `Spell.higher_level`, a column that states the upcast rule in words, was read
  by NOTHING. `rules/spell_scaling.py` takes the rule from the spell's own
  prose. **Read the stated TABLE, never a general "one more die per tier"**:
  Eldritch Blast scales BEAMS ("two beams at level 5") and Magic Missile scales
  DARTS, so a generic rule turns four 1d10 beams into one 4d10 hit. Both state
  no dice table and are correctly left alone.
  **The PDF's soft hyphens are load-bearing**: "increases" arrives as
  "in-<soft hyphen> creases", and Fireball's entire upcast rule was invisible
  until the text was de-hyphenated first. Where the dice themselves did not
  survive extraction ("i<18" for "1d8", 8 spells) the rule is FLAGGED for the DM
  rather than guessed — a wrong number in a damage roll is worse than none.
- **A curated override's damage map is a different SHAPE, and the engine dealt
  nothing for it.** `format_spell_brief` already knew a hand-curated spell
  stores a flat `{"2": "2d10 force"}` rather than the SRD's nested rows; the
  engine did not, fell past both branches and returned None — so all 17
  hand-curated book spells dealt ZERO damage in combat. The flat map is read
  now, and scaled from `higher_level` only when it has a SINGLE row, because a
  multi-row map already IS the scaling and would double-count.
- **Spell damage is DERIVED from the description, because 17 of 430 rows have
  it.** Every spell in this project came from the owned-book PDF parse, not the
  SRD JSON, so `Spell.damage` is null almost everywhere and the engine's spell
  branch — which keys on it — dealt **no damage at all** for a Fire Bolt.
  `_spell_damage`/`_spell_type` fall back to `damage.parse_damage(sp.desc)`,
  which is OCR-tolerant on purpose: `ldlO` is `1d10` and `Necr otic` is
  necrotic, and a parser that only accepts clean input reads the ~3% that
  happen to be clean. Same doctrine as `rules/components.py` — derived at read
  time, never stored beside the prose it came from. `save_halves(desc)` is the
  matching fallback for "half as much damage on a successful one", without
  which every Fireball a target saved against dealt nothing instead of half.
- **A monster attack's SECOND damage entry used to be dropped.** The parser
  read `damage[0]` and broke; a dragon's bite is piercing PLUS acid, and both
  the rider damage and both types went on the floor. `damage_extra` carries the
  rest, each as its own typed lump.
- **A body has two hands, and `rules/equipment.py` is the one place a loadout
  is decided.** `equipped` was a single boolean, which answers "is this
  strapped on" — enough for armour class and a portrait, and unable to answer
  the question the casting rules ask: *is a hand free?* A shield and a
  greatsword were two rows both flagged equipped with nothing saying they were
  fighting over the same two hands, so the free-hand rule was left to the DM as
  a note on their board. `grip` (`main`/`off`/`both`) is now recorded beside
  `equipped`, and the model is deliberately NARROW: only weapons, shields and
  obviously-held gear (a torch, a wand, a staff) cost a hand. Everything else
  is *worn* and can never cause a refusal — a wrong guess that eats a hand
  stops a spell, a wrong guess that doesn't merely makes the game generous,
  which is the direction `_material_check` already errs in. Nothing equipped =
  two free hands, so turning it on cannot break an imported sheet. An older row
  flagged `equipped` before grips existed is placed DETERMINISTICALLY
  (two-handed → both, shield → off, weapon → main) so it reads the same twice.
- **Free hands are ENFORCED, and the refusal names the remedy.** `_hands_gate`
  runs after `_material_check`, because a component already in a hand casts a
  spell that the same component in the pack cannot. One free hand does both S
  and M; with none free, the hand holding the focus or the component performs
  the somatic for the SAME spell; a WORN holy symbol pays a material component
  with no hand (the only reason a cleric with a shield can cast at all) but
  gestures with nothing; and War Caster waives the somatic requirement — read
  off the feat's own BENEFIT TEXT ("somatic component"), not a name check, so a
  book feat granting the same thing works. A refusal always names the single
  item to stow, because that is a free object interaction and a dead end would
  be worse than the old note. `[[GRIP: draw|stow | Item | main|off|both]]` is
  what changes it, and it runs BEFORE `[[CAST]]` in the same reply.
- **The combat engine swings what is IN THE HAND.** It used to take
  `weapons[0]` — the first row in the pack — so a rogue with three daggers and
  a longbow attacked with whatever the inventory happened to list first, and a
  weapon in the sack was as swingable as one in a fist. `_combat_pc_profile`
  now orders the pool by grip (main, both, off, then stowed) and marks
  `PCWeapon.stowed`; a named stowed weapon is REFUSED with the draw named,
  never silently swapped for something else. `_melee_profile` (opportunity
  attacks) skips stowed weapons instead — nobody draws a blade to take a
  reaction. Consequences that were unreachable before: **a versatile weapon
  rolls its bigger die only when gripped `both`** (`two_handed_damage_dice` had
  been in the database since the first ingest and nothing ever read it), and
  the free-hands safety applies here too — a PC holding NOTHING has every
  weapon available exactly as before.
- **Two-weapon fighting is a fact about the HANDS, not about a class.**
  `gear.two_weapon_pair` grants `"bonus attack"` to anyone actually holding two
  qualifying weapons: 2024 Light in the main hand plus a different Light weapon
  in the other, or — with Dual Wielder — any melee weapon lacking Two-Handed in
  the off hand (Dual Wielder relaxes the OFF hand only; it still wants Light in
  the main). The bonus swing is made with the OFF hand whatever the DM named,
  and it is chosen BEFORE the reach/range checks because a dagger's reach is
  not a shortbow's range. Its damage drops the ability modifier unless the
  Two-Weapon Fighting style is taken (a `feat:` tag — fighting styles are feats
  in 2024) or the modifier is negative. **A stack has one grip**, so equipping
  a second blade from a "2x Dagger" row SPLITS it — without that the commonest
  two-weapon build in the game could never be expressed.
- **One object interaction a turn, and the hook CHARGES it.** Drawing,
  sheathing or swapping a grip is free once per turn; a second costs the Action
  (Utilize), and with neither left it is refused. Without this the free-hand
  rule had an unlimited remedy — a caster could stow a shield, cast, and
  re-draw it in the same turn for nothing, which is as wrong as never enforcing
  the hands. `Combatant.interactions_used` is turn-scoped like `attacks_made`;
  outside a fight nothing is charged, because the limit is per-TURN and a table
  not in initiative has no turns. Two consequences: naming a sheathed weapon in
  an attack DRAWS it (`_combat_draw_for_intents` — "you can draw a weapon as
  part of the same action you use to attack"), spending that same interaction;
  and **both hands on one haft can still free a hand to cast** —
  `Loadout.can_free_a_hand` — because letting go of a greatsword and taking
  hold again puts nothing down. A sword AND a shield is the case it excludes:
  freeing that hand means actually stowing something.
- **A thrown weapon leaves the hand, and it can be thrown at all.** A dagger is
  a melee weapon, so an out-of-reach target was refused outright and the SRD's
  own throw ranges (20/60 for a dagger, 30/120 for a javelin) had sat unread in
  each row's `raw` blob since the first ingest — `range_normal` is the 5 ft
  REACH, which is why reading one for the other loses the throw entirely.
  `Item.throw_range_normal/long` now carry it (backfilled from `raw`, no
  re-download). Out of reach + Thrown = a throw, and the engine reports
  `ev["thrown"]` so the backend takes it out of the grip — the engine never
  touches the character DB.
- **Weapon Mastery is an ENGINE here and NUMBERS in the slot.** `rules/mastery.py`
  holds the eight 2024 masteries as mechanisms (an on-miss rider, a save, an
  economy change); `owned_books/weapon_masteries_overrides.json` (gitignored)
  says which weapon carries which and how many a class may have active. Absent
  file = mastery OFF, which is correct for an SRD-only checkout — the open SRD
  has none. Holding a weapon whose mastery you never CHOSE gives nothing
  (`mastery:` tags are the picks); the gate is the feature.
  **All eight are applied by the code, not narrated.** Nick changes the action
  ECONOMY (the Light extra attack moves into the Attack action, so the bonus
  action stays free — two swings a turn versus three). Cleave rolls a real
  second attack and a real damage roll against a creature beside the first,
  once per turn, with `damage_flat` so no ability modifier rides along. Graze
  is the only one that fires on a MISS, for exactly the attack's ability
  modifier and nothing else — no crit, no Rage, no Sneak Attack, because it
  "can be increased only by increasing the ability modifier". Topple rolls the
  Con save at DC 8 + that same modifier + proficiency. Push is the board's
  `shove` (forced movement, no opportunity attack, stops at the first
  obstacle), gated on Large-or-smaller. Slow takes real feet off the board's
  movement budget and does not stack.
  **Every timed rider stamps its expiry ROUND into the condition** —
  `sapped:4`, `vexing:goblin:5`, `slowed:4` — because a condition is already a
  string the tracker persists, and `_live_rider` clears a lapsed one on the way
  past. Sap and Vex are then SPENT in `_attack_advantage`: a rider that waits
  forever for its victim to swing is a different, better rider than the book
  prints, and a mastery that leaves a permanent label on a creature is
  decoration rather than a rule.
  Graze and Topple both say "the ability modifier used to make the attack
  roll", so `PCWeapon.ability_mod` carries it — the backend already chose it
  (finesse takes the better of Str/Dex), and re-deriving it in the engine got
  finesse weapons wrong.
- **A shield is only worth +2 while it is in a hand**, and only one suit of
  armour is on a body. Both used to be the same question as `equipped`, and
  `_compute_ac` took whichever armour row it met last. `gear.normalize` is what
  puts an impossible loadout right — a bulk outfitter (the arena's
  Quartermaster, an imported sheet, a creation kit) sets `equipped` line by
  line and never asks whether the result fits on a body, so the excess is
  stowed and named rather than reaching the rules as fewer free hands than a
  body has.
- **Silence is a place, and `MapEffect.silences` is where it lives.** Its own
  column for the same reason `obscured` has one: `kind` says how the UI paints
  an area, not what standing in it does. `silenced_at`/`token_silenced` answer
  per floor; `Condition.SILENCED` answers the same question at a table with no
  board (not an SRD condition — the SRD only has the spell's area, and a
  gridless table still needs a way to say "you cannot speak").
- **A material line WRAPS, and the parser read one line.** `_fldrx` captures
  `[^\n]+`, and the material component is the one field that routinely runs
  past it — "M (a diamond worth 300+ GP,\nwhich the spell consumes)" truncates
  to an unclosed bracket, the `M (...)` regex finds nothing, and the spell is
  stored with NO material at all. That was 55 spells silently free to cast.
  `_components_value` reads on until the bracket closes, treats `}`/`]` as a
  closing bracket (the extractor confuses them often enough to matter), and a
  lone `l` glued to digits is a 1 — in a material component that misreading is
  the price of the spell.
- **Re-running a bulk parser WIPES the additive class lists.** `ingest_spells`
  replaces each row's `classes`, so `ingest_spell_lists_overrides` has to run
  after it, every time — miss it and the artificer's pool silently drops from
  20 cantrips to 4. `main()` already orders them correctly; a hand-run re-parse
  is where this bites.
- **A class needs a spell LIST, and only the artificer's was missing.** Its 75
  spells live in the `spell_lists_overrides.json` slot (additive — see
  `rules/OWNED_IMPORT_FORMAT.md`). Before it the DB held exactly one spell
  tagged `artificer`, so an artificer PC reached the CC spell stage with an
  empty pool. Twelve XGE/Tasha's spells on that list are still absent from
  `rules_spell` and are named in the file's `_absent_from_rules_db` block.
- **A 2024 background GRANTS its Origin feat** — it is not a pick from the
  origin pool. CC resolves `background.origin_feat` against the WHOLE feat list
  (a book background can grant a feat filed elsewhere: Rune Carver → Rune
  Shaper, a `giant` feat) and shows it as granted; only a background naming no
  feat offers a free pick. `register_character` enforces the match.
- **Feat prerequisites are requirements (`;` / ` and `) of alternatives (`or`)**
  — see `_feat_prereq_met`. Reading the alternatives as requirements locks a
  Rune Carver fighter out of the feat their own background grants. It resolves
  prerequisite feats, feat options ("Strike of the Giants (Fire Strike)"),
  backgrounds, and dragonmark exclusivity; anything it can't parse is allowed.
- **`find_entities_by_name` decides whether two records are the SAME person,
  and both ways of being wrong are silent.** A false MISS creates a second Kara
  standing beside the first; a false HIT merges two people into one record.
  Nothing raises either way, which is why the contract is pinned in
  `scripts/graph_identity_smoke.py` rather than left to its six callers
  (extraction, hoards, pantheon, the origin ties, the goal resolver).
  It used to build a full ORM object for EVERY entity in the world —
  deserializing each one's attribute JSON — to compare one string: ~20 ms per
  call at 2,000 entities. It now narrows over two columns (`id`, `name`) and
  loads only the matches: **2.3 ms**, byte-identical results on every probe.
  **The comparison stays in PYTHON on purpose.** SQLite's `lower()` folds A-Z
  and nothing else, so pushing it into SQL silently stops matching a stored
  name whose odd case is outside ASCII — "Ærik" never meets "ærik", and the
  consequence is an invented duplicate rather than an error. The smoke test
  was written once with the wrong probe and PASSED against that regression:
  the caller's string is folded by Python before it reaches the database, so
  a query of "KAËLITH" arrives already lowercased and proves nothing. The
  STORED name has to carry the uppercase non-ASCII letter for the check to
  bite.
