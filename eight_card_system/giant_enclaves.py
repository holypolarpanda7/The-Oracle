"""
Opt-in seeder: giant enclaves as arcane sites in the world graph.

This is TOOLING ONLY — it carries no book content. It reads a LOCAL, gitignored
data file (``owned_books/giant_enclaves.json``) describing far-flung giant
enclaves and materializes each as a PLACE entity whose ``arcane_sites`` follow
the backend's ``_ARCANE_SITE_KINDS`` schema (rest-modifying auras, hazards,
restorative/curse-lifting sites). The book-derived DATA lives only in the
gitignored file; if it's absent (e.g. a fresh public checkout), this degrades to
a no-op.

These enclaves are DELIBERATELY not part of the default starter world — they are
remote, high-tier locations. Call ``seed_giant_enclaves`` explicitly when a
campaign turns giant-themed. Idempotent: everything upserts by slug.

    from eight_card_system.graph import WorldGraph
    from eight_card_system.giant_enclaves import seed_giant_enclaves
    seed_giant_enclaves(WorldGraph())
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from . import geo
from .graph import WorldGraph
from .models import EntityType, PlaceScale, RelationType

_DATA_FILE = Path(__file__).resolve().parent.parent / "owned_books" / "giant_enclaves.json"

# Spread enclaves a little around the region anchor so they aren't stacked on one
# point — deterministic offsets keyed by their order in the file.
_SPREAD = [("north", 0.0), ("east", 30.0), ("west", 30.0), ("north", 60.0),
           ("east", 60.0), ("west", 60.0)]


def seed_giant_enclaves(graph: WorldGraph,
                        data_file: Optional[Path] = None) -> dict:
    """Materialize the local giant-enclave data into the world graph.

    Returns a summary: region slug + the enclave slugs created, or ``{"skipped":
    reason}`` when the local data file is missing/unreadable.
    """
    path = Path(data_file) if data_file else _DATA_FILE
    if not path.is_file():
        return {"skipped": f"no local data file: {path.name}"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # malformed local file — never fatal to world setup
        return {"skipped": f"unreadable {path.name}: {e}"}

    graph.create_tables()
    reg = data.get("region") or {}
    enclaves = data.get("enclaves") or []

    # --- Region anchor (a remote frontier) ---
    reg_coords = None
    if reg.get("direction") and reg.get("miles") is not None:
        reg_coords = geo.from_origin(reg["direction"], float(reg["miles"]))
    region = graph.upsert_entity(
        reg.get("name", "The Giant Reaches"), EntityType.PLACE,
        subtype=PlaceScale.REGION,
        attributes={
            "description": reg.get("description", ""),
            "scale": "region",
            **({"coords": geo.coords_attr(*reg_coords)} if reg_coords else {}),
            "danger": reg.get("danger", "deadly"),
        },
        tags=reg.get("tags") or ["region", "frontier", "giant", "remote"],
    )

    created = []
    for i, enc in enumerate(enclaves):
        name = enc.get("name")
        if not name or not enc.get("arcane_sites"):
            continue
        coords = None
        if reg_coords:
            direction, miles = _SPREAD[i % len(_SPREAD)]
            coords = geo.offset_coords(reg_coords, direction, miles)
        attrs = {
            "description": enc.get("description", ""),
            "scale": enc.get("scale", "wilds"),
            "danger": enc.get("danger", "deadly"),
            "arcane_sites": enc["arcane_sites"],
            **({"coords": geo.coords_attr(*coords)} if coords else {}),
        }
        # A guardian creature is stored as a DM hint, not a graph node (there is
        # no MONSTER entity type); the rules DB holds the stat block by slug.
        if enc.get("guardian_monster"):
            attrs["guardian_monster"] = enc["guardian_monster"]
        place = graph.upsert_entity(
            name, EntityType.PLACE, subtype=enc.get("subtype", PlaceScale.WILDS),
            attributes=attrs,
            tags=enc.get("tags") or ["arcane-site", "enclave", "giant"],
        )
        graph.add_relation(place, RelationType.PART_OF, region)
        created.append(place.slug)

    return {"region": region.slug, "enclaves": created, "count": len(created)}
