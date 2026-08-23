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
> **TRELLIS.2 (image -> 3D mesh) is installed, and how it survives matters.**
> `ComfyUI-TRELLIS2` + `ComfyUI-GeometryPack` live in `D:\ComfyUI\custom_nodes`,
> but their heavy dependencies do NOT: `comfy-env` provisions pixi environments
> under `C:\Users\holyp\AppData\Local\Programs\comfy-env` (~9.7 GB), so the
> host ComfyUI venv gained 27 packages and **zero version changes** — nothing
> the SDXL pipeline depends on was touched. The package list from before the
> install is kept at `D:\ComfyUI\venv-packages-before-trellis2.txt`.
>
> Two things cost real time to find, and both will come back if the isolated
> envs are ever rebuilt:
>
> 1. **`comfy_kitchen` registers its custom ops with PEP-585 annotations**
>    (`kernel_size: list[int]`), and torch 2.6's `torch.library.infer_schema`
>    matches parameter types against a DICT that only ever contained
>    `typing.List[X]`. Same type to a reader, two different keys to a dict — so
>    every op raised at import and ComfyUI registered **0 nodes** from both
>    packs, with no error anywhere near the nodes themselves. The fix is a
>    documented `sitecustomize.py` in each pixi env's `site-packages` that adds
>    the builtin spellings beside the typing ones. It adds names and changes
>    none. **Delete `comfy-env\install.hash` and reinstall and the shim is
>    gone** — re-copy it, or the packs silently register nothing again.
>    (125 GeometryPack + 24 TRELLIS2 nodes when it is in place.)
> 2. The env is pinned to **torch 2.6 on purpose** — comfy-env matches the host
>    so tensors cross the boundary, and every CUDA wheel is built
>    `+cu124torch2.6-cp312`. Do not "fix" the shim by upgrading torch there.
>    (Other TRELLIS2 wrappers ship hand-built cp311/torch2.8 wheels and would
>    not have worked on this machine at all; this one builds its own.)
>
> The gated `facebook/dinov3-*` encoder needs no HuggingFace login: the wrapper
> remaps it to a public reupload. Weights auto-download to
> `ComfyUI/models/trellis2` on first use — `microsoft/TRELLIS.2-4B` is 16.2 GB
> whole, and a geometry-only 512 run pulls ~6 GB of it plus ~1.2 GB of DINOv3.
> At 2.58 GB per 1.3B model on a 12.9 GB card that also holds SDXL, it has to
> time-share the way the local LLM already does.
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
   - `session_channels.py` — the ephemeral voice TABLES. **A table's occupants
     are its PLAYERS: `seated()` ignores bots.** The music sidecar joins as its
     own bot user and never leaves on its own, so counting it held every table
     open forever — the last player walked out and the channel stayed because
     something was still "in" it. A table the last player leaves closes after
     `EMPTY_GRACE_SECONDS` (20) and is re-checked on waking, so a dropped
     client, a switch to the phone or an Activity reconnect comes back to the
     table rather than to a hole where it was. **A sweep must forget its own
     task before calling cleanup**, which cancels whatever is registered for
     the channel — cancelling yourself mid-await kills the deletion you were on
     your way to perform, which is why an idle table was never actually swept.
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
   - `ventures.py` — **other people's quests**: an NPC steps out of their role
     and goes after something, in 1-3 stages, progressing on world-time whether
     or not anyone is watching. See `docs/design/ventures.md`.
   - `threads.py` — **the character's OWN unfinished business**: the half of a
     backstory a DM can act on. A personality trait describes how somebody
     behaves in a scene already happening and is no help at all with "what
     should we do next"; what answers that is something left OPEN, because
     every one carries a verb — a home to RETURN to, a wrong to AVENGE,
     somebody to FIND, a thing to RECOVER, a debt to REPAY, a wrong to ATONE
     for. So a thread is not prose: it is a real PLACE with real coordinates
     (so `[[ROUTES]]` costs the journey and the mapmaker draws it), optionally
     a real person standing there, and an `UNRESOLVED` edge from the PC.
     Resolving it CHANGES the world and it stops being offered — which a
     paragraph in a text box cannot do, since prose goes on saying the village
     is burned long after the party rebuilt it.
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
   crewing in shifts stretch the 8-hour day toward 24. **Raising one is
   `bastion/build.py`**, and it splits in two on purpose: the CONSTRAINTS are
   the game's, decided in one place so the builder screen and the DM cannot
   disagree and re-checked on commit (the Quartermaster's rule — the server
   prices the cart), and the EXPRESSION is the player's, validated by nothing.
   A refusal always names what would fix it; something merely unwise (an engine
   in a fixed hall) is a NOTE, because advice that blocks is a builder nobody
   uses twice. The screen is `BastionBuilder.tsx`.
   **HOW MANY facilities is the LEVEL talking, not the purse** — the tier
   levels (5/9/13/17) sat in `catalog.FACILITY_TIER_LEVELS` unchecked, so a
   rich level-5 character could buy the whole book and a poor level-17 one was
   entitled to nothing. `special_allowance`/`basic_allowance` decide, per-tier
   counts are config. **A stronghold is never finished**: the second visit is
   the first with the slots spent (`plan(held_special=…)`,
   `check(extending=True)`, which skips only what was settled at raising).
   **BASIC facilities are the expressive half** — `facility_type="basic"` was
   in the table from the start and nothing ever created one, so every bastion
   was workshops with nowhere to sleep. A room is a KIND the rules price and a
   NAME that is wholly the player's; the only refusal on that half is a room
   left unnamed. Those names reach the place's description (APPENDED) and a
   vessel board's compartments via `_bastion_rooms`.
   **ENLARGING (cramped→roomy→vast) needed a size to MEAN something first** —
   `space` and `hirelings` were stored and read by nothing, so a vast smithy
   and a cramped one were the same smithy. A size now sets capacity and scales
   what a turn produces (`space_capacity`/`space_output`; a cramped gaming hall
   earns 100 gp, a vast one 250). The work is PAID when ordered and LANDS on a
   bastion turn — gold alone deciding the pace is what makes the calendar
   pointless — and mid-works the facility still works at its OLD size.
   `resolve_bastion_turn` returns `completions` rather than applying them,
   because it touches no database. One step at a time, each size has its own
   level, one building site at a time, and an ordinary room is never enlarged.

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
- Token-occlusion arithmetic: `node activity-ui/occlusion-check.mjs` — needs no
  preview server and no build (it bundles `boardView.ts` out of src with
  esbuild), because `occludedAt` is pure grid arithmetic over a camera that
  never moves. The LOOK still needs a browser; that is `occlusion-shot`.

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
- **Being DOM is also why OCCLUSION has to be computed.** An element over a
  canvas is in front of the room by construction, so no depth test will ever put
  a creature behind a wall — "behind" is a thing somebody has to SAY.
  `boardView.occludedAt` marches the view ray over the grid: the camera is
  orthographic and never MOVES, so for any one angle the ray back to the lens
  is a fixed direction that climbs `RAY_RISE` (tan of the pitch, which no
  amount of turning changes) per foot it crosses the floor, and a square's drawn height is the same arithmetic the geometry is
  built from. Grid, not picture — the same rule as cover and sight, and the only
  answer available on a painted board, where the geometry draws no colour at all
  and a depth-buffer readback would stall the frame besides. The point tested is
  the creature's CHEST: a wall that hides the boots is not worth marking, and at
  this pitch a ten-foot wall one square in front leaves exactly the head
  showing. An occluded token is drawn HOLLOW — bright rim, quiet inside — never
  hidden, because a token that vanished would be indistinguishable from a bug.
  The flat canvas reports `false` and always will; nothing on it can stand in
  front of anything.
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
- **The prismatoid existed for a year and almost nothing used it.** Measured:
  **106 of 178 skin parts were still boxes**, every single piece of FURNITURE
  was boxes (altar 5, table 8, crate 8, low wall 2, zero prismatoids between
  them), and the pillar was `prism(cx, cz, 0.32)` written by hand in BOTH
  renderers and in neither table — the last shape in the project living where
  the generated gate could not see it, which is the exact arrangement the tree
  was moved out of for having drifted between the two languages. So the board
  read as stacked blocks not because the geometry could not do better but
  because nobody had filled the table in. Down to **25**, and the ones left are
  things that really are flat: a plank deck, a canvas plane, a rung.
  `skins.slab(...)` is the workhorse — a box with `chamfer` (top plan pulled
  in) and `batter` (bottom plan pulled in), both zero by default so it degrades
  to exactly the box it replaces. What it buys is that **the only thing that
  says "worked" rather than "block" at this camera is the line where two planes
  of slightly different size meet**: a crate's lid proud of the body it closes,
  a coping that sheds, a die that leans in as it rises, a cornice that throws
  out over it. A tower's batter is a fortieth of a square over sixteen feet —
  nothing you would measure, and the difference between masonry and a carton.
  Two cases where the WORDS had been describing something the silhouette flatly
  contradicted, which is the arrangement this whole table exists to prevent: a
  taproom post whose `words` have said "chamfered" since the day it was written
  and which was a plain box, and a chitin hull whose own comment promised "no
  straight line anywhere" and whose every part was a rectangle. Round things
  are round now — logs, masts, stanchions, column drums, pipes, ladder
  rungs — because a square top face is most of what this camera sees, and it
  says "sawn" however loudly the prompt says "split log". `ring`, `rect`,
  `inset` and `slab` live in `skins.py` beside `solid`, not in the renderer
  that happened to need them first: skins is imported BY isocam, so a skin
  cannot reach the other way.
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
- **A ROOF is bigger than a square too, and that is why a street looked like
  huts.** The `townhouse` skin carried a gable PER SQUARE, so a terrace of
  close-packed two-storey houses came out a sawtooth of one-square huts —
  twelve little ridges over one building. No amount of shape authoring inside a
  square fixes that: what is wrong is the SIZE OF THE UNIT, which is the
  `vtt/hull.py` argument arriving from the other side. `hull.roofs` traces each
  contiguous run of a roofed skin and puts one roof on it, computed on the
  server and shipped in `state()` for the same reason a hull is — an algorithm
  over the board is the one kind of geometry two languages cannot be trusted to
  agree about. A skin opts in with `roof_ft` (how far the ridge stands above
  the eaves) and `roof_at` (where the eaves sit in its drawn height).
  **The ridge is a uniform inward OFFSET of the footprint**, which is the
  straight skeleton for any rectangle and close enough for the rest, and two
  things about it are load-bearing. The corner factor is `d / |bisector|²`,
  not `d / |bisector|` — the average of two unit normals is shorter than either,
  so a corner has to travel `d / cos` to put both edges at `d`, and getting
  that wrong by one factor left a two-square terrace with a ridge half a square
  across, which is a flat-topped slab. And **a polygon collapsed to a line is
  the ANSWER, not a failure**: that is exactly what a ridge IS over a building
  narrower than twice the inset, and over a square one it collapses to a point,
  which is a pyramid. What must be rejected is an offset that has turned itself
  inside out, told apart by asking whether any edge now runs backwards — and
  then halved and retried rather than abandoned.
  **Winding is normalized, never trusted** (the `skins.solid` rule, and it
  bites twice as hard on shipped geometry): a loop traced the other way shades
  the near pitch as though it faced away and, in the browser, culls the roof
  outright — the building comes back with no top and neither program looks
  broken. Both renderers walk the SAME cycle: eaves i, eaves j, ridge j,
  ridge i.
  **A BATTER is a property of a mass, so it cannot be per square.** The
  townhouse and tower walls were given a lean as part of the same pass and a
  terrace came back with a bright hairline slot up the face at every square
  boundary — two neighbours both tapering in leave a wedge between them. Where
  a shape belongs to something bigger than a square, the square is the wrong
  place to put it, which is the same sentence the traced roof answers.
  Two tools came out of doing this and are worth knowing about:
  `scripts/shape_probe.py` draws any archetype's GEOMETRY in colour with no GPU
  and no browser (the rasterizer already took a `_colour_of` and nothing had
  ever passed one, so the only way to look at a silhouette was to build the
  app), and `scripts/demo_textures.py --board <archetype>` stages a REAL
  generated board over the offline demo so the browser can be pointed at a
  street or a reef without a backend or a session.
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
- **A ship has a CLASS, and from the inside it has ROOMS.** `_hull` took its
  length and beam from the BOARD (`width - 2` by `height - 2`), so a two-crew
  skiff and a forty-passenger cruiser were the same outline in the same frame
  and only the water underneath told them apart. `vtt/vessels.py` is the
  missing middle: length, beam, fineness and plan per class, DERIVED from a
  catalogued vessel's own crew/passengers/cargo (the fleet is gitignored data,
  so a hull table naming those vessels could not be committed) or rolled from
  the seed. **The silhouette is what the painter is conditioned on**, so two
  classes differing only in name would be two pictures of one ship, and a
  vessel that will not fit keeps its PROPORTIONS rather than being clamped into
  the board's own rectangle. The same complaint applies inboard: `_rig_ship`
  built one deckhouse aft whatever it was rigging. **How many compartments is
  the class's business** (`HullClass.compartments`), swept from the transom
  forward one square at a time so the narrowing beam rations them; **what they
  are CALLED can only come from the caller** (`generate_map(rooms=)`, the
  `landmarks=` bargain), and for a bastion that flies the backend's
  `_bastion_rooms` supplies the facilities its owner actually bought — read
  there, not in `vtt/`, because the tactical layer must not know what a bastion
  is. A trader is nine squares in the beam and holds ONE deckhouse, so the rest
  go BELOW, divided by bulkheads that are ordinary walls with ordinary
  doorways — and a hold must be built from the whole HULL, not from walkable
  deck, or it comes out full of holes with the floor under a cabin stranded as
  an island. A room is not a rule, so it lives in `notes`; it reaches
  `state()`, the DM board, and the PNG's label chip on the drawn floor only.
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
- **A hillside was a flight of stairs, because elevation is stored per SQUARE.**
  Whole feet per square drawn at one height per square is a terrace, and the
  terracing is most of what makes an outdoor board read as stacked blocks — a
  meadow with a knoll on it came out a wedding cake. `isocam.corner_lift_ft`
  (mirrored by `boardView.cornerLiftFt`, and now compared by the alignment
  gate) bends the SURFACE between square centres by averaging the shared
  CORNERS. **A corner's height must be a property of the CORNER**: anything
  that reads the asking square gives the two squares sharing an edge two
  different answers there, and the ground tears along every seam.
  Two guards keep it a drawing rather than a lie. It applies to natural ground
  only — a floor, a road, a quay and a deck are LAID, and laid things are flat
  — and **the SKIN decides, because the tile code cannot**: `.` is scree on a
  mountain pass and cobbles on a street, which is exactly the distinction a
  skin exists to make (`Skin.soft`, with `terrain.SOFT_GROUND` as the fallback
  for a square wearing none). And it only joins a difference of one STEP: a
  LEDGE is the height the rules make you decide about, and ramping one draws a
  ramp where the board says there is a fall.
  **`GROUND_RIPPLE_FT` is the other half**, and it is the `HEIGHT_JITTER`
  precedent applied to the ground: outdoor relief is mostly built from LEDGES,
  which must stay hard, so everything between them was still a dead-flat plate.
  A wander of a foot and a bit, hashed from the corner so both squares agree,
  riding on the smoothed average so it appears only where the ground was
  already allowed to slope. No rule reads it and the occlusion march never
  sees it — a creature stands at its square's stated elevation and every
  distance, cover and area check reads the integer.
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
- **A tile KIND may have a MODEL, and that is where furniture-sized meshes
  stopped being forbidden.** `vtt/furniture.py`. The shape tables draw a crate,
  a table, an altar and a column out of prismatoids and that goes a long way
  for a few numbers; what they cannot do is put a handle on a barrel or a
  moulding on an altar, because none of those is a shape a rule can describe.
  A model per KIND is affordable on the sprite economics — one crate model
  serves every crate on every board in every session, nine kinds rather than
  nine hundred squares. Three rules, and the second is the one that reverses
  the old prohibition: the TILE keeps every rule; **the model is scaled to the
  height the board would have DRAWN**, so it cannot restate a height the rules
  quote (a set piece stamps its own codes and one mesh at one scale cannot
  honour a per-square quoted height — here the code is already there and the
  scale is derived FROM it); and a missing model is never an error, it is the
  prismatoids every board drew before. The fit carries no height of its own —
  `unit_scale` takes the mesh to one unit tall and the caller multiplies — so a
  quoted height stays exact and a jittered one still jitters.
  **A model wider than its own square is REFUSED, not squashed.** Measured: a
  "stack of two crates" came back 1.00 x 0.45 x 0.58 and would have stood nine
  feet across a five-foot square. Scaling it to fit would draw a crate that
  screens four feet at two, and a player deciding whether they can break line
  of sight behind it would read the wrong number off the board — so
  `MAX_SPREAD` refuses it and `--audit` says why. Every subject phrase
  therefore describes ONE upright thing of roughly square footprint, which is a
  constraint and not a style.
  **Rendering and COMMITTING are two steps on purpose.**
  `scripts/furniture_meshes.py --render` gives this installation a model;
  `--collect` is the deliberate act of putting one in the repo for everyone.
  The code can tell you a model is ILLEGAL; it cannot tell you whether one is
  any GOOD — a pedestal that is a perfectly correct altar is the wrong thing in
  a crypt full of coffins, and no measurement catches that. The committed set
  is empty until a person has looked.
