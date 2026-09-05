# Running The Oracle — services, demos and smoke tests

The full catalogue of entry points, demos, self-tests, smoke tests and browser
harnesses. Split out of `CLAUDE.md`, which keeps only the handful of commands
used every session.

- Backend: `uv run python oracle-dm-backend/fastapi-dm.py`
- Bot: `uv run python ai-dm-sicord-bot/oracle-dm-discord-bot.py`
- World-graph demo: `uv run python -m eight_card_system.demo`
- Rules ingest/demo: `uv run python -m rules.demo` (network required)
- Dice demo: `uv run python -m dice.demo`
- Tactical board demo: `uv run python -m vtt.demo`
- Tactical board self-test: `uv run python -m vtt.selftest` (asserts the rules
  math — run it after touching `vtt/geometry.py` or `vtt/mapgen.py`)
- Tactical board wiring smoke test: `uv run python scripts/vtt_smoke.py` (drives
  the real chat path with a stubbed LLM: fight → board → hooks → prompt → close)
- LoRA probes (**Windows interpreter** — they talk to ComfyUI):
  `./.venv/Scripts/python.exe scripts/style_lora_probe.py` sweeps a HOUSE-STYLE
  LoRA over every kind it touches at several strengths from one seed;
  `scripts/map_lora_probe.py` does the same for the `map` kind over all 21
  archetypes; `scripts/worldmap_lora_probe.py` does it for the `worldmap` kind
  over real survey prompts (that LoRA is caption-free — no trigger word exists,
  strength is the only dial). All three print a pixel-diff column — 0.00 means
  the LoRA did nothing. `scripts/map_composite_check.py` renders a real drafted
  map, wash and ink together, to check labels stay readable over the paint.
- Board surface catalogue (**Windows interpreter** to draw, either one to
  measure): `scripts/material_prerender.py --audit` says what is missing,
  `--render` fills the gaps and is resumable, `--sheet` writes a contact sheet.
  Three measurements guard it and they catch different things: `--contrast`
  asks whether a player can tell COVER from the floor under it, `--surface`
  asks whether a swatch is a picture of a SURFACE or of a PLACE, and
  `--palette` asks whether every surface NAMES its colour and whether the
  unnamed ones drifted cool. Run `--palette` after touching any material
  prompt; `--contrast` is blind to a whole family drifting together, which is
  how the board went teal under it. **`--redraw` deletes before it draws**, so
  it is for a prompt you have already decided on, never for one you are still
  choosing between — that is what `scripts/material_style_probe.py` is for,
  which renders to its own `probe-*` slugs and touches the catalogue not at all.
- Furniture models (**Windows interpreter** — it talks to ComfyUI):
  `scripts/furniture_meshes.py --audit` prints each kind's spread and whether
  `furniture.fit` will take it, `--render --only o,n` draws, `--collect` moves
  the ones you are happy with into the committed tree. `--force` re-renders,
  and it now drops the cached REFERENCE too — without that a reworded subject
  re-meshes the same old picture and the audit blames the shape.
- Canopy lens: `npx node activity-ui/canopy-lens.mjs http://localhost:4190/`
  against a staged wooded board (`--board forest`). A tree's crown is about as
  wide as the tree is tall and covers squares that are open, so the board bores
  a view-aligned shaft through the leaves toward anything standing under them.
  That hole only appears when a tree happens to stand between the camera and a
  creature, so the harness widens the lens until it is unmissable and takes the
  same frame twice, shut and open, from ONE page load — it asserts the board
  both CHANGED and LOST GREEN, because a hole through a canopy is the second
  and not merely the first.
- Picture caching: `uv run python scripts/cache_smoke.py` — a long
  `Cache-Control` is served IF AND ONLY IF the URL quotes the version that
  image id currently carries. An id is not a safe cache key on its own: SQLite
  reuses it, and this store deletes rows, so an unstamped URL can start meaning
  a different picture. Run it after touching any `/imagery/*` route.
