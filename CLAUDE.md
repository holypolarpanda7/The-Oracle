# The Oracle — Claude Code Project Guide

An AI Dungeon Master for Discord that runs a persistent, living D&D world.
Players create a character, "enter the world," and adventure while an LLM narrates.

## Environment

> **Reaching Windows services from WSL — read this before concluding "it's down".**
> The GPU services (ComfyUI :8188, Ollama :11434) and the Cloudflare tunnel run
> as **Windows** processes. WSL2 has its own network namespace, so `curl
> 127.0.0.1:8188` from the Linux side ALWAYS fails and proves nothing. The
> bridge is the project's **Windows venv**, which is callable straight from WSL:
> ```bash
> ./.venv/Scripts/python.exe -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8188/system_stats',timeout=6).read()[:80])"
> ```
> (`.venv` = Windows/`Scripts`; `.venv-linux` = what `uv run` uses in WSL.)
> Anything that must TALK to those services — the species-portrait generator,
> any diffusion render, an Ollama call — has to run under `./.venv/Scripts/python.exe`,
> not `uv run`. Use `uv run` for everything else (tests, DB work, parsing).
>
> Env vars do **not** cross into a Windows process by default; name them in
> `WSLENV` or they arrive as `None`. Windows paths, not `/mnt/...`:
> ```bash
> DATABASE_URL="sqlite:///D:/path/no spaces/oracle.db" WSLENV=DATABASE_URL \
>   ./.venv/Scripts/python.exe -m imagery.species_portraits --audit
> ```
>
> If ComfyUI really is stopped, start it yourself and wait ~40s:
> ```bash
> cd /mnt/d/ComfyUI && nohup ./.venv/Scripts/python.exe main.py --listen 127.0.0.1 --port 8188 > /tmp/comfy.log 2>&1 &
> ```
> (`launcher/run_comfyui.bat` is the same command; `COMFYUI_HOME` defaults to
> `D:\ComfyUI`. The launcher starts it, the backend, the bot and the tunnel.)
- **Package manager**: `uv` (`uv run` to execute, `uv add` to add deps)
- **Python**: 3.12+
- **Config**: `pyproject.toml` (deps live here; `requirements.txt` is legacy)
- **OS**: Windows (bash terminal via Git Bash; venv at `.venv/Scripts/activate`)
- **Secrets**: per-component `.env` files — `ai-dm-sicord-bot/cred.env`,
  `oracle-dm-backend/backend-cred.env` (never commit these)

## Architecture (separable systems)
1. **`ai-dm-sicord-bot/`** — Discord bot (discord.py). Modularized:
   - `oracle-dm-discord-bot.py` — entry point, wiring
   - `character_creation.py` — Avrae import + AI-guided creation, ephemeral channels
   - `backend_integration.py` — HTTP client to the FastAPI backend
   - `dm_commands.py` / `event_handlers.py` — commands & Discord events
   - `music_player.py` / `music_control.py` — ambient music through the DAVE
     voice sidecar. **A playlist holds MUSIC; a room recording is not music.**
     Freesound is a sound-effects library first, so a mood padded with
     "tavern ambience"/"village ambience" comes back with a field recording of
     a crowded bar — filed beside the songs and played in rotation with them,
     which is how a fresh table went song → four minutes of babble → song.
     Fallback queries name an INSTRUMENT or a genre, never a room, and
     `scripts/audio_classify.py` measures what got seeded (beat strength off
     the onset envelope; `--quarantine` moves the beatless tracks of a music
     mood into `<mood>/ambience/`, which `load_playlist`'s non-recursive glob
     already ignores). A table opens on `music_control.MENU_PLAYLIST` — a
     title screen and a CC wizard are not a scene — and `leave_menu_music`
     moves it off when play begins with no DM cue to follow.
   - See `MODULE_ARCHITECTURE.md` for the full module map
2. **`oracle-dm-backend/fastapi-dm.py`** — the "DM brain."
   - OpenRouter LLM call → narration
   - SQLModel character DB (`oracle.db`) — **the source of truth for characters**
   - Endpoints: `/chat`, `/reset`, `/enterworld`, `/register_character`, `/check_character`
   - In-memory `SESSIONS` history (per `guild:channel` session_id)
