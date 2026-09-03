# The living world — terrain, maps, threads, ventures & items

The one terrain answer and how the ground lies, drawn maps and routes, a
character's unfinished business, NPCs' own quests, item art economics, loot
affixes, cultural typefaces, lore capture, puzzles, shops and chases. Split
out of `CLAUDE.md`; read this before touching `eight_card_system/`,
`loot/`, `survival/travel.py` or the imagery catalogue.

- **Each culture reads in its own hand.** Six OFL display faces live in
  `activity-ui/public/assets/fonts/` (keep `ATTRIBUTION.md` beside them — the
  licence requires it). The server owns the only thing the client cannot know:
  WHICH culture a name belongs to. That table is `_FAMILY_SCRIPT` /
  `_SPECIES_SCRIPT` in the backend, surfaced as a `script` field on lexicon
  entries, CC species/powers, the sheet, and speech blocks. They are DISPLAY
  faces — set on proper nouns only, never body text — and a name whose culture
  can't be placed correctly stays in the house serif.
- **Rarity buys SLOTS, not bigger numbers.** `loot/affixes.py` gives dropped
  gear rolled properties: common 0, uncommon 1, rare 2, very rare 3, legendary
  4. Two invariants keep it 5e rather than ARPG stat soup — a piece never rolls
  two affixes feeding the SAME numeric bonus (5e tops out at +3, and
  `mechanical_bonuses` clamps regardless), and never more than one prefix (a
  second would be invisible in the name). The DM emits `[[LOOT: <item> |
  <rarity>]]` naming only the base item and how fine it is; the CODE rolls what
  it turns out to be. Affixes rename the piece, so — exactly like a
  player-named item — the entry keeps `base` and every mechanical lookup must
  go through it. `_compute_ac` already lost a suit of armour's entire AC to
  this once.