- **A landmark that GROWS belongs to a latitude.** Reported by a player: a
  temperate northern wood came back with a sixty-foot PALM standing in it.
  `SetPiece.on` says what GROUND a piece may stand on and has nothing to say
  about where in the world that ground is, so the jungle giant was in the pool
  for every forest, clearing and swamp on the planet. `SetPiece.climates` is
  the declaration and `setpieces.suits_climate` the gate — per PIECE rather
  than a table somewhere central, because masonry needs none of it (a ruined
  arch is a ruined arch in the snow) and only the things that grow care.
  `forest-giant` is the same tree one band north, and it needed no new asset —
  Kenney's Nature Kit has a broadleaf beside the palm, so it is a different
  preference order, a different name and forty-five feet instead of sixty,
  because an oak is not a kapok. Lenient in the direction every gate in that
  file errs in: a piece naming no band stands anywhere and a board told no
  climate places everything, since a landmark refused for a climate nobody
  stated never appears at all. The DM's own `landmark=` is NOT filtered — the
  pool is a default rather than a permission, and somebody who narrates a palm
  in the snow has narrated a palm in the snow. `climate` reaches the board as
  an INPUT (`generate_map(climate=)`, stored in `notes`) exactly as `relief`
  does, and for the same reason.
- **Four of the world's seven climate BANDS were silently coming out
  temperate.** `geo.climate_for` derives a band from LATITUDE and produces
  arctic, subarctic, cool temperate, temperate, warm temperate, subtropical and
  tropical; `survival.weather.CLIMATES` knew three of them, and
  `climate if climate in CLIMATES else "temperate"` never complains about a
  word it has not got — so the subarctic never froze and the subtropics were
  never warm, everywhere in the world, every day of the year. The same shape as
  `TERRAIN.get(name, TERRAIN["grassland"])` costing a sea crossing as a stroll,
  in the module next door, and found only by going looking for it.
  `desert`/`coastal`/`mountain` stay in that table and are NOT bands — they are
  the TERRAIN axis, which arrived because the module was written standalone,
  and what a desert or a summit does to the sky is `placelore.WEATHER_BIAS`
  applied on top of whatever the latitude gives. `routes_smoke` now fails if
  the world can produce a band the weather model has never heard of.
