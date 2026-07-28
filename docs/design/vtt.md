# The Tactical Board (`vtt/`)

A square-grid virtual tabletop that opens **only for the moments that need it**,
and closes again the instant they pass.

## Why it exists, and why it's not always on

The Oracle's default mode is prose: the DM narrates, the world graph remembers,
and combat runs on gridless spacing bands (`melee with Gruk` / `near` / `far`).
That is the right register for ninety percent of play — it's fast, it reads
well, and it costs almost nothing in the prompt.

It is the wrong register when *where you stand decides what happens*: a fight in
a room with pillars and a chasm, a trapped chamber, a puzzle you solve by
standing somewhere, the leg of a chase that crosses broken ground. There, "the
goblin is near you" is not an answer to "can I get behind the crates without
eating an opportunity attack?"

So the board is a spotlight. `vtt/triggers.py` holds the entire policy for when
it comes out, and `VttConfig` lets a DM retune it without touching wiring.

## The rule that runs through everything

> **The model decides fiction. The code decides mechanics.**

The LLM may decide that a board opens here, that the room is a cave, that the
wizard hurls a fireball at the altar. It never decides where the walls are, how
far 30 feet gets you, which squares the fireball covers, or whether the ogre has
cover. Those come from `mapgen`, `geometry` and `scene` — deterministic, seeded,
and asserted in `vtt/selftest.py`.

This matters because a hallucinated pillar is a rules bug, and a hallucinated
distance is a cheat. An illegal move comes back as a *rejection with a reason*
("that's 40 ft and Kara has 30 ft of movement left"), exactly like the combat
engine's contract, so the narrator kicks the problem back to the player instead
of quietly applying it.

## Layers

| module | what it owns |
|---|---|
| `models.py` | four tables in `oracle.db`: `vtt_map`, `vtt_token`, `vtt_effect`, `vtt_event` |
| `terrain.py` | the tile taxonomy (cost / sight / cover / hazard) and the `Grid` container |
| `geometry.py` | distance, pathing, line of sight, cover, spell templates, field of view |
| `mapgen.py` | 17 seeded layout generators, all guaranteed connected with opposed spawn zones |
| `art.py` | the diffusion battlemap (through `imagery/` → ComfyUI) |
| `scene.py` | `VttEngine` — the service the backend calls; `state()` for the UI, `render()` for the prompt |
| `bridge.py` | keeps the board and `combat/`'s initiative tracker in step |
| `triggers.py` | when a board is worth opening at all |

### Terrain

A board is one string per row, one character per 5-ft square. Compact enough for
a JSON column, cheap over the socket, readable in a log or a prompt:

```
RRRRRRRRRRRRRRRRRRRRRRRR
RR.....RRRRRRR..RRRRRRRR
RRd.....RRRRR..O.RRRRRRR
RRff..,.RRRRR..,..RRRRRR
```

Each code maps to a `Tile` describing what the square *does*: feet to enter
(`5` open, `10` difficult, `None` impassable), whether it blocks sight, what
cover it grants, whether standing there hurts, and whether a flier or a swimmer
can cross it anyway.

### Geometry

The rules-facing math, all pure functions:

* **Distance** — PHB 5-5-5 by default, the DMG 5-10-5 variant behind a config
  switch. Measured footprint-to-footprint, so two adjacent creatures are 5 ft
  apart and a Large creature threatens from its near edge.
* **Movement** — A* for a route, Dijkstra for "everywhere I could go", both
  charging difficult terrain, refusing to cut a diagonal between two walls, and
  respecting flying/swimming modes.
* **Line of sight** — corner rays, inset slightly inside their own square so a
  ray sliding along a wall face can't see through a solid wall (the classic
  grid-VTT failure) while genuine sight lines past a corner still work.
* **Cover** — the PHB corner rule, best case for the attacker: 1–2 blocked lines
  is half, 3 is three-quarters, 4 is total. Tiles that grant cover (a low wall, a
  crate) count at their own rating, and creatures in the way count as half cover
  (the DMG option).
* **Templates** — sphere, cone, line, cube, emanation, resolved to the exact set
  of squares, clipped by line of effect so a fireball doesn't leak through a
  wall. Cones and lines start at the *edge* of the caster's space, so nobody is
  caught in their own breath weapon.
* **Opportunity attacks** — a path that starts inside a threat's reach and ends
  outside it.

### Map generation