- **HOW THE GROUND LIES is one answer, and it lives beside the one terrain.**
  `placelore` already owned a CLOSED terrain vocabulary that three renderers
  shared — farmland, forest, hills, river, swamp, mountains, desert, coast,
  sea, underdark, dungeon, urban, interior — and three other systems were each
  answering "how rugged is this country" their own way. The street generator
  rolled a fall off a die; `survival/travel.py` kept a private terrain table
  whose words only HALF overlap these; and the cartographer painted country
  with nothing to say about relief. `placelore.RELIEF` is keyed on the same
  words and carries what each caller needs: `fall_ft`/`waves`/`cross` for a
  board's ground, `travel` for the journey, `map_words` for the drawn sheet.
  **The travel half was a live bug, and a silent one**:
  `TERRAIN.get(name, TERRAIN["grassland"])` never complains about a word it
  has not got, so farmland, river, coast, **sea**, underdark, dungeon and
  interior were every one of them costed as a stroll over a meadow — a sea
  crossing included. `_place_terrain` says what country a place is IN;
  `_travel_terrain` is the only thing that may hand that to `survival.travel`.
  `scripts/routes_smoke.py` fails if the two vocabularies drift apart again,
  and it checks the mapping MEANS something (rough country must cost more than
  easy) rather than merely resolving — including that nothing in that table is
  UNREACHABLE, which is how the last two orphans were found. **`arctic`** (half
  speed, +10 nav) needed the CLIMATE, since two of travel's entries are about
  weather rather than ground: open country in the far north is an arctic march
  whatever is under the snow. **`road`** (full speed, -5 nav) needed
  `_ROUTE_KINDS` to say which ways are MADE — all three were costed as the raw
  country between the places, so the high road was purely longer, which is the
  opposite of what a built road is for. It is now longer AND faster per mile
  AND unloseable, so through mountains or marsh it beats the track outright and
  over grassland it does not. (`urban` is not `road`: you cannot get lost in a
  town and you do not forage in one, you buy.)
  **The country reaches the SKY too.** `survival.weather` rolled on CLIMATE
  alone, so a summit and the marsh in the valley below it — same latitude, same
  band, same day — got identical weather forever, and the fog that is most of
  what a marsh IS arrived no more often there than on a ploughed field.
  `placelore.WEATHER_BIAS` is the sibling table, keyed on the same words and
  deliberately kept APART from `RELIEF` because they are two different facts:
  one is the ground, one is the air over it. Hazards need nothing of their own
  — `hazards_from_weather` is derived from the weather, so the bias flows into
  extreme cold, heat and wind for free. `generate_weather(terrain=)` defaults
  to no bias, so every caller written before it keeps the weather it had.
  **`mapgen._ruggedness` is the same dial for a board's GROUND.** Generators
  used fixed probabilities for their height features — a third of open boards
  terraced, four fifths of the rest given a knoll — which is `rng.randint(3,
  8)` one level up: a salt flat and an alpine meadow were equally likely to
  come back a stack of mesas. Measured over 60 boards: a marsh is stepped 5% of
  the time and flat 63%, the high country stepped 100%… which was capped to
  85%, because a board that is ALWAYS terraced stops being a thing anyone
  notices. The STEP is the country's as well as the odds: a knoll on a plain is
  five feet and in hill country it is the ten a player has to decide about,
  which is a decision and not a jitter. An archetype that NAMES its own country
  keeps it (`_ruggedness(default="mountains")`) — a mountain pass is
  mountainous whether or not the DM said so, and reading the generic middling
  answer there would make it gentler than it was before relief existed.
  It reaches `forest` and `clearing` the same way, and `swamp` deliberately
  gets nothing from it: **a bog is flat wherever it lies**, which is the ANSWER
  rather than an omission — `swamp` says so in `RELIEF`. What a swamp DID need
  was the `_for_area` rule, since a hummock is the only dry ground in a mire
  and three of them scattered over four times the bog is running out of the one
  thing that makes it worth fighting in.
  **The interesting half is knowing which heights are the LAND's and which are
  somebody's LABOUR.** A camp and a ruin take the ground they were pitched or
  raised on — but a camp digs the same bank on a plain as in the hills, and a
  ruin's courses are masonry whose survival is about how long ago it fell, so
  neither answers to `_ruggedness` and the selftest checks that they DON'T.
  Reaching for the dial everywhere would be as wrong as never having it.
  Relief reaches a board as an INPUT (`generate_map(relief=)`, stored in
  `notes` so a regenerated board is the same board) because `vtt/` must not
  know what a world graph is — the `_bastion_rooms` line. It reaches the
  cartographer as a clause on the DOMINANT country only: three sectors' worth
  of hachuring instructions is a prompt about hachures rather than country.
- **A map is painted country under inked truth.** `eight_card_system/mapmaker.py`
  draws every dot, name, route, compass and scale bar from real spherical
  coordinates; the `worldmap` image kind paints only the TERRAIN under it,
  surveyed in nine sectors from the biomes of the places actually on the sheet
  (so the knowledge gate governs the country too, and an ignorant cartographer
  gets vague land). Same doctrine as `vtt/art.py`: the picture is a texture.
  The model must never write a word — a label it invents is a second, wrong map
  showing through. Distortion runs BEFORE the survey so a failed draft is wrong
  self-consistently. Offline degrades to bare parchment, never an exception.
  **Maps accrue**: `[[MAP: update-success|update-failure]]` re-reads a sheet's
  own recorded slugs and merges, so revising never loses country — re-surveying
  by radius alone would drop the far half the moment its owner walked away.
- **A sheet holds only what it can carry, and is FOR something.** Zooming out
  DROPS features, it doesn't shrink them: every place has a `prominence`
  (`attributes["prominence"]` overrides the scale-derived default, so a famous
  ruin outranks its size), and each `MapScale` — local/regional/provincial/world,
  a third field on the hook — sets a reach, a minimum prominence and a hard
  feature cap. The cap is physical: ~20 labels is all 768px holds, so labels are
  decluttered and the least prominent lose their names first. A map's PURPOSE
  decides its contents: `[[MAP: treasure | <object> | <goal place>]]` draws an
  unlabelled cross plus only the landmarks along the corridor to it (a prominent
  city the wrong way is no help), never a survey with an X added. A revision
  keeps the sheet's own scale and purpose.
