# Board set pieces

The meshes in this directory are landmarks on the tactical board — a pyramid,
a colossus, a wrecked hull. They are **third-party models**, and every one is
under a licence that permits redistribution, which is the operative question
because this repository is public: a committed mesh is a mesh we hand on.
"Free for personal use", which is what most of a search for a free 3D model
returns, is not such a licence and nothing under it is here.

This file is **generated** from `vtt/setpieces.py`. Regenerate it with:

```bash
uv run python scripts/setpiece_assets.py --attribution
```

## Packs

| Pack | Author | Models | Licence | Source |
|------|--------|--------|---------|--------|
| Fantasy Town Kit | Kenney | 160 | CC0-1.0 | <https://kenney.nl/assets/fantasy-town-kit> |
| Graveyard Kit | Kenney | 90 | CC0-1.0 | <https://kenney.nl/assets/graveyard-kit> |
| Nature Kit | Kenney | 330 | CC0-1.0 | <https://kenney.nl/assets/nature-kit> |
| Pirate Kit | Kenney | 70 | CC0-1.0 | <https://kenney.nl/assets/pirate-kit> |
| Ultimate Modular Ruins Pack | Quaternius | 90 | CC0-1.0 | <https://quaternius.com/packs/ultimatemodularruins.html> |

CC0 1.0 waives attribution entirely — <https://creativecommons.org/publicdomain/zero/1.0/>.
The register is kept regardless, so that a binary in the tree can always be
traced to where it came from and the terms it came under.

## What is used

| Set piece | Squares | Height | Pack |
|-----------|---------|--------|------|
| `village-fountain` | 5x5 | 6 ft | Fantasy Town Kit |
| `mausoleum` | 3x5 | 14 ft | Graveyard Kit |
| `boulder-heap` | 3x3 | 14 ft | Nature Kit |
| `cave-pillar` | 1x1 | 16 ft | Nature Kit |
| `forest-giant` | 9x9 | 45 ft | Nature Kit |
| `jungle-giant` | 9x9 | 60 ft | Nature Kit |
| `standing-stone` | 1x1 | 11 ft | Nature Kit |
| `gatehouse-tower` | 3x3 | 40 ft | Pirate Kit |
| `shipwreck` | 2x5 | 20 ft | Pirate Kit |
| `broken-pillar` | 1x1 | 12 ft | Ultimate Modular Ruins Pack |
| `great-statue` | 3x2 | 22 ft | Ultimate Modular Ruins Pack |
| `ruined-arch` | 3x1 | 18 ft | Ultimate Modular Ruins Pack |
| `ruined-wall` | 3x1 | 12 ft | Ultimate Modular Ruins Pack |

## What a mesh is, and is not

A set piece contributes **volume and silhouette only**. It contributes no
mechanics: cover, movement, sight and breakability are read off the tile codes
the piece stamps onto the grid, exactly as they are everywhere else on the
board. Once the painted layer is present the geometry stops drawing colour
altogether and becomes a depth occluder for a diffusion render — which is also
why a mesh whose art style does not match the game costs very little here.