17 archetypes (`dungeon-room`, BSP `dungeon-complex`, cellular-automata `cave`,
`forest`, `clearing`, `street`, `tavern`, `bridge`, `ruins`, `camp`, `ship`,
`arena`, `crypt`, `swamp`, `mountain-pass`, `sewer`, `open`). Every generated
board is checked for a single connected walkable region — dead pockets are
carved together or filled back in as solid — and every board comes with two
spawn zones on opposite edges.

`archetype_for()` maps loose DM language ("a smoky taproom", "the sewer outfall")
onto the closest generator, so the DM never needs to know the names.

Layouts are pure functions of `(archetype, width, height, seed)`, so a stored map
row can be rebuilt exactly, and a room the party fought in last week comes back
the same.

### Art

`vtt/art.py` asks the diffusion backend to *paint what the generator already
decided*: the prompt is built from the tile inventory, the render canvas is sized
to the board's aspect ratio at a pixel budget, and the result is cached in
`entity_image` under a hash of the layout so an identical board never burns a
second render.

The art is a **texture, not the truth**. A diffusion model cannot be trusted to
put a wall on the exact square we asked for, so the client stretches the image
across the board rectangle and draws its own grid, blockers and tokens on top.
With no GPU up, `image_id` is `None` and the overlay simply draws tiles — the
tactical layer never depends on the picture existing.

## How it meets the systems already here

### Combat

`combat/` is untouched. It still owns initiative, HP, conditions, the action
economy and its gridless bands. The board adds exact position *underneath* that
and keeps both descriptions true at once:

* every combatant gets a token (size, speed and reach hydrated from the SRD row);
* after anything moves, each combatant's `position` band is recomputed from real
  distance, so `combat/engine.py` keeps validating reach exactly as before;
* when the *engine* moves someone by band — a monster's default AI closing to
  melee — `bridge.reconcile_bands` walks that token to a matching square
  **before** the bands are rewritten, so the two models can't drift apart.

The rule of thumb: **the grid is the truth when a board is out; the bands are the
interface.**

### The DM prompt

While a board is out, the compact ASCII board goes into the context — the map,
one line per creature with its position, distance to the nearest enemy and its
cover, and a summary of the effects on the field. A few hundred tokens buys
spatially-correct narration.

### The Activity

The overlay takes the scene panel's place while a board is out. Canvas draws the
world (art, tiles, grid, effect overlays, fog, movement wash, path preview,
ruler, pings); tokens are DOM elements carrying portraits, HP, condition pips and
pointer interaction.

The movement wash is a **server-costed** set of squares; the path drawn on hover
is derived from those costs client-side (no round trip per mouse move); the move
itself is a request the backend can refuse. A player may drag only their own PC,
and only on their turn.

## Hooks the DM can emit

```
[[VTT: open | combat | cave | The Sunken Shrine]]
[[VTT: move | Gruk | 9,5]]                       (pathed, costed, OA-checked)
[[VTT: move | Gruk | 14,5 | dash]]
[[VTT: place | Wight | 3,12]]
[[VTT: remove | Rat]]
[[VTT: effect | Fireball | shape=sphere | at=9,5 | size=20 | damage=8d6 fire |
      save=dex 15 | rounds=1]]
[[VTT: effect | Web | shape=cube | at=6,4 | size=20 | rounds=10 | difficult]]
[[VTT: clear | Web]]
[[VTT: terrain | 4,7 5,7 | rubble]]
[[VTT: door | 12,3 | open]]
[[VTT: reveal | 8,8 | 30]]
[[VTT: close]]
```

A fight opened with `[[COMBAT: start]]` gets its board automatically; the DM
doesn't ask twice.

## Running it

```bash
uv run python -m vtt.demo       # end-to-end walkthrough, temp DB, no GPU needed
uv run python -m vtt.selftest   # 89 assertions over the rules-facing behaviour
cd activity-ui && npm run build && node vtt-shot.mjs   # screenshot the overlay
```

## Known gaps

* **Elevation is stored, not enforced** — tokens carry `elevation_ft` and maps a
  sparse elevation map, but nothing charges climbing or applies high ground yet.
* **Fog is party-wide**, not per-player; there is no per-viewer vision.
* **Doors are stateful but not interactive from the overlay** — the DM opens
  them with a hook.
* **The combat engine still reasons in bands.** The board keeps them honest, but
  the engine does not yet consume exact feet for range checks, so a 120-ft
  longbow shot and a 30-ft one are both simply "far".
* **No token drag-and-drop** — click to select, click to move (touch-friendly);
  drag is a later nicety.
* **Art alignment is approximate** by design; a wall painted at the wrong square
  is cosmetic, but a fussy DM will notice.
