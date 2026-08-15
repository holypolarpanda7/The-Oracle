"""What each square IS, in a language the painter was trained to read.

The isometric render has had three inputs and none of them could say what an
individual square is:

* the **depth map** says where everything is and how tall — geometry only, so a
  two-foot shaft is a timber post, a candle or a bollard as far as the model can
  tell (it painted candles);
* the **prompt** says what the ROOM is, globally, and every skin's ``words``
  are concatenated into it where they compete — which is why "square oak posts
  … NOT candles" loses to the eleven other clauses beside it;
* the **ground-only init** says what the floor is MADE of — a colour average,
  not an identity.

A segmentation control image is the missing one. Every square is painted a flat
colour that names a CLASS, and a seg-conditioned model draws that class there.
It is spatially exact in a way a prompt can never be, and unlike attention
masking it does not weaken as the region gets smaller — which matters here,
because the things that come out wrong are scattered single squares.

## The palette is not ours and must not be invented

These RGB values are ADE20K's, as published in mmsegmentation's dataset
definition — the same palette the seg ControlNets were trained against. A colour
that is off by one is a different class or no class at all, and NOTHING in the
resulting picture would tell you which; it would just quietly draw the wrong
thing, which is the failure this module exists to fix. So the table was
extracted from the source rather than written from memory, and the smoke test
re-checks it.

**One trap lives in the palette itself**: ``road`` and ``skyscraper`` share
(140, 140, 140). It is the only duplicate in all 150 classes, and it means a
paved street painted ``road`` is genuinely ambiguous — so streets are painted
``path`` instead, and ``road`` is deliberately absent below.

## What decides a square's class

The SKIN first, because a skin is already the answer to "what is this made of
and what does it look like" — ``field-stone`` is a boulder wherever it appears,
whatever tile code is under it. Then the tile code's own default. A square with
neither is left unpainted, which the seg net reads as "no constraint" and is the
honest answer for a square we cannot name.
"""
from __future__ import annotations

from typing import Optional

from . import skins as _skins

#: ADE20K class -> RGB, from mmsegmentation's `ADE20KDataset.METAINFO`
#: (Apache-2.0). Only the classes this board vocabulary actually uses; adding
#: one means taking its colour from the same source, never guessing it — the
#: full 150 are kept beside this file in `ade20k_palette.json` so the smoke
#: test can check these against the original rather than against itself.
ADE20K: dict[str, tuple[int, int, int]] = {
    "wall": (120, 120, 120),
    "building": (180, 120, 120),
    "house": (255, 9, 224),
    "hovel": (255, 0, 255),
    "floor": (80, 50, 50),
    "path": (255, 31, 0),
    "earth": (120, 120, 70),
    "grass": (4, 250, 7),
    "sand": (160, 150, 20),
    "field": (112, 9, 255),
    "land": (0, 194, 255),
    "tree": (4, 200, 3),
    "palm": (0, 82, 255),
    "plant": (204, 255, 4),
    "water": (61, 230, 250),
    "sea": (9, 7, 230),
    "river": (11, 200, 200),
    "rock": (255, 41, 10),
    "mountain": (143, 255, 140),
    "column": (255, 8, 41),
    "pole": (51, 0, 255),
    "box": (0, 255, 20),
    "barrel": (255, 0, 112),
    "basket": (92, 255, 0),
    "table": (255, 6, 82),
    "bench": (194, 255, 0),
    "counter": (235, 12, 255),
    "bar": (0, 255, 153),
    "fireplace": (250, 10, 15),
    "light": (255, 173, 0),
    "fence": (255, 184, 6),
    "railing": (255, 61, 6),
    "door": (8, 255, 51),
    "stairs": (255, 224, 0),
    "tent": (112, 224, 255),
    "boat": (173, 255, 0),
    "ship": (255, 235, 0),
    "bridge": (255, 82, 0),
    "sculpture": (255, 255, 0),
    "stage": (82, 0, 255),
}

