"""A colour per tile code, for LOOKING at the board's geometry.

`vtt/isocam.py` rasterizes the shapes for the depth map a painting is
conditioned on, and a depth ramp is exactly the wrong thing to judge a
silhouette by: everything at the same distance comes out the same grey, so a
roof and the wall under it are one shape and a crate is a smudge on a floor.

The same rasterizer takes a ``_colour_of(code, skin) -> (r, g, b)`` and will
shade by material instead. That parameter existed and nothing outside the
module ever passed one, which is why the only way to look at the board's
geometry was to build the browser and serve it.

Nothing here is a RULE and nothing here reaches a player: these are the colours
of a working drawing. The board's real palette is `TILE_STYLES` in
`activity-ui/src/lib/boardView.ts`, which the browser reads for the same codes.
"""
from __future__ import annotations

#: Rough, readable, and deliberately not the game's own palette — a working
#: drawing wants materials told apart, not a mood.
_BY_CODE: dict[str, tuple[int, int, int]] = {
    "#": (150, 146, 138), "R": (120, 116, 110), "T": (86, 128, 74),
    "O": (176, 170, 156), "o": (150, 116, 72), "n": (140, 104, 64),
    "w": (150, 146, 138), "A": (168, 162, 150), "+": (128, 96, 56),
    "/": (128, 96, 56), "p": (110, 110, 118),
    ".": (176, 176, 180), "g": (110, 150, 96), "s": (206, 190, 150),
    "b": (166, 132, 84), "=": (150, 150, 156), ",": (140, 138, 134),
    '"': (100, 140, 92), "~": (110, 160, 190), "W": (70, 120, 160),
    "m": (120, 106, 84), "i": (190, 214, 224), "%": (146, 142, 136),
    "u": (162, 158, 166), "^": (30, 30, 36), "x": (24, 24, 28),
    "l": (210, 110, 50), "f": (220, 140, 60), " ": (30, 30, 36),
}

#: Where a SKIN wants telling apart from the code it wears. Only where the
#: distinction is the point of looking — a roof against its own wall, rock
#: against masonry, canvas against timber.
_BY_SKIN: dict[str, tuple[int, int, int]] = {
    "townhouse": (176, 168, 152), "cobbles": (140, 140, 146),
    "palisade": (138, 104, 62), "canvas": (214, 208, 190),
    "tent-canopy": (222, 216, 198), "flap": (214, 208, 190),
    "boulder": (128, 124, 118), "cliff": (114, 110, 104),
    "coral": (196, 118, 128), "seabed": (176, 168, 140),
    "sea-deck": (156, 120, 74), "sky-deck": (150, 128, 92),
    "field-stone": (132, 128, 120), "foliage": (78, 120, 68),
}

_DECOR = (128, 120, 108)


def colour_of(code: str, skin: str = "") -> tuple[int, int, int]:
    """A working-drawing colour for one square's material."""
    if code.startswith("decor:"):
        return _DECOR
    if skin and skin in _BY_SKIN:
        return _BY_SKIN[skin]
    return _BY_CODE.get(code, (150, 150, 150))