3. **`eight_card_system/`** — the **persistent world knowledge graph** (the
   "living world" backbone). NOTE: the old hex-map / terrain-render engine was
   removed; this name now belongs to the graph. Modules:
   - `models.py` — SQLModel tables: `Entity`, `Relation` (temporal, valid_from/
     valid_to in world-days), `WorldEvent` (append-only log), `WorldMeta` (day).
   - `graph.py` — `WorldGraph`: entity/relation CRUD, `move_entity`, `add_event`,
     and `get_world_context(pc, action)` — a BFS that returns ONLY the local slice
     of the world near the PC's location + entities named in the action.
   - `seed.py` — `seed_starter_world` + `place_pc` (starter region "Greenfields").
   - `extraction.py` — second-LLM-call change extractor: `extract_and_apply`
     reads (action + narration + context) → JSON `WorldDelta` → applies it.
   - `hoards.py` — the world buries treasure on its own account: on the entropy
     cadence a hoard SITE lands out past the party (a real place with real
     coords, prominence 0 so it never clutters a survey sheet) and a CHART item
     — which knows its target's slug — surfaces in a settlement. The DM hands
     over the object; the code knows where it leads, so it can't be leaked.
   - `cartography.py` — what a character brings to drawing a map, computed from
     the sheet. Discovery is deliberately OPEN: any feature whose text mentions
     cartographer's tools or map-making counts, and one that GRANTS the tool
     proficiency is read from its text (the Artificer's Cartographer subclass
     arrives through that door, not a name check). Rulings where a book names a
     neighbouring tool are flagged `house_rule=True` and are meant to be edited.
   - `placelore.py` — **the one terrain answer** three renderers share:
     `character_of(graph, place)` returns a `PlaceCharacter` with `scene_look()`
     (arrival art), `board_look()` (the battlemap floor) and `map_terrain()`
     (drawn country). Keeps `terrain` (the land it stands IN) apart from
     `biome` (the surface it presents) — a tavern is `interior` in `farmland`.
     A place narration invents with no biome inherits one and it is PERSISTED;
     never re-derive terrain per render or the scene and the board will drift.
   - `pantheon.py` — the original power families (gods, giant-gods, celestials,
     archfey, old gods, archdevils, demon lords) seeded as DEITY entities, plus
     the DM-gated `apply_divine_event`. **The GRAPH is the live roster** —
     `living_powers`/`pantheon_payload` read it, so a power born in play is
     offered by character creation and a slain one stops being offered. Never
     hard-code a deity list in a caller.
   - `demo.py` — runnable end-to-end demo.
4. **`rules/`** — SRD **rules reference** (structured game data). Seeded from the
   open, CC-BY-4.0 5e SRD dataset so the DM brain + dice roller get exact numbers.
   - `models.py` — `Monster`, `Spell` SQLModel tables (share `oracle.db`).
   - `ingest.py` — `ingest_srd()` downloads 5e-bits/5e-database JSON, upserts by slug.
   - `query.py` — `RulesLibrary` (get/search monsters & spells, `find_mentions`) +
     `format_*_brief` renderers for prompt injection; `ability_modifier`.
   - Structured half only; prose-rules RAG is a later, separate layer.
5. **`dice/`** — internal **dice roller** (no Avrae copy-paste).
   - `roller.py` — `roll(expr)` (NdM, modifiers, kh/kl), `double_dice` for crits.
   - `mechanics.py` — `ability_check`/`saving_throw` (d20+mod vs DC, adv/disadv),
     `attack_roll` (nat20 auto-hit+crit, nat1 auto-miss), `damage_roll`.
   - Wired into the DM brain: the LLM emits `[[ROLL: 1d20+5 | Stealth | DC 15]]`
     or `[[ROLL: 2d6+3 | Greataxe damage]]` and the backend substitutes the
     resolved result inline (`resolve_roll_hooks`). Single-voice UX.
6. **`vtt/`** — the **tactical board**: a square grid that opens ONLY for moments
   where position and timing decide the outcome (combat, spatial puzzles, chase
   terrain, trap rooms), then closes and hands play back to prose. See
   `docs/design/vtt.md`.
   - `models.py` — `TacticalMap`, `MapToken`, `MapEffect`, `MapEvent` (share `oracle.db`).
   - `terrain.py` — tile taxonomy (cost/sight/cover/hazard) + the `Grid` container.
     A board is one string per row, one char per 5-ft square.
   - `geometry.py` — 5e distance (5-5-5, and 5-10-5 behind a config switch), A*
     + Dijkstra movement, line of sight, the PHB corner cover rule, spell
     templates clipped by line of effect, field of view, OA triggers.
   - `mapgen.py` — 21 deterministic seeded layout generators; every board is one
     connected region with opposed spawn zones. `archetype_for()` maps loose DM
     language ("a smoky taproom") onto a generator.
   - `art.py` — top-down battlemap through `imagery/`; the picture is a TEXTURE,
     the tile grid is the truth. Offline = tiles only, never a broken board.
   - `scene.py` — `VttEngine`: open/close, tokens, validated movement, effects,
     fog; `state()` for the Activity, `render()` for the DM prompt.
   - `bridge.py` — the board and `combat/` stay in step: grid distance is written
     back as spacing bands, and bands the engine changed walk their token to match.
   - `triggers.py` — the whole "is a board worth it?" policy, tuned by `VttConfig`.
   - Boards carry a **medium** (`GeneratedMap.mode`: walk / swim / fly). Sea and
     sky layouts are only connected to a swimmer or a flier, so connectivity,
     spawn zones and token movement all key off it.
7. **`arena/`** — the **Proving Grounds**: a practice mode outside the world.
   3 overwritable level-1 slots → pick land/sea/air + a level + difficulty →
   climb through the REAL level-up flow → fight a code-rostered encounter on a
   real board. Nothing is remembered (no world clock, no extraction). Exists to
   exercise CC + level-up + combat/VTT on purpose. See `docs/design/arena.md`.
   - `environments.py` — the catalog (slug → domain, mapgen archetype, medium).
   - `encounters.py` — XP-budgeted roster building from the rules bestiary.
   - `loadout.py` — the **Quartermaster**: a conjured stipend scaled to the
     level being fought at, stall stock gated by rarity, and cart pricing. The
     server prices the cart, never the client; re-outfitting refunds in full.
   - Wiring lives in `_arena_*` in the backend; the screens in `Arena.tsx`.
