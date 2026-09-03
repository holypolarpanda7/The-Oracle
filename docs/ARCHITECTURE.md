# Architecture — the full module map

The complete, unabridged architecture section that used to live in `CLAUDE.md`,
including the long-form notes for each module. `CLAUDE.md` keeps a condensed map
that names every module; this is what each entry's hard-won detail was.

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
   - See `ai-dm-sicord-bot/MODULE_ARCHITECTURE.md` for the full module map
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

