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
   - `music_player.py` / `music_control.py` — Lavalink/wavelink ambient music
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
  archetypes. Both print a pixel-diff column — 0.00 means the LoRA did nothing.
- Proving Grounds demo: `uv run python -m arena.demo [level] [difficulty]`
- Proving Grounds smoke test: `uv run python scripts/arena_smoke.py` (slots →
  level-up climb → bout → victory/defeat, engine *and* WebSocket, LLM stubbed)
- Pantheon / patron-choice smoke test: `uv run python scripts/pantheon_smoke.py`
  (a god born in play becomes choosable in CC; an unmade one stops being offered)
- Activity UI harnesses (Playwright, against the offline demo — run
  `npm run build && npx vite preview --port 4173` in `activity-ui/` first, then
  `npx node <script>.mjs`): `feat-choices`, `spell-picker`, `levelup-spells`,
  `reprepare`, `mobile-smoke`, `arena-shot`, `vtt-shot`, `deity-shot`,
  `race-dup` (species traits render exactly once per viewport), `granted-feat`
  (a background grants its Origin feat, choices and all), `pframe-shot`
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
    **Derived ART is not data and IS committed.** A rendered species portrait
    carries no book text, stat block or mechanics, so all of
    `activity-ui/public/assets/species/` is tracked, owned-book species
    included. The line falls between the picture and the words that produced
    it: the descriptors in `owned_books/species_looks.json` stay local.
  - Retrieval is selective — only fetch rules when the action needs a mechanic; prose
    lore stays out of prompts except brief mechanical facts.
- **The tactical board is a spotlight, not a stage.** Play stays theater-of-the-
  mind; `vtt/` opens a grid only for moments where position decides the outcome,
  and closes it after. The LLM decides FICTION (a board opens here, the fireball
  lands on the altar); the code decides MECHANICS (layout, path, squares, cover).
  Never let the model author a layout or a distance. This does NOT contradict the
  "hex maps were dropped" rule below — that was a world-map render engine; this is
  an encounter-scale square grid.
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
- **Level-up is gated on its choices** the same way the subclass pick is: an ASI
  level returns `asi_required` + `asi_feats` until the player sends either
  `ability_increases` or a `feat` (+ `feat_choices`). `FEAT_CHOICES` is the one
  schema for what a feat asks; `_apply_feat` is the one place it is applied, so
  creation and level-up can never drift. The UI half is `FeatChoices.tsx`.
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