8. **`airships/`** — **flying vessels**: a hull with its own AC/HP/damage
   threshold, crew stations that take damage and die separately from it, an
   elemental core that gates nearly everything (engaged / suppressed → hovers
   and crawls / broken → never moves again), piloting checks for anyone without
   the mark a ship expects, emergency repairs (once per docking), crashes,
   capped upgrades, and `journey.fly()` passages that report hours and hazards
   but NEVER coordinates — the same line `mapmaker` and `[[ROUTES]]` hold.
   The ENGINE is here; the NUMBERS are data (`owned_books/airships_overrides.json`,
   gitignored) with one self-authored generic vessel in the repo so a bookless
   checkout still flies. A vessel is also a world-graph PLACE, which is what
   makes the party, arrival art and movement work aboard it for free; the
   tactical layer already has `skyship`/`sky-islands` boards in the `fly` medium.
   Mobile bastions live in `bastion/mobile.py`: a bastion built into a vehicle
   travels if one of its facilities declares `propulsion`, and several helms
   crewing in shifts stretch the 8-hour day toward 24.

## Running
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
- Loot / affix demo: `uv run python -m loot.demo`
- Proving Grounds demo: `uv run python -m arena.demo [level] [difficulty]`
- Proving Grounds smoke test: `uv run python scripts/arena_smoke.py` (slots →
  level-up climb → bout → victory/defeat, engine *and* WebSocket, LLM stubbed)
- Session-feature smoke tests (all offline, fresh scratch DB, no GPU/LLM):
  `uv run python scripts/<name>_smoke.py` for `locale` (place/clock/weather/
  who's here), `chronicle` (journal + quests + bonds), `speech` (dialogue
  attribution), `itemart` (catalog vs player-named art, and that a renamed
  piece keeps its stats), `cultural_scripts` (culture -> typeface mapping),
  `affix` (a drop rolls properties that reach real mechanics), `forge`
  (tempering needs a smith), `routes` (roads costed from real geography, and
  no map data leaks), `map` (one terrain answer across scene/board/parchment;
  tool + knowledge gating; a sheet accrues across revisions), `airship`
  (core/helm/damage/repair/crash/upgrade + passages + mobile bastions), `bonds`
  (linked creatures: initiative dice, sight through cover, blink, rescue)
- Pantheon / patron-choice smoke test: `uv run python scripts/pantheon_smoke.py`
  (a god born in play becomes choosable in CC; an unmade one stops being offered)
- Activity UI harnesses (Playwright, against the offline demo — run
  `npm run build && npx vite preview --port 4173` in `activity-ui/` first, then
  `npx node <script>.mjs`): `feat-choices`, `spell-picker`, `levelup-spells`,
  `reprepare`, `mobile-smoke`, `arena-shot`, `vtt-shot`, `deity-shot`,
  `floors-shot` (the storey switcher: peek at a gallery, and what a connector
  looks like on the board), `race-dup` (species traits render exactly once per
  viewport), `granted-feat`
  (a background grants its Origin feat, choices and all), `feat-spells` (the
  two feat slots are independent: the granted feat is gone from the species
  pool, both feats' questions gate Onward separately, and a school-scoped
  spell pick lands on the Spells stage), `pframe-shot`
  (portrait corner ornaments stay corner-sized), `play-shot` (the play surface
  at desktop and phone: status bar, "here & now" rail, narration column, roll
  card), `chronicle-shot` (suggested-action chips send on tap; the Chronicle's
  journal and bonds tabs).

## Key facts & constraints
- **D&D Beyond has NO public write API.** You cannot create/store a character on a
  user's DDB account. DDB is read-only (via Avrae `!import`). The backend's own
  character DB is the source of truth — do not architect around DDB storage.
- **Dice**: an internal roller (`dice/`) is wired into the DM brain. The LLM emits
  `[[ROLL: expr | label | DC n]]` hooks and the backend resolves them inline via
  `resolve_roll_hooks` — the player never copy-pastes Avrae. The legacy
  `render_avrae_hooks`/`[[AVRAE:...]]` path remains in the file but is unused.
- **LLM**: Configurable via `LLM_BASE_URL`/`LLM_MODEL`/`LLM_API_KEY` env vars. Defaults to OpenRouter; set `LLM_BASE_URL=http://127.0.0.1:11434/v1/chat/completions` for local Ollama.
- **Rules content is split by SOURCE and DESTINATION**:
  - Open SRD (CC-BY-4.0) → seeded into `rules/` tables from code in the repo. Safe to commit.
  - **Owned books (WotC PDFs etc.) → LOCAL-ONLY ingestion** for this free campaign:
    `rules/owned_ingest.py` extracts text from the user's PDF library
    (`C:\Users\holyp\OneDrive\Documents\D&D`) into a gitignored workspace and parses
    mechanics into `oracle.db` (also gitignored). Book-derived DATA must NEVER be
    committed — no extracted text, no stat rows, no summaries of book content in
    repo code. The public GitHub repo carries only the tooling. Small third-party
    homebrew (Illrigger, Gunslinger) is summarized in own words in seeds — keep those
    concise-mechanical, never verbatim.
    Paste-and-translate override slots (all gitignored, see
    `rules/OWNED_IMPORT_FORMAT.md`): species, feats, **classes**, subclasses,
    spells, monsters, items, puzzles, backgrounds, **bastion facilities**
    (`bastion_facilities_overrides.json`) and the **airship fleet**
    (`airships_overrides.json` — vessels, stations and tuning). A class from
    an owned book goes in the slot, NOT beside Illrigger/Gunslinger in
    `rules/ingest.py`; those are small third-party homebrew, which the rule
    allows, and a WotC book's class is not.
    **Derived ART is not data and IS committed.** A rendered species portrait
    carries no book text, stat block or mechanics, so all of
    `activity-ui/public/assets/species/` is tracked, owned-book species
    included. The line falls between the picture and the words that produced
    it: the descriptors in `owned_books/species_looks.json` stay local.
  - Retrieval is selective — only fetch rules when the action needs a mechanic; prose
    lore stays out of prompts except brief mechanical facts.