- **A LANDMARK may be somebody else's mesh, and it still owns no rules.**
  `vtt/setpieces.py`. Everything else the board draws is derived from (tile
  code, skin, x, z), which works because a wall, a cliff and a hull are things
  a rule can describe — and stops working at a colossal seated guardian with a
  human face, because no hash produces one. That is a capability limit, not an
  efficiency one. A set piece is a mesh plus a footprint of tile codes it
  STAMPS, and the tiles are its entire mechanical content, so cover, movement,
  sight and breakability read the board exactly as always. Three guards fire at
  import: a square the mesh FILLS must stamp an impassable code (a picture may
  not close a square the rules leave open — `skins.occludes_floor` again); a
  passable square must declare its `elevation` or a creature stands inside the
  model; and a set piece may not stamp a code whose height the rules QUOTE,
  since one mesh at one scale cannot honour a per-square quoted height — a
  market stall was written and deleted by that rule, and furniture-sized things
  stay tiles. **Not every set piece has a mesh**: a stepped pyramid is rock
  faces and floors at a stated height, geometry the board has drawn since
  elevation went in, so `source=None` and a model of one would only be a rival
  answer to a shape the rules already fix. Enterable buildings stay in
  `structures.py`; a set piece is fought AROUND or ON, never INTO. Sources are
  **CC0 or CC-BY only** — the operative question for a public repo is
  REDISTRIBUTION, not use, and "free for personal use" fails it. The packs are
  a register in code (`PACKS`), unzipped into the gitignored `assets_src/`;
  only meshes actually used are copied into
  `activity-ui/public/assets/setpieces/` and committed, with an
  ATTRIBUTION.md **generated** from that register (`scripts/setpiece_assets.py
  --list | --audit | --collect | --attribution`) so file and code cannot drift,
  exactly as the OFL fonts do. An entry's FILE half is a search key, not a
  path — pack contents get renamed between releases — and the audit resolves it
  and reports the mesh's bounding box against the height the entry declares.
  Scale is always DERIVED, never authored — a per-pack magic multiplier is a
  number nobody can check — and **the declared HEIGHT is what it is derived
  from, with the footprint giving way.** Fitting a mesh's width to its declared
  footprint (the first rule, now reversed) made every tall thing a dwarf: a
  60-ft jungle giant came out 21 ft, a 40-ft gate tower 3 ft. The height is the
  fiction and it frames the depth map; the footprint is a floor-level rules
  statement about which squares are stamped, and nothing stops it being wider.
  Scaling stays UNIFORM — stretching one axis to satisfy both numbers distorts
  anything organic. `--audit` reports the squares a mesh NEEDS at its declared
  height, which is how a wrong ENTRY is told from a wrong footprint: a gate
  tower wanting 27x27 had matched the Castle Kit's crenellated *cap*, because
  that kit is modular and sells no whole tower. A kit sold for assembling
  buildings is `structures.py` territory; a set piece wants a pack that sells
  whole objects.
  Wider footprints then forced **`KEEP` — a square a piece RESERVES and does
  not change.** A 9x9 tree is 80 squares of ground its canopy merely hangs
  over, and stamping them repaved a meadow into flagstones: the picture
  contradicting the grid in the one direction nobody checks, because the
  terrain was RIGHT before the landmark arrived. A reserved square is still
  checked by `fits` and still kept clear of scatter.
  **`triggers.board_size_for` now grows a board for its SCENERY**, reversing
  the note that a landmark needing a bigger board is one that mostly does not
  appear — true when a footprint was a mesh's width, false once footprints were
  measured honestly. `"open"` is deliberately absent from the archetype table:
  it is the FALLBACK archetype, so giving it landmarks grew the default board
  for scenery nobody asked for (the selftest caught exactly that).
  **The mesh must turn the way the TILES turn.** `_turned` sends a footprint
  square to `(-z, x)` at 90°; three.js `rotation.y` is the other handedness and
  sends it to `(z, -x)`. Applied naively the picture rotates and the cover does
  not, and both programs look correct in isolation — so the handedness lives in
  `setpieces.rotate_xz` / `boardView.setpieceRotate` and is gated by
  `iso_alignment_check.py`. The FIT (`mesh_fit`: scale + pivot) is measured on
  the server and shipped in `state()`, never recomputed in the browser: it is a
  measurement of a FILE, so the only way two languages cannot disagree is for
  one of them to do it. All three renderers stay in step — the isometric board
  draws the mesh, the depth map rasterizes it (a 40-line OBJ reader: `v` and
  `f`, nothing else), and the Discord PNG draws no mesh at all but NAMES the
  landmark, since its stamped tiles were always on that board already.
- **A landmark has to be ASKED for, and a place is read in three tiers.**
  `[[VTT: open | … | landmark=a stepped ziggurat]]` is the channel between the
  fiction and the catalogue, and without it the model narrated a ziggurat the
  board had never heard of — the picture flatly contradicting the prose, which
  is the one direction the grid-is-truth rule does not cover.
  `setpieces.landmark_for` maps loose words onto a slug on WORD BOUNDARIES
  (unlike `mapgen`'s archetype table, because "arch" lives inside "archer" and
  a board full of archers is what a DM describes when a fight starts);
  `board_size_for` grows the board for what was asked; `_place_setpieces`
  stands it before the archetype's own pool and exempt from the coin-flip
  rationing. The DM still chooses only WHAT — the footprint, the place and the
  fit stay the code's. The prompt lists the catalogue GENERATED from
  `landmark_vocabulary()`: a model cannot ask for a ziggurat nobody told it
  exists. Where nothing is named, the place text is read for one, so a DM who
  never learned the parameter still gets the fountain they described.
  **`archetype_for` reads medium, then architecture, then country.** A flat
  first-match list put `jungle` above `temple`, so "an overgrown temple in the
  jungle" came back a plain patch of forest — the jungle already reaches the
  render through the biome and the skins, and nothing else in the chain can put
  terraces on the board. The MEDIUM outranks both, because it decides who can
  be there at all: an underwater ruin is fought swimming whatever it is built
  of, and reading the sea first is also what keeps a "shipwreck" off a ship's
  deck (matching is by substring).
  **Placement TRIES every square in a seeded order** — forty random darts
  measured badly, since a 9x9 piece wants an 11x11 clearing, wooded boards have
  few, and a miss is indistinguishable from a board with no room. Two forests
  in three refused a pyramid that fitted; six boards in ten stood nothing at
  all. Scanning inner ground first keeps a landmark off the rim, where it fits
  most easily and is least worth having. `fits` judges clearance in the board's
  own MEDIUM (the `_connect_regions` rule), or deep water reads as "something
  already standing here" and a wreck may not lie in the sea — and an `on` list
  that forgets the archetype's actual floor is the same bug quietly: rubble
  kept every temple piece off the RUINS boards.
- **A probe that quietly shows something other than the app is worse than no
  probe.** Two of these, found by finally pointing a browser at a real
  generated board rather than at the demo's one mill room. `demo_textures`
  staged its swatches keyed by TILE CODE, and the renderer looks a material up
  by SLOT (`materialSlot(code, skin)` is `#@townhouse` wherever a skin is on) —
  so on any board with skins every square missed and fell back to its flat tile
  colour. A street came back a field of untextured dark grey and a swamp looked
  fine, purely because a swamp's codes mostly wear no skin. And the seam did
  not carry `water` at all, so a staged bog was a set of empty sunken basins:
  the geometry as it looks before the water goes back on top, which is exactly
  the thing the seam exists to let somebody look at. `board-look.mjs` is the
  harness; run `--clear` afterwards, because every other harness reads the same
  seam and a staged street has no gallery for `floors-shot` to climb.
- **A roof quad needs WORLD uvs.** `MeshBuilder.quad` defaults its corners to
  the unit square, which is right for a floor tile — one square, one repeat —
  and stretches the whole swatch across a pitch six squares long. Every roof on
  a street came back as a set of nested bands, a ziggurat rather than a roof,
  and the geometry had been correct the whole time. One unit is one square,
  exactly as on the floor.
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
- **The model paints the SILHOUETTE it is handed, and no knob on the painter
  outranks it.** A mountain pass came back as a snowy village with wooden
  doors, and four attempts to argue the model out of it from the painter's side
  all measured WORSE than the disease: judging "built up" by BUILT codes so raw
  country gets its terrain image (village gone, board back as grey cubes — the
  flat tinted diagram again, and the cave took the same loss), 0.85 as a middle
  ground (architecture straight back), and forbidding houses and doors in the
  negative — tried once against the old shapes (it built carved stone pilasters
  instead) and once against the new (worse still: a timber shrine and a gilded
  stupa; naming a thing a dozen times to forbid it is still naming it). The
  fault was the shape both times. **A cliff square is a PRISMATOID**, battered
  and canted with no flat top or right angle in plan — it was a full-square box
  at one of six heights, and a field of flat-topped boxes at varied heights is
  a hill town. Only the buried bottoms stay square, which keeps the merging
  rule that stops a rock face breaking into towers. **A tree is a crown on a
  trunk** (`isocam.OBJECT_VARIANTS["T"]`, four crowns), stands 18 ft rather
  than 12 because a crown needs room above head height, and its swatch is
  FOLIAGE not bark — one swatch colours the whole square, and what a square of
  tree shows a camera on the ceiling is leaves. Painted brown they came back
  violet; painted green the forest is a forest instead of a field of sawn-off
  stumps. It also had to MOVE into the generated table: the old tree was two
  cylinders written by hand in each language and they had already drifted, so
  the depth map carried a different tree from the one the player saw.
  `scripts/scene_probe.py --paint --force --tag <arm>` is how an arm of such an
  experiment is measured: **`--force` is not optional**, since the art cache is
  keyed on the layout and knows nothing about denoise or negatives, so a second
  arm is otherwise served the first arm's picture.
- **A landmark may be one the DM INVENTED, and the name is its identity.** The
  catalogue is a fixed list of meshes so a model cannot ask for one nobody
  shipped — a guarantee about MESHES, which says nothing about a thing the board
  can already draw. A `landmark=` phrase the catalogue does not know becomes a
  piece with `source=None` stamping `A` (a worked object, half cover, four feet,
  breakable) at 2x2, so the DM's own gilded sow stands in the tavern named for
  it. Four rules keep it honest: a description of the ROOM is refused (the same
  resolver is fed loose place text, and invention is enabled only for an
  explicit `landmark=`); a SPECIFIC description beats a loose catalogue keyword,
  judged by how much of the phrase the matched names cover, or "a life-size
  statue of a pig" resolves to the colossal seated guardian; the placed record
  carries the NAME, because the ad-hoc register is in memory and a board
  outlives the process that drew it; and two squares rather than one, because a
  single square of a 24x18 board is a four-hundredth of the frame. `SetPiece.
  words` now actually reaches the prompt — it claimed to for months and nothing
  joined it — and a landmark gets a REGION of its own squares, which is the one
  case regional prompting is good at: a contiguous block fighting no strong
  wrong prior.
- **A landmark the DM ASKED for sweeps the scatter under it.** Only
  `terrain.DECOR_CODES` — the set a generator scatters *without* touching
  connectivity — so a sweep can never open a way through anything and a wall is
  never moved. Without it the channel failed on exactly the boards most worth a
  landmark: a 9x9 piece wants an 11x11 clearing and a ruin strewn with broken
  pillars has none. A landmark nobody asked for still takes the board as it
  finds it. Found by making `vtt_smoke` DETERMINISTIC (`random.seed`) — it drew
  a fresh layout every run, and a landmark check is a check about whether one
  fits, so it failed intermittently and would have been blamed on whatever
  change somebody was holding at the time.
