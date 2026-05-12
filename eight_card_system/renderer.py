"""
Hex map renderer for the Eight Card System.

Renders hex grids at two scales:
  - Region map: Encounter Areas as hex tiles (37 hexes, radius 3)
  - Encounter map: Spaces as hex tiles (~3,900 hexes per z-level)

Outputs PIL Image objects suitable for saving to PNG or posting to Discord.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from .hex_math import Hex, hex_to_pixel, hex_corners

from .terrain_gen import Cover, Lighting


# ─── Color Palettes ──────────────────────────────────────────────────────────

BIOME_COLORS = {
    "forest":    (34, 139, 34),
    "desert":    (237, 201, 126),
    "tundra":    (200, 220, 230),
    "swamp":     (85, 107, 47),
    "plains":    (154, 205, 50),
    "mountain":  (139, 137, 137),
    "coastal":   (70, 130, 180),
    "urban":     (160, 160, 160),
    "dungeon":   (60, 60, 60),
    "ocean":     (30, 80, 140),
    "lake":      (50, 120, 180),
    "river":     (65, 135, 195),
    "hills":     (120, 160, 80),
    "jungle":    (0, 100, 0),
    "savanna":   (195, 176, 90),
    "taiga":     (50, 100, 70),
    "ice":       (220, 235, 245),
    "volcanic":  (80, 20, 20),
}

TERRAIN_COLORS = {
    "grass":   (115, 140, 58),   # vivid green
    "stone":   (168, 165, 160),  # cool neutral grey
    "water":   (48, 100, 130),   # rich teal-blue
    "mud":     (120, 95, 62),    # warm brown
    "sand":    (210, 190, 148),  # bright warm sand
    "wood":    (145, 110, 65),   # light wood brown
    "ice":     (195, 215, 228),  # bright blue-grey
    "lava":    (190, 65, 25),    # bright red-orange
    "void":    (22, 20, 16),     # near-black warm
    "dirt":    (145, 120, 78),   # light earthy brown
    "snow":    (228, 228, 225),  # cool off-white
    "marsh":   (82, 110, 52),    # rich olive-green
    "cobblestone": (165, 158, 148), # flat grey paving — no rock sprites
    "gravel":      (128, 125, 105),  # grey-brown with green tinge, city ground
}

FOG_COLOR = (30, 28, 20, 170)  # warm dark overlay
GRID_COLOR = (50, 45, 35)
GRID_COLOR_LIGHT = (90, 85, 75)
HIGHLIGHT_COLOR = (255, 215, 0)  # gold for player position
POI_COLOR = (200, 100, 50)
BACKGROUND_COLOR = (32, 28, 22)
LABEL_COLOR = (220, 215, 200)
BOUNDARY_COLOR = (65, 58, 45)

# Structure-specific colors (override terrain color when structure is present)
STRUCTURE_COLORS: dict[str, tuple[int, int, int]] = {
    "house":        (148, 115, 70),   # wood floor interior
    "house_wall":   (148, 115, 70),   # same as floor — wall line drawn on edge only
    "house_door":   (148, 115, 70),   # same as floor — door drawn as gap in wall
    "fence":        (148, 122, 75),   # weathered wood
    "well":         (115, 110, 100),  # grey stone
    "farmland":     (80, 88, 45),     # tilled rows, slightly different from grass
    "road":         (165, 158, 148),  # same as cobblestone — defined by edge walls
    "hay_bale":     (195, 175, 90),   # golden straw
    "crate":        (150, 120, 70),   # wooden crate
    "cart":         (135, 108, 65),   # cart wood
    "wagon":        (140, 112, 65),   # wagon wood
    "market_stall": (155, 128, 78),   # stall wood
    "barrel":       (120, 95, 55),    # dark wood barrel
    "box_stack":    (145, 118, 68),   # stacked crates
    "awning":       (165, 158, 148),  # same as cobblestone base (canopy drawn on top)
    "city_wall_stone":  (130, 125, 115),  # grey stone fortification
    "city_wall_lumber": (140, 115, 75),   # heavy timber palisade
    "cobblestone":      (165, 158, 148),  # flat paving (structure color fallback)
    "gravel":           (128, 125, 105),  # grey-brown ground (structure color fallback)
    "road_wall":    (145, 140, 130),  # low stone wall along road
    "street_lamp":  (165, 158, 148),  # cobblestone base — lamp drawn on top
    "watchtower":   (140, 120, 85),   # tower interior
    "tree":         (45, 90, 30),     # canopy green — drawn by structure symbol
    "bed":          (148, 115, 70),   # wood floor (shape drawn at building level)
    "table":        (148, 115, 70),   # wood floor (shape drawn at building level)
    "chair":        (148, 115, 70),   # wood floor (shape drawn at building level)
    "desk":         (148, 115, 70),   # wood floor (shape drawn at building level)
    "rug":          (148, 115, 70),   # wood floor (shape drawn at building level)
    "bookshelf":    (148, 115, 70),   # wood floor (shape drawn at building level)
    "chest":        (148, 115, 70),   # wood floor (shape drawn at building level)
}


# ─── Per-Style Structure Palettes ─────────────────────────────────────────────

@dataclass(frozen=True)
class StructurePalette:
    """Color palette for all structure sprites, themed per city style."""

    # ── Wood (crate, cart, wagon, fence, box_stack, chest, desk, building planks) ──
    wood: tuple[tuple[int, int, int], ...]
    # ── Barrel body ──
    barrel: tuple[tuple[int, int, int], ...]
    barrel_band: tuple[int, int, int]
    # ── Hay / straw ──
    hay: tuple[tuple[int, int, int], ...]
    # ── Stone (road_wall, well, watchtower) ──
    stone: tuple[int, int, int]
    stone_dark: tuple[int, int, int]
    stone_cap: tuple[int, int, int]
    # ── Fabric / canopy (market stalls, awnings, rugs) ──
    fabric: tuple[tuple[int, int, int], ...]
    # ── Market stall table and goods ──
    table: tuple[int, int, int]
    table_dark: tuple[int, int, int]
    goods: tuple[tuple[int, int, int], ...]
    # ── Awning support poles ──
    pole: tuple[int, int, int]
    # ── Tree / nature ──
    tree_canopy: tuple[tuple[int, int, int], ...]
    tree_trunk: tuple[int, int, int]
    # ── Lamp ──
    lamp_post: tuple[int, int, int]
    lamp_glow: tuple[int, int, int]
    # ── Interior details ──
    pillow: tuple[int, int, int]
    paper: tuple[int, int, int]
    book_spines: tuple[tuple[int, int, int], ...]
    chest_clasp: tuple[int, int, int]
    # ── Wheel (cart & wagon) ──
    wheel: tuple[int, int, int]
    wheel_rim: tuple[int, int, int]
    # ── Wagon cargo ──
    cargo: tuple[tuple[int, int, int], ...]
    # ── Building / fence / door ──
    plank_line: tuple[int, int, int]
    door: tuple[int, int, int]
    door_dark: tuple[int, int, int]
    fence: tuple[int, int, int]
    # ── Well water ──
    well_water: tuple[int, int, int]
    # ── Farmland rows ──
    farm_rows: tuple[int, int, int]
    # ── Base hex fills (override STRUCTURE_COLORS per style) ──
    hex_fills: dict[str, tuple[int, int, int]] = field(default_factory=dict)


def _structure_colors_for_palette(
    palette: StructurePalette,
) -> dict[str, tuple[int, int, int]]:
    """Build a STRUCTURE_COLORS dict from a palette, falling back to defaults."""
    merged = dict(STRUCTURE_COLORS)
    merged.update(palette.hex_fills)
    return merged


STRUCTURE_PALETTES: dict[str, StructurePalette] = {
    # ── Human: warm browns, grey stone, iron bands, bold primary canopies ──
    "human": StructurePalette(
        wood=(
            (145, 115, 60), (138, 108, 52), (155, 120, 65),
            (132, 105, 50), (148, 118, 58), (140, 100, 48),
        ),
        barrel=(
            (130, 100, 58), (122, 92, 48), (140, 108, 62),
            (125, 88, 45), (138, 102, 55), (118, 85, 42),
        ),
        barrel_band=(145, 145, 138),
        hay=(
            (195, 175, 70), (205, 185, 80), (185, 165, 55),
            (200, 178, 65), (190, 170, 58),
        ),
        stone=(125, 120, 108),
        stone_dark=(95, 90, 78),
        stone_cap=(140, 135, 122),
        fabric=(
            (185, 55, 45), (45, 115, 165), (175, 145, 40),
            (55, 140, 70), (165, 85, 45), (135, 65, 135),
            (165, 75, 95), (85, 130, 110), (178, 110, 55),
            (95, 85, 145), (170, 88, 75), (58, 120, 90),
            (180, 52, 42), (42, 105, 155), (170, 140, 38),
        ),
        table=(160, 132, 82),
        table_dark=(115, 92, 55),
        goods=(
            (200, 50, 40), (240, 200, 50), (50, 160, 80),
            (200, 140, 60), (180, 80, 120), (100, 170, 150),
        ),
        pole=(85, 70, 45),
        tree_canopy=(
            (45, 90, 30), (38, 82, 25), (52, 98, 35),
            (40, 85, 28), (48, 95, 32),
        ),
        tree_trunk=(75, 58, 35),
        lamp_post=(55, 50, 42),
        lamp_glow=(230, 210, 100),
        pillow=(200, 195, 182),
        paper=(210, 205, 190),
        book_spines=((140, 45, 35), (35, 65, 120), (45, 100, 50)),
        chest_clasp=(170, 165, 148),
        wheel=(90, 70, 35),
        wheel_rim=(60, 45, 20),
        cargo=(
            (170, 150, 95), (115, 88, 48), (155, 130, 75),
            (95, 80, 50), (180, 165, 110),
        ),
        plank_line=(125, 95, 55),
        door=(160, 110, 55),
        door_dark=(120, 80, 35),
        fence=(130, 100, 45),
        well_water=(50, 120, 200),
        farm_rows=(95, 75, 40),
    ),

    # ── Elven: light birch wood, white marble, silver, soft pastels ──
    "elven": StructurePalette(
        wood=(
            (195, 180, 150), (188, 172, 142), (202, 188, 155),
            (182, 168, 138), (198, 185, 148), (190, 175, 145),
        ),
        barrel=(
            (175, 162, 130), (168, 155, 122), (182, 170, 135),
            (165, 150, 118), (178, 165, 128), (170, 158, 125),
        ),
        barrel_band=(192, 195, 205),
        hay=(
            (210, 200, 125), (218, 208, 132), (202, 192, 118),
            (215, 205, 128), (208, 198, 122),
        ),
        stone=(195, 192, 188),
        stone_dark=(165, 162, 155),
        stone_cap=(215, 212, 208),
        fabric=(
            (145, 185, 155), (155, 165, 195), (185, 165, 195),
            (165, 195, 175), (195, 185, 165), (148, 178, 188),
            (175, 155, 185), (165, 192, 165), (188, 175, 158),
            (155, 188, 178), (178, 165, 175), (162, 185, 155),
            (142, 175, 168), (168, 158, 192), (185, 178, 162),
        ),
        table=(185, 172, 145),
        table_dark=(155, 142, 115),
        goods=(
            (160, 195, 145), (195, 185, 140), (140, 175, 195),
            (185, 155, 165), (155, 195, 165), (175, 165, 145),
        ),
        pole=(155, 145, 118),
        tree_canopy=(
            (55, 115, 48), (48, 108, 42), (62, 122, 55),
            (50, 110, 45), (58, 118, 50),
        ),
        tree_trunk=(95, 82, 55),
        lamp_post=(165, 162, 155),
        lamp_glow=(200, 225, 180),
        pillow=(225, 222, 215),
        paper=(228, 225, 218),
        book_spines=((85, 125, 95), (95, 105, 145), (125, 115, 85)),
        chest_clasp=(195, 198, 205),
        wheel=(145, 132, 102),
        wheel_rim=(118, 105, 78),
        cargo=(
            (195, 185, 148), (165, 152, 118), (185, 175, 138),
            (148, 138, 108), (205, 195, 158),
        ),
        plank_line=(165, 148, 115),
        door=(178, 162, 128),
        door_dark=(148, 132, 98),
        fence=(175, 162, 130),
        well_water=(75, 155, 195),
        farm_rows=(108, 125, 78),
        hex_fills={
            "house": (188, 172, 142), "house_wall": (188, 172, 142),
            "house_door": (188, 172, 142), "bed": (188, 172, 142),
            "table": (188, 172, 142), "chair": (188, 172, 142),
            "desk": (188, 172, 142), "rug": (188, 172, 142),
            "bookshelf": (188, 172, 142), "chest": (188, 172, 142),
            "fence": (192, 178, 148), "well": (175, 172, 165),
            "crate": (195, 178, 145), "barrel": (172, 158, 128),
            "cart": (185, 168, 135), "wagon": (188, 172, 138),
            "box_stack": (190, 175, 142), "hay_bale": (215, 205, 130),
            "market_stall": (195, 180, 152), "watchtower": (195, 188, 178),
            "tree": (55, 115, 48),
            "city_wall_stone": (188, 185, 178),
            "city_wall_lumber": (185, 168, 135),
            "road_wall": (192, 188, 182),
        },
    ),

    # ── Orc: crude dark wood, dark stone, black iron, blood/war colours ──
    "orc": StructurePalette(
        wood=(
            (95, 75, 42), (88, 68, 35), (102, 82, 48),
            (82, 62, 32), (98, 78, 45), (90, 70, 38),
        ),
        barrel=(
            (82, 65, 38), (75, 58, 32), (88, 72, 42),
            (70, 55, 28), (85, 68, 40), (78, 62, 35),
        ),
        barrel_band=(62, 60, 55),
        hay=(
            (155, 138, 55), (162, 145, 62), (148, 132, 48),
            (158, 142, 58), (152, 135, 52),
        ),
        stone=(88, 82, 72),
        stone_dark=(58, 55, 48),
        stone_cap=(105, 98, 85),
        fabric=(
            (145, 32, 28), (95, 28, 22), (125, 42, 32),
            (85, 72, 35), (155, 45, 35), (72, 25, 25),
            (108, 35, 28), (138, 52, 42), (112, 32, 25),
            (78, 65, 32), (148, 38, 30), (95, 30, 25),
            (135, 48, 38), (88, 25, 22), (118, 38, 30),
        ),
        table=(105, 85, 52),
        table_dark=(72, 58, 35),
        goods=(
            (155, 42, 35), (115, 95, 42), (82, 105, 55),
            (165, 108, 42), (95, 55, 42), (125, 82, 48),
        ),
        pole=(65, 55, 38),
        tree_canopy=(
            (35, 68, 28), (28, 60, 22), (42, 75, 32),
            (30, 62, 25), (38, 72, 30),
        ),
        tree_trunk=(58, 45, 28),
        lamp_post=(48, 42, 35),
        lamp_glow=(195, 125, 45),
        pillow=(118, 105, 82),
        paper=(128, 115, 92),
        book_spines=((115, 32, 28), (42, 55, 72), (68, 85, 42)),
        chest_clasp=(72, 68, 62),
        wheel=(62, 48, 28),
        wheel_rim=(42, 32, 18),
        cargo=(
            (108, 92, 58), (75, 62, 38), (98, 82, 52),
            (65, 52, 32), (118, 102, 65),
        ),
        plank_line=(75, 58, 32),
        door=(98, 72, 42),
        door_dark=(68, 48, 28),
        fence=(85, 65, 38),
        well_water=(38, 82, 95),
        farm_rows=(72, 58, 32),
        hex_fills={
            "house": (88, 72, 45), "house_wall": (88, 72, 45),
            "house_door": (88, 72, 45), "bed": (88, 72, 45),
            "table": (88, 72, 45), "chair": (88, 72, 45),
            "desk": (88, 72, 45), "rug": (88, 72, 45),
            "bookshelf": (88, 72, 45), "chest": (88, 72, 45),
            "fence": (92, 75, 48), "well": (78, 72, 62),
            "crate": (98, 82, 52), "barrel": (78, 62, 38),
            "cart": (88, 72, 45), "wagon": (92, 75, 48),
            "box_stack": (95, 78, 50), "hay_bale": (158, 142, 58),
            "market_stall": (102, 85, 55), "watchtower": (82, 75, 62),
            "tree": (35, 68, 28),
            "city_wall_stone": (78, 72, 62),
            "city_wall_lumber": (82, 65, 42),
            "road_wall": (85, 78, 68),
        },
    ),

    # ── Goblin: scavenged mismatched wood, dirty grey-brown, rusty iron, chaotic ──
    "goblin": StructurePalette(
        wood=(
            (158, 138, 85), (148, 128, 78), (165, 145, 92),
            (142, 122, 72), (155, 135, 88), (150, 130, 80),
        ),
        barrel=(
            (142, 122, 72), (135, 115, 65), (148, 128, 78),
            (128, 108, 58), (145, 125, 75), (138, 118, 68),
        ),
        barrel_band=(135, 105, 72),
        hay=(
            (175, 158, 72), (182, 165, 78), (168, 152, 65),
            (178, 162, 75), (172, 155, 68),
        ),
        stone=(128, 118, 92),
        stone_dark=(95, 88, 68),
        stone_cap=(145, 135, 108),
        fabric=(
            (148, 175, 42), (195, 155, 32), (185, 115, 35),
            (125, 168, 48), (205, 145, 55), (145, 82, 135),
            (165, 185, 45), (178, 128, 38), (155, 162, 42),
            (195, 138, 48), (138, 172, 52), (208, 162, 42),
            (152, 178, 48), (188, 118, 35), (142, 165, 45),
        ),
        table=(148, 128, 82),
        table_dark=(112, 95, 58),
        goods=(
            (175, 155, 42), (195, 92, 35), (108, 165, 55),
            (215, 172, 48), (145, 125, 42), (185, 135, 52),
        ),
        pole=(108, 92, 58),
        tree_canopy=(
            (52, 85, 32), (45, 78, 25), (58, 92, 38),
            (48, 82, 28), (55, 88, 35),
        ),
        tree_trunk=(82, 65, 38),
        lamp_post=(95, 82, 58),
        lamp_glow=(185, 195, 75),
        pillow=(168, 155, 128),
        paper=(175, 162, 135),
        book_spines=((142, 82, 35), (55, 95, 48), (108, 72, 95)),
        chest_clasp=(138, 108, 75),
        wheel=(105, 85, 52),
        wheel_rim=(78, 62, 35),
        cargo=(
            (148, 132, 85), (108, 92, 58), (135, 118, 75),
            (95, 78, 48), (158, 142, 92),
        ),
        plank_line=(122, 102, 58),
        door=(148, 118, 68),
        door_dark=(112, 85, 48),
        fence=(135, 112, 65),
        well_water=(55, 105, 85),
        farm_rows=(108, 92, 48),
        hex_fills={
            "house": (148, 128, 78), "house_wall": (148, 128, 78),
            "house_door": (148, 128, 78), "bed": (148, 128, 78),
            "table": (148, 128, 78), "chair": (148, 128, 78),
            "desk": (148, 128, 78), "rug": (148, 128, 78),
            "bookshelf": (148, 128, 78), "chest": (148, 128, 78),
            "fence": (152, 132, 82), "well": (122, 112, 88),
            "crate": (155, 135, 85), "barrel": (138, 118, 72),
            "cart": (145, 125, 78), "wagon": (148, 128, 80),
            "box_stack": (150, 132, 82), "hay_bale": (178, 162, 75),
            "market_stall": (158, 138, 88), "watchtower": (135, 125, 98),
            "tree": (52, 85, 32),
            "city_wall_stone": (125, 115, 88),
            "city_wall_lumber": (142, 122, 75),
            "road_wall": (132, 122, 95),
        },
    ),

    # ── Tiefling: dark polished wood, obsidian, burnished bronze, crimson/purple ──
    "tiefling": StructurePalette(
        wood=(
            (72, 52, 42), (65, 45, 35), (78, 58, 48),
            (60, 42, 32), (75, 55, 45), (68, 48, 38),
        ),
        barrel=(
            (62, 45, 35), (55, 38, 28), (68, 52, 42),
            (50, 35, 25), (65, 48, 38), (58, 42, 32),
        ),
        barrel_band=(108, 82, 62),
        hay=(
            (125, 112, 55), (132, 118, 62), (118, 105, 48),
            (128, 115, 58), (122, 108, 52),
        ),
        stone=(62, 58, 55),
        stone_dark=(38, 35, 32),
        stone_cap=(78, 72, 68),
        fabric=(
            (145, 28, 42), (82, 28, 115), (115, 22, 35),
            (68, 35, 108), (155, 35, 48), (95, 25, 98),
            (125, 25, 38), (75, 32, 112), (138, 30, 45),
            (88, 28, 105), (148, 32, 52), (72, 25, 102),
            (132, 28, 40), (78, 30, 108), (142, 35, 48),
        ),
        table=(78, 62, 48),
        table_dark=(52, 42, 32),
        goods=(
            (155, 35, 42), (85, 45, 125), (55, 115, 82),
            (175, 125, 42), (65, 35, 105), (48, 95, 72),
        ),
        pole=(48, 40, 32),
        tree_canopy=(
            (25, 48, 32), (18, 42, 25), (32, 55, 38),
            (22, 45, 28), (28, 52, 35),
        ),
        tree_trunk=(42, 32, 22),
        lamp_post=(32, 28, 25),
        lamp_glow=(145, 65, 175),
        pillow=(92, 82, 72),
        paper=(102, 92, 82),
        book_spines=((115, 25, 38), (32, 28, 85), (65, 25, 82)),
        chest_clasp=(115, 88, 65),
        wheel=(45, 35, 25),
        wheel_rim=(28, 22, 15),
        cargo=(
            (82, 68, 52), (52, 42, 32), (72, 58, 45),
            (42, 35, 25), (92, 78, 58),
        ),
        plank_line=(52, 38, 28),
        door=(75, 55, 42),
        door_dark=(48, 35, 25),
        fence=(62, 48, 35),
        well_water=(25, 55, 95),
        farm_rows=(48, 38, 25),
        hex_fills={
            "house": (65, 52, 42), "house_wall": (65, 52, 42),
            "house_door": (65, 52, 42), "bed": (65, 52, 42),
            "table": (65, 52, 42), "chair": (65, 52, 42),
            "desk": (65, 52, 42), "rug": (65, 52, 42),
            "bookshelf": (65, 52, 42), "chest": (65, 52, 42),
            "fence": (68, 55, 42), "well": (55, 52, 48),
            "crate": (72, 58, 48), "barrel": (58, 45, 35),
            "cart": (65, 52, 42), "wagon": (68, 55, 45),
            "box_stack": (70, 58, 45), "hay_bale": (128, 115, 58),
            "market_stall": (75, 62, 50), "watchtower": (62, 58, 52),
            "tree": (25, 48, 32),
            "city_wall_stone": (55, 52, 48),
            "city_wall_lumber": (58, 45, 35),
            "road_wall": (60, 55, 52),
            "street_lamp": (52, 48, 45),
            "road": (52, 48, 45),
            "awning": (52, 48, 45),
        },
    ),
}


# Cover indicator colors
COVER_COLORS: dict = {
    Cover.QUARTER:       (200, 200, 50, 100),
    Cover.HALF:          (230, 160, 40, 120),
    Cover.THREE_QUARTER: (230, 100, 30, 140),
    Cover.FULL:          (180, 60, 60, 160),
}

# Lighting overlay colors
LIGHTING_OVERLAYS: dict = {
    Lighting.DIM:  (30, 20, 50, 70),
    Lighting.DARK: (10, 5, 20, 140),
}


# ─── Terrain Feature Definitions ─────────────────────────────────────────────
# Maps terrain_type (or biome name) → (symbol, count, symbol_color)

TERRAIN_FEATURES: dict[str, tuple[str, int, tuple[int, int, int]]] = {
    # Space-level terrain types — vivid, visible sprite colors
    "grass":    ("grass_tuft", 6, (70, 110, 30)),
    "stone":    ("rock",  1, (150, 148, 142)),
    "cobblestone": ("dot", 0, (0, 0, 0)),  # flat paving — no features
    "gravel":      ("grass_tuft", 4, (75, 105, 40)),  # sparse grass tufts on gravel
    "water":    ("wave",  0, (60, 120, 160)),   # water uses custom layered rendering
    "sand":     ("dot",   6, (185, 168, 125)),
    "snow":     ("flake", 3, (220, 230, 240)),
    "marsh":    ("reed",  5, (55, 95, 28)),
    "mud":      ("dot",   3, (100, 78, 48)),
    "ice":      ("flake", 3, (190, 210, 230)),
    "lava":     ("dot",   4, (220, 110, 30)),
    "dirt":     ("dot",   3, (120, 100, 65)),
    # Region-level biome names
    "forest":   ("tree",  4, (35, 80, 22)),
    "jungle":   ("tree",  6, (20, 70, 12)),
    "desert":   ("dot",   5, (195, 178, 130)),
    "mountain": ("peak",  2, (110, 105, 92)),
    "swamp":    ("reed",  5, (45, 80, 25)),
    "ocean":    ("wave",  0, (40, 80, 130)),
    "lake":     ("wave",  0, (45, 90, 140)),
    "plains":   ("grass_tuft", 6, (65, 105, 28)),
    "hills":    ("bush",  3, (65, 110, 42)),
    "tundra":   ("flake", 5, (200, 220, 235)),
    "taiga":    ("tree",  3, (30, 72, 28)),
    "coastal":  ("wave",  0, (55, 100, 140)),
    "savanna":  ("grass_tuft", 4, (145, 150, 48)),
    "volcanic": ("rock",  3, (140, 50, 18)),
    "urban":    ("dot",   2, (130, 125, 112)),
    "dungeon":  ("rock",  3, (85, 78, 65)),
    # Structure types
    "house":        ("dot",  0, (0, 0, 0)),
    "house_wall":   ("dot",  0, (0, 0, 0)),
    "house_door":   ("dot",  0, (0, 0, 0)),
    "fence":        ("dot",  0, (0, 0, 0)),
    "well":         ("dot",  0, (0, 0, 0)),
    "farmland":     ("blade",4, (70, 100, 35)),
    "road":         ("dot",  0, (0, 0, 0)),
    "hay_bale":     ("dot",  0, (0, 0, 0)),
    "crate":        ("dot",  0, (0, 0, 0)),
    "cart":         ("dot",  0, (0, 0, 0)),
    "wagon":        ("dot",  0, (0, 0, 0)),
    "market_stall": ("dot",  0, (0, 0, 0)),
    "barrel":       ("dot",  0, (0, 0, 0)),
    "box_stack":    ("dot",  0, (0, 0, 0)),
    "awning":       ("dot",  0, (0, 0, 0)),
    "road_wall":    ("dot",  0, (0, 0, 0)),
    "street_lamp":  ("dot",  0, (0, 0, 0)),
}


# ─── Visual Helpers ───────────────────────────────────────────────────────────

def _shade_color(
    color: tuple[int, int, int], elevation: float, intensity: float = 0.25,
) -> tuple[int, int, int]:
    """Darken/lighten a color based on elevation (0=low/dark, 1=high/light)."""
    factor = 1.0 + (elevation - 0.5) * intensity
    return (
        max(0, min(255, int(color[0] * factor))),
        max(0, min(255, int(color[1] * factor))),
        max(0, min(255, int(color[2] * factor))),
    )


def _jitter_color(
    color: tuple[int, int, int], q: int, r: int, amount: int = 12,
) -> tuple[int, int, int]:
    """Deterministic per-hex color variation to break up monotone patches."""
    h = ((q + 100) * 73856093 ^ (r + 100) * 19349669) & 0xFFFFFFFF
    return (
        max(0, min(255, color[0] + ((h & 0xFF) % (amount * 2 + 1)) - amount)),
        max(0, min(255, color[1] + (((h >> 8) & 0xFF) % (amount * 2 + 1)) - amount)),
        max(0, min(255, color[2] + (((h >> 16) & 0xFF) % (amount * 2 + 1)) - amount)),
    )


def _corner_jitter(
    x: float, y: float, hex_size: float, amount: float = 0.12,
) -> tuple[float, float]:
    """Deterministic displacement for a hex corner so shared edges match."""
    qx = int(round(x * 10))
    qy = int(round(y * 10))
    h = (qx * 73856093) ^ (qy * 19349669)
    dx = ((h & 0xFF) / 255 - 0.5) * hex_size * amount
    dy = (((h >> 8) & 0xFF) / 255 - 0.5) * hex_size * amount
    return (x + dx, y + dy)


def _feature_offsets(
    q: int, r: int, count: int, hex_size: float, spread: float = 0.55,
) -> list[tuple[float, float]]:
    """Deterministic jittered positions within a hex for feature placement."""
    offsets = []
    for i in range(count):
        h = ((q + 100) * 73856093 + (r + 100) * 19349669 + i * 83492791) & 0xFFFFFFFF
        angle = (h % 628) / 100.0  # 0 to ~2π
        radius = ((h >> 10) % 100) / 100.0 * hex_size * spread
        dx = radius * math.cos(angle)
        dy = radius * math.sin(angle)
        offsets.append((dx, dy))
    return offsets


# ─── Feature Symbol Drawing ──────────────────────────────────────────────────

def _draw_tree(draw, x, y, s, color):
    """Large round-canopy tree filling most of the hex."""
    # Trunk (visible below canopy)
    tw = max(2, int(s * 0.12))
    trunk_h = s * 0.35
    trunk_color = (75, 55, 28)
    draw.line(
        [(int(x), int(y + s * 0.1)), (int(x), int(y + trunk_h))],
        fill=trunk_color, width=tw,
    )
    # Ground shadow
    sr = s * 0.55
    shadow_c = tuple(max(0, c - 40) for c in color)
    draw.ellipse(
        [x - sr * 1.0, y + sr * 0.0, x + sr * 1.0, y + sr * 0.7],
        fill=shadow_c,
    )
    # Main canopy (large oval, fills ~80% of hex)
    cr = s * 0.55
    draw.ellipse(
        [x - cr, y - cr * 1.2, x + cr, y + cr * 0.35],
        fill=color,
    )
    # Secondary canopy lobe for depth
    cr2 = cr * 0.7
    lobe_c = tuple(max(0, c - 10) for c in color)
    draw.ellipse(
        [x - cr * 0.8, y - cr * 1.35, x + cr * 0.2, y - cr * 0.2],
        fill=lobe_c,
    )
    # Highlight on canopy
    hr = cr * 0.4
    highlight = (min(255, color[0] + 35), min(255, color[1] + 40), min(255, color[2] + 15))
    draw.ellipse(
        [x - hr, y - cr * 1.0, x + hr, y - cr * 0.3],
        fill=highlight,
    )


def _draw_peak(draw, x, y, s, color):
    h = s * 0.55
    w = s * 0.40
    draw.polygon(
        [(x, y - h / 2), (x - w / 2, y + h / 3), (x + w / 2, y + h / 3)],
        fill=color,
    )
    ch, cw = h * 0.3, w * 0.45
    draw.polygon(
        [(x, y - h / 2), (x - cw / 2, y - h / 2 + ch), (x + cw / 2, y - h / 2 + ch)],
        fill=(235, 240, 248),
    )


def _draw_wave(draw, x, y, s, color):
    """Water ripple — concentric partial arcs."""
    lw = max(1, int(s * 0.05))
    for i, scale in enumerate([0.28, 0.18, 0.10]):
        w = max(4, s * scale)
        h = max(3, s * scale * 0.45)
        c = tuple(min(255, v + i * 15) for v in color)
        draw.arc([x - w, y - h, x + w, y + h], 200, 340, fill=c, width=lw)


def _draw_dot(draw, x, y, s, color):
    r = max(1.5, s * 0.05)
    draw.ellipse([x - r, y - r, x + r, y + r], fill=color)


def _draw_blade(draw, x, y, s, color):
    """A single curved grass blade."""
    h = s * 0.30
    lw = max(1, int(s * 0.04))
    # Slight curve via two segments
    draw.line(
        [(int(x), int(y + h * 0.3)), (int(x - s * 0.03), int(y - h * 0.1))],
        fill=color, width=lw,
    )
    draw.line(
        [(int(x - s * 0.03), int(y - h * 0.1)), (int(x - s * 0.06), int(y - h * 0.5))],
        fill=color, width=lw,
    )


def _draw_flake(draw, x, y, s, color):
    r = max(2, s * 0.055)
    for angle_deg in (0, 60, 120):
        rad = math.radians(angle_deg)
        draw.line(
            [(int(x - r * math.cos(rad)), int(y - r * math.sin(rad))),
             (int(x + r * math.cos(rad)), int(y + r * math.sin(rad)))],
            fill=color, width=1,
        )


def _draw_reed(draw, x, y, s, color):
    """Tall reed/cattail with seed head."""
    h = s * 0.50
    lw = max(1, int(s * 0.03))
    draw.line(
        [(int(x), int(y + h * 0.25)), (int(x + s * 0.01), int(y - h * 0.45))],
        fill=color, width=lw,
    )
    # Seed head (elongated oval)
    rw = max(2, s * 0.04)
    rh = max(3, s * 0.09)
    head_color = (130, 100, 50)
    draw.ellipse(
        [x - rw, y - h * 0.45 - rh, x + rw, y - h * 0.45 + rh * 0.3],
        fill=head_color,
    )


def _draw_rock(draw, x, y, s, color):
    """A jagged boulder — sharp irregular polygon with flat facets."""
    r = max(4, s * 0.25)
    seed = int(x * 73 + y * 97) & 0xFFFFFFFF
    nv = 5 + (seed % 3)  # 5, 6, or 7 vertices
    pts = []
    for i in range(nv):
        angle = (i / nv) * 2 * math.pi - math.pi / 2
        # Wide radius variation for jagged silhouette
        rv = r * (0.45 + 0.55 * (((seed + i * 48271) >> 4) & 0xFF) / 255.0)
        pts.append((
            int(x + rv * math.cos(angle)),
            int(y + rv * 0.55 * math.sin(angle) + r * 0.1),
        ))
    # Ground shadow (offset down-right)
    shadow_pts = [(px + int(r * 0.15), py + int(r * 0.2)) for px, py in pts]
    shadow_c = tuple(max(0, c - 45) for c in color)
    draw.polygon(shadow_pts, fill=shadow_c)
    # Main rock body
    draw.polygon(pts, fill=color)
    # Darker facet on right side (partial polygon)
    if len(pts) >= 5:
        facet_pts = pts[0:3] + [pts[-1]]
        facet_c = tuple(max(0, c - 20) for c in color)
        draw.polygon(facet_pts, fill=facet_c)
    # Crack lines — multiple for jagged feel
    crack = tuple(max(0, c - 50) for c in color)
    lw = max(1, int(s * 0.02))
    draw.line([(int(x - r * 0.3), int(y - r * 0.08)),
              (int(x + r * 0.1), int(y + r * 0.18))], fill=crack, width=lw)
    draw.line([(int(x - r * 0.05), int(y - r * 0.25)),
              (int(x + r * 0.2), int(y + r * 0.05))], fill=crack, width=lw)
    # Angular highlight facet (flat polygon, not ellipse)
    hl = tuple(min(255, c + 40) for c in color)
    hl_pts = [
        (int(x - r * 0.2), int(y - r * 0.28)),
        (int(x + r * 0.25), int(y - r * 0.18)),
        (int(x + r * 0.15), int(y - r * 0.02)),
        (int(x - r * 0.25), int(y - r * 0.08)),
    ]
    draw.polygon(hl_pts, fill=hl)


def _draw_rocky_ground(draw, x, y, s, color):
    """Raised rough rocky ground — multiple small jagged stones."""
    # Angular ground patch (irregular polygon, not ellipse)
    base_r = s * 0.35
    base_c = tuple(min(255, c + 15) for c in color)
    seed_b = (int(x * 73 + y * 97)) & 0xFFFFFFFF
    base_pts = []
    for i in range(6):
        a = (i / 6) * 2 * math.pi
        br = base_r * (0.7 + 0.3 * (((seed_b + i * 31) >> 3) & 0xFF) / 255.0)
        base_pts.append((int(x + br * math.cos(a)), int(y + br * 0.5 * math.sin(a))))
    draw.polygon(base_pts, fill=base_c)
    # Scatter small jagged rocks on top
    seed = seed_b
    for i in range(3):
        h = (seed + i * 48271) & 0xFFFF
        ox2 = ((h & 0xFF) / 255.0 - 0.5) * base_r * 1.2
        oy2 = (((h >> 8) & 0xFF) / 255.0 - 0.5) * base_r * 0.7
        rr = max(2, s * 0.08 + (h % 3) * s * 0.02)
        rc = tuple(max(0, min(255, c + ((h >> 4) % 30) - 15)) for c in color)
        # Small angular polygon instead of ellipse
        sp = []
        for j in range(4 + (h % 2)):
            a = (j / (4 + (h % 2))) * 2 * math.pi
            sr = rr * (0.5 + 0.5 * (((h + j * 37) >> 2) & 0xFF) / 255.0)
            sp.append((int(x + ox2 + sr * math.cos(a)),
                       int(y + oy2 + sr * 0.5 * math.sin(a))))
        draw.polygon(sp, fill=rc)
        # Angular highlight shard
        hl_c = tuple(min(255, c + 40) for c in rc)
        if len(sp) >= 3:
            draw.polygon(sp[:3], fill=hl_c)


_SYMBOL_DRAW = {
    "tree":       _draw_tree,
    "peak":       _draw_peak,
    "rocky_ground": _draw_rocky_ground,
    "wave":       _draw_wave,
    "dot":        _draw_dot,
    "blade":      _draw_blade,
    "flake":      _draw_flake,
    "reed":       _draw_reed,
    "rock":       _draw_rock,
    "grass_tuft": None,  # handled inline below
    "bush":       None,  # handled inline below
}


def _draw_grass_tuft(draw, x, y, s, color):
    """A cluster of grass blades fanning outward."""
    h = s * 0.35
    lw = max(1, int(s * 0.035))
    for angle_deg in (-25, -10, 5, 18, 28):
        rad = math.radians(angle_deg)
        tip_x = x + math.sin(rad) * h * 0.7
        tip_y = y - h
        draw.line(
            [(int(x), int(y + h * 0.1)), (int(tip_x), int(tip_y))],
            fill=color, width=lw,
        )
    # Base tuft cluster
    r = max(2, s * 0.06)
    draw.ellipse([x - r, y - r * 0.3, x + r, y + r * 0.5],
                 fill=tuple(max(0, c - 12) for c in color))

_SYMBOL_DRAW["grass_tuft"] = _draw_grass_tuft


def _draw_bush(draw, x, y, s, color):
    """A low, round bush cluster."""
    r = s * 0.22
    # Shadow
    shadow = tuple(max(0, c - 25) for c in color)
    draw.ellipse([x - r * 0.9, y - r * 0.1, x + r * 1.1, y + r * 0.8], fill=shadow)
    # Main body
    draw.ellipse([x - r, y - r * 0.7, x + r, y + r * 0.35], fill=color)
    # Highlight
    hr = r * 0.45
    hl = (min(255, color[0] + 25), min(255, color[1] + 32), min(255, color[2] + 12))
    draw.ellipse([x - hr, y - r * 0.5, x + hr, y - r * 0.1], fill=hl)

_SYMBOL_DRAW["bush"] = _draw_bush


# ── Biome-aware feature resolution ───────────────────────────────────────────

# Maps (terrain_type, biome) → overridden (symbol, count, color).
# Forest biomes turn "grass" into dense trees instead of grass tufts.
_BIOME_FEATURE_OVERRIDES: dict[tuple[str, str], tuple[str, int, tuple[int, int, int]]] = {
    ("grass", "forest"):    ("tree", 3, (30, 85, 20)),
    ("grass", "jungle"):    ("tree", 4, (18, 70, 12)),
    ("grass", "taiga"):     ("tree", 3, (28, 68, 25)),
    ("dirt",  "forest"):    ("bush", 2, (50, 90, 30)),
    ("dirt",  "jungle"):    ("bush", 3, (35, 75, 18)),
    ("marsh", "swamp"):     ("reed", 5, (50, 88, 25)),
    # Mountain: sparse stones only, cliff edges provide visual definition
    ("stone", "mountain"):  ("rock", 0, (160, 158, 152)),
    ("dirt",  "mountain"):  ("rock", 1, (145, 140, 130)),
    ("grass", "mountain"):  ("bush", 2, (65, 100, 35)),
    ("snow",  "mountain"):  ("dot", 0, (220, 220, 218)),
}


def _resolve_feature(terrain_type: str, biome: str) -> tuple[str, int, tuple[int, int, int]] | None:
    """Get feature info for a terrain type, with biome override if applicable."""
    override = _BIOME_FEATURE_OVERRIDES.get((terrain_type, biome))
    if override:
        return override
    return TERRAIN_FEATURES.get(terrain_type)


def _draw_hex_features(draw, cx, cy, hex_size, terrain_type, q, r, biome=""):
    """Draw terrain-specific feature symbols within a hex."""
    if hex_size < 10:
        return
    info = _resolve_feature(terrain_type, biome)
    if not info:
        return
    symbol, count, color = info
    if count == 0:
        return
    drawer = _SYMBOL_DRAW.get(symbol)
    if not drawer:
        return
    offsets = _feature_offsets(q, r, count, hex_size)
    for i, (dx, dy) in enumerate(offsets):
        jc = _jitter_color(color, q + i * 7, r + i * 13, amount=18)
        drawer(draw, cx + dx, cy + dy, hex_size, jc)


def _draw_cobblestone_hex(draw, cx, cy, hex_size, q, r, poly):
    """Draw cross-hatch lines and a faint hex outline on cobblestone."""
    if hex_size < 6:
        return
    import math

    line_color = (140, 134, 124, 70)  # faint warm-grey lines
    line_w = max(1, int(hex_size * 0.04))
    hs = hex_size * 0.82  # stay within hex bounds

    # Three sets of parallel lines at 0°, 60°, 120° (cross-hatch)
    spacing = max(4, int(hex_size * 0.35))
    for angle_deg in (0, 60, 120):
        a = math.radians(angle_deg)
        dx = math.cos(a)
        dy = math.sin(a)
        # Perpendicular direction for line offsets
        px = -dy
        py = dx
        n_lines = int(hs * 2 / spacing)
        for i in range(-n_lines, n_lines + 1):
            off = i * spacing
            x0 = cx + px * off - dx * hs
            y0 = cy + py * off - dy * hs
            x1 = cx + px * off + dx * hs
            y1 = cy + py * off + dy * hs
            draw.line([(x0, y0), (x1, y1)], fill=line_color, width=line_w)

    # Faint hex outline
    outline_color = (130, 124, 114, 50)
    draw.polygon(poly, outline=outline_color)


def _draw_gravel_hex(draw, cx, cy, hex_size, q, r, poly):
    """Draw scattered pebble dots in browns and greys on gravel ground."""
    if hex_size < 6:
        return

    # Deterministic seed per hex for reproducible pebble placement
    seed = ((q + 500) * 73856093 + (r + 500) * 19349669) & 0xFFFFFFFF

    pebble_colors = [
        (110, 108, 98, 50),   # grey
        (125, 115, 95, 45),   # warm grey-brown
        (100, 100, 90, 40),   # cool grey
        (118, 110, 88, 48),   # brown-grey
        (95, 105, 85, 42),    # greenish grey
    ]
    hs = hex_size * 0.72
    n_pebbles = max(4, int(hex_size * 0.35))

    for i in range(n_pebbles):
        v = (seed * (i + 1) * 2654435761) & 0xFFFFFFFF
        px = cx + (((v >> 0) & 0xFF) / 255.0 - 0.5) * 2 * hs
        py = cy + (((v >> 8) & 0xFF) / 255.0 - 0.5) * 2 * hs * 0.87
        pr = max(1, int(hex_size * (0.02 + ((v >> 16) & 0x1F) / 31.0 * 0.04)))
        color = pebble_colors[i % len(pebble_colors)]
        draw.ellipse([px - pr, py - pr, px + pr, py + pr], fill=color)


# ─── Road-angle helper ───────────────────────────────────────────────────────

def _compute_road_angle(
    h,
    tile_lookup: dict,
    centers: dict,
    ox: float,
    oy: float,
) -> float | None:
    """Return the angle (radians) along the road edge closest to hex *h*.

    Finds adjacent road hexes and returns the angle of a vector connecting
    them (or from *h* toward a single road neighbor).  The wall should be
    drawn *parallel* to this direction.
    """
    road_nbs = []
    for nb in h.neighbors():
        nt = tile_lookup.get(nb)
        if nt and nt.structure_type == "road":
            road_nbs.append(nb)
    if not road_nbs:
        return None

    if len(road_nbs) >= 2:
        # Average road-neighbor positions to get a tangent direction
        # Pick the two most spread-apart neighbours for best tangent
        best_pair = None
        best_dist = -1
        for i in range(len(road_nbs)):
            for j in range(i + 1, len(road_nbs)):
                d = road_nbs[i].distance(road_nbs[j])
                if d > best_dist:
                    best_dist = d
                    best_pair = (road_nbs[i], road_nbs[j])
        if best_pair:
            p0 = centers.get(best_pair[0])
            p1 = centers.get(best_pair[1])
            if p0 and p1:
                dx = (p1[0] + ox) - (p0[0] + ox)
                dy = (p1[1] + oy) - (p0[1] + oy)
                return math.atan2(dy, dx)

    # Single road neighbour — angle from h toward road
    rn = road_nbs[0]
    pc = centers.get(h)
    pr = centers.get(rn)
    if pc and pr:
        dx = (pr[0]) - (pc[0])
        dy = (pr[1]) - (pc[1])
        # Perpendicular to the toward-road direction (wall runs parallel to road)
        return math.atan2(dy, dx) + math.pi / 2
    return None


# ─── Mud smearing helper ─────────────────────────────────────────────────────

def _draw_road_mud(draw, cx, cy, hex_size, q, r):
    """Draw subtle mud/dirt streaks on a road hex (top-down rut marks)."""
    if hex_size < 6:
        return
    h_seed = ((q + 200) * 73856093 + (r + 200) * 19349669) & 0xFFFFFFFF
    if h_seed % 3 == 0:
        # Skip some hexes so mud isn't everywhere
        return
    mud_c = (140, 118, 82, 40)     # warm brown, very transparent
    dark_mud = (110, 90, 58, 35)   # darker rut
    s = hex_size
    rng_bits = h_seed
    # 2-3 short streaks per hex
    n_streaks = 2 + (rng_bits & 1)
    for i in range(n_streaks):
        bits = (rng_bits >> (4 + i * 8)) & 0xFF
        frac_x = (bits & 0xF) / 15.0 - 0.5        # [-0.5, 0.5]
        frac_y = ((bits >> 4) & 0xF) / 15.0 - 0.5
        sx = cx + frac_x * s * 0.6
        sy = cy + frac_y * s * 0.6
        angle = ((rng_bits >> (3 + i * 5)) & 0x1F) / 31.0 * math.pi
        length = s * (0.2 + (bits & 0x7) / 30.0)
        w = max(1, int(s * 0.04))
        c = mud_c if i % 2 == 0 else dark_mud
        ex = sx + math.cos(angle) * length
        ey = sy + math.sin(angle) * length
        draw.line([(int(sx), int(sy)), (int(ex), int(ey))], fill=c, width=w)


# ─── Cluster Detection ───────────────────────────────────────────────────────

def _find_clusters(
    tile_lookup: dict,
    terrain_type: str,
) -> list[set]:
    """Find connected clusters of hexes with the same terrain type."""
    visited: set = set()
    clusters: list[set] = []
    for h, tile in tile_lookup.items():
        tt = tile.terrain_type if not tile.structure_type else ""
        if tt != terrain_type or h in visited:
            continue
        # BFS to find connected component
        cluster: set = set()
        stack = [h]
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            t2 = tile_lookup.get(cur)
            if not t2:
                continue
            tt2 = t2.terrain_type if not t2.structure_type else ""
            if tt2 != terrain_type:
                continue
            visited.add(cur)
            cluster.add(cur)
            for nb in cur.neighbors():
                if nb not in visited:
                    stack.append(nb)
        if len(cluster) >= 2:
            clusters.append(cluster)
    return clusters


def _draw_large_tree(draw, x, y, s, color):
    """A large tree canopy spanning ~2 hex widths."""
    # Trunk peeking below
    tw = max(3, int(s * 0.14))
    draw.line([(int(x), int(y + s * 0.2)), (int(x), int(y + s * 0.8))],
              fill=(70, 50, 25), width=tw)
    # Ground shadow
    sr = s * 1.1
    shadow_c = tuple(max(0, c - 45) for c in color)
    draw.ellipse([x - sr, y + sr * 0.0, x + sr, y + sr * 0.65], fill=shadow_c)
    # Large canopy
    cr = s * 1.05
    draw.ellipse([x - cr, y - cr * 1.15, x + cr, y + cr * 0.4], fill=color)
    # Secondary lobe for volume
    lobe_c = tuple(max(0, c - 12) for c in color)
    draw.ellipse([x - cr * 0.7, y - cr * 1.35, x + cr * 0.3, y - cr * 0.1],
                 fill=lobe_c)
    # Third lobe offset
    draw.ellipse([x + cr * 0.1, y - cr * 1.0, x + cr * 0.9, y + cr * 0.05],
                 fill=tuple(max(0, c - 6) for c in color))
    # Highlight
    hr = cr * 0.35
    hl = (min(255, color[0] + 40), min(255, color[1] + 45), min(255, color[2] + 18))
    draw.ellipse([x - hr, y - cr * 0.95, x + hr, y - cr * 0.3], fill=hl)


def _draw_large_bush(draw, x, y, s, color):
    """A large bush cluster spanning ~1.5 hexes."""
    r = s * 0.85
    shadow = tuple(max(0, c - 28) for c in color)
    draw.ellipse([x - r * 0.9, y + r * 0.0, x + r * 1.1, y + r * 0.6], fill=shadow)
    draw.ellipse([x - r, y - r * 0.65, x + r, y + r * 0.3], fill=color)
    # Sub-lobes for organic shape
    for ox2, oy2, sc in [(-r * 0.5, -r * 0.2, 0.6), (r * 0.45, -r * 0.3, 0.55),
                          (-r * 0.1, -r * 0.5, 0.45)]:
        lr = r * sc
        lc = tuple(min(255, c + 14) for c in color)
        draw.ellipse([x + ox2 - lr, y + oy2 - lr * 0.55, x + ox2 + lr, y + oy2 + lr * 0.3], fill=lc)
    hr = r * 0.3
    hl = (min(255, color[0] + 32), min(255, color[1] + 40), min(255, color[2] + 16))
    draw.ellipse([x - hr, y - r * 0.5, x + hr, y - r * 0.1], fill=hl)


def _draw_large_boulder(draw, x, y, s, color):
    """A large jagged rock outcrop spanning ~1.5 hexes."""
    r = s * 0.85
    seed = int(x * 73 + y * 97) & 0xFFFFFFFF
    # Main rock body — jagged irregular polygon with wide radius variation
    nv = 8
    pts = []
    for i in range(nv):
        angle = (i / nv) * 2 * math.pi - math.pi / 3
        rv = r * (0.45 + 0.55 * (((seed + i * 48271) >> 4) & 0xFF) / 255.0)
        pts.append((
            int(x + rv * math.cos(angle)),
            int(y + rv * 0.50 * math.sin(angle) + r * 0.05),
        ))
    # Ground shadow
    shadow_pts = [(px + int(r * 0.12), py + int(r * 0.18)) for px, py in pts]
    shadow_c = tuple(max(0, c - 50) for c in color)
    draw.polygon(shadow_pts, fill=shadow_c)
    # Main body
    draw.polygon(pts, fill=color)
    # Angular facet (darker half)
    mid = len(pts) // 2
    facet_pts = pts[:mid+1]
    facet_c = tuple(max(0, c - 22) for c in color)
    draw.polygon(facet_pts, fill=facet_c)
    # Crack lines — extra for jagged character
    crack = tuple(max(0, c - 45) for c in color)
    lw = max(1, int(s * 0.03))
    draw.line([(int(x - r * 0.4), int(y - r * 0.1)),
              (int(x + r * 0.15), int(y + r * 0.12))], fill=crack, width=lw)
    draw.line([(int(x - r * 0.1), int(y - r * 0.35)),
              (int(x + r * 0.25), int(y - r * 0.02))], fill=crack, width=lw)
    draw.line([(int(x + r * 0.05), int(y + r * 0.08)),
              (int(x + r * 0.35), int(y - r * 0.15))], fill=crack, width=lw)
    # Secondary smaller jagged rock beside main
    r2 = r * 0.4
    ox2, oy2 = r * 0.55, -r * 0.25
    pts2 = []
    for i in range(5):
        angle = (i / 5) * 2 * math.pi
        rv2 = r2 * (0.45 + 0.55 * (((seed + i * 91) >> 6) & 0xFF) / 255.0)
        pts2.append((
            int(x + ox2 + rv2 * math.cos(angle)),
            int(y + oy2 + rv2 * 0.50 * math.sin(angle)),
        ))
    c2 = tuple(min(255, c + 10) for c in color)
    draw.polygon(pts2, fill=c2)
    # Angular highlight facet on main (flat polygon, not ellipse)
    hl = tuple(min(255, c + 35) for c in color)
    hl_pts = [
        (int(x - r * 0.2), int(y - r * 0.30)),
        (int(x + r * 0.22), int(y - r * 0.20)),
        (int(x + r * 0.15), int(y - r * 0.02)),
        (int(x - r * 0.25), int(y - r * 0.08)),
    ]
    draw.polygon(hl_pts, fill=hl)


def _draw_impassable_outcrop(draw, cx, cy, s, color, hex_count, centers, covered_hexes, ox, oy):
    """A single massive dark jagged rock shape spanning multiple hexes.

    Computes one unified irregular polygon sized to cover all the hex
    positions in the formation, then renders it as a single menacing rock.
    """
    seed = int(cx * 73 + cy * 97) & 0xFFFFFFFF
    # Darken the terrain color moderately so it blends with surroundings
    dark = (max(0, color[0] - 35), max(0, color[1] - 35), max(0, color[2] - 30))
    crack_c = (max(0, dark[0] - 25), max(0, dark[1] - 25), max(0, dark[2] - 22))

    # Collect pixel centers of all covered hexes
    hex_positions = []
    for h in covered_hexes:
        if h in centers:
            hex_positions.append((centers[h][0] + ox, centers[h][1] + oy))
    if not hex_positions:
        return

    # Centroid of the entire formation
    avg_x = sum(p[0] for p in hex_positions) / len(hex_positions)
    avg_y = sum(p[1] for p in hex_positions) / len(hex_positions)

    # Compute bounding radius: max distance from centroid to any hex center
    # plus one hex_size so the rock covers the outermost hexes fully
    max_dist = max(
        ((hx - avg_x)**2 + (hy - avg_y)**2) ** 0.5
        for hx, hy in hex_positions
    )
    bound_r = max_dist + s * 1.05

    # Generate ONE large jagged polygon around the centroid
    # More vertices for larger formations
    nv = 10 + hex_count
    pts = []
    for i in range(nv):
        angle = (i / nv) * 2 * math.pi - math.pi / 3
        # Base radius varies per vertex for jagged shape
        base_rv = 0.50 + 0.50 * (((seed + i * 48271) >> 4) & 0xFF) / 255.0
        rv = bound_r * base_rv
        # Push vertices outward toward nearby hex centers for organic coverage
        vx = avg_x + rv * math.cos(angle)
        vy = avg_y + rv * 0.50 * math.sin(angle)
        # Find nearest hex position and pull vertex toward it
        min_hd = float('inf')
        nearest_hx, nearest_hy = avg_x, avg_y
        for hx, hy in hex_positions:
            hd = ((vx - hx)**2 + (vy - hy)**2) ** 0.5
            if hd < min_hd:
                min_hd = hd
                nearest_hx, nearest_hy = hx, hy
        # Blend toward nearest hex center to ensure coverage
        pull = 0.25
        vx = vx * (1 - pull) + nearest_hx * pull
        vy = vy * (1 - pull) + nearest_hy * pull
        pts.append((int(vx), int(vy)))

    # Ground shadow — blended with rock color for subtlety
    shadow_off = max(2, int(s * 0.12))
    shadow_pts = [(px + shadow_off, py + int(shadow_off * 1.3)) for px, py in pts]
    shadow_c = (max(0, dark[0] - 40), max(0, dark[1] - 40), max(0, dark[2] - 35))
    draw.polygon(shadow_pts, fill=shadow_c)

    # Main body fill
    draw.polygon(pts, fill=dark)

    # Darker facet on one half
    mid = len(pts) // 2
    facet_c = (max(0, dark[0] - 18), max(0, dark[1] - 18), max(0, dark[2] - 15))
    draw.polygon(pts[:mid + 1], fill=facet_c)

    # Dense crack / hatch lines scattered across the full formation area
    lw = max(1, int(s * 0.03))
    num_cracks = 5 + hex_count * 2
    for ci in range(num_cracks):
        h_val = (seed + ci * 48271) & 0xFFFF
        # Pick a random point within the formation bounds
        t1 = (h_val & 0xFF) / 255.0
        t2 = ((h_val >> 8) & 0xFF) / 255.0
        # Use a random hex position as base for the crack
        base_idx = h_val % len(hex_positions)
        bx, by = hex_positions[base_idx]
        crack_x = bx + s * 0.8 * (t1 - 0.5)
        crack_y = by + s * 0.4 * (t2 - 0.5)
        angle = ((h_val >> 4) & 0xFF) / 255.0 * math.pi - math.pi / 2
        ll = s * (0.3 + 0.5 * ((h_val >> 2) & 0xFF) / 255.0)
        draw.line(
            [(int(crack_x), int(crack_y)),
             (int(crack_x + ll * math.cos(angle)),
              int(crack_y + ll * 0.5 * math.sin(angle)))],
            fill=crack_c, width=lw,
        )

    # Outline on the full perimeter — uses rock-derived color
    outline_c = (max(0, dark[0] - 30), max(0, dark[1] - 30), max(0, dark[2] - 25))
    olw = max(2, int(s * 0.05))
    for i in range(len(pts)):
        p1 = pts[i]
        p2 = pts[(i + 1) % len(pts)]
        draw.line([p1, p2], fill=outline_c, width=olw)

    # Angular highlight shard near center
    hl = (min(255, dark[0] + 28), min(255, dark[1] + 28), min(255, dark[2] + 22))
    hr = s * 0.5
    hl_pts = [
        (int(avg_x - hr * 0.35), int(avg_y - hr * 0.45)),
        (int(avg_x + hr * 0.40), int(avg_y - hr * 0.30)),
        (int(avg_x + hr * 0.25), int(avg_y + hr * 0.10)),
        (int(avg_x - hr * 0.30), int(avg_y - hr * 0.05)),
    ]
    draw.polygon(hl_pts, fill=hl)


# ─── Water Rendering ─────────────────────────────────────────────────────────

def _draw_water_hex(draw, cx, cy, hex_size, poly, q, r, tile_lookup, tile, ox, oy, centers):
    """Draw a water hex with depth-based shading and shoreline effects.
    
    Water near land = lighter (shallow), water far from land = darker (deep).
    Reference style: layered teal with slight wave texture.
    """
    # Count distance to nearest non-water neighbor (0 = shore, 1+ = deeper)
    shore_dist = 3  # default deep
    water_types = {"water", "ocean", "lake", "coastal"}
    for d in range(1, 4):
        found_land = False
        for nb in tile.hex.ring(d) if d > 0 else [tile.hex]:
            nt = tile_lookup.get(nb)
            if nt is None:
                found_land = True
                break
            tt = nt.terrain_type if not nt.structure_type else nt.structure_type
            if tt not in water_types:
                found_land = True
                break
        if found_land:
            shore_dist = d - 1
            break

    # Color gradient: shore (bright teal) -> deep (dark blue-green)
    shore_color = (65, 145, 165)   # bright teal
    mid_color   = (42, 105, 140)   # medium blue
    deep_color  = (28, 72, 110)    # deep blue

    if shore_dist == 0:
        base = shore_color
    elif shore_dist == 1:
        base = mid_color
    else:
        base = deep_color

    # Jitter per hex
    base = _jitter_color(base, q, r, amount=10)

    # Fill the hex with depth color
    draw.polygon(poly, fill=base)

    # Shore foam line: if this hex borders land, draw a lighter edge
    if shore_dist == 0:
        neighbors_list = tile.hex.neighbors()
        _DIR_EDGE_W = {0: (0, 1), 1: (5, 0), 2: (4, 5), 3: (3, 4), 4: (2, 3), 5: (1, 2)}
        foam_color = (120, 190, 200, 180)
        foam_lw = max(2, int(hex_size * 0.10))
        for dir_idx, nb in enumerate(neighbors_list):
            nt = tile_lookup.get(nb)
            if nt is None:
                continue
            tt = nt.terrain_type if not nt.structure_type else nt.structure_type
            if tt in water_types:
                continue
            # This edge borders land — draw foam
            ca, cb = _DIR_EDGE_W[dir_idx]
            ax, ay = poly[ca]
            bx, by = poly[cb]
            # Offset inward slightly
            mcx, mcy = (ax + bx) / 2, (ay + by) / 2
            inward_x = cx + (mcx - cx) * 0.75
            inward_y = cy + (mcy - cy) * 0.75
            # Draw foam arc along edge
            draw.line(
                [(int(ax + (cx - ax) * 0.15), int(ay + (cy - ay) * 0.15)),
                 (int(bx + (cx - bx) * 0.15), int(by + (cy - by) * 0.15))],
                fill=foam_color[:3], width=foam_lw,
            )

    # Subtle wave ripples on all water
    h_val = ((q + 100) * 73856093 ^ (r + 100) * 19349669) & 0xFFFFFFFF
    ripple_color = tuple(min(255, c + 18) for c in base)
    lw = max(1, int(hex_size * 0.03))
    for i in range(2):
        angle = ((h_val + i * 12345) % 314) / 100.0
        rx = hex_size * 0.15 * math.cos(angle)
        ry = hex_size * 0.08 * math.sin(angle)
        w = max(3, hex_size * 0.12)
        draw.arc(
            [cx + rx - w, cy + ry - w * 0.35, cx + rx + w, cy + ry + w * 0.35],
            200, 340, fill=ripple_color, width=lw,
        )


def _draw_cover_indicator(draw, cx, cy, hex_size, cover_level):
    """Draw a small cover indicator icon in the corner of a hex."""
    if cover_level == Cover.NONE or hex_size < 10:
        return
    ix = cx + hex_size * 0.32
    iy = cy - hex_size * 0.32
    r = max(2, hex_size * 0.09)

    color = COVER_COLORS.get(cover_level, (200, 200, 200, 100))
    rgb = color[:3]

    if cover_level == Cover.QUARTER:
        draw.arc([ix - r, iy - r, ix + r, iy + r], 180, 270, fill=rgb, width=max(1, int(r * 0.5)))
    elif cover_level == Cover.HALF:
        draw.pieslice([ix - r, iy - r, ix + r, iy + r], 180, 360, fill=rgb)
        draw.arc([ix - r, iy - r, ix + r, iy + r], 0, 360, fill=rgb, width=1)
    elif cover_level == Cover.THREE_QUARTER:
        draw.pieslice([ix - r, iy - r, ix + r, iy + r], 90, 360, fill=rgb)
        draw.arc([ix - r, iy - r, ix + r, iy + r], 0, 360, fill=rgb, width=1)
    elif cover_level == Cover.FULL:
        draw.ellipse([ix - r, iy - r, ix + r, iy + r], fill=rgb)


def _rotated_rect(cx, cy, hw, hh, angle):
    """Return corner points of a rectangle rotated by *angle* radians."""
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    return [(cx + x * cos_a - y * sin_a, cy + x * sin_a + y * cos_a)
            for x, y in corners]


def _seed_angle(h_seed, lo=-0.25, hi=0.25):
    """Deterministic angle in radians from a hash seed."""
    return lo + (h_seed & 0xFFFF) / 0xFFFF * (hi - lo)


def _draw_structure_symbol(draw, cx, cy, hex_size, structure_type, road_angle=None, palette=None):
    """Draw structure-specific symbols on a hex.

    *road_angle* (radians) may be supplied for ``road_wall`` so the wall
    aligns parallel to the nearest road edge.
    *palette* is a :class:`StructurePalette` for per-culture colors.
    """
    if palette is None:
        palette = STRUCTURE_PALETTES["human"]
    if hex_size < 4:
        return
    s = hex_size

    if structure_type == "house":
        # Contrasting wood plank lines on interior floor
        lw = max(1, int(s * 0.02))
        plank_color = palette.plank_line
        spacing = s * 0.18
        for i in range(-2, 3):
            y_off = i * spacing
            draw.line(
                [(int(cx - s * 0.32), int(cy + y_off)),
                 (int(cx + s * 0.32), int(cy + y_off))],
                fill=plank_color, width=lw,
            )
    elif structure_type == "house_wall":
        # Contrasting wood plank lines (same as interior — wall drawn on edges)
        lw = max(1, int(s * 0.02))
        plank_color = palette.plank_line
        spacing = s * 0.18
        for i in range(-2, 3):
            y_off = i * spacing
            draw.line(
                [(int(cx - s * 0.32), int(cy + y_off)),
                 (int(cx + s * 0.32), int(cy + y_off))],
                fill=plank_color, width=lw,
            )
    elif structure_type == "house_door":
        # Door mat / threshold marker
        w, h = s * 0.15, s * 0.22
        draw.rounded_rectangle(
            [cx - w, cy - h, cx + w, cy + h],
            radius=max(1, int(s * 0.04)),
            fill=palette.door,
            outline=palette.door_dark, width=max(1, int(s * 0.03)),
        )
    elif structure_type == "fence":
        lw = max(1, int(s * 0.03))
        pw = s * 0.14
        ph = s * 0.18
        draw.line([(int(cx - pw), int(cy - ph)), (int(cx - pw), int(cy + ph))],
                  fill=palette.fence, width=lw)
        draw.line([(int(cx + pw), int(cy - ph)), (int(cx + pw), int(cy + ph))],
                  fill=palette.fence, width=lw)
        draw.line([(int(cx - pw), int(cy)), (int(cx + pw), int(cy))],
                  fill=palette.fence, width=lw)
    elif structure_type == "well":
        r = s * 0.14
        draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                     outline=palette.stone_dark, width=max(1, int(s * 0.04)))
        dr = max(1, s * 0.04)
        draw.ellipse([cx - dr, cy - dr, cx + dr, cy + dr], fill=palette.well_water)
    elif structure_type == "road_wall":
        # Top-down: low stone wall aligned parallel to adjacent road
        h_seed = ((int(cx) + 200) * 73856093 + (int(cy) + 200) * 19349669) & 0xFFFFFFFF
        # Use road_angle if provided; otherwise fall back to small random
        if road_angle is not None:
            angle = road_angle
        else:
            angle = _seed_angle(h_seed, -0.25, 0.25)
        wall_c = palette.stone
        dark_c = palette.stone_dark
        cap_c = palette.stone_cap
        # Wall footprint — wide band filling most of the hex
        ww = s * 0.44
        wh = s * 0.18
        pts = _rotated_rect(cx, cy, ww, wh, angle)
        draw.polygon(pts, fill=wall_c, outline=dark_c,
                     width=max(1, int(s * 0.025)))
        # Top-face capstones — slightly lighter inner rectangle
        cw = ww * 0.82
        ch = wh * 0.50
        cap_pts = _rotated_rect(cx, cy, cw, ch, angle)
        draw.polygon(cap_pts, fill=cap_c)
        # Stone block divisions — short perpendicular lines across the wall
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        lw = max(1, int(s * 0.02))
        for frac in (-0.5, -0.15, 0.2, 0.55):
            bx = cx + ww * frac * cos_a
            by = cy + ww * frac * sin_a
            x0 = bx - wh * 0.9 * sin_a
            y0 = by + wh * 0.9 * cos_a
            x1 = bx + wh * 0.9 * sin_a
            y1 = by - wh * 0.9 * cos_a
            draw.line([(int(x0), int(y0)), (int(x1), int(y1))],
                      fill=dark_c, width=lw)
    elif structure_type == "street_lamp":
        # Top-down: looking down at a lamp post — circular base + glow ring
        h_seed = ((int(cx) + 200) * 73856093 + (int(cy) + 200) * 19349669) & 0xFFFFFFFF
        post_c = palette.lamp_post
        lantern_c = palette.lamp_glow
        glow_c = tuple(min(255, c + 25) for c in palette.lamp_glow)
        shadow_c = tuple(max(0, c - 130) for c in palette.lamp_glow)
        # Shadow cast by the lamp (offset circle)
        sr = s * 0.30
        sx_off = s * 0.06
        sy_off = s * 0.08
        draw.ellipse([cx + sx_off - sr, cy + sy_off - sr,
                      cx + sx_off + sr, cy + sy_off + sr],
                     fill=shadow_c)
        # Warm glow ring on ground
        gr = s * 0.35
        draw.ellipse([cx - gr, cy - gr, cx + gr, cy + gr],
                     outline=glow_c, width=max(1, int(s * 0.03)))
        gr2 = s * 0.25
        draw.ellipse([cx - gr2, cy - gr2, cx + gr2, cy + gr2],
                     outline=tuple(min(255, c + 15) for c in palette.lamp_glow), width=max(1, int(s * 0.02)))
        # Iron post base — dark circle
        br = s * 0.10
        draw.ellipse([cx - br, cy - br, cx + br, cy + br],
                     fill=post_c, outline=tuple(max(0, c - 15) for c in post_c),
                     width=max(1, int(s * 0.02)))
        # Lantern top — bright dot at centre
        lr = s * 0.06
        draw.ellipse([cx - lr, cy - lr, cx + lr, cy + lr],
                     fill=lantern_c)
    elif structure_type == "hay_bale":
        # Golden hay bale — rotated, fills most of the hex
        h_seed = ((int(cx) + 200) * 73856093 + (int(cy) + 200) * 19349669) & 0xFFFFFFFF
        angle = _seed_angle(h_seed, -0.3, 0.3)
        bale_colors = list(palette.hay)
        fill_c = bale_colors[h_seed % len(bale_colors)]
        dark_c = tuple(max(0, c - 40) for c in fill_c)
        straw_c = tuple(max(0, c - 20) for c in fill_c)
        # Fills ~90% of hex
        hw = s * (0.46 + (h_seed >> 4 & 0xF) / 200)
        hh = s * (0.38 + (h_seed >> 8 & 0xF) / 250)
        pts = _rotated_rect(cx, cy, hw, hh, angle)
        draw.polygon(pts, fill=fill_c, outline=dark_c,
                     width=max(1, int(s * 0.03)))
        # Straw lines follow the rotation
        lw = max(1, int(s * 0.02))
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        for frac in (-0.35, 0.05, 0.4):
            dy = hh * frac
            x0 = cx + (-hw * 0.85) * cos_a - dy * sin_a
            y0 = cy + (-hw * 0.85) * sin_a + dy * cos_a
            x1 = cx + (hw * 0.85) * cos_a - dy * sin_a
            y1 = cy + (hw * 0.85) * sin_a + dy * cos_a
            draw.line([(int(x0), int(y0)), (int(x1), int(y1))],
                      fill=straw_c, width=lw)
    elif structure_type == "crate":
        # Wooden crate — rotated, fills ~85% of hex
        h_seed = ((int(cx) + 200) * 73856093 + (int(cy) + 200) * 19349669) & 0xFFFFFFFF
        angle = _seed_angle(h_seed, -0.22, 0.22)
        crate_colors = list(palette.wood)
        fill_c = crate_colors[h_seed % len(crate_colors)]
        dark_c = tuple(max(0, c - 45) for c in fill_c)
        brace_c = tuple(max(0, c - 25) for c in fill_c)
        # Fills ~170% of hex (2x scaled)
        hw = s * (0.80 + (h_seed >> 4 & 0xF) / 100)
        hh = s * (0.84 + (h_seed >> 8 & 0xF) / 100)
        pts = _rotated_rect(cx, cy, hw, hh, angle)
        draw.polygon(pts, fill=fill_c, outline=dark_c,
                     width=max(1, int(s * 0.03)))
        # Rotated cross bracing
        lw = max(1, int(s * 0.02))
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        for sx, sy, ex, ey in [(-0.75, -0.75, 0.75, 0.75),
                                (0.75, -0.75, -0.75, 0.75)]:
            x0 = cx + hw * sx * cos_a - hh * sy * sin_a
            y0 = cy + hw * sx * sin_a + hh * sy * cos_a
            x1 = cx + hw * ex * cos_a - hh * ey * sin_a
            y1 = cy + hw * ex * sin_a + hh * ey * cos_a
            draw.line([(int(x0), int(y0)), (int(x1), int(y1))],
                      fill=brace_c, width=lw)
    elif structure_type == "cart":
        # Wagon cart — rotated at a seeded angle
        h_seed = ((int(cx) + 200) * 73856093 + (int(cy) + 200) * 19349669) & 0xFFFFFFFF
        angle = _seed_angle(h_seed, -0.35, 0.35)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        cart_colors = list(palette.wood)
        fill_c = cart_colors[h_seed % len(cart_colors)]
        dark_c = tuple(max(0, c - 45) for c in fill_c)
        plank_c = tuple(max(0, c - 20) for c in fill_c)
        bw, bh = s * 0.46, s * 0.30
        # Cart bed — rotated
        pts = _rotated_rect(cx, cy, bw, bh, angle)
        draw.polygon(pts, fill=fill_c, outline=dark_c,
                     width=max(1, int(s * 0.03)))
        # Plank lines following rotation
        lw = max(1, int(s * 0.015))
        for dx_frac in (-0.45, 0.05, 0.5):
            x_off = bw * dx_frac
            px0 = cx + x_off * cos_a - (-bh * 0.75) * sin_a
            py0 = cy + x_off * sin_a + (-bh * 0.75) * cos_a
            px1 = cx + x_off * cos_a - (bh * 0.75) * sin_a
            py1 = cy + x_off * sin_a + (bh * 0.75) * cos_a
            draw.line([(int(px0), int(py0)), (int(px1), int(py1))],
                      fill=plank_c, width=lw)
        # Wheels at ends of bed
        wr = s * 0.12
        spoke_lw = max(1, int(s * 0.015))
        for side in (-1, 1):
            wx = cx + side * bw * cos_a - bh * 0.2 * sin_a
            wy = cy + side * bw * sin_a + bh * 0.2 * cos_a
            draw.ellipse([wx - wr, wy - wr, wx + wr, wy + wr],
                         fill=palette.wheel, outline=palette.wheel_rim,
                         width=max(1, int(s * 0.02)))
            # Spokes in X pattern
            for sx, sy in [(0.6, 0), (0, 0.6)]:
                draw.line([(int(wx - wr * sx), int(wy - wr * sy)),
                           (int(wx + wr * sx), int(wy + wr * sy))],
                          fill=palette.wheel_rim, width=spoke_lw)
        # Handle / tongue extending from one end
        hx0 = cx + bw * cos_a
        hy0 = cy + bw * sin_a
        hx1 = hx0 + s * 0.18 * cos_a - s * 0.08 * sin_a
        hy1 = hy0 + s * 0.18 * sin_a + s * 0.08 * cos_a
        draw.line([(int(hx0), int(hy0)), (int(hx1), int(hy1))],
                  fill=dark_c, width=max(2, int(s * 0.03)))
    elif structure_type == "market_stall":
        # Market stall: table + colored canopy — rotated
        h_seed = ((int(cx) + 200) * 73856093 + (int(cy) + 200) * 19349669) & 0xFFFFFFFF
        angle = _seed_angle(h_seed, -0.2, 0.2)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        canopy_colors = list(palette.fabric)
        canopy = canopy_colors[h_seed % len(canopy_colors)]
        dark = tuple(max(0, c - 45) for c in canopy)
        # Canopy half-widths — fills ~90% of hex
        aw = s * (0.48 + (h_seed >> 4 & 0xF) / 250)
        ah = s * 0.36
        # Table beneath canopy (slightly narrower)
        tw, th = aw * 0.88, s * 0.13
        tbl_pts = _rotated_rect(cx - 0.02 * aw * cos_a, cy + ah * 0.1 + th * 0.5,
                                tw, th * 0.5, angle)
        draw.polygon(tbl_pts, fill=palette.table, outline=palette.table_dark,
                     width=max(1, int(s * 0.02)))
        # Goods on table: small colored dots
        gr = max(2, int(s * 0.04))
        goods_colors = list(palette.goods)
        for gi in range(3):
            frac = -0.5 + gi * 0.5
            gx = cx + tw * frac * cos_a + (ah * 0.1 + th * 0.35) * sin_a * 0
            gy = cy + tw * frac * sin_a + ah * 0.1 + th * 0.35
            gc = goods_colors[(h_seed + gi) % len(goods_colors)]
            draw.ellipse([gx - gr, gy - gr, gx + gr, gy + gr], fill=gc)
        # Canopy — rotated rectangle above table
        canopy_cy = cy - ah * 0.45
        canopy_pts = _rotated_rect(cx, canopy_cy, aw, ah * 0.55, angle)
        draw.polygon(canopy_pts, fill=canopy, outline=dark,
                     width=max(1, int(s * 0.03)))
        # Fabric stripes following rotation
        stripe = tuple(min(255, c + 40) for c in canopy)
        slw = max(1, int(s * 0.025))
        for frac in (0.3, 0.6):
            dy = -ah * 0.55 + ah * 1.1 * frac
            x0 = cx + (-aw * 0.85) * cos_a - dy * sin_a
            y0 = canopy_cy + (-aw * 0.85) * sin_a + dy * cos_a
            x1 = cx + (aw * 0.85) * cos_a - dy * sin_a
            y1 = canopy_cy + (aw * 0.85) * sin_a + dy * cos_a
            draw.line([(int(x0), int(y0)), (int(x1), int(y1))],
                      fill=stripe, width=slw)
        # Scalloped front edge
        scallop_r = max(2, int(s * 0.04))
        num_scallops = max(3, int(aw * 2 / (scallop_r * 2.5)))
        for si in range(num_scallops):
            frac = (si + 0.5) / num_scallops - 0.5
            sx = cx + aw * 2 * frac * cos_a - ah * 0.55 * sin_a
            sy = canopy_cy + aw * 2 * frac * sin_a + ah * 0.55 * cos_a
            draw.arc([sx - scallop_r, sy - scallop_r,
                      sx + scallop_r, sy + scallop_r],
                     0, 180, fill=dark, width=max(1, int(s * 0.02)))
    elif structure_type == "barrel":
        # Round barrel with position jitter and varied tones
        h_seed = ((int(cx) + 200) * 73856093 + (int(cy) + 200) * 19349669) & 0xFFFFFFFF
        r = s * 0.38
        # Slight position jitter
        jx = ((h_seed >> 4 & 0xF) - 8) * s * 0.015
        jy = ((h_seed >> 8 & 0xF) - 8) * s * 0.012
        bcx, bcy = cx + jx, cy + jy
        barrel_colors = list(palette.barrel)
        body_c = barrel_colors[h_seed % len(barrel_colors)]
        shadow_c = tuple(max(0, c - 35) for c in body_c)
        outline_c = tuple(max(0, c - 40) for c in body_c)
        highlight_c = tuple(min(255, c + 22) for c in body_c)
        # Slight tilt via ellipse aspect ratio
        ar = 1.05 + (h_seed >> 12 & 0xF) / 150
        # Shadow beneath (offset)
        draw.ellipse([bcx - r * 1.05, bcy - r * 0.85,
                      bcx + r * 1.08, bcy + r * ar + r * 0.1],
                     fill=shadow_c)
        # Barrel body
        draw.ellipse([bcx - r, bcy - r * ar, bcx + r, bcy + r * ar],
                     fill=body_c, outline=outline_c,
                     width=max(1, int(s * 0.03)))
        # Metal bands — slight positional variance
        bw = max(1, int(s * 0.025))
        band_c = palette.barrel_band
        for band_frac in (-0.48, -0.02, 0.44):
            by = bcy + r * ar * band_frac
            bx_off = abs(band_frac) * 0.02 * r  # slight wobble
            draw.line([(int(bcx - r * 0.82 + bx_off), int(by)),
                       (int(bcx + r * 0.82 - bx_off), int(by))],
                      fill=band_c, width=bw)
        # Top highlight (offset)
        hr = r * 0.35
        draw.ellipse([bcx - hr + r * 0.05, bcy - r * 0.82,
                      bcx + hr + r * 0.05, bcy - r * 0.28],
                     fill=highlight_c)
    elif structure_type == "box_stack":
        # Stack of crates — each rotated independently
        h_seed = ((int(cx) + 200) * 73856093 + (int(cy) + 200) * 19349669) & 0xFFFFFFFF
        r = s * 0.64
        olw = max(1, int(s * 0.025))
        clw = max(1, int(s * 0.015))
        box_fills = list(palette.wood)
        outl_c = tuple(max(0, c - 45) for c in palette.wood[0])
        brace_c = tuple(max(0, c - 20) for c in palette.wood[0])
        # Bottom-left crate — fills ~90% of hex with stacked boxes
        a0 = _seed_angle(h_seed, -0.15, 0.15)
        f0 = box_fills[h_seed % len(box_fills)]
        p0 = _rotated_rect(cx - r * 0.72, cy + r * 0.65, r * 0.95, r * 1.0, a0)
        draw.polygon(p0, fill=f0, outline=outl_c, width=olw)
        # Bottom-right crate
        a1 = _seed_angle(h_seed >> 5, -0.12, 0.18)
        f1 = box_fills[(h_seed >> 3) % len(box_fills)]
        p1 = _rotated_rect(cx + r * 0.72, cy + r * 0.78, r * 0.90, r * 0.85, a1)
        draw.polygon(p1, fill=f1, outline=outl_c, width=olw)
        # Top crate — overlapping, different angle
        a2 = _seed_angle(h_seed >> 10, -0.2, 0.2)
        f2 = box_fills[(h_seed >> 6) % len(box_fills)]
        p2 = _rotated_rect(cx - r * 0.08, cy - r * 0.6, r * 1.3, r * 0.95, a2)
        draw.polygon(p2, fill=f2, outline=outl_c, width=olw)
        # Cross brace on top crate (rotated with it)
        cos_a2, sin_a2 = math.cos(a2), math.sin(a2)
        tcx, tcy = cx - r * 0.08, cy - r * 0.6
        for sx, sy, ex, ey in [(-0.9, -0.6, 0.9, 0.6),
                                (0.9, -0.6, -0.9, 0.6)]:
            x0 = tcx + r * sx * cos_a2 - r * sy * sin_a2
            y0 = tcy + r * sx * sin_a2 + r * sy * cos_a2
            x1 = tcx + r * ex * cos_a2 - r * ey * sin_a2
            y1 = tcy + r * ex * sin_a2 + r * ey * cos_a2
            draw.line([(int(x0), int(y0)), (int(x1), int(y1))],
                      fill=brace_c, width=clw)
    elif structure_type == "awning":
        # Colored awning / shop front canopy — rotated with asymmetry
        h_seed = ((int(cx) + 200) * 73856093 + (int(cy) + 200) * 19349669) & 0xFFFFFFFF
        angle = _seed_angle(h_seed, -0.28, 0.28)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        awning_colors = list(palette.fabric)
        color = awning_colors[h_seed % len(awning_colors)]
        dark = tuple(max(0, c - 40) for c in color)
        stripe_light = tuple(min(255, c + 35) for c in color)
        # Fills ~95% of hex
        aw = s * (0.48 + (h_seed >> 4 & 0xF) / 250)
        ah = s * (0.42 + (h_seed >> 8 & 0xF) / 300)
        # Main canopy — rotated polygon
        pts = _rotated_rect(cx, cy, aw, ah, angle)
        draw.polygon(pts, fill=color, outline=dark,
                     width=max(1, int(s * 0.03)))
        # Alternating stripes follow the rotation (perpendicular to long axis)
        num_stripes = 4
        stripe_w_f = 1.0 / (num_stripes * 2)
        for si in range(num_stripes):
            frac = si * 2 * stripe_w_f - 0.5 + stripe_w_f * 0.5
            # Stripe is a thin rotated rectangle
            stripe_cx = cx + (aw * 2 * frac) * cos_a
            stripe_cy = cy + (aw * 2 * frac) * sin_a
            stripe_pts = _rotated_rect(stripe_cx, stripe_cy,
                                       aw * stripe_w_f, ah * 0.95, angle)
            draw.polygon(stripe_pts, fill=stripe_light)
        # Re-draw outline
        draw.polygon(pts, outline=dark, width=max(1, int(s * 0.03)))
        # Scalloped bottom edge along rotation
        scallop_r = max(2, int(s * 0.05))
        num_scallops = max(3, int(aw * 2 / (scallop_r * 2.2)))
        for si in range(num_scallops):
            frac = (si + 0.5) / num_scallops - 0.5
            sx = cx + aw * 2 * frac * cos_a + ah * sin_a
            sy = cy + aw * 2 * frac * sin_a - ah * cos_a * (-1)
            draw.arc([sx - scallop_r, sy - scallop_r,
                      sx + scallop_r, sy + scallop_r],
                     0, 180, fill=dark, width=max(1, int(s * 0.02)))
        # Support poles at bottom corners
        pole_r = max(1, int(s * 0.025))
        pole_c = palette.pole
        for side in (-1, 1):
            px = cx + side * aw * cos_a + ah * sin_a
            py = cy + side * aw * sin_a - ah * cos_a * (-1)
            draw.ellipse([px - pole_r, py - pole_r, px + pole_r, py + pole_r],
                         fill=pole_c)
    elif structure_type == "tree":
        # Top-down tree — canopy circle with trunk shadow
        h_seed = ((int(cx) + 200) * 73856093 + (int(cy) + 200) * 19349669) & 0xFFFFFFFF
        canopy_colors = list(palette.tree_canopy)
        canopy_c = canopy_colors[h_seed % len(canopy_colors)]
        dark_c = tuple(max(0, c - 22) for c in canopy_c)
        light_c = tuple(min(255, c + 18) for c in canopy_c)
        trunk_c = palette.tree_trunk
        # Trunk shadow offset
        tr = s * 0.08
        draw.ellipse([cx + s * 0.04 - tr, cy + s * 0.06 - tr,
                      cx + s * 0.04 + tr, cy + s * 0.06 + tr],
                     fill=trunk_c)
        # Main canopy
        cr = s * (0.32 + (h_seed >> 4 & 0xF) / 200)
        draw.ellipse([cx - cr, cy - cr, cx + cr, cy + cr],
                     fill=canopy_c, outline=dark_c,
                     width=max(1, int(s * 0.025)))
        # Light highlight
        hr = cr * 0.45
        draw.ellipse([cx - hr - cr * 0.15, cy - hr - cr * 0.15,
                      cx + hr - cr * 0.15, cy + hr - cr * 0.15],
                     fill=light_c)
    elif structure_type == "bed":
        # Pillow accent — light rectangle in one corner of the colored hex
        r = s * 0.30
        draw.rectangle([cx - r, cy - r, cx + r, cy + r],
                       fill=palette.pillow)
    elif structure_type == "table":
        # No extra sprite — the dark hex fill IS the table
        pass
    elif structure_type == "chair":
        # No extra sprite — the lighter hex fill IS the chair
        pass
    elif structure_type == "desk":
        # Paper accent — light spot
        r = s * 0.25
        draw.rectangle([cx - r, cy - r * 0.7, cx + r, cy + r * 0.7],
                       fill=palette.paper)
    elif structure_type == "rug":
        # Small diamond pattern accent in centre
        ir = s * 0.28
        # Use first fabric color as rug accent base
        _rug_base = palette.fabric[0]
        inner_c = tuple(min(255, c + 50) for c in _rug_base)
        draw.polygon([(cx, cy - ir), (cx + ir, cy),
                      (cx, cy + ir), (cx - ir, cy)],
                     fill=inner_c)
    elif structure_type == "bookshelf":
        # Coloured book spine accents
        h_seed = ((int(cx) + 200) * 73856093 + (int(cy) + 200) * 19349669) & 0xFFFFFFFF
        book_colors = list(palette.book_spines)
        lw = max(1, int(s * 0.12))
        bc = book_colors[h_seed % len(book_colors)]
        draw.line([(int(cx), int(cy - s * 0.35)),
                   (int(cx), int(cy + s * 0.35))],
                  fill=bc, width=lw)
    elif structure_type == "chest":
        # Metal clasp accent
        cr = max(1, int(s * 0.18))
        draw.ellipse([cx - cr, cy - cr, cx + cr, cy + cr],
                     fill=palette.chest_clasp)
    elif structure_type == "farmland":
        lw = max(1, int(s * 0.02))
        for dy in (-s * 0.12, 0, s * 0.12):
            draw.line([(int(cx - s * 0.25), int(cy + dy)),
                       (int(cx + s * 0.25), int(cy + dy))],
                      fill=palette.farm_rows, width=lw)


def _draw_multi_hex_decoration(
    draw: ImageDraw.Draw,
    cx: float, cy: float,
    span_w: float, span_h: float,
    hex_size: int,
    structure_type: str,
    q: int, r: int,
    palette: StructurePalette | None = None,
) -> None:
    """Draw a decoration sprite spanning multiple hexes — rotated and asymmetric."""
    if palette is None:
        palette = STRUCTURE_PALETTES["human"]
    h_seed = ((q + 200) * 73856093 + (r + 200) * 19349669) & 0xFFFFFFFF
    angle = _seed_angle(h_seed, -0.18, 0.18)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    olw = max(1, int(hex_size * 0.03))

    if structure_type == "awning":
        awning_colors = list(palette.fabric)
        color = awning_colors[h_seed % len(awning_colors)]
        dark = tuple(max(0, c - 40) for c in color)
        stripe_light = tuple(min(255, c + 35) for c in color)
        # Canopy — asymmetric half-widths
        hw = span_w * (0.43 + (h_seed >> 4 & 0xF) / 400)
        hh = span_h * (0.40 + (h_seed >> 8 & 0xF) / 500)
        pts = _rotated_rect(cx, cy, hw, hh, angle)
        draw.polygon(pts, fill=color, outline=dark, width=olw)
        # Alternating stripes perpendicular to long axis
        num_stripes = max(3, int(hw * 2 / (hex_size * 0.25)))
        stripe_w_f = 1.0 / (num_stripes * 2)
        for si in range(num_stripes):
            frac = si * 2 * stripe_w_f - 0.5 + stripe_w_f * 0.5
            scx = cx + (hw * 2 * frac) * cos_a
            scy = cy + (hw * 2 * frac) * sin_a
            sp = _rotated_rect(scx, scy, hw * stripe_w_f, hh * 0.95, angle)
            draw.polygon(sp, fill=stripe_light)
        draw.polygon(pts, outline=dark, width=olw)
        # Scalloped bottom edge
        scallop_r = max(2, int(hex_size * 0.05))
        num_scallops = max(3, int(hw * 2 / (scallop_r * 2.2)))
        for si in range(num_scallops):
            frac = (si + 0.5) / num_scallops - 0.5
            sx = cx + hw * 2 * frac * cos_a + hh * sin_a
            sy = cy + hw * 2 * frac * sin_a - hh * cos_a * (-1)
            draw.arc([sx - scallop_r, sy - scallop_r,
                      sx + scallop_r, sy + scallop_r],
                     0, 180, fill=dark, width=max(1, int(hex_size * 0.02)))
        # Support poles at bottom corners
        pole_r = max(1, int(hex_size * 0.03))
        pole_c = palette.pole
        for side in (-1, 1):
            px = cx + side * hw * cos_a + hh * sin_a
            py = cy + side * hw * sin_a - hh * cos_a * (-1)
            draw.ellipse([px - pole_r, py - pole_r, px + pole_r, py + pole_r],
                         fill=pole_c)

    elif structure_type == "market_stall":
        canopy_colors = list(palette.fabric)
        canopy = canopy_colors[h_seed % len(canopy_colors)]
        dark = tuple(max(0, c - 45) for c in canopy)
        stripe = tuple(min(255, c + 40) for c in canopy)
        hw = span_w * (0.43 + (h_seed >> 4 & 0xF) / 400)
        hh = span_h * (0.40 + (h_seed >> 8 & 0xF) / 500)
        # Table / counter in lower portion — rotated
        tw = hw * 0.88
        th = hh * 0.32
        table_cy = cy + hh * 0.32
        tbl_pts = _rotated_rect(cx, table_cy, tw, th * 0.5, angle)
        draw.polygon(tbl_pts, fill=palette.table, outline=palette.table_dark,
                     width=olw)
        # Goods on table
        gr = max(2, int(hex_size * 0.04))
        goods_colors = list(palette.goods)
        num_goods = max(3, int(tw * 2 / (gr * 4)))
        for gi in range(num_goods):
            frac = -0.8 + 1.6 * gi / max(1, num_goods - 1)
            gx = cx + tw * frac * cos_a
            gy = table_cy + tw * frac * sin_a
            gc = goods_colors[(h_seed + gi) % len(goods_colors)]
            draw.ellipse([gx - gr, gy - gr, gx + gr, gy + gr], fill=gc)
        # Canopy in upper portion — rotated
        canopy_cy = cy - hh * 0.28
        canopy_hh = hh * 0.6
        c_pts = _rotated_rect(cx, canopy_cy, hw, canopy_hh, angle)
        draw.polygon(c_pts, fill=canopy, outline=dark, width=olw)
        # Fabric stripes
        slw = max(1, int(hex_size * 0.025))
        num_stripes = max(2, int(hw * 2 / (hex_size * 0.3)))
        for i in range(num_stripes):
            frac = (i + 1) / (num_stripes + 1) - 0.5
            dy = canopy_hh * 2 * frac
            x0 = cx + (-hw * 0.85) * cos_a - dy * sin_a
            y0 = canopy_cy + (-hw * 0.85) * sin_a + dy * cos_a
            x1 = cx + (hw * 0.85) * cos_a - dy * sin_a
            y1 = canopy_cy + (hw * 0.85) * sin_a + dy * cos_a
            draw.line([(int(x0), int(y0)), (int(x1), int(y1))],
                      fill=stripe, width=slw)
        # Front edge scallops
        scallop_r = max(2, int(hex_size * 0.04))
        num_scallops = max(3, int(hw * 2 / (scallop_r * 2.5)))
        for si in range(num_scallops):
            frac = (si + 0.5) / num_scallops - 0.5
            sx = cx + hw * 2 * frac * cos_a + canopy_hh * sin_a
            sy = canopy_cy + hw * 2 * frac * sin_a - canopy_hh * cos_a * (-1)
            draw.arc([sx - scallop_r, sy - scallop_r,
                      sx + scallop_r, sy + scallop_r],
                     0, 180, fill=dark, width=max(1, int(hex_size * 0.02)))

    elif structure_type == "wagon":
        # Top-down wagon cart — wooden bed with cargo, wheels peeking at edges
        cart_colors = list(palette.wood)
        fill_c = cart_colors[h_seed % len(cart_colors)]
        dark_c = tuple(max(0, c - 45) for c in fill_c)
        plank_c = tuple(max(0, c - 15) for c in fill_c)
        rim_c = tuple(max(0, c - 30) for c in fill_c)

        # Always orient wagon along the longest span dimension
        if span_h > span_w:
            long_span, short_span = span_h, span_w
            angle = angle + math.pi / 2
            cos_a, sin_a = math.cos(angle), math.sin(angle)
        else:
            long_span, short_span = span_w, span_h

        hw = long_span * 0.36   # half-length (along wagon)
        hh = short_span * 0.28  # half-width (across wagon)
        # Enforce minimum width so thin strips don't look silly
        min_hh = hw * 0.35
        if hh < min_hh:
            hh = min_hh

        # Wheels peek out from under the bed at the four corners (top-down = thin rectangles)
        wr_l = max(6, int(hex_size * 0.36))  # length along wagon direction
        wr_w = max(2, int(hex_size * 0.06))  # narrow width from above
        wheel_c = palette.wheel
        wheel_rim = palette.wheel_rim
        for sx in (-0.8, 0.8):
            for sy in (-1.15, 1.15):
                wx = cx + sx * hw * cos_a - sy * hh * sin_a
                wy = cy + sx * hw * sin_a + sy * hh * cos_a
                # Long edge parallel to wagon length
                w_pts = _rotated_rect(wx, wy, wr_l, wr_w, angle)
                draw.polygon(w_pts, fill=wheel_c, outline=wheel_rim,
                             width=max(1, int(hex_size * 0.015)))

        # Tongue / yoke extending from one end (drawn under bed)
        tx0 = cx + hw * 0.7 * cos_a
        ty0 = cy + hw * 0.7 * sin_a
        tx1 = tx0 + hex_size * 0.5 * cos_a
        ty1 = ty0 + hex_size * 0.5 * sin_a
        draw.line([(int(tx0), int(ty0)), (int(tx1), int(ty1))],
                  fill=dark_c, width=max(2, int(hex_size * 0.03)))
        # Yoke crossbar
        yoke_w = hex_size * 0.18
        yx0 = int(tx1 - yoke_w * sin_a)
        yy0 = int(ty1 + yoke_w * cos_a)
        yx1 = int(tx1 + yoke_w * sin_a)
        yy1 = int(ty1 - yoke_w * cos_a)
        draw.line([(yx0, yy0), (yx1, yy1)],
                  fill=dark_c, width=max(1, int(hex_size * 0.025)))

        # Wooden bed (main body on top)
        bed_pts = _rotated_rect(cx, cy, hw, hh, angle)
        draw.polygon(bed_pts, fill=fill_c, outline=dark_c, width=olw)

        # Plank grain lines (lengthwise, subtle)
        plank_lw = max(1, int(hex_size * 0.015))
        num_planks = max(2, int(hh * 2 / (hex_size * 0.25)))
        for pi in range(num_planks):
            frac = (pi + 1) / (num_planks + 1) - 0.5
            y_off = hh * 2 * frac
            lx0 = cx + (-hw * 0.85) * cos_a - y_off * sin_a
            ly0 = cy + (-hw * 0.85) * sin_a + y_off * cos_a
            lx1 = cx + (hw * 0.85) * cos_a - y_off * sin_a
            ly1 = cy + (hw * 0.85) * sin_a + y_off * cos_a
            draw.line([(int(lx0), int(ly0)), (int(lx1), int(ly1))],
                      fill=plank_c, width=plank_lw)

        # Raised side rails (top-down = darker border along long edges)
        rail_lw = max(1, int(hex_size * 0.025))
        for side in (-1.0, 1.0):
            ry = hh * side
            rx0 = cx + (-hw * 0.95) * cos_a - ry * sin_a
            ry0 = cy + (-hw * 0.95) * sin_a + ry * cos_a
            rx1 = cx + (hw * 0.95) * cos_a - ry * sin_a
            ry1 = cy + (hw * 0.95) * sin_a + ry * cos_a
            draw.line([(int(rx0), int(ry0)), (int(rx1), int(ry1))],
                      fill=rim_c, width=rail_lw)
        # Back board (short edge opposite the tongue)
        bx = -hw * 0.95
        bb0 = cx + bx * cos_a - (-hh) * sin_a
        bb0y = cy + bx * sin_a + (-hh) * cos_a
        bb1 = cx + bx * cos_a - hh * sin_a
        bb1y = cy + bx * sin_a + hh * cos_a
        draw.line([(int(bb0), int(bb0y)), (int(bb1), int(bb1y))],
                  fill=rim_c, width=rail_lw)

        # Cargo on the bed — a few small shapes (sacks, barrel top, crate)
        cargo_colors = list(palette.cargo)
        num_cargo = 2 + (h_seed >> 4) % 3
        for ci_c in range(num_cargo):
            c_seed = (h_seed + ci_c * 7919) & 0xFFFFFFFF
            frac_x = -0.5 + (c_seed % 100) / 100.0 * 0.6
            frac_y = -0.6 + (c_seed >> 8 & 0xFF) / 255.0 * 1.2
            gx = cx + hw * frac_x * cos_a - hh * frac_y * sin_a
            gy = cy + hw * frac_x * sin_a + hh * frac_y * cos_a
            cc = cargo_colors[c_seed % len(cargo_colors)]
            cr = max(4, int(hex_size * 0.12 + (c_seed >> 12 & 3) * 2))
            if c_seed % 3 == 0:
                # Round sack / barrel top
                draw.ellipse([gx - cr, gy - cr, gx + cr, gy + cr],
                             fill=cc, outline=tuple(max(0, c - 30) for c in cc),
                             width=max(1, int(hex_size * 0.015)))
            else:
                # Square crate top
                draw.rectangle([gx - cr, gy - cr, gx + cr, gy + cr],
                               fill=cc, outline=tuple(max(0, c - 30) for c in cc),
                               width=max(1, int(hex_size * 0.015)))


# ─── Data Structures ─────────────────────────────────────────────────────────

@dataclass
class HexTile:
    """A renderable hex tile with display properties."""
    hex: Hex
    color: tuple[int, int, int] = (80, 80, 80)
    label: str = ""
    discovered: bool = True
    highlight: bool = False
    icon: Optional[str] = None  # future: POI icon name
    elevation: float = 0.5
    terrain_type: str = ""
    cover: Cover = Cover.NONE
    lighting: Lighting = Lighting.BRIGHT
    structure_type: str = ""
    building_id: int = 0
    building_shape: str = ""   # "rect" or "hex"


@dataclass
class Token:
    """A token (player, NPC, creature) placed on the map."""
    hex: Hex
    label: str
    color: tuple[int, int, int] = (220, 50, 50)
    size: float = 0.6  # fraction of hex size


# ─── Renderer ─────────────────────────────────────────────────────────────────

@dataclass
class HexRenderer:
    """Renders a collection of hex tiles to a PIL Image."""
    hex_size: float = 32.0
    padding: int = 40
    background: tuple[int, int, int] = BACKGROUND_COLOR
    show_coordinates: bool = False
    title: str = ""
    organic_borders: bool = False
    draw_features: bool = True
    draw_cover: bool = True
    draw_lighting: bool = True
    biome: str = ""
    culture: str = "human"

    def render(
        self,
        tiles: list[HexTile],
        tokens: list[Token] | None = None,
    ) -> Image.Image:
        """Render hex tiles and tokens to a PIL Image.

        Rendering layers (inspired by Watabou's CityMap.hx):
          1. Hex fills with elevation shading
          2. Borders with biome boundary detection
          3. Terrain feature sprites
          4. Fog of war overlay
          5. Labels and coordinates
          6. Tokens
        """
        if not tiles:
            return Image.new("RGBA", (200, 200), self.background)

        tokens = tokens or []

        # Pre-compute pixel centres and build lookup
        centers: dict = {}
        tile_lookup: dict = {}
        for tile in tiles:
            px, py = hex_to_pixel(tile.hex, self.hex_size)
            centers[tile.hex] = (px, py)
            tile_lookup[tile.hex] = tile

        # Determine image bounds
        xs = [c[0] for c in centers.values()]
        ys = [c[1] for c in centers.values()]
        title_offset = 30 if self.title else 0
        img_w = int(max(xs) - min(xs) + self.hex_size * 2 + self.padding * 2)
        img_h = int(
            max(ys) - min(ys) + self.hex_size * 2 + self.padding * 2 + title_offset
        )
        ox = -min(xs) + self.hex_size + self.padding
        oy = -min(ys) + self.hex_size + self.padding + title_offset

        img = Image.new("RGBA", (img_w, img_h), self.background)
        draw = ImageDraw.Draw(img)

        # Draw title
        if self.title:
            try:
                font = ImageFont.truetype("arial.ttf", 18)
            except (OSError, IOError):
                font = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), self.title, font=font)
            draw.text(
                ((img_w - (bbox[2] - bbox[0])) / 2, 10),
                self.title, fill=LABEL_COLOR, font=font,
            )

        # Load fonts
        try:
            label_font = ImageFont.truetype(
                "arial.ttf", max(10, int(self.hex_size * 0.35)),
            )
        except (OSError, IOError):
            label_font = ImageFont.load_default()
        try:
            coord_font = ImageFont.truetype(
                "arial.ttf", max(8, int(self.hex_size * 0.22)),
            )
        except (OSError, IOError):
            coord_font = ImageFont.load_default()

        # Pre-compute polygon corners (with optional organic jitter)
        # Buildings get clean hex corners so walls form crisp straight lines.
        _building_set = {"house", "house_wall", "house_door",
                         "bed", "table", "chair", "desk", "rug",
                         "bookshelf", "chest"}

        # Resolve per-culture palette and structure color overrides
        _palette = STRUCTURE_PALETTES.get(self.culture, STRUCTURE_PALETTES["human"])
        _struct_colors = _structure_colors_for_palette(_palette)

        hex_polys: dict = {}
        for tile in tiles:
            cx = centers[tile.hex][0] + ox
            cy = centers[tile.hex][1] + oy
            raw = hex_corners((cx, cy), self.hex_size)
            if self.organic_borders and tile.structure_type not in _building_set:
                raw = [_corner_jitter(x, y, self.hex_size) for x, y in raw]
            hex_polys[tile.hex] = [(int(x), int(y)) for x, y in raw]

        # --- Layer 1: Hex fills (blurred terrain + crisp structures) ---
        fog_polys: list = []
        for tile in tiles:
            poly = hex_polys[tile.hex]
            base_color = tile.color
            if tile.structure_type and tile.structure_type in _struct_colors:
                base_color = _struct_colors[tile.structure_type]
            base_color = _jitter_color(base_color, tile.hex.q, tile.hex.r, amount=10)
            fill = _shade_color(base_color, tile.elevation)
            if tile.discovered:
                draw.polygon(poly, fill=fill)
            else:
                draw.polygon(poly, fill=tuple(c // 3 for c in fill))
                fog_polys.append(poly)

        # Blur the entire image for soft terrain transitions (light blur)
        blur_r = max(1, int(self.hex_size * 0.18))
        img = img.filter(ImageFilter.GaussianBlur(radius=blur_r))
        draw = ImageDraw.Draw(img)

        # Redraw title (was blurred)
        if self.title:
            try:
                title_font = ImageFont.truetype("arial.ttf", 18)
            except (OSError, IOError):
                title_font = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), self.title, font=title_font)
            draw.text(
                ((img_w - (bbox[2] - bbox[0])) / 2, 10),
                self.title, fill=LABEL_COLOR, font=title_font,
            )

        # --- Pre-compute building clusters and bounding rectangles ---
        _furniture_types = {"bed", "table", "chair", "desk", "rug",
                            "bookshelf", "chest"}
        building_types = {"house", "house_wall", "house_door"} | _furniture_types

        # Group building hexes by building_id (each _place_building call
        # assigns a unique id).  Fall back to BFS if ids are all 0.
        _bld_tiles = [
            t for t in tiles
            if t.structure_type in building_types and t.discovered
        ]
        _bld_hex_set = {t.hex for t in _bld_tiles}

        # Exclusion zone: building hexes + 3-ring buffer around them
        # Prevents large sprites/outcrops from visually bleeding into buildings
        # (sprites are drawn much larger than one hex)
        _near_building: set = set(_bld_hex_set)
        _ring = set(_bld_hex_set)
        for _ in range(3):
            _next_ring: set = set()
            for bh in _ring:
                for nb in bh.neighbors():
                    if nb not in _near_building:
                        _near_building.add(nb)
                        _next_ring.add(nb)
            _ring = _next_ring

        _id_groups: dict[int, list] = {}
        _id_shapes: dict[int, str] = {}  # building_id -> "rect" or "hex"
        for t in _bld_tiles:
            _id_groups.setdefault(t.building_id, []).append(t.hex)
            if t.building_shape:
                _id_shapes[t.building_id] = t.building_shape

        if len(_id_groups) == 1 and 0 in _id_groups:
            # No building_ids assigned — fall back to BFS
            _visited_b: set = set()
            _clusters: list[list] = []
            for start_h in _bld_hex_set:
                if start_h in _visited_b:
                    continue
                cluster: list = []
                queue = [start_h]
                while queue:
                    h = queue.pop()
                    if h in _visited_b:
                        continue
                    _visited_b.add(h)
                    cluster.append(h)
                    for nb in h.neighbors():
                        if nb in _bld_hex_set and nb not in _visited_b:
                            queue.append(nb)
                _clusters.append(cluster)
        else:
            _clusters = [
                hexes for bid, hexes in sorted(_id_groups.items()) if bid != 0
            ]
            if 0 in _id_groups:
                _clusters.append(_id_groups[0])

        # Determine shape for each cluster
        _cluster_shapes: list[str] = []
        for cluster in _clusters:
            # Check first hex's tile for building_shape
            shape = "rect"
            for h in cluster:
                t = tile_lookup.get(h)
                if t and t.building_shape:
                    shape = t.building_shape
                    break
            _cluster_shapes.append(shape)

        # Map boundary: bounding polygon of all hex corners for clipping
        _all_map_corners = []
        for poly in hex_polys.values():
            _all_map_corners.extend(poly)
        _map_x0 = min(c[0] for c in _all_map_corners)
        _map_x1 = max(c[0] for c in _all_map_corners)
        _map_y0 = min(c[1] for c in _all_map_corners)
        _map_y1 = max(c[1] for c in _all_map_corners)

        # Compute bounding geometry for each cluster
        inset = max(2, int(self.hex_size * 0.08))
        _cluster_rects: list[tuple[float, float, float, float] | None] = []
        _cluster_hex_polys: list[list[tuple] | None] = []  # for hex buildings

        for ci, cluster in enumerate(_clusters):
            shape = _cluster_shapes[ci]
            all_corners_px = []
            for h in cluster:
                all_corners_px.extend(hex_polys[h])

            if shape == "hex":
                # Build convex hull from ALL hex corners for a clean outline
                import math as _math

                all_pts = list(set(all_corners_px))

                # Andrew's monotone chain convex hull
                all_pts.sort(key=lambda p: (p[0], p[1]))

                def _cross(o, a, b):
                    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

                lower = []
                for p in all_pts:
                    while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
                        lower.pop()
                    lower.append(p)
                upper = []
                for p in reversed(all_pts):
                    while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
                        upper.pop()
                    upper.append(p)
                hull = lower[:-1] + upper[:-1]

                # Inset each hull vertex slightly toward centroid
                avg_x = sum(p[0] for p in hull) / len(hull)
                avg_y = sum(p[1] for p in hull) / len(hull)
                inset_poly = []
                for px, py in hull:
                    dx, dy = px - avg_x, py - avg_y
                    dist = (dx**2 + dy**2) ** 0.5 or 1
                    factor = max(0, dist - inset) / dist
                    inset_poly.append((avg_x + dx * factor, avg_y + dy * factor))
                _cluster_hex_polys.append(inset_poly)
                _cluster_rects.append(None)
            else:
                bx0 = min(c[0] for c in all_corners_px) + inset
                bx1 = max(c[0] for c in all_corners_px) - inset
                by0 = min(c[1] for c in all_corners_px) + inset
                by1 = max(c[1] for c in all_corners_px) - inset
                bx0 = max(bx0, _map_x0 + inset)
                bx1 = min(bx1, _map_x1 - inset)
                by0 = max(by0, _map_y0 + inset)
                by1 = min(by1, _map_y1 - inset)
                _cluster_rects.append((bx0, by0, bx1, by1))
                _cluster_hex_polys.append(None)

        # --- Layer 1b: Building drop shadows ---
        shadow_offset = max(2, int(self.hex_size * 0.15))
        shadow_img = Image.new("RGBA", img.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow_img)
        for ci in range(len(_clusters)):
            if _cluster_rects[ci]:
                bx0, by0, bx1, by1 = _cluster_rects[ci]
                shadow_draw.rectangle(
                    [bx0 + shadow_offset, by0 + shadow_offset,
                     bx1 + shadow_offset, by1 + shadow_offset],
                    fill=(15, 12, 8, 100),
                )
            elif _cluster_hex_polys[ci]:
                shifted = [(p[0] + shadow_offset, p[1] + shadow_offset)
                           for p in _cluster_hex_polys[ci]]
                shadow_draw.polygon(shifted, fill=(15, 12, 8, 100))
        shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(radius=max(2, shadow_offset)))
        img = Image.alpha_composite(img, shadow_img)
        draw = ImageDraw.Draw(img)

        # --- Layer 1c: Building fills ---
        bld_fill = _struct_colors.get("house", (215, 180, 130))
        for ci in range(len(_clusters)):
            if _cluster_rects[ci]:
                bx0, by0, bx1, by1 = _cluster_rects[ci]
                draw.rectangle([bx0, by0, bx1, by1], fill=bld_fill)
            elif _cluster_hex_polys[ci]:
                draw.polygon(_cluster_hex_polys[ci], fill=bld_fill)

        # --- Layer 1d: Faint hex grid on building floors ---
        hex_grid_color = (160, 130, 90, 90)
        hex_grid_width = max(1, int(self.hex_size * 0.04))
        grid_img = Image.new("RGBA", img.size, (0, 0, 0, 0))
        grid_draw = ImageDraw.Draw(grid_img)
        for ci, cluster in enumerate(_clusters):
            for h in cluster:
                t = tile_lookup.get(h)
                if t and t.structure_type in building_types:
                    poly = hex_polys[h]
                    pts = list(poly) + [poly[0]]
                    for i in range(len(pts) - 1):
                        grid_draw.line([pts[i], pts[i + 1]],
                                       fill=hex_grid_color,
                                       width=hex_grid_width)
        img = Image.alpha_composite(img, grid_img)
        draw = ImageDraw.Draw(img)

        # Non-building structure fills
        for tile in tiles:
            if not tile.structure_type or not tile.discovered:
                continue
            if tile.structure_type in building_types:
                continue
            poly = hex_polys[tile.hex]
            base_color = _struct_colors.get(tile.structure_type, tile.color)
            base_color = _jitter_color(base_color, tile.hex.q, tile.hex.r, amount=8)
            fill = _shade_color(base_color, tile.elevation)
            draw.polygon(poly, fill=fill)

        # --- Layer 1e: Building outlines ---
        wall_lw = max(4, int(self.hex_size * 0.18))
        wall_color = (85, 75, 60)
        window_color = (140, 175, 210)
        corner_post_r = max(3, int(self.hex_size * 0.10))
        corner_post_color = (110, 100, 80)

        for ci, cluster in enumerate(_clusters):
            shape = _cluster_shapes[ci]

            door_hexes = [
                h for h in cluster
                if tile_lookup[h].structure_type == "house_door"
            ]

            if shape == "hex":
                # --- Hex building outline ---
                hp = _cluster_hex_polys[ci]
                if not hp:
                    continue
                # Draw all wall segments first
                pts = list(hp) + [hp[0]]
                for i in range(len(pts) - 1):
                    draw.line([pts[i], pts[i + 1]], fill=wall_color, width=wall_lw)
                # Corner posts at polygon vertices
                for px, py in hp:
                    draw.ellipse(
                        [px - corner_post_r, py - corner_post_r,
                         px + corner_post_r, py + corner_post_r],
                        fill=corner_post_color,
                    )
                # Erase door area: find closest hull point to door hex,
                # paint over with floor color, then draw threshold
                if door_hexes:
                    dh = door_hexes[0]
                    dcx = centers[dh][0] + ox
                    dcy = centers[dh][1] + oy
                    # Find the point on the hull boundary closest to the door
                    best_pt = hp[0]
                    best_d2 = 1e18
                    for si in range(len(pts) - 1):
                        # Project door center onto segment
                        ax, ay = pts[si]
                        bx, by = pts[si + 1]
                        abx, aby = bx - ax, by - ay
                        ab_len2 = abx**2 + aby**2
                        if ab_len2 < 1:
                            t = 0.0
                        else:
                            t = max(0.0, min(1.0, ((dcx - ax) * abx + (dcy - ay) * aby) / ab_len2))
                        cpx, cpy = ax + t * abx, ay + t * aby
                        d2 = (cpx - dcx)**2 + (cpy - dcy)**2
                        if d2 < best_d2:
                            best_d2 = d2
                            best_pt = (cpx, cpy)
                    # Compute direction from building center to door point
                    avg_x = sum(p[0] for p in hp) / len(hp)
                    avg_y = sum(p[1] for p in hp) / len(hp)
                    dx = best_pt[0] - avg_x
                    dy = best_pt[1] - avg_y
                    d_len = (dx**2 + dy**2) ** 0.5 or 1
                    # Perpendicular to the outward direction = along the wall
                    perp_x, perp_y = -dy / d_len, dx / d_len
                    gap = self.hex_size * 1.2
                    gs = (int(best_pt[0] - perp_x * gap), int(best_pt[1] - perp_y * gap))
                    ge = (int(best_pt[0] + perp_x * gap), int(best_pt[1] + perp_y * gap))
                    # Erase wall with thick floor-colored line
                    draw.line([gs, ge], fill=bld_fill, width=wall_lw + 4)
                    # Draw visible threshold
                    draw.line([gs, ge], fill=(175, 145, 100),
                              width=max(1, wall_lw // 3))
                    # Door posts
                    post_r = max(2, int(self.hex_size * 0.12))
                    draw.ellipse([gs[0]-post_r, gs[1]-post_r,
                                  gs[0]+post_r, gs[1]+post_r],
                                 fill=corner_post_color)
                    draw.ellipse([ge[0]-post_r, ge[1]-post_r,
                                  ge[0]+post_r, ge[1]+post_r],
                                 fill=corner_post_color)
            else:
                # --- Rectangular building outline ---
                rect = _cluster_rects[ci]
                if not rect:
                    continue
                bx0, by0, bx1, by1 = rect

                rect_corners = [
                    (bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1),
                ]
                sides = [
                    (rect_corners[0], rect_corners[1]),  # top
                    (rect_corners[1], rect_corners[2]),  # right
                    (rect_corners[2], rect_corners[3]),  # bottom
                    (rect_corners[3], rect_corners[0]),  # left
                ]

                door_side = -1
                if door_hexes:
                    dh = door_hexes[0]
                    dcx = centers[dh][0] + ox
                    dcy = centers[dh][1] + oy
                    dists = [
                        abs(dcy - by0), abs(dcx - bx1),
                        abs(dcy - by1), abs(dcx - bx0),
                    ]
                    door_side = dists.index(min(dists))

                for si, (p1, p2) in enumerate(sides):
                    if si == door_side:
                        mx = (p1[0] + p2[0]) / 2
                        my = (p1[1] + p2[1]) / 2
                        seg_dx = p2[0] - p1[0]
                        seg_dy = p2[1] - p1[1]
                        seg_len = (seg_dx**2 + seg_dy**2) ** 0.5 or 1
                        ux, uy = seg_dx / seg_len, seg_dy / seg_len
                        gap = self.hex_size * 1.0
                        gs = (int(mx - ux * gap), int(my - uy * gap))
                        ge = (int(mx + ux * gap), int(my + uy * gap))
                        draw.line([p1, gs], fill=wall_color, width=wall_lw)
                        draw.line([ge, p2], fill=wall_color, width=wall_lw)
                        draw.line(
                            [gs, ge], fill=(175, 145, 100),
                            width=max(1, wall_lw // 3),
                        )
                        post_r = max(2, int(self.hex_size * 0.06))
                        draw.ellipse(
                            [gs[0] - post_r, gs[1] - post_r,
                             gs[0] + post_r, gs[1] + post_r],
                            fill=corner_post_color,
                        )
                        draw.ellipse(
                            [ge[0] - post_r, ge[1] - post_r,
                             ge[0] + post_r, ge[1] + post_r],
                            fill=corner_post_color,
                        )
                    else:
                        seg_len = (
                            (p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2
                        ) ** 0.5
                        h_val = (
                            (cluster[0].q + 100) * 73856093
                            + si * 19349669
                        ) & 0xFFFFFFFF
                        num_windows = 0
                        if self.hex_size >= 16 and seg_len > self.hex_size * 2:
                            num_windows = max(
                                1, int(seg_len / (self.hex_size * 1.5)),
                            )
                            if h_val % 3 == 0:
                                num_windows = 0

                        draw.line([p1, p2], fill=wall_color, width=wall_lw)
                        if num_windows > 0:
                            win_r = max(2, int(self.hex_size * 0.06))
                            spacing = 1.0 / (num_windows + 1)
                            for wi in range(num_windows):
                                t = spacing * (wi + 1)
                                wx = int(p1[0] + (p2[0] - p1[0]) * t)
                                wy = int(p1[1] + (p2[1] - p1[1]) * t)
                                draw.rectangle(
                                    [wx - win_r, wy - win_r,
                                     wx + win_r, wy + win_r],
                                    fill=window_color,
                                    outline=(70, 55, 35), width=1,
                                )

                for cx_p, cy_p in rect_corners:
                    draw.ellipse(
                        [cx_p - corner_post_r, cy_p - corner_post_r,
                         cx_p + corner_post_r, cy_p + corner_post_r],
                        fill=corner_post_color,
                    )

        # --- Layer 1f: Fence rendering (drawn as post-and-rail) ---
        for tile in tiles:
            if tile.structure_type != "fence" or not tile.discovered:
                continue
            cx1 = centers[tile.hex][0] + ox
            cy1 = centers[tile.hex][1] + oy
            # Draw fence post
            pr = max(2, int(self.hex_size * 0.06))
            draw.ellipse([cx1-pr, cy1-pr, cx1+pr, cy1+pr], fill=(100, 80, 40))
            # Connect to neighboring fences
            for nb in tile.hex.neighbors():
                nt = tile_lookup.get(nb)
                if not nt or nt.structure_type != "fence":
                    continue
                cx2 = centers[nb][0] + ox
                cy2 = centers[nb][1] + oy
                draw.line(
                    [(int(cx1), int(cy1)), (int(cx2), int(cy2))],
                    fill=(120, 95, 50),
                    width=max(1, int(self.hex_size * 0.04)),
                )

        # --- Layer 1g: City wall rendering (thick fortification lines) ---
        city_wall_types = {"city_wall_stone", "city_wall_lumber"}
        _DIR_EDGE_CW = {0: (0, 1), 1: (5, 0), 2: (4, 5), 3: (3, 4), 4: (2, 3), 5: (1, 2)}
        cw_lw = max(5, int(self.hex_size * 0.22))
        cw_post_r = max(3, int(self.hex_size * 0.12))
        for tile in tiles:
            if tile.structure_type not in city_wall_types or not tile.discovered:
                continue
            is_stone = tile.structure_type == "city_wall_stone"
            wall_fill = (105, 100, 90) if is_stone else (110, 88, 55)
            post_fill = (85, 80, 70) if is_stone else (90, 72, 45)
            poly = hex_polys[tile.hex]
            neighbors_list = tile.hex.neighbors()
            for dir_idx, nb in enumerate(neighbors_list):
                nt = tile_lookup.get(nb)
                # Draw wall edge on sides facing non-wall, non-building hexes
                if nt and (nt.structure_type in city_wall_types
                           or nt.structure_type in building_types
                           or nt.structure_type == "watchtower"):
                    continue
                ca, cb = _DIR_EDGE_CW[dir_idx]
                ax, ay = poly[ca]
                bx, by = poly[cb]
                draw.line([(ax, ay), (bx, by)], fill=wall_fill, width=cw_lw)
                # Corner posts
                draw.ellipse(
                    [ax - cw_post_r, ay - cw_post_r,
                     ax + cw_post_r, ay + cw_post_r],
                    fill=post_fill,
                )
                draw.ellipse(
                    [bx - cw_post_r, by - cw_post_r,
                     bx + cw_post_r, by + cw_post_r],
                    fill=post_fill,
                )

        # --- Layer 1h: Watchtower rendering ---
        # The "watchtower" structure_type marks the centre hex of a
        # radius-6 tower.  Draw a prominent turret marker so it stands
        # out from the surrounding wall-material ring.
        tower_lw = max(6, int(self.hex_size * 0.35))
        tower_post_r = max(6, int(self.hex_size * 0.22))
        for tile in tiles:
            if tile.structure_type != "watchtower" or not tile.discovered:
                continue
            poly = hex_polys[tile.hex]
            cx_t = centers[tile.hex][0] + ox
            cy_t = centers[tile.hex][1] + oy
            tower_fill = (95, 80, 55)
            tower_outline = (60, 48, 30)
            # Thick hex outline for the turret cap
            draw.polygon(poly, fill=_struct_colors.get("watchtower", (140, 120, 85)),
                         outline=tower_outline, width=tower_lw)
            # Central post / flagpole
            draw.ellipse(
                [cx_t - tower_post_r, cy_t - tower_post_r,
                 cx_t + tower_post_r, cy_t + tower_post_r],
                fill=tower_fill,
                outline=tower_outline,
                width=max(2, int(self.hex_size * 0.06)),
            )
            # Crenellation dots on each corner
            cren_r = max(3, int(self.hex_size * 0.10))
            for px_c, py_c in poly:
                draw.ellipse(
                    [px_c - cren_r, py_c - cren_r,
                     px_c + cren_r, py_c + cren_r],
                    fill=tower_outline,
                )

        # --- Layer 2: Only highlight outlines (no terrain grid lines) ---
        for tile in tiles:
            if not tile.discovered:
                continue
            if tile.highlight:
                draw.polygon(hex_polys[tile.hex], outline=HIGHLIGHT_COLOR, width=2)

        # --- Layer 2a: Cliff / ledge edge rendering ---
        # Three-pass approach:
        #   1. Collect candidate steep edges with pixel coordinates
        #   2. Cluster edges sharing a vertex (endpoint) into linear chains
        #   3. Render only chains of 4+ edges for cohesive escarpments
        _ELEV_DROP_THRESHOLD = 0.18
        _MIN_CLIFF_CHAIN = 4   # minimum connected edges forming a cliff line
        _DIR_EDGE_CLIFF = {0: (0, 1), 1: (5, 0), 2: (4, 5), 3: (3, 4), 4: (2, 3), 5: (1, 2)}

        # Pass 1: Collect all candidate cliff edges with geometry
        cliff_candidates: list = []  # list of dicts
        seen_pairs: set = set()
        for tile in tiles:
            if not tile.discovered:
                continue
            # Skip cliff rendering inside buildings
            if tile.structure_type in building_types:
                continue
            h = tile.hex
            corners = hex_corners((centers[h][0] + ox, centers[h][1] + oy), self.hex_size)
            for dir_idx, nb in enumerate(h.neighbors()):
                nb_tile = tile_lookup.get(nb)
                if not nb_tile or not nb_tile.discovered:
                    continue
                pair = (min(id(tile), id(nb_tile)), max(id(tile), id(nb_tile)))
                if pair in seen_pairs:
                    continue
                drop = tile.elevation - nb_tile.elevation
                if abs(drop) < _ELEV_DROP_THRESHOLD:
                    continue
                seen_pairs.add(pair)
                ca, cb = _DIR_EDGE_CLIFF[dir_idx]
                p1 = corners[ca]
                p2 = corners[cb]
                # Round vertex coords to ints for reliable matching
                v1 = (round(p1[0]), round(p1[1]))
                v2 = (round(p2[0]), round(p2[1]))
                cliff_candidates.append({
                    "idx": len(cliff_candidates),
                    "p1": p1, "p2": p2, "v1": v1, "v2": v2,
                    "drop": drop,
                })

        # Pass 2: Build vertex adjacency graph and cluster into chains.
        # Two edges are connected only if they share a vertex endpoint,
        # producing linear cliff lines rather than sprawling hex blobs.
        from collections import defaultdict as _ddict
        vtx_to_edges: dict = _ddict(list)
        for c in cliff_candidates:
            vtx_to_edges[c["v1"]].append(c["idx"])
            vtx_to_edges[c["v2"]].append(c["idx"])

        visited_edges: set = set()
        cliff_chains: list = []
        for c in cliff_candidates:
            if c["idx"] in visited_edges:
                continue
            chain: list = []
            queue = [c["idx"]]
            visited_edges.add(c["idx"])
            while queue:
                cur = queue.pop(0)
                chain.append(cur)
                cur_info = cliff_candidates[cur]
                for vtx in (cur_info["v1"], cur_info["v2"]):
                    for adj_idx in vtx_to_edges[vtx]:
                        if adj_idx not in visited_edges:
                            visited_edges.add(adj_idx)
                            queue.append(adj_idx)
            cliff_chains.append(chain)

        # Collect indices of edges in sufficiently large chains
        valid_cliff_indices: set = set()
        for chain in cliff_chains:
            if len(chain) >= _MIN_CLIFF_CHAIN:
                valid_cliff_indices.update(chain)

        # Pass 3: Render validated cliff edges
        cliff_overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        cliff_draw = ImageDraw.Draw(cliff_overlay)
        for ci in valid_cliff_indices:
            info = cliff_candidates[ci]
            p1, p2 = info["p1"], info["p2"]
            drop = info["drop"]
            intensity = min(1.0, (abs(drop) - _ELEV_DROP_THRESHOLD) / 0.20)
            # Edge direction vector
            edge_dx = p2[0] - p1[0]
            edge_dy = p2[1] - p1[1]
            edge_len = max(1, (edge_dx**2 + edge_dy**2) ** 0.5)
            # Normal pointing toward the lower hex
            if drop > 0:
                nx, ny = -edge_dy / edge_len, edge_dx / edge_len
            else:
                nx, ny = edge_dy / edge_len, -edge_dx / edge_len
            # Wide band extending into the lower hex
            band_depth = self.hex_size * (0.35 + 0.30 * intensity)
            band_poly = [
                (int(p1[0]), int(p1[1])),
                (int(p2[0]), int(p2[1])),
                (int(p2[0] + nx * band_depth), int(p2[1] + ny * band_depth)),
                (int(p1[0] + nx * band_depth), int(p1[1] + ny * band_depth)),
            ]
            # Dark cliff face fill
            band_alpha = int(70 + 90 * intensity)
            cliff_draw.polygon(band_poly, fill=(55, 52, 48, band_alpha))
            # Dense parallel hatch strokes within the band
            num_hatches = max(5, int(edge_len / (self.hex_size * 0.06)))
            hatch_alpha = int(80 + 100 * intensity)
            hatch_color = (35, 32, 28, hatch_alpha)
            hlw = max(1, int(self.hex_size * 0.025))
            for hi in range(num_hatches):
                t = (hi + 0.3) / num_hatches
                mx = p1[0] + edge_dx * t
                my = p1[1] + edge_dy * t
                h_var = 0.6 + 0.7 * (((hi * 48271 + int(p1[0])) >> 3) & 0xFF) / 255.0
                hl = band_depth * h_var
                cliff_draw.line(
                    [(int(mx), int(my)),
                     (int(mx + nx * hl), int(my + ny * hl))],
                    fill=hatch_color, width=hlw,
                )
            # Dark edge line on cliff lip
            edge_lw = max(2, int(self.hex_size * 0.06 * (0.7 + 0.3 * intensity)))
            cliff_draw.line(
                [(int(p1[0]), int(p1[1])), (int(p2[0]), int(p2[1]))],
                fill=(40, 38, 32, int(120 + 100 * intensity)), width=edge_lw,
            )
        img = Image.alpha_composite(img, cliff_overlay)
        draw = ImageDraw.Draw(img)

        # --- Layer 2b: Water rendering (depth-based, drawn crisp after blur) ---
        water_types = {"water", "ocean", "lake", "coastal"}
        for tile in tiles:
            if not tile.discovered:
                continue
            tt = tile.terrain_type if not tile.structure_type else ""
            if tt not in water_types:
                continue
            cx = centers[tile.hex][0] + ox
            cy = centers[tile.hex][1] + oy
            _draw_water_hex(
                draw, cx, cy, self.hex_size, hex_polys[tile.hex],
                tile.hex.q, tile.hex.r, tile_lookup, tile, ox, oy, centers,
            )

        # --- Layer 3: Terrain features + structure symbols ---
        if self.draw_features:
            # Cluster detection on actual encounter terrain types
            _cluster_terrain = {"grass", "dirt", "stone", "sand", "marsh", "mud"}
            _large_sprite_placed: set = set()

            # Determine what large sprite to use based on biome
            _forest_biomes = {"forest", "jungle", "taiga"}
            _uses_trees = self.biome in _forest_biomes

            for ctype in _cluster_terrain:
                clusters = _find_clusters(tile_lookup, ctype)
                for cluster in clusters:
                    if len(cluster) < 3:
                        continue
                    cluster_list = sorted(cluster, key=lambda h: (h.q, h.r))
                    h_seed = sum(h.q * 73 + h.r * 97 for h in cluster_list) & 0xFFFFFFFF
                    # Density: stone/sand get sparser boulders, vegetation gets more
                    if ctype in ("stone", "sand"):
                        num_large = max(1, len(cluster) // 12)
                    else:
                        num_large = max(1, len(cluster) // 4)

                    for i in range(num_large):
                        idx = (h_seed + i * 83492791) % len(cluster_list)
                        h = cluster_list[idx]
                        # Skip hexes near buildings
                        if h in _near_building:
                            continue
                        # Must have >=2 neighbors in cluster to be interior
                        nb_in = sum(1 for nb in h.neighbors() if nb in cluster)
                        if nb_in < 2:
                            continue
                        _large_sprite_placed.add(h)
                        pcx = centers[h][0] + ox
                        pcy = centers[h][1] + oy
                        info = _resolve_feature(ctype, self.biome)
                        if not info:
                            continue
                        _, _, base_color = info
                        jc = _jitter_color(base_color, h.q, h.r, amount=20)
                        if ctype == "grass" and _uses_trees:
                            _draw_large_tree(draw, pcx, pcy, self.hex_size, jc)
                        elif ctype == "stone":
                            _draw_large_boulder(draw, pcx, pcy, self.hex_size,
                                                _jitter_color((148, 140, 128), h.q, h.r, 18))
                        elif ctype == "sand":
                            _draw_large_boulder(draw, pcx, pcy, self.hex_size,
                                                _jitter_color((185, 168, 140), h.q, h.r, 15))
                        else:
                            _draw_large_bush(draw, pcx, pcy, self.hex_size, jc)

            # --- Impassable rock outcrops (max 6 per map) ---
            # Uses farthest-point sampling across ALL stone hexes so outcrops
            # are spread evenly across the map instead of clustering together.
            _MAX_OUTCROPS = 6
            _outcrop_count = 0
            _outcrop_positions: list = []  # pixel coords of placed outcrops
            _stone_base = TERRAIN_COLORS.get("stone", (168, 165, 160))

            # Gather all stone hexes that have enough stone neighbours for an
            # outcrop anchor (at least 2 stone neighbours not yet placed).
            # Exclude hexes near buildings so outcrops don't bleed into them.
            _all_stone = set()
            for h, tile in tile_lookup.items():
                tt = tile.terrain_type if not tile.structure_type else ""
                if tt == "stone" and h not in _near_building:
                    _all_stone.add(h)

            # Build candidate anchors: stone hexes with ≥2 stone neighbours
            def _stone_candidates():
                avail = _all_stone - _large_sprite_placed
                cands = []
                for h in avail:
                    n_count = sum(1 for nb in h.neighbors()
                                 if nb in avail)
                    if n_count >= 2:
                        cands.append(h)
                return cands

            # Farthest-point sampling with randomness: pick from the top
            # candidates by distance so placement is spread but not geometric.
            import math as _m
            import random as _oc_rng_mod
            _oc_rng = _oc_rng_mod.Random(sum(h.q * 73 + h.r * 97
                                              for h in _all_stone) & 0xFFFFFFFF)
            _map_cx = sum(c[0] for c in centers.values()) / max(len(centers), 1) + ox
            _map_cy = sum(c[1] for c in centers.values()) / max(len(centers), 1) + oy

            for _ in range(_MAX_OUTCROPS):
                cands = _stone_candidates()
                if len(cands) < 3:
                    break

                if not _outcrop_positions:
                    # First outcrop: pick randomly from the inner 40% of
                    # candidates (by distance to centre) — near centre-ish
                    cands.sort(key=lambda h: ((centers[h][0] + ox - _map_cx)**2
                                              + (centers[h][1] + oy - _map_cy)**2))
                    pool_size = max(1, len(cands) * 2 // 5)
                    best_hex = _oc_rng.choice(cands[:pool_size])
                else:
                    # Subsequent: rank by distance from nearest existing outcrop,
                    # then pick randomly from the top 25%
                    def _min_dist(h):
                        px, py = centers[h][0] + ox, centers[h][1] + oy
                        return min(((px - opx)**2 + (py - opy)**2) ** 0.5
                                   for opx, opy in _outcrop_positions)
                    cands.sort(key=_min_dist, reverse=True)
                    # If even the farthest is too close, stop
                    if _min_dist(cands[0]) < self.hex_size * 4:
                        break
                    pool_size = max(1, len(cands) // 4)
                    best_hex = _oc_rng.choice(cands[:pool_size])

                # BFS from anchor to gather 3-9 stone hexes for this outcrop
                avail_set = _all_stone - _large_sprite_placed
                # Interior score decides outcrop size
                local_avail = [h for h in cands
                               if ((centers[h][0] + ox - centers[best_hex][0] - ox)**2
                                   + (centers[h][1] + oy - centers[best_hex][1] - oy)**2) ** 0.5
                               < self.hex_size * 6]
                if len(local_avail) >= 12:
                    outcrop_size = 9
                elif len(local_avail) >= 7:
                    outcrop_size = 6
                else:
                    outcrop_size = 3

                outcrop_hexes = []
                visited_oc = {best_hex}
                queue_oc = [best_hex]
                while queue_oc and len(outcrop_hexes) < outcrop_size:
                    cur = queue_oc.pop(0)
                    if cur in _large_sprite_placed or cur not in avail_set:
                        for nb in cur.neighbors():
                            if nb in avail_set and nb not in visited_oc:
                                visited_oc.add(nb)
                                queue_oc.append(nb)
                        continue
                    outcrop_hexes.append(cur)
                    for nb in cur.neighbors():
                        if nb in avail_set and nb not in visited_oc:
                            visited_oc.add(nb)
                            queue_oc.append(nb)

                if len(outcrop_hexes) < 3:
                    break

                for h in outcrop_hexes:
                    _large_sprite_placed.add(h)

                anchor_px = centers[best_hex][0] + ox
                anchor_py = centers[best_hex][1] + oy
                _outcrop_positions.append((anchor_px, anchor_py))
                _draw_impassable_outcrop(
                    draw, anchor_px, anchor_py, self.hex_size,
                    _jitter_color(_stone_base, best_hex.q, best_hex.r, 15),
                    len(outcrop_hexes), centers, outcrop_hexes, ox, oy,
                )
                _outcrop_count += 1

            # Regular per-hex features (skip hexes that got large sprites)
            # --- Cobblestone brick pattern (drawn & clipped per hex) ---
            _cobble_struct_ok = {
                "", "road", "barrel", "crate", "cart", "hay_bale",
                "box_stack", "awning", "market_stall", "well",
                "road_wall", "street_lamp",
            }
            _cobble_hexes = [
                t for t in tiles
                if t.discovered
                and t.terrain_type == "cobblestone"
                and (not t.structure_type or t.structure_type in _cobble_struct_ok)
            ]
            if _cobble_hexes:
                cobble_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
                cobble_draw = ImageDraw.Draw(cobble_layer)
                cobble_mask = Image.new("L", img.size, 0)
                mask_draw = ImageDraw.Draw(cobble_mask)
                for tile in _cobble_hexes:
                    cx = centers[tile.hex][0] + ox
                    cy = centers[tile.hex][1] + oy
                    poly = hex_polys[tile.hex]
                    _draw_cobblestone_hex(
                        cobble_draw, cx, cy, self.hex_size,
                        tile.hex.q, tile.hex.r, poly,
                    )
                    mask_draw.polygon(poly, fill=255)
                # Multiply drawn alpha with hex mask (don't replace — that
                # turns transparent gaps into solid black).
                drawn_alpha = cobble_layer.split()[3]
                clipped_alpha = ImageChops.multiply(drawn_alpha, cobble_mask)
                cobble_layer.putalpha(clipped_alpha)
                img = Image.alpha_composite(img, cobble_layer)
                draw = ImageDraw.Draw(img)

            # --- Gravel pebble texture ---
            _gravel_struct_ok = {
                "", "barrel", "crate", "cart", "hay_bale",
                "box_stack", "awning", "market_stall", "well",
                "road_wall", "street_lamp",
            }
            _gravel_hexes = [
                t for t in tiles
                if t.discovered
                and t.terrain_type == "gravel"
                and (not t.structure_type or t.structure_type in _gravel_struct_ok)
            ]
            if _gravel_hexes:
                gravel_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
                gravel_draw = ImageDraw.Draw(gravel_layer)
                gravel_mask = Image.new("L", img.size, 0)
                gmask_draw = ImageDraw.Draw(gravel_mask)
                for tile in _gravel_hexes:
                    cx = centers[tile.hex][0] + ox
                    cy = centers[tile.hex][1] + oy
                    poly = hex_polys[tile.hex]
                    _draw_gravel_hex(
                        gravel_draw, cx, cy, self.hex_size,
                        tile.hex.q, tile.hex.r, poly,
                    )
                    gmask_draw.polygon(poly, fill=255)
                drawn_alpha = gravel_layer.split()[3]
                clipped_alpha = ImageChops.multiply(drawn_alpha, gravel_mask)
                gravel_layer.putalpha(clipped_alpha)
                img = Image.alpha_composite(img, gravel_layer)
                draw = ImageDraw.Draw(img)

            # --- Mud smearing on road hexes ---
            _road_hexes = [
                t for t in tiles
                if t.discovered and t.structure_type == "road"
            ]
            if _road_hexes:
                mud_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
                mud_draw = ImageDraw.Draw(mud_layer)
                mud_mask = Image.new("L", img.size, 0)
                mud_mask_draw = ImageDraw.Draw(mud_mask)
                for tile in _road_hexes:
                    cx = centers[tile.hex][0] + ox
                    cy = centers[tile.hex][1] + oy
                    _draw_road_mud(
                        mud_draw, cx, cy, self.hex_size,
                        tile.hex.q, tile.hex.r,
                    )
                    mud_mask_draw.polygon(hex_polys[tile.hex], fill=255)
                mud_alpha = mud_layer.split()[3]
                clipped_mud = ImageChops.multiply(mud_alpha, mud_mask)
                mud_layer.putalpha(clipped_mud)
                img = Image.alpha_composite(img, mud_layer)
                draw = ImageDraw.Draw(img)

            # --- Multi-hex decoration clusters (awnings, market stalls, wagons) ---
            _multi_hex_types = {"awning", "market_stall", "wagon"}
            # Types that should render OVER road_wall / street_lamp
            _over_road_types = {"awning", "market_stall", "wagon"}
            _multi_drawn: set = set()  # hexes already drawn as part of a cluster
            _multi_tiles = {}
            for tile in tiles:
                if tile.discovered and tile.structure_type in _multi_hex_types:
                    _multi_tiles[tile.hex] = tile
            # Find connected clusters of same type
            _multi_visited: set = set()
            _multi_clusters: list[tuple[str, list]] = []
            for h, tile in _multi_tiles.items():
                if h in _multi_visited:
                    continue
                stype = tile.structure_type
                cluster_hexes = []
                stack = [h]
                while stack:
                    cur = stack.pop()
                    if cur in _multi_visited:
                        continue
                    ct = _multi_tiles.get(cur)
                    if not ct or ct.structure_type != stype:
                        continue
                    _multi_visited.add(cur)
                    cluster_hexes.append(cur)
                    for nb in cur.neighbors():
                        if nb not in _multi_visited and nb in _multi_tiles:
                            stack.append(nb)
                if cluster_hexes:
                    _multi_clusters.append((stype, cluster_hexes))
            # Draw each multi-hex cluster as one large sprite
            for stype, hexes in _multi_clusters:
                for ch in hexes:
                    _multi_drawn.add(ch)

            # --- Draw single-hex structures FIRST (road_wall, street_lamp
            #     drawn before multi-hex stalls/awnings/wagons) ---
            _under_road_types = {"road_wall", "street_lamp"}
            for tile in tiles:
                if not tile.discovered:
                    continue
                cx = centers[tile.hex][0] + ox
                cy = centers[tile.hex][1] + oy
                if tile.structure_type:
                    if tile.structure_type not in building_types:
                        if tile.hex in _multi_drawn:
                            continue
                        # Draw road_wall / street_lamp first (under stalls)
                        if tile.structure_type in _under_road_types:
                            _ra = None
                            if tile.structure_type == "road_wall":
                                _ra = _compute_road_angle(
                                    tile.hex, tile_lookup, centers, ox, oy,
                                )
                            _draw_structure_symbol(
                                draw, cx, cy, self.hex_size, tile.structure_type,
                                road_angle=_ra, palette=_palette,
                            )

            # --- Draw multi-hex clusters ON TOP of road walls/lamps ---
            for stype, hexes in _multi_clusters:
                all_cxs = [centers[h][0] + ox for h in hexes]
                all_cys = [centers[h][1] + oy for h in hexes]
                span_cx = (min(all_cxs) + max(all_cxs)) / 2
                span_cy = (min(all_cys) + max(all_cys)) / 2
                all_corners = []
                for h in hexes:
                    all_corners.extend(hex_polys[h])
                bx0 = min(c[0] for c in all_corners)
                bx1 = max(c[0] for c in all_corners)
                by0 = min(c[1] for c in all_corners)
                by1 = max(c[1] for c in all_corners)
                span_w = bx1 - bx0
                span_h = by1 - by0
                _draw_multi_hex_decoration(
                    draw, span_cx, span_cy, span_w, span_h,
                    self.hex_size, stype, hexes[0].q, hexes[0].r,
                    palette=_palette,
                )

            # --- Draw remaining single-hex structures (carts, barrels, etc.) ---
            for tile in tiles:
                if not tile.discovered:
                    continue
                cx = centers[tile.hex][0] + ox
                cy = centers[tile.hex][1] + oy
                if tile.structure_type:
                    if tile.structure_type not in building_types:
                        if tile.hex in _multi_drawn:
                            continue
                        if tile.structure_type in _under_road_types:
                            continue  # already drawn above
                        _ra = None
                        if tile.structure_type == "road_wall":
                            _ra = _compute_road_angle(
                                tile.hex, tile_lookup, centers, ox, oy,
                            )
                        _draw_structure_symbol(
                            draw, cx, cy, self.hex_size, tile.structure_type,
                            road_angle=_ra, palette=_palette,
                        )
                elif tile.terrain_type:
                    tt = tile.terrain_type
                    if tt in water_types:
                        continue
                    if tile.hex in _large_sprite_placed:
                        continue
                    # Skip terrain features near buildings (they bleed visually)
                    if tile.hex in _near_building:
                        continue
                    _draw_hex_features(
                        draw, cx, cy, self.hex_size,
                        tt, tile.hex.q, tile.hex.r, self.biome,
                    )

        # --- Layer 3b: Redraw building fills over any sprites that bled in ---
        if _clusters:
            bld_fill = _struct_colors.get("house", (215, 180, 130))
            for ci in range(len(_clusters)):
                if _cluster_rects[ci]:
                    bx0, by0, bx1, by1 = _cluster_rects[ci]
                    draw.rectangle([bx0, by0, bx1, by1], fill=bld_fill)
                elif _cluster_hex_polys[ci]:
                    draw.polygon(_cluster_hex_polys[ci], fill=bld_fill)

            # Re-draw faint hex grid on floors
            grid_img2 = Image.new("RGBA", img.size, (0, 0, 0, 0))
            grid_draw2 = ImageDraw.Draw(grid_img2)
            for ci, cluster in enumerate(_clusters):
                for h in cluster:
                    t = tile_lookup.get(h)
                    if t and t.structure_type in building_types:
                        poly = hex_polys[h]
                        pts = list(poly) + [poly[0]]
                        for i in range(len(pts) - 1):
                            grid_draw2.line([pts[i], pts[i + 1]],
                                            fill=hex_grid_color,
                                            width=hex_grid_width)
            img = Image.alpha_composite(img, grid_img2)
            draw = ImageDraw.Draw(img)

            # Redraw wall outlines
            for ci, cluster in enumerate(_clusters):
                shape = _cluster_shapes[ci]
                door_hexes = [
                    h for h in cluster
                    if tile_lookup[h].structure_type == "house_door"
                ]

                if shape == "hex":
                    hp = _cluster_hex_polys[ci]
                    if not hp:
                        continue
                    # Draw all wall segments first
                    pts = list(hp) + [hp[0]]
                    for i in range(len(pts) - 1):
                        draw.line([pts[i], pts[i+1]], fill=wall_color, width=wall_lw)
                    for px, py in hp:
                        draw.ellipse([px-corner_post_r, py-corner_post_r,
                                      px+corner_post_r, py+corner_post_r],
                                     fill=corner_post_color)
                    # Erase door area after walls are drawn
                    if door_hexes:
                        dh = door_hexes[0]
                        dcx = centers[dh][0] + ox
                        dcy = centers[dh][1] + oy
                        best_pt = hp[0]
                        best_d2 = 1e18
                        for si in range(len(pts) - 1):
                            ax, ay = pts[si]
                            bx, by = pts[si + 1]
                            abx, aby = bx - ax, by - ay
                            ab_len2 = abx**2 + aby**2
                            if ab_len2 < 1:
                                t = 0.0
                            else:
                                t = max(0.0, min(1.0, ((dcx-ax)*abx + (dcy-ay)*aby) / ab_len2))
                            cpx, cpy = ax + t*abx, ay + t*aby
                            d2 = (cpx - dcx)**2 + (cpy - dcy)**2
                            if d2 < best_d2:
                                best_d2 = d2
                                best_pt = (cpx, cpy)
                        avg_x = sum(p[0] for p in hp) / len(hp)
                        avg_y = sum(p[1] for p in hp) / len(hp)
                        dx = best_pt[0] - avg_x
                        dy = best_pt[1] - avg_y
                        d_len = (dx**2 + dy**2)**0.5 or 1
                        perp_x, perp_y = -dy / d_len, dx / d_len
                        gap = self.hex_size * 1.2
                        gs = (int(best_pt[0] - perp_x*gap), int(best_pt[1] - perp_y*gap))
                        ge = (int(best_pt[0] + perp_x*gap), int(best_pt[1] + perp_y*gap))
                        draw.line([gs, ge], fill=bld_fill, width=wall_lw + 4)
                        draw.line([gs, ge], fill=(175, 145, 100),
                                  width=max(1, wall_lw // 3))
                        post_r = max(2, int(self.hex_size * 0.12))
                        draw.ellipse([gs[0]-post_r, gs[1]-post_r,
                                      gs[0]+post_r, gs[1]+post_r],
                                     fill=corner_post_color)
                        draw.ellipse([ge[0]-post_r, ge[1]-post_r,
                                      ge[0]+post_r, ge[1]+post_r],
                                     fill=corner_post_color)
                else:
                    rect = _cluster_rects[ci]
                    if not rect:
                        continue
                    bx0, by0, bx1, by1 = rect
                    rect_corners = [
                        (bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1),
                    ]
                    sides = [
                        (rect_corners[0], rect_corners[1]),
                        (rect_corners[1], rect_corners[2]),
                        (rect_corners[2], rect_corners[3]),
                        (rect_corners[3], rect_corners[0]),
                    ]
                    door_side = -1
                    if door_hexes:
                        dh = door_hexes[0]
                        dcx = centers[dh][0] + ox
                        dcy = centers[dh][1] + oy
                        dists = [
                            abs(dcy - by0), abs(dcx - bx1),
                            abs(dcy - by1), abs(dcx - bx0),
                        ]
                        door_side = dists.index(min(dists))
                    for si, (p1, p2) in enumerate(sides):
                        if si == door_side:
                            mx = (p1[0] + p2[0]) / 2
                            my = (p1[1] + p2[1]) / 2
                            seg_dx = p2[0] - p1[0]
                            seg_dy = p2[1] - p1[1]
                            seg_len = (seg_dx**2 + seg_dy**2) ** 0.5 or 1
                            ux, uy = seg_dx / seg_len, seg_dy / seg_len
                            gap = self.hex_size * 1.0
                            gs = (int(mx - ux * gap), int(my - uy * gap))
                            ge = (int(mx + ux * gap), int(my + uy * gap))
                            draw.line([p1, gs], fill=wall_color, width=wall_lw)
                            draw.line([ge, p2], fill=wall_color, width=wall_lw)
                            draw.line([gs, ge], fill=(175, 145, 100),
                                      width=max(1, wall_lw // 3))
                            post_r = max(2, int(self.hex_size * 0.06))
                            draw.ellipse([gs[0]-post_r, gs[1]-post_r,
                                          gs[0]+post_r, gs[1]+post_r],
                                         fill=corner_post_color)
                            draw.ellipse([ge[0]-post_r, ge[1]-post_r,
                                          ge[0]+post_r, ge[1]+post_r],
                                         fill=corner_post_color)
                        else:
                            draw.line([p1, p2], fill=wall_color, width=wall_lw)
                    for cx_p, cy_p in rect_corners:
                        draw.ellipse([cx_p-corner_post_r, cy_p-corner_post_r,
                                      cx_p+corner_post_r, cy_p+corner_post_r],
                                     fill=corner_post_color)

        # --- Layer 3c: Furniture drawn at building level ---
        # Group furniture hexes per building cluster, compute centroids,
        # then push wall-adjacent furniture toward the nearest wall edge.
        hs = self.hex_size
        for ci, cluster in enumerate(_clusters):
            # Get building bounding rect/hex poly for wall-push
            brect = _cluster_rects[ci]
            bpoly = _cluster_hex_polys[ci]
            # Compute building centroid from all hex centres
            all_px = [centers[h][0] + ox for h in cluster]
            all_py = [centers[h][1] + oy for h in cluster]
            bld_cx = sum(all_px) / len(all_px)
            bld_cy = sum(all_py) / len(all_py)

            # Collect furniture hexes grouped by type
            furn_groups: dict[str, list[tuple[float, float]]] = {}
            for h in cluster:
                t = tile_lookup.get(h)
                if t and t.structure_type in _furniture_types:
                    px = centers[h][0] + ox
                    py = centers[h][1] + oy
                    furn_groups.setdefault(t.structure_type, []).append((px, py))

            # Wall-adjacent types should be pushed toward nearest wall
            _wall_types = {"bed", "bookshelf", "desk", "chest"}

            for ftype, positions in furn_groups.items():
                # Centroid of the furniture hex group
                gcx = sum(p[0] for p in positions) / len(positions)
                gcy = sum(p[1] for p in positions) / len(positions)
                n = len(positions)
                spread = hs * (0.8 + 0.4 * n)

                # Push wall-adjacent furniture toward the nearest wall edge
                if ftype in _wall_types and brect:
                    bx0, by0, bx1, by1 = brect
                    inset = hs * 0.3
                    edges = [
                        ("top", gcy - by0, gcx, by0 + inset),
                        ("bot", by1 - gcy, gcx, by1 - inset),
                        ("left", gcx - bx0, bx0 + inset, gcy),
                        ("right", bx1 - gcx, bx1 - inset, gcy),
                    ]
                    edges.sort(key=lambda e: e[1])
                    _, _, push_x, push_y = edges[0]
                    # Blend: 70% toward wall, 30% keep original
                    gcx = gcx * 0.3 + push_x * 0.7
                    gcy = gcy * 0.3 + push_y * 0.7

                if ftype == "bed":
                    h_seed = (int(gcx) * 73856093 + int(gcy) * 19349669) & 0xFFFFFFFF
                    blanket_colors = [
                        (95, 55, 45), (55, 70, 95), (75, 85, 55),
                        (90, 50, 60), (60, 60, 85),
                    ]
                    bc = blanket_colors[h_seed % len(blanket_colors)]
                    rw, rh = spread * 0.48, spread * 0.35
                    draw.rectangle([gcx - rw - 1, gcy - rh - 1,
                                    gcx + rw + 1, gcy + rh + 1],
                                   fill=(100, 78, 45))
                    draw.rectangle([gcx - rw, gcy - rh,
                                    gcx + rw * 0.6, gcy + rh],
                                   fill=bc)
                    draw.rectangle([gcx + rw * 0.65, gcy - rh * 0.7,
                                    gcx + rw, gcy + rh * 0.7],
                                   fill=(205, 198, 185))

                elif ftype == "table":
                    r = spread * 0.32
                    draw.ellipse([gcx - r, gcy - r, gcx + r, gcy + r],
                                 fill=(100, 75, 40),
                                 outline=(75, 55, 28),
                                 width=max(1, int(hs * 0.08)))

                elif ftype == "chair":
                    # Cluster chairs around the table if one exists
                    table_pos = furn_groups.get("table")
                    if table_pos:
                        tcx = sum(p[0] for p in table_pos) / len(table_pos)
                        tcy = sum(p[1] for p in table_pos) / len(table_pos)
                    else:
                        tcx, tcy = gcx, gcy
                    # Place chairs in a ring around the table centre
                    import math as _math
                    nc = len(positions)
                    chair_r = hs * 1.2
                    for i in range(nc):
                        angle = (2 * _math.pi * i / nc) + 0.4
                        cx = tcx + chair_r * _math.cos(angle)
                        cy = tcy + chair_r * _math.sin(angle)
                        r = hs * 0.32
                        draw.rectangle([cx - r, cy - r, cx + r, cy + r],
                                       fill=(135, 108, 65),
                                       outline=(105, 80, 48),
                                       width=max(1, int(hs * 0.06)))

                elif ftype == "rug":
                    h_seed = (int(gcx) * 73856093 + int(gcy) * 19349669) & 0xFFFFFFFF
                    rug_colors = [
                        (145, 55, 42), (50, 72, 115), (100, 65, 110),
                        (120, 90, 40), (55, 95, 60),
                    ]
                    rc = rug_colors[h_seed % len(rug_colors)]
                    rw, rh = spread * 0.45, spread * 0.35
                    draw.ellipse([gcx - rw, gcy - rh, gcx + rw, gcy + rh],
                                 fill=rc,
                                 outline=tuple(max(0, c - 35) for c in rc),
                                 width=max(1, int(hs * 0.08)))

                elif ftype == "bookshelf":
                    rw, rh = spread * 0.40, spread * 0.22
                    draw.rectangle([gcx - rw, gcy - rh, gcx + rw, gcy + rh],
                                   fill=(60, 45, 28))
                    draw.rectangle([gcx - rw * 0.2, gcy - rh * 0.6,
                                    gcx + rw * 0.2, gcy + rh * 0.6],
                                   fill=(130, 42, 32))

                elif ftype == "desk":
                    rw, rh = spread * 0.42, spread * 0.30
                    draw.rectangle([gcx - rw, gcy - rh, gcx + rw, gcy + rh],
                                   fill=(88, 65, 35))
                    pr = spread * 0.12
                    draw.rectangle([gcx - pr, gcy - pr * 0.8,
                                    gcx + pr, gcy + pr * 0.8],
                                   fill=(210, 205, 190))

                elif ftype == "chest":
                    rw, rh = spread * 0.30, spread * 0.24
                    draw.rectangle([gcx - rw, gcy - rh, gcx + rw, gcy + rh],
                                   fill=(105, 78, 40),
                                   outline=(78, 55, 28),
                                   width=max(1, int(hs * 0.06)))
                    cr = max(1, int(hs * 0.10))
                    draw.ellipse([gcx - cr, gcy - cr, gcx + cr, gcy + cr],
                                 fill=(170, 165, 148))

        # --- Layer 4: Lighting overlay ---
        if self.draw_lighting:
            light_polys: dict[Lighting, list] = {}
            for tile in tiles:
                if not tile.discovered or tile.lighting == Lighting.BRIGHT:
                    continue
                # Skip building hexes — we'll draw rectangular overlays below
                if tile.structure_type in building_types:
                    continue
                light_polys.setdefault(tile.lighting, []).append(hex_polys[tile.hex])
            if light_polys:
                light_img = Image.new("RGBA", img.size, (0, 0, 0, 0))
                light_draw = ImageDraw.Draw(light_img)
                for level, polys in light_polys.items():
                    overlay_color = LIGHTING_OVERLAYS.get(level)
                    if overlay_color:
                        for poly in polys:
                            light_draw.polygon(poly, fill=overlay_color)
                img = Image.alpha_composite(img, light_img)
                draw = ImageDraw.Draw(img)

            # Building lighting: draw shape-matched DIM overlay per building.
            for ci, cluster in enumerate(_clusters):
                interior_lighting = set()
                for bh in cluster:
                    bt = tile_lookup.get(bh)
                    if bt and bt.structure_type == "house":
                        interior_lighting.add(bt.lighting)
                for level in interior_lighting:
                    if level == Lighting.BRIGHT:
                        continue
                    overlay_color = LIGHTING_OVERLAYS.get(level)
                    if overlay_color:
                        bld_light = Image.new("RGBA", img.size, (0, 0, 0, 0))
                        bld_light_draw = ImageDraw.Draw(bld_light)
                        if _cluster_rects[ci]:
                            bx0, by0, bx1, by1 = _cluster_rects[ci]
                            bld_light_draw.rectangle(
                                [bx0, by0, bx1, by1], fill=overlay_color,
                            )
                        elif _cluster_hex_polys[ci]:
                            bld_light_draw.polygon(
                                _cluster_hex_polys[ci], fill=overlay_color,
                            )
                        img = Image.alpha_composite(img, bld_light)
                        draw = ImageDraw.Draw(img)

        # --- Layer 5: Cover indicators ---
        if self.draw_cover:
            for tile in tiles:
                if not tile.discovered or tile.cover == Cover.NONE:
                    continue
                if tile.structure_type in building_types:
                    continue  # walls already convey cover; skip noisy dots
                cx = centers[tile.hex][0] + ox
                cy = centers[tile.hex][1] + oy
                _draw_cover_indicator(draw, cx, cy, self.hex_size, tile.cover)

        # --- Layer 6: Fog of war (single composite) ---
        if fog_polys:
            fog_img = Image.new("RGBA", img.size, (0, 0, 0, 0))
            fog_draw = ImageDraw.Draw(fog_img)
            for poly in fog_polys:
                fog_draw.polygon(poly, fill=FOG_COLOR)
            img = Image.alpha_composite(img, fog_img)
            draw = ImageDraw.Draw(img)

        # --- Layer 6b: Noise/grain texture for painterly look ---
        rng = np.random.RandomState(42)  # deterministic grain
        noise = rng.randint(-18, 19, size=(img_h, img_w), dtype=np.int16)
        grain_rgba = np.zeros((img_h, img_w, 4), dtype=np.uint8)
        pos_mask = noise > 0
        neg_mask = ~pos_mask
        # Bright warm grain
        grain_rgba[pos_mask, 0] = np.clip(128 + noise[pos_mask], 0, 255).astype(np.uint8)
        grain_rgba[pos_mask, 1] = np.clip(120 + noise[pos_mask], 0, 255).astype(np.uint8)
        grain_rgba[pos_mask, 2] = np.clip(100 + noise[pos_mask], 0, 255).astype(np.uint8)
        grain_rgba[pos_mask, 3] = (np.abs(noise[pos_mask]) * 2).clip(0, 255).astype(np.uint8)
        # Dark grain
        grain_rgba[neg_mask, 3] = (np.abs(noise[neg_mask]) * 2).clip(0, 255).astype(np.uint8)
        grain_img = Image.fromarray(grain_rgba, "RGBA")
        img = Image.alpha_composite(img, grain_img)
        draw = ImageDraw.Draw(img)

        # --- Layer 7: Labels and coordinates ---
        for tile in tiles:
            if not tile.discovered:
                continue
            cx = centers[tile.hex][0] + ox
            cy = centers[tile.hex][1] + oy

            if tile.label:
                bbox = draw.textbbox((0, 0), tile.label, font=label_font)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                draw.text(
                    (cx - tw / 2, cy - th / 2),
                    tile.label, fill=LABEL_COLOR, font=label_font,
                )

            if self.show_coordinates:
                coord_text = f"{tile.hex.q},{tile.hex.r}"
                bbox = draw.textbbox((0, 0), coord_text, font=coord_font)
                tw = bbox[2] - bbox[0]
                draw.text(
                    (cx - tw / 2, cy + self.hex_size * 0.35),
                    coord_text, fill=GRID_COLOR_LIGHT, font=coord_font,
                )

        # --- Layer 8: Tokens ---
        for token in tokens:
            if token.hex not in centers:
                continue
            cx = centers[token.hex][0] + ox
            cy = centers[token.hex][1] + oy
            r = self.hex_size * token.size / 2
            draw.ellipse(
                [cx - r, cy - r, cx + r, cy + r],
                fill=token.color, outline=(255, 255, 255), width=2,
            )
            if token.label:
                bbox = draw.textbbox((0, 0), token.label, font=coord_font)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                draw.text(
                    (cx - tw / 2, cy - th / 2),
                    token.label, fill=(255, 255, 255), font=coord_font,
                )

        return img