- **The code rolls the cartography check, not the LLM.** `[[MAP: draft | <area>
  | <scale> | <boons>]]` computes the modifier from the real sheet (Wisdom, tool
  proficiency/expertise, Survival/Nature for advantage, dragonmarks, a
  cartographer's own training), rolls it and decides. The old
  `draft-success`/`draft-failure` forms remain for a DM adjudicating it
  themselves. The ROLL is reported to the table; whether the sheet is any good
  is never reported — a drafter who knows their map is wrong doesn't have a
  wrong map. Declared boons are allowlisted: Guidance/Inspiration help the
  check, a vantage (flight, Clairvoyance, Scrying) WIDENS the survey radius,
  and Find the Path makes the sheet true regardless of the roll.
- **Setting out is a decision, and still not a map.** `[[ROUTES: <dest>]]` makes
  the code cost two or three roads from the world's real coordinates
  (`_routes_to` + `survival/travel.py`): how far, how many days, how dangerous.
  The payload carries NO coordinates or bearings — only what a traveller could
  tell you in a taproom. This is the same line `mapmaker.py` draws; keep it.
- **Coin is money, not gear.** Book equipment lists write starting coin as a line
  item ("15 GP"); `_add_inventory_item` folds any coin name into the PURSE so the
  pack never shows currency next to the sheet's own Gold row.
- **The free CC trinket is a common WONDROUS item** — not every Common magic item
  (a potion is drunk once, a scroll burns). The SRD has none, so the pool comes
  from the gitignored `owned_books/items_overrides.json` slot; an empty pool
  degrades to a skippable stage.
- **A new place is always drawn.** `_maybe_render_arrival` (hung off the world
  extractor) keeps `meta["scene_place"]` and renders any location the table
  hasn't been shown — the DM's `[[IMAGE: place]]` hook is a bonus, not the
  mechanism. Activity tables get the frame pushed; Discord tables collect it
  from `_PENDING_TABLE_IMAGES` on their next reply.
- **Item pictures are built from the catalog row**, never the bare name: see
  `_item_art_prompt`. Mundane gear also swaps the ornate house style out, or
  "Common Clothes" comes back as courtly finery.
- **Item art is a FIXED catalog cost, not a per-player one.** Pictures are keyed
  by item slug, so one render of "Longsword" serves every character in every
  campaign forever — the whole 700-item catalog is pre-rendered once by
  `uv run python scripts/item_art.py --audit` / `--render` (Windows interpreter
  to reach ComfyUI; ~4h of GPU, resumable). During play `imagery.item_art_mode
  = "catalog"` means an ordinary item is NEVER drawn on demand: it either has
  its shared picture or reports `pending` until the batch reaches it.
  The ONE render play pays for is a piece a player **names and describes**
  (`describe_item`), which gets its own per-character ref
  (`kara-emberfall-dawnbreaker`) and leaves the shared catalog art untouched.
  A renamed piece keeps `base` in its inventory entry, so every mechanical
  lookup — stats, weight, cost, equip/attune — still resolves through the
  catalog. Never look an item up by its display name alone; use
  `_item_base_name`.
- **A backstory is only useful to the DM if it left something OPEN, and where
  the anchor LANDS is the scale decision.** `threads.py`. The six kinds are
  unresolved business, not personality — a trait says how you behave in a
  scene already running, and the question a DM actually needs answered is
  "what could these people do next". An anchor is placed on a bearing and at a
  distance seeded from the **CHARACTER**, never beside wherever they happen to
  be standing: placed locally, a hundred players' ruins pile on the starting
  village and the rest of a 50-million-square-mile planet stays empty.
  Deterministic, for the cartographer's reason — a retried registration must
  lay the same map, not a second ruin beside the first.
  **An anchor is CHARTERED, or it is a pin and not country.** A place with
  coordinates and a sentence answers none of the questions the rest of the
  world asks it, and each failure is quiet: `placelore` has no `biome` to
  derive arrival art, battlemap floor or drawn country from; the DM's danger
  block skips a place carrying no `danger` or `denizens`; a place in no region
  has no settlement budget. `cartographer.charter_place` gives it exactly what
  a frontier stub is born with — biome from the climate band, rolled danger,
  scale ceiling, motifs, denizens — and PART_OF a real region, founding one
  when the nearest heart is beyond `REGION_RADIUS_MI`, which out at thread
  distances it usually is. That is the payoff rather than the cost: one
  character's past brings a whole region of country with it. Existing values
  are never overwritten (a persisted biome is what the scene was drawn from).
  **Two threads want a DAY's room, not the cartographer's six miles.** Six
  keeps a ruin out of a village and does nothing about crowding; measured at
  300 characters it left a MEDIAN gap of 16 miles with two thirds of anchors
  within a day of another, which is a world that is more backstory than
  country. `THREAD_SPACING_MI` is 24, and when a band is full the placement
  steps OUTWARD (`_CONGESTION_STEPS`) rather than packing tighter — that is
  what makes it scale, since the bands reach 1,100 miles and the planet is
  12,566 around. Distance within a band is drawn area-uniformly
  (`sqrt` of a uniform over the squared radii) or a ring piles its points on
  its inner edge. **Measured** at 400 characters / 800 anchors: nearest other
  anchor a median of 27 mi, **0% within a day's walk**, median 350 mi from the
  starting village, and only ~10% of the planet in use — so the headroom is an
  order of magnitude, and the reach bands are what caps it, not the world.
  **The cheapest anchor is one that already EXISTS.** `candidates_for` offers
  what the world made in play — a place the extractor marked `destroyed`, an
  NPC who went `missing`, anything named in a WorldEvent whose summary reads
  like the kind (the second is what catches a village the DM burned in prose
  without the status ever being set, which is most of them). Adopting one via
  `attach_thread` creates NOTHING: no place, no bearing, no roll. Two
  characters out of the same ruin is the POINT — resolution is per-PC, so
  settling one leaves the other's open. Only INVENTED anchors are withheld
  from the next player; history the world made is offered to everyone.
  **Fit is ranked and annotated, never refused.** A tiefling should not be
  shown a wood-elf village's burning as the obvious answer, but a tiefling
  raised among humans is one of the oldest backstories there is — so
  `fit_for` sorts natives first and labels the rest ("mostly elves and humans
  — you would have been an outsider there"). `denizens` is the HAZARD table
  (wolves, bandits), so the population signal is the races of the NPCs the
  world actually placed there, plus species words in the description.
  Comparison runs on loose tokens (`Elf (Wood Elf)` → elf/wood elf/wood, so a
  half-elf reads as at home among elves); DISPLAY is a separate step, because
  printing the first three tokens alphabetically cut "humans" off a village
  that was half human.
  **WHICH words name a people is the species roster's question**, not this
  module's — `people_vocabulary` reads `rules_race` through the graph's own
  engine (lazy, guarded import: `eight_card_system` depends on `rules`
  nowhere else, and a checkout with no rules tables must still place threads).
  A hand-kept list goes stale silently — an owned book's khoravar simply stops
  being recognised — and it did worse than that: an unnamed people made the
  display string empty, which `fit_for` read as "nothing to say" and reported
  as NATIVE, declaring a stranger at home. Only WHOLE species and lineage
  names enter the vocabulary, never their parts: "Elf" earns "elf" because it
  is a species, "Wood Elf" earns "wood elf" and NOT "wood", or a wood palisade
  makes a village of wood-folk. **`lineages` is overloaded and only a LINEAGE
  lists peoples** — the book says Lineage when the choices are peoples (Elven
  Lineage → Drow; Shifter Lineage → Beasthide) and Ancestry or Legacy when
  they are traits (Giant Ancestry → Cloud's Jaunt; Fiendish Legacy →
  Infernal), so the label is the gate; taking all of them put "cloud's jaunt"
  in the roster. The plural map that remains is DISPLAY POLISH only — English
  mangles "elf" — and anything missing from it prints as written rather than
  vanishing.
  **Placement cost: I guessed wrong and the profiler said so.** The distance
  loop was never the problem — 87% of the time was `_anchor_name` calling
  `graph.find_entities_by_name` twelve times per anchor, and that helper loads
  EVERY entity and compares names in Python (half a million JSON decodes per
  twenty anchors). One name-column query, plus folding two full table scans
  into one two-column `_placed_world`, took 800 anchors from **367 ms to 14
  ms** and 2,000 anchors to 39 ms. The lat/lon grid is kept — it is why
  distance no longer appears in the profile at all — but it bought ~10% on its
  own.
  **The DM block is gated on the player's MESSAGE alone**, not on
  `_scene_text` — that helper folds in the location's name and description,
  and a thread is something somebody ASKS for, so a tavern describing itself
  as a place people look for work is not somebody asking. Second half of the
  gate is `mentions_thread`, which matches a thread's own place/person on WORD
  BOUNDARIES (the `setpieces.landmark_for` rule: "The Ford" is inside
  "afford", and a length guard does not save you, because the name that trips
  it is a real four-letter name). Never more than one offered at a time, and
  never as an instruction — a player is allowed to leave their past alone, and
  a DM who keeps raising it is running their character for them.
  `[[THREAD: resolve | <kind> | <what happened>]]` closes one out.
- **An NPC may want something, and a VENTURE is not a companion.** Until
  `eight_card_system/ventures.py` the world had people who *are* something (a
  role, a hook) and quests the PARTY takes, and nothing in between — so an NPC
  was furniture between the moments a player addressed them. A venture is an
  NPC's own quest: a goal rolled from their trade, 1-3 stages ending in a
  climax, a stage attempt every `STEP_DAYS` world-days. **The party may
  ACCOMPANY (`ACCOMPANIES`, pc -> npc — deliberately the mirror of
  `travels_with`) and stop at any moment**; accompanying opens no companion
  relation and buys no control, only that the venture stops resolving behind
  their backs and starts happening at the table. `step_venture` is the ONE place
  a stage moves whichever side called it, so a watched and an unwatched venture
  can never advance by different rules. **Depth is a real dial on the odds** —
  measured over 431 ventures, depth 1 succeeds 79% unwatched, depth 2 61%,
  depth 3 41%, because the setback allowance is `depth + 1` and every setback
  raises the current stage's DC. The deep ones usually need somebody, which is
  the whole argument for walking beside a person. A venture IS a QUEST entity
  (tier `venture`) so the journal, the world slice and entropy's main-cast
  protection read it for free — but it must never take a party STAKES clock,
  because those escalate on the party's NEGLECT and neglect is not what decides
  this. `create_entity`, never `upsert`: a second venture is a new thread, and
  upserting by slug reopens the finished one.
  **A venturer has to come HOME**, and the first long simulation is why: the
  climax moves them to the wild place they were aiming at, and with no
  homecoming they stand there forever — the town loses its smith, and because
  eligibility keys on living somewhere the party has VISITED, the pool of people
  who could ever set out drained to zero and ventures stopped appearing entirely
  after 126 days. Same shape one level down: kill a venturer at the climax and
  `census.spawn_successor` births the heir in the wilds the predecessor never
  came back from, so the successor is moved to the venture's home.
  **Rationing is per PASS, not per candidate** — rolled per candidate it is not
  a rate limit at all, since a town of ten gets ten chances and something is
  born nearly every time. And a venture is only ever rolled for somebody the
  party could HEAR about (known to a PC, or living where they have been); a
  stranger's errand in an unvisited town is a die roll nobody will ever see.
- **A party may want somebody to FAIL, and opposing is not the mirror of
  accompanying.** `OPPOSES` (pc -> npc) is the stance — deliberately not
  `HOSTILE_TO`, since you can want the smith's guild seat to go to somebody else
  without hating the smith, and this edge ends when the venture does. An
  ACCOMPANIED venture pauses the offline roll because the party is standing in
  it; an OPPOSED one keeps rolling, harder, because sabotage is a thing you do
  and then walk away from — a declared enemy who has to stand there watching to
  matter is not an enemy, it is an escort. `effective_dc` adds `OPPOSED_DC` at
  roll time and never writes it back: a setback is permanent and lives in the
  stage, opposition lasts only while somebody is set against them, and storing
  them the same way would leave a relented grudge lifting the bar forever.
  `hinder` is one act of sabotage (a setback, COUNTED — the count is what gets
  traced back), `thwart` is the race the venturer lost (no roll; the goal is
  gone), `relent` backs off. **A hindered venture fails the way any venture
  fails**, so sabotaging the ranger who was going to make the road safe leaves
  the road unsafe — there is no consequence-free way to wreck somebody's work.
  **Discovery is rolled ONCE, at resolution**, scaled by how much the party
  actually interfered: a clock ticking through a covert operation is a lot of
  machinery for a question that only matters at the end, and a party that got
  away with it should get away with it cleanly. An OPEN enemy skips the roll.
  On discovery the deed lands in `relationships.record_deed` as **`betrayal`**
  if the party was ACCOMPANYING while working against them — which is what
  betrayal is, and the ledger already prices it at the slow-decay maximum —
  else `theft`. Opposing while accompanying is allowed and is the saboteur
  inside the camp, not a bug.
- **A recorded REASON is recorded history.** `WorldContext.render()` filtered
  relationship edges by TYPE, so every neutral `[[LORE:]]` — which is most of
  them, since `record_lore` opens a plain `knows` edge whenever its sentiment
  cues miss — was written to the database and shown to nobody. The point of
  writing a feud's origin down is that the next player who asks gets the same
  story.
- **A place that declares its puzzle tags is taken at its word.**
  `_scene_puzzle_tags` unioned a location's own `puzzle_tags` with every word in
  its name and the player's sentence, so a chamber tagged `sealed-door,
  mechanism` went looking for puzzles matching "i", "look" and "around" too —
  and matching is by token overlap, so that noise offers puzzles about nothing.
- **Streaming shows a PREVIEW, and hooks never reach it.** The reply a table
  reads is post-processed (hooks pulled, dice rolled and substituted, speech
  split out), all of which needs it entire. `narration/stream.py` streams the
  prose around the hooks as it is written — holding at most one character, since
  a cut can land between the two brackets of `[[` — and the preview is REPLACED
  by the authoritative blocks, never appended to.
- **A shop panel prices nothing.** Stock is `shops.roll_stock`, a pure function
  of (merchant, settlement scale, world week), so the stall and the DM's own
  context line are the same roll; buying goes through `process_trade_hooks`,
  the path a narrated deal takes. A second commerce path would let a player buy
  what the DM never saw for sale.
- **World persistence** = the graph, not maps. It's append-only: facts are opened/
  closed over in-world days (nothing deleted), and the DM is only ever fed the
  *relevant* subgraph via `get_world_context`, never the whole world.
- The world graph shares the backend's `oracle.db` by default (`get_engine`).
- Hex maps were intentionally dropped (not worth the complexity). Do NOT reintroduce
  hex/terrain-render code under `eight_card_system`.

- **A chase happens SOMEWHERE, and where changes what goes wrong.** The chase
  minigame picked its complications out of three buckets — urban, wilderness,
  dungeon — chosen by a keyword scan over whatever the DM wrote in the hook.
  The DM's words there are almost always about WHO is being chased, so nearly
  every chase outside a city got "wilderness": a fruit-seller's cart tipping
  across the way, a startled ox, a temple procession. In a marsh. Meanwhile
  `placelore` has known what country every place is in since the map-coherence
  layer went in, and five other systems read it. `_CHASE_FOR_TERRAIN` maps the
  world's whole vocabulary onto the buckets and six new ones were written
  (swamp, mountains, forest, desert, coast, river). **The order is: a bucket
  the DM named outright, then the ground their sentence describes, then WHERE
  THE PARTY ACTUALLY IS, then the generic** — and making that work needed
  `_guess_terrain` to return "" rather than "wilderness" when the sentence
  names no ground, because a default that is always truthy means the caller can
  never fall through to something better. `scripts/chase_smoke.py` drives the
  real hook.
