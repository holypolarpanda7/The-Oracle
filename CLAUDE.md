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
> `WSLENV` or they arrive as `None`. Windows paths, not `/mnt/...` (spaces are
> fine, quote them):
> ```bash
> DATABASE_URL="sqlite:///D:/Projects/The Oracle/oracle-dm-backend/oracle.db" \
>   WSLENV=DATABASE_URL ./.venv/Scripts/python.exe -m imagery.species_portraits --audit
> ```
> **The database is `oracle-dm-backend/oracle.db`, NOT `oracle.db` at the repo
> root.** That is where the Linux-side default (`ImageStore()`, `get_engine()`)
> resolves to, so a Windows run pointed anywhere else quietly writes a whole
> second database that WSL never reads — the renders "succeed", the audit says
> they exist, and every lookup from the backend returns nothing. Verify the two
> sides agree before a long batch:
> ```bash
> uv run python -c "from imagery import ImageStore; print(ImageStore().engine.url)"
> DATABASE_URL=... WSLENV=DATABASE_URL ./.venv/Scripts/python.exe -c \
>   "from imagery import ImageStore; print(ImageStore().engine.url)"
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
   - `equipment.py` — the loadout: what is WORN and what is HELD, in which
     hand. Pure logic over inventory dicts + a catalogue lookup, so the
     backend, the arena's Quartermaster and the smoke tests share one answer.
   - `mastery.py` — the 2024 Weapon Mastery engine. Mechanisms committed, the
     weapon→mastery table and class counts in the gitignored slot.
   - `checks.py` — the skill→ability table and the d20 modifier arithmetic,
     so the DM names a check instead of computing one.
   - `damage.py` — damage TYPES: parsing typed dice out of (OCR-damaged) book
     prose, reading a creature's resistances out of either shape the bestiary
     stores them in, and the halve/double/zero arithmetic itself.
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
- Feat smoke test: `uv run python scripts/feats_smoke.py` (a feat's questions,
  its grants, its named options, the resource it hands over and the at-will
  spell it grants — all the way to what the DM is told)
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
  (tempering needs a smith), `resistance` (damage types out of scanned prose,
  defences out of both bestiary shapes, and the arithmetic through the real
  engine — a mace on a skeleton, a fireball on a fire elemental), `routes` (roads costed from real geography, and
  no map data leaks), `map` (one terrain answer across scene/board/parchment;
  tool + knowledge gating; a sheet accrues across revisions), `airship`
  (core/helm/damage/repair/crash/upgrade + passages + mobile bastions), `bonds`
  (linked creatures: initiative dice, sight through cover, blink, rescue),
  `targeting` (what a spell targets out of OCR-damaged prose; who the board
  says may be hit and why not the rest; a template clipped by line of effect;
  the action bar reaching the engine as an intent, and refusing when it can't),
  `skins` (what a square is MADE of versus what it DOES: a skin changes no
  rule, may reshape a quoted height but never restate it, a tent you can walk
  into, a watchtower top that is a real storey, a hold at -8 ft)
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
    spells, monsters, items, puzzles, backgrounds, the **weapon-mastery table**
    (`weapon_masteries_overrides.json` — which mastery each weapon carries and
    how many a class may have; absent = mastery off), **bastion facilities**
    (`bastion_facilities_overrides.json`), the **airship fleet**
    (`airships_overrides.json` — vessels, stations and tuning) and the
    **summoned spirits** (`summons_overrides.json` — recipes, not rows). A class from
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
- **Every board draws the same room.** `render_image.py` (Discord PNG),
  `activity-ui/src/lib/vttScene3d.ts` (the isometric board) and
  `vttPaint.ts` (the flat canvas it is replacing) read the identical `state()`
  dict and must stay in step — objects, wreckage, panels, labels, fog tiers,
  and which tokens are visible. Sprites are matted ONCE by `art.sprite_png`
  and served to the browser over `/imagery/sprite/{id}`; a browser cannot run
  rembg, and two views cutting their own pillars differently is the same
  disagreement the grid-is-truth rule exists to prevent.
  The browser renderers meet at `lib/boardView.ts` — a `BoardView` is all
  `VttOverlay` knows, so the interaction shell, the action bar and the token
  layer are written once. **Tokens are DOM over the canvas, never painted into
  it**, which is what makes a camera-facing character free on the isometric
  board: an element over a canvas already faces the viewer, so a billboard
  needs a projection (`BoardView.screenOf`) and no character art at all.
  `vttPaint.ts` and `canvasBoardView.ts` are a deliberate fallback for a
  webview with no usable WebGL, and retire together once the isometric board
  reaches parity.
- **Python owns the board's SHAPES; the TypeScript is generated.** Once objects
  stopped being plain boxes, their shapes became as load-bearing as the camera:
  `vtt/isocam.py` rasterizes them into the depth map the painted layer is
  conditioned on, and `vttScene3d.ts` builds them as the geometry the player
  looks at. A hand-mirrored table drifts, and the failure is INVISIBLE — the
  painting simply lands on furniture nobody is looking at.
  `scripts/gen_board_shapes.py` writes `boardShapes.generated.ts`, and
  `iso_alignment_check.py` runs it in `--check` mode, so a change to one side
  that never reached the other fails at the gate. The per-instance rules
  (variant, quarter-turn, height jitter) are functions rather than tables, so
  the gate runs the same squares through BOTH languages — with big coordinates
  on purpose, since the hash multiplies and the two only diverge once the
  product passes 2^32 and JavaScript's bitwise operators wrap it.
- **A wall is a thin skin where solid meets open floor, not a five-foot cube.**
  Drawn as a full cube it presents an enormous top face; a ring of them is a
  rim, and a rim around a floor is a TRAY, which is what every enclosed room
  read as. The square is still fully solid in the rules — the floor strip beside
  the slab is a drawing. Keying the slab on which way the wall RUNS fails: mapgen
  walls are commonly two squares thick, so every square reads as a corner and a
  band of pluses notches at every seam. Draw the FACE, and let buried squares
  draw nothing.
- **Never vary a height the RULES quote.** Per-instance jitter gives walls,
  pillars and trees a little life, but `cover_height_ft > 0` marks the tiles
  whose height IS the answer — a crate screens four feet, a low wall three — and
  those are drawn exactly. A player deciding whether they can break line of
  sight reads it off the board, and against a stronger enemy that decision is
  most of the fight; a crate drawn shorter than its neighbour invents a
  difference the engine will not honour, exactly where being misled is expensive.
- **A tile code says what a square DOES; a SKIN says what it is MADE OF.**
  `vtt/skins.py` is the same split `placelore.py` makes between the land a
  place stands in and the surface it presents, one level down. Before it a
  `#` was the same dressed masonry in a sewer, on a mountainside and around a
  tent, so the pass read as a built corridor and the reef's columns stood
  pristine and carved on the seabed. More tile CODES is the wrong fix and the
  project already rules it out — a cliff and a wall are not different rules,
  they are different stuff. A skin carries a substance (one swatch shared by
  every coral thing everywhere), a silhouette, and what to tell the painter.
  It is invisible to every rule, and that is what makes the vocabulary safe to
  grow. Derived from the ARCHETYPE where it is uniform, recorded per square
  (`GeneratedMap.skins`, sparse, like elevation) where a generator built an
  exception. **A skin's silhouette wins over everything including the
  wall-face model** — that model exists to stop an enclosed ROOM reading as a
  tray, which is the wrong worry for a mountainside. **It may reshape a height
  the rules quote but never restate one**: `_check_heights` refuses at import,
  and caught three on the first run (a ship's rail is three feet whether it is
  timber, brass or grown chitin). A skin that only says what the model would
  paint anyway is not free — `masonry` on the dungeon boards bought nothing
  and was removed.