- First-load budget: `uv run python scripts/bundle_budget.py` (after a
  `npm run build` in activity-ui) — three.js is two thirds of the application's
  JavaScript and nothing before a fight can draw a triangle with it, so the
  isometric board is imported dynamically and gets its own chunk. One
  top-level `import` of `vttScene3d` anywhere in the eager graph folds it
  silently back in: the board still works and the app is just heavy again,
  which is why this is measured off the built output rather than trusted.
- Loot / affix demo: `uv run python -m loot.demo`
- Proving Grounds demo: `uv run python -m arena.demo [level] [difficulty]`
- Combat-music smoke test: `uv run python scripts/music_smoke.py` (the two
  halves: the BACKEND decides when from the tracker and holds a scene cue that
  arrives mid-fight, the BOT decides which mood from its own vocabulary — and
  a warm tavern is not a war)
- Spell-resolution smoke test: `uv run python scripts/spell_resolve_smoke.py`
  (how a spell resolves, read off the spell rather than off a column almost
  nothing fills — and the damage actually landing, through the real engine)
- Proving Grounds smoke test: `uv run python scripts/arena_smoke.py` (slots →
  level-up climb → bout → victory/defeat, engine *and* WebSocket, LLM stubbed;
  plus the three things a frozen Quartermaster turned out to be — a LEVEL-1
  bout, which opens the stall with no climb before it, going through to the
  sand; a column a model declares reaching a database that already had the
  table; and a handler that throws not taking the socket down with it; plus the
  engine reporting each turn on its own, naming whose it was, in its own text,
  and a resolved turn's prompt being a fraction of an ordinary one while still
  carrying what happened and where everyone stands)
- Feat smoke test: `uv run python scripts/feats_smoke.py` (a feat's questions,
  its grants, its named options, the resource it hands over, the at-will spell
  it grants, and an OPTION that asks its own questions — all the way to what
  the DM is told)
- Session-table smoke test: `uv run python scripts/session_table_smoke.py` (fake
  guild/channel/member through the REAL voice-state handler: the music sidecar
  is not an occupant, an emptied table closes after the grace period, stepping
  back in cancels it, and a table nobody sat at is still swept)
- Creation-story smoke test: `uv run python scripts/cc_story_smoke.py` (the CC
  payload survives the wire — spells, tools, languages, feat picks — a keepsake
  renamed keeps its `base`, an origin becomes real world entities and edges, a
  likeness drawn before the seal is ADOPTED by it and its rolled face pinned,
  words with no picture are still kept, and a spell picker ships the whole
  spell rather than one sentence of it)
- Species-choice smoke test: `uv run python scripts/species_choices_smoke.py`
  (a species asks what its traits promise — languages read off its own line, a
  skill, an either/or gift — and the answers reach the sheet as real tags; plus
  the "any feat you qualify for" slot reaching a level-4 feat at level 1 while
  the background slot and every epic boon stay gated). Scratch DB carrying a
  COPY of the real `rules_race`/`rules_feat` rows — never the world DB.
