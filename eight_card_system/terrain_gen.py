"""
Procedural terrain generation for the Eight Card System.

Uses layered Perlin noise to generate elevation, moisture, and temperature,
then derives biome via a Whittaker-style lookup.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass, field
from enum import Enum

import noise
import numpy as np

from .hex_math import Hex, hex_to_pixel, pixel_to_hex, DIRECTIONS, DIRECTION_LIST, _axial_round


# ─── D&D 5e Tactical Enums ───────────────────────────────────────────────────

class Cover(Enum):
    """D&D 5e cover levels (PHB p.196)."""
    NONE = 0       # No cover — open ground
    QUARTER = 1    # +2 AC — low wall, furniture, creature
    HALF = 2       # +2 AC +2 Dex saves — thick tree, half-wall
    THREE_QUARTER = 3  # +5 AC +5 Dex saves — arrow slit, heavy barricade
    FULL = 4       # Can't be targeted directly


class Lighting(Enum):
    """D&D 5e lighting levels (PHB p.183)."""
    BRIGHT = 0     # Normal vision
    DIM = 1        # Lightly obscured — disadvantage on Perception (sight)
    DARK = 2       # Heavily obscured — effectively blinded


# ─── Biome Lookup (Whittaker-style) ──────────────────────────────────────────

def _classify_biome(elevation: float, moisture: float, temperature: float) -> str:
    """
    Classify biome from normalized values (0.0–1.0).
    Elevation: 0=sea level, 1=peak
    Moisture: 0=arid, 1=saturated
    Temperature: 0=freezing, 1=scorching
    """
    if elevation < 0.15:
        return "ocean"
    if elevation < 0.2:
        if moisture > 0.5:
            return "swamp"
        return "coastal"

    if elevation > 0.8:
        if temperature < 0.3:
            return "ice"
        return "mountain"

    if elevation > 0.6:
        if temperature < 0.25:
            return "tundra"
        if moisture > 0.6:
            return "taiga"
        return "hills"

    # Mid elevation (0.2 – 0.6)
    if temperature < 0.2:
        if moisture > 0.5:
            return "taiga"
        return "tundra"

    if temperature > 0.75:
        if moisture < 0.25:
            return "desert"
        if moisture < 0.5:
            return "savanna"
        return "jungle"

    # Temperate
    if moisture < 0.2:
        return "plains"
    if moisture < 0.5:
        return "plains"
    if moisture < 0.75:
        return "forest"
    return "swamp"


def _derive_terrain_tags(biome: str, elevation: float, moisture: float) -> list[str]:
    """Derive terrain tags from biome and local values."""
    tags = []

    if biome == "forest":
        tags.append("trees")
        if moisture > 0.8:
            tags.append("dense_undergrowth")
    elif biome == "jungle":
        tags.extend(["dense_trees", "vines", "humid"])
    elif biome == "desert":
        tags.append("sand_dunes" if elevation < 0.4 else "rocky_desert")
    elif biome == "mountain":
        tags.append("rocky_outcrops")
        if elevation > 0.9:
            tags.append("snow_cap")
    elif biome == "swamp":
        tags.extend(["shallow_water", "reeds"])
    elif biome == "plains":
        tags.append("tall_grass" if moisture > 0.3 else "scrubland")
    elif biome == "coastal":
        tags.extend(["sandy_shore", "tidal"])
    elif biome == "hills":
        tags.append("rolling_hills")
        if moisture > 0.5:
            tags.append("scattered_trees")
    elif biome == "tundra":
        tags.extend(["permafrost", "sparse_vegetation"])
    elif biome == "taiga":
        tags.extend(["coniferous_trees", "cold"])

    if moisture > 0.7 and biome not in ("ocean", "lake", "coastal", "swamp"):
        tags.append("shallow_river")

    return tags


# ─── Terrain Generators ──────────────────────────────────────────────────────

def _seed_to_offsets(seed: int) -> tuple[float, float, float]:
    """Convert an integer seed to 3 noise offsets."""
    h = hashlib.sha256(struct.pack(">q", seed)).digest()
    o1 = struct.unpack(">d", h[0:8])[0] % 10000
    o2 = struct.unpack(">d", h[8:16])[0] % 10000
    o3 = struct.unpack(">d", h[16:24])[0] % 10000
    return o1, o2, o3


@dataclass
class TerrainData:
    """Generated terrain attributes for a single hex."""
    biome: str
    elevation: float
    moisture: float
    temperature: float
    terrain_tags: list[str]


@dataclass
class SpaceTerrain:
    """Generated terrain for a single Space hex."""
    terrain_type: str
    elevation: float
    moisture: float
    cover: Cover = Cover.NONE
    lighting: Lighting = Lighting.BRIGHT
    structure_type: str = ""   # e.g. "house", "fence", "well", "farmland"
    building_id: int = 0       # unique per building; 0 = not a building
    building_shape: str = ""   # "rect" or "hex"; empty = not a building


# ─── Culture Configuration ───────────────────────────────────────────────────
# Culture controls the *racial / societal identity* of a city: which structures
# appear, how buildings and walls are constructed, and district layout.
# Climate / terrain (ground material, road surface) is derived from the
# *biome* parameter separately so any culture can be combined with any climate
# — e.g. ``human`` + ``desert``, ``orc`` + ``mountain``.

@dataclass
class CultureConfig:
    """Racial / cultural identity for city generation.

    Controls building materials, wall types, decorations, district layout,
    and structural preferences.  Climate-dependent terrain (ground, roads)
    is resolved from the ``biome`` parameter via :data:`CITY_CLIMATE`.
    """
    name: str

    # ── Building fabric ──
    building_interior: str = "wood"         # terrain inside buildings
    building_wall_terrain: str = "stone"    # terrain on building perimeter

    # ── Fortification ──
    wall_material: str = "city_wall_lumber"  # structure type for city walls
    wall_terrain: str = "wood"               # terrain under walls
    wall_coverage: float = 0.55              # fraction of perimeter walled
    wall_inset: int = 8                      # hexes inset from area edge
    tower_radius: int = 2                    # watchtower hex-disk radius
    tower_interval_vertices: int = 10        # vertices spacing for towers
    wall_inset_noise: int = 3                # organic noise amplitude (hexes ±)

    # ── District layout ──
    district_weights: tuple[int, int, int] = (1, 2, 4)  # civic, market, residential

    # ── Decorations & amenities ──
    building_decorations: tuple[str, ...] = (
        "barrel", "crate", "box_stack", "cart", "hay_bale",
    )
    has_market_stalls: bool = True
    has_road_walls: bool = True
    has_street_lamps: bool = True

    # ── Building extent ──
    building_radius_frac: float = 0.72   # buildings up to this fraction of area radius


# Keep old name as alias for backward compat
CityStyle = CultureConfig


# ── Climate / terrain mapping ────────────────────────────────────────────────
# Provides ground and road terrain based on biome.  Culture is independent.
CITY_CLIMATE: dict[str, dict[str, str]] = {
    "plains":   {"ground_terrain": "gravel",      "road_terrain": "cobblestone"},
    "forest":   {"ground_terrain": "grass",        "road_terrain": "dirt"},
    "desert":   {"ground_terrain": "sand",         "road_terrain": "dirt"},
    "mountain": {"ground_terrain": "stone",        "road_terrain": "cobblestone"},
    "swamp":    {"ground_terrain": "mud",          "road_terrain": "gravel"},
    "coastal":  {"ground_terrain": "sand",         "road_terrain": "cobblestone"},
}


# ── Pre-defined racial / cultural identities ─────────────────────────────────
CULTURES: dict[str, CultureConfig] = {
    # ── Human: classic medieval — timber palisades, bustling markets ──
    "human": CultureConfig(
        name="human",
        building_interior="wood",
        building_wall_terrain="stone",
        wall_material="city_wall_lumber",
        wall_terrain="wood",
        district_weights=(1, 2, 4),
        building_decorations=("barrel", "crate", "box_stack", "cart", "hay_bale"),
        has_market_stalls=True,
        has_road_walls=True,
        has_street_lamps=True,
        wall_coverage=0.55,
        wall_inset=8,
        tower_radius=2,
        tower_interval_vertices=10,
        building_radius_frac=0.72,
        wall_inset_noise=3,
    ),
    # ── Elven: elegant, nature-integrated — stone arches, minimal clutter ──
    "elven": CultureConfig(
        name="elven",
        building_interior="wood",
        building_wall_terrain="stone",
        wall_material="city_wall_stone",
        wall_terrain="stone",
        district_weights=(2, 1, 4),
        building_decorations=("barrel", "crate"),
        has_market_stalls=False,
        has_road_walls=False,
        has_street_lamps=True,
        wall_coverage=0.35,
        wall_inset=9,
        tower_radius=2,
        tower_interval_vertices=8,
        building_radius_frac=0.68,
        wall_inset_noise=2,
    ),
    # ── Orc: militaristic, brutal — heavy stone, large towers, no lamps ──
    "orc": CultureConfig(
        name="orc",
        building_interior="stone",
        building_wall_terrain="stone",
        wall_material="city_wall_stone",
        wall_terrain="stone",
        district_weights=(3, 1, 3),
        building_decorations=("barrel", "crate", "box_stack", "cart"),
        has_market_stalls=True,
        has_road_walls=True,
        has_street_lamps=False,
        wall_coverage=0.70,
        wall_inset=7,
        tower_radius=3,
        tower_interval_vertices=8,
        building_radius_frac=0.75,
        wall_inset_noise=4,
    ),
    # ── Goblin: chaotic, scavenged — rickety lumber, cluttered, no walls ──
    "goblin": CultureConfig(
        name="goblin",
        building_interior="wood",
        building_wall_terrain="wood",
        wall_material="city_wall_lumber",
        wall_terrain="wood",
        district_weights=(1, 3, 3),
        building_decorations=(
            "barrel", "crate", "box_stack", "cart", "hay_bale",
            "barrel", "crate", "box_stack",  # extra junk
        ),
        has_market_stalls=True,
        has_road_walls=False,
        has_street_lamps=False,
        wall_coverage=0.40,
        wall_inset=9,
        tower_radius=1,
        tower_interval_vertices=14,
        building_radius_frac=0.68,
        wall_inset_noise=5,
    ),
    # ── Tiefling: dark, orderly, arcane — obsidian stone, disciplined layout ──
    "tiefling": CultureConfig(
        name="tiefling",
        building_interior="stone",
        building_wall_terrain="stone",
        wall_material="city_wall_stone",
        wall_terrain="stone",
        district_weights=(2, 2, 3),
        building_decorations=("barrel", "crate", "box_stack", "cart"),
        has_market_stalls=True,
        has_road_walls=True,
        has_street_lamps=True,
        wall_coverage=0.65,
        wall_inset=7,
        tower_radius=2,
        tower_interval_vertices=10,
        building_radius_frac=0.70,
        wall_inset_noise=2,
    ),
}

# Backward-compat alias
CITY_STYLES = CULTURES


_next_building_id: int = 1


# ─── Cover & Lighting Derivation ─────────────────────────────────────────────

# Terrain types that inherently provide cover
_TERRAIN_COVER: dict[str, Cover] = {
    "stone":  Cover.QUARTER,     # rocky outcrops, boulders
    "water":  Cover.NONE,
    "marsh":  Cover.NONE,
    "mud":    Cover.NONE,
    "sand":   Cover.NONE,
    "grass":  Cover.NONE,
    "dirt":   Cover.NONE,
    "snow":   Cover.NONE,
    "ice":    Cover.NONE,
    "lava":   Cover.NONE,
    "void":   Cover.NONE,
    "wood":   Cover.QUARTER,     # wooden floor planks, some concealment
    "cobblestone": Cover.NONE,  # flat paving
    "gravel": Cover.NONE,        # loose gravel with grass tufts
}

# Structure types and their cover
_STRUCTURE_COVER: dict[str, Cover] = {
    "house":       Cover.FULL,
    "house_wall":  Cover.THREE_QUARTER,
    "house_door":  Cover.HALF,
    "fence":       Cover.HALF,
    "well":        Cover.HALF,
    "cart":        Cover.HALF,
    "wagon":       Cover.HALF,
    "crate":       Cover.HALF,
    "hay_bale":    Cover.HALF,
    "barrel":      Cover.HALF,
    "box_stack":   Cover.THREE_QUARTER,
    "awning":      Cover.NONE,
    "farmland":    Cover.NONE,
    "road":        Cover.NONE,
    "market_stall": Cover.HALF,
    "road_wall":    Cover.HALF,
    "street_lamp":  Cover.NONE,
    "city_wall_stone":  Cover.THREE_QUARTER,
    "city_wall_lumber": Cover.THREE_QUARTER,
    "watchtower":       Cover.FULL,
    "tree":             Cover.HALF,
    "bed":              Cover.HALF,
    "table":            Cover.HALF,
    "chair":            Cover.QUARTER,
    "desk":             Cover.HALF,
    "rug":              Cover.NONE,
    "bookshelf":        Cover.THREE_QUARTER,
    "chest":            Cover.HALF,
}

# Structure types and their lighting effects (interiors are dim/dark)
_STRUCTURE_LIGHTING: dict[str, Lighting] = {
    "house":      Lighting.DIM,
    "house_wall": Lighting.BRIGHT,
    "house_door": Lighting.BRIGHT,
    "city_wall_stone":  Lighting.BRIGHT,
    "city_wall_lumber": Lighting.BRIGHT,
    "watchtower":       Lighting.BRIGHT,
    "bed":        Lighting.DIM,
    "table":      Lighting.DIM,
    "chair":      Lighting.DIM,
    "desk":       Lighting.DIM,
    "rug":        Lighting.DIM,
    "bookshelf":  Lighting.DIM,
    "chest":      Lighting.DIM,
}

# Terrain types that create dim lighting when dense
_DIM_TERRAIN = {"marsh", "mud"}


def derive_cover(
    terrain_type: str, structure_type: str, elevation: float,
) -> Cover:
    """Determine cover level from terrain + structure."""
    # Structure cover overrides terrain cover
    if structure_type:
        return _STRUCTURE_COVER.get(structure_type, Cover.NONE)
    base = _TERRAIN_COVER.get(terrain_type, Cover.NONE)
    # High elevation stone = better cover (large boulders)
    if terrain_type == "stone" and elevation > 0.7:
        return Cover.HALF
    return base


def derive_lighting(
    terrain_type: str, structure_type: str, moisture: float,
) -> Lighting:
    """Determine lighting level from terrain + structure."""
    if structure_type:
        return _STRUCTURE_LIGHTING.get(structure_type, Lighting.BRIGHT)
    if terrain_type in _DIM_TERRAIN and moisture > 0.6:
        return Lighting.DIM  # thick fog in swamps
    return Lighting.BRIGHT


def _pick_space_terrain(biome: str, elevation: float, moisture: float) -> str:
    """Select ground terrain type based on biome and local conditions.

    Thresholds calibrated for normalized [0,1] noise with distribution
    ~p25=0.36, p50=0.51, p75=0.64. Uses condition ordering so the
    first matching rule wins.
    """
    if biome == "ocean":
        return "water"
    if biome == "lake":
        return "sand" if elevation > 0.6 else "water"
    if biome == "forest":
        if moisture > 0.58 and elevation < 0.38:
            return "water"
        if elevation > 0.72:
            return "stone"
        if moisture < 0.42:
            return "dirt"
        return "grass"
    if biome == "desert":
        if elevation < 0.15 and moisture > 0.72:
            return "water"
        if elevation > 0.72:
            return "stone"
        if moisture > 0.55:
            return "dirt"
        return "sand"
    if biome == "mountain":
        if elevation > 0.7:
            return "snow"
        if elevation > 0.35:
            return "stone"
        if moisture > 0.55:
            return "grass"
        return "dirt"
    if biome == "swamp":
        if moisture > 0.55:
            return "water"
        if moisture > 0.38:
            return "marsh"
        if elevation > 0.5:
            return "grass"
        return "mud"
    if biome == "plains":
        if moisture > 0.68 and elevation < 0.28:
            return "water"
        if moisture < 0.35:
            return "dirt"
        if elevation > 0.75:
            return "stone"
        return "grass"
    if biome == "coastal":
        if elevation < 0.38:
            return "water"
        if elevation < 0.55:
            return "sand"
        return "grass" if moisture > 0.5 else "sand"
    if biome == "tundra":
        if elevation > 0.65:
            return "ice"
        if moisture > 0.42:
            return "snow"
        return "stone" if elevation > 0.35 else "dirt"
    if biome == "taiga":
        if elevation > 0.68:
            return "snow"
        return "grass" if moisture > 0.48 else "dirt"
    if biome == "hills":
        if elevation > 0.65:
            return "stone"
        return "grass" if moisture > 0.42 else "dirt"
    if biome == "jungle":
        if moisture > 0.6 and elevation < 0.35:
            return "water"
        if elevation > 0.72:
            return "stone"
        return "grass" if moisture > 0.35 else "mud"
    if biome == "savanna":
        if moisture > 0.68 and elevation < 0.25:
            return "water"
        if moisture < 0.40:
            return "sand"
        return "grass"
    if biome == "ice":
        return "ice" if elevation > 0.48 else "snow"
    if biome == "volcanic":
        if elevation > 0.72:
            return "lava"
        if elevation > 0.38:
            return "stone"
        return "dirt"
    if biome == "urban":
        return "stone" if elevation > 0.45 else "wood"
    if biome == "dungeon":
        if elevation < 0.2:
            return "water"
        if elevation > 0.8:
            return "void"
        return "stone"
    # Fallback
    if moisture > 0.6 and elevation < 0.3:
        return "water"
    if elevation > 0.65:
        return "stone"
    return "grass"


def generate_region_terrain(
    region_q: int,
    region_r: int,
    world_seed: int,
    radius: int = 3,
) -> dict[Hex, TerrainData]:
    """
    Generate terrain for all Encounter Areas in a Region.

    Returns a dict mapping hex position to terrain data.
    Region has `radius` rings of hexes (radius=3 → 37 hexes).
    """
    center = Hex(0, 0)
    hexes = center.disk(radius)

    # Derive noise offsets from world seed
    off_e, off_m, off_t = _seed_to_offsets(world_seed)

    # Scale: how spread out the noise is. Larger = smoother terrain.
    scale = 0.08

    result = {}
    for h in hexes:
        # Convert hex to a world position using region offset
        # Each region is offset so adjacent regions produce continuous noise
        world_q = region_q * (radius * 2 + 1) + h.q
        world_r = region_r * (radius * 2 + 1) + h.r

        # Sample Perlin noise (returns -1 to 1, normalize to 0–1)
        elev = (noise.pnoise2(
            world_q * scale + off_e,
            world_r * scale + off_e,
            octaves=4, persistence=0.5, lacunarity=2.0,
        ) + 1) / 2
        elev = max(0.0, min(1.0, elev ** 1.2))  # power redistribution for valleys

        moist = (noise.pnoise2(
            world_q * scale + off_m,
            world_r * scale + off_m,
            octaves=3, persistence=0.5, lacunarity=2.0,
        ) + 1) / 2

        # Temperature: base from latitude (region_r as proxy) + noise
        lat_factor = max(0.0, min(1.0, 0.5 - region_r * 0.05))
        temp_noise = (noise.pnoise2(
            world_q * scale * 0.5 + off_t,
            world_r * scale * 0.5 + off_t,
            octaves=2, persistence=0.5, lacunarity=2.0,
        ) + 1) / 2
        temp = max(0.0, min(1.0, lat_factor * 0.7 + temp_noise * 0.3))

        biome = _classify_biome(elev, moist, temp)
        tags = _derive_terrain_tags(biome, elev, moist)

        result[h] = TerrainData(
            biome=biome,
            elevation=round(elev, 3),
            moisture=round(moist, 3),
            temperature=round(temp, 3),
            terrain_tags=tags,
        )

    return result


def generate_space_terrain(
    terrain_seed: int,
    biome: str,
    radius: int = 60,
    shared_edges: dict[str, list["SpaceTerrain"]] | None = None,
    overlays: list[tuple["TerrainOverlay", Hex, int]] | None = None,
) -> dict[Hex, SpaceTerrain]:
    """
    Generate Space-level terrain for an Encounter Area.

    Uses separate elevation and moisture noise channels with per-octave
    decorrelation. Normalizes values to [0, 1] using the actual min/max
    within the encounter area, ensuring the full range of terrain variation
    is represented regardless of noise library output range.

    If *shared_edges* is provided, stamps the neighbour's outermost
    hex row directly onto the matching edge for pixel-perfect seams.

    If *overlays* is provided, each ``(overlay, center, rotation)`` tuple
    is stamped onto the grid after terrain generation — DM narrative
    inserts like rivers, ponds, or shrines.
    """
    center = Hex(0, 0)
    hexes = center.disk(radius)

    off_e, off_m, _ = _seed_to_offsets(terrain_seed)
    scale = 0.12

    # Pass 1: compute raw noise values
    raw: dict[Hex, tuple[float, float]] = {}
    for h in hexes:
        e_raw = 0.0
        e_amp = 0.0
        for octave, (freq, amp) in enumerate([
            (1.0, 1.0), (2.0, 0.5), (4.0, 0.25), (8.0, 0.125),
        ]):
            e_raw += amp * noise.pnoise2(
                h.q * scale * freq + off_e + octave * 31.7,
                h.r * scale * freq + off_e + octave * 47.3 + 50,
                octaves=1,
            )
            e_amp += amp

        m_raw = 0.0
        m_amp = 0.0
        for octave, (freq, amp) in enumerate([
            (1.0, 1.0), (2.0, 0.75), (4.0, 0.33),
        ]):
            m_raw += amp * noise.pnoise2(
                h.q * scale * freq + off_m + octave * 23.1,
                h.r * scale * freq + off_m + octave * 67.9 + 50,
                octaves=1,
            )
            m_amp += amp

        raw[h] = (e_raw / e_amp, m_raw / m_amp)

    # Pass 2: normalize to [0, 1] using actual min/max for full range coverage
    e_vals = [v[0] for v in raw.values()]
    m_vals = [v[1] for v in raw.values()]
    e_min, e_max = min(e_vals), max(e_vals)
    m_min, m_max = min(m_vals), max(m_vals)
    e_range = e_max - e_min if e_max > e_min else 1.0
    m_range = m_max - m_min if m_max > m_min else 1.0

    result = {}
    for h in hexes:
        e_norm = (raw[h][0] - e_min) / e_range
        m_norm = (raw[h][1] - m_min) / m_range

        elevation = max(0.0, min(1.0, e_norm ** 1.2))
        moisture = max(0.0, min(1.0, m_norm))

        terrain_type = _pick_space_terrain(biome, elevation, moisture)
        cover = derive_cover(terrain_type, "", elevation)
        lighting = derive_lighting(terrain_type, "", moisture)

        result[h] = SpaceTerrain(
            terrain_type=terrain_type,
            elevation=round(elevation, 3),
            moisture=round(moisture, 3),
            cover=cover,
            lighting=lighting,
        )

    # Apply shared edges from neighbours (if any)
    if shared_edges:
        _apply_shared_edges(result, radius, shared_edges, biome)

    # Apply DM narrative overlays
    if overlays:
        for ovl, ovl_center, ovl_rot in overlays:
            apply_overlay(result, ovl, ovl_center, ovl_rot)

    return result


# ─── Edge Consistency ─────────────────────────────────────────────────────────

OPPOSITE_DIR = {
    "E": "W", "W": "E",
    "NE": "SW", "SW": "NE",
    "NW": "SE", "SE": "NW",
}


@dataclass
class EdgeProfile:
    """Terrain profile along one edge of an encounter area.

    Captures the terrain distribution, average elevation, and average
    moisture in the outermost rows of hexes facing a given direction.
    A neighbour can use this to constrain its own edge so the two areas
    feel continuous.
    """
    terrain_distribution: dict[str, float]   # terrain_type → fraction [0‑1]
    avg_elevation: float
    avg_moisture: float


def _get_edge_rows(
    radius: int, direction: str, depth: int = 3,
) -> list[list[Hex]]:
    """Return edge hex rows ordered outermost→inward.

    Row 0 is the boundary itself; row *depth‑1* is furthest inward.
    Each row is a list of ``Hex`` objects at the same projection level.

    The projection is the dot‑product of the hex position with the
    direction vector.  For axial directions (E/W/NW/SE) each row has
    *R+1* hexes.  For diagonal directions (NE/SW) rows are thinner
    (geometrically correct – those edges share a corner, not a face).
    """
    dq, dr = DIRECTIONS[direction]
    disk = Hex(0, 0).disk(radius)

    def proj(h: Hex) -> int:
        return h.q * dq + h.r * dr

    max_p = max(proj(h) for h in disk)

    rows: list[list[Hex]] = []
    for d in range(depth):
        target = max_p - d
        row = [h for h in disk if proj(h) == target]
        if row:
            rows.append(row)
    return rows


def extract_edge_profiles(
    terrain: dict[Hex, SpaceTerrain],
    radius: int,
) -> dict[str, EdgeProfile]:
    """Extract terrain profiles for all 6 edges of an encounter area.

    Returns ``{direction: EdgeProfile}`` using the outermost 2 rows
    of hexes on each edge.
    """
    profiles: dict[str, EdgeProfile] = {}
    for direction in DIRECTION_LIST:
        rows = _get_edge_rows(radius, direction, depth=2)
        edge_hexes = [h for row in rows for h in row if h in terrain]
        if not edge_hexes:
            continue

        counts: dict[str, int] = {}
        total_e = 0.0
        total_m = 0.0
        for h in edge_hexes:
            st = terrain[h]
            counts[st.terrain_type] = counts.get(st.terrain_type, 0) + 1
            total_e += st.elevation
            total_m += st.moisture

        n = len(edge_hexes)
        dist = {k: v / n for k, v in counts.items()}
        profiles[direction] = EdgeProfile(
            terrain_distribution=dist,
            avg_elevation=total_e / n,
            avg_moisture=total_m / n,
        )
    return profiles


def extract_shared_edges(
    terrain: dict[Hex, SpaceTerrain],
    radius: int,
) -> dict[str, list[SpaceTerrain]]:
    """Extract the outermost row of actual hex data for all 6 edges.

    Returns ``{direction: [SpaceTerrain, ...]}`` where each list is
    ordered by y-coordinate (top to bottom) so that neighbouring areas
    can directly stamp matching terrain onto their opposite edge.
    """
    edges: dict[str, list[SpaceTerrain]] = {}
    for direction in DIRECTION_LIST:
        rows = _get_edge_rows(radius, direction, depth=1)
        if not rows:
            continue
        row0 = [h for h in rows[0] if h in terrain]
        row0.sort(key=lambda h: hex_to_pixel(h, 1.0)[1])
        edges[direction] = [terrain[h] for h in row0]
    return edges


def _apply_shared_edges(
    terrain: dict[Hex, SpaceTerrain],
    radius: int,
    shared: dict[str, list[SpaceTerrain]],
    biome: str,
) -> None:
    """Stamp shared edge data onto Row 0; blend Rows 1-2.

    Row 0 is an exact copy of the neighbour's outermost row — same
    terrain type, elevation, and moisture.  Rows 1-2 are nudged toward
    the shared edge's averages for a smooth transition.
    """
    for direction, src_data in shared.items():
        rows = _get_edge_rows(radius, direction, depth=3)
        if not rows:
            continue

        # --- Row 0: direct copy from neighbour ---
        row0 = [h for h in rows[0] if h in terrain]
        row0.sort(key=lambda h: hex_to_pixel(h, 1.0)[1])

        for h, src in zip(row0, src_data):
            terrain[h] = SpaceTerrain(
                terrain_type=src.terrain_type,
                elevation=src.elevation,
                moisture=src.moisture,
                cover=derive_cover(src.terrain_type, "", src.elevation),
                lighting=derive_lighting(src.terrain_type, "", src.moisture),
            )

        # --- Rows 1-2: nudge toward shared edge averages ---
        if not src_data:
            continue
        target_e = sum(s.elevation for s in src_data) / len(src_data)
        target_m = sum(s.moisture for s in src_data) / len(src_data)

        outer = [h for row in rows[:2] for h in row if h in terrain]
        if not outer:
            continue
        cur_e = sum(terrain[h].elevation for h in outer) / len(outer)
        cur_m = sum(terrain[h].moisture for h in outer) / len(outer)
        de = target_e - cur_e
        dm = target_m - cur_m

        blend_weights = [0.65, 0.30]
        for row_idx in range(1, min(len(rows), 3)):
            w = blend_weights[row_idx - 1]
            for h in rows[row_idx]:
                if h not in terrain:
                    continue
                st = terrain[h]
                new_e = max(0.0, min(1.0, st.elevation + de * w))
                new_m = max(0.0, min(1.0, st.moisture + dm * w))
                new_type = _pick_space_terrain(biome, new_e, new_m)
                terrain[h] = SpaceTerrain(
                    terrain_type=new_type,
                    elevation=round(new_e, 3),
                    moisture=round(new_m, 3),
                    cover=derive_cover(new_type, st.structure_type, new_e),
                    lighting=derive_lighting(
                        new_type, st.structure_type, new_m,
                    ),
                    structure_type=st.structure_type,
                )


def _apply_edge_constraints(
    terrain: dict[Hex, SpaceTerrain],
    radius: int,
    constraints: dict[str, EdgeProfile],
    biome: str,
) -> None:
    """Adjust edge terrain to match the neighbour's profile.

    Row 0 (boundary): directly override terrain types to match the
    constraint's distribution, preserving spatial coherence by sorting
    hexes by elevation and assigning types accordingly.

    Rows 1‑2: nudge elevation/moisture toward the target averages so
    terrain transitions smoothly.
    """
    for direction, profile in constraints.items():
        rows = _get_edge_rows(radius, direction, depth=3)
        if not rows:
            continue

        # --- Row 0: direct terrain‑type override ---
        row0 = [h for h in rows[0] if h in terrain]
        if row0:
            # Build target type list proportional to the distribution
            n = len(row0)
            type_list: list[str] = []
            for ttype, frac in sorted(
                profile.terrain_distribution.items(), key=lambda x: -x[1],
            ):
                count = max(1, round(frac * n)) if frac > 0.05 else 0
                type_list.extend([ttype] * count)
            # Trim or pad to exactly n items
            while len(type_list) < n:
                top_type = max(profile.terrain_distribution,
                               key=profile.terrain_distribution.get)
                type_list.append(top_type)
            type_list = type_list[:n]

            # Sort hexes high→low elevation; sort types so "high terrain"
            # types go on high‑elevation hexes (natural spatial coherence).
            _TERRAIN_ELEV_ORDER = {
                "snow": 6, "ice": 5, "stone": 4, "sand": 3,
                "dirt": 2, "grass": 1, "mud": 0, "marsh": 0,
                "water": -1, "wood": 3, "lava": 5, "void": 0,
            }
            row0.sort(key=lambda h: terrain[h].elevation, reverse=True)
            type_list.sort(
                key=lambda t: _TERRAIN_ELEV_ORDER.get(t, 1), reverse=True,
            )

            for h, ttype in zip(row0, type_list):
                st = terrain[h]
                terrain[h] = SpaceTerrain(
                    terrain_type=ttype,
                    elevation=round(st.elevation, 3),
                    moisture=round(st.moisture, 3),
                    cover=derive_cover(ttype, st.structure_type, st.elevation),
                    lighting=derive_lighting(
                        ttype, st.structure_type, st.moisture,
                    ),
                    structure_type=st.structure_type,
                )

        # --- Rows 1‑2: elevation/moisture nudge for smooth transition ---
        outer = [h for row in rows[:2] for h in row if h in terrain]
        if not outer:
            continue
        cur_e = sum(terrain[h].elevation for h in outer) / len(outer)
        cur_m = sum(terrain[h].moisture for h in outer) / len(outer)
        de = profile.avg_elevation - cur_e
        dm = profile.avg_moisture - cur_m

        blend_weights = [0.65, 0.30]
        for row_idx in range(1, min(len(rows), 3)):
            w = blend_weights[row_idx - 1]
            for h in rows[row_idx]:
                if h not in terrain:
                    continue
                st = terrain[h]
                new_e = max(0.0, min(1.0, st.elevation + de * w))
                new_m = max(0.0, min(1.0, st.moisture + dm * w))
                new_type = _pick_space_terrain(biome, new_e, new_m)
                terrain[h] = SpaceTerrain(
                    terrain_type=new_type,
                    elevation=round(new_e, 3),
                    moisture=round(new_m, 3),
                    cover=derive_cover(new_type, st.structure_type, new_e),
                    lighting=derive_lighting(
                        new_type, st.structure_type, new_m,
                    ),
                    structure_type=st.structure_type,
                )


# ─── City / Connected‑Area Generator ─────────────────────────────────────────


def _area_to_world(ah: Hex, h: Hex, R: int) -> Hex:
    """Map local encounter hex *h* in area *ah* to world-space hex.

    Hex disks of radius R tile on the hex lattice with basis vectors
    ``E → (2R, −R)`` and ``SE → (R, R)`` in axial coordinates.
    """
    return Hex(
        R * (2 * ah.q + ah.r) + h.q,
        R * (ah.r - ah.q) + h.r,
    )


def _world_to_local(ah: Hex, wh: Hex, R: int) -> Hex:
    """Inverse of :func:`_area_to_world`."""
    return Hex(
        wh.q - R * (2 * ah.q + ah.r),
        wh.r - R * (ah.r - ah.q),
    )


def generate_city(
    city_seed: int,
    num_areas: int = 5,
    radius: int = 60,
    biome: str = "plains",
    overlays: list[tuple] | None = None,
    culture: str | CultureConfig = "human",
    style: str | CultureConfig | None = None,
) -> tuple[list[Hex], dict[Hex, dict[Hex, SpaceTerrain]]]:
    """Generate a city as a cluster of connected encounter areas.

    Uses continuous world-space noise with *global* normalisation so
    elevation and moisture are seamless across area boundaries.
    After terrain generation, reconciles edges between adjacent areas
    and overlays urban structures (buildings, streets, plazas).

    Parameters
    ----------
    overlays
        Optional list of ``(area_hex, overlay, center, rotation)`` tuples.
        Each overlay is stamped onto the specified area *after* city
        structures are placed — so the DM narrative takes priority.
        ``area_hex`` is the region-level ``Hex`` identifying the encounter
        area (one of the returned ``city_hexes``), or ``None`` to target
        area index 0 (the city centre).
    culture
        Racial/cultural identity name (key in ``CULTURES``) or a
        ``CultureConfig`` instance.  Defaults to ``"human"``.
    style
        **Deprecated** — use *culture* instead.  Accepted for backward
        compatibility; if given, overrides *culture*.

    Returns
    -------
    city_hexes : list[Hex]
        Region-level hex positions of the city areas (ordered).
    areas : dict[Hex, dict[Hex, SpaceTerrain]]
        Mapping from region hex → {space hex: SpaceTerrain}.
    """
    import random as _rng_mod

    # Resolve culture (with backward-compat for style=)
    _culture_raw = style if style is not None else culture
    if isinstance(_culture_raw, str):
        city_culture = CULTURES.get(_culture_raw, CULTURES["human"])
    else:
        city_culture = _culture_raw

    # Resolve climate from biome
    _climate = CITY_CLIMATE.get(biome, CITY_CLIMATE["plains"])
    rng = _rng_mod.Random(city_seed)
    center = Hex(0, 0)

    # --- pick a connected cluster of hexes for the city -----------------------
    city_hexes: list[Hex] = [center]
    candidates: list[Hex] = list(center.neighbors())
    rng.shuffle(candidates)
    while len(city_hexes) < num_areas and candidates:
        pick = candidates.pop(0)
        if pick in city_hexes:
            continue
        if not any(pick.distance(ch) == 1 for ch in city_hexes):
            continue
        city_hexes.append(pick)
        for nb in pick.neighbors():
            if nb not in city_hexes and nb not in candidates:
                candidates.append(nb)
        rng.shuffle(candidates)

    # --- continuous world‑space noise -----------------------------------------
    off_e, off_m, _ = _seed_to_offsets(city_seed)
    scale = 0.12
    space_center = Hex(0, 0)

    # Pass 1 — raw noise
    all_raw: dict[Hex, dict[Hex, tuple[float, float]]] = {}
    for area_hex in city_hexes:
        raw: dict[Hex, tuple[float, float]] = {}
        for h in space_center.disk(radius):
            wh = _area_to_world(area_hex, h, radius)
            wq, wr = wh.q, wh.r

            e_raw = 0.0
            e_amp = 0.0
            for octave, (freq, amp) in enumerate([
                (1.0, 1.0), (2.0, 0.5), (4.0, 0.25), (8.0, 0.125),
            ]):
                e_raw += amp * noise.pnoise2(
                    wq * scale * freq + off_e + octave * 31.7,
                    wr * scale * freq + off_e + octave * 47.3 + 50,
                    octaves=1,
                )
                e_amp += amp

            m_raw = 0.0
            m_amp = 0.0
            for octave, (freq, amp) in enumerate([
                (1.0, 1.0), (2.0, 0.75), (4.0, 0.33),
            ]):
                m_raw += amp * noise.pnoise2(
                    wq * scale * freq + off_m + octave * 23.1,
                    wr * scale * freq + off_m + octave * 67.9 + 50,
                    octaves=1,
                )
                m_amp += amp

            raw[h] = (e_raw / e_amp, m_raw / m_amp)
        all_raw[area_hex] = raw

    # Pass 2 — global normalisation across ALL areas
    all_e = [v[0] for area_raw in all_raw.values() for v in area_raw.values()]
    all_m = [v[1] for area_raw in all_raw.values() for v in area_raw.values()]
    e_min, e_max = min(all_e), max(all_e)
    m_min, m_max = min(all_m), max(all_m)
    e_range = (e_max - e_min) or 1.0
    m_range = (m_max - m_min) or 1.0

    # Pass 3 — classify terrain
    areas: dict[Hex, dict[Hex, SpaceTerrain]] = {}
    for area_hex in city_hexes:
        area_terrain: dict[Hex, SpaceTerrain] = {}
        for h in space_center.disk(radius):
            e_raw, m_raw = all_raw[area_hex][h]
            e_norm = (e_raw - e_min) / e_range
            m_norm = (m_raw - m_min) / m_range

            elevation = max(0.0, min(1.0, e_norm ** 1.2))
            moisture = max(0.0, min(1.0, m_norm))

            terrain_type = _pick_space_terrain(biome, elevation, moisture)
            cover = derive_cover(terrain_type, "", elevation)
            lighting = derive_lighting(terrain_type, "", moisture)

            area_terrain[h] = SpaceTerrain(
                terrain_type=terrain_type,
                elevation=round(elevation, 3),
                moisture=round(moisture, 3),
                cover=cover,
                lighting=lighting,
            )
        areas[area_hex] = area_terrain

    # --- Pass 4: reconcile edges between adjacent areas -----------------------
    # For every adjacent pair, extract edge profiles from the first area and
    # apply them as constraints to the second's facing edge (and vice versa).
    city_set = set(city_hexes)
    reconciled: set[tuple[int, int, int, int]] = set()
    for ah in city_hexes:
        for dname in DIRECTION_LIST:
            dq, dr = DIRECTIONS[dname]
            bh = Hex(ah.q + dq, ah.r + dr)
            if bh not in city_set:
                continue
            pair_key = (min(ah.q, bh.q), min(ah.r, bh.r),
                        max(ah.q, bh.q), max(ah.r, bh.r))
            if pair_key in reconciled:
                continue
            reconciled.add(pair_key)

            opp = OPPOSITE_DIR[dname]
            prof_a = extract_edge_profiles(areas[ah], radius).get(dname)
            prof_b = extract_edge_profiles(areas[bh], radius).get(opp)
            if prof_a and prof_b:
                # Blend: average the two profiles for mutual consistency
                all_types = set(prof_a.terrain_distribution) | set(prof_b.terrain_distribution)
                blended_dist = {
                    t: (prof_a.terrain_distribution.get(t, 0)
                         + prof_b.terrain_distribution.get(t, 0)) / 2
                    for t in all_types
                }
                avg_e = (prof_a.avg_elevation + prof_b.avg_elevation) / 2
                avg_m = (prof_a.avg_moisture + prof_b.avg_moisture) / 2
                shared = EdgeProfile(blended_dist, avg_e, avg_m)

                _apply_edge_constraints(areas[ah], radius, {dname: shared}, biome)
                _apply_edge_constraints(areas[bh], radius, {opp: shared}, biome)

    # --- Pass 5: apply DM narrative overlays (before structures) ----------------
    # Stamp overlays onto raw terrain so that building placement, road routing,
    # and wall placement all treat overlay hexes as obstacles to build around.
    # Track which hexes were placed by overlays so only those are preserved.
    overlay_hexes: dict[Hex, set[Hex]] = {ah: set() for ah in city_hexes}
    if overlays:
        for entry in overlays:
            area_hex, ovl, ovl_center, ovl_rot = entry
            if area_hex is None:
                area_hex = city_hexes[0]
            if area_hex in areas:
                stamped = apply_overlay(areas[area_hex], ovl, ovl_center, ovl_rot)
                overlay_hexes[area_hex].update(stamped)

    # --- Pass 5½: pre-compute wall boundary (before buildings) ----------------
    # Compute the noisy wall ring and interior set so that building
    # placement in Pass 6 can be constrained to stay inside the walls.
    wall_rng = rng.__class__(city_seed + 99999)
    wall_precomputed = _compute_wall_boundary(
        areas, city_hexes, radius, city_culture, wall_rng,
    )
    _wall_interior = wall_precomputed[1]  # set of world hexes inside walls

    # --- Pass 6: overlay city structures --------------------------------------
    _overlay_city_structures(areas, city_hexes, radius, city_seed, rng,
                             overlay_hexes, city_culture,
                             wall_interior=_wall_interior,
                             climate=_climate)

    # --- Pass 7: city walls as overlay (applied AFTER structures) -------------
    # Uses the pre-computed boundary so walls match the constraint used
    # for buildings — same noisy inset, same interior.
    wall_rng2 = rng.__class__(city_seed + 99999)
    _place_city_walls(
        areas, city_hexes, radius, city_culture, wall_rng2,
        precomputed=wall_precomputed,
        climate=_climate,
    )

    return city_hexes, areas


def _overlay_city_structures(
    areas: dict[Hex, dict[Hex, SpaceTerrain]],
    city_hexes: list[Hex],
    radius: int,
    city_seed: int,
    rng,
    overlay_hexes: dict[Hex, set[Hex]] | None = None,
    style: CultureConfig | None = None,
    wall_interior: set[Hex] | None = None,
    climate: dict[str, str] | None = None,
) -> None:
    """Overlay urban structures onto city terrain areas.

    Produces a city layout controlled by *style* (culture) and *climate*:
      - Climate-driven ground / road terrain
      - Culture-driven buildings, decorations, district layout
      - Many tightly-packed geometric buildings in clusters
      - Organic road network with junction-based routing
      - District variation (civic / market / residential)

    City walls are placed separately in ``generate_city`` (Pass 7) so
    they overwrite structures rather than being hidden underneath.
    """
    if style is None:
        style = CULTURES["human"]
    if climate is None:
        climate = CITY_CLIMATE["plains"]

    center = Hex(0, 0)
    city_set = set(city_hexes)

    # Terrain types placed by DM overlays that must not be overwritten
    _overlay_terrain = {"water", "mud", "lava"}

    # Assign district types from style weights
    _dw = style.district_weights  # (civic, market, residential)
    _district_pool: list[str] = (
        ["civic"] * _dw[0]
        + ["market"] * _dw[1]
        + ["residential"] * _dw[2]
    )
    rng.shuffle(_district_pool)
    # Ensure first area is civic
    district_types = _district_pool[:max(len(city_hexes), len(_district_pool))]
    if "civic" in district_types:
        district_types.remove("civic")
    district_types.insert(0, "civic")

    # Precompute the maximum world-space pixel distance from the
    # global city centre.  Used for building density falloff.
    _max_world_r = 0.0
    for ch in city_hexes:
        for bh in Hex(0, 0).ring(radius):
            wh = _area_to_world(ch, bh, radius)
            px, py = hex_to_pixel(wh, 1.0)
            d = math.sqrt(px * px + py * py)
            if d > _max_world_r:
                _max_world_r = d

    for area_idx, area_hex in enumerate(city_hexes):
        grid = areas[area_hex]
        district = district_types[area_idx % len(district_types)]
        area_rng = rng.__class__(city_seed + area_idx * 1000 + 7)

        # --- Ground base: style-driven terrain; roads will use road_terrain ---
        _area_overlay = overlay_hexes.get(area_hex, set()) if overlay_hexes else set()
        for h in list(grid.keys()):
            st = grid[h]
            if h in _area_overlay:
                continue

            # Sprinkle grass, dirt, and tree patches — denser near edges
            wh = _area_to_world(area_hex, h, radius)
            _wpx, _wpy = hex_to_pixel(wh, 1.0)
            _wdist = math.sqrt(_wpx * _wpx + _wpy * _wpy)
            _wfrac = _wdist / max(1.0, _max_world_r) if _max_world_r > 0 else 0.5
            # Probability of a natural patch: ~0% at centre → ~25% at edge
            _nat_prob = max(0.0, (_wfrac - 0.3) * 0.36)
            _hv = ((h.q + 500) * 73856093 + (h.r + 500) * 19349669
                   + city_seed * 83492791) & 0xFFFFFFFF
            _nat_roll = (_hv & 0xFFFF) / 0xFFFF

            if _nat_roll < _nat_prob:
                # Pick terrain type: 50% grass, 30% dirt, 20% grass+tree
                _kind = (_hv >> 16) % 10
                if _kind < 5:
                    _t = "grass"
                    _cover = Cover.NONE
                    _stype = ""
                elif _kind < 8:
                    _t = "dirt"
                    _cover = Cover.NONE
                    _stype = ""
                else:
                    _t = "grass"
                    _cover = Cover.HALF
                    _stype = "tree"
                grid[h] = SpaceTerrain(
                    terrain_type=_t,
                    elevation=st.elevation,
                    moisture=st.moisture,
                    cover=_cover,
                    lighting=Lighting.DIM if _stype == "tree" else derive_lighting(_t, "", st.moisture),
                    structure_type=_stype,
                )
            else:
                grid[h] = SpaceTerrain(
                    terrain_type=climate["ground_terrain"],
                    elevation=st.elevation,
                    moisture=st.moisture,
                    cover=Cover.NONE,
                    lighting=derive_lighting("stone", "", st.moisture),
                )

        # --- Place buildings densely ---
        # Pass world-space context so buildings thin out from the
        # global city centre outward (smooth radial gradient).
        _build_r = int(radius * style.building_radius_frac)
        building_centers = _place_city_buildings(
            grid, center, radius, area_rng, district,
            area_hex, _max_world_r, _build_r,
            wall_interior=wall_interior,
            interior_terrain=style.building_interior,
            wall_terrain=style.building_wall_terrain,
        )

        # --- Furnish building interiors ---
        _furnish_buildings(grid, area_rng)

        # --- Road network: organic grid (not center-originating) ---
        # Step 1: Pick entry points — mandatory at shared boundaries,
        # optional random at outer edges.
        city_set_check = set(city_hexes)
        shared_dirs: list[str] = []
        outer_dirs: list[str] = []
        for d in DIRECTION_LIST:
            dq, dr = DIRECTIONS[d]
            nb_ah = Hex(area_hex.q + dq, area_hex.r + dr)
            if nb_ah in city_set_check:
                shared_dirs.append(d)
            else:
                outer_dirs.append(d)

        entry_points: list[Hex] = []
        # Always create entry points on shared boundaries (cross-area roads)
        for d in shared_dirs:
            dq, dr = DIRECTIONS[d]
            ep = Hex(center.q + dq * (radius - 2),
                     center.r + dr * (radius - 2))
            if ep in grid:
                entry_points.append(ep)

        # Add 1-2 random external entries if available
        num_outer = min(area_rng.randint(1, 2), len(outer_dirs))
        if outer_dirs and num_outer > 0:
            for d in area_rng.sample(outer_dirs, num_outer):
                dq, dr = DIRECTIONS[d]
                ep = Hex(center.q + dq * (radius - 2),
                         center.r + dr * (radius - 2))
                if ep in grid:
                    entry_points.append(ep)

        # Also add entry points toward bridges (road hexes on overlays)
        if _area_overlay:
            for h in _area_overlay:
                if h in grid and grid[h].structure_type == "road":
                    # This is a bridge hex — add nearby non-water hex as entry
                    for nb in h.neighbors():
                        if (nb in grid
                                and grid[nb].terrain_type not in _overlay_terrain
                                and nb not in _area_overlay):
                            entry_points.append(nb)
                            break

        # Step 2: Pick 2-4 junction points spread across the area interior
        half_r = radius // 2
        junction_candidates = [
            h for h in center.disk(half_r)
            if h in grid
            and h.distance(center) >= 8
            and grid[h].terrain_type != "water"
            and grid[h].structure_type not in (
                "house", "house_wall", "house_door")
        ]
        area_rng.shuffle(junction_candidates)
        junctions: list[Hex] = []
        min_jdist = max(12, radius // 4)
        target_junctions = area_rng.randint(2, 4)
        for cand in junction_candidates:
            if all(cand.distance(j) >= min_jdist for j in junctions):
                junctions.append(cand)
            if len(junctions) >= target_junctions:
                break
        if not junctions:
            junctions.append(center)

        # Step 3: Build main arteries (3-hex wide)
        _road_t = climate["road_terrain"]
        all_road_hexes: list[Hex] = []
        # Connect each entry to nearest junction
        for ep in entry_points:
            nearest_j = min(junctions, key=lambda j: ep.distance(j))
            stamped = _place_road(grid, ep, nearest_j,
                                  road_terrain=_road_t, width=5)
            all_road_hexes.extend(stamped)
        # Connect junctions to each other (chain + loop if 3+)
        for i in range(len(junctions) - 1):
            stamped = _place_road(grid, junctions[i], junctions[i + 1],
                                  road_terrain=_road_t, width=5)
            all_road_hexes.extend(stamped)
        if len(junctions) >= 3:
            stamped = _place_road(grid, junctions[-1], junctions[0],
                                  road_terrain=_road_t, width=5)
            all_road_hexes.extend(stamped)

        # Step 4: Connect buildings to nearest road (narrow side streets)
        road_set = set(all_road_hexes)
        for hc in building_centers:
            door_hex = None
            for h in hc.disk(8):
                if h in grid and grid[h].structure_type == "house_door":
                    door_hex = h
                    break
            target = door_hex if door_hex else hc
            road_end = target
            if door_hex:
                for nb in door_hex.neighbors():
                    if nb in grid and grid[nb].structure_type not in (
                        "house", "house_wall", "house_door",
                    ):
                        road_end = nb
                        break
            # Skip if already adjacent to a road
            if road_end in road_set or any(
                nb in road_set for nb in road_end.neighbors()
            ):
                continue
            # Route to nearest road hex
            if road_set:
                nearest_road = min(
                    road_set, key=lambda rh: road_end.distance(rh))
                stamped = _place_road(grid, road_end, nearest_road,
                                      road_terrain=_road_t, width=2)
                all_road_hexes.extend(stamped)
                road_set.update(stamped)

        # --- District-specific additions ---
        if district == "civic":
            # Central plaza: clear a small area around center
            for h in center.disk(3):
                if h in grid and grid[h].structure_type not in ("road",):
                    st = grid[h]
                    if st.terrain_type not in _overlay_terrain:
                        grid[h] = SpaceTerrain(
                            terrain_type=_road_t,
                            elevation=st.elevation,
                            moisture=st.moisture,
                            cover=Cover.NONE,
                            lighting=Lighting.BRIGHT,
                        )
            _place_single_structure(grid, center, "well", "stone")

        # --- Market stalls set back from roads (one hex off the edge) ---
        stall_placed: set[Hex] = set()
        road_set = set(all_road_hexes)
        if style.has_market_stalls:
            # Hexes directly bordering the road (ring 1)
            road_ring1: set[Hex] = set()
            for h in list(grid.keys()):
                if h in road_set:
                    continue
                if any(nb in road_set for nb in h.neighbors()):
                    road_ring1.add(h)
            # Candidates: free hexes adjacent to ring-1 but NOT adjacent to road
            stall_candidates: list[Hex] = []
            for h in list(grid.keys()):
                if h in road_set or h in road_ring1:
                    continue
                st = grid[h]
                if st.structure_type or st.terrain_type in _overlay_terrain:
                    continue
                if any(nb in road_ring1 for nb in h.neighbors()):
                    stall_candidates.append(h)
            area_rng.shuffle(stall_candidates)

            stall_chance = 0.12 if district == "market" else 0.04
            max_stalls = max(3, len(stall_candidates) // (6 if district == "market" else 15))
            stall_count = 0
            for h in stall_candidates:
                if stall_count >= max_stalls:
                    break
                if h in stall_placed:
                    continue
                if area_rng.random() > stall_chance:
                    continue
                # Build a 2-3 hex strip away from the road
                strip = [h]
                for nb in h.neighbors():
                    if len(strip) >= 3:
                        break
                    if nb in stall_placed or nb not in grid:
                        continue
                    if nb in road_set or nb in road_ring1:
                        continue
                    nb_st = grid[nb]
                    if nb_st.structure_type or nb_st.terrain_type in _overlay_terrain:
                        continue
                    if any(nb2 in road_ring1 for nb2 in nb.neighbors()):
                        strip.append(nb)
                if len(strip) < 2:
                    continue
                for sh in strip:
                    _place_single_structure(grid, sh, "market_stall", _road_t)
                    stall_placed.add(sh)
                stall_count += 1

        # --- Wagon carts parked near roads (6-hex strips) ---
        wagon_placed: set[Hex] = set()
        if not road_set:
            road_set = set(all_road_hexes)
        # Candidates: free hexes adjacent to road but not buildings
        wagon_candidates: list[Hex] = []
        for h in list(grid.keys()):
            if h in road_set or h in stall_placed:
                continue
            st = grid[h]
            if st.structure_type or st.terrain_type in _overlay_terrain:
                continue
            if not any(nb in road_set for nb in h.neighbors()):
                continue
            # Skip if next to a building
            if any(nb in grid and grid[nb].structure_type in (
                "house", "house_wall", "house_door") for nb in h.neighbors()):
                continue
            wagon_candidates.append(h)
        area_rng.shuffle(wagon_candidates)

        wagon_chance = 0.06 if district == "market" else 0.02
        max_wagons = max(1, len(wagon_candidates) // 20)
        wagon_count = 0
        for h in wagon_candidates:
            if wagon_count >= max_wagons:
                break
            if h in wagon_placed:
                continue
            if area_rng.random() > wagon_chance:
                continue
            # Build a 4-5 hex strip along the road edge
            strip = [h]
            pool = set(wagon_candidates) - wagon_placed - stall_placed
            for _ in range(4):
                grew = False
                for nb in strip[-1].neighbors():
                    if len(strip) >= 5:
                        break
                    if nb in wagon_placed or nb in stall_placed:
                        continue
                    if nb not in pool:
                        continue
                    strip.append(nb)
                    pool.discard(nb)
                    grew = True
                    break
                if not grew or len(strip) >= 5:
                    break
            if len(strip) < 4:
                continue
            for sh in strip:
                _place_single_structure(grid, sh, "wagon", _road_t)
                wagon_placed.add(sh)
            wagon_count += 1

        # --- Road edge decorations: low stone walls and street lamps ---
        road_set_final = {h for h in list(grid.keys())
                          if grid[h].structure_type == "road"}
        building_set_edge = {"house", "house_wall", "house_door"}
        occupied = set(stall_placed) | wagon_placed
        # Collect hexes just outside the road that are free
        road_edge_free: list[Hex] = []
        for h in list(grid.keys()):
            if h in occupied:
                continue
            st = grid[h]
            if st.structure_type or st.terrain_type in _overlay_terrain:
                continue
            # Must neighbour at least one road hex
            if not any(nb in road_set_final for nb in h.neighbors()):
                continue
            # Skip if next to a building (those get wall-adjacent decor)
            if any(nb in grid and grid[nb].structure_type in building_set_edge
                   for nb in h.neighbors()):
                continue
            road_edge_free.append(h)

        # Build connected components of road-edge hexes so walls form
        # continuous runs instead of scattered single hexes.
        edge_set = set(road_edge_free)
        visited: set[Hex] = set()
        components: list[list[Hex]] = []
        for seed in road_edge_free:
            if seed in visited:
                continue
            # BFS to find the connected component
            comp: list[Hex] = []
            queue = [seed]
            visited.add(seed)
            while queue:
                cur = queue.pop()
                comp.append(cur)
                for nb in cur.neighbors():
                    if nb in edge_set and nb not in visited:
                        visited.add(nb)
                        queue.append(nb)
            components.append(comp)

        # Sort components so larger runs get placed first
        components.sort(key=len, reverse=True)

        lamp_interval = 8
        for comp in components:
            # Skip tiny isolated hexes occasionally for variety
            if len(comp) == 1 and area_rng.random() > 0.5:
                continue
            for idx, h in enumerate(comp):
                if h in occupied:
                    continue
                if idx % lamp_interval == 0 and len(comp) > 2 and style.has_street_lamps:
                    _place_single_structure(grid, h, "street_lamp", _road_t)
                elif style.has_road_walls:
                    _place_single_structure(grid, h, "road_wall", _road_t)
                occupied.add(h)

        # --- Scatter city decorations against building walls (all districts) ---
        building_set = {"house", "house_wall", "house_door"}
        # Collect hexes that are directly adjacent to building walls
        wall_adjacent: list[tuple[Hex, list[Hex]]] = []
        for h in list(grid.keys()):
            st = grid[h]
            if st.structure_type or st.terrain_type == "water":
                continue
            # Find which neighbor(s) are wall hexes
            adj_walls = [
                nb for nb in h.neighbors()
                if nb in grid and grid[nb].structure_type in building_set
            ]
            if adj_walls:
                wall_adjacent.append((h, adj_walls))
        area_rng.shuffle(wall_adjacent)

        # Multi-hex decorations (awning): place 2-3 hex strips along walls
        multi_hex_types = ["awning"]
        placed_hexes: set[Hex] = set()
        multi_count = 0
        max_multi = max(3, len(wall_adjacent) // 6)

        for h, adj_walls in wall_adjacent:
            if h in placed_hexes or multi_count >= max_multi:
                continue
            if area_rng.random() > 0.30:
                continue
            # Find 1-2 additional adjacent hexes along the same wall face
            # that are also free and wall-adjacent
            strip = [h]
            for nb in h.neighbors():
                if len(strip) >= 3:
                    break
                if nb in placed_hexes or nb not in grid:
                    continue
                nb_st = grid[nb]
                if nb_st.structure_type or nb_st.terrain_type == "water":
                    continue
                # Must also be adjacent to a building wall
                if not any(
                    nb2 in grid and grid[nb2].structure_type in building_set
                    for nb2 in nb.neighbors()
                ):
                    continue
                strip.append(nb)

            if len(strip) < 2:
                continue  # need at least 2 hexes

            obj = area_rng.choice(multi_hex_types)
            for sh in strip:
                _place_single_structure(grid, sh, obj, _road_t)
                placed_hexes.add(sh)
            multi_count += 1

        # Single-hex decorations: barrels, crates, box stacks against walls
        single_choices = list(style.building_decorations)
        single_count = 0
        max_single = max(4, len(wall_adjacent) // 4)

        for h, adj_walls in wall_adjacent:
            if h in placed_hexes or single_count >= max_single:
                continue
            if area_rng.random() > 0.25:
                continue
            obj = area_rng.choice(single_choices)
            _place_single_structure(grid, h, obj, _road_t)
            placed_hexes.add(h)
            single_count += 1


def _place_city_buildings(
    grid: dict[Hex, SpaceTerrain],
    center: Hex,
    radius: int,
    rng,
    district: str,
    area_hex: Hex = Hex(0, 0),
    max_world_r: float = 0.0,
    building_max_r: int = 0,
    wall_interior: set[Hex] | None = None,
    interior_terrain: str = "wood",
    wall_terrain: str = "stone",
) -> list[Hex]:
    """Place buildings in city-block clusters.

    Buildings are grouped into tight clusters (blocks) separated by
    streets, creating realistic city blocks.  Each block contains
    several buildings packed closely together.

    Density is controlled by each block candidate's world-space pixel
    distance from the global city centre (``max_world_r``).  Blocks
    near the centre are placed at full density; blocks near the
    perimeter are probabilistically skipped so building coverage
    thins out smoothly.

    Returns the list of building center hexes placed.
    """
    placed_centers: list[Hex] = []

    scale = max(1.0, radius / 12.0)

    # Block-level parameters (distance between block centres)
    # Spacing is wide enough to create clear streets between clusters
    if district == "civic":
        block_spacing = max(18, int(14 + scale * 1.5))
        buildings_per_block = (2, 4)
        size_range = (3, max(4, min(5, int(3 + scale * 0.3))))
    elif district == "market":
        block_spacing = max(16, int(12 + scale * 1.5))
        buildings_per_block = (2, 4)
        size_range = (2, max(4, min(5, int(3 + scale * 0.3))))
    else:  # residential
        block_spacing = max(16, int(12 + scale * 1.5))
        buildings_per_block = (3, 5)
        size_range = (2, max(4, min(5, int(3 + scale * 0.3))))

    # Tight gap within a block — buildings can share walls
    within_gap = 2

    # --- Step 1: place block centres across the area ---
    block_centers: list[Hex] = []
    build_r = building_max_r if building_max_r > 0 else (radius - 5)
    candidates = center.disk(build_r)
    rng.shuffle(candidates)
    num_blocks = max(3, int(math.pi * build_r * build_r
                            / (block_spacing * block_spacing * 1.8)))

    # Scale num_blocks by this area's radial position so outer areas
    # get fewer blocks overall (smooth gradient across the city).
    if max_world_r > 0:
        area_wh = _area_to_world(area_hex, center, radius)
        apx, apy = hex_to_pixel(area_wh, 1.0)
        area_frac = math.sqrt(apx * apx + apy * apy) / max_world_r
        # Full density in inner 50%, then gentle falloff to 40% at edge
        area_density = max(0.40, 1.0 - max(0.0, area_frac - 0.5) * 1.2)
        num_blocks = max(2, int(num_blocks * area_density))

    for hc in candidates:
        if len(block_centers) >= num_blocks:
            break
        if hc not in grid:
            continue
        st = grid[hc]
        if st.terrain_type == "water" or st.structure_type:
            continue
        # Skip blocks outside the wall boundary
        if wall_interior is not None:
            wh = _area_to_world(area_hex, hc, radius)
            if wh not in wall_interior:
                continue
        if district == "civic" and hc.distance(center) < 4:
            continue
        if any(hc.distance(bc) < block_spacing for bc in block_centers):
            continue

        # --- Per-block density: WORLD-SPACE primary, local secondary ---
        local_dist = hc.distance(center)
        if local_dist > build_r:
            continue
        local_frac = local_dist / max(1, build_r)
        # Local: only taper near the very edge of the area (prevent bleed)
        local_prob = 1.0 if local_frac < 0.85 else max(0.20, 1.0 - (local_frac - 0.85) * 5.3)

        # Primary: world-space distance from global city centre
        if max_world_r > 0:
            wh = _area_to_world(area_hex, hc, radius)
            wpx, wpy = hex_to_pixel(wh, 1.0)
            frac = math.sqrt(wpx * wpx + wpy * wpy) / max_world_r
            world_prob = max(0.15, 1.0 - max(0.0, frac - 0.4) * 1.4)
            place_prob = world_prob * local_prob
        else:
            place_prob = local_prob
        if rng.random() > place_prob:
            continue

        block_centers.append(hc)

    # --- Step 2: fill each block with tightly-packed buildings ---
    for bc in block_centers:
        block_hexes = bc.disk(block_spacing // 2)
        rng.shuffle(block_hexes)
        n_bldgs = rng.randint(*buildings_per_block)
        block_placed = 0

        for hc in block_hexes:
            if block_placed >= n_bldgs:
                break
            if hc not in grid:
                continue
            st = grid[hc]
            if st.terrain_type == "water" or st.structure_type:
                continue
            if any(hc.distance(pc) < within_gap for pc in placed_centers):
                continue

            w = rng.randint(size_range[0], size_range[1])
            d = rng.randint(size_range[0], size_range[1])

            # Door faces toward area centre (main roads)
            best_dir = "E"
            best_dist = 9999
            for dname in DIRECTION_LIST:
                dq, dr = DIRECTIONS[dname]
                candidate = Hex(hc.q + dq, hc.r + dr)
                dd = candidate.distance(center)
                if dd < best_dist:
                    best_dist = dd
                    best_dir = dname

            use_hex = rng.random() < 0.35
            if use_hex:
                hex_r = max(w, d)
                success = _place_hex_building(grid, hc, hex_r, best_dir,
                                             tight=True,
                                             interior_terrain=interior_terrain,
                                             wall_terrain=wall_terrain)
            else:
                success = _place_building(grid, hc, w, d, best_dir,
                                          tight=True,
                                          interior_terrain=interior_terrain,
                                          wall_terrain=wall_terrain)

            if success:
                placed_centers.append(hc)
                block_placed += 1

    return placed_centers


# Furniture types and their cover / lighting properties
_FURNITURE_TYPES = ("bed", "table", "chair", "desk", "rug", "bookshelf", "chest")

_FURNITURE_COVER: dict[str, Cover] = {
    "bed":       Cover.HALF,
    "table":     Cover.HALF,
    "chair":     Cover.QUARTER,
    "desk":      Cover.HALF,
    "rug":       Cover.NONE,
    "bookshelf": Cover.THREE_QUARTER,
    "chest":     Cover.HALF,
}


def _furnish_buildings(
    grid: dict[Hex, SpaceTerrain],
    rng,
) -> None:
    """Place multi-hex furniture groups inside building interiors.

    Furniture is placed as coherent clusters:
    - Bed: 2-3 adjacent hexes against a wall
    - Table + chairs: table cluster in centre with chair hexes around it
    - Rug: 2-4 hex patch in the centre
    - Bookshelf: 2+ hexes along a wall (big buildings only)
    """
    from collections import defaultdict

    # Group interior hexes by building_id
    buildings: dict[int, list[Hex]] = defaultdict(list)
    for h, st in grid.items():
        if st.structure_type == "house" and st.building_id > 0:
            buildings[st.building_id].append(h)

    for bid, hexes in buildings.items():
        if len(hexes) < 3:
            continue

        interior_set = set(hexes)

        # Classify hexes: wall-adjacent vs deep interior
        wall_adj: list[Hex] = []
        deep: list[Hex] = []
        for h in hexes:
            near_wall = False
            for nb in h.neighbors():
                st_nb = grid.get(nb)
                if st_nb and st_nb.structure_type == "house_wall" and st_nb.building_id == bid:
                    near_wall = True
                    break
            if near_wall:
                wall_adj.append(h)
            else:
                deep.append(h)

        # Small buildings: everything is wall-adjacent; pick centremost as "deep"
        if not deep:
            avg_q = sum(h.q for h in hexes) / len(hexes)
            avg_r = sum(h.r for h in hexes) / len(hexes)
            wall_adj.sort(key=lambda h: abs(h.q - avg_q) + abs(h.r - avg_r))
            deep = wall_adj[:max(1, len(wall_adj) // 3)]
            wall_adj = wall_adj[len(deep):]

        rng.shuffle(wall_adj)
        rng.shuffle(deep)

        furnished: set[Hex] = set()

        def _place_cluster(start: Hex, ftype: str, count: int,
                           pool: list[Hex]) -> int:
            """Place *ftype* on *start* and up to *count-1* adjacent hexes."""
            placed = 0
            if start in furnished:
                return 0
            _apply_furniture(grid, start, ftype)
            furnished.add(start)
            placed += 1
            # Grow into neighbors that are in pool and not yet used
            nbs = [nb for nb in start.neighbors()
                   if nb in interior_set and nb not in furnished and nb in set(pool)]
            rng.shuffle(nbs)
            for nb in nbs:
                if placed >= count:
                    break
                _apply_furniture(grid, nb, ftype)
                furnished.add(nb)
                placed += 1
            return placed

        # --- Bed cluster (2-3 hexes) against a wall ---
        bed_size = 2 if len(hexes) < 12 else 3
        for h in wall_adj:
            if h in furnished:
                continue
            if _place_cluster(h, "bed", bed_size, wall_adj) > 0:
                break

        # --- Rug patch (2-3 hexes) in centre ---
        if len(hexes) >= 5:
            rug_size = 2 if len(hexes) < 15 else 3
            for h in deep:
                if h in furnished:
                    continue
                if _place_cluster(h, "rug", rug_size, deep) > 0:
                    break

        # --- Table (1-2 hexes) in centre + chair ring around it ---
        if len(hexes) >= 8:
            table_size = 1 if len(hexes) < 16 else 2
            for h in deep:
                if h in furnished:
                    continue
                placed = _place_cluster(h, "table", table_size, deep)
                if placed > 0:
                    # Ring chairs around the table cluster
                    table_hexes = {h}
                    for nb in h.neighbors():
                        if nb in furnished and grid.get(nb) and grid[nb].structure_type == "table":
                            table_hexes.add(nb)
                    chair_candidates = []
                    for th in table_hexes:
                        for nb in th.neighbors():
                            if (nb in interior_set and nb not in furnished
                                    and nb not in chair_candidates):
                                chair_candidates.append(nb)
                    rng.shuffle(chair_candidates)
                    n_chairs = min(len(chair_candidates), rng.randint(2, 3))
                    for ci in range(n_chairs):
                        _apply_furniture(grid, chair_candidates[ci], "chair")
                        furnished.add(chair_candidates[ci])
                    break

        # --- Bookshelf cluster (2 hexes) along wall — big buildings ---
        if len(hexes) >= 16:
            for h in wall_adj:
                if h in furnished:
                    continue
                if _place_cluster(h, "bookshelf", 2, wall_adj) > 0:
                    break

        # --- Desk (1 hex) near wall — big buildings ---
        if len(hexes) >= 20:
            for h in wall_adj:
                if h not in furnished:
                    _apply_furniture(grid, h, "desk")
                    furnished.add(h)
                    break

        # --- Chest (1 hex) near wall — very big buildings ---
        if len(hexes) >= 28:
            for h in wall_adj:
                if h not in furnished:
                    _apply_furniture(grid, h, "chest")
                    furnished.add(h)
                    break


def _apply_furniture(
    grid: dict[Hex, SpaceTerrain],
    h: Hex,
    furniture_type: str,
) -> None:
    """Stamp a furniture piece onto an interior hex, keeping building metadata."""
    st = grid[h]
    cover = _FURNITURE_COVER.get(furniture_type, Cover.NONE)
    grid[h] = SpaceTerrain(
        terrain_type=st.terrain_type,
        elevation=st.elevation,
        moisture=st.moisture,
        cover=cover,
        lighting=st.lighting,
        structure_type=furniture_type,
        building_id=st.building_id,
        building_shape=st.building_shape,
    )


def _hex_line(a: Hex, b: Hex) -> list[Hex]:
    """Return all hexes along the straight line from *a* to *b*."""
    from eight_card_system.hex_math import _axial_round

    n = a.distance(b)
    if n == 0:
        return [a]
    results: list[Hex] = []
    for i in range(n + 1):
        t = i / n
        q = a.q + (b.q - a.q) * t
        r = a.r + (b.r - a.r) * t
        results.append(_axial_round(q, r))
    return results


def _compute_wall_boundary(
    areas: dict[Hex, dict[Hex, SpaceTerrain]],
    city_hexes: list[Hex],
    radius: int,
    style: CultureConfig,    rng,
) -> tuple[
    list[Hex],                                  # wall_candidates
    set[Hex],                                   # interior (inside wall ring)
    dict[Hex, int],                             # dist_from_edge
    dict[Hex, list[tuple[Hex, Hex]]],           # world_to_local
    set[Hex],                                   # world_set
]:
    """Pre-compute a noisy wall boundary and the interior hex set.

    Uses angle-based sinusoidal noise to vary the wall inset distance
    around the city perimeter, producing organic, irregular wall shapes.

    Returns the wall candidate list, the set of world hexes inside the
    wall ring (for constraining buildings), plus supporting data needed
    by :func:`_place_city_walls` so it can skip recomputing them.
    """
    import math as _m
    from collections import deque

    inset = style.wall_inset
    noise_amp = style.wall_inset_noise
    skip_terrain = {"water", "mud", "lava"}

    # Deterministic noise parameters from RNG — six 2D spatial frequencies
    # and offsets so the wall outline varies independently of the edge shape.
    freqs = tuple(rng.uniform(0.03, 0.07) for _ in range(6))
    offsets = tuple(rng.uniform(0, 200) for _ in range(6))

    # --- Step 1: Build world-space hex set and area lookup ----------------
    world_set: set[Hex] = set()
    world_to_local: dict[Hex, list[tuple[Hex, Hex]]] = {}
    for ah in city_hexes:
        for h in areas[ah]:
            wh = _area_to_world(ah, h, radius)
            world_set.add(wh)
            if wh not in world_to_local:
                world_to_local[wh] = []
            world_to_local[wh].append((ah, h))

    # --- Step 2: BFS from boundary inward — distance from city edge -------
    dist_from_edge: dict[Hex, int] = {}
    queue: deque[Hex] = deque()
    for wh in world_set:
        for nb in wh.neighbors():
            if nb not in world_set:
                dist_from_edge[wh] = 1
                queue.append(wh)
                break

    while queue:
        cur = queue.popleft()
        for nb in cur.neighbors():
            if nb in world_set and nb not in dist_from_edge:
                dist_from_edge[nb] = dist_from_edge[cur] + 1
                queue.append(nb)

    # --- Step 3: Per-hex varying inset via 2D spatial noise ----------------
    # Uses the hex's world-space pixel position (not angle) so the wall
    # outline varies independently of the city edge shape — creating
    # genuine bulges, indentations, and alcoves.
    def _varied_inset(wh: Hex) -> int:
        px, py = hex_to_pixel(wh, 1.0)
        noise = (
            _m.sin(px * freqs[0] + offsets[0])
            * _m.sin(py * freqs[1] + offsets[1]) * 0.45
            + _m.sin(px * freqs[2] + py * freqs[3] + offsets[2]) * 0.35
            + _m.sin(px * freqs[4] - py * freqs[5] + offsets[3]) * 0.20
        )
        return max(4, inset + round(noise_amp * noise))

    wall_candidates: list[Hex] = []
    interior: set[Hex] = set()
    for wh, d in dist_from_edge.items():
        vi = _varied_inset(wh)
        # Wall ring: single-hex-wide line at the varied inset
        if d == vi:
            entries = world_to_local.get(wh, [])
            if entries:
                ah0, h0 = entries[0]
                st = areas[ah0][h0]
                if st.terrain_type not in skip_terrain and st.structure_type != "road":
                    wall_candidates.append(wh)
        # Interior = everything deeper than the wall line
        if d > vi:
            interior.add(wh)

    return wall_candidates, interior, dist_from_edge, world_to_local, world_set


def _place_city_walls(
    areas: dict[Hex, dict[Hex, SpaceTerrain]],
    city_hexes: list[Hex],
    radius: int,
    style: CultureConfig,
    rng,
    *,
    precomputed: tuple[
        list[Hex], set[Hex], dict[Hex, int],
        dict[Hex, list[tuple[Hex, Hex]]], set[Hex],
    ] | None = None,
    climate: dict[str, str] | None = None,
) -> None:
    """Place city walls along the true outer perimeter of the entire city.

    Instead of per-area rings, computes the actual city boundary in world
    space via BFS distance-from-edge, then places a wall ring at a
    noise-varied inset from the city edge.  Walls only appear at the
    combined city outline — never cutting through interior areas.

    If *precomputed* is provided (from :func:`_compute_wall_boundary`),
    skips Steps 1–3 and uses the cached data directly.
    """
    import math as _m

    if climate is None:
        climate = CITY_CLIMATE["plains"]
    terrain_type = style.wall_terrain
    wall_material = style.wall_material
    ground_t = climate["ground_terrain"]
    skip_terrain = {"water", "mud", "lava"}

    if precomputed is not None:
        wall_candidates, _interior, dist_from_edge, world_to_local, world_set = precomputed
    else:
        wall_candidates, _interior, dist_from_edge, world_to_local, world_set = (
            _compute_wall_boundary(areas, city_hexes, radius, style, rng)
        )

    if len(wall_candidates) < 6:
        return

    # --- Step 4: Angular sort and coverage selection ----------------------
    def _wangle(wh: Hex) -> float:
        px, py = hex_to_pixel(wh, 1.0)
        return _m.atan2(py, px)

    wall_candidates.sort(key=_wangle)
    n_total = len(wall_candidates)
    n_walled = max(6, int(n_total * style.wall_coverage))
    start_idx = rng.randint(0, n_total - 1)

    wall_world: set[Hex] = set()
    walled_list: list[Hex] = []
    for k in range(n_walled):
        idx = (start_idx + k) % n_total
        wh = wall_candidates[idx]
        wall_world.add(wh)
        walled_list.append(wh)

    # --- Step 5: Tower positions at even intervals along the arc ----------
    n_towers = max(2, min(12, n_walled // 25))
    tower_centers: list[Hex] = []
    tower_step = n_walled / n_towers
    for i in range(n_towers):
        ti = int(i * tower_step)
        tower_centers.append(walled_list[ti])
    if walled_list[-1] not in tower_centers:
        tower_centers.append(walled_list[-1])

    # --- Step 6: Thicken wall (inward walkway) ----------------------------
    inner_hexes: set[Hex] = set()
    for wh in list(wall_world):
        best_nb = None
        best_dist = -1
        for nb in wh.neighbors():
            if nb in wall_world or nb not in world_set:
                continue
            if nb not in dist_from_edge:
                continue
            entries = world_to_local.get(nb, [])
            if not entries:
                continue
            ah0, h0 = entries[0]
            nst = areas[ah0][h0]
            if nst.terrain_type in skip_terrain:
                continue
            d = dist_from_edge[nb]
            if d > best_dist:
                best_dist = d
                best_nb = nb
        if best_nb is not None:
            inner_hexes.add(best_nb)
    wall_world |= inner_hexes

    # --- Step 7: Stamp wall hexes into area grids -------------------------
    building_types = {"house", "house_wall", "house_door"}
    for wh in wall_world:
        for ah, h in world_to_local.get(wh, []):
            grid = areas[ah]
            if h not in grid:
                continue
            st = grid[h]
            if st.structure_type == "road":
                continue
            if st.terrain_type in skip_terrain:
                continue
            grid[h] = SpaceTerrain(
                terrain_type=terrain_type,
                elevation=st.elevation,
                moisture=st.moisture,
                cover=derive_cover("", wall_material, st.elevation),
                lighting=derive_lighting(
                    terrain_type, wall_material, st.moisture,
                ),
                structure_type=wall_material,
            )

    # --- Step 7b: Clear buildings adjacent to walls -----------------------
    for wh in list(wall_world):
        for nb in wh.neighbors():
            if nb in wall_world:
                continue
            for ah, h in world_to_local.get(nb, []):
                grid = areas[ah]
                if h not in grid:
                    continue
                nst = grid[h]
                if nst.structure_type in building_types:
                    grid[h] = SpaceTerrain(
                        terrain_type=ground_t,
                        elevation=nst.elevation,
                        moisture=nst.moisture,
                        cover=Cover.NONE,
                        lighting=derive_lighting(ground_t, "", nst.moisture),
                        structure_type="",
                    )

    # --- Step 8: Place watchtowers ----------------------------------------
    _tw_r = style.tower_radius
    for tp in tower_centers:
        entries = world_to_local.get(tp, [])
        if not entries:
            continue
        ah0, h0 = entries[0]
        tp_st = areas[ah0][h0]
        if tp_st.terrain_type in skip_terrain:
            continue

        tower_disk = tp.disk(_tw_r)
        valid_count = sum(1 for th in tower_disk if th in world_set)
        if valid_count < max(4, int(len(tower_disk) * 0.3)):
            continue

        for th in tower_disk:
            if th not in world_set:
                continue
            for ah, h in world_to_local.get(th, []):
                grid = areas[ah]
                if h not in grid:
                    continue
                tst = grid[h]
                if tst.terrain_type in skip_terrain:
                    continue
                if th == tp:
                    stype = "watchtower"
                    tcover = Cover.FULL
                else:
                    stype = wall_material
                    tcover = derive_cover("", wall_material, tst.elevation)
                grid[h] = SpaceTerrain(
                    terrain_type=terrain_type,
                    elevation=tst.elevation,
                    moisture=tst.moisture,
                    cover=tcover,
                    lighting=derive_lighting(
                        terrain_type, stype, tst.moisture,
                    ),
                    structure_type=stype,
                )


# ─── Hamlet / Settlement Generator ───────────────────────────────────────────

def _hash_hex(q: int, r: int, seed: int) -> int:
    """Fast deterministic hash for structure placement decisions."""
    return ((q + 500) * 73856093 + (r + 500) * 19349669 + seed * 83492791) & 0xFFFFFFFF


def _place_building(
    grid: dict[Hex, SpaceTerrain],
    center: Hex,
    width: int,
    depth: int,
    door_direction: str,
    tight: bool = False,
    interior_terrain: str = "wood",
    wall_terrain: str = "stone",
) -> bool:
    """Stamp a rectangular building onto the grid.

    Selects hexes whose pixel-space centres fall within a rectangle,
    giving a visually rectangular footprint on the hex grid.
    Marks interior hexes as 'house', perimeter as 'house_wall',
    and one hex in the door_direction as 'house_door'.

    When *tight* is True the 1-hex gap between adjacent buildings is
    not enforced, allowing buildings in the same block to share walls.

    Returns True if placement succeeded, False if it would overlap
    an existing building.
    """
    global _next_building_id
    import math
    from .hex_math import DIRECTIONS, hex_to_pixel

    building_structs = ("house", "house_wall", "house_door")

    # Pixel centre of the building (unit size)
    cx, cy = hex_to_pixel(center, 1.0)

    # Half-extents in pixel space (unit-size coordinates).
    # Each q step = 1.5 px, each r step ≈ 1.73 px.
    half_w = 1.5 * width + 0.01
    half_h = math.sqrt(3) * depth + 0.01

    # Collect hexes whose pixel centre falls inside the rectangle
    interior: set[Hex] = set()
    search = max(width, depth) + 2
    for dq in range(-search, search + 1):
        for dr in range(-search, search + 1):
            h = Hex(center.q + dq, center.r + dr)
            hx, hy = hex_to_pixel(h, 1.0)
            if abs(hx - cx) <= half_w and abs(hy - cy) <= half_h:
                if h not in grid:
                    # Building would extend outside map — reject entirely
                    return False
                interior.add(h)

    # Reject placement if any interior hex is overlay terrain (water/mud/lava)
    # or already belongs to a building (ensures 1-hex gap between buildings)
    _overlay_terrain = {"water", "mud", "lava"}
    for h in interior:
        if h in grid:
            if grid[h].structure_type in building_structs:
                return False
            if grid[h].terrain_type in _overlay_terrain:
                return False
        if not tight:
            for nb in h.neighbors():
                if nb in interior:
                    continue
                if nb in grid and grid[nb].structure_type in building_structs:
                    return False

    bid = _next_building_id
    _next_building_id += 1

    # Perimeter = hexes in interior that have a neighbor NOT in interior
    perimeter = set()
    for h in interior:
        for nb in h.neighbors():
            if nb not in interior:
                perimeter.add(h)
                break

    # Door placement: find the perimeter hex closest to door direction
    # that actually opens to free space (not blocked by another building)
    dq_d, dr_d = DIRECTIONS.get(door_direction, (1, 0))
    door_target = Hex(center.q + dq_d * (width + 1), center.r + dr_d * (depth + 1))

    def _has_free_exit(h: Hex) -> bool:
        """True if at least one neighbor outside the building is not another building."""
        for nb in h.neighbors():
            if nb in interior:
                continue
            if nb not in grid:
                continue
            if grid[nb].structure_type not in building_structs:
                return True
        return False

    # Prefer perimeter hexes that have a walkable exit
    candidates = [h for h in perimeter if _has_free_exit(h)]
    pool = candidates if candidates else list(perimeter)
    door_hex = min(pool, key=lambda h: h.distance(door_target)) if pool else None

    # Apply structures
    for h in interior:
        if h not in grid:
            continue
        st = grid[h]
        if h == door_hex:
            stype = "house_door"
        elif h in perimeter:
            stype = "house_wall"
        else:
            stype = "house"
        grid[h] = SpaceTerrain(
            terrain_type=interior_terrain if h not in perimeter else wall_terrain,
            elevation=st.elevation,
            moisture=st.moisture,
            cover=derive_cover("", stype, st.elevation),
            lighting=derive_lighting("", stype, st.moisture),
            structure_type=stype,
            building_id=bid,
            building_shape="rect",
        )
    return True


def _place_hex_building(
    grid: dict[Hex, SpaceTerrain],
    center: Hex,
    radius: int,
    door_direction: str,
    tight: bool = False,
    interior_terrain: str = "wood",
    wall_terrain: str = "stone",
) -> bool:
    """Stamp a hex-shaped building onto the grid.

    Uses center.disk(radius) for a hexagonal footprint.
    Marks interior hexes as 'house', perimeter as 'house_wall',
    and one hex in the door_direction as 'house_door'.

    When *tight* is True the 1-hex gap between adjacent buildings is
    not enforced, allowing buildings in the same block to share walls.

    Returns True if placement succeeded, False if it would overlap
    an existing building.
    """
    global _next_building_id
    from .hex_math import DIRECTIONS

    building_structs = ("house", "house_wall", "house_door")

    interior = set(center.disk(radius))

    # Reject if any hex is outside the grid
    for h in interior:
        if h not in grid:
            return False

    # Reject if any interior hex is overlay terrain or already a building
    _overlay_terrain = {"water", "mud", "lava"}
    for h in interior:
        if grid[h].structure_type in building_structs:
            return False
        if grid[h].terrain_type in _overlay_terrain:
            return False
        if not tight:
            for nb in h.neighbors():
                if nb in interior:
                    continue
                if nb in grid and grid[nb].structure_type in building_structs:
                    return False

    bid = _next_building_id
    _next_building_id += 1

    # Perimeter = hexes on the outermost ring
    perimeter = set(center.ring(radius)) & interior

    # Door placement
    dq_d, dr_d = DIRECTIONS.get(door_direction, (1, 0))
    door_target = Hex(center.q + dq_d * (radius + 1), center.r + dr_d * (radius + 1))

    def _has_free_exit(h: Hex) -> bool:
        for nb in h.neighbors():
            if nb in interior:
                continue
            if nb not in grid:
                continue
            if grid[nb].structure_type not in building_structs:
                return True
        return False

    candidates = [h for h in perimeter if _has_free_exit(h)]
    pool = candidates if candidates else list(perimeter)
    door_hex = min(pool, key=lambda h: h.distance(door_target)) if pool else None

    for h in interior:
        st = grid[h]
        if h == door_hex:
            stype = "house_door"
        elif h in perimeter:
            stype = "house_wall"
        else:
            stype = "house"
        grid[h] = SpaceTerrain(
            terrain_type=interior_terrain if h not in perimeter else wall_terrain,
            elevation=st.elevation,
            moisture=st.moisture,
            cover=derive_cover("", stype, st.elevation),
            lighting=derive_lighting("", stype, st.moisture),
            structure_type=stype,
            building_id=bid,
            building_shape="hex",
        )
    return True


def _place_farmland(
    grid: dict[Hex, SpaceTerrain],
    center: Hex,
    radius: int,
    crop_seed: int,
) -> None:
    """Place a patch of farmland (tilled rows)."""
    for h in center.disk(radius):
        if h not in grid:
            continue
        st = grid[h]
        if st.structure_type:
            continue  # don't overwrite buildings
        if st.terrain_type == "water":
            continue
        grid[h] = SpaceTerrain(
            terrain_type="dirt",
            elevation=st.elevation,
            moisture=max(st.moisture, 0.5),
            cover=Cover.NONE,
            lighting=derive_lighting("dirt", "farmland", st.moisture),
            structure_type="farmland",
        )


def _smooth_hex_path(
    path: list[Hex],
    grid: dict[Hex, SpaceTerrain],
    avoid_structures: tuple[str, ...],
    avoid_terrain: set[str],
) -> list[Hex]:
    """Smooth a jagged A* hex path via Chaikin corner-cutting in pixel space.

    Returns a smoothed centre-line that stays within *grid* and avoids
    blocked structures/terrain.  Falls back to the raw path when the
    smoothed result is too short.
    """
    if len(path) < 5:
        return path

    # --- 1. Subsample to control points ---
    step = max(2, len(path) // 8)
    indices = list(range(0, len(path), step))
    if indices[-1] != len(path) - 1:
        indices.append(len(path) - 1)

    size = 1.0
    pts = [hex_to_pixel(path[i], size) for i in indices]

    # --- 2. Chaikin corner-cutting (2 iterations) ---
    for _ in range(2):
        if len(pts) < 3:
            break
        new_pts = [pts[0]]
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            new_pts.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
            new_pts.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        new_pts.append(pts[-1])
        pts = new_pts

    # --- 3. Convert back to hex coordinates (dedup consecutive) ---
    smooth: list[Hex] = []
    for x, y in pts:
        h = pixel_to_hex(x, y, size)
        if h in grid and (not smooth or h != smooth[-1]):
            smooth.append(h)
    if not smooth:
        return path

    # Ensure endpoints match original path
    if smooth[0] != path[0]:
        smooth.insert(0, path[0])
    if smooth[-1] != path[-1]:
        smooth.append(path[-1])

    # --- 4. Fill connectivity gaps via pixel-space interpolation ---
    connected: list[Hex] = [smooth[0]]
    for i in range(1, len(smooth)):
        prev, cur = connected[-1], smooth[i]
        d = prev.distance(cur)
        if d > 1:
            px0 = hex_to_pixel(prev, size)
            px1 = hex_to_pixel(cur, size)
            for si in range(1, d + 1):
                t = si / d
                mx = px0[0] + (px1[0] - px0[0]) * t
                my = px0[1] + (px1[1] - px0[1]) * t
                h = pixel_to_hex(mx, my, size)
                if h in grid and h != connected[-1]:
                    connected.append(h)
        elif cur != prev:
            connected.append(cur)

    # --- 5. Drop hexes that hit blocked structures/terrain ---
    valid: list[Hex] = []
    for h in connected:
        st = grid.get(h)
        if st is None:
            continue
        if st.structure_type in avoid_structures:
            continue
        if st.terrain_type in avoid_terrain:
            continue
        valid.append(h)

    return valid if len(valid) >= 2 else path


def _place_road(
    grid: dict[Hex, SpaceTerrain],
    start: Hex,
    end: Hex,
    road_terrain: str = "dirt",
    width: int = 1,
) -> list[Hex]:
    """Place a road between two hexes, routing around buildings via A*.

    *width* controls how many hex-rows the road spans:
      - width 1: single-hex path
      - width 2: centre-line + partial neighbours (~2 hex)
      - width 3: centre-line + all neighbours (~3 hex)
      - width 5: centre-line + 2 rings of neighbours (~5 hex)
    Returns every hex stamped as road.
    """
    import heapq

    building_types = ("house", "house_wall", "house_door")
    _overlay_terrain = {"water", "mud", "lava"}

    # A* pathfinding that avoids buildings and overlay terrain
    open_set: list[tuple[int, int, Hex]] = []
    heapq.heappush(open_set, (start.distance(end), 0, start))
    came_from: dict[Hex, Hex | None] = {start: None}
    g_score: dict[Hex, int] = {start: 0}
    counter = 1

    while open_set:
        _, _, current = heapq.heappop(open_set)
        if current == end:
            break
        for nb in current.neighbors():
            if nb not in grid:
                continue
            st = grid[nb]
            if st.structure_type in building_types:
                continue
            if st.terrain_type in _overlay_terrain:
                continue
            cost = g_score[current] + 1
            if cost < g_score.get(nb, 999999):
                g_score[nb] = cost
                f = cost + nb.distance(end)
                heapq.heappush(open_set, (f, counter, nb))
                counter += 1
                came_from[nb] = current

    # Reconstruct centre-line path
    if end not in came_from:
        return []
    path: list[Hex] = []
    h: Hex | None = end
    while h is not None:
        path.append(h)
        h = came_from[h]

    # Smooth the jagged A* path into gentle curves
    path = _smooth_hex_path(path, grid, building_types, _overlay_terrain)

    # Collect hexes adjacent to buildings so roads keep a sidewalk gap
    building_adj: set[Hex] = set()
    for ph in path:
        for nb in ph.neighbors():
            if nb in grid and grid[nb].structure_type in building_types:
                for nb2 in nb.neighbors():
                    if nb2 in grid and grid[nb2].structure_type not in building_types:
                        building_adj.add(nb2)

    # Expand path to desired width
    road_hexes: set[Hex] = set(path)
    if width >= 2:
        for ph in path:
            for nb in ph.neighbors():
                if nb in grid and grid[nb].structure_type not in building_types \
                        and grid[nb].terrain_type not in _overlay_terrain \
                        and nb not in building_adj:
                    road_hexes.add(nb)
    if width >= 5:
        ring1 = set(road_hexes)
        for ph in ring1:
            for nb in ph.neighbors():
                if nb in grid and grid[nb].structure_type not in building_types \
                        and grid[nb].terrain_type not in _overlay_terrain \
                        and nb not in building_adj:
                    road_hexes.add(nb)

    stamped: list[Hex] = []
    for h in road_hexes:
        if h not in grid:
            continue
        st = grid[h]
        if st.structure_type in building_types:
            continue
        if st.terrain_type in _overlay_terrain:
            continue
        grid[h] = SpaceTerrain(
            terrain_type=road_terrain,
            elevation=st.elevation,
            moisture=st.moisture,
            cover=Cover.NONE,
            lighting=derive_lighting(road_terrain, "road", st.moisture),
            structure_type="road",
        )
        stamped.append(h)
    return stamped


def _place_single_structure(
    grid: dict[Hex, SpaceTerrain],
    pos: Hex,
    structure_type: str,
    terrain_type: str = "stone",
) -> None:
    """Place a single-hex structure (well, cart, crate, etc.)."""
    if pos not in grid:
        return
    st = grid[pos]
    # Don't overwrite DM overlay terrain
    if st.terrain_type in ("water", "mud", "lava"):
        return
    grid[pos] = SpaceTerrain(
        terrain_type=terrain_type,
        elevation=st.elevation,
        moisture=st.moisture,
        cover=derive_cover("", structure_type, st.elevation),
        lighting=derive_lighting("", structure_type, st.moisture),
        structure_type=structure_type,
    )


def _surround_with_fence(
    grid: dict[Hex, SpaceTerrain],
    center: Hex,
    radius: int,
    gap_direction: str = "SE",
) -> None:
    """Place a fence ring around a center hex, with a gap for entry."""
    from .hex_math import DIRECTIONS
    fence_hexes = center.ring(radius)
    dq_g, dr_g = DIRECTIONS.get(gap_direction, (0, 1))
    gap_pos = Hex(center.q + dq_g * radius, center.r + dr_g * radius)

    for h in fence_hexes:
        if h not in grid:
            continue
        if h.distance(gap_pos) <= 1:
            continue  # leave gap
        st = grid[h]
        if st.structure_type in ("house", "house_wall", "house_door"):
            continue
        grid[h] = SpaceTerrain(
            terrain_type="wood",
            elevation=st.elevation,
            moisture=st.moisture,
            cover=derive_cover("", "fence", st.elevation),
            lighting=derive_lighting("", "fence", st.moisture),
            structure_type="fence",
        )


def generate_hamlet(
    terrain_seed: int,
    radius: int = 60,
    num_houses: int = 12,
) -> dict[Hex, SpaceTerrain]:
    """Generate a small hamlet encounter area.

    Creates a plains settlement with:
      - Houses (2-3 hex radius each) placed around a village green
      - A central well
      - Surrounding farmland patches
      - A road connecting the houses through center
      - Fences around some farm plots
      - Hay bales and crates for cover near buildings
    """
    grid = generate_space_terrain(terrain_seed, biome="plains", radius=radius)

    center = Hex(0, 0)

    # --- Place houses in a rough ring around center ---
    house_centers: list[Hex] = []
    ring_radius = max(5, radius // 3)
    ring_hexes = center.ring(ring_radius)

    step = max(1, len(ring_hexes) // num_houses)
    for i in range(num_houses):
        idx = (i * step + _hash_hex(i, terrain_seed, 7) % 3) % len(ring_hexes)
        hc = ring_hexes[idx]
        if hc in grid and grid[hc].terrain_type != "water":
            house_centers.append(hc)

    directions_list = ["E", "NE", "NW", "W", "SW", "SE"]
    for i, hc in enumerate(house_centers):
        scale = max(1, radius // 20)
        w = 1 + (_hash_hex(hc.q, hc.r, terrain_seed) % (1 + scale))
        d = 1 + (_hash_hex(hc.r, hc.q, terrain_seed) % (1 + scale))
        # Door faces center (toward roads)
        best_dir = "E"
        best_dist = 9999
        for dname in directions_list:
            dq, dr = DIRECTIONS[dname]
            candidate = Hex(hc.q + dq, hc.r + dr)
            dd = candidate.distance(center)
            if dd < best_dist:
                best_dist = dd
                best_dir = dname
        # Alternate between rect and hex buildings
        if _hash_hex(hc.q, hc.r, terrain_seed + 55) % 3 == 0:
            _place_hex_building(grid, hc, max(w, d), best_dir)
        else:
            _place_building(grid, hc, w, d, best_dir)

    # --- Central well ---
    _place_single_structure(grid, center, "well", "stone")

    # --- Road from south edge through center to north edge ---
    south_edge = Hex(0, radius - 1)
    north_edge = Hex(0, -(radius - 1))
    _place_road(grid, south_edge, center)
    _place_road(grid, center, north_edge)

    # Road branches to each house door (find the door hex, route to it)
    for hc in house_centers:
        # Find the door hex for this building
        door_hex = None
        for h, st in grid.items():
            if st.structure_type == "house_door" and h.distance(hc) <= 3:
                door_hex = h
                break
        target = door_hex if door_hex else hc
        # Route to the nearest non-building hex adjacent to the door
        road_target = target
        if door_hex:
            for nb in door_hex.neighbors():
                if nb in grid and grid[nb].structure_type not in (
                    "house", "house_wall", "house_door",
                ):
                    road_target = nb
                    break
        _place_road(grid, center, road_target)

    # --- Farmland patches near edges ---
    num_farms = max(3, radius // 8)
    farm_offsets = []
    for fi in range(num_farms):
        angle_frac = fi / num_farms
        fq = int(radius * 0.5 * math.cos(angle_frac * 2 * math.pi))
        fr = int(radius * 0.35 * math.sin(angle_frac * 2 * math.pi))
        farm_offsets.append((fq, fr))
    for i, (fq, fr) in enumerate(farm_offsets):
        farm_center = Hex(fq, fr)
        if farm_center in grid and grid[farm_center].terrain_type != "water":
            farm_r = 2 + max(1, radius // 15) + _hash_hex(fq, fr, terrain_seed) % 2
            _place_farmland(grid, farm_center, farm_r, terrain_seed + i)
            if i % 2 == 0:
                gap_dir = directions_list[_hash_hex(fq, fr, terrain_seed + 99) % 6]
                _surround_with_fence(
                    grid, farm_center, farm_r + 1, gap_dir,
                )

    # --- Scatter cover objects near houses ---
    for hc in house_centers:
        for nb in hc.neighbors():
            if nb not in grid:
                continue
            st = grid[nb]
            if st.structure_type:
                continue
            h_val = _hash_hex(nb.q, nb.r, terrain_seed + 42)
            if h_val % 5 == 0:
                obj = "hay_bale" if h_val % 2 == 0 else "crate"
                _place_single_structure(grid, nb, obj, "wood")

    return grid


# ─── Terrain Overlays ─────────────────────────────────────────────────────────
# Premade shapes that a DM can stamp onto any encounter-area grid.
# Each overlay is a dict of relative Hex offsets → SpaceTerrain templates.
# After stamping, the surrounding procedural generation fills around it.


@dataclass
class TerrainOverlay:
    """A premade terrain/structure stamp that can be inserted into a grid.

    *hexes* maps relative ``Hex`` offsets (origin = Hex(0,0)) to
    ``SpaceTerrain`` objects.  The overlay is positioned, optionally
    rotated, and written onto a live grid via :func:`apply_overlay`.
    """
    name: str
    hexes: dict[Hex, SpaceTerrain] = field(default_factory=dict)
    description: str = ""


def _rotate_hex_60(h: Hex, steps: int) -> Hex:
    """Rotate *h* around origin by *steps* × 60° (clockwise).

    Uses cube-coordinate rotation:
      (q, r, s) → (-r, -s, -q)  per 60° step.
    """
    q, r = h.q, h.r
    s = -q - r
    for _ in range(steps % 6):
        q, r, s = -r, -s, -q
    return Hex(q, r)


def apply_overlay(
    grid: dict[Hex, SpaceTerrain],
    overlay: TerrainOverlay,
    center: Hex,
    rotation: int = 0,
) -> list[Hex]:
    """Stamp *overlay* onto *grid* centred at *center*.

    Parameters
    ----------
    grid
        The encounter area's ``{Hex: SpaceTerrain}`` mapping (mutated).
    overlay
        The premade template to insert.
    center
        Grid position where the overlay's origin (0, 0) is placed.
    rotation
        Number of 60° clockwise rotation steps (0–5).

    Returns
    -------
    list[Hex]
        Every grid hex that was written (useful for exclusion masks).
    """
    stamped: list[Hex] = []
    for rel_hex, template in overlay.hexes.items():
        rotated = _rotate_hex_60(rel_hex, rotation)
        target = Hex(center.q + rotated.q, center.r + rotated.r)
        if target not in grid:
            continue
        grid[target] = SpaceTerrain(
            terrain_type=template.terrain_type,
            elevation=template.elevation,
            moisture=template.moisture,
            cover=template.cover,
            lighting=template.lighting,
            structure_type=template.structure_type,
            building_id=template.building_id,
            building_shape=template.building_shape,
        )
        stamped.append(target)
    return stamped


# ─── Premade Overlay Builders ─────────────────────────────────────────────────
# Each function returns a TerrainOverlay ready to stamp.


def overlay_river(
    length: int = 40,
    width: int = 3,
    curve_seed: int = 0,
) -> TerrainOverlay:
    """A river flowing roughly north-to-south through an encounter area.

    *length* controls how many hex rows the river spans.
    *width* controls the river's width in hexes (1 = stream, 3 = river).
    *curve_seed* adds deterministic lateral meandering.
    """
    import random as _rng_mod
    rng = _rng_mod.Random(curve_seed)

    hexes: dict[Hex, SpaceTerrain] = {}
    center_q = 0
    half_w = width // 2

    for r_off in range(-length // 2, length // 2 + 1):
        # Meander: occasionally shift the centre column
        if rng.random() < 0.25:
            center_q += rng.choice([-1, 1])

        for dq in range(-half_w, half_w + 1):
            q = center_q + dq
            # Water tile
            hexes[Hex(q, r_off)] = SpaceTerrain(
                terrain_type="water",
                elevation=0.05,
                moisture=1.0,
                cover=Cover.NONE,
                lighting=Lighting.BRIGHT,
            )

        # Mud/marsh banks on each side
        for bank_dq in (-half_w - 1, half_w + 1):
            bq = center_q + bank_dq
            hexes[Hex(bq, r_off)] = SpaceTerrain(
                terrain_type="mud",
                elevation=0.15,
                moisture=0.8,
                cover=Cover.NONE,
                lighting=Lighting.BRIGHT,
            )

    return TerrainOverlay(
        name="river",
        hexes=hexes,
        description=f"River {width}w × {length}L, seed={curve_seed}",
    )


def overlay_pond(radius: int = 4) -> TerrainOverlay:
    """A small pond / lake with muddy banks."""
    center = Hex(0, 0)
    hexes: dict[Hex, SpaceTerrain] = {}
    for h in center.disk(radius):
        d = center.distance(h)
        if d <= radius - 1:
            hexes[h] = SpaceTerrain(
                terrain_type="water",
                elevation=0.05,
                moisture=1.0,
                cover=Cover.NONE,
                lighting=Lighting.BRIGHT,
            )
        else:
            hexes[h] = SpaceTerrain(
                terrain_type="mud",
                elevation=0.15,
                moisture=0.7,
                cover=Cover.NONE,
                lighting=Lighting.BRIGHT,
            )
    return TerrainOverlay(name="pond", hexes=hexes,
                          description=f"Pond r={radius}")


def overlay_bridge(length: int = 6) -> TerrainOverlay:
    """A wooden bridge spanning *length* hexes (place over a river)."""
    hexes: dict[Hex, SpaceTerrain] = {}
    half = length // 2
    for r_off in range(-half, half + 1):
        hexes[Hex(0, r_off)] = SpaceTerrain(
            terrain_type="wood",
            elevation=0.30,
            moisture=0.1,
            cover=Cover.NONE,
            lighting=Lighting.BRIGHT,
            structure_type="road",
        )
    return TerrainOverlay(name="bridge", hexes=hexes,
                          description=f"Bridge len={length}")


def overlay_shrine(radius: int = 4) -> TerrainOverlay:
    """A stone shrine / altar with surrounding stone plaza.

    *radius* controls the overall footprint (default 4 → ~61 hexes).
    The centre holds the altar, ring-1 is raised stone, ring-2 is
    stone floor, and the outer rings form a dirt/gravel transition.
    """
    center = Hex(0, 0)
    hexes: dict[Hex, SpaceTerrain] = {}
    for h in center.disk(radius):
        d = center.distance(h)
        if d == 0:
            # Altar at the very centre
            hexes[h] = SpaceTerrain(
                terrain_type="stone",
                elevation=0.50,
                moisture=0.1,
                cover=Cover.HALF,
                lighting=Lighting.BRIGHT,
                structure_type="well",  # re-uses the well visual (stone circle)
            )
        elif d == 1:
            # Inner ring: raised stone floor
            hexes[h] = SpaceTerrain(
                terrain_type="stone",
                elevation=0.45,
                moisture=0.1,
                cover=Cover.NONE,
                lighting=Lighting.BRIGHT,
            )
        elif d <= radius - 1:
            # Stone plaza
            hexes[h] = SpaceTerrain(
                terrain_type="stone",
                elevation=0.40,
                moisture=0.1,
                cover=Cover.NONE,
                lighting=Lighting.BRIGHT,
            )
        else:
            # Dirt / gravel transition at edge
            hexes[h] = SpaceTerrain(
                terrain_type="dirt",
                elevation=0.35,
                moisture=0.2,
                cover=Cover.NONE,
                lighting=Lighting.BRIGHT,
            )
    return TerrainOverlay(name="shrine", hexes=hexes,
                          description=f"Stone shrine r={radius}")


def overlay_grove(radius: int = 5, seed: int = 0) -> TerrainOverlay:
    """A copse of trees on grass — nature pocket for parks or forests."""
    import random as _rng_mod
    rng = _rng_mod.Random(seed)

    center = Hex(0, 0)
    hexes: dict[Hex, SpaceTerrain] = {}
    for h in center.disk(radius):
        d = center.distance(h)
        # Inner hexes get grass; outer ring has dirt transition
        if d <= radius - 1:
            hexes[h] = SpaceTerrain(
                terrain_type="grass",
                elevation=0.35 + rng.uniform(-0.05, 0.05),
                moisture=0.6 + rng.uniform(-0.1, 0.1),
                cover=Cover.HALF if rng.random() < 0.4 else Cover.NONE,
                lighting=Lighting.DIM if rng.random() < 0.3 else Lighting.BRIGHT,
            )
        else:
            hexes[h] = SpaceTerrain(
                terrain_type="dirt",
                elevation=0.30,
                moisture=0.4,
                cover=Cover.NONE,
                lighting=Lighting.BRIGHT,
            )
    return TerrainOverlay(name="grove", hexes=hexes,
                          description=f"Grove r={radius}")


def overlay_campsite() -> TerrainOverlay:
    """A small campsite with a fire pit and surrounding dirt."""
    center = Hex(0, 0)
    hexes: dict[Hex, SpaceTerrain] = {}
    # Centre: fire pit (re-uses lava visual for the coals)
    hexes[center] = SpaceTerrain(
        terrain_type="lava",
        elevation=0.28,
        moisture=0.0,
        cover=Cover.NONE,
        lighting=Lighting.BRIGHT,
    )
    # Ring of dirt around fire
    for nb in center.neighbors():
        hexes[nb] = SpaceTerrain(
            terrain_type="dirt",
            elevation=0.30,
            moisture=0.2,
            cover=Cover.NONE,
            lighting=Lighting.BRIGHT,
        )
    # Outer ring — some crates/supplies
    for nb2 in center.ring(2):
        hexes[nb2] = SpaceTerrain(
            terrain_type="dirt",
            elevation=0.30,
            moisture=0.2,
            cover=Cover.NONE,
            lighting=Lighting.BRIGHT,
        )
    return TerrainOverlay(name="campsite", hexes=hexes,
                          description="Small campsite with fire pit")


def overlay_city_river(
    city_hexes: list[Hex],
    radius: int = 60,
    width: int = 3,
    curve_seed: int = 0,
    flow_direction: str = "SE",
) -> list[tuple[Hex | None, TerrainOverlay, Hex, int]]:
    """Generate a river that flows through *multiple* city encounter areas.

    The river is traced in continuous world-space coordinates through the
    midpoints of shared area boundaries, producing a naturally curving
    path that connects seamlessly across encounter areas.

    Parameters
    ----------
    city_hexes
        Region-level hex positions of the city areas (from
        ``generate_city``).
    radius
        Encounter-area hex radius (must match ``generate_city``).
    width
        River width in hexes (1 = stream, 3 = river).
    curve_seed
        Deterministic seed for lateral meandering.
    flow_direction
        General compass direction the river flows toward.  One of
        ``"E"``, ``"NE"``, ``"NW"``, ``"W"``, ``"SW"``, ``"SE"``.

    Returns
    -------
    list[tuple]
        Ready-to-use ``(area_hex, overlay, center, rotation)`` entries
        for the ``overlays`` parameter of ``generate_city``.
    """
    import random as _rng_mod

    rng = _rng_mod.Random(curve_seed)
    half_w = width // 2

    _dir_map: dict[str, tuple[int, int]] = {
        "SE": (0, 1), "E": (1, 0), "NE": (1, -1),
        "NW": (0, -1), "W": (-1, 0), "SW": (-1, 1),
    }
    fq, fr = _dir_map.get(flow_direction, (0, 1))

    # Perpendicular direction for meander drift
    _perp: dict[str, tuple[int, int]] = {
        "SE": (1, 0), "E": (0, 1), "NE": (0, 1),
        "NW": (-1, 0), "W": (0, -1), "SW": (0, -1),
    }
    pq, pr = _perp.get(flow_direction, (1, 0))

    # --- Find the longest chain of connected areas ---
    city_set = set(city_hexes)
    adj: dict[Hex, list[Hex]] = {ah: [] for ah in city_hexes}
    for ah in city_hexes:
        for nb in ah.neighbors():
            if nb in city_set:
                adj[ah].append(nb)

    best_path: list[Hex] = []
    for start in city_hexes:
        stack: list[tuple[Hex, list[Hex], set[Hex]]] = [
            (start, [start], {start})
        ]
        while stack:
            cur, path, visited = stack.pop()
            if len(path) > len(best_path):
                best_path = list(path)
            for nb in adj[cur]:
                if nb not in visited:
                    stack.append((nb, path + [nb], visited | {nb}))

    # Orient path so it flows in the chosen direction
    def _flow_proj(ah: Hex) -> float:
        return ah.q * fq + ah.r * fr

    if _flow_proj(best_path[-1]) < _flow_proj(best_path[0]):
        best_path.reverse()

    # --- Build world-space hex sets per area ---
    world_hexes_per_area: dict[Hex, set[Hex]] = {}
    for ah in city_hexes:
        ws: set[Hex] = set()
        for h in Hex(0, 0).disk(radius):
            ws.add(_area_to_world(ah, h, radius))
        world_hexes_per_area[ah] = ws

    # --- Build waypoints at boundary midpoints ---
    # Instead of routing through area centres (which causes zigzag and
    # smearing), route through the midpoints of shared boundaries.
    # This produces a smooth, natural river course.
    def _hex_line(a: Hex, b: Hex) -> list[Hex]:
        """Bresenham-style hex line (cube-coord lerp)."""
        n = a.distance(b)
        if n == 0:
            return [a]
        results = []
        for i in range(n + 1):
            t = i / n
            fq_ = a.q + (b.q - a.q) * t
            fr_ = a.r + (b.r - a.r) * t
            fs = (-a.q - a.r) + ((-b.q - b.r) - (-a.q - a.r)) * t
            rq = round(fq_)
            rr = round(fr_)
            rs = round(fs)
            q_diff = abs(rq - fq_)
            r_diff = abs(rr - fr_)
            s_diff = abs(rs - fs)
            if q_diff > r_diff and q_diff > s_diff:
                rq = -rr - rs
            elif r_diff > s_diff:
                rr = -rq - rs
            results.append(Hex(rq, rr))
        return results

    waypoints: list[Hex] = []
    if len(best_path) == 1:
        # Single area — river goes through the centre
        c = best_path[0]
        wc = _area_to_world(c, Hex(0, 0), radius)
        waypoints.append(wc)
    else:
        # Waypoints at shared-boundary midpoints between consecutive areas
        for i in range(len(best_path) - 1):
            a, b = best_path[i], best_path[i + 1]
            wa = _area_to_world(a, Hex(0, 0), radius)
            wb = _area_to_world(b, Hex(0, 0), radius)
            waypoints.append(Hex(
                (wa.q + wb.q) // 2,
                (wa.r + wb.r) // 2,
            ))

    # Lead-in and lead-out so the river enters / exits at area edges
    lead = radius + 15
    if len(waypoints) >= 2:
        # Extend along the direction from second → first waypoint
        d0q = waypoints[0].q - waypoints[1].q
        d0r = waypoints[0].r - waypoints[1].r
        d0_len = max(1, Hex(0, 0).distance(Hex(d0q, d0r)))
        entry = Hex(
            waypoints[0].q + (d0q * lead) // d0_len,
            waypoints[0].r + (d0r * lead) // d0_len,
        )
        dlq = waypoints[-1].q - waypoints[-2].q
        dlr = waypoints[-1].r - waypoints[-2].r
        dl_len = max(1, Hex(0, 0).distance(Hex(dlq, dlr)))
        exit_ = Hex(
            waypoints[-1].q + (dlq * lead) // dl_len,
            waypoints[-1].r + (dlr * lead) // dl_len,
        )
    else:
        entry = Hex(waypoints[0].q - fq * lead, waypoints[0].r - fr * lead)
        exit_ = Hex(waypoints[0].q + fq * lead, waypoints[0].r + fr * lead)

    waypoints = [entry] + waypoints + [exit_]

    # Interpolate centreline through all waypoints
    centreline: list[Hex] = []
    for i in range(len(waypoints) - 1):
        seg = _hex_line(waypoints[i], waypoints[i + 1])
        if centreline and seg and seg[0] == centreline[-1]:
            seg = seg[1:]
        centreline.extend(seg)

    # --- Apply sinusoidal meander to the centreline ---
    # Offset each point perpendicular to flow using layered sine waves.
    # This gives the river natural-looking S-curves.
    import math
    area_span = 2 * radius  # world-space distance between adjacent area centres
    amplitude = radius * 0.35          # max lateral swing in hexes
    freq1 = 2.0 * math.pi / (area_span * 1.1)   # primary wave (~1 period per area)
    freq2 = 2.0 * math.pi / (area_span * 0.45)  # secondary harmonic (tighter bends)
    phase1 = rng.uniform(0, 2.0 * math.pi)
    phase2 = rng.uniform(0, 2.0 * math.pi)

    meandered: list[Hex] = []
    for idx, ctr in enumerate(centreline):
        t = float(idx)
        offset = (amplitude * math.sin(freq1 * t + phase1)
                  + amplitude * 0.35 * math.sin(freq2 * t + phase2))
        off_int = int(round(offset))
        meandered.append(Hex(ctr.q + pq * off_int, ctr.r + pr * off_int))
    centreline = meandered

    # --- Stamp river using isotropic disk-based width ---
    # Using disk() at each centreline point makes the river width
    # uniform regardless of flow direction changes — no smearing.
    river_world: dict[Hex, SpaceTerrain] = {}
    for ctr in centreline:
        # Water: all hexes within half_w of centreline
        for h in ctr.disk(half_w):
            river_world[h] = SpaceTerrain(
                terrain_type="water",
                elevation=0.05,
                moisture=1.0,
                cover=Cover.NONE,
                lighting=Lighting.BRIGHT,
            )

        # Mud banks: ring just outside the water
        for h in ctr.ring(half_w + 1):
            if h not in river_world:
                river_world[h] = SpaceTerrain(
                    terrain_type="mud",
                    elevation=0.15,
                    moisture=0.8,
                    cover=Cover.NONE,
                    lighting=Lighting.BRIGHT,
                )

    # --- Place bridges at area-boundary crossings -------------------------
    # Find the centreline hex closest to each boundary midpoint (waypoint).
    # The bridge spans the river perpendicular to the local flow direction,
    # and is 6 hexes wide (along the flow) — slightly wider than main roads.
    bridge_world: dict[Hex, SpaceTerrain] = {}
    # Waypoints[0] and waypoints[-1] are lead-in/out; real crossings are [1:-1]
    crossing_waypoints = waypoints[1:-1]
    bridge_length = width + 4  # span across river + approaches
    bridge_road_width = 6      # hexes wide along flow direction

    for wp in crossing_waypoints:
        # Find the closest centreline hex to this waypoint
        best_idx = 0
        best_d = 99999
        for ci, ch in enumerate(centreline):
            d = ch.distance(wp)
            if d < best_d:
                best_d = d
                best_idx = ci

        bridge_ctr = centreline[best_idx]

        # Determine local flow direction from centreline neighbours
        prev_idx = max(0, best_idx - 5)
        next_idx = min(len(centreline) - 1, best_idx + 5)
        flow_q = centreline[next_idx].q - centreline[prev_idx].q
        flow_r = centreline[next_idx].r - centreline[prev_idx].r

        fpx, fpy = hex_to_pixel(Hex(flow_q, flow_r), 1.0)
        flow_len = max(0.001, math.sqrt(fpx * fpx + fpy * fpy))
        # Perpendicular in pixel space: (-fpy, fpx)
        perp_px, perp_py = -fpy / flow_len, fpx / flow_len

        # Find hex direction closest to the perpendicular (bridge span)
        best_dir = "E"
        best_dot = -999
        for dname in DIRECTION_LIST:
            dq, dr = DIRECTIONS[dname]
            dpx, dpy = hex_to_pixel(Hex(dq, dr), 1.0)
            dlen = max(0.001, math.sqrt(dpx * dpx + dpy * dpy))
            dot = (dpx / dlen) * perp_px + (dpy / dlen) * perp_py
            if dot > best_dot:
                best_dot = dot
                best_dir = dname

        # Find hex direction closest to the flow (bridge width)
        flow_dir = "E"
        best_flow_dot = -999
        fpx_n, fpy_n = fpx / flow_len, fpy / flow_len
        for dname in DIRECTION_LIST:
            dq, dr = DIRECTIONS[dname]
            dpx, dpy = hex_to_pixel(Hex(dq, dr), 1.0)
            dlen = max(0.001, math.sqrt(dpx * dpx + dpy * dpy))
            dot = (dpx / dlen) * fpx_n + (dpy / dlen) * fpy_n
            if dot > best_flow_dot:
                best_flow_dot = dot
                flow_dir = dname

        # Stamp bridge as a wide strip: span × width
        bdq, bdr = DIRECTIONS[best_dir]
        fdq, fdr = DIRECTIONS[flow_dir]
        half_b = bridge_length // 2
        half_w = bridge_road_width // 2
        for step in range(-half_b, half_b + 1):
            for w in range(-half_w, half_w + 1):
                bh = Hex(
                    bridge_ctr.q + bdq * step + fdq * w,
                    bridge_ctr.r + bdr * step + fdr * w,
                )
                bridge_world[bh] = SpaceTerrain(
                    terrain_type="wood",
                    elevation=0.30,
                    moisture=0.1,
                    cover=Cover.NONE,
                    lighting=Lighting.BRIGHT,
                    structure_type="road",
                )

    # Merge bridge hexes into river_world (bridges overwrite water)
    river_world.update(bridge_world)

    # --- Slice into per-area local overlays ---
    overlays: list[tuple[Hex | None, TerrainOverlay, Hex, int]] = []
    for ah in city_hexes:
        area_hexes: dict[Hex, SpaceTerrain] = {}
        ws = world_hexes_per_area[ah]
        for wh, st in river_world.items():
            if wh in ws:
                local = _world_to_local(ah, wh, radius)
                area_hexes[local] = st
        if area_hexes:
            ovl = TerrainOverlay(
                name=f"river_{ah.q}_{ah.r}",
                hexes=area_hexes,
                description=f"River segment for area ({ah.q},{ah.r})",
            )
            overlays.append((ah, ovl, Hex(0, 0), 0))

    return overlays


# ─── Encounter Manager ───────────────────────────────────────────────────────

class EncounterManager:
    """Manages encounter area generation with automatic edge consistency.

    Tracks generated encounter areas within a region. When a new area is
    generated, the manager extracts edge profiles from already-generated
    neighbours and passes them as constraints, ensuring that terrain
    features at shared boundaries match.

    Usage::

        region = generate_region_terrain(0, 0, world_seed=42)
        mgr = EncounterManager(region, radius=15)

        # Generate areas in any order — each new area checks its neighbours
        area_a = mgr.generate(Hex(0, 0), terrain_seed=100)
        area_b = mgr.generate(Hex(1, 0), terrain_seed=101)  # east of A
        # B's west edge now matches A's east edge
    """

    def __init__(
        self,
        region: dict[Hex, TerrainData],
        radius: int = 60,
    ) -> None:
        self.region = region
        self.radius = radius
        self._areas: dict[Hex, dict[Hex, SpaceTerrain]] = {}
        self._shared: dict[Hex, dict[str, list[SpaceTerrain]]] = {}

    @property
    def areas(self) -> dict[Hex, dict[Hex, SpaceTerrain]]:
        """All generated encounter areas keyed by region hex."""
        return self._areas

    def generate(
        self,
        region_hex: Hex,
        terrain_seed: int,
        radius: int | None = None,
    ) -> dict[Hex, SpaceTerrain]:
        """Generate (or return cached) space terrain for *region_hex*.

        Automatically applies shared edges from any already-generated
        neighbour areas.
        """
        if region_hex in self._areas:
            return self._areas[region_hex]

        r = radius or self.radius
        region_data = self.region.get(region_hex)
        biome = region_data.biome if region_data else "plains"

        # Collect shared edge data from generated neighbours
        shared: dict[str, list[SpaceTerrain]] = {}
        for dname in DIRECTION_LIST:
            dq, dr = DIRECTIONS[dname]
            nb_hex = Hex(region_hex.q + dq, region_hex.r + dr)
            if nb_hex not in self._areas:
                continue
            opp = OPPOSITE_DIR[dname]
            nb_shared = self._shared.get(nb_hex, {})
            if opp in nb_shared:
                shared[dname] = nb_shared[opp]

        terrain = generate_space_terrain(
            terrain_seed, biome, r,
            shared_edges=shared if shared else None,
        )

        self._areas[region_hex] = terrain
        self._shared[region_hex] = extract_shared_edges(terrain, r)
        return terrain
