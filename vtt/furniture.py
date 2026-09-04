"""A MODEL for the things standing on a square, where a model is worth having.

The board draws a crate, a table, an altar and a pillar out of prismatoids —
chamfered, battered, turned — and that is as far as a shape table goes. It goes
a long way: a crate with its lid proud of the body it closes reads as a packing
case, and it costs nothing but a few numbers. What it cannot do is put a
handle on a barrel, a moulding on an altar or a grain in a plank, because none
of those is a shape a rule can describe.

So a tile KIND may have a mesh, on exactly the economics the sprites already
use: **one crate model serves every crate on every board in every session**.
That is the whole reason this is affordable where a per-square model would not
be — there are nine kinds, not nine hundred squares.

Three rules, and the first is the one the project already wrote down for set
pieces, arriving somewhere it was previously said not to go:

* **The TILE keeps every rule.** Cover, height, movement, sight and
  breakability all read the code, exactly as before. A mesh is drawing.
* **The mesh is scaled to the height the board would have DRAWN**, so it cannot
  restate a height the rules quote. A crate screens four feet whether it is
  four prismatoids or a model, and a player deciding whether they can break
  line of sight behind it reads the same answer either way. This is why
  furniture-sized meshes were ruled out before and are allowed now: a SET PIECE
  stamps its own codes and one mesh at one scale cannot honour a per-square
  quoted height, and here the code is already there and the scale is derived
  FROM it.
* **A missing mesh is not an error.** Nothing here is required: a kind with no
  file falls back to the prismatoids, which is what every board drew before and
  what an installation that has never rendered one keeps drawing.

The fit is measured on the SERVER and shipped, the ``setpieces.mesh_fit``
rule — it is a measurement of a FILE, and the only way two languages cannot
disagree about one is for one of them to do it.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

#: Committed, and derived art rather than data — the species-portrait line. A
#: model of a crate carries no book text, no stat block and no mechanics.
MESH_ROOT = "activity-ui/public/assets/furniture"

#: Where a machine that has RENDERED one keeps it before it is committed.
GENERATED_ROOT = "generated/furniture"

#: The kinds worth a model, and what to ask for when making one.
#:
#: Deliberately short. A wall, a floor and a cliff are MASSES — they tile, they
#: join their neighbours, and a model of one is the wrong unit (see
#: `vtt.hull.roofs`). What is here is the discrete standing things: a shape
#: with a silhouette of its own that a hash will never produce.
#: Each phrase describes ONE upright thing of roughly square footprint, and
#: that is a constraint rather than a style: the model is scaled to the height
#: the rules quote, so a subject that comes back wider than it is tall spills
#: off its own square at that height. "A stack of two crates" measured 1.00 x
#: 0.45 x 0.58 and would have stood nine feet across a five-foot square.
SUBJECTS: dict[str, str] = {
    # "AS TALL AS IT IS WIDE" LOST TO THE FRAMING. `ImageKind.MESHREF` asks
    # for "a product photograph of a museum piece", which is a strong pull
    # toward things as they are DISPLAYED, and what came back was a wide low
    # footlocker lying on its long side — 1.00 x 0.45 x 0.57, refused at a
    # spread of 1.78. The mesher was faithful; the picture was wrong. A
    # proportion has to be named as a SHAPE, not as a comparison.
    "o": "a cube-shaped wooden packing box, tall as a stool, its height "
         "equal to its width and its depth, iron-banded corners, planks",
    # "a small SQUARE oak table" came back a long refectory trestle, wider
    # than deep, refused at 1.25. Same fix: say the proportion twice and say
    # it as a measurement.
    "n": "a small oak table with a perfectly square top, the top exactly as "
         "deep as it is wide, four turned legs, planked",
    "A": "a carved stone altar on a stepped plinth, plain mensa slab, "
         "as tall as it is wide",
    "w": "a short section of low drystone wall, one course of coping",
    "O": "a single carved stone column, moulded base and capital, "
         "tall and slender",
    "T": "a broadleaf tree with a heavy round crown and a short bole",
}

#: How far a model may reach across its square before it is the wrong model.
#:
#: A little over one, because the shape tables already draw a tree's crown
#: proud of its square and a landmark is allowed to overhang. Much past that
#: and the picture is contradicting the grid in the direction nobody checks —
#: the `KEEP` rule, one scale down. Refusing is the right answer rather than
#: squashing: scaling to fit the square would draw a crate that screens four
#: feet at two, and a player deciding whether they can break line of sight
#: behind it would read the wrong number off the board.
MAX_SPREAD = 1.15


def root() -> Path:
    return Path(__file__).resolve().parents[1] / MESH_ROOT


def generated_root() -> Path:
    return Path(__file__).resolve().parents[1] / GENERATED_ROOT


def slug_for(code: str) -> str:
    """The file name for one kind. The tile's own name, so it is readable."""
    from .terrain import tile
    return (tile(code).name or code).replace(" ", "-").lower()


