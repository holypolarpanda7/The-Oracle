# The Oracle — Claude Code Project Guide

An AI Dungeon Master for Discord that runs a persistent, living D&D world.
Players create a character, "enter the world," and adventure while an LLM narrates.

**This file is the map, not the territory.** Every subsystem's hard-won rules live
in `docs/` — see [Where the deep rules live](#where-the-deep-rules-live) and read
the relevant file BEFORE changing that subsystem. Those rules were expensive to
learn; they are not optional reading, they are just not all loaded at once.

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
> **TRELLIS.2 (image -> 3D mesh) is installed**, in pixi environments outside the
> host ComfyUI venv, and a `sitecustomize.py` shim in each is load-bearing —
> delete `comfy-env\install.hash` and reinstall and both node packs silently
> register ZERO nodes. See `docs/TRELLIS2-INSTALL.md` before touching it.
>
> **ONE side at a time on `oracle.db`.** The database lives on a DrvFs mount
> and the two interpreters reach it as two different operating systems, so a
> Linux-side reader running while a Windows-side renderer writes gets
> `sqlite3.OperationalError: disk I/O error` — not `database is locked`, which
> is what you would go looking for. Nothing is corrupted and the failing side
> is whichever one you happened to start second. A long render batch owns the
> file; audit and measure before or after it, not beside it.
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
- **Shell**: WSL2 bash on Linux, against a project on the Windows `D:` drive.
  Two interpreters: `uv run` uses `.venv-linux`; `./.venv/Scripts/python.exe` is
  the Windows one and the ONLY way to reach ComfyUI/Ollama (see above).
- **Secrets**: per-component `.env` files — `ai-dm-sicord-bot/cred.env`,
  `oracle-dm-backend/backend-cred.env` (never commit these)

## Architecture (separable systems)

1. **`ai-dm-sicord-bot/`** — Discord bot (discord.py).
   `oracle-dm-discord-bot.py` (entry/wiring), `character_creation.py`,
   `backend_integration.py`, `dm_commands.py` / `event_handlers.py`,
   `music_player.py` / `music_control.py` (ambient music via the DAVE voice
   sidecar), `session_channels.py` (ephemeral voice TABLES).
   Full module map: `ai-dm-sicord-bot/MODULE_ARCHITECTURE.md`. Music and table-lifecycle rules:
   `docs/ARCHITECTURE.md`.
2. **`oracle-dm-backend/fastapi-dm.py`** — the "DM brain." OpenRouter LLM call →
   narration; SQLModel character DB (`oracle.db`) — **the source of truth for
   characters**; endpoints `/chat`, `/reset`, `/enterworld`,
   `/register_character`, `/check_character`; in-memory `SESSIONS` history per
   `guild:channel` session_id.
3. **`eight_card_system/`** — the **persistent world knowledge graph** (the
   "living world" backbone). NOTE: the old hex-map / terrain-render engine was
   removed; this name now belongs to the graph.
   - `models.py` — `Entity`, `Relation` (temporal, valid_from/valid_to in
     world-days), `WorldEvent` (append-only log), `WorldMeta` (day).
   - `graph.py` — `WorldGraph`: CRUD, `move_entity`, `add_event`, and
     `get_world_context(pc, action)` — a BFS returning ONLY the local slice of
     the world near the PC + entities named in the action.
   - `seed.py` — `seed_starter_world` + `place_pc` (starter region "Greenfields").
   - `extraction.py` — second-LLM-call change extractor: (action + narration +
     context) → JSON `WorldDelta` → applied.
   - `placelore.py` — **the one terrain answer** three renderers share; keeps
     `terrain` (the land a place stands IN) apart from `biome` (the surface it
     presents). Never re-derive terrain per render.
   - `mapmaker.py`, `cartography.py` — drawn maps and what a character brings to
     drawing one. `hoards.py`, `pantheon.py`, `ventures.py`, `threads.py`,
     `demo.py`.
   - Long-form notes: `docs/ARCHITECTURE.md`,
     `docs/design/world-maps-and-items.md`, `docs/design/ventures.md`.
4. **`rules/`** — SRD **rules reference** (structured game data), seeded from the
   open CC-BY-4.0 5e SRD dataset. `models.py` (`Monster`, `Spell`, sharing
   `oracle.db`), `ingest.py` (`ingest_srd()`, upsert by slug), `query.py`
   (`RulesLibrary` + `format_*_brief` prompt renderers), `equipment.py` (the
   loadout), `mastery.py` (2024 Weapon Mastery), `checks.py` (skill→ability),
   `damage.py` (damage TYPES), `components.py`, `targeting.py`, `summons.py`,
   `metamagic.py`, `legendary.py`, `spell_scaling.py`, `subclass_grants.py`.
   Structured half only; prose-rules RAG is a later, separate layer.
5. **`dice/`** — internal **dice roller** (no Avrae copy-paste). `roller.py`
   (`roll(expr)`, `double_dice` for crits), `mechanics.py` (`ability_check` /
   `saving_throw` / `attack_roll` / `damage_roll`). The LLM emits
   `[[ROLL: 1d20+5 | Stealth | DC 15]]` and the backend substitutes the resolved
   result inline (`resolve_roll_hooks`). Single-voice UX.
6. **`vtt/`** — the **tactical board**: a square grid that opens ONLY for moments
   where position and timing decide the outcome, then closes and hands play back
   to prose. `models.py`, `terrain.py` (tile taxonomy + `Grid`), `geometry.py`
   (5e distance, A*/Dijkstra, LoS, cover, templates, FoV, OA), `mapgen.py` (21
   deterministic seeded generators; `archetype_for()`), `art.py`, `scene.py`
   (`VttEngine`; `state()` for the Activity, `render()` for the DM prompt),
   `bridge.py` (board ↔ `combat/` via spacing bands), `triggers.py`,
   `boardshapes.py`, `skins.py`, `hull.py`, `structures.py`, `setpieces.py`,
   `furniture.py`, `surface.py`, `decor.py`, `water.py`.
   Boards carry a **medium** (`GeneratedMap.mode`: walk / swim / fly).
   Design: `docs/design/vtt.md`. Rules: `docs/design/vtt-board-geometry.md`,
   `docs/design/vtt-board-appearance.md`, `docs/design/vtt-board-rules.md`.
7. **`arena/`** — the **Proving Grounds**: practice mode outside the world.
   3 overwritable level-1 slots → land/sea/air + level + difficulty → the REAL
   level-up flow → a code-rostered encounter on a real board. Nothing is
   remembered. Exists to exercise CC + level-up + combat/VTT on purpose.
   `environments.py`, `encounters.py`, `loadout.py` (the **Quartermaster** — the
   server prices the cart, never the client). Wiring: `_arena_*` in the backend;
   screens in `Arena.tsx`. See `docs/design/arena.md`.
8. **`airships/`** + **`bastion/`** — flying vessels (hull, crew stations, an
   elemental core that gates nearly everything, piloting checks, repairs,
   crashes, `journey.fly()` passages that report hours and hazards but NEVER
   coordinates) and strongholds (`bastion/build.py` raises one, `mobile.py`
   makes it travel). ENGINE here, NUMBERS in the gitignored data slot. A vessel
   is also a world-graph PLACE, which is what makes party, arrival art and
   movement work aboard it for free. See `docs/ARCHITECTURE.md`.
9. **`combat/`** — the initiative tracker and resolution engine.
   `docs/design/combat-engine.md`.
10. **`activity-ui/`** — the Discord Activity (React). `docs/design/activity-ui.md`.

## Running

- Backend: `uv run python oracle-dm-backend/fastapi-dm.py`
- Bot: `uv run python ai-dm-sicord-bot/oracle-dm-discord-bot.py`
- Tactical board self-test: `uv run python -m vtt.selftest` — **run it after
  touching `vtt/geometry.py` or `vtt/mapgen.py`**
- Tactical board wiring: `uv run python scripts/vtt_smoke.py`
- Vocabulary audit: `uv run python scripts/vocab_audit.py` (see
  [The silent fallback](#the-silent-fallback))
- World wipe: `uv run python scripts/world_wipe.py` — **never `rm oracle.db`**
- Anything that must TALK to ComfyUI or Ollama runs under
  `./.venv/Scripts/python.exe`, not `uv run`.

**`docs/RUNNING.md` is the full catalogue** — every demo, self-test, smoke test
and browser harness, with what each one actually pins. A feature you touch almost
certainly has a smoke test there; find it before writing a new one.

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
- **The DM narrates; the ENGINE factors.** A number the model computed is a
  number nothing checked. Deciding THAT a check happens and how hard it is stays
  the DM's; what a character's Stealth is worth does not. The same line runs
  everywhere: the LLM decides FICTION, the code decides MECHANICS.
- **The art is a TEXTURE; the grid is the TRUTH.** A diffusion model cannot put a
  pillar on square 6,5, so the picture and the rules disagree by design — and
  nothing the picture says may reach the players as a rule.
- **Derived, never stored beside the prose it came from.** A stored number and
  the sentence it came from drift the moment a re-parse improves one of them
  (`rules/components.py`, `rules/damage.py`, `rules/targeting.py`,
  `vtt/surface.py`).
- **ONE place decides each thing.** `placelore` for terrain, `survival/light.py`
  for light, `rules/checks.py` for checks, `rules/damage.py` for reduction,
  `_save_mod` for saves, `boardView.ts` for the shared board answer. A second
  answer is a drift waiting to happen.
- **`create_all` never ALTERs an existing table.** A column added to a model
  never reaches a database that already has the table; nothing complains at
  import and it fails at the INSERT, deep inside a feature. A "frozen" Activity
  panel is usually this. `docs/design/activity-ui.md`.
- **`_VTT_HOOK_ACTIONS` is an allowlist.** A handler with no entry there is
  silently dropped before the dispatcher ever sees it. Add both, always.

## The silent fallback

**`d.get(key, d["fallback"])` and `x if x in VOCAB else default` never complain
about a word they have not got.** Where the key comes from somewhere else —
another module's table, a roll, a derivation from latitude — the two sides
drift and NOTHING ANYWHERE FAILS. Four of these turned up in one week, all
identical in shape and every one silent:

- `TERRAIN.get(name, TERRAIN["grassland"])` costed a **sea crossing** as a
  stroll over a meadow, along with farmland, river, coast, underdark and
  dungeon.
- `climate if climate in CLIMATES else "temperate"` made four of the world's
  seven latitude bands temperate: **the subarctic never froze.**
- `_RELICS.get(fam, _RELICS["common"])` handed a soldier, a merchant and a
  farmer the generic prize for a quest they had just won.
- `_KIND_FRAMING.get(kind, ...CREATURE)` framed a **mesh reference** — an
  instrument reading nobody ever looks at — as "dynamic pose, menacing
  presence", then argued with sixty words of careful framing after it.
- `_ARCH_LOOK.get(archetype, "dungeon")` drew **`terraces`** — stacked plateaus
  whose own description is "dry rock, flats of scree and scrub" — with a crypt's
  floor, rubble and stairs. The one archetype with no entry, and the pair is in
  the register now: every archetype the generator can make must have a look, and
  every look it names must be one the catalogue holds.

None raised. Every one resolved to something plausible and wrong, and each was
found only by going looking. **`scripts/vocab_audit.py` is the standing
guard**: it names each producer/consumer pair explicitly and asks whether the
consumer knows every key the producer can make. Deliberately a REGISTER and not
a scan — a scan finds the `.get` calls and can never tell which of them matter,
and the interesting half is knowing what makes the key. Adding a pair there is
how a new vocabulary joins the guard. Verified to fail on all four.

When writing one of these, the question to ask is not "what should the default
be" but **"who produces this key, and can they produce one I do not have?"** If
the answer is yes, the default is a bug with a plausible face on it.

## Where the deep rules live

Read the file for the subsystem you are changing. These are the project's
accumulated rules — each one is a bug that was expensive to find.

| File | What it holds |
| --- | --- |
| `docs/design/vtt.md` | The tactical board's design intent: when a board opens at all. |
| `docs/design/vtt-board-geometry.md` | What a square IS and what shape is drawn for it — tile codes, skins, walls, prismatoids, roofs, hulls, structures you can enter, furniture models, landmark set pieces. |
| `docs/design/vtt-board-appearance.md` | How the board LOOKS — swatches and world uvs, shadows, ambient light, PBR channels, the ground under an object, water, scenery, re-shading in place. |
| `docs/design/vtt-board-rules.md` | What the board MEANS — picking, cover, the cutaway, the camera, fog/sight/light, elevation, upper floors, awareness, movement, board sizing, generation and connectivity, art conditioning. |
| `docs/design/combat-engine.md` | Turn pacing and narration, damage types and resistance, legendary creatures, spell scaling and riders, the loadout, weapon mastery, OCR hazards in book-parsed data. |
| `docs/design/character-rules.md` | 2024 creation and level-up, the feat-choice schema, species questions, subclass grants that speak to the engine, summons, concentration, saves, components. |
| `docs/design/world-maps-and-items.md` | The one terrain answer and how the ground lies, drawn maps and routes, unfinished business, NPC ventures, item art economics, loot affixes, typefaces, lore, puzzles, shops, chases. |
| `docs/design/activity-ui.md` | The fight's own page, and why a frozen panel is a dead socket. |
| `docs/ARCHITECTURE.md` | The unabridged module map: every module's long-form notes, including vessels/strongholds and the bot's music and voice tables. |
| `docs/design/arena.md` | The Proving Grounds. |
| `docs/design/ventures.md` | NPCs' own quests. |
| `docs/RUNNING.md` | Every demo, self-test, smoke test and harness. |
| `docs/TRELLIS2-INSTALL.md` | How the image→3D install survives. |
| `rules/OWNED_IMPORT_FORMAT.md` | The paste-and-translate override slots. |

**Adding a rule**: put it in the subsystem's `docs/` file, not here. This file
only gains a line when the rule is genuinely cross-cutting — when it would bite
somebody working anywhere in the project.

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