- **Furniture is breakable, and the tile is what changes.** A pillar, door,
  crate or wall carries AC/HP (`terrain._BREAKABLE`, the project's OWN
  tuning, not a copied table). `[[VTT: damage | x,y | n | type]]` applies it;
  at 0 the tile becomes what it leaves behind, so the cover it granted and
  the way it blocked vanish with it and NOTHING else has to be told — cover,
  sight and movement all read the tile. State follows the `doors` precedent:
  terrain holds what a square IS, a JSON column holds what happened to it.
  Objects are immune to poison and psychic; material decides the rest.
- **Damage does NOT re-render the battlemap** — because nothing calls
  `render_art` on a break, not because the cache key lies. (An earlier
  attempt pinned the art to the PRISTINE layout signature; that made two
  tables who painted different furniture into the same generated room share
  one picture. The signature follows the CURRENT grid, so the key always
  matches what was drawn.) Wreckage is a small sprite instead.
- **Wreckage sprites: render large, cut, store small, PRE-render.** SDXL is
  trained at 1024, so 512-and-downscale is sharper than asking for 320 (and
  256 measured WORSE than 320). rembg (`u2netp`, Windows venv) cuts the
  background so a sprite is debris lying on the floor rather than a picture
  stuck to it; without rembg it feathers instead and still works. The whole
  catalogue is ~190 sprites (9 object kinds + 10 wreckage kinds x 10 board
  looks) — run `scripts/debris_prerender.py --render` once and a mid-combat
  smash costs a cache lookup, not a render.
  **A matte is checked, never trusted.** Told to cut stone out of a picture of
  the same stone in the same light, rembg fails three ways, and all three put
  a wrong thing on the board: it keeps everything (a pale BOX round a pillar),
  keeps nothing (an empty square where the rules say a crate is), or keeps the
  right shape at half strength (a smudge). So `art.cutout` measures what the
  matte kept and falls back to the raw render outside 8–97%, hardens soft
  alpha with a contrast curve, re-fits the subject to its square from the
  alpha's own bounding box (the model draws a crate a sixth of the frame often
  enough to matter, and how big it draws things is not steerable), and eases
  the corners so a failed cut never lands as a rectangle. Sprite prompts name
  the VIEW before the thing — "the flat circular capital of a pillar directly
  below the viewer", not "a pillar, seen from above", because the model's
  prior for any object is its elevation and a trailing modifier loses to it.
  `art.SPRITE_REV` versions the ref slug: bump it when the FRAMING changes, or
  boards keep serving sprites drawn to a framing that no longer matches.
- **A door belongs to the wall it interrupts.** Two consequences, both in
  code: `mapgen._door_on_wall` punches ALL the way through — most generators
  fill the grid with wall and carve a room inside it, so setting one square of
  the room's own ring to a door left it opening onto solid rock, an alcove
  that read as a door standing in a wall for no reason. And a door is DRAWN as
  a thin panel lying along its wall run (`terrain.aperture_axis` reads the
  direction off the grid; `render_image._paste_panel` draws it, jamb ticks and
  all), never as a picture filling its square — square and centred it reads as
  furniture parked on the floor. The control image leaves an aperture's
  PASSAGE faces open and keeps its JAMBS, or a door at the board's edge gets
  sealed shut by the out-of-bounds rule.
- **Every object and every wreck is NAMED on the board.** The walls-overlay
  argument applied to sprites: the picture may be atmospheric, the word is not
  a guess, and "broken pillar" is the answer to wreckage that appears from
  nowhere. One chip per run, not per square — two crates side by side are one
  fact, and labelling both prints each over the other. Wreckage also gets a
  deterministic dark scuff under its sprite, because stone rubble on a
  flagstone floor is the low-contrast case a render can lose.
- **Both boards draw the same room.** `render_image.py` (Discord PNG) and
  `activity-ui/src/lib/vttPaint.ts` (canvas) read the identical `state()`
  dict and must stay in step — objects, wreckage, panels, labels, fog tiers,
  and which tokens are visible. Sprites are matted ONCE by `art.sprite_png`
  and served to the browser over `/imagery/sprite/{id}`; a browser cannot run
  rembg, and two views cutting their own pillars differently is the same
  disagreement the grid-is-truth rule exists to prevent.
