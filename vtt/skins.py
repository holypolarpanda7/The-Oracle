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
    #: Pick the arrangement from a COARSE hash, so neighbouring squares usually
    #: match. For anything that is a MASS rather than a set of objects: a rock
    #: face varying per square is a field of separate cubes (measured — the
    #: first mountain pass came back exactly that), where the same variation
    #: spread over two-square blocks reads as ridges and shelves. Objects want
    #: the opposite, which is why it is a flag and not the default.
    smooth: bool = False
    #: How deep this floor's own SIDE is, in feet. Non-zero means the square
    #: carries its own edge wherever it meets something that is not the same
    #: skin — which is how a ship gets a hull. The default skirt only fires
    #: against a HOLE, and deep water is not a hole, so a sea ship had no sides
    #: at all: a deck lying flat on the water like a raft.
    skirt_ft: float = 0.0
    #: How far the BOTTOM of that side pulls in toward the square's centre, as
    #: a fraction. A vertical drop is a slab; pulled in, the same two triangles
    #: become a trapezoid, and a hull with tumblehome stops reading as a box.
    #: The tile grid is untouched — this is a drawing, like WALL_THICKNESS.
    skirt_inset: float = 0.0


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

#: Rock, drawn as MASS.
#:
#: The wall-face model this replaces exists to stop an enclosed ROOM reading as
#: a tray, which is the right worry for masonry and the wrong one for a
#: mountainside — a pass is a canyon cut through solid rock, and the rock should
#: look solid.
#:
#: Two things learned from the first attempt, which came back as a field of
#: white DICE. Footprints must run the FULL square: inset even slightly, each
#: square is its own little block with a visible gap round it, and a hundred of
#: them is a bead curtain rather than a cliff. And the variation has to be in
#: the HEIGHT rather than in fussy stacked nubs on top — a rock face reads as
#: rock because its skyline is broken, and neighbouring squares at 100%, 74% and
#: 46% of the same height do that where three differently-decorated cubes of
#: equal height do not. (`height_scale`'s jitter is only 12%, deliberately, and
#: nowhere near enough on its own.)
_CLIFF = _v(
    ((0.0, 1.0, 0.0, 1.0, 0.0, 1.00),),
    ((0.0, 1.0, 0.0, 1.0, 0.0, 0.74), (0.14, 0.82, 0.20, 0.88, 0.74, 0.90)),
    ((0.0, 1.0, 0.0, 1.0, 0.0, 0.58), (0.28, 1.00, 0.00, 0.72, 0.58, 0.84)),
    ((0.0, 1.0, 0.0, 1.0, 0.0, 0.88), (0.10, 0.72, 0.24, 0.90, 0.88, 1.00)),
    ((0.0, 1.0, 0.0, 1.0, 0.0, 0.46),),
    ((0.0, 1.0, 0.0, 1.0, 0.0, 0.66), (0.00, 0.64, 0.30, 1.00, 0.66, 0.94)),
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

#: A tent wall: canvas leaning steeply in to a ridge. Drawn along the run so a
#: tent reads as one tent instead of four quarter-turned fragments.
#:
#: The first version was a mild lean at nine feet, and every camp came back with
#: three timber PENS in it. A tent is not a short building — what says "tent" is
#: the pitch of the canvas, so the slope runs from full width at the pegs to a
#: ridge line at the top across four steps, and the whole thing is lower than a
#: wall. The material was already canvas; the model was painting the silhouette
#: it was handed, which is the same lesson as the pillar and the ship's hull.
_TENT_WALL = _v(
    ((0.00, 1.00, 0.02, 0.98, 0.00, 0.26),
     (0.00, 1.00, 0.16, 0.84, 0.26, 0.56),
     (0.00, 1.00, 0.31, 0.69, 0.56, 0.82),
     (0.00, 1.00, 0.43, 0.57, 0.82, 1.00)),
)

#: A tower wall: a tall solid mass with merlons crowning it.
#:
#: This replaces putting a nine-foot PARAPET on the ground storey, which is
#: what a watchtower's walls were built from and is why they came back as four
#: low crenellated boxes instead of towers. A parapet is the thing you crouch
#: behind on the roof; the storey below it is a wall, and it has to be tall
#: enough that the platform on top reads as raised.
#:
#: Two arrangements with the merlons in different places, quarter-turned per
#: square, so a ring of them crenellates unevenly instead of marching.
_TOWER = _v(
    ((0.00, 1.00, 0.00, 1.00, 0.0, 0.86),
     (0.02, 0.40, 0.02, 0.40, 0.86, 1.00),
     (0.60, 0.98, 0.02, 0.40, 0.86, 1.00)),
    ((0.00, 1.00, 0.00, 1.00, 0.0, 0.86),
     (0.30, 0.70, 0.02, 0.40, 0.86, 1.00),
     (0.02, 0.34, 0.60, 0.98, 0.86, 1.00)),
)

#: A doorway: two jambs and a lintel over an OPEN passage.
#:
#: Needed because an open doorway is a walkable square with no height, so in a
#: sixteen-foot tower wall it drew as a gap running from the ground to the sky —
#: which reads as a missing wall, not a way in. The passage is left clear below
#: the lintel; the square stays exactly as walkable as it always was.
_DOORWAY = _v(
    ((0.00, 0.15, 0.28, 0.72, 0.00, 1.00),
     (0.85, 1.00, 0.28, 0.72, 0.00, 1.00),
     (0.00, 1.00, 0.28, 0.72, 0.58, 1.00)),
)

#: A tent flap: the canvas rolled back at the jambs, a valance across the top.
#: The same job as _DOORWAY and the same rule — the passage stays clear, because
#: the square is one the rules let you walk through.
_FLAP = _v(
    ((0.00, 0.14, 0.28, 0.72, 0.00, 0.92),
     (0.86, 1.00, 0.28, 0.72, 0.00, 0.92),
     (0.00, 1.00, 0.34, 0.66, 0.72, 1.00)),
)

#: A stone parapet: a merloned roof edge, for the platform ON TOP of a tower.
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
         variants=_CLIFF, height_ft=14, smooth=True),
    Skin("scree", "scree",
         "close-up of loose shale and broken slate scree",
         words="the ground is loose shale and scree"),
    Skin("cave-rock", "limestone",
         "a flat expanse of damp grey limestone rock, mineral streaks and "
         "flowstone, filling the whole frame",
         words="the walls are living cave rock, damp limestone",
         variants=_CLIFF, height_ft=13, smooth=True),

    # --- the sea ----------------------------------------------------------
    Skin("coral", "coral",
         "close-up of living reef coral, brain coral and branching polyps",
         words="the reef heads are living coral in ochre and violet, "
               "encrusted and irregular",
         variants=_CORAL, height_ft=9, smooth=True),
    Skin("drowned-column", "drowned-stone",
         "a flat expanse of pale ancient cut stone crusted with barnacles and "
         "green weed, filling the whole frame",
         words="the columns are ancient, SNAPPED OFF and toppled, furred with "
               "weed and barnacle — a drowned ruin, nothing intact",
         variants=_BROKEN_COLUMN, height_ft=9),
    # No height override: a low wall screens three feet and that is a number a
    # player reads off the board. The SHAPE says it is a ruin — an eroded,
    # broken-topped stub instead of a tidy waist-high course — and the height
    # stays exactly what the rules quote.
    Skin("drowned-wall", "drowned-stone",
         "a flat expanse of pale ancient cut stone crusted with barnacles and "
         "green weed, filling the whole frame",
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
         "thick stagnant green-brown sewage seen straight down, greasy scum "
         "film and floating litter, no bank and no edge anywhere in frame",
         words="the channel runs with foul green-brown effluent, scummed and "
               "littered"),
    Skin("sewer-ledge", "wet-flagstone",
         "close-up of wet slimed flagstone, standing water in the joints",
         words="the ledges are wet slimed flagstone"),

    # --- built things -----------------------------------------------------
    Skin("masonry", "dressed-stone",
         "close-up of dressed and coursed grey building stone"),
    # What a watchtower is BUILT of, and the DM's narration decides which — a
    # crossing in deep forest gets a timber tower and a mountain road a stone
    # one. See building_material: the board reads it off the biome the DM
    # already typed, so nothing new has to be asked for.
    Skin("tower-stone", "dressed-stone",
         "close-up of dressed and coursed grey building stone",
         words="the watchtowers are squat drystone towers, merloned at the top",
         variants=_TOWER, height_ft=16),
    Skin("tower-timber", "log-palisade",
         "close-up of a wall of upright split logs, bark and axe marks",
         words="the watchtowers are stockades of upright logs, hoarding at the "
               "top",
         variants=_TOWER, height_ft=14),
    Skin("flap", "canvas",
         "a flat expanse of heavy woven CLOTH — coarse canvas sailcloth, "
         "off-white and grey, individual threads visible, stained and patched, "
         "soft fabric with no wood and no planks anywhere",
         words="the tent flaps are tied back at the poles",
         variants=_FLAP, height_ft=8, directional=True),
    Skin("doorway-stone", "dressed-stone",
         "close-up of dressed and coursed grey building stone",
         words="a low arched doorway is cut through at ground level",
         variants=_DOORWAY, height_ft=16, directional=True),
    Skin("doorway-timber", "log-palisade",
         "close-up of a wall of upright split logs, bark and axe marks",
         words="a plank-framed doorway is cut through at ground level",
         variants=_DOORWAY, height_ft=14, directional=True),
    Skin("parapet", "dressed-stone",
         "close-up of dressed and coursed grey building stone",
         words="the tower tops are merloned stone parapets",
         variants=_PARAPET, height_ft=9, directional=True),
    Skin("palisade", "log-palisade",
         "close-up of a wall of upright split logs, bark and axe marks",
         words="the camp wall is a palisade of sharpened upright logs",
         variants=_PALISADE, height_ft=10, directional=True),
    Skin("canvas", "canvas",
         "a flat expanse of heavy woven CLOTH — coarse canvas sailcloth, "
         "off-white and grey, individual threads visible, stained and patched, "
         "soft fabric with no wood and no planks anywhere",
         words="the tents are heavy stained canvas over timber poles, guy ropes "
               "pegged out, flaps tied back",
         variants=_TENT_WALL, height_ft=8, directional=True),

    # --- ships ------------------------------------------------------------
    Skin("hull", "tarred-planking",
         "close-up of tarred ship planking, caulked seams and iron nail heads",
         words="the hull is tarred carvel planking with caulked seams"),
    Skin("deck", "deck-planking",
         "close-up of holystoned ship deck planking, pale scrubbed oak, pitched "
         "seams",
         words="the deck is scrubbed pale oak planking, seams payed with pitch",
         skirt_ft=7, skirt_inset=0.5),
    Skin("sea-deck", "deck-planking",
         "close-up of holystoned ship deck planking, pale scrubbed oak, pitched "
         "seams",
         words="a working sea deck — planking dark with spray, salt-bleached "
               "in patches, the hull tarred black where it meets the water",
         skirt_ft=9, skirt_inset=0.62),
    Skin("mast", "spar-timber",
         "a flat expanse of oiled timber, straight close grain all running one "
         "way, no corners and no edges",
         words="a single mast steps amidships, yard crossed and rigging set up "
               "to the rails",
         variants=_MAST, height_ft=26),
    Skin("railing", "spar-timber",
         "a flat expanse of oiled timber, straight close grain all running one "
         "way, no corners and no edges",
         words="the deck is edged with a stanchion rail you can see the water "
               "through",
         variants=_RAILING, directional=True),
    Skin("plating", "riveted-brass",
         "close-up of riveted brass and iron plating, verdigris and oil stains",
         words="the vessel is a riveted brass-and-iron contraption — rivets, "
               "pipework, pressure gauges, vented steam",
         variants=_PLATING, height_ft=8, directional=True),
    Skin("chitin", "chitin",
         "the glossy black-green back of a giant beetle filling the whole "
         "frame, hard armour plating with fine parallel grooves, oily "
         "iridescent sheen",
         words="the vessel is GROWN rather than built — ridged chitin, veined "
               "and iridescent, no straight lines anywhere",
         variants=_CHITIN, height_ft=8, directional=True),
    # A vessel's DECK, which is most of what you see of it. The first pass gave
    # all three styles the same scrubbed oak and changed only the trim, and the
    # three came back indistinguishable — correctly, because they WERE the same
    # ship with different railings. What a hull is made of has to reach the
    # floor or it reaches nothing.
    Skin("plated-deck", "riveted-brass",
         "a flat expanse of riveted brass and iron deck plating, verdigris and "
         "oil stains, filling the whole frame",
         words="the deck is riveted metal plate, oil-stained, with grilles and "
               "pipe runs let into it",
         skirt_ft=7, skirt_inset=0.5),
    Skin("chitin-deck", "chitin",
         "the glossy black-green back of a giant beetle filling the whole "
         "frame, hard armour plating with fine parallel grooves, oily "
         "iridescent sheen",
         words="the deck is the creature's own back — ridged chitin underfoot, "
               "warm and faintly translucent",
         skirt_ft=7, skirt_inset=0.62),
    # The rail versions. Same substance, same silhouette as any other rail, and
    # emphatically the same THREE FEET — a ship's rail is half cover, and what
    # it is made of has no vote on how tall it is.
    Skin("plating-rail", "riveted-brass",
         "close-up of riveted brass and iron plating, verdigris and oil stains",
         words="the deck is edged with a pipework rail you can see the air "
               "through",
         variants=_RAILING, directional=True),
    Skin("chitin-rail", "chitin",
         "the glossy black-green back of a giant beetle filling the whole "
         "frame, hard armour plating with fine parallel grooves, oily "
         "iridescent sheen",
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
    # A sea ship and a skyship are not the same vessel, and sharing one deck
    # skin was most of why they came back looking identical. A caravel's deck
    # is wet, tarred and salt-bleached and its hull sits IN the water; a
    # skyship's is dry and hangs in air, and you see its underside.
    "ship":          {"b": "sea-deck", "w": "railing", "O": "mast",
                      "#": "hull"},
    "skyship":       {"b": "deck", "w": "railing", "O": "mast", "#": "hull"},
    "ruins":         {"O": "drowned-column"},
    "sky-islands":   {"R": "cliff"},
    # NB: no entry for dungeon-room, crypt, dungeon-complex or arena. They had
    # one — `masonry`, dressed grey building stone — and it bought nothing: it
    # is precisely what the model paints for a dungeon wall unprompted. A skin
    # earns its place by saying something the model would NOT guess, and a
    # decorative one is not free, because a board carrying skins is a board the
    # renderer has to reason about differently. `masonry` survives for the
    # watchtower, where it is doing real work against a camp full of canvas.
}