def mesh_path(code: str) -> Optional[Path]:
    """This kind's model, or None if this machine has not got one.

    Committed first, then generated — the ``setpieces.mesh_path`` order and the
    same reasoning: a modeller's answer to what a crate looks like outranks a
    diffusion model's guess at one.

        EVERY FORMAT THE MEASURER CAN READ, and `.obj` is no longer the only one
    this pipeline produces. `imagery.landmark3d` emits GLB now — it has to, or
    the texture is thrown away — and this searched for `{slug}.obj` alone while
    `furniture_meshes.py` wrote the generator's bytes under that name whatever
    they were. The result was a file called `crate.obj` whose first four bytes
    are `glTF`: `mesh_path` found it, `fit` accepted the suffix, `_obj_bounds`
    could not parse a word of it and returned None, and the board fell back to
    the prismatoids without a sound. The client would have failed too — it
    picks its loader off the same extension.
    """
    if code not in SUBJECTS:
        return None
    slug = slug_for(code)
    from .setpieces import MEASURABLE
    for base in (root(), generated_root()):
        for ext in MEASURABLE:
            p = base / f"{slug}{ext}"
            if p.exists():
                return p
    return None


@lru_cache(maxsize=32)
def fit(code: str) -> Optional[dict]:
    """How to put this kind's model on a square, per UNIT OF DRAWN HEIGHT.

    Returns ``{"mesh", "unit_scale", "pivot"}``. Unlike a landmark's fit this
    carries no height of its own, and that is the point: the caller multiplies
    by whatever the board would have DRAWN on that square, so a quoted height
    stays exactly quoted and a jittered one still jitters. A renderer applies

        v -> (v - pivot) * unit_scale * drawn_height, then + the square's centre

    and needs to know nothing else.
    """
    path = mesh_path(code)
    from .setpieces import MEASURABLE, _mesh_bounds
    if path is None or path.suffix.lower() not in MEASURABLE:
        return None
    bounds = _mesh_bounds(path)
    if bounds is None:
        return None
    (x0, y0, z0), (x1, y1, z1) = bounds
    tall = y1 - y0
    if tall <= 0:
        return None
    if spread(code, bounds) > MAX_SPREAD:
        return None
    return {
        "mesh": f"/assets/furniture/{path.name}"
        if path.parent == root() else f"/vtt/furniture/{path.name}",
        "unit_scale": 1.0 / tall,
        "pivot": [(x0 + x1) / 2.0, y0, (z0 + z1) / 2.0],
    }


def quoted_height_ft(code: str) -> float:
    """What the board draws this kind at, in feet — the number the model is
    scaled to. The rules' own where they quote one, the tile's otherwise."""
    from .terrain import cover_height_ft, tile_height_ft
    return float(cover_height_ft(code) or tile_height_ft(code) or 0)


def spread(code: str, bounds=None) -> float:
    """How far this kind's model reaches across its square, at its height.

    The number the guard is on, and the one an audit prints: a model is scaled
    by its HEIGHT, so a subject that came back wider than it is tall stands
    proportionally wider than its square. Returns 0 when there is no model.
    """
    # `_mesh_bounds`, not `_obj_bounds`: this is the number the AUDIT prints,
    # and reading it with the OBJ parser alone made every GLB report 0.00 —
    # which the audit then rendered as a blank column beside a refusal.
    from .setpieces import _mesh_bounds
    if bounds is None:
        path = mesh_path(code)
        if path is None:
            return 0.0
        bounds = _mesh_bounds(path)
        if bounds is None:
            return 0.0
    (x0, y0, z0), (x1, y1, z1) = bounds
    tall = y1 - y0
    if tall <= 0:
        return 0.0
    ft = quoted_height_ft(code)
    if ft <= 0:
        return 0.0
    across = max(x1 - x0, z1 - z0) / tall * ft
    return across / 5.0                     # squares


def forget() -> None:
    """Drop the cached measurements — a mesh has arrived or changed."""
    fit.cache_clear()


def have() -> dict[str, str]:
    """Every kind that has a model on this machine, ``{code: path}``."""
    out: dict[str, str] = {}
    for code in SUBJECTS:
        p = mesh_path(code)
        if p is not None:
            out[code] = str(p)
    return out