- Summoning smoke test: `uv run python scripts/summons_smoke.py` (the scaling
  arithmetic, variant gates, the stat block the combat engine reads, the side
  it fights on, where it lands in initiative, and the backend's own hooks)
- Session-feature smoke tests (all offline, fresh scratch DB, no GPU/LLM):
  `uv run python scripts/<name>_smoke.py` for `locale` (place/clock/weather/
  who's here), `chronicle` (journal + quests + bonds), `speech` (dialogue
  attribution), `itemart` (catalog vs player-named art, and that a renamed
  piece keeps its stats), `cultural_scripts` (culture -> typeface mapping),
  `affix` (a drop rolls properties that reach real mechanics), `components`
  (V/S/M priced out of the book's prose, enforced at the cast hook, and the
  gate on a caster who can't act or can't speak), `grip` (what is worn and
  what is held, in which hand; the free-hand rule for Somatic and Material
  components, War Caster, the [[GRIP]] hook that frees one, and the combat
  half — main/off-hand weapon choice, versatile dice, two-weapon fighting),
  `forge`
  (tempering needs a smith),
  `legendary` (a boss's Legendary Resistance parsed from OCR prose and
  SPENT by the engine until it runs out, and its legendary actions
  reaching the DM's brief at all),
  `spell_rider` (a buff spell adding its dice to real attacks: scaling with
  the slot, replaced not stacked, a mark paying out only against its
  quarry, and ending with the concentration that held it),
  `spell_scaling` (a cantrip growing with the caster and a spell with its
  slot, the two spells a generic rule would wreck, damaged book text
  flagged rather than guessed, and the curated flat damage map),
  `subclass_engine` (the five things a subclass tells the ENGINE: a widened
  crit range measured over thousands of rolls, a subclass-granted Extra
  Attack, an Unarmored Defense it sets itself, third-caster spell slots,
  and a granted sense reaching the sheet as a tag the board reads),
  `subclass_overrides` (the subclass slot end to end for whatever is IN
  it — offered at the right level, the pick sticks, always-prepared
  spells become castable at their tier, a stated resistance is enforced,
  and a companion stat block materializes at its own declared formula;
  expectations are derived from the gitignored file, never written in), `resistance` (damage types out of scanned prose,
  defences out of both bestiary shapes, and the arithmetic through the real
  engine — a mace on a skeleton, a fireball on a fire elemental), `routes` (roads costed from real geography, and
  no map data leaks), `map` (one terrain answer across scene/board/parchment;
  tool + knowledge gating; a sheet accrues across revisions), `airship`
  (core/helm/damage/repair/crash/upgrade + passages + mobile bastions), `bonds`
  (linked creatures: initiative dice, sight through cover, blink, rescue),
  `targeting` (what a spell targets out of OCR-damaged prose; who the board
  says may be hit and why not the rest; a template clipped by line of effect;
  the action bar reaching the engine as an intent, and refusing when it can't),
  `ventures` (an NPC's own quest: it is born, it moves on the clock unwatched,
  the party joins and leaves, the DM settles a step at the table, and every
  ending marks the world — a place calmed, a role promoted, a relic won, a
  successor taking a dead venturer's post; plus the other side of it — an
  opposed venture rolls on harder, a race lost ends it outright, and a covert
  saboteur walking beside them is exposed as a BETRAYAL or gets away clean),
  `skins` (what a square is MADE of versus what it DOES: a skin changes no
  rule, may reshape a quoted height but never restate it, a tent you can walk
  into, a watchtower top that is a real storey, a hold at -8 ft)
- Entity-identity smoke test: `uv run python scripts/graph_identity_smoke.py`
  (case-insensitive name lookup, two people sharing a name, a slug sorting
  first, and non-ASCII case-folding — the check that fails if the comparison
  is ever pushed into SQL)
- Unfinished-business smoke test: `uv run python scripts/threads_smoke.py` (a
  thread survives creation as real world state; anchors scatter from the
  character's own seed rather than beside the party, and a retry lays the same
  map; reach bands make visibly different offers; the DM is told only when
  somebody asks or names their own past; settling one closes it and the others
  are untouched)
- Bastion builder smoke test: `uv run python scripts/bastion_build_smoke.py`
  (what the rules allow, what they refuse and how they say so, that nothing
  argues with the player's description, and raising one against a real purse)
- Vessel-rooms smoke test: `uv run python scripts/vessel_rooms_smoke.py` (from
  the DM's own sentence: the hull decides how many compartments, the caller
  names them, a bastion's facilities become its rooms, and the hold you divide
  is still one you can walk)
- Pantheon / patron-choice smoke test: `uv run python scripts/pantheon_smoke.py`
  (a god born in play becomes choosable in CC; an unmade one stops being offered)
- Activity UI harnesses (Playwright, against the offline demo). **`vite
  preview` PROXIES `/ws` to the backend**, so with the backend up the demo feed
  never engages and every harness that needs a character sits there waiting —
  serve the build with something dumber instead:
  `npm run build && (cd dist && python3 -m http.server 4190)`, then
  `npx node <script>.mjs http://localhost:4190/`. The harnesses: `feat-choices`, `spell-picker`, `levelup-spells`,
  `reprepare`, `mobile-smoke`, `arena-shot`, `vtt-shot`, `deity-shot`,
  `floors-shot` (the storey switcher: peek at a gallery, and what a connector
  looks like on the board), `race-dup` (species traits render exactly once per
  viewport), `granted-feat`
  (a background grants its Origin feat, choices and all), `feat-spells` (the
  two feat slots are independent: the granted feat is gone from the species
  pool, both feats' questions gate Onward separately, and a school-scoped
  spell pick lands on the Spells stage), `species-choices` (the species' own
  questions gate the Origin stage, a conditional one appears only when the
  option it hangs off is taken, and the "any feat" slot reaches a level-4 feat
  while an epic boon stays locked), `cc-panels` (the background panel carries
  the whole of what a background grants; Pact of the Tome's two spell questions
  gate the Spells stage separately; the landing's way out asks first),
  `cc-story` (a feat that builds on another stays locked without it; the
  background's story panel; a keepsake named and described; the review page
  showing the whole character; the Likeness stage, reached BEFORE the seal),
  `spell-detail` (the pane carries the whole spell — casting time, range,
  components with the material, concentration folded into the duration, the
  upcast rule — pointing at one does not take it, and a card the grid has
  locked can still be read), `pframe-shot`
  (portrait corner ornaments stay corner-sized), `play-shot` (the play surface
  at desktop and phone: status bar, "here & now" rail, narration column, roll
  card), `chronicle-shot` (suggested-action chips send on tap; the Chronicle's
  journal and bonds tabs), `battle-shot` (a board out puts the fight on its own
  page; the board is most of the screen; the order is a rail and not a row of
  cards; the page says whose turn it is; the engine has a log of its own with a
  hit reading differently from a miss; the prose can be turned off without
  leaving the fight; the sheet is one tap away and does not live on screen; the
  log folds; Reset Layout does not reload; and on a phone the MAP — not just
  its panel — still leads), `occlusion-shot` (a creature
  standing behind the mill's pillars is drawn hollow, and nobody else is).
- Narration-streaming guard: `uv run python scripts/stream_smoke.py` (the hook
  filter and both wire formats, against a synthetic stream — no model needed).
  Live streaming is OFF; `ORACLE_LLM_STREAM=1` turns it on and it has not been
  measured against a real model.
- World stall: `uv run python scripts/shop_smoke.py` (the panel's stock IS the
  DM's roll; buying goes through the same `[[TRADE]]` resolver).
- Lore capture: `uv run python scripts/lore_smoke.py`.
- Puzzle chain: `uv run python scripts/puzzle_smoke.py` (the location gate, the
  site verb, and that the answer key reaches the DM and never the player).
- NPC combat AI: `uv run python scripts/ai_arena.py --board <archetype> --bouts 6
  --rounds 120 --quiet` — it attaches a real board, so fights take the turns
  real geometry costs; a 40-turn cap reads as "never resolves" and is not.
- Ground-under-an-object arithmetic: `node activity-ui/ground-check.mjs` —
  no browser and no build (it bundles `boardView.ts` out of src with esbuild),
  because `groundSlot` is pure arithmetic over the grid.
- Token-occlusion arithmetic: `node activity-ui/occlusion-check.mjs` — needs no
  preview server and no build (it bundles `boardView.ts` out of src with
  esbuild), because `occludedAt` is pure grid arithmetic over a camera that
  never moves. The LOOK still needs a browser; that is `occlusion-shot`.
