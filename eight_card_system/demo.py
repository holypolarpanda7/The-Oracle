"""
Demo script for the Eight Card System hex renderer.

Generates two maps:
  1. Region map — 37 Encounter Areas colored by biome
  2. Encounter map — Space-level terrain for one Encounter Area

Saves output to eight_card_system/demo_output/
"""

import os
import sys

# Ensure package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _save_image(img, path: str, max_dim: int = 4096, quality: int = 92) -> str:
    """Save an image as JPEG, resized to *max_dim* on longest side."""
    from PIL import Image as _Img
    ratio = min(max_dim / img.width, max_dim / img.height)
    if ratio < 1:
        img = img.resize(
            (int(img.width * ratio), int(img.height * ratio)),
            _Img.LANCZOS,
        )
    jpg_path = os.path.splitext(path)[0] + ".jpg"
    img.convert("RGB").save(jpg_path, "JPEG", quality=quality, optimize=True)
    return jpg_path

from eight_card_system.hex_math import Hex
from eight_card_system.renderer import (
    BIOME_COLORS,
    TERRAIN_COLORS,
    HexRenderer,
    HexTile,
)
from eight_card_system.terrain_gen import (
    generate_region_terrain,
    generate_space_terrain,
    generate_hamlet,
    generate_city,
    extract_edge_profiles,
    EncounterManager,
    Cover,
    Lighting,
    apply_overlay,
    overlay_river,
    overlay_pond,
    overlay_bridge,
    overlay_shrine,
    overlay_grove,
    overlay_campsite,
    overlay_city_river,
    TerrainOverlay,
)

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_output")


def demo_region_map(world_seed: int = 42):
    """Render a Region map showing 37 Encounter Areas."""
    print("Generating Region terrain...")
    terrain = generate_region_terrain(region_q=0, region_r=0, world_seed=world_seed, radius=3)

    tiles = []
    for h, data in terrain.items():
        color = BIOME_COLORS.get(data.biome, (100, 100, 100))
        # Simulate some undiscovered areas (outer ring)
        discovered = h.distance(Hex(0, 0)) <= 2
        tiles.append(HexTile(
            hex=h,
            color=color,
            label=data.biome[:4].upper() if discovered else "",
            discovered=discovered,
            highlight=(h.q == 0 and h.r == 0),  # highlight center
            elevation=data.elevation,
            terrain_type=data.biome,
        ))

    renderer = HexRenderer(
        hex_size=48,
        show_coordinates=True,
        title="Region Map — Encounter Areas by Biome",        organic_borders=True,    )
    img = renderer.render(tiles)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = _save_image(img, os.path.join(OUTPUT_DIR, "region_map.png"))
    print(f"  Saved: {path}")
    print(f"  Tiles: {len(tiles)} ({sum(1 for t in tiles if t.discovered)} discovered)")

    # Print biome distribution
    biomes = {}
    for data in terrain.values():
        biomes[data.biome] = biomes.get(data.biome, 0) + 1
    print("  Biomes:", dict(sorted(biomes.items(), key=lambda x: -x[1])))

    return terrain


def demo_encounter_map(biome: str = "forest", terrain_seed: int = 12345):
    """Render an Encounter Area map showing individual Spaces."""
    print(f"\nGenerating Encounter Area terrain (biome={biome})...")

    # Use radius=20 for demo (smaller than full 54, but visually rich)
    space_terrain = generate_space_terrain(
        terrain_seed=terrain_seed,
        biome=biome,
        radius=60,
    )

    tiles = []
    for h, data in space_terrain.items():
        color = TERRAIN_COLORS.get(data.terrain_type, (100, 100, 100))
        tiles.append(HexTile(
            hex=h, color=color,
            elevation=data.elevation,
            terrain_type=data.terrain_type,
        ))

    renderer = HexRenderer(
        hex_size=8,
        show_coordinates=False,
        title=f"Encounter Map \u2014 {biome.title()} (5ft Spaces, 600ft across)",
        biome=biome,
    )
    img = renderer.render(tiles)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = _save_image(img, os.path.join(OUTPUT_DIR, "encounter_map.png"))
    print(f"  Saved: {path}")
    print(f"  Spaces: {len(tiles)}")

    # Terrain distribution
    counts = {}
    for data in space_terrain.values():
        counts[data.terrain_type] = counts.get(data.terrain_type, 0) + 1
    print("  Terrain:", dict(sorted(counts.items(), key=lambda x: -x[1])))


