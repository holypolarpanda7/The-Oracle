"""
The Oracle's virtual tabletop — a square-grid tactical layer that opens only
when a moment needs it.

The Oracle runs theater-of-the-mind by default. When a scene turns *tactical* —
a fight, a timed puzzle, a trap-laced room, the terrain leg of a chase — the DM
drops a board: a procedurally-generated 5-ft grid, a diffusion-rendered
top-down battlemap over it, tokens for everyone in the fight, and overlays for
every spell area, aura and hazard on the field. When the moment passes, the
board closes and play returns to prose.

    from vtt import VttEngine
    v = VttEngine()
    v.create_tables()
    scene = v.open_scene("guild:chan", kind="combat", archetype="cave",
                         name="The Sunken Shrine")
    v.place_token(scene.id, name="Kara", team="party", kind="pc", speed_ft=30)
    v.state(scene.id)          # everything the Activity overlay draws
    print(v.render(scene.id))  # the compact board the DM prompt reads

Layers:
  ``models``    the four tables (map / token / effect / event)
  ``terrain``   tile taxonomy + the Grid container
  ``geometry``  distance, pathing, line of sight, cover, spell templates
  ``mapgen``    deterministic seeded layout generators per archetype
  ``art``       the diffusion battlemap render (ComfyUI via ``imagery``)
  ``scene``     ``VttEngine`` — the service the backend actually calls
  ``bridge``    keeps the board and ``combat``'s initiative tracker in step
  ``triggers``  the policy for when a board is worth opening at all
"""
from .models import (
    TacticalMap,
    MapToken,
    MapEffect,
    MapEvent,
    SceneKind,
    TokenKind,
    Team,
    EffectKind,
    Shape,
    size_squares,
)
from .terrain import Grid, TILES, Tile, tile
from .scene import VttEngine
from .mapgen import generate_map, ARCHETYPES, archetype_for, archetype_for_place
from .triggers import should_open_scene, scene_kind_for

__all__ = [
    "TacticalMap", "MapToken", "MapEffect", "MapEvent",
    "SceneKind", "TokenKind", "Team", "EffectKind", "Shape", "size_squares",
    "Grid", "TILES", "Tile", "tile",
    "VttEngine",
    "generate_map", "ARCHETYPES", "archetype_for", "archetype_for_place",
    "should_open_scene", "scene_kind_for",
]