- **An invented landmark can now be given a SHAPE, and the mesher is only ever
  as good as the picture.** `imagery/landmark3d.py` + `imagery/mesh_client.py`.
  The catalogue exists so a model cannot ask for a mesh nobody shipped, and a
  catalogue is a fixed list — the DM's own gilded sow was standing on the board
  as a 2x2 stamped box: mechanically exact and visually nothing. The missing
  step was never geometry; a PICTURE of the thing is something the project
  already makes for every catalogue item and every wreck, and nothing turned one
  into a shape. TRELLIS.2 does, and what comes back is a mesh like any other —
  fitted by `setpieces.mesh_fit` on the server, drawn by the isometric board,
  rasterized into the depth map, carrying NO mechanical content. The tiles the
  piece stamps stay its entire rules meaning. OBJ, because all three readers
  already speak it (the browser's `OBJLoader`, `_obj_bounds`, `isocam`'s
  rasterizer); a GLB would need three new readers to buy nothing. Off by default
  (`ORACLE_LANDMARK_MESH`) — minutes of GPU on a card already shared with SDXL
  and the local LLM — and every failure leaves the box exactly where it was.
  **Two roots, searched COLLECTED FIRST** (`MESH_ROOT`, then `GENERATED_ROOT`,
  gitignored): a pack mesh is a modeller's answer to what the thing is, a
  generated one is a diffusion model's guess, so a catalogue entry can never be
  displaced by something this machine invented under the same slug. They serve
  over different URLs, because vite serves only `public/`.
  **The reference render is its own ImageKind, and that cost a real run to
  learn.** Drawn as a `MAP` — which is what the first version did — the gilded
  sow came back a flat heraldic emblem, because a kind carries LoRAs and
  SDXL-Battlemaps + HadesLevel@0.9 do exactly what they are for; no wording
  survives a LoRA at that strength. TRELLIS then faithfully produced a 2D emblem
  in relief: **1.00 x 0.02 x 0.95**, correct work on the wrong input, and not one
  thing in the pipeline complained. `ImageKind.MESHREF` renders it with no house
  style at all, because nobody ever LOOKS at this picture — it is an instrument
  reading, and dramatic light bakes a shadow into the geometry. `_too_flat`
  refuses a mesh whose thinnest side is under 8% of its widest, since a sheet
  standing on its edge is worse than the box it replaces. With a real
  photograph the same phrase came back a **sow on a plinth**, 1.00 x 0.44 x
  1.00, in **76 s warm** (30 s picture + 46 s mesh).
  **TRELLIS.2 is Z-UP and the board is Y-up**, and nothing in the project
  rotates anything — `SetPiece.up` is read by `mesh_fit` and by NO renderer, so
  an unrotated file arrives lying on its side, correctly scaled and silently
  wrong. `_normalize_obj` stands it up at WRITE time ((x,y,z) -> (x,z,-y), a
  proper rotation so winding survives) and strips the file to `v` and `f` —
  all three readers want nothing else, it halves the bytes, and it drops a
  `mtllib` line naming a file no route serves.
  **For an INVENTED landmark the footprint binds and the height gives way** —
  the exact reverse of the catalogue's rule, and deliberately. There, height is
  a stated fact about the fiction and fitting width to the footprint made every
  tall thing a dwarf; here BOTH numbers are defaults nobody chose for this
  thing, and the sow at nine feet tall would spill five feet onto every square
  around it, which is the picture contradicting the grid in the direction the
  `KEEP` rule exists to prevent.
  Three more things that would each have been silent: the fit measurement is
  cached but its MISSES are not — an invented piece is registered when its
  phrase is first seen, which in a fresh process is after something has already
  asked about the slug, and a remembered `None` would leave the landmark flat
  for the life of the run (`forget_mesh` drops it when a mesh lands mid-session); the file is written ATOMICALLY, because
  `_obj_bounds` measures whatever is on disk and a half-written OBJ measures,
  fits and stands the landmark at a confidently wrong size; and the mesh is asked
  for BEFORE the painting, since the depth map the painter is conditioned on
  rasterizes these meshes. **`Trellis2ExportTrimesh` reports `outputs: {}`** —
  measured: the file was written, the job reported success, and the history said
  nothing — so `_poll` returning None is normal and `_locate` finds the file
  under the prefix we chose.
- **A swatch is albedo, and albedo alone is a picture of stone laid FLAT on a
  shape.** `vtt/surface.py`. Every face of every block returned the same light
  for its orientation, so mortar courses, grain and pitting were painted ON the
  surface instead of being surface, and the geometry read as coloured cardboard
  however good the swatch was. Two halves, from different places, and that split
  is the design. **The RELIEF is already in the picture**: recovered by a
  HIGH-PASS, never by plain luminance — an albedo render contains the lighting it
  was made under, so luminance-as-height bakes somebody else's sun into the
  geometry. Measurable, and pinned: after the pass, detail beats low-frequency
  energy by 11x on a swatch with a gradient across it, where luminance alone
  fails the same check. **The SHINE is not in the picture at all** — contrast
  says nothing about whether stone is wet — so roughness and metalness are
  declared per SUBSTANCE, which is what a skin already is. Metalness is a switch
  and not a dial, and nothing is a mirror, because everything on a battlefield is
  dirty. **Every filter WRAPS** (`numpy.roll`): the swatch is tiled with
  `RepeatWrapping`, so a blur that clamps at the edges leaves a seam every five
  feet, in a grid, over the whole board. Derived, never stored beside the
  swatch — the `rules/components.py` doctrine one layer down — and derived on the
  SERVER for the `mesh_fit` reason. Client-side: `MeshStandardMaterial`, a
  hemisphere fill so the underside of every ledge stops going flat black, and a
  small procedural environment because **a metal with nothing to reflect renders
  BLACK**. The derived channels load as `NoColorSpace` deliberately: a normal map
  read through the sRGB curve lights as the material being subtly the wrong
  colour, which nobody can point at and everybody can see.
- **The GROUND is where a board stops making sense, and three different things
  were wrong with it.** All three read as "the painter is being silly" and none
  of them was the painter. **Scenery had no idea where it was**: `decor_for`
  drew from one pool, so a meadow got rugs, sacks and braziers — now a kind
  declares its SETTING (`built` / `paved` / `wild` / `under`; a cave has no
  furniture and no vegetation, a street is open to the sky and nobody lays a rug
  on it) and outdoor kinds exist at all (tussock, bush, deadfall, stones,
  stump). **A tile's LOOK is a skin's job, and the rules code is often right
  when the picture is wrong**: `_gen_open` scatters twenty-one `o` squares for
  cover, and `o` is a crate — mechanically exactly right, and a field strewn
  with crates. The `field-stone` skin leaves every rule alone (half cover, four
  feet, breakable) and makes them boulders. Same fix for a street's `#`
  (`townhouse`: buildings with ROOFS at 24 ft, where a 10-ft wall slab beside a
  paved strip had come back as a garden fence) and a ruin's `,` and `O`.
  **And a shape is a shape at every size**: the first outdoor scenery was built
  out of BOXES, and a meadow came back strewn with purple crates — the same
  lesson as the cliff and the crypt-of-dice, in the one place small enough to
  think it did not matter. Everything wild is a prismatoid now.
  **A swatch prompt is a POSITIVE prompt, so what a material must not be needs
  a negative** — `Skin.negative`, on top of `art.MATERIAL_NEGATIVE`. Written
  into the positive it does the opposite of what it says: the seabed swatch
  asked for "no water surface in view, no plants" and came back an aerial
  photograph of a BEACH, surf and palm fronds included.
  **An ACHROMATIC swatch is an invitation to invent a hue.** "Dressed grey
  stone" is a request for a colourless image and the model obliges; every stone
  swatch averaged to a dead, faintly COOL grey, and the painter — free to pick
  a hue where the init has none — came back with BLUE columns in a ruin and
  violet crowns on the trees before their swatch went green. Real limestone has
  a colour, so the prompt names one, and the drift stops. The lesson generalises
  past stone: a swatch that says only what a thing is made of and nothing about
  what colour it is has left the decision to the sampler.
  (`material_prerender.py --redraw <substance>` exists for this: the swatch
  PROMPT is the thing you iterate on, and before it the only way to see a
  changed one was to rename the substance in the code forever.)
  Two more traps worth remembering: a material swatch is a sample of a SURFACE
  and whatever it averages to is where the painter starts, so asking for "weeds
  forcing up through the joints" made a green swatch and the ruin stayed a lawn;
  and `R` (rock face) is in `STRUCTURE_CODES`, so an unskinned one is drawn as a
  thin WALL panel — `DEFAULT_SKINS` gives it `boulder` everywhere, which the
  pass and the cave had been hiding by overriding it for other reasons.
- **Underwater is a COLOUR GRADE, and it is applied rather than requested.**
  Every other thing on a board is decided per square — a tile, a skin, a
  swatch — and the one thing that makes a reef read as a reef is a property of
  none of them: the water column in front of all of them. Four per-square
  levers were measured and none reached it. The `~` swatch was a picture of a
  pond SURFACE with lily leaves (right for a stream through a meadow,
  catastrophic on the 73% of a reef that wears it) and is now a genuine sea
  floor; the coral was thin vertical boxes, which is a stand of REEDS, and is
  now domes, plates and thick antlers; `_void_reads_as` was asserting "its
  surface catching the light" on a board fought INSIDE the water, and now takes
  the medium; the prompt says in as many words that the whole scene is
  submerged. The board still came back a green pond, because ninety percent of
  it is flat, and a flat green expanse in an isometric fantasy diorama is a
  pond. So `_underwater_grade` puts the water back after the render — tinted
  toward the sea's own colour, stronger and DARKER with distance (water
  absorbs; a grade that pales with distance reads as mist, which is a thing
  that happens in air). Deterministic, no GPU, and it lands on every swim board.
  **The residue was the GENERATOR, and fixing it is what actually worked.**
  `_gen_reef` laid 73% featureless shallow water and 2% coral: no cover for the
  rules, and nothing for the depth map to carry, on the archetype whose own
  description promises that sight lines die at twenty feet. It is now a sand
  SHELF (about 55%) with silt and weed over it, coral heads standing in banks
  (6-8%), and two or three channels cut clean across it at
  `REEF_CHANNEL_FT` = **-10 ft of real elevation** (17-28%). The relief is the
  point on both sides of the wire — cover and a climb to the rules, and the only
  thing a depth map can say to a painter. Channels are carved as a random WALK
  from edge to edge, because a channel is a thing water cut and a swimmer can
  follow it; blobs made ponds, which was the shape the whole reef had. Coral is
  laid FIRST and the channels cut through it. Swept 117 boards: one connected
  region for a swimmer and two spawn zones on every one.