def demo_multi_biome_encounters(world_seed: int = 42):
    """Render encounter maps for several biomes side by side."""
    biomes_to_demo = ["desert", "swamp", "mountain", "plains"]
    print(f"\nGenerating {len(biomes_to_demo)} additional biome encounter maps...")

    for i, biome in enumerate(biomes_to_demo):
        space_terrain = generate_space_terrain(
            terrain_seed=world_seed * 100 + i,
            biome=biome,
            radius=15,
        )

        tiles = [
            HexTile(
                hex=h,
                color=TERRAIN_COLORS.get(data.terrain_type, (100, 100, 100)),
                elevation=data.elevation,
                terrain_type=data.terrain_type,
            )
            for h, data in space_terrain.items()
        ]

        renderer = HexRenderer(
            hex_size=24,
            title=f"Encounter — {biome.title()}",
            biome=biome,
        )
        img = renderer.render(tiles)

        path = _save_image(img, os.path.join(OUTPUT_DIR, f"encounter_{biome}.png"))
        print(f"  Saved: {path}")


def demo_hamlet(terrain_seed: int = 7777):
    """Render a small hamlet with buildings, farmland, and cover/lighting."""
    print("\nGenerating Hamlet (daytime)...")
    grid = generate_hamlet(terrain_seed, radius=60, num_houses=12)

    tiles = []
    for h, data in grid.items():
        color = TERRAIN_COLORS.get(data.terrain_type, (100, 100, 100))
        tiles.append(HexTile(
            hex=h,
            color=color,
            elevation=data.elevation,
            terrain_type=data.terrain_type,
            cover=data.cover,
            lighting=data.lighting,
            structure_type=data.structure_type,
            building_id=data.building_id,
            building_shape=data.building_shape,
        ))

    renderer = HexRenderer(
        hex_size=8,
        show_coordinates=False,
        title="Hamlet — Daytime (Cover & Lighting)",
        draw_cover=True,
        draw_lighting=True,
        organic_borders=True,
    )
    img = renderer.render(tiles)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = _save_image(img, os.path.join(OUTPUT_DIR, "hamlet_day.png"))
    print(f"  Saved: {path}")
    print(f"  Spaces: {len(tiles)}")

    # Structure distribution
    structs = {}
    for data in grid.values():
        if data.structure_type:
            structs[data.structure_type] = structs.get(data.structure_type, 0) + 1
    print("  Structures:", dict(sorted(structs.items(), key=lambda x: -x[1])))

    # Cover distribution
    covers = {}
    for data in grid.values():
        if data.cover != Cover.NONE:
            covers[data.cover.name] = covers.get(data.cover.name, 0) + 1
    print("  Cover:", dict(sorted(covers.items(), key=lambda x: -x[1])))

    # Lighting distribution
    lights = {}
    for data in grid.values():
        lights[data.lighting.name] = lights.get(data.lighting.name, 0) + 1
    print("  Lighting:", dict(sorted(lights.items(), key=lambda x: -x[1])))