#: Tile code -> class, when no skin has a better answer. These follow the tile
#: taxonomy in ``vtt/terrain.py``; a code absent here is painted nothing.
SEG_BY_CODE: dict[str, str] = {
    "#": "wall",
    "R": "rock",
    "T": "tree",
    "O": "column",
    "o": "box",
    "n": "table",
    "w": "fence",
    "A": "sculpture",      # an altar: a worked stone thing standing on a floor
    "+": "door",
    "/": "door",
    "p": "fence",          # a portcullis is bars, and bars read as a fence
    "u": "stairs",
    "f": "fireplace",
    ".": "floor",
    "=": "path",
    "g": "grass",
    "s": "sand",
    "b": "floor",          # decking; a vessel's own skin overrides to `boat`
    "~": "water",
    "W": "sea",
    ",": "earth",
    '"': "plant",
    "m": "earth",
    "i": "water",
    "%": "earth",
    "u#": "stairs",
}

#: Skin -> class, where the skin knows better than the code under it.
#:
#: Kept here rather than as a field on ``Skin`` for one reason: this is a fact
#: about how a PAINTER is conditioned, not about what a square is made of or
#: shaped like, and ``Skin`` is generated into the browser, which has no painter
#: in it. The smoke test asserts every skin either appears here or falls back
#: to a code that does, so the two cannot drift apart silently.
SEG_BY_SKIN: dict[str, str] = {
    # Rock, and the distinction the boards already make between a mass and a
    # thing standing on its own.
    "cliff": "mountain",
    "cave-rock": "rock",
    "boulder": "rock",
    "field-stone": "rock",
    "scree": "earth",
    # Built.
    "townhouse": "building",
    "cobbles": "path",
    "masonry": "wall",
    "sewer-brick": "wall",
    "palisade": "fence",
    "tower-stone": "building",
    "tower-timber": "building",
    "ruin-stub": "wall",
    "ruin-floor": "floor",
    "broken-column": "column",
    "drowned-column": "column",
    "drowned-wall": "wall",
    # A taproom. These three are the whole reason the module exists: a post
    # painted as a column stops being a candle, a counter painted as a bar
    # stops being invisible, and boards stop being flagstones.
    "taproom-bar": "bar",
    "taproom-post": "column",
    "taproom-wall": "wall",
    "taproom-floor": "floor",
    # Canvas and vessels.
    "tent-canopy": "tent",
    "tent-wall": "tent",
    "sea-deck": "boat",
    "sky-deck": "boat",
    "hull": "boat",
    "mast": "pole",
    "railing": "railing",
    "sky-rail": "railing",
    "plating": "boat",
    "plating-rail": "railing",
    "plated-deck": "boat",
    "chitin": "boat",
    "chitin-rail": "railing",
    "chitin-deck": "boat",
    # Under the sea. ADE20K has no coral, and `plant` is the closest honest
    # thing standing on a sea floor — `rock` would ask for bare stone.
    "coral": "plant",
    "seabed-shallow": "sand",
    "seabed-deep": "sea",
    "seabed-sand": "sand",
    "sludge": "water",
    "sewer-ledge": "floor",
    # The rest of the catalogue, so nothing falls through to a code that means
    # something coarser than the skin does.
    "cabin-roof": "building",
    "canvas": "tent",
    "flap": "tent",
    "doorway-stone": "door",
    "doorway-timber": "door",
    "parapet": "wall",
    "tower-ladder": "stairs",
    "tower-post": "pole",
    "tower-top": "building",
}


def seg_class(code: str, skin: str = "") -> Optional[str]:
    """The ADE20K class this square should be painted as, or None for unpainted."""
    if skin:
        got = SEG_BY_SKIN.get(skin)
        if got:
            return got
        if _skins.is_setpiece(skin):
            # A landmark's own mesh. It is somebody else's model of a specific
            # thing and no tile class describes it; leaving it unpainted lets
            # the depth map speak for it alone, which is what drew it before.
            return None
    return SEG_BY_CODE.get(code)


def seg_colour(code: str, skin: str = "") -> tuple[int, int, int]:
    """Flat class colour for a square. Black where we cannot name it."""
    name = seg_class(code, skin)
    return ADE20K[name] if name else (0, 0, 0)


def seg_image(**depth_kw) -> bytes:
    """The board as a segmentation map. PNG bytes, pixel-aligned with the depth.

    Same rasterizer, same camera, same kwargs as ``isocam.depth_image`` — so the
    two conditioning images cannot describe different rooms, which is the same
    guarantee the terrain image already gives. ``_flat`` turns the face shading
    off: a lit class colour is a different colour, and a different colour is a
    different class.
    """
    from . import isocam

    return isocam.terrain_image(
        colour_of=lambda c, sk: seg_colour(c, sk), _flat=True, **depth_kw)