- **`vtt/decor.py` is scenery: in the room, not in the rules.** Bones, a rug, a
  brazier — drawn by the geometry and by the depth map, invisible to movement,
  cover and sight. It exists because the visual vocabulary was capped by the
  tile taxonomy, and every new code costs rules meaning that a rug should not
  have to pay. **Nothing decorative may reach cover height**; the cap is
  asserted at import, and anything that deserves to be cover is a TILE. Placement
  is DERIVED from layout + seed (the `objects_for` precedent), the server ships
  it in `state()`, and the DM board names it as having no mechanical effect —
  unnamed, it is scenery the picture has and the board denies.
- **...and re-tinting from ONE base painted all the scenery white.** `reshade`
  rewrites every vertex colour as `base x shade`, which is right for terrain
  merged per material slot — every vertex of it is the same swatch — and wrong
  for the one builder that carries a colour PER PIECE. Bushes, tussocks,
  deadfall, stumps and stones all go in with their own tints and were
  registered with a base of WHITE, so the first shading pass painted the lot of
  it. **It had looked right for exactly one frame since fog shading went in**,
  and every board in the game was strewn with identical white blobs. A
  shadeTarget may now carry `tints`, a copy of the colours as built, and the
  shade is applied as a FACTOR instead of a replacement. Nothing was ever going
  to notice this from the inside, which is why the guard in `board-look.mjs` is
  a pixel count: near-white must stay under 0.15% of the board (it was 0.49%).
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
- **The painted isometric board was built, probed, prerendered into a gallery —
  and never called.** `VttEngine.render_iso_art` had NO callers, so every board
  an Activity opened came back as bare geometry while `iso_art_status` sat at
  `"none"` on every row in the database. `render_art` (the top-down picture a
  Discord table looks at) was wired from the first day and its isometric
  counterpart never was. They are two views of the same room and a board can
  have either, both or neither, so nothing failed — it just quietly never
  happened. Measured on this rig: **an uncached isoboard painting is ~58s**,
  which is why it is a background task and the fight runs on geometry until it
  lands.
- **A fight gets its own PAGE, because the board was a fifth of the screen.**
  `BattleSurface.tsx` replaces the play surface whenever a board is out. The old
  layout spent its height on a status bar, an initiative carousel of CARDS, a
  "here & now" rail, a narration column and a permanent character sheet, so the
  one thing that decides the outcome was a small panel in the middle of them.
  **The board IS the page and everything else floats ON it.** The first cut
  gave the board a grid CELL with the log in a column beside it, which is
  better than a panel in a scrolling stage and still not what a fight wants: a
  third of the width went to a narration column nobody reads mid-turn, and the
  prose was squeezed into it. Now the strip, the turn line and the log are
  overlays — they cost the map nothing when you are not reading them, the log
  folds to a tab, and on a phone it is a bottom drawer that starts SHUT because
  open it covers the action bar. The page never scrolls: the wheel over the
  board is the zoom and only the zoom, which needs a NON-PASSIVE native
  listener, since React attaches wheel passively and `preventDefault` there is
  a no-op. The page has three things: one strip (round, the whole order as a
  tight rail, your own HP/AC, the way out), a line saying WHOSE TURN it is in
  words, and the board with its action bar filling the rest. The sheet is a thing you look
  UP — it does not change between turns — and the log folds away entirely.
  Two mechanical notes. `vtt.css` and the shared narration/prompt styles are
  re-scoped `:is(.play, .battle)` rather than duplicated, and the log is
  PARCHMENT for that reason: every narration style (a name, an item, damage, a
  whisper) is inked for parchment, so a dark log would need a second palette
  for the same words. And the board takes a `fill` prop that stops it owning a
  height of its own — on the play surface it trades height with the narration
  and the player drags the split, and that persisted height would otherwise
  clamp the battle page's board. **Sizing the board CELL as a fraction of the
  viewport is the bug this page exists to fix, one breakpoint down**: the panel
  carries a title bar, a floor strip, a movement line and the action bar, and
  those fixed costs ate a 46vh cell down to a sliver of map on a phone. The MAP
  gets the floor, and the page scrolls.
- **A fight whose first initiative belonged to a MONSTER simply sat there.**
  Monsters only ever moved inside `_combat_engine_turn`, which runs on a
  player's MESSAGE — so the board said "Cultist 1's turn", the cultist did
  nothing, and the only thing that could have moved it was the player acting
  out of a turn they had just been told was not theirs.
  `_combat_npc_catchup` plays out whoever is up until it is a PC's turn again,
  and the Grounds run it when a bout opens. It stops at a PC, at a pending
  reaction (a question only a player can answer) and when one side is wiped.
  The other half of that hang was in `CombatEngine.render_report`: **a move
  does NOT always land on a band.** Three paths emit one without — a creature
  that covered as far as its turn allowed, a failed leap, and a jump (that one
  deliberately, since a `to` would send the caller to `apply_band_move` and
  undo the leap it was just told about) — and the renderer indexed `e['to']`
  directly, so the whole exchange raised. Each event is rendered inside its own
  guard now, because the tracker has ALREADY applied everything in a report and
  a rendering failure that raises throws away the record of damage that really
  happened.
- **A frozen panel is a DEAD SOCKET, and it was three bugs stacked.** Reported
  as "the equipment screen just froze". None of the three was in that screen.
  **`create_all` never ALTERs an existing table**, so a column added to a model
  never reached a database that already had the table — `combat_combatant.
  awareness` and `vtt_map.setpieces` had been missing for months, which means
  NO FIGHT COULD START AT ALL, in the world or in the Grounds. Nothing complains
  at import; it fails at the INSERT, deep inside a feature. The startup
  self-heal used to hand-list columns per table, which is exactly how two went
  missing, so the last pass is now DERIVED: any column a model declares and its
  table lacks is added, always nullable (SQLite cannot add NOT NULL to a table
  with rows, and the models apply their own defaults on write).
  **The Activity WebSocket loop caught only `WebSocketDisconnect`**, so that
  exception tore down the whole connection — and to a player a dead socket is
  not an error, it is the screen they were holding refusing to respond. Each
  message is now handled inside its own `try`, which reports the failure where
  the player is looking and puts the busy spinner down. A turn may fail; the
  table should not have to be rebuilt.
  **And the client said nothing about it**: `onclose` did nothing once the
  socket had opened, and every later `send` silently no-op'd on a closed one.
  `connect()` reconnects with backoff and reports a `ConnStatus`; the surface
  shows a banner, and re-entering on reconnect goes through the same
  `pendingEnterRef` the seal already uses (a fresh socket is bound to no
  session). Only a frame carrying a `t` counts as having been ANSWERED — a dev
  server's HMR socket accepts any upgrade and sends its own JSON down it, and
  counting that would make a page with no backend look like a live table.
  **"Reset Layout" was `location.reload()`** — a far bigger hammer than the
  button says, since reloading drops the socket, so pressing it mid-fight put
  the player on the landing with a bout still running behind them. Panels clear
  their own inline size on a broadcast event now; nothing about resetting a
  HEIGHT requires throwing the table away. **A long wait is said out loud**:
  opening a bout rosters an encounter, lays out a board and may draw it, which
  is tens of seconds behind a screen that otherwise just stops responding. The
  veil appears only after 250 ms, so a fast answer never flashes one (offline,
  where the Grounds answer synchronously, it never appears at all).
  (Related, and worth knowing when a demo-fed harness suddenly fails: `vite
  preview` PROXIES `/ws` to the backend, so the offline demo feed only engages
  when the backend is actually down. Serve `dist` with a plain static server to
  exercise it while the backend is up.)
- **A click lands on the square you are LOOKING at, not on the ground plane
  under it.** `squareAt` unprojected the pixel onto the storey's floor, which
  answers "which square would be here if the board were flat" — and it has not
  been flat since elevation went in. On the demo board's ten-foot dais the
  plane was wrong by **two squares** on every probe, and the error grows with
  the height, which is exactly backwards: the whole point of high ground is
  that people stand on it. A player reported it as "I need to click on the 2d
  mesh location". `boardView.squareUnderRay` is the `occludedAt` march run the
  other way — every point of the form `(gx + rayX*u, u*rayRise, gz + rayZ*u)`
  projects to the same pixel, so walking `u` down from above and asking each
  square how tall it is DRAWN finds the first surface the ray meets. Drawn, not
  solid: **you pick what you can see**, so a click on what looks like the top
  of a wall selects that wall, and a wall the cutaway took down stops
  swallowing clicks meant for the floor behind it. Two traps, both measured: a
  SUNKEN square is reached at a NEGATIVE `u` (beyond the ground plane, not
  short of it), so the march is bounded by the board's deepest floor as well as
  its tallest thing; and it must step a little PAST that bound, because a
  surface sitting exactly on it is only ever approached and never crossed.
  Turning the camera pivots about `groundAt` — the CONTINUOUS ground point, never
  a square — because the square under the middle of the frame legitimately
  changes as the camera comes round, and pivoting about a moving target means a
  whole turn does not come back to where it started.
- **Cover is REPORTED while you are choosing where to stand.** It has been
  computed exactly and applied correctly since the board went 3D, and the only
  place a player ever saw the word was on a foe's own line after the fact —
  reported as "cover is not obvious". `VttEngine.cover_preview` rides in
  `path_preview`, so the answer for a hovered square arrives with what the move
  costs and who it provokes. **Per ENEMY, because cover is a relationship and
  not a property of a square**: the crate that screens you from the archer on
  your left does nothing about the one on your right, and one number for the
  square would be a comfortable lie. `best`/`worst` are for a caller with one
  line to spend, and the line NAMES the foes while there are few enough to name.
  Silent when there is nobody to take cover from, and silent when the answer is
  "none" — printing that on every square teaches a player to stop reading the
  line. **A preview says WHICH square it is about** (`x`/`y`), because it
  arrives after a debounce and the pointer has usually moved on; without that
  the panel shows the cover of a square the player has already left. It went
  missing once on the way through `App.tsx`, which copies the preview field by
  field — the same shape of bug as `_cc_request`.
