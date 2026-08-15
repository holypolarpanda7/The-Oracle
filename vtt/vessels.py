"""What SHAPE a ship is, so two of them are not the same ship.

Every vessel board carved the same hull. ``_hull`` took its length and beam
from the BOARD — ``width - 2`` by ``height - 2`` — so a two-crew skyskiff and a
forty-passenger cruiser came out as identical outlines filling identical
frames, and the only thing telling them apart was whether the water was under
them. Meanwhile ``airships/catalog.py`` has known the difference the whole time:
it carries crew, passengers, cargo and hull points per vessel and nothing ever
asked it.

A CLASS is the missing middle. It says how long a hull is in squares, how broad
in proportion, which plan it is cut to and how much of the board it should take
— and it can be derived from a real vessel's own numbers or rolled from the
seed when nobody named one. Those are the four things the outline needs; every
other difference between two ships (deck, rail, mast, skin) already existed and
was being applied to one shape.

**Length and beam are the SILHOUETTE, and the silhouette is what the painter is
conditioned on.** That is why this is worth more than it looks: the depth map
carries the outline and nothing else about a vessel, so two ships with the same
outline are two pictures of the same ship however different their prompts are.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class HullClass:
    """One kind of vessel, as a shape.

    ``length`` and ``beam`` are in SQUARES and are what the vessel wants, not
    what the board has — a skiff on a big board is a small ship in a lot of sky
    rather than a skiff stretched to the frame, which is most of what made every
    vessel look alike.

    ``fineness`` is how sharply the ends come to a point: low is a blunt trader,
    high is a courier that cuts. ``plan`` is the profile family — a sea hull is
    fine at the bow and flat at the transom, a sky hull is fine at both ends.
    """

    slug: str
    name: str
    plan: str                  # "sea" | "sky"
    length: int
    beam: int
    fineness: float = 1.0
    #: A deck this big wants dividing: rooms, not one open floor. What a
    #: bastion vessel is, and what a skiff never is.
    compartments: int = 0
    words: str = ""


#: The shapes a board can cut, smallest first. Sizes are in five-foot squares,
#: so a skiff is 45 ft on deck and a galleon 130 — which are real numbers for
#: the things they are named after, and more to the point are numbers a player
#: can feel the difference between when they walk from one end to the other.
CLASSES: tuple[HullClass, ...] = (
    HullClass("skiff", "skiff", "sky", 9, 4, fineness=1.35,
              words="a small open skiff, barely more than a hull and a mast"),
    HullClass("cutter", "cutter", "sea", 12, 6, fineness=1.30,
              words="a lean cutter built for speed, low in the water"),
    HullClass("courier", "courier", "sky", 13, 5, fineness=1.30,
              words="a slender courier, fine at both ends"),
    HullClass("caravel", "caravel", "sea", 16, 8, fineness=1.0,
              words="a working caravel, full through the waist"),
    HullClass("trader", "trader", "sky", 18, 9, fineness=0.78, compartments=2,
              words="a broad-bellied trader, deep-hulled and slow"),
    HullClass("galleon", "galleon", "sea", 22, 11, fineness=0.85, compartments=2,
              words="a high-sided galleon, tall at the stern"),
    HullClass("cruiser", "air cruiser", "sky", 24, 11, fineness=0.95,
              compartments=3,
              words="a great air cruiser, decks and cabins along her length"),
)

_BY_SLUG = {c.slug: c for c in CLASSES}


def get(slug: str) -> Optional[HullClass]:
    return _BY_SLUG.get((slug or "").strip().lower())


def for_vessel(vessel: Optional[dict], *, sky: bool = True) -> Optional[HullClass]:
    """The hull class a catalogued vessel wants, from its own numbers.

    Derived rather than authored beside each vessel, for the reason the whole
    owned-books split exists: the fleet is DATA in a gitignored slot and this is
    code in the repo, so a hull table naming those vessels could not be
    committed and a vessel added tomorrow would have no shape at all. Crew,
    passengers and cargo are what a ship is FOR, and what it is for is what
    decides how big a deck it needs.
    """
    if not vessel:
        return None
    aboard = (int(vessel.get("crew") or 0)
              + int(vessel.get("passengers") or 0))
    cargo = float(vessel.get("cargo_tons") or 0)
    # Deck squares wanted: somewhere for everyone aboard to stand, plus hold.
    want = aboard * 1.6 + cargo * 4.0
    plan = "sky" if sky else "sea"
    pool = [c for c in CLASSES if c.plan == plan] or list(CLASSES)
    return min(pool, key=lambda c: abs(c.length * c.beam * 0.62 - want))


def rolled(seed: int, *, sky: bool = True,
           width: int = 0, height: int = 0) -> HullClass:
    """A hull class from the seed, when nobody named a vessel.

    Weighted toward the middle: most ships anyone fights on are working ships,
    and a board that is a great cruiser one time in three stops reading as a
    special occasion.

    Given a board size, only classes that SUIT it are rolled — long enough to be
    most of what is there, short enough to fit. A skiff on a thirty-square board
    is nine squares of deck in six hundred of sea, which is a true picture of a
    skiff and an unplayable board; the vessel a DM NAMES may still be that small,
    because then it is a choice somebody made.
    """
    rng = random.Random((int(seed) * 2246822519) & 0xFFFFFFFF)
    plan = "sky" if sky else "sea"
    pool = [c for c in CLASSES if c.plan == plan] or list(CLASSES)
    if width and height:
        room = max(6, width - 2)
        fits = [c for c in pool if c.length <= room and c.length >= room * 0.45]
        pool = fits or [min(pool, key=lambda c: abs(c.length - room))]
    weights = [1 if (c.length <= 11 or c.length >= 22) else 3 for c in pool]
    return rng.choices(pool, weights=weights, k=1)[0]


def fitted(cls: HullClass, width: int, height: int) -> tuple[int, int]:
    """The class's length and beam, cut down to what this board can hold.

    A vessel keeps its PROPORTIONS when it will not fit: shrinking length and
    beam together keeps a courier a courier, where clamping each against the
    board independently turns every large vessel into the board's own rectangle
    — which is the bug this module exists to end.
    """
    max_len = max(6, width - 2)
    max_beam = max(3, height - 2)
    scale = min(1.0, max_len / cls.length, max_beam / cls.beam)
    return max(6, int(cls.length * scale)), max(3, int(cls.beam * scale))


def half_beam(cls: HullClass, t: float, beam: float) -> float:
    """Half-width at ``t`` along the hull; 0 is the bow, 1 the stern.

    The two plans, with the class's own fineness on top — which is what makes a
    cutter and a caravel two sea ships rather than one sea ship at two sizes.
    """
    f = max(0.3, float(cls.fineness))
    if cls.plan == "sky":
        # Fine at BOTH ends — nothing about air rewards a transom — and fullest
        # a little aft of amidships where the lift is.
        k = max(0.16, math.sin(math.pi * min(1.0, t * 0.92 + 0.04)) ** (0.62 * f))
        return k * 0.88 * (beam / 2.0)
    if t < 0.42:                       # the bow, entering fine
        k = 0.10 + 0.90 * (t / 0.42) ** (0.62 * f)
    elif t < 0.78:                     # the waist, full beam
        k = 1.0
    else:                              # quarters easing to a flat stern
        k = 1.0 - 0.28 * ((t - 0.78) / 0.22) ** 1.4
    return k * (beam / 2.0)