def demo_city(city_seed: int = 8888, culture: str = "human"):
    """Render a city with a river overlay flowing through all areas."""

    print(f"\nGenerating City ({culture}) with river (5 connected encounter areas)...")

    # First pass: get area layout to plan the river
    city_hexes_preview, _ = generate_city(
        city_seed, num_areas=5, radius=60, biome="plains", culture=culture,
    )

    # Build a river that flows through all city areas
    city_overlays = overlay_city_river(
        city_hexes_preview, radius=60, width=3,
        curve_seed=7, flow_direction="SE",
    )

    # Generate with river overlays
    city_hexes, areas = generate_city(
        city_seed, num_areas=5, radius=60, biome="plains",
        overlays=city_overlays, culture=culture,
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Render and save each area as a separate image
    hex_size = 7
    for area_hex in city_hexes:
        tiles = []
        for h, data in areas[area_hex].items():
            color = TERRAIN_COLORS.get(data.terrain_type, (100, 100, 100))
            tiles.append(HexTile(
                hex=h, color=color,
                elevation=data.elevation,
                terrain_type=data.terrain_type,
                structure_type=data.structure_type,
                building_id=data.building_id,
                building_shape=data.building_shape,
            ))

        renderer = HexRenderer(
            hex_size=hex_size,
            title=f"City Area ({area_hex.q},{area_hex.r})",
            biome="plains",
            culture=culture,
        )
        img = renderer.render(tiles)
        path = _save_image(img, os.path.join(OUTPUT_DIR, f"city_{culture}_{area_hex.q}_{area_hex.r}.png"))
        print(f"  Saved: {path}")

    print(f"  Areas: {len(city_hexes)} at {[(h.q, h.r) for h in city_hexes]}")

    # Terrain distribution across the city
    all_counts: dict[str, int] = {}
    struct_counts: dict[str, int] = {}
    for area_terrain in areas.values():
        for st in area_terrain.values():
            all_counts[st.terrain_type] = all_counts.get(st.terrain_type, 0) + 1
            if st.structure_type:
                struct_counts[st.structure_type] = struct_counts.get(st.structure_type, 0) + 1
    print("  Terrain:", dict(sorted(all_counts.items(), key=lambda x: -x[1])))
    print("  Structures:", dict(sorted(struct_counts.items(), key=lambda x: -x[1])))

    # --- Composite: all areas on one canvas with edges properly aligned ---
    # Place each area's tiles in world-space hex coords so shared edges
    # physically overlap, proving continuity.
    from eight_card_system.terrain_gen import _area_to_world
    radius_used = 60
    all_tiles = []
    for area_hex in city_hexes:
        for h, data in areas[area_hex].items():
            # Shift local hex → world hex
            wh = _area_to_world(area_hex, h, radius_used)
            color = TERRAIN_COLORS.get(data.terrain_type, (100, 100, 100))
            all_tiles.append(HexTile(
                hex=wh, color=color,
                elevation=data.elevation,
                terrain_type=data.terrain_type,
                structure_type=data.structure_type,
                building_id=data.building_id,
                building_shape=data.building_shape,
            ))

    renderer = HexRenderer(
        hex_size=4,
        title=f"City Composite ({culture}) — Edge Alignment",
        biome="plains",
        culture=culture,
    )
    composite = renderer.render(all_tiles)
    path = _save_image(composite, os.path.join(OUTPUT_DIR, f"city_composite_{culture}.png"))
    print(f"  Composite: {path}")


def demo_traversal(world_seed: int = 42):
    """Show edge consistency: two encounter areas on ONE canvas.

    Renders area A and area B as a single combined map so the shared
    edge is directly visible.  Also renders the same pair *without*
    edge matching for comparison.
    """
    print("\nGenerating Edge Consistency comparison...")

    radius = 60
    seed_a = 5001
    seed_b = 5002
    # Visual offset: use 2*R so the outermost edge columns overlap in
    # pixel space.  The r-offset cancels the y-stagger inherent in
    # flat-top hex grids, keeping the two areas at the same height.
    q_off = 2 * radius
    r_off = -radius

    region = generate_region_terrain(0, 0, world_seed, radius=3)

    # Area A at (0,0), Area B at (1,0) — B is east of A
    hex_a = Hex(0, 0)
    hex_b = Hex(1, 0)
    biome_a = region[hex_a].biome if hex_a in region else "forest"
    biome_b = region[hex_b].biome if hex_b in region else "plains"

    # --- WITH edge matching ---
    mgr = EncounterManager(region, radius=radius)
    terrain_a = mgr.generate(hex_a, seed_a)
    terrain_b = mgr.generate(hex_b, seed_b)

    # --- WITHOUT edge matching (for comparison) ---
    terrain_b_raw = generate_space_terrain(seed_b, biome_b, radius)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Build combined tile lists: offset B's hex coordinates by +stride on q
    # so both areas render on one canvas with their E/W edges touching
    def _combined_tiles(t_a, t_b, label_a, label_b):
        tiles = []
        for h, data in t_a.items():
            tiles.append(HexTile(
                hex=h,
                color=TERRAIN_COLORS.get(data.terrain_type, (100, 100, 100)),
                elevation=data.elevation,
                terrain_type=data.terrain_type,
            ))
        for h, data in t_b.items():
            # Shift B's hex coords so its west edge overlaps A's east edge
            shifted = Hex(h.q + q_off, h.r + r_off)
            tiles.append(HexTile(
                hex=shifted,
                color=TERRAIN_COLORS.get(data.terrain_type, (100, 100, 100)),
                elevation=data.elevation,
                terrain_type=data.terrain_type,
            ))
        return tiles

    # Render WITH matching
    tiles_matched = _combined_tiles(terrain_a, terrain_b, biome_a, biome_b)
    renderer = HexRenderer(
        hex_size=6,
        title=f"Edge Matched: {biome_a.title()} (left) \u2192 {biome_b.title()} (right)",
        biome=biome_a,
    )
    img_matched = renderer.render(tiles_matched)
    path = _save_image(img_matched, os.path.join(OUTPUT_DIR, "edge_matched.png"))
    print(f"  Saved: {path}")

    # Render WITHOUT matching
    tiles_raw = _combined_tiles(terrain_a, terrain_b_raw, biome_a, biome_b)
    renderer2 = HexRenderer(
        hex_size=6,
        title=f"No Matching: {biome_a.title()} (left) → {biome_b.title()} (right)",
        biome=biome_a,
    )
    img_raw = renderer2.render(tiles_raw)
    path = _save_image(img_raw, os.path.join(OUTPUT_DIR, "edge_unmatched.png"))
    print(f"  Saved: {path}")

    # Print stats
    prof_a = extract_edge_profiles(terrain_a, radius).get("E")
    prof_b_m = extract_edge_profiles(terrain_b, radius).get("W")
    prof_b_r = extract_edge_profiles(terrain_b_raw, radius).get("W")
    if prof_a:
        print(f"  A east edge:       {_fmt_dist(prof_a.terrain_distribution)}"
              f"  elev={prof_a.avg_elevation:.2f}")
    if prof_b_m:
        print(f"  B west (matched):  {_fmt_dist(prof_b_m.terrain_distribution)}"
              f"  elev={prof_b_m.avg_elevation:.2f}")
    if prof_b_r:
        print(f"  B west (raw):      {_fmt_dist(prof_b_r.terrain_distribution)}"
              f"  elev={prof_b_r.avg_elevation:.2f}")


def _fmt_dist(d: dict[str, float]) -> str:
    """Format terrain distribution dict for display."""
    return " ".join(f"{k}={v:.0%}" for k, v in sorted(d.items(), key=lambda x: -x[1]))


def demo_overlays():
    """Show the overlay / narrative insert system.

    Forest encounter with a pond and a shrine inserted.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("\nGenerating Forest with pond + shrine overlays...")

    pond = overlay_pond(radius=5)
    shrine = overlay_shrine()

    terrain = generate_space_terrain(
        terrain_seed=55555, biome="forest", radius=60,
        overlays=[
            (pond, Hex(-10, 15), 0),
            (shrine, Hex(20, -10), 3),
        ],
    )

    tiles = []
    for h, data in terrain.items():
        color = TERRAIN_COLORS.get(data.terrain_type, (100, 100, 100))
        tiles.append(HexTile(
            hex=h, color=color,
            elevation=data.elevation,
            terrain_type=data.terrain_type,
            structure_type=data.structure_type,
        ))

    renderer = HexRenderer(hex_size=7, title="Forest + Pond & Shrine", biome="forest")
    img = renderer.render(tiles)
    path = _save_image(img, os.path.join(OUTPUT_DIR, "forest_overlays.png"))
    print(f"  Saved: {path}")


if __name__ == "__main__":
    print("=" * 60)
    print("Eight Card System — Hex Renderer Demo")
    print("=" * 60)

    terrain_data = demo_region_map(world_seed=42)
    demo_encounter_map(biome="forest", terrain_seed=12345)
    demo_multi_biome_encounters(world_seed=42)
    demo_hamlet()
    demo_city()
    demo_overlays()
    demo_traversal()


    print(f"\nAll maps saved to: {OUTPUT_DIR}")
    print("Done!")