#: A skyship is the one board with a genuine style CHOICE rather than a
#: material: the same deck can be a brass-and-steam contraption or a grown
#: organic hull, and both are right. The generator picks one and records it, so
#: a table's ship stays the ship it was.
SKYSHIP_STYLES: dict[str, dict[str, str]] = {
    "timber":    {},
    "steampunk": {"#": "plating", "w": "plating-rail", "b": "plated-deck",
                  "O": "mast"},
    "organic":   {"#": "chitin", "w": "chitin-rail", "b": "chitin-deck",
                  "O": "mast"},
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


def is_smooth(name: str) -> bool:
    sk = SKINS.get(name or "")
    return bool(sk and sk.smooth)


def skirt_of(name: str) -> tuple[float, float]:
    """``(depth in feet, bottom inset)`` for a floor that carries its own side.

    ``(0, 0)`` means this square has no side of its own and falls back to the
    board-wide rule, which only draws one against a HOLE.
    """
    sk = SKINS.get(name or "")
    if sk is None or not sk.skirt_ft:
        return (0.0, 0.0)
    return (sk.skirt_ft, sk.skirt_inset)


def occludes_floor(name: str) -> bool:
    """Would this skin's shape stand in the way on a square you can walk on?

    The guard for a bug that already happened: a watchtower's whole wall ring
    was skinned in one pass, including its OPEN DOORWAY, so a square the rules
    let you walk through was drawn as a solid nine-foot merloned block. A
    picture contradicting the grid is the one thing the board must never do,
    and it is worse in this direction than in any other — the way in simply is
    not there.

    Judged by footprint at standing height: anything reaching the ground and
    covering most of the square is in the way. A lintel over a passage starts
    high and is fine; a canopy is fine; a wall is not.
    """
    sk = SKINS.get(name or "")
    if sk is None or not sk.variants:
        return False
    for parts in sk.variants:
        covered = sum((x1 - x0) * (z1 - z0)
                      for x0, x1, z0, z1, y0, _y1 in parts if y0 <= 0.01)
        if covered > 0.40:
            return True
    return False


#: Words in a DM's own biome text -> what things are BUILT of there.
#:
#: A watchtower on a forest crossing should be a timber stockade and one on a
#: mountain road drystone, and the DM has already said which without being
#: asked: the biome string they typed is sitting on the board row. Same
#: mechanism as `board_look` mapping free text onto a closed set, and the same
#: reason — the alternative is one material everywhere.
_BUILT_OF: tuple[tuple[tuple[str, ...], str], ...] = (
    (("forest", "wood", "jungle", "grove", "thicket", "timber", "pine",
      "frontier", "wild"), "timber"),
    (("stone", "mountain", "granite", "keep", "castle", "city", "town",
      "quarry", "cliff", "snow", "alpine"), "stone"),
)


def building_material(biome: str = "", default: str = "stone") -> str:
    """"stone" or "timber" — what a structure on this board is made of."""
    b = (biome or "").strip().lower()
    for words, mat in _BUILT_OF:
        if any(w in b for w in words):
            return mat
    return default


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