- **The near walls are CUT AWAY, and the rule is one sentence.** A room is a
  box and an isometric camera looks into it over a corner, so the two walls
  nearest the lens stand between the viewer and the fight. At the canonical
  angle that was survivable because the wall is IN the painting; it stopped
  being survivable the moment the camera could turn, since a quarter turn puts
  what used to be the far wall across the front of the board. `boardView.
  cutAwayAt`: **cut the near walls exactly when you are looking at the geometry
  rather than at a painting of the room.** Where a painting is showing, the
  wall is a thing in that picture and not drawing the geometry removes nothing
  anybody can see — the geometry there is a depth-only proxy, so cutting it
  would only delete the occlusion. Where none is (art not drawn yet, offline,
  or any angle away from the bake) the geometry IS the picture and the walls
  come down to a stub, never to nothing: a floor with no edge at all looks like
  it is hanging in space.
  **STRUCTURE only**, which turns out to be the same thing as "never vary a
  height the rules quote" arrived at from the other side — a crate, a low wall,
  a table and an altar are OBJECTS, and every one of them has a quoted cover
  height. A rock MASS deeper than `CUTAWAY_DEPTH` is left alone: that is the
  edge of the world, not a wall in front of the room, and slicing the top off
  it reads as a mountain someone has been at with a bread knife. The depth
  exists at all because generated walls are commonly two squares thick, so a
  one-square test cuts the inner course and leaves the outer one standing.
  **`drawnTopFt` applies the identical reduction**, so the board's account of
  who is HIDDEN follows what it drew — otherwise the cutaway reveals a creature
  the board still calls occluded, which is the picture-versus-grid
  disagreement the occlusion march exists to prevent, arriving from the other
  side. The consequence, stated rather than discovered: after a cutaway what
  hides a creature is the FURNITURE and the ground, never the room's own walls.
  Nothing about the RULES moves — a cut wall is still total cover and still
  impassable. **Everything that reads terrain and can be asked about an upper
  STOREY goes through `rowsOf`**: `scene.terrain` is the ground floor and always
  has been (`state()` repeats each floor's own inside `levels[]`), so reading it
  for a gallery cuts that gallery to the plan of the hall underneath — walls
  missing where it has them, walls standing where it does not. Survivable while
  terrain reads were only the occlusion march, which documented itself as
  ground-floor; not survivable once the cutaway started deciding what to DRAW
  from it. `drawnTopFt`, `cutAwayAt`, `occludedAt` and `squareUnderRay` all take
  the level now.
  `awayDir` is one of EIGHT directions on purpose: the exact
  direction changes with every degree of a drag and the set of squares it picks
  out does not, so the mesh rebuilds eight times in a full turn instead of on
  every frame of one.
- **The camera TURNS now, and what that cost was one thing, not three.** The
  note here used to say the camera never rotates and that offering rotation
  would cost the closed-form inverse, pan-and-zoom being a translate-and-scale,
  and a painting staying aligned — all at once. Two of the three survive: yaw is
  a PARAMETER (`project(x,y,z,yawDeg)`, `basis(yaw)` memoised per angle), and
  for any fixed yaw the projection is still a plain affine map, so picking is
  still arithmetic and pan/zoom still mean what they meant. The geometry is real
  3D and never moves; only the lens does, so `vttScene3d` needed one basis
  swap and the shape tables needed nothing. `occludedAt` takes the yaw too —
  `rayRise` is tan(pitch) and does NOT depend on yaw, only which way the ray
  runs across the floor does — and it had to start testing the NEAR edges of the
  board, which it never did when the ray could only run one way.
  **The PAINTING is the real price, and it is paid honestly.** A picture baked
  against a depth map rasterized at one angle is a photograph of the room from
  one place, and no transform makes it a photograph from another. So the SERVER
  still works at exactly one angle — `YAW_DEG`, the canonical yaw, which
  `vtt/isocam.py` mirrors and `iso_alignment_check.py` compares — and the client
  FADES the painting out as it turns away (`paintOpacity`, full within 3°, gone
  by 16°, measured the short way round). A picture that vanished at a threshold
  would read as a bug; one that dissolves reads as the room turning. Off-axis
  you are looking at the geometry, which is exactly why the surfaces had to
  learn to answer to light before this was worth offering — the two changes are
  one change in the right order.
  `camera-turn.mjs` holds the arithmetic with no browser (canonical projection
  unchanged to the bit, every basis orthonormal, the inverse exact at every
  angle, a full turn returning exactly); `turn-shot.mjs` holds the look in a
  real WebGL context. The flat canvas answers `canTurn: false` and shows no
  control — looking straight down there is nothing a rotation would reveal.
- **The isometric camera is ORTHOGRAPHIC, and that buys three things.** The projection is a plain affine map, so it inverts in closed
  form and picking is arithmetic; pan and zoom are a translate-and-scale, so
  one `View` (`scale`/`ox`/`oy`) drives both browser renderers and the camera
  needs no state of its own; and a painting baked at one framing stays aligned
  at every other FRAMING — which is why turning it costs the painting and
  nothing else (see above).
  `activity-ui/src/lib/isocam.ts` is the only place the camera is defined, and
  `vtt/isocam.py` mirrors it so the server can rasterize a depth map of the
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
- **Almost no board is flat any more, and height is the cheapest asymmetry
  there is.** Elevation had been folded into every distance, reach, cover check
  and spell area since the board went 3D, climbing had cost a foot per foot
  since then, and a drop of 10 ft or more had been reported as a fall — and
  FOURTEEN of the twenty-one archetypes still generated a perfectly flat board,
  including the one called `mountain-pass`. The rules were ready and the
  generators were not. `mapgen` now has a height vocabulary (`_raise`,
  `_terrace`, `_mound`, and `_storey`/`_stair` for real floors) and every
  archetype but `open-water` uses it: a dais or a sunken floor in a chamber,
  tiers around the arena's pit, a rock shelf in a cave, benches on the pass,
  a knoll on open ground, hummocks in the swamp, a standing floor over a fallen
  cellar in a ruin, walkways above a sewer's channel, a rampart round a camp,
  rooms a step apart in a complex — and two real STOREYS where the fiction has
  always had one: a tavern GALLERY and a street's ROOFTOPS, both reached by
  stairs the generator lays. Two heights only: a STEP (5 ft) is cheap to climb
  and free to come down, so it shapes a fight without punishing anyone; a LEDGE
  (10 ft) is the one you have to decide about, because stepping off it is a
  fall. A ramp is only worth laying on a ledge — half of a step is two feet,
  which is a number nobody needs. The DM board now states the high ground
  (`_elevation_summary`) whether or not anyone is standing on it: it used to
  mention height only on a creature's own line, which is no help at all to a DM
  deciding whether anybody *should* take it. The selftest guards it — a
  generator may be rewritten, but not back into a table top.
- **Elevation is PER STOREY, and it was the one such fact left out.** Terrain,
  fog, live sight and light are each stored level 0 on the row and upper floors
  inside `levels`, because they are the same kind of fact about what a storey
  looks like. Elevation was a single flat map belonging to the ground — so a
  gallery could not have a step, a rooftop could not have a ridge, a hold could
  not have a platform, and **the whole height vocabulary the ground floor grew
  (`_raise`, `_terrace`, `_mound`, `_plateaus`) stopped at the stairs.** Every
  upper storey in the game was a table top by construction, which is the same
  complaint the fourteen flat archetypes answered, one floor up.
  `elevation_of(row, level)` mirrors `fog_of` exactly, and the height
  primitives all take a `level`, so the vocabulary now works on any floor.
  **A level's `base_ft` is where its FLOOR sits; its elevation is what stands
  ON that floor.** Adding them is `token_height_ft`'s job and only its job — do
  it in two places and a one-foot step on a fifteen-foot gallery is sixteen
  feet in the air. Everything that reads a height takes a level now:
  `_height_at`, `_drop_ft` (a step is WITHIN one storey; the way between floors
  is a connector), `_effect_cost_fn` (climbing a gallery's step costs what that
  step is, not what the hall below happens to have at the same coordinates),
  the spell-area origin, the cover preview, and `bridge`'s band moves.
  Two deliberate uses, and the selftest pins both. A street's **rooftops** are
  raised by RING in from the eaves — a hipped roof, not a mesa with a rim,
  which is what "raise every interior square" gives on a block whose interior
  is most of it — with a second tier only where a block is deep enough to have
  a middle, and stepping off THAT is the ten-foot fall the rules make you
  decide about. A taproom's **gallery** gets a landing at the far end from its
  stair, which is the whole asymmetry a walkway can honestly carry. The other
  guard is the invariant that catches the classic mistake: **no storey may
  carry height on a square it has no floor on** — a primitive writing the
  ground's coordinates onto a storey that is mostly open air is height on
  nothing.
- **`_plateaus` stacks a whole board, and it climbs AWAY from the camera.**
  A ledge on a flat board and a board that IS stepped are different things: the
  primitive lays two to four tiers with impassable rock between them and two or
  three RAMPS, which is what makes high ground a position to be taken rather
  than a square anyone can scramble onto from anywhere. Boundaries wander,
  because a straight terrace is a retaining wall rather than a hillside, and the
  ramps are cut before the faces are drawn so no tier is ever sealed. The
  `terraces` archetype is built entirely from it (a DM asking for a mesa, a
  plateau, an escarpment or a quarry gets one), `mountain-pass` is now genuinely
  stepped, and `open`, `clearing` and `ruins` come out fully terraced about a
  third of the time — not always, or every board is the same board.
  **Which way it climbs is a drawing decision with a reason**: the isometric
  view looks along +x and +z, so a tier rising TOWARD the viewer hides its own
  riser behind the tier in front of it. The first terraced board measured as
  perfectly flat for exactly that reason — every height was present and not one
  face was visible. Ground rises away from the camera.
- **The LLM is not the only thing that moves a creature.** The engine decides a
  BAND (`melee with Gruk` / `near` / `far`) and `bridge.apply_band_move` turns
  it into a square — and that translation read flat distance alone, so on a
  board made of ledges and terraces every monster archer stood in the mud below
  them however loudly the DM prompt talked about high ground. It now prefers
  height: strongly for a creature holding a range band (a shooter belongs up),
  and as a tiebreak among the equally-close squares when closing to melee.
  Height counts only up to one LEDGE above the mover, so a terrace two tiers up
  is never chosen for being high — it is taken a tier per turn. That is a limit
  on the PREFERENCE and not on the move: a band move is already unbounded in
  distance (`free=True, enforce_speed=False`, because the engine charged it in
  its own coarse economy and the board must not bill it twice), and making band
  moves pay real feet is a change to the engine's abstraction rather than to
  this translation.
- **Play only uses what the prompt asks for.** The board grew height and the DM
  would have kept fighting on the floor: the active-board block explained how to
  CREATE a ledge and how to rule on cover, and never once said the ground was
  worth contesting. It now gives the tactical direction outright — shooters
  climb, melee shoves them off, a hurt creature drops out of sight and takes the
  fall as the cheaper price — and `vtt_smoke` pins that a generated chamber has
  high ground AND that the DM board states it with what it costs. The offline
  demo board carries a dais for the same reason: it is the only board a browser
  can draw with no backend, and it was flat while every real board grew height.
- **A thing that is ONE thing is GROWN, not speckled.** `mapgen._blob` throws
  N independent darts inside a radius, which is right for scattered rock and
  thin scrub and wrong for anything that is a single object. Measured, it was
  wrong in three places at once and each had been that way for as long as the
  generator existed: a bog's pools had a **median size of ONE SQUARE** —
  eighty-five puddles across a 46x34 board, no water anybody could see, wade or
  swim, and a foot of basin cut under every one of them; the reef's "coral
  heads standing in banks", which its own docstring has always promised, were
  forty-odd single squares; and a kelp bed was confetti. `_patch` grows from
  the frontier instead, so every square touches another and the outline still
  wanders. Same coverage, real bodies — median clump 6-12 squares with meres up
  to 130. `_blob` stays, documented as SPECKLE, for the things that really are
  scattered. The lesson generalises past water: when a generator's own prose
  says "bank", "bed", "pool" or "stand" and the picture says confetti, the
  primitive is the thing to look at.
  **`_drifts` is the same question one level down**, about DENSITY rather than
  shape. `_scatter` decides square by square, which is right for a thing that
  IS one square — a crate, a boulder, a fallen pillar, a patch of rubble — and
  wrong for anything that GROWS. At 15% decided per square a bog came back a
  checkerboard of reed and mire with no bank anywhere, and a wood an even
  stipple of bramble with no thicket in it. Same coverage, laid in stands.
  Deliberately for PASSABLE growth only, so it needs no connectivity guard of
  its own and REFUSES a blocking code at the call: a stand of reed walls
  nothing off, and anything that could belongs in `_scatter`, which checks.
- **A pool is brim-full ONLY if this code cut the hollow.** `water.surfaces`
  filled every basin to just under its bank, which is right for a pool `sink`
  made — the sink cuts it exactly deep enough — and wrong for a bed some
  generator had already dug for another reason. A forest's stream runs along
  the floor of a five-foot GULLY, and reading the bank alone put **4.6 ft of
  water in something the description calls shallow**, hiding the relief the
  gully exists to carry. The surface is capped by the deepest square's own tile
  depth above its own bed, so a stream is two feet deep in a five-foot cut and
  a sunk pond is still full to the brim.
- **Boards were coming out in fifty pieces, and nothing said so.**
  `_connect_regions` carved exactly ONE corridor per pass and gave up after
  twelve. A clearing's ring of trees leaves dozens of four- and five-square
  pockets between the trunks, and FOUR was the fill threshold, so every one of
  them qualified for a corridor and only twelve ever got one: **fifty to
  seventy-eight regions on a finished board**, most of them unreachable. The
  "did the generator collapse" guard counts WALKABLE squares, not connected
  ones, so it passed every time. Every outstanding region is carved in ONE pass
  now — carving only ever ADDS connectivity, so the main region a later carve
  aims at is still main — and the function RETURNS whether it managed.
  `POCKET_FLOOR` is 8: two hundred square feet, room for a creature and its
  reach, which is the smallest space where anything can happen. Absolute, for
  the `PLAYABLE_FLOOR` reason. Below it a "region" is a gap in the scenery and
  a corridor to it spends a real passage on somewhere nobody will ever stand.
- **A dead pocket is filled with SOLID, and most boards had none to hand.**
  `_dominant_blocker` took the commonest impassable code, and on most outdoor
  boards the commonest impassable thing is the MEDIUM: a bridge board filled
  its pockets with CHASM (a hole, and one a flier crosses, so it filled
  nothing), a ship's deck with DEEP WATER, an open field with CRATES, and the
  open sea with a stray dungeon wall. `FILL_CODES` is derived from the tile
  table rather than listed — a fill blocks every medium, it SCREENS (half cover
  is furniture standing in a gap, not the gap closed), and it is not an
  aperture — which leaves wall, rock, tree and pillar. The fallback is ROCK
  rather than wall, because a wall is something somebody BUILT and a board with
  no solid on it is open country or open sea.
- **A LANDMARK may not seal anything off either.** It is stamped AFTER the
  connectivity net — it has to be, or the net would carve a corridor straight
  through a colossus — and that means nothing was left to notice. Measured: a
  nine-square step pyramid landed flush against the right edge of a 56-wide
  board with its way in facing off the map, and its own thirty-five-square
  interior became unreachable. `fits` demands a clear margin all round and
  SKIPS the part of that ring which is out of bounds, so the board edge had
  been standing in for clear ground — the `_road_beyond` mistake again, in a
  different file. `setpieces_for(joins=)` re-checks after stamping and UNDOES
  the placement, then tries elsewhere: a set piece is optional scenery, so
  refusing is free. The standing guard is that EVERY archetype at three sizes
  comes out one region, which is a check nothing made before and which both of
  these were hiding behind.
- **...and `_scatter` re-flood-filled the whole grid after every crate.** The
  guard is right — a scattered impassable square must not cut the board in half
  — and it was asking the question the most expensive way there is: a full
  `_regions` traversal per square laid, ~42 whole-board flood fills per street.
  `_locally_joined` settles the easy case by asking whether the square's own
  open NEIGHBOURS can still reach each other without it, inside a small budget:
  if they can, nothing that used to route through it has lost its way, so no
  split is possible. **Sound in one direction and that is all it may be** — a
  yes skips the real check, so a yes must mean yes, and it is brute-forced
  against the full scan on dense random boards in the selftest, which is the
  only honest way to pin an approximation.
  **And it has to actually ANSWER — which for a while it did not.** Written
  with a stack it is a DEPTH-first search, and on open floor a DFS wanders a
  hundred squares across the board before it comes back to the neighbour
  standing right beside where it started; the budget ran out and a single crate
  dropped in an empty room came back "not joined". Sound, useless, and
  invisible, because every answer was still right — it just fell through to the
  full scan every time. It fired on FOUR of 193 placements. Breadth-first, and
  the selftest now asks how OFTEN the fast path fires as well as whether it
  lies. street 170 -> 29 ms, ruins 143 -> 48, open 127 -> 7, and 17 ms across
  the whole catalogue; the selftest 51 s -> 25 s.
  **A set piece's own passable squares are part of the question.**
  `_locally_joined_cells` asked only about the RING around a footprint, which
  is why the pyramid case survived it: flush against the board's edge, the
  outside stayed joined all the way round and the thirty-five squares inside
  were sealed. A landmark is not a solid block — a stepped pyramid has terraces
  you walk on.
  It changes two archetypes' layouts, and the change is the FIX: the old guard
  reverted a crate whenever the BOARD had more than one region, not when that
  crate was what split it — so on a tavern or a cave that momentarily had a
  pocket somewhere else, every impassable scatter square was refused and the
  room came back with less clutter than the generator asked for. Region counts
  are unchanged or better on all 22 archetypes.
- **A board costs 9 ms now, and it cost over a hundred.** The four things
  that were expensive, in the order they were found, and none of them was the
  layout: the rotated footprint rebuilt per candidate square (`_turned`, now
  cached by slug and quarter turn); a full board flood fill after every
  scattered crate (`_locally_joined`, a bounded BREADTH-first check); the
  landmark placer walking a hundred and twenty squares to be told no
  (`_prefix`/`_count`, two summed-area tables per piece, so a hopeless spot is
  eight lookups); and `_regions` asking `passable` -> `code_cost` -> `get` ->
  `in_bounds` per square AND per neighbour, when connectivity depends on the
  code and the medium and nothing else (`_connective_codes`, a frozen set).
  **Every one is pinned as an EQUIVALENCE, not as a speed**: byte-identical
  boards and byte-identical landmark placements across all 22 archetypes, the
  connective set compared square by square against `passable`, the prefilter
  compared against `fits` on real boards, and the local check brute-forced
  against the full scan. A faster answer that is a different answer is not an
  optimisation.
- **Landmark placement was 89% of the cost of generating a board.**
  `setpieces.fits` is asked about every square, for every piece and every
  quarter turn, and it called `_turned` each time — rebuilding the rotated
  footprint strings from scratch for an answer that depends only on the piece
  and the angle. Measured on a swamp: **6.0 s of 6.75 s** over six boards, none
  of it different from the time before. Cached by `(slug, quarter turn)` rather
  than by the piece, because `SetPiece` is frozen but carries a dict and so is
  not hashable; every caller treats the result as read-only. **362 ms to 102 ms
  per board**, byte-identical output with a warm cache and a cold one.
  `terrain.code_cost` is the same move for the inner loop — a square's cost
  depends on its code and the medium and nothing else.
- **A SKY ISLAND hangs at ONE height, and it is the whole island.** The height
  was stamped as a 7x7 BOX on the middle of a round island, so a stone hanging
  twenty feet up had a square mesa on it and a rim at zero — the picture flatly
  contradicting the shape, with the rules agreeing with the picture. `_island`
  returns its squares now and all of them are raised. A knoll on top is fine,
  because it RIDES on the island's own height; two base heights in one rock is
  not, and that is what the selftest asks. **Its top being broken is NOT the
  country's business** — this was the last outdoor archetype with no relief and
  the obvious move was to hang it off `_ruggedness`, which would be the camp's
  bank all over again: an island is a torn-off chunk of rock in open air, and
  what lies a thousand feet below it shaped neither its stone nor its top. What
  it DID want was `_for_area` (more sky, more islands) and a placement that
  STOPS when nothing is far enough from its neighbours — two that merge are one
  continent with a ten-foot cliff through the middle of what the board calls
  one rock. 2 merged in 345.
- **A liquid surface is LEVEL, and water lies in a DEPRESSION.** `~` and `W`
  were on `terrain.SOFT_GROUND`, so a pool's surface was averaged with the
  ground around it and given the ordinary ripple — a swamp pool with a hummock
  beside it ran visibly UPHILL into the bank, which is the one thing water
  never does. Off the list; the `seabed-*` skins keep `soft` instead, because a
  board fought UNDER the water has no surface in view and its floor is ordinary
  ground that should roll. The skin answers first, exactly as it does for scree
  and cobbles sharing a `.`. Level is not enough on its own, though: a pool
  flush with its bank is paint on a floor. `vtt/water.py` cuts the BED into a
  basin below its own shore — deeper the further from the bank, so the shallows
  are where anyone would expect them — as real elevation in whole feet that
  every rule reads, capped under a LEDGE so walking into water is never
  reported as a fall and wading out costs the foot-per-foot the SRD charges.
  It only ever lowers a square, so a generator that already dug its channel (a
  sewer's sludge run under its walkways) keeps what it dug. `surfaces()` is the
  other half — the sheet put back on top, or the depression reads as a hole —
  and it is traced on the SERVER and shipped in `state()` for the `hull.py`
  reason: a surface belongs to the whole POOL, no square can see one, and two
  languages tracing it separately is two answers. The shore is the LOWEST dry
  square a pool touches, which is the one it would spill over.
- **A town is somewhere you go IN.** A street was a block of solid `#` with a
  roof traced over it: scenery you fought around, never in. A house is not a
  different KIND of thing from a tent — `structures.townhouse` is
  :func:`shelter` with a party wall on each side and its door on a NAMED side —
  so everything a tent already earns comes with it: the inside is real squares,
  cover and sight read the walls, and the way in is a `/` doorway the engine
  already understands. `shelter` had to learn `door_side` for it: a door is
  onto the STREET, and one rolled onto the back wall of a terrace opens into
  the neighbour's masonry. The selftest checks that every open square on a town
  board is reachable from every other, which is the check that catches exactly
  that.
  **A TERRACE IS NOT ONE BUILDING**, and two things had to say so. The roof
  tracer groups by SKIN, and every house on a terrace wears the same plaster —
  so the whole row came back under one roof, which is a warehouse.
  `hull.roofs(footprints=…)` takes the generator's own list of houses
  (`GeneratedMap.buildings`, carried into `notes` so `state()` can trace it
  again), and each gets its own. And a terrace is not one HEIGHT: houses stand
  one or two storeys taller than their neighbours, which is what per-storey
  elevation on the rooftops level is for.
  **DEPTH IS SHARED between a block's two frontages**, and getting that wrong
  is how 175 pairs of houses across 120 boards came to be built on top of each
  other: both took `min(deep, block height)` independently, so any block
  between one and two houses deep had its terraces overlap — the second house
  overwriting the first's walls, and the first's roof left traced over squares
  that were no longer there. A block up to two houses deep is split down the
  middle, back to back; a deeper one gets a terrace at each road and a YARD
  between them. **An alley is DECIDED, never discovered**: a yard with no way
  through is a sealed block, and `_connect_regions` then carves its own hole in
  somebody's wall, which reads as nothing at all — exactly as it does when it
  punches through a cliff. So the frontage is PLANNED before anything is built
  (`_plan`), and where a yard exists the narrowest house on the run gives up a
  square to buy the alley — refused if that would take it under the smallest
  house a street has, which leaves a block with no yard rather than a terrace
  of sheds.
  **A LAID floor does not ripple.** The inside of a house took the archetype's
  default for `.`, which on a street is `cobbles` — `soft` on purpose, because
  a road follows the ground it is laid over. A floor does not: somebody
  levelled the plot and laid boards on it. `house-floor` shares the taproom's
  SUBSTANCE so it costs no second swatch, with words of its own; a ruin's
  inside gets `ruin-floor`, since nothing was scrubbing those boards.
  **Frontage cannot be found by scanning.** The first version looked for runs
  of wall with a road beside them, and one alley put a road square in every
  row, so every row read as facing a street and no terrace was ever laid. The
  BLOCKS are derived from the lane positions instead — the roads are laid by
  the generator, so it already knows where they are.
  **A road is LAID and it is not FLAT.** "Laid things are flat" is about a
  FLOOR — a dungeon's flagstones, a ship's deck — where the builder levelled
  the site, and nobody levels a hillside to put a street on it. `cobbles` is
  `soft`, and a street falls across the board in ONE-FOOT steps: the smallest
  the rules have, so climbing it costs the foot per foot the SRD charges and
  nothing on the roadway is ever a drop.
  **HOW steep is the COUNTRY's business, not a die's.** The fall was
  `rng.randint(3, 8)` whatever the board said it stood in, so a town on the
  plains and a town clinging to a mountain got the same slope off the same
  roll — the `_for_area` complaint arriving from another direction, a number
  DERIVED from something the board already knows being rolled instead. The
  answer lives in `placelore.RELIEF` (see the terrain rule below), handed DOWN
  as an input the way `style` and `wanted_rooms` are; `mapgen.terrain_of` reads
  the DM's prose only for a caller that has no place — a demo, the Grounds, the
  selftest. Two dials, because "steeper" alone is the
  wrong answer for a mountain — a mountain road is steeper AND it is not a
  ramp, so it climbs, saddles and climbs again and is CANTED across its width
  as well, which is most of what makes one read as cut into a hillside. Flat
  country wants the opposite of both, and the low end of its range is ZERO: a
  street on a plain is genuinely allowed to come back level.
  The profile is a WALK toward a target curve rather than a sampling of it,
  which is what enforces `ROAD_MAX_STEP_FT` by construction — no budget and no
  amount of waviness can put a drop in a street, and a road too steep for its
  country simply arrives at the far end still climbing. **A BUILDING is level
  INSIDE**, and the steeper the street the more that matters: a house six
  squares deep on a mountain road would otherwise have six feet of fall across
  its own floor. The plot is cut and filled to one height and the difference
  lands OUTSIDE, as the step up to the door that every hill town has — allowed
  to be a step, never allowed to be a fall.
  The same treatment reaches `ruins`: one standing building in three, with a
  doorway and sometimes a floor still up. A ruin drawn only as broken outlines
  is walls to run between and never anything to be inside, and the survivors
  are what make the outlines read as ruins.
- **A bigger board is MORE OF THE PLACE, not a bigger place.** Boards are
  roughly **four times the area** they were (combat 24x18 -> 46x34, which is
  230 by 170 feet), because a 120-ft board is enough to stand and trade blows
  on and not enough to skirmish, break off or kite: a longbow reaches 150 feet,
  so backing out of reach meant backing off the map. The area is the whole
  change — **nothing was stretched to fill it.**
  A square is five feet, which makes almost every dimension on a board a real
  measurement, and that is the thing to hold: a chamber is 20-45 ft, a great
  hall 90, a roadway 25, a block of houses 40 deep, a mountain track 10-30. Hold
  the FEATURES in feet and let the COUNTS grow (`_for_area`) and doubling a
  board doubles the number of rooms; write a feature as a fraction of the board
  and doubling it doubles every room. **Measured before any of this: between
  24x18 and 48x36 the dungeon complex grew its rooms 7.5x, the taproom 5.2x,
  the crypt 4.9x.** Six halls of seventy-five by sixty feet is not a dungeon,
  and a 190-ft taproom is a barn. `_bsp_cells` stopped at `depth >= 3` — a
  fixed EIGHT cells however big the rectangle — which is the bug in one line.
  What changed, and why each is the right shape: the complex and the crypt
  split until cells are ROOM-sized; the tavern caps its taproom and puts the
  rest of the inn behind it (`_inn_rooms`); `dungeon-room` caps its great hall
  and rings it with side chambers, **unless the margin is too small to hold
  one**, in which case the hall takes it — a board that was already one room
  must stay one room; the street lays a BLOCK GRID with a real roadway and
  cross streets, and blocks are two buildings deep so nothing is left solid;
  the sewer's bore is a bore and a bigger board holds more tunnels; the camp
  pitches more tents. Open country needs no rule at all: a meadow, a forest and
  a marsh SHOULD be one region four times the size, because that is what more
  of them is.
  **The board-collapsed floor was a fraction of the area, and that is a bug
  that only appears when boards grow.** "An eighth of the board must be
  walkable" is sensible at one size and wrong at another: the walkable content
  of a corridor-shaped place grows with its LENGTH, so a 48x36 mountain pass
  producing four times the track was condemned for not producing eight times —
  and silently replaced with a MEADOW, because the fallback is a real board.
  `PLAYABLE_FLOOR` is absolute. The guard is
  `selftest`'s "a big X is no WIDER than it was, only longer": the widest clear
  span of a built place, which is the one measure that works for a room and a
  roadway alike — a street is legitimately one long thin rectangle and a hall
  is not. Verified to fail by exactly that check with the old room sizing
  restored.
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
- **A fight begins with everyone knowing something different.** Until
  `Combatant.awareness` every fight started squared up, so an ambush read
  exactly like a stand-up brawl. Three states, per CREATURE (half a camp waking
  is the interesting case): `alert` knows where you are and is the default;
  `suspicious` has heard something and, on its turn, SEARCHES rather than
  swinging at a square it has no reason to think you are in — which runs
  through the board's existing hide contest, Perception against the Stealth
  roll it already remembers, with `found_by` kept per creature; `unaware` is
  SURPRISED on round one, losing move, action and reaction entirely, and comes
  out of it **suspicious rather than blind**, because a creature that stays
  unaware after steel is drawn is a statue for the rest of the fight.
  `[[COMBAT: start | The Sleeping Camp | unaware]]` sets the opposition (the
  preset outlives the hook, since the roster arrives one `add` at a time), and
  `[[COMBAT: alert | Sentry | suspicious]]` is the one call every waking —
  a failed Stealth check, a shout, a slammed door, a spell — routes through.
  Escalation only ever goes UP: a creature that has seen you does not go back
  to wondering. **The DM sets the state and the board contests it**; the prompt
  says outright never to roll a creature's Perception by hand. "Roll
  initiative" from a player means start the fight and put the board out.
- **A jump is the one way over what you cannot walk.** Boards grew chasms,
  ten-foot channels, ledges and stacked terraces and there was no rule for
  going OVER any of it — a creature could climb at a foot per foot or walk
  round. `VttEngine.jump` uses the SRD's numbers as written: a running long
  jump clears the creature's STRENGTH SCORE in feet, a standing one half that,
  and "running" means it has already moved 10 ft this turn (checked, not
  assumed). **The squares crossed are not checked and the landing is
  everything** — that is what a jump IS — so the landing must be somewhere the
  creature could stand, empty, and no more than a high jump above the take-off:
  a ten-foot ledge is a CLIMB, and letting a hop reach it would quietly delete
  the climb rule. Landing lower reports the drop exactly as stepping off does,
  and the jump costs its own distance in movement, because a jump is movement
  and not a free way across difficult ground. Strength is read off the stat
  block or the sheet with the `senses` lazy-lookup pattern and degrades to 10.
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
- **A band change has to reach the BOARD, and a move you cannot finish still
  covers ground.** `combat/` thinks in bands and `vtt/` in squares, and
  `_do_move` used to set the band and stop — so with a spatial provider attached
  a monster that closed to melee had its swing measured from the square it
  started on and refused. Every archetype stopped resolving. It pushes the band
  to the board now (`BoardSpatial.move_to_band`), and a move too long to
  COMPLETE walks as far as it can (`advance_toward`) instead of being refused:
  opposed spawn zones are ninety feet apart, so all-or-nothing left every melee
  creature standing on its spawn square for the whole fight. The AI can also
  LEAP — `gap_between` is the cheap planner question, `jump_toward` takes the
  run-up and the leap, and the run-up is what makes it work at all (a standing
  jump clears one square, which lands you in the channel). `scripts/ai_arena.py`
  had never attached a board, so every number it ever printed was for a
  blindfolded engine.
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