- **Fog is MEMORY; sight is LIVE. Both, or a door means nothing.**
  `TacticalMap.fog` records everywhere the party has ever seen and never dims
  — right for "have we been here", useless for "can we see it now".
  `VttEngine.sight()` is the second tier, recomputed per frame from real line
  of sight (which reads `blocks_sight` off the tile, so a closed door blocks
  and an open one doesn't, for free) and shipped as `state()["sight"]`. Never
  seen renders opaque black; seen-but-not-watched gets a cold veil; only live
  sight is clear. A foe standing where nobody can see is NOT DRAWN at all —
  in either view — because fog over a token you already drew hides nothing.
  `sight()` returns None when there is no fog, and both renderers treat that
  as "everything visible".
- **The painting may not invent terrain the rules don't have.** "The art is a
  texture, the grid is the truth" settles who WINS an argument; it doesn't stop
  the argument. A model given a dungeon floorplan will paint a pool across dry
  flagstone, and then a player asks how deep it is and the DM — who only ever
  sees the grid, never the picture — says there is no water there. Nobody is
  wrong and everybody is confused. So `Grid.absent_terrain_negative()` forbids
  water/lava/chasm/ice/sky to the render unless those tiles are actually
  present (a reef keeps its water, a dungeon doesn't get any), derived from the
  grid so it can't disagree with the art cache keyed on that same grid. Fire is
  deliberately NOT on the list — negating flame takes the torches out of every
  dungeon. For whatever still slips through, the DM board tells the model that
  unlisted scenery is decoration to be narrated as shallow/dry/harmless, which
  is true of every square the legend calls open floor. Terrain that IS on the
  grid needs none of this: `legend(rules=True)` already gives the DM
  "~ shallow water (difficult, costs double; swimmable)".
- **Light is a rule, and `survival/light.py` is the ONE place it is decided.**
  `perceives(level, distance_ft, senses)` answers "can this creature make that
  out" for everything — the board's `vision()`, the DM board's text, and the
  combat engine's advantage — so they cannot drift. Before it, `can_see` was
  pure geometry: two creatures in an unlit crypt saw each other perfectly and
  `lighting` was decoration. `VttEngine.light_map()` is the spatial half:
  ambient board lighting, raised by `kind="light"` effects (radius bright,
  2x radius dim — the 5e convention already in `_SOURCES`) cast as FIELD OF
  VIEW so a torch doesn't light through a wall, then lowered by `obscured`
  effects (which existed on `MapEffect` and had never been read, so a fog cloud
  blocked nothing). Senses live in `MapToken.senses` as `{"darkvision": 60}`
  and are looked up lazily from the bestiary (`monster_slug`) or the species
  (`character_id`) when absent, so a token nobody told about darkvision still
  gets it. **`parse_senses` must handle raw book lines** —
  `{"raw": "Darkvision60ft.;PassivePerception15"}` is how a large share of the
  ingested bestiary stores it, and reading only tidy rows silently costs the
  wolf its darkvision in the direction that changes fights. The combat payoff
  reaches `_attack_advantage` through `BoardSpatial.can_see` (a callback, like
  every other board→combat channel): can't see the target = disadvantage,
  target can't see you = advantage, and in a dark room both apply and cancel —
  which is correct, and is why darkvision is worth having.
- **A board is sized by the FIGHT, not by one number.** `triggers.board_size_for`
  starts from the scene kind's default (`DEFAULT_SIZE`: combat 24x18, explore
  30x24, chase 34x14…) and grows it for room to STAND (total footprint on the
  board), room to MOVE (the fastest creature present — three moves to cross, so
  a charge is a decision rather than the whole encounter) and room to SHOOT
  (`VttConfig.outdoor_range_ft`, 150 by default). The last two apply OUTDOORS
  only: a tavern is the size of the tavern, and being outranged indoors is what
  a building is, not a sizing bug. `SCALES` (duel/skirmish/battle/pitched/
  mounted) let a DM force it via `[[VTT: open | … | scale=mounted]]` or
  `size=WxH`, for the charge that starts with two riders. Two rules were
  UNREACHABLE before this and are the reason it exists: a dashing warhorse
  crossed the whole 120-ft board in one turn, and a longbow's 150-ft normal
  range was longer than the battlefield, so long-range disadvantage could never
  fire. `_vtt_open` used to pass `default_width/height` unconditionally, which
  overrode the per-kind table and made every board 24x18 including exploration
  ones — pass width/height only when someone actually asked for a size.
  `bridge.roster_for` builds the (size, speed) list, because a Combatant row
  carries neither and the bridge is already where the rules library lives.
- **Upper floors are grids, and the only new rule is the CEILING.**
  `TacticalMap.levels` holds one terrain grid per storey with its own
  `base_ft`; level 0 is the board's own `terrain`, so a single-storey board
  carries nothing and every caller written before floors existed keeps working
  (`grid_of(row, level=0)` is defaulted, not required). This was cheap for one
  reason: **height was already folded into every distance, reach, cover check
  and spell area**, so `token_height_ft` adding the level's base was enough to
  make an archer on the gallery 20 ft away rather than standing on your head.
  What genuinely needed writing: `_occupied` is per floor (two creatures share
  an x,y on different storeys and are not in each other's way); a new level
  starts as ALL VOID because a gallery is the strip you build and everywhere
  else is open to the hall below; and a VOID square is what sight passes
  through — anywhere else there is a floor between you, which is the one thing
  an upper storey adds that height alone never expressed. Levels are otherwise
  sealed: `add_stairs` links a square to a square, both ways, and
  `take_stairs` is the only way across. Both renderers draw ONE floor (the PNG
  takes `level=`, the Activity follows your own token) because a gallery drawn
  over the hall it overlooks is unreadable. **Looking at a floor is not
  standing on it**: the Activity's floor strip lets a player peek upstairs
  before deciding to climb, marks which storey they are ON separately from
  which is DRAWN, and offers the one button that actually moves them —
  `vtt_stairs`, which the server gates exactly like a move and re-checks
  against the engine, since the client is not the authority. Connectors are
  painted on the board (▲/▼ plus the destination's name) because a player
  cannot choose a stair they cannot see. Anything belonging to a floor has to
  SAY so — `MapEffect.level` exists for that reason: peeking at the gallery is
  what made the hall's fireball burning on both storeys obvious.
  **Light, fog and live sight are per floor**, and each is stored the way
  terrain is (level 0 on the row, upper floors inside `levels`) because they
  are the same kind of fact and splitting them any other way would put two
  answers to "what does this storey look like" in two places. `light_map`,
  `light_at`, `sight` and `reveal` all take a `level`; a torch on the gallery
  does not light the hall, walking the hall does not reveal the gallery, and
  `reveal_from_party` lights the floor each creature is actually standing on.
  `state()` still ships the ground floor's flat, where it has always been, and
  repeats every floor's own inside `levels[]`.
- **A rider has no movement of their own.** `MapToken.mounted_on` names the
  mount (on the RIDER, the same shape as `grappled_by` and for the same reason:
  the carried one is whose movement stops being its own). They share the
  mount's space, so there is no second position to keep in step — `move_token`
  REFUSES a mounted creature and names the remedy, and moving the mount places
  the rider on the same square. Not the captive-dragging path: a captive is
  hauled to a square NEXT to its hauler, a rider is in the saddle. Mounting
  costs half Speed and needs an animal at least one size larger. A shoved mount
  or either of them knocked down puts the rider to a DC 10 Dex save the board
  rolls itself; failure lands them prone beside it. Placement bypasses go
  through `_place`, never `update_token` — that method refuses x/y on purpose
  so nothing sidesteps the movement rules by editing a position.
- **Squeezing is decided by the PATH, not the destination.** A Large creature
  crossing between two halls through one narrow door has a destination that
  fits perfectly; it is the way there that doesn't. So `move_token` tries the
  full-size path first and retries at one size smaller before giving up, marks
  `MapToken.squeezing`, and doubles the cost (stacking with crawling, because
  they are two separate extra feet). The flag is remembered rather than
  recomputed because `_attack_advantage` asks about it at a different moment —
  squeezing is disadvantage on your attacks and advantage on attacks against
  you, reached through `BoardSpatial.squeezing`.
- **Underwater combat is enforced, not requested.** The weapon rules used to
  live as a `dm_note` sentence in `arena/environments.py` telling the model to
  remember them by hand — the last place asking the LLM to apply a mechanic.
  `BoardSpatial.underwater()` (the board's medium is `swim`) and `swims(c)` now
  carry it to `_attack_advantage`: a melee weapon that is swung rather than
  thrust is at disadvantage for anyone WITHOUT a swimming speed, a ranged
  weapon that isn't a crossbow/net/thrown spear is at disadvantage for
  everyone, and past normal range it misses automatically (rolled and spent,
  not refused — that is what the rule says happens). Two traps: `movement_mode`
  is NOT "has a swimming speed" — on a swim board it is `swim` for everyone
  including the dwarf who is drowning, so `MapToken.swim_speed_ft` is looked up
  from the stat block the way senses are; and the weapon allowlists match by
  SUBSTRING, because the list is of weapon kinds and the table is full of
  "Trident of Warning". Spell attacks are untouched — the rule is about
  weapons, so passing no weapon skips it. **Fire resistance while immersed is
  NOT enforced**: the engine has no damage-type or resistance layer at all
  (weapons carry `"1d8"`, not a type), so the DM board states that one rule
  explicitly as the DM's to apply. Building damage typing is its prerequisite.
- **Cover is a question about HEIGHT, and the engine finally has a number for
  it.** `Tile.cover_height_ft` says how tall an obstacle SCREENS and
  `terrain.profile_height_ft(size, prone)` how tall a target presents; an
  obstacle at least as tall as the target grants TOTAL cover instead of its
  listed rating. That is not a house rule — the DMG defines total cover as
  "completely concealed by an obstacle", and lying flat behind a crate is how
  you get completely concealed by a crate. Two guards on it: the height is set
  ONLY on obstacles limited by height (crate 4 ft, low wall 3 ft, table 3 ft,
  altar 4 ft) — a pillar or tree is three-quarters because it is NARROW and
  lying down doesn't widen a trunk, a portcullis is bars and lying down doesn't
  make them opaque — and `attacker_height_advantage_ft` takes it away again, so
  you cannot lie behind a crate to dodge an archer on the gallery shooting down
  over it. Total cover then means what 5e says it means: `vision()` reports the
  target as unseeable and the combat engine already refuses the attack, so
  going prone behind low cover really does make you untargetable, and lets you
  Hide. `cover_between` with no height behaves exactly as before.
- **Hiding is a CONTEST, and it is personal.** `MapToken.hidden` used to be a
  bare bool a DM set, tied to nothing. Now the board decides ELIGIBILITY
  (`hide_eligibility`: every living enemy must either not perceive you at all,
  or be looking through three-quarters cover or better — dim light does NOT
  qualify, and an enemy with blindsight blocks the attempt no matter how much
  cover you have, because cover is protection from being *seen*), the CODE
  rolls the Stealth check (`hide`, DC 15, 2024 rules), and the result is KEPT
  as `stealth_dc` — without it every Search action is a check against nothing
  and the DM invents a DC each time. `found_by` tracks who has beaten it, so
  the guard who spotted you sees you while the rest of the room does not; one
  bool cannot say that, and `state()` filters each side's board by it. Hiding
  breaks on `unhide` (attacking, casting aloud) and by itself in `move_token`
  when the new square no longer qualifies. An enemy whose passive Perception
  already beats the roll is added to `found_by` at hide time — making them
  spend a Search action on something they could not have missed is wrong.
  Hooks: `[[VTT: hide|search|unhide]]` — **and they are in `_VTT_HOOK_ACTIONS`**.
- **A door belongs at the threshold the corridor made.** `_threshold_doors`
  hangs doors where a corridor breaches a room's wall ring; punching one into
  an arbitrary wall square leaves the corridor's own mouth gaping beside it,
  so the door guards nothing and closing it changes nothing. This only works
  because `mapgen._connective` treats a closed door as a way THROUGH for
  connectivity — judged by `passable` alone, a room behind a shut door reads
  as cut off and `_connect_regions` obligingly carves a second way in. The
  selftest asserts both halves: one connected region *granted the doors*, and
  that shutting them genuinely divides the warren.
- **The battlemap is CONDITIONED on the layout, not described to it.** A text
  prompt cannot say where a wall goes: told "a dungeon room with stone walls",
  the model paints a plausible room whose walls land nowhere near the grid's.
  That is not a tuning problem and no wording fixes it — the picture and the
  rules simply depicted different places. So `art.control_image` draws the
  grid as an architectural floorplan (white strokes on every wall FACE, gaps
  at doorways) and the render is conditioned on it through an SDXL scribble
  ControlNet (`imagery.map_controlnet`, model in ComfyUI/models/controlnet/).
  Empty config = off, and the graph is untouched. Verified by rendering the
  grid, the control image and the art side by side — see map-probe/alignment.
  Discrete objects are deliberately NOT in the control image: they are drawn
  as sprites, because a pillar that can be smashed has to be able to change.
- **Discrete objects are SPRITES, drawn from the grid on their own squares.**
  `terrain.OBJECT_SPRITES` (pillar, crate, furniture, tree, altar, low wall,
  door, open door, portcullis) — keyed by KIND, so eight pillars are one
  picture. Two reasons a painting can't do this: it cannot place anything on
  a named square, and a painted pillar cannot become rubble. Without them a
  player cannot find a door that mechanically exists, and wreckage appears
  from nowhere because the thing that broke was never visible. `objects_for`
  reads the TERRAIN each time rather than storing a list — the grid already
  says what stands where, and a broken square is no longer its object.
  Structure (walls, rock faces) is deliberately excluded: ControlNet puts
  that in the painting itself.
- **A painted board still draws the rules on top.** `render_board_png` given
  an `image_lookup` composites the battlemap and its debris — then OUTLINES
  the mechanically significant tiles, and washes only ground that stops or
  hurts you. A diffusion model cannot put a pillar on square 6,5, so the art
  and the grid disagree by design; the outline is what stops that
  disagreement reaching the players. Solid tints over every wall smother the
  picture — that was the first attempt and it was wrong.
- **DEPRECATED-NOTE:** The base art is pinned to the
  layout as GENERATED (a pristine `layout_signature`), because the live grid
  hashes differently the moment anything breaks and would otherwise repaint
  the whole room over one square. Wreckage is a small sprite drawn on top,
  keyed by (what it became, material, board look) so it is SHARED across
  rooms. Measured warm on this rig: a 320px sprite is 6.1s against 13.5s for
  a full battlemap — ~2x cheaper, not proportional to pixels, because the
  per-call overhead dominates (256px measured WORSE than 320px). The real
  saving is the sharing, exactly as with the item-art catalogue.
- **The board is THREE-dimensional.** Elevation was stored (`elevation_ft` on
  tokens, a per-square elevation map) and measured by nothing, so a dragon
  hovering 100 ft overhead read as 10 ft away and a wyvern 60 ft up provoked
  opportunity attacks. `geo.token_distance_ft(..., dz_ft=)` folds height in
  as a third axis under the board's own diagonal rule, and
  `VttEngine.token_height_ft` / `height_gap_ft` are the ONE place height is
  decided. Every consumer passes it: measure, reach, grapple, opportunity
  attacks, spell areas (a flat template excludes what's far above its
  origin), the DM board's distances, and `bridge.BoardSpatial` — which is
  what gates the combat engine's weapon ranges. This matters most on the
  four archetypes fought off the ground (`sky-islands`, `skyship`, `reef`,
  `open-water`) and anywhere airships fly.
- **The AI never decides where a creature can be — the grid does.** The art is
  generated FROM the tile grid, so water on the picture IS water in the rules;
  `move_token` refuses a square the creature's medium forbids and there is no
  path for the model to overrule it. Two things make that legible rather than
  frustrating: the DM board's legend gives each present tile its RULE
  (`W deep water (swimmers only — a walker can't be here)`), and a refusal
  names the remedy (`…would have to swim ([[VTT: token | X | swim]])`). A
  square that DEMANDS a medium is adopted on arrival — one-way only, since
  adopting can unblock a creature but dropping one could strand a flier.
- **Battlemap art is reused until something really changed.** The bucket is
  (layout signature, biome + lighting + CONDITIONS). The signature hashes the
  tile grid, so painting one square regenerates the picture; the conditions
  carry season and precipitation, so the same room in snow earns its own art
  and the same room in the same weather reuses it. Interiors report a stable
  condition, so a taproom is never repainted for weather it can't feel — and
  time of day stays OUT of it, because `lighting` already carries it.
- **Speed 0 is a movement rule, so the BOARD enforces it.** A grappled or
  restrained token can't walk (`[[VTT: grapple/restrain]]`) — but it can still
  teleport, and it can still be shoved, because a grapple holds you rather
  than your magic. `grappled_by` names the holder, not a bare flag, because
  the grappler DRAGS their captive along at half speed. A grapple breaks by
  itself the moment the pair are out of reach, whichever of them moved.
  Prone doubles movement (crawling) and standing costs half Speed.
- **Forced movement is not movement.** `VttEngine.shove` (`[[VTT: push/pull]]`)
  ignores the target's speed, provokes NO opportunity attack, and travels a
  straight line stopping at the first obstacle — never `move_token`, which
  paths around things and charges the victim. 34 ingested features push or
  pull; before this there was no correct way to say so.
- **`_VTT_HOOK_ACTIONS` is an allowlist.** A handler with no entry there is
  silently dropped before the dispatcher ever sees it. Add both, always.
- **A creature LINK overrules the board on purpose.** `combat/bonds.py` is a
  generic three-lever link between creatures — bonus initiative dice, mutual
  sight/targeting *through* cover, and an emergency rescue — scoped to a table
  and an owner, replaced (never stacked) when re-granted. The Cartographer
  artificer's Adventurer's Atlas is one CONFIGURATION of it, not a special
  case in code; the label and the numbers come from the `[[BOND: grant]]` hook.
  Both overrides are callbacks (`VttEngine(linked=...)`,
  `roll_initiative(bonus_dice_for=...)`) so `vtt/` and `combat/` keep knowing
  nothing about any particular feature. `VttEngine.blink` is the matching
  movement: a short teleport whose long form is gated on the link, and which
  CHARGES movement (unlike `move_token(teleport=True)`, which is free).
- **The tactical board is a spotlight, not a stage.** Play stays theater-of-the-
  mind; `vtt/` opens a grid only for moments where position decides the outcome,
  and closes it after. The LLM decides FICTION (a board opens here, the fireball
  lands on the altar); the code decides MECHANICS (layout, path, squares, cover).
  Never let the model author a layout or a distance. This does NOT contradict the
  "hex maps were dropped" rule below — that was a world-map render engine; this is
  an encounter-scale square grid.
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
- **NEVER delete `oracle.db` to wipe the world.** Use
  `uv run python scripts/world_wipe.py` (prints a plan; `--yes` applies). One
  database holds three lifecycles, and the file-delete cannot tell them apart:
  world state (graph, characters, combat, bastions, economy, hazards,
  reputation, boards) is disposable and re-seeds on boot; the `rules_*` tables
  are NOT — only the SRD half re-downloads, while the owned-book half (khoravar,
  kalashtar, hexblood, reborn, the shifter lineages…) has to be re-parsed from
  the PDF library; and `entity_image` is hours of GPU art with nothing to
  re-derive it from. The wipe script deletes rows from the world tables only,
  and **refuses to run on a table it doesn't recognise** — classify any new
  subsystem's table there rather than letting it guess.
- **World persistence** = the graph, not maps. It's append-only: facts are opened/
  closed over in-world days (nothing deleted), and the DM is only ever fed the
  *relevant* subgraph via `get_world_context`, never the whole world.
- The world graph shares the backend's `oracle.db` by default (`get_engine`).
- Hex maps were intentionally dropped (not worth the complexity). Do NOT reintroduce
  hex/terrain-render code under `eight_card_system`.

## Conventions
- Prefer editing existing files over adding new ones; keep modules single-purpose.
- Don't reintroduce DDB-as-storage assumptions.
- Don't block the playable MVP (create char → enter world → narrated play) on
  advanced world-graph features.

## Committing & syncing
- Commit work in logical, single-purpose chunks and push after each — don't let
  changes pile up uncommitted.
- **Scope every commit deliberately.** Stage the specific files for that chunk
  (`git add <paths>`); never `git add -A`. The working tree usually carries
  unrelated in-progress changes (and sometimes pre-staged ones) — check
  `git status` and keep them out of your commit.
- **On WSL, push via Windows git**: `git.exe push origin master` (routes through
  the Windows credential manager; the Linux `git` has no stored creds). Regular
  `git` is fine for local ops (add/commit/status).
- Never commit secrets (`*cred.env`, `.env`), `oracle.db`, or any book-derived
  data — see the rules-content split in "Key facts & constraints".
