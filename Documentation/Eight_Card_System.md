# Eight Card System

## Persistent World Architecture & Infrastructure

The Eight Card System is the procedural world-building backbone of The Oracle. It structures the game world as a **multi-scale hexagonal grid** — a nested hierarchy of hex tiles from tactical 5ft spaces up to continental regions. The AI DM reads and writes world state through MCP tools, enabling a persistent, living world that evolves over time with decay-priority temporal tracking.

---

## Hex Grid Fundamentals

The world uses **flat-top hexagonal tiles** with axial coordinates `(q, r)`. Hexagons tile perfectly into a 2D plane with 6 neighbors per hex. All hex math follows the conventions from [Red Blob Games — Hexagonal Grids](https://www.redblobgames.com/grids/hexagons/).

Direction labels for hex neighbors:

```
    NW    NE
      \  /
  W ---  --- E
      /  \
    SW    SE
```

---

## Scale Hierarchy

The world is organized into three nested scales. Each larger hex is composed of smaller hexes at the scale below.

```
Region
├── Contains 37 Encounter Areas (radius-3 hex ring)
│   ├── Contains ~3,900 Spaces each
│   │   └── Space = 5ft hex prism (q, r, z)
```

### Level 1: Space (5ft hex prism)

The atomic unit of tactical play.

- **Hex edge:** 5ft
- **Hex width (flat-to-flat):** ~8.66ft
- **Hex area:** ~65 sq ft
- **Vertical slice:** 5ft per z-level
- **Coordinate:** `(q, r, z)` — hex position + elevation layer

Spaces are **3D hex prisms**, not flat tiles. The z-axis supports:
- Flying creatures at different altitudes
- Multi-story buildings (each floor = a z-level)
- Underground layers beneath the surface (negative z)
- Falling, climbing, levitation

Each Space carries:
- Terrain type (stone, grass, water, mud, etc.)
- Movement cost modifier
- Cover value (none, half, three-quarters, full)
- Elevation (z-level relative to local datum)
- Occupants (creatures, objects)

### Level 2: Encounter Area (250,000 sq ft hex)

The primary unit of exploration and encounter play.

- **Hex edge:** ~310ft
- **Hex width (flat-to-flat):** ~540ft
- **Area:** ~250,000 sq ft (~5.7 acres)
- **Spaces across (flat-to-flat):** ~108
- **Total Spaces per Encounter Area:** ~3,900 (per z-level)

Encounter Areas are what a player interacts with as a "place." They carry biome data, terrain features, narrative lore, and temporal state. When a player moves to the edge of one Encounter Area and crosses into the next, the adjacent Encounter Area is **generated on demand** if it doesn't already exist.

### Level 3: Region (37 Encounter Areas)

The macro unit of geography, politics, and climate.

- **Layout:** Radius-3 hex ring (1 center + 6 + 12 + 18 = **37 Encounter Areas**)
- **Approximate width:** ~3,800ft (~0.7 miles) across
- **Approximate area:** ~9.25 million sq ft (~212 acres)

Regions carry high-level attributes:
- Climate zone (derived from latitude/longitude on world map)
- Political affiliation (kingdom, faction, wild)
- Regional biome tendency (forest belt, desert, coastal, etc.)
- Trade routes, roads, and major waterways
- Regional threats / dominant creature types

Regions are the unit at which world-gen seeds terrain. Individual Encounter Areas within a Region inherit the Region's climate and biome tendency, with local noise variation.

---

## Schemas

### Space Schema

```json
{
  "q": 0,
  "r": 0,
  "z": 0,
  "terrain": "grass | stone | water | mud | sand | wood | ice | lava | void",
  "movement_cost": 1.0,
  "cover": "none | half | three_quarters | full",
  "occupants": [],
  "objects": []
}
```

Spaces are generated in bulk when an encounter begins in an Encounter Area. They are **not persisted individually** unless modified — the base terrain grid can be regenerated deterministically from the Encounter Area's seed.

### Encounter Area Schema

```json
{
  "id": "uuid",
  "region_id": "uuid",
  "q": 0,
  "r": 0,
  "name": null,
  "biome": "forest | desert | tundra | swamp | plains | mountain | coastal | urban | dungeon",
  "elevation": 0,
  "moisture": 0.0,
  "temperature": 0.0,
  "terrain_tags": ["dense_trees", "rocky_outcrops", "shallow_river"],
  "terrain_seed": 12345,
  "discovered": false,
  "lore_entries": [],
  "encounter_flags": {},
  "points_of_interest": [],
  "temporal_state": {},
  "created_at": "timestamp",
  "updated_at": "timestamp"
}
```

### Region Schema

```json
{
  "id": "uuid",
  "q": 0,
  "r": 0,
  "name": null,
  "climate_zone": "tropical | subtropical | temperate | subarctic | arctic",
  "latitude": 0.0,
  "longitude": 0.0,
  "political_affiliation": null,
  "dominant_biome": "forest",
  "regional_threats": [],
  "trade_routes": [],
  "major_waterways": [],
  "created_at": "timestamp"
}
```

### Edge Schema (Encounter Area level)

```json
{
  "node_a": "uuid",
  "node_b": "uuid",
  "direction": "E | NE | NW | W | SW | SE",
  "traversal_type": "open | road | river | cliff | wall | door | portal",
  "traversal_difficulty": "easy | moderate | hard | impassable"
}
```

---

## Lazy Generation

Generation is **on-demand** — only what the players interact with gets fully built.

| Scale | When Generated | Persistence |
|---|---|---|
| **Region** | World creation (noise-seeded) | Always persisted |
| **Encounter Area** | Player approaches an adjacent edge | Persisted once created |
| **Spaces** | Encounter starts in the area | Regenerated from seed; only modifications persisted |

### Generation Flow

```
Player at edge of Encounter Area → moves into adjacent hex
    ↓
Check: does adjacent Encounter Area exist in DB?
    ↓ No
Lookup parent Region → get climate, biome tendency
    ↓
Generate Encounter Area:
  - Seed from Region seed + (q, r) coordinates (deterministic)
  - Apply Perlin noise for elevation, moisture, temperature
  - Derive biome from Whittaker lookup: f(elevation, moisture, temp)
  - Derive terrain_tags from biome + local noise
  - Derive edge traversal types from neighbor comparison
    ↓
Insert into DB → mark discovered
    ↓
If encounter triggers:
  - Generate Space grid from terrain_seed
  - Apply terrain type per-Space from Encounter Area biome + noise
  - Load into tactical engine
```

---

## Temporal State Tracking

Each Encounter Area maintains a **decay-priority temporal state** — a layered history of events that naturally ages, with low-impact events decaying and high-impact events persisting.

### Time Windows

| Window | Duration | Detail Level | Decay Threshold |
|---|---|---|---|
| **Immediate** | Last ~6 rounds | Full detail (positions, spells, fires) | Everything kept |
| **Short-term** | Last ~1 hour | High detail (aftermath, bodies, debris) | Priority ≥ 1 |
| **Long-term** | Last ~24 hours | Medium detail (environmental changes) | Priority ≥ 2 |
| **Monthly** | Last ~30 days | Key events only | Priority ≥ 3 |
| **Yearly** | Last ~1 year | Major impacts only | Priority ≥ 3 |
| **Decadal** | Last ~10 years | Permanent changes only | Priority ≥ 4 |

### Event Priority Levels

```
COSMETIC   = 1   Footprints, minor litter — decays in hours
TACTICAL   = 2   Traps set, doors broken — lasts hours/days
STRUCTURAL = 3   Building damaged, bridge built — lasts months/years
GEOGRAPHIC = 4   River diverted, forest burned — lasts decades
LEGENDARY  = 5   Dragon's crater, ancient curse — permanent
```

### Decay Promotion Rules

When a time window rolls over, events are promoted to the next window only if their priority meets the threshold:

```
Immediate → Short-term:   priority ≥ 1 (everything survives)
Short-term → Long-term:   priority ≥ 2 (cosmetic decays)
Long-term → Monthly:      priority ≥ 3 (tactical decays)
Monthly → Yearly:         priority ≥ 3 (structural+ survives)
Yearly → Decadal:         priority ≥ 4 (geographic+ survives)
Decadal → Permanent:      priority ≥ 5 (only legendary persists forever)
```

### Temporal State Schema

```json
{
  "temporal_state": {
    "immediate": [
      {"event": "fire burning at (5,4,0)", "priority": 2, "timestamp": "...", "session_id": "..."}
    ],
    "short_term": [
      {"event": "3 goblin corpses near east tree line", "priority": 2, "timestamp": "...", "session_id": "..."}
    ],
    "long_term": [
      {"event": "rain washed blood away, scavengers took corpses", "priority": 1, "timestamp": "...", "session_id": "..."}
    ],
    "monthly": [
      {"event": "goblins abandoned camp, vegetation reclaiming path", "priority": 3, "timestamp": "...", "session_id": "..."}
    ],
    "yearly": [
      {"event": "watchtower erected on hilltop", "priority": 3, "timestamp": "...", "session_id": "..."}
    ],
    "decadal": [
      {"event": "forest fire scarred eastern half of area", "priority": 4, "timestamp": "...", "session_id": "..."}
    ]
  }
}
```

### AI Scene Composition

When the AI DM describes a node, it layers all active time windows bottom-up:

```
Base terrain (from world-gen seed)
  + Decadal state (permanent scars, ruins)
  + Yearly state (recent constructions, major events)
  + Monthly state (current political/environmental situation)
  + Long-term state (today's weather effects, daily changes)
  + Short-term state (recent activity in the area)
  + Immediate state (what is happening right now)
= What the player sees and hears
```

---

## Node Lifecycle

### Fresh Node (Undiscovered)
A newly generated Encounter Area contains only:
- Biome type (derived from Region + noise)
- Elevation, moisture, temperature values
- Terrain tags (derived from biome)
- Terrain seed (for deterministic Space generation)
- `discovered: false`

All narrative fields (`lore_entries`, `encounter_flags`, `points_of_interest`, `name`, `temporal_state`) are empty.

### Discovered Node
When players enter an Encounter Area, it is marked `discovered: true` and the AI DM may begin layering narrative context onto it via MCP tools, within the rules below.

---

## MCP Tool Rules — AI Write Permissions

The AI DM operates under strict rules to prevent world over-creation or narrative blur.

| Field | AI Can Read | AI Can Write | Notes |
|---|---|---|---|
| `biome` | Yes | **No** | Set by world-gen only |
| `elevation` | Yes | **No** | Set by world-gen only |
| `moisture` | Yes | **No** | Set by world-gen only |
| `temperature` | Yes | **No** | Set by world-gen only |
| `terrain_tags` | Yes | **No** | Set by world-gen only |
| `terrain_seed` | Yes | **No** | Set by world-gen only |
| `name` | Yes | Yes (once) | Cannot be renamed after set |
| `lore_entries` | Yes | **Append only** | Cannot delete or contradict terrain facts |
| `encounter_flags` | Yes | Yes (if unset) | Cannot overwrite a flag set in a prior session |
| `points_of_interest` | Yes | Yes | Can add; cannot remove or rename existing |
| `temporal_state` | Yes | Yes | Must assign priority; decay rules enforced by system |
| `discovered` | Yes | Yes (true only) | Cannot un-discover a node |

**Lore entry constraint:** Each lore entry must be tagged with a type (`history`, `rumor`, `observation`, `event`) and a session ID. The AI may not contradict facts established by a prior lore entry of type `history` or `event`.

---

## MCP Tools

### Read Tools
- `get_encounter_area(area_id)` — Returns full Encounter Area data including temporal state
- `get_adjacent_areas(area_id)` — Returns all 6 neighboring Encounter Areas with edge metadata
- `get_region(region_id)` — Returns Region data (climate, politics, threats)
- `get_region_areas(region_id)` — Returns all 37 Encounter Areas in a Region
- `search_areas(filters)` — Query Encounter Areas by biome, terrain tags, discovered state, etc.
- `get_area_lore(area_id)` — Returns all lore entries for an Encounter Area
- `get_world_path(area_a, area_b)` — Returns shortest traversable path between two Encounter Areas
- `get_spaces(area_id, z_level)` — Returns the Space grid for an Encounter Area at a given z-level

### Write Tools
- `discover_area(area_id, session_id)` — Marks an Encounter Area as discovered
- `set_area_name(area_id, name, session_id)` — Sets Encounter Area name (idempotent after first set)
- `append_lore(area_id, entry_type, content, session_id)` — Appends a lore entry
- `set_encounter_flag(area_id, flag_key, flag_value, session_id)` — Sets an encounter flag if not already set
- `add_point_of_interest(area_id, poi, session_id)` — Adds a named POI
- `record_event(area_id, event_description, priority, session_id)` — Records a temporal event at the Immediate window
- `modify_space(area_id, q, r, z, changes, session_id)` — Modifies a specific Space (terrain, objects, etc.)

### System Tools (not AI-callable)
- `generate_encounter_area(region_id, q, r)` — Creates a new Encounter Area from Region seed + noise
- `generate_spaces(area_id)` — Generates the full Space grid from terrain_seed
- `decay_temporal_state(area_id)` — Promotes/decays events across time windows

---

## Database

### Recommended Stack: PostgreSQL

PostgreSQL is preferred for self-hosting simplicity. The hex grid is a logical structure — adjacency is computed from coordinates, not stored as a separate graph.

### Schema

```sql
-- Regions: macro-scale geography
CREATE TABLE regions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  q INT NOT NULL,
  r INT NOT NULL,
  name TEXT,
  climate_zone TEXT NOT NULL,
  latitude FLOAT NOT NULL DEFAULT 0,
  longitude FLOAT NOT NULL DEFAULT 0,
  political_affiliation TEXT,
  dominant_biome TEXT NOT NULL,
  regional_threats JSONB DEFAULT '[]',
  trade_routes JSONB DEFAULT '[]',
  major_waterways JSONB DEFAULT '[]',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(q, r)
);

-- Encounter Areas: the primary world hex
CREATE TABLE encounter_areas (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  region_id UUID REFERENCES regions(id),
  q INT NOT NULL,
  r INT NOT NULL,
  name TEXT,
  biome TEXT NOT NULL,
  elevation FLOAT NOT NULL DEFAULT 0,
  moisture FLOAT NOT NULL DEFAULT 0,
  temperature FLOAT NOT NULL DEFAULT 0,
  terrain_tags TEXT[] DEFAULT '{}',
  terrain_seed BIGINT NOT NULL,
  discovered BOOLEAN DEFAULT FALSE,
  lore_entries JSONB DEFAULT '[]',
  encounter_flags JSONB DEFAULT '{}',
  points_of_interest JSONB DEFAULT '[]',
  temporal_state JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(region_id, q, r)
);

-- Edges: traversal metadata between adjacent Encounter Areas
CREATE TABLE edges (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  node_a UUID REFERENCES encounter_areas(id),
  node_b UUID REFERENCES encounter_areas(id),
  direction TEXT NOT NULL,
  traversal_type TEXT DEFAULT 'open',
  traversal_difficulty TEXT DEFAULT 'easy'
);

-- Space modifications: only stores diffs from generated baseline
CREATE TABLE space_modifications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  encounter_area_id UUID REFERENCES encounter_areas(id),
  q INT NOT NULL,
  r INT NOT NULL,
  z INT NOT NULL DEFAULT 0,
  modifications JSONB NOT NULL,
  session_id TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(encounter_area_id, q, r, z)
);

-- Session tracking
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  started_at TIMESTAMPTZ DEFAULT NOW(),
  ended_at TIMESTAMPTZ,
  metadata JSONB DEFAULT '{}'
);
```

---

## Terrain Generation Pipeline

The Eight Card System uses **custom procedural generation** — no third-party map generator. The pipeline uses layered Perlin/Simplex noise to seed terrain attributes deterministically.

### Tools

| Tool | Role |
|---|---|
| **`noise` or `FastNoiseLite`** (Python) | Perlin/Simplex noise for elevation, moisture, temperature maps |
| **NumPy** | Grid math and array operations |
| **Whittaker biome lookup** | `f(elevation, moisture, temperature) → biome` |
| **NetworkX** (Python) | Pathfinding and connectivity validation |

### Region Seeding Flow

```
World seed + Region (q, r)
    ↓
Multi-octave Perlin noise → elevation map for Region
Multi-octave Perlin noise → moisture map (different frequency)
Latitude-based gradient → temperature map
    ↓
For each of 37 Encounter Area positions:
  Sample noise at (q, r) → elevation, moisture, temperature
  Whittaker lookup → biome
  Derive terrain_tags from biome + local noise variation
  Compute terrain_seed = hash(world_seed, region_q, region_r, area_q, area_r)
    ↓
Insert Encounter Areas into PostgreSQL
```

### Space Generation Flow (on-demand)

```
Encounter Area terrain_seed
    ↓
Perlin noise at Space resolution (5ft hexes)
    ↓
Per-Space: terrain type, movement cost, cover, elevation (z)
    ↓
Apply any stored space_modifications from DB
    ↓
Return Space grid to tactical engine
```

---

## Integration with The Oracle

The MCP tools are exposed as FastAPI endpoints in `oracle-dm-backend/fastapi-dm.py` and consumed by the AI DM in `ai-dm-discord-bot/oracle-dm-discord-bot.py`.

The AI DM calls read tools to gather world context before describing a scene, and calls write tools to record what happened during a session. The temporal state system ensures the world evolves naturally — recent events are vivid, old events fade unless they left a lasting mark.

---

## Map Rendering & Avrae Coexistence

### Division of Responsibility

The Oracle and Avrae run side-by-side in the same Discord server with clearly separated roles:

| System | Owns | Commands |
|---|---|---|
| **Avrae** | Combat tracker (`!init`), dice rolling (`!r`), character sheets, spell/item lookups | `!` prefix |
| **The Oracle** | World state, hex map rendering, AI DM narration, lore, temporal state, procedural generation | Custom prefix |

Avrae handles **what happens mechanically**. The Oracle handles **where it happens and what the world looks like**.

### Hex Map Renderer

The Oracle includes its own hex map renderer that generates map images and posts them to Discord. This renderer is hex-native and operates at two scales:

#### Region Map (Encounter Area hexes)
- Shows the 37 Encounter Areas of a Region as hex tiles
- Color-coded by biome
- Icons for points of interest, settlements, roads
- Fog of war — undiscovered areas are dim/hidden
- Player position marker
- Used for **exploration and travel**

#### Encounter Map (Space hexes)
- Shows the ~3,900 Spaces of an Encounter Area
- Color-coded by terrain type (grass, stone, water, etc.)
- Elevation shading for z-level differences
- Token markers for players, NPCs, creatures
- Cover indicators, difficult terrain hatching
- Used for **tactical combat and encounters**

### Rendering Stack

| Tool | Role |
|---|---|
| **Pillow (PIL)** | Core image generation — draw hex grids, fill colors, place icons |
| **Cairo (pycairo)** | Alternative: vector rendering for crisp scaling at any zoom |
| **Pre-built hex sprites** | Terrain tile assets layered onto the grid |
| **Discord file upload** | Bot sends rendered PNG as message attachment |

### Render Pipeline

```
Player moves / encounter starts
    ↓
Oracle bot queries world state via MCP tools
    ↓
Hex renderer generates map image:
  - Build hex grid at appropriate scale
  - Apply biome/terrain coloring per hex
  - Place token markers at (q, r) positions
  - Apply fog of war for undiscovered areas
  - Overlay labels, POI icons, edge markers
    ↓
Post image to Discord channel
    ↓
AI DM posts narrative description alongside the map
```

### Avrae Sync Points

Though the systems are independent, they share context at key moments:

| Event | Avrae Does | Oracle Does |
|---|---|---|
| **Combat starts** | `!init begin` — tracks initiative, HP, conditions | Renders encounter map with token positions |
| **Player moves** | `!init move <name> <coord>` — updates position | Listens for position updates, re-renders map |
| **Attack/spell** | `!r`, `!cast` — resolves mechanics | Records environmental effects (`record_event`) |
| **Combat ends** | `!init end` — closes tracker | Updates temporal state with aftermath |
| **Exploration** | (not involved) | Handles all movement, generation, rendering |

The Oracle bot can optionally listen to Avrae's command outputs in the channel to stay in sync on combat positions — parsing `!init` status messages for token coordinates. This avoids requiring players to issue commands to both bots.
