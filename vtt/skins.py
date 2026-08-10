"""What a square is MADE OF, kept apart from what a square DOES.

**Why this exists.** The board has twenty-nine tile codes and twenty-one
archetypes, and until now a code meant exactly one thing everywhere. A ``#`` was
a wall — the same dressed masonry in a sewer, on a mountainside and around a
tent. So the mountain pass came back looking like a built corridor rather than a
canyon, the reef's columns stood pristine and carved on the seabed, and every
board's walls were the same wall.

The obvious fix — more tile codes — is the one the project has already ruled
out, and rightly: *"every new code costs rules meaning (cover, height,
breakability) and has to propagate to four renderers"*. A cliff and a wall are
not different RULES. They are both total cover, both block sight, both stop
movement. They are different STUFF.

So this is the same split :mod:`eight_card_system.placelore` already makes
between the land a place stands in and the surface it presents, applied one
level down:

* the **tile code** answers the rules — cover, movement, sight, breakability;
* the **skin** answers the eye — what it is made of, what shape it takes, and
  what the painter should be told about it.

Skins are invisible to every rule. Nothing in :mod:`vtt.geometry`,
:mod:`vtt.bridge` or the movement and cover code may read one, and none of it
does. That is the property that makes the vocabulary safe to grow: a new skin
can never change what a square does, so it can never break a fight.

**The one hard rule: a skin may not restate a height the rules quote.** A crate
screens four feet and a low wall three, and a player deciding whether they can
break line of sight is reading those off the board. A skin may reshape them —
a ship's rail is posts and a top rail rather than a slab — but
:func:`_check_heights` refuses at import any skin that changes how TALL they
stand. Walls and rock faces carry no quoted height (they are total cover at any
height), so those are free to vary, which is where all the useful variation is
anyway.

Two ways a square gets its skin, mirroring how the board already handles
elevation and decoration:

* **by archetype** — :data:`ARCH_SKINS` is a pure function of the generator that
  built the board, so it is DERIVED and never stored. Every ``R`` on a reef is
  coral; nothing has to write that down 300 times.
* **by square** — a generator that builds a specific thing (a tent's canvas
  inside a camp that also has a log palisade) records the exception on
  ``GeneratedMap.skins``, sparse and keyed ``"x,y"``, exactly like elevation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

#: A shape part, in the same language as :data:`vtt.isocam.OBJECT_VARIANTS`:
#: ``(x0, x1, z0, z1, y0, y1)`` — fractions of the square for x/z, fractions of
#: the thing's standing height for y.
Part = tuple[float, float, float, float, float, float]
Variants = tuple[tuple[Part, ...], ...]


@dataclass(frozen=True)
class Skin:
    """One material identity a square can wear.

    ``substance`` is the material-catalogue slug, so every coral thing on every
    reef in every session shares one swatch — the same economics as
    ``object_ref`` keying on the KIND of thing rather than the square.

    ``variants`` is an optional shape override. When present it wins over
    whatever the code would otherwise be drawn as, INCLUDING the wall-face model
    used for ``#`` and ``R`` — which is the point, because a mountainside drawn
    as thin wall panels is precisely what made the pass read as architecture.
    Several arrangements are better than one: :func:`vtt.isocam.variant_of`
    picks per square, so four chunky cliff variants read as natural rock where
    one would read as a repeated stamp.

    ``directional`` makes a run of squares line up along the way the run goes
    instead of taking a per-square quarter-turn. A rail, a palisade and a tent
    wall are things that RUN; a boulder is not.
    """

    name: str
    substance: str
    art: str
    #: What the painter is told. One clause, joined into the iso prompt.
    words: str = ""
    variants: Optional[Variants] = None
    #: Drawn height in feet, overriding the tile's own. Refused on any code the
    #: rules quote a height for — see the module docstring.
    height_ft: float = 0.0
    #: Line up along the run rather than taking a per-square quarter-turn.
    directional: bool = False


def _v(*arrangements: Sequence[Part]) -> Variants:
    return tuple(tuple(a) for a in arrangements)


# --------------------------------------------------------------------------
# Shapes
#
# Written out rather than generated, because each one is a silhouette somebody
# has to look at and judge. The model paints the shape it is handed: a square
# column comes back a square column, and a cliff drawn as a thin panel comes
# back as a wall however loudly the prompt says "canyon".
# --------------------------------------------------------------------------

#: Rock, drawn as MASS. Near-full-square footprints with stepped, uneven tops.
#: The wall-face model these replace exists to stop an enclosed ROOM reading as
#: a tray, which is the right worry for masonry and the wrong one for a
#: mountainside — a pass is a canyon cut through solid rock, and the rock should
#: look solid.
_CLIFF = _v(
    ((0.02, 0.98, 0.02, 0.98, 0.0, 0.60), (0.12, 0.86, 0.08, 0.90, 0.60, 0.86),
     (0.30, 0.70, 0.26, 0.66, 0.86, 1.0)),
    ((0.00, 0.92, 0.06, 1.00, 0.0, 0.70), (0.18, 0.80, 0.20, 0.84, 0.70, 1.0)),
    ((0.06, 1.00, 0.00, 0.94, 0.0, 0.55), (0.10, 0.70, 0.16, 0.78, 0.55, 0.92),
     (0.50, 0.94, 0.40, 0.86, 0.55, 0.78)),
    ((0.04, 0.96, 0.04, 0.96, 0.0, 0.80), (0.22, 0.68, 0.30, 0.74, 0.80, 1.0)),
)

#: Coral heads: lower, lumpier, branching. Same idea as the cliff and a
#: different silhouette, so a reef never reads as a quarry.
_CORAL = _v(
    ((0.20, 0.62, 0.24, 0.66, 0.0, 0.72), (0.50, 0.86, 0.44, 0.80, 0.0, 0.50),
     (0.30, 0.54, 0.34, 0.58, 0.72, 1.0)),
    ((0.16, 0.56, 0.30, 0.72, 0.0, 0.60), (0.46, 0.90, 0.18, 0.60, 0.0, 0.86),
     (0.34, 0.62, 0.50, 0.80, 0.60, 0.80)),
    ((0.24, 0.78, 0.22, 0.76, 0.0, 0.42), (0.36, 0.60, 0.34, 0.58, 0.42, 1.0),
     (0.60, 0.84, 0.50, 0.74, 0.42, 0.70)),
    ((0.10, 0.50, 0.20, 0.58, 0.0, 0.80), (0.44, 0.72, 0.46, 0.82, 0.0, 0.56),
     (0.20, 0.42, 0.30, 0.50, 0.80, 1.0)),
)

#: A snapped column. Ruins and drowned ruins want the pillar to have ALREADY
#: fallen — an intact colonnade on a seabed is a stranger sight than a broken
#: one, and the board had no way to say so.
_BROKEN_COLUMN = _v(
    ((0.30, 0.70, 0.30, 0.70, 0.0, 0.44), (0.32, 0.68, 0.32, 0.68, 0.44, 0.52)),
    ((0.30, 0.70, 0.30, 0.70, 0.0, 0.70), (0.34, 0.64, 0.30, 0.60, 0.70, 0.78)),
    ((0.32, 0.68, 0.32, 0.68, 0.0, 0.28), (0.10, 0.86, 0.22, 0.48, 0.0, 0.14)),
    ((0.28, 0.66, 0.34, 0.72, 0.0, 0.58), (0.30, 0.62, 0.36, 0.68, 0.58, 0.66),
     (0.60, 0.92, 0.30, 0.54, 0.0, 0.16)),
)

#: A mast: a pole, a yard across it, and a top. One variant on purpose — a ship
#: has one mast and it is not a random thing.
_MAST = _v(
    ((0.43, 0.57, 0.43, 0.57, 0.0, 1.0),
     (0.06, 0.94, 0.46, 0.54, 0.60, 0.655),
     (0.20, 0.80, 0.47, 0.53, 0.84, 0.875),
     (0.34, 0.66, 0.34, 0.66, 0.0, 0.06)),
)

#: A ship's rail: stanchions and a top rail, so you can see the sea THROUGH it.
#: Drawn along the run, and at exactly the three feet the rules quote — the
#: shape changes, the height may not.
_RAILING = _v(
    ((0.08, 0.20, 0.42, 0.58, 0.0, 1.0), (0.44, 0.56, 0.42, 0.58, 0.0, 1.0),
     (0.80, 0.92, 0.42, 0.58, 0.0, 1.0),
     (0.00, 1.00, 0.44, 0.56, 0.80, 1.00),
     (0.00, 1.00, 0.45, 0.55, 0.34, 0.46)),
)

#: A palisade: sharpened logs shoulder to shoulder, pointed tops.
_PALISADE = _v(
    ((0.00, 0.24, 0.34, 0.66, 0.0, 0.90), (0.04, 0.20, 0.40, 0.60, 0.90, 1.0),
     (0.26, 0.50, 0.34, 0.66, 0.0, 0.96), (0.30, 0.46, 0.40, 0.60, 0.96, 1.0),
     (0.52, 0.76, 0.34, 0.66, 0.0, 0.88), (0.56, 0.72, 0.40, 0.60, 0.88, 1.0),
     (0.78, 1.00, 0.34, 0.66, 0.0, 0.94)),
)

#: A tent wall: canvas leaning in to a ridge. Drawn along the run so a tent
#: reads as one tent instead of four quarter-turned fragments.
_TENT_WALL = _v(
    ((0.00, 1.00, 0.16, 0.84, 0.0, 0.46), (0.00, 1.00, 0.26, 0.74, 0.46, 0.78),
     (0.00, 1.00, 0.38, 0.62, 0.78, 1.00)),
)

#: A stone parapet: a merloned tower top, so a watchtower reads as somewhere to
#: shoot from rather than a box.
_PARAPET = _v(
    ((0.00, 1.00, 0.30, 0.70, 0.0, 0.62),
     (0.00, 0.30, 0.26, 0.74, 0.62, 1.00),
     (0.42, 0.72, 0.26, 0.74, 0.62, 1.00)),
)

#: Steampunk plating: riveted panels and a pipe run along the hull.
_PLATING = _v(
    ((0.00, 1.00, 0.32, 0.68, 0.0, 0.84), (0.00, 1.00, 0.26, 0.74, 0.84, 1.0),
     (0.10, 0.34, 0.20, 0.30, 0.30, 0.62), (0.62, 0.88, 0.20, 0.30, 0.30, 0.62)),
)

#: A grown hull: ribbed chitin, no straight line anywhere.
_CHITIN = _v(
    ((0.00, 1.00, 0.34, 0.66, 0.0, 0.58), (0.06, 0.94, 0.28, 0.72, 0.58, 0.86),
     (0.22, 0.78, 0.36, 0.64, 0.86, 1.00)),
    ((0.00, 1.00, 0.30, 0.70, 0.0, 0.70), (0.14, 0.86, 0.36, 0.64, 0.70, 1.00)),
)


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------

SKINS: dict[str, Skin] = {s.name: s for s in (
    # --- rock, in its several honest forms -------------------------------
    Skin("cliff", "granite",
         "raw grey granite, close-up of the bare fractured rock face",
         words="the rock is a natural granite cliff face, fractured and "
               "weathered, not built masonry and not brickwork",
         variants=_CLIFF, height_ft=14),
    Skin("scree", "scree",
         "close-up of loose shale and broken slate scree",
         words="the ground is loose shale and scree"),
    Skin("cave-rock", "limestone",
         "close-up of damp limestone cave rock, flowstone and mineral streaks",
         words="the walls are living cave rock, damp limestone",
         variants=_CLIFF, height_ft=13),

    # --- the sea ----------------------------------------------------------
    Skin("coral", "coral",
         "close-up of living reef coral, brain coral and branching polyps",
         words="the reef heads are living coral in ochre and violet, "
               "encrusted and irregular",
         variants=_CORAL, height_ft=9),
    Skin("drowned-column", "drowned-stone",
         "close-up of ancient pale stone crusted with barnacles and weed",
         words="the columns are ancient, SNAPPED OFF and toppled, furred with "
               "weed and barnacle — a drowned ruin, nothing intact",
         variants=_BROKEN_COLUMN, height_ft=9),
    # No height override: a low wall screens three feet and that is a number a
    # player reads off the board. The SHAPE says it is a ruin — an eroded,
    # broken-topped stub instead of a tidy waist-high course — and the height
    # stays exactly what the rules quote.
    Skin("drowned-wall", "drowned-stone",
         "close-up of ancient pale stone crusted with barnacles and weed",
         words="what walls remain are collapsed stubs of drowned masonry",
         variants=_v(
             ((0.00, 1.00, 0.18, 0.78, 0.0, 0.72),
              (0.06, 0.62, 0.22, 0.70, 0.72, 1.00)),
             ((0.00, 1.00, 0.22, 0.80, 0.0, 0.58),
              (0.34, 0.94, 0.26, 0.74, 0.58, 1.00)),
         ), directional=True),

    # --- the underneath ---------------------------------------------------
    Skin("sewer-brick", "sewer-brick",
         "close-up of slime-blackened brickwork, wet mortar, green-black "
         "staining and mineral weep",
         words="the brickwork is filthy — black slime to the tide line, weeping "
               "mortar, rust stains and fungal bloom"),
    Skin("sludge", "sludge",
         "close-up of thick stagnant green-brown effluent, scum and floating "
         "debris",
         words="the channel runs with foul green-brown effluent, scummed and "
               "littered"),
    Skin("sewer-ledge", "wet-flagstone",
         "close-up of wet slimed flagstone, standing water in the joints",
         words="the ledges are wet slimed flagstone"),

    # --- built things -----------------------------------------------------
    Skin("masonry", "dressed-stone",
         "close-up of dressed and coursed grey building stone"),
    Skin("parapet", "dressed-stone",
         "close-up of dressed and coursed grey building stone",
         words="the tower tops are merloned stone parapets",
         variants=_PARAPET, height_ft=9, directional=True),
    Skin("palisade", "log-palisade",
         "close-up of a wall of upright split logs, bark and axe marks",
         words="the camp wall is a palisade of sharpened upright logs",
         variants=_PALISADE, height_ft=10, directional=True),
    Skin("canvas", "canvas",
         "close-up of heavy weathered tent canvas, coarse weave, stained and "
         "patched",
         words="the tents are heavy stained canvas over timber poles, guy ropes "
               "pegged out, flaps tied back",
         variants=_TENT_WALL, height_ft=9, directional=True),

    # --- ships ------------------------------------------------------------
    Skin("hull", "tarred-planking",
         "close-up of tarred ship planking, caulked seams and iron nail heads",
         words="the hull is tarred carvel planking with caulked seams"),
    Skin("deck", "deck-planking",
         "close-up of holystoned ship deck planking, pale scrubbed oak, pitched "
         "seams",
         words="the deck is scrubbed pale oak planking, seams payed with pitch"),
    Skin("mast", "spar-timber",
         "close-up of oiled spar timber, banded with iron",
         words="a single mast steps amidships, yard crossed and rigging set up "
               "to the rails",
         variants=_MAST, height_ft=26),
    Skin("railing", "spar-timber",
         "close-up of oiled spar timber, banded with iron",
         words="the deck is edged with a stanchion rail you can see the water "
               "through",
         variants=_RAILING, directional=True),
    Skin("plating", "riveted-brass",
         "close-up of riveted brass and iron plating, verdigris and oil stains",
         words="the vessel is a riveted brass-and-iron contraption — rivets, "
               "pipework, pressure gauges, vented steam",
         variants=_PLATING, height_ft=8, directional=True),
    Skin("chitin", "chitin",
         "close-up of ridged organic chitin shell, iridescent and veined",
         words="the vessel is GROWN rather than built — ridged chitin, veined "
               "and iridescent, no straight lines anywhere",
         variants=_CHITIN, height_ft=8, directional=True),
    # The rail versions. Same substance, same silhouette as any other rail, and
    # emphatically the same THREE FEET — a ship's rail is half cover, and what
    # it is made of has no vote on how tall it is.
    Skin("plating-rail", "riveted-brass",
         "close-up of riveted brass and iron plating, verdigris and oil stains",
         words="the deck is edged with a pipework rail you can see the air "
               "through",
         variants=_RAILING, directional=True),
    Skin("chitin-rail", "chitin",
         "close-up of ridged organic chitin shell, iridescent and veined",
         words="the deck is edged with a grown chitin lip, ribbed and low",
         variants=_RAILING, directional=True),
)}


# --------------------------------------------------------------------------
# Who wears what
# --------------------------------------------------------------------------

#: archetype -> {tile code: skin name}. Derived, never stored: the archetype is
#: on the map row, so this is a lookup rather than three hundred rows of the
#: same fact. A code with no entry keeps its default look, which is what every
#: board had before skins existed.
ARCH_SKINS: dict[str, dict[str, str]] = {
    "mountain-pass": {"R": "cliff", "#": "cliff", "O": "cliff",
                      ",": "scree", ".": "scree"},
    "cave":          {"R": "cave-rock", "#": "cave-rock"},
    "mine":          {"R": "cave-rock", "#": "cave-rock"},
    "reef":          {"R": "coral", "O": "drowned-column", "w": "drowned-wall"},
    "open-water":    {"R": "coral"},
    "sewer":         {"#": "sewer-brick", "~": "sludge", ".": "sewer-ledge",
                      "W": "sludge", ",": "sewer-ledge"},
    "camp":          {"#": "palisade"},
    "ship":          {"b": "deck", "w": "railing", "O": "mast", "#": "hull"},
    "skyship":       {"b": "deck", "w": "railing", "O": "mast", "#": "hull"},
    "ruins":         {"O": "drowned-column", "#": "masonry", "w": "masonry"},
    "crypt":         {"#": "masonry"},
    "dungeon-room":  {"#": "masonry"},
    "dungeon-complex": {"#": "masonry"},
    "arena":         {"#": "masonry"},
    "sky-islands":   {"R": "cliff"},
}

#: A skyship is the one board with a genuine style CHOICE rather than a
#: material: the same deck can be a brass-and-steam contraption or a grown
#: organic hull, and both are right. The generator picks one and records it, so
#: a table's ship stays the ship it was.
SKYSHIP_STYLES: dict[str, dict[str, str]] = {
    "timber":    {},
    "steampunk": {"#": "plating", "w": "plating-rail", "b": "deck",
                  "O": "mast"},
    "organic":   {"#": "chitin", "w": "chitin-rail", "b": "deck", "O": "mast"},
}


def _check_heights() -> None:
    """A skin may reshape a quoted height. It may never restate one.

    ``cover_height_ft`` is the number a player reads off the board when they
    decide whether they can break line of sight, and against something stronger
    than them that decision is most of the fight. Drawing a crate at five feet
    because it looked better invents a difference the engine will not honour, at
    exactly the moment being misled is expensive.
    """
    from .terrain import cover_height_ft

    bad: dict[str, str] = {}
    for arch, mapping in list(ARCH_SKINS.items()) + \
            [("skyship:" + k, v) for k, v in SKYSHIP_STYLES.items()]:
        for code, name in mapping.items():
            sk = SKINS.get(name)
            if sk is None:
                bad[f"{arch}.{code}"] = f"unknown skin {name!r}"
            elif sk.height_ft and cover_height_ft(code):
                bad[f"{arch}.{code}"] = (
                    f"skin {name!r} redraws {code!r} at {sk.height_ft} ft, but "
                    f"the rules quote {cover_height_ft(code)} ft")
    if bad:
        raise ValueError(f"illegal skin assignment: {bad}")


def skins_for(archetype: str, *, style: str = "") -> dict[str, str]:
    """The default skin for each tile code on a board of this archetype."""
    arch = (archetype or "").strip().lower()
    if arch == "skyship" and style in SKYSHIP_STYLES:
        out = dict(ARCH_SKINS.get(arch, {}))
        out.update(SKYSHIP_STYLES[style])
        return out
    return dict(ARCH_SKINS.get(arch, {}))


def skin_at(code: str, x: int, y: int, *, codes: Optional[dict] = None,
            squares: Optional[dict] = None) -> str:
    """This square's skin name, or "" for the code's own default look.

    A per-square override beats the archetype default, which is the whole point
    of having both: a camp is palisaded, and the tents inside it are canvas.
    """
    if squares:
        got = squares.get(f"{x},{y}")
        if got:
            return str(got)
    if codes:
        return str(codes.get(code) or "")
    return ""


def skin(name: str) -> Optional[Skin]:
    return SKINS.get(name or "")


def variants_of(name: str) -> Optional[Variants]:
    sk = SKINS.get(name or "")
    return sk.variants if sk else None


def height_of(name: str) -> float:
    sk = SKINS.get(name or "")
    return sk.height_ft if sk else 0.0


def is_directional(name: str) -> bool:
    sk = SKINS.get(name or "")
    return bool(sk and sk.directional)


def substances() -> dict[str, str]:
    """Every substance the catalogue needs, ``{slug: subject}``.

    What ``scripts/material_prerender.py`` iterates. Substances are shared —
    a reef's coral heads and its coral rubble are one swatch — so this is far
    shorter than the skin list.
    """
    out: dict[str, str] = {}
    for sk in SKINS.values():
        out.setdefault(sk.substance, sk.art)
    return out


def words_for(names: Sequence[str]) -> str:
    """What the painter is told about this board's materials.

    Only for skins actually PRESENT, so an ordinary dungeon is not told about
    coral. Deduplicated by clause, because a reef wears three skins that share
    one sentence about drowned stone.
    """
    seen: list[str] = []
    for n in names:
        sk = SKINS.get(n or "")
        if sk and sk.words and sk.words not in seen:
            seen.append(sk.words)
    return ", ".join(seen)


_check_heights()