- **A part may be a PRISMATOID, and that is what stops everything being a
  cube.** A part was six numbers — an axis-aligned box — so every silhouette
  was built by stacking boxes, and a slope came out as a flight of terraces.
  The second form is `(bottom polygon, top polygon, y0, y1)`, told apart in
  both languages by whether the first element is a number. It subsumes the box,
  so gaining it rewrote nothing, and it buys exactly three things: a narrower
  top is a **taper** (a tent's canvas drawn in to a ridge, a hipped roof, a
  hull with tumblehome), an OFFSET top is a **lean** (the raked legs of a
  timber watchtower, a ladder, a guy rope), and more than four vertices is a
  **cut corner**. **Winding is normalized in `skins.solid()`, never trusted** —
  a ring written the wrong way round points its normals into the solid, so the
  renderer culls every face you should see; the first watchtower roof simply
  did not appear and nothing in either program looked broken.
- **There are three orientation rules, and a tent needed the third.** `yaw_of`
  turns a boulder any way at all; `run_axis` lines a rail up with its run; and
  `out_axis` points a part at the OUTDOORS. Without the last, a tent's canvas
  could only lean the same amount toward both faces of its wall — which against
  a five-foot square and a seven-foot tent is not leaning at all, and is why
  three rewrites of the tent still came back as pens. A doorway counts as part
  of the wall it interrupts (the project's existing rule about doors), or a
  tent takes its own flap for the weather and pitches the roof at the way in.
  **An outward skin does not ROLL for its arrangement**: 0 is the plain run and
  1 is the CORNER (`out_corner`), because four of the twelve squares in a
  tent's ring face the weather on two sides at once and a shape aimed at one of
  them leaves the other a sheer face — two pitched sides and two cliffs.
- **A THIN wall is not the skin of a mass.** `wall_parts` draws a slab hugging
  each open side, which is right for the face of a thick band and wrong for a
  wall with two faces: hugging BOTH sides of a one-square bulkhead draws two
  slabs with a slot down the middle, which is how a ship's deckhouse came back
  with double walls and a corridor between them. Two or more open sides means a
  thin wall, and a thin wall is drawn on its CENTRELINE — a hub plus an arm
  toward each square it carries on into — so it is one wall, mitres its own
  corners, and stops cleanly at a stub end. It keeps less top face than a full
  cube, so the tray this rule exists to prevent stays prevented.
- **A MASS picks its shape from a coarse hash; an OBJECT picks per square —
  and a boulder is an OBJECT.** `variant_smooth` samples over two-square
  blocks. Only rock bordering open floor is drawn, so a one-square shell whose
  every square chose its own height has nothing left to connect it. Three more
  things the mountain pass needed, all measured: the shell must be judged on
  all EIGHT neighbours (`isocam.exposed`), because a wandering track steps
  diagonally as often as squarely and a square whose only open neighbour was a
  diagonal was drawn as buried, notching the face into separate towers; the
  blocks must overhang their squares by about a tenth, because two diagonal
  neighbours otherwise meet at a single point; and the pass must not sprinkle
  its chasm through the rock at random, because a few hundred holes leave every
  remaining square bordering one and the whole face shatters. **A cliff and a
  boulder are not one skin.** Both are granite and they want opposite
  silhouettes — a cliff fills its square so neighbours merge into a face, a
  boulder stands alone and needs an outline — so sharing one drew every fallen
  stone as a full-square fourteen-foot block.
- **A thing bigger than a square cannot be drawn a square at a time.** A
  vessel's deck is carved out of a grid, so its outline is a staircase, and
  cutting each step's outer corner within its own square joins the steps into a
  line — a one-square line. Joining the corners FARTHEST from the hull's middle
  needs the outline as a LOOP, and no square can see one. So `vtt/hull.py`
  traces it: the boundary of each vessel-bodied region, the notch vertices
  dropped where the step is short (`MAX_SMOOTH_RUN`, or a chord swings across
  the whole waist and hangs two square yards of hull over the water), the
  triangle each dropped notch gave up kept so the deck reaches its own hull,
  and the bottom mitred round the whole loop — which per-square geometry could
  never do, because the mitre has to reach ACROSS squares.
  **It is traced once on the server and SHIPPED in `state()`**, and that is the
  point: the shape tables are data and so can be generated, the camera is
  arithmetic and so has to be gated, but this is an algorithm over the board,
  and the only way to add one without giving two languages a chance to disagree
  is to have one of them do it. Two traps, both hit: a vessel's body is full of
  HOLES (the mast's square, the cabin's, every crate) so only the outer loop is
  the hull; and a boundary can PINCH, where the walker must always turn as
  tightly as it can back toward the region or the outline breaks into
  fragments that then draw little hulls inside the deck.
  **The taper is nearly nothing (`HULL_TAPER`, a tenth of a square) and that is
  measured**: the mitre closes a corner within one square and could not reach
  across to the next, so at 0.42 every step of the bow opened a three-foot
  wedge of sky. Same lesson as `SKIRT_INSET` board-wide: flush faces meet.
  (The per-square chamfer that came first was deleted, not left standing. A
  code path nothing reaches is a trap for whoever comes next.)
- **A skyship is not a boat that happens to be up.** They shared a hull plan
  and a deck skin and came back the same vessel. A sea hull is fine at the bow,
  full through the waist and flat at the transom, and sits IN the water; an
  airship is slender and fine at BOTH ends — nothing about air rewards a
  transom — and you see its whole keel, so it gets a side half again as deep.
  `_hull(plan=)` and two deck skins.
- **A timber watchtower is not a stone one in wood.** Drawn as a walled shelter
  in log cladding it came back a squat box with a door in it. It is four raked
  legs holding a platform up, open underneath — `structures._post_tower` — and
  it needed no new rules primitive either: the legs are `O` pillars, which is
  what a post IS to the engine, and the ground between them stays the ground it
  was. Its footprint is ODD (five squares) because the platform and roof are
  drawn from the MIDDLE square and reach out over the rest, and only an odd
  footprint has a middle whose own centre is the structure's centre — which is
  what keeps the roof where it belongs when the square takes its quarter turn.
  Drawing them from one square is not laziness: only one storey is ever drawn
  at a time, so from the ground the platform is something you look at.
- **`elevation` is DRAWN now, and was not for a long time.** It is stored per
  square, shipped in `state()`, and folded into every distance, reach, cover
  and spell-area check since the board went 3D — and neither renderer read it.
  A mountain-pass ledge stood ten feet up in the rules and flat in the picture.
  Three consequences once it is drawn: a floor ends against a LOWER square as
  well as against a hole, so a ledge gets a face; a creature rides its square's
  elevation on top of its own (a wyvern over a ledge is above both); and every
  wash, path and marker rides it too, or the movement range on a ship's deck is
  a stain on the sea beside it. `SHIP_FREEBOARD_FT` is the first deliberate
  use — a caravel's deck is six real feet above the water, so her hull is
  visible and hauling somebody out of the sea is six feet of climb in the rules
  exactly as it is in the picture.
- **Things you get INSIDE are compositions of tiles that already existed.**
  `vtt/structures.py`. A camp's tents were 2x2 blocks of impassable furniture,
  so a creature could only ever be beside one and a token on the next square
  read as a soldier standing on the canvas. A tent is a walkable floor, a wall
  ring and a flap; a watchtower is that plus a real storey and a ladder, so an
  archer on top is fifteen feet up for every distance, cover and area check.
  Nothing new was needed — `add_level`/`add_stairs` already existed, and a
  hold is the same machinery at `base_ft: -8` (a level's height is an OFFSET,
  which is why the DM board signs it). **Scale is a rules question**: nothing
  is built with an interior under 10 ft, because a 5-ft interior holds one
  creature and is a box with a hole in it. Shelters demand clear ground around
  them — two built back to back seal a pocket, and the connectivity net then
  carves a corridor through somebody's tent to reach it.
- **The cache key must name everything the picture depends on.** `isoboard_ref`
  hashed the tile grid, and a skin changes materials without changing one
  tile — so a skyship's timber, steampunk and organic styles shared a slug and
  the first render was served to all three. They came back identical because
  they *were* one picture. Worse than the wasted work: it looked like evidence
  for a limit that does not exist, and "a depth ControlNet cannot convey a
  material" got written down as measured when it was a cache bug. With the
  skins in the key the same denoise gives three plainly different vessels. Same
  lesson as `layout_signature` following the CURRENT grid — **when two renders
  should differ and don't, suspect the key before the technique.**
- **The built-up rule survived being second-guessed.** A skinned board was
  given its terrain image regardless of how built up it is, on the theory that
  a skin is the board saying "not the default material". Measured across the
  gallery it cost six boards their painted detail to help one, and dropping the
  denoise to 0.60 to compensate turned everything into flat tinted geometry —
  exactly what `ISO_DENOISE_FLAT` already warns about. Both reverted; the
  reasoning is kept in `iso_denoise_for` so it isn't re-derived.
- **`vtt/decor.py` is scenery: in the room, not in the rules.** Bones, a rug, a
  brazier — drawn by the geometry and by the depth map, invisible to movement,
  cover and sight. It exists because the visual vocabulary was capped by the
  tile taxonomy, and every new code costs rules meaning that a rug should not
  have to pay. **Nothing decorative may reach cover height**; the cap is
  asserted at import, and anything that deserves to be cover is a TILE. Placement
  is DERIVED from layout + seed (the `objects_for` precedent), the server ships
  it in `state()`, and the DM board names it as having no mechanical effect —
  unnamed, it is scenery the picture has and the board denies.
- **Fog, sight and light are re-tinted in place, never rebuilt.** They used to
  sit in the terrain cache key, so every step anyone took threw away the whole
  mesh because a torch had moved. Each vertex records its SQUARE and `reshade`
  writes colours straight into the attribute; shading only ever takes nine
  values (three visibility tiers by three light levels), so it is a table lookup
  per vertex. Instancing was considered and rejected — meshes are already merged
  per tile code, so a board is ~10 draw calls and rebuild FREQUENCY was the cost.
- **A creature walks the route the server walked.** `move_token` paths around
  walls and records the route in the event log for monsters as well as players;
  `state()["last_move"]` carries the newest one and the client animates along
  it. A straight lerp between two squares draws a creature strolling through
  masonry, which was tolerable when walls were flat shading and is not now.
- **The isometric camera is ORTHOGRAPHIC and never rotates, and that buys
  three things.** The projection is a plain affine map, so it inverts in closed
  form and picking is arithmetic; pan and zoom are a translate-and-scale, so
  one `View` (`scale`/`ox`/`oy`) drives both browser renderers and the camera
  needs no state of its own; and a painting baked at one framing stays aligned
  at every other. Offering rotation would cost all three at once.
  `activity-ui/src/lib/isocam.ts` is the only place the camera is defined, and
  `vtt/isocam.py` will mirror it so the server can rasterize a depth map of the
  SAME view for a depth ControlNet. **Change one and you must change the
  other** — a degree of drift puts every painted shadow beside the thing
  casting it. Winding is load-bearing in the mesh builder for a related reason:
  normals are derived from vertex order, so a reversed face gets a normal
  pointing into the block and the light finds nothing to catch.
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
- **"A creature you can see within range" is the board's answer, and the
  ACTION BAR is a third intent source.** `[[CAST]]` carried a name and a slot
  level and no target, so range and sight were enforced by nobody:
  `VttEngine.vision()` had been the complete "can A perceive B" answer since
  the light layer went in and no player-facing path ever called it.
  `targets_for` asks it together with range and returns EVERY creature — the
  illegal ones carrying a REASON, because a target greyed out with a stated
  cause is information and one missing from the list is indistinguishable from
  a bug. `area_preview` is the same answer for a template: the squares, who is
  caught (your own party included, before the slot is spent), and whether the
  origin is legal at all. `rules/targeting.py` reads what a spell targets out
  of the book's prose — derived, never stored, like `rules/components.py`.
  The bar (`_activity_actions` → `_board_action_plan` → `board_action`) emits
  the SAME intent dicts into the SAME `CombatEngine.resolve` that
  `_combat_preparse` and the extraction LLM do; `_combat_engine_turn(intents=)`
  just skips the parse. It is deliberately not a second resolution path — that
  would be a second set of rules to keep in step — and because skipping the
  parse is not skipping the RULES, intents arriving untrusted on the public
  `/chat` route can do no more than a typed sentence. **Enforcement is lenient
  where it cannot be honest**: no board, no token, or a name the board doesn't
  know means no refusal at all, the same direction `_material_check` errs in.
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
