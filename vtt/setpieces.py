"""Landmarks too big and too particular to derive from a square.

Everything the board draws so far is DERIVED: a shape is a function of (tile
code, skin, x, z), and :mod:`vtt.hull` traces a vessel's outline from the
squares it occupies. That works because a wall, a cliff and a hull are all
things a rule can describe. It stops working at a colossal seated guardian with
a human face, because no hash produces one — which is a capability limit, not
an efficiency one, and is the whole reason this module exists.

A set piece is a MESH somebody else modelled, given a place in the rules. The
mesh contributes volume and silhouette; it contributes no mechanics whatever.
What decides mechanics is :attr:`SetPiece.tiles` — the codes the piece STAMPS
onto the grid — so cover, movement, sight and breakability read the board
exactly as they always have, and a creature stands where the tile code says it
stands. That is the same line :mod:`vtt.skins` draws one level down, and the
same line :mod:`vtt.decor` draws from the other side.

**Why an outside mesh is unusually cheap here.** Once the painted layer lands
the geometry switches to ``colorWrite: false`` and becomes a pure depth
occluder — the player is looking at a diffusion render conditioned on that
depth. So a set piece supplies a SHAPE for the painter to work from, not a
look, and a blocky CC0 model of roughly the right silhouette comes back painted
in the board's own hand. Style mismatch, which is normally the reason not to
use somebody else's art, costs very little on this pipeline.

Three rules, all enforced at import by :func:`_check_catalogue`:

* **A square the mesh FILLS must stamp an impassable code.** A picture may
  never close a square the rules leave open — :func:`vtt.skins.occludes_floor`
  is the same guard, and it was written after a watchtower's open doorway came
  back as solid masonry. Where a piece is walked ON rather than walked INTO,
  the square stamps a passable code and declares an ``elevation``: the mesh is
  then below the walking surface, which is what a terrace is.
* **A set piece may not stamp a code whose height the rules quote.** A crate
  screens four feet and a low wall three, and those are drawn exactly (see the
  jitter rule). One mesh scaled to one height cannot honour two such answers
  across its footprint, so anything crate-shaped is a TILE and not a set piece
  — the identical line :mod:`vtt.decor` draws from below.
* **A piece that names a pack names a licence with it.** The register is
  :data:`PACKS`, and ``scripts/setpiece_assets.py --attribution`` writes the
  ATTRIBUTION.md beside the meshes from it, so the file and the code cannot
  drift. The fonts already work this way because the OFL requires it.

**Not every set piece has a mesh, and the pyramid is why.** A stepped pyramid
is rock faces and floors at a stated height — geometry the board has drawn
since elevation went in — so a model of one would be a second, rival answer to
a shape the rules already fix, and the two would disagree the moment somebody
asked how far down it is. ``source=None`` means the piece is drawn entirely
from the codes it stamps. The mesh is reserved for what a hash genuinely
cannot produce, which turns out to be a shorter list than it first looks.

**Enterable buildings are not set pieces.** A tent, a cabin or a watchtower is
composed from tiles by :mod:`vtt.structures` and stays there: those need a real
interior, a real doorway and sometimes a real storey, all of which are rules.
A set piece is a solid landmark or a terraced mass — something you fight
AROUND, or ON.

**The file half of an entry is a search key, not a path.** A pack's model names
are its own business and change between releases, so :attr:`Source.match`
carries candidate name fragments, best first, and the audit script resolves
them against the extracted pack and reports what it found. The RULES half of
every entry — footprint, tiles, height — is authored and exact.
"""
from __future__ import annotations

import hashlib
import math
import random
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Sequence

from . import skins as _skins
from .terrain import DECOR_CODES, FLOOR, Grid, tile

#: What a landmark the DM ASKED for may sweep off the ground it stands on.
#:
#: Exactly ``terrain.DECOR_CODES`` — "codes a generator may scatter as
#: decoration without changing connectivity" — which is the same guarantee this
#: needs from the other end: clearing one can never open a way through
#: anything, because nothing was ever closed by one. A wall, a rock face or
#: water is not on the list and never will be; a landmark that would need to
#: move a wall is a landmark that does not fit.
CLEARABLE = frozenset(DECOR_CODES)

# --------------------------------------------------------------------------
# Where the meshes live
# --------------------------------------------------------------------------

#: Extracted third-party packs, gitignored. Whole packs are large and are not
#: ours to redistribute in bulk; this is the same workspace arrangement as
#: ``owned_books/``.
PACK_WORKSPACE = "assets_src"

#: The meshes actually used, copied out of the workspace and COMMITTED. Served
#: to the browser by vite from ``/assets/setpieces/`` and read from disk by the
#: depth rasterizer, so both sides look at one file — the invariant that keeps
#: the painting aligned with what the player sees.
MESH_ROOT = "activity-ui/public/assets/setpieces"

#: Meshes this installation MADE, from a picture of a thing the DM invented.
#: Deliberately a second root rather than more files in the first: the packs'
#: meshes are committed and carry somebody else's licence, and these are
#: derived, unshippable and re-derivable — the same line the project draws
#: between a rendered species portrait and the descriptors that produced it.
#: Gitignored, and served by the BACKEND (vite only serves what is in
#: ``public/``), which is why the two roots produce different URLs.
GENERATED_ROOT = "generated/setpieces"

#: A footprint square the piece RESERVES but does not change.
#:
#: Not a tile code — the grid never holds one. It exists because height became
#: authoritative and footprints grew to suit: a sixty-foot tree reserves nine
#: squares by nine, and eighty of those are ground its canopy merely hangs
#: over. Stamping them with a code of its own repaved a meadow into flagstones
#: wherever a tree stood, which is the picture contradicting the grid in the
#: one direction nobody would think to check — the terrain was RIGHT before the
#: landmark arrived.
#:
#: A reserved square is still checked by :func:`fits` (it must be clear ground
#: the piece may stand on) and still kept clear of scatter. It simply keeps
#: whatever it already was, along with its own elevation.
KEEP = "-"

#: Preferred mesh format, in order. OBJ first for a reason that is not taste:
#: :mod:`vtt.isocam` has to rasterize the same mesh the browser draws, and OBJ
#: parses in a few dozen lines of Python with no new dependency, where glTF
#: would mean adding one server-side. Every pack in :data:`PACKS` offers OBJ.
FORMATS = ("obj", "glb", "gltf", "fbx")


# --------------------------------------------------------------------------
# The licence register
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Pack:
    """One third-party source, with the terms it comes under.

    ``models`` and ``formats`` are as published by the author and were checked
    against the pack's own page; they are here so the audit can say when a
    download looks wrong, and so ATTRIBUTION.md is generated rather than typed.
    """

    slug: str
    name: str
    author: str
    url: str
    #: SPDX-style identifier. Only two are allowed — see :func:`_check_packs`.
    license: str
    models: int
    formats: tuple[str, ...]

    @property
    def attribution_required(self) -> bool:
        """CC0 waives it; CC-BY does not, and the file has to carry it."""
        return not self.license.startswith("CC0")


#: Licences a set piece may be built on.
#:
#: The operative question is REDISTRIBUTION, not use: this repository is
#: public, so a committed mesh is a mesh we are handing on. CC0 and CC-BY both
#: permit that. "Free for personal use", which is what most of a search for a
#: free model returns, does not — and is why the obvious big marketplaces are
#: absent from the register below however many results they have.
ALLOWED_LICENSES = ("CC0-1.0", "CC-BY-3.0", "CC-BY-4.0")


PACKS: dict[str, Pack] = {p.slug: p for p in (
    # ---- Kenney. Uniformly CC0, uniformly low-poly, one texture atlas per
    # kit, and the low poly count is a feature on the Discord mobile webview.
    Pack("kenney-nature", "Nature Kit", "Kenney",
         "https://kenney.nl/assets/nature-kit", "CC0-1.0", 330,
         ("obj", "fbx", "glb")),
    Pack("kenney-castle", "Castle Kit", "Kenney",
         "https://kenney.nl/assets/castle-kit", "CC0-1.0", 75,
         ("obj", "fbx", "glb")),
    Pack("kenney-graveyard", "Graveyard Kit", "Kenney",
         "https://kenney.nl/assets/graveyard-kit", "CC0-1.0", 90,
         ("obj", "fbx", "glb")),
    Pack("kenney-fantasy-town", "Fantasy Town Kit", "Kenney",
         "https://kenney.nl/assets/fantasy-town-kit", "CC0-1.0", 160,
         ("obj", "fbx", "glb")),
    Pack("kenney-dungeon", "Modular Dungeon Kit", "Kenney",
         "https://kenney.nl/assets/modular-dungeon-kit", "CC0-1.0", 40,
         ("obj", "fbx", "glb")),
    Pack("kenney-cave", "Modular Cave Kit", "Kenney",
         "https://kenney.nl/assets/modular-cave-kit", "CC0-1.0", 40,
         ("obj", "fbx", "glb")),
    Pack("kenney-pirate", "Pirate Kit", "Kenney",
         "https://kenney.nl/assets/pirate-kit", "CC0-1.0", 70,
         ("obj", "fbx", "glb")),
    Pack("kenney-watercraft", "Watercraft Kit", "Kenney",
         "https://kenney.nl/assets/watercraft-kit", "CC0-1.0", 45,
         ("obj", "fbx", "glb")),
    Pack("kenney-survival", "Survival Kit", "Kenney",
         "https://kenney.nl/assets/survival-kit", "CC0-1.0", 80,
         ("obj", "fbx", "glb")),
    Pack("kenney-mini-forest", "Mini Forest", "Kenney",
         "https://kenney.nl/assets/mini-forest", "CC0-1.0", 20,
         ("obj", "fbx", "glb")),

    # ---- Quaternius. Also uniformly CC0, chunkier and more "fantasy" in
    # silhouette than Kenney, and the ruins pack is the closest thing on the
    # open web to the temple-in-the-jungle vocabulary.
    Pack("quat-ruins", "Ultimate Modular Ruins Pack", "Quaternius",
         "https://quaternius.com/packs/ultimatemodularruins.html", "CC0-1.0",
         90, ("obj", "fbx", "blend")),
    Pack("quat-dungeons", "Modular Dungeons Pack", "Quaternius",
         "https://quaternius.com/packs/modulardungeon.html", "CC0-1.0", 48,
         ("obj", "fbx", "blend")),
    Pack("quat-nature", "Ultimate Nature Pack", "Quaternius",
         "https://quaternius.com/packs/ultimatenature.html", "CC0-1.0", 150,
         ("obj", "fbx", "blend")),
    Pack("quat-stylized-nature", "Stylized Nature MegaKit", "Quaternius",
         "https://quaternius.com/packs/stylizednaturemegakit.html", "CC0-1.0",
         116, ("obj", "fbx", "gltf", "blend")),
    Pack("quat-village", "Medieval Village MegaKit", "Quaternius",
         "https://quaternius.com/packs/medievalvillagemegakit.html", "CC0-1.0",
         304, ("obj", "fbx", "gltf", "blend")),
    Pack("quat-props", "Fantasy Props MegaKit", "Quaternius",
         "https://quaternius.com/packs/fantasypropsmegakit.html", "CC0-1.0",
         211, ("obj", "fbx", "gltf", "blend")),
    Pack("quat-ships", "Ships Pack", "Quaternius",
         "https://quaternius.com/packs/ships.html", "CC0-1.0", 6,
         ("obj", "fbx", "blend")),
    Pack("quat-pirate", "Pirate Kit", "Quaternius",
         "https://quaternius.com/packs/piratekit.html", "CC0-1.0", 71,
         ("obj", "fbx", "gltf", "blend")),
    Pack("quat-rts", "Ultimate Fantasy RTS", "Quaternius",
         "https://quaternius.com/packs/ultimatefantasyrts.html", "CC0-1.0",
         128, ("obj", "fbx", "gltf", "blend")),
    Pack("quat-monsters", "Ultimate Monsters", "Quaternius",
         "https://quaternius.com/packs/ultimatemonsters.html", "CC0-1.0", 50,
         ("obj", "fbx", "gltf", "blend")),
)}


# --------------------------------------------------------------------------
# A piece
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Source:
    """Which pack a mesh comes out of, and how to find it in there.

    ``match`` is a tuple of lowercase fragments of the model's file name, best
    first. Not a path: pack contents are renamed and re-released, and a
    hard-coded file name turns a pack update into a silently missing landmark.
    The audit resolves these and prints what it matched, which is also the only
    honest way to write a catalogue against archives nobody has opened yet.
    """

    pack: str
    match: tuple[str, ...]
    #: The pack has nothing that IS this thing, and what it does have is
    #: standing in. Recorded rather than glossed over, because it is the honest
    #: shape of the gap: open kits cover columns, arches, rubble, trees and
    #: rocks in quantity, and cover a named landmark creature — a sphinx, a
    #: particular god's idol — not at all. A generic statue at the right size
    #: is a fine depth proxy and a poor answer to "what is it".
    stand_in: bool = False


@dataclass(frozen=True)
class SetPiece:
    """A landmark: a mesh, its footprint, and the tiles it stamps.

    ``tiles`` is one string per row of the footprint, one character per square,
    in the same row-major order as :class:`vtt.terrain.Grid`. It is the whole
    mechanical content of a set piece — everything else here is drawing.

    ``fills`` marks the squares the mesh actually occupies at floor level. It
    defaults to "every impassable square", which is right for a statue and
    wrong for a terrace, and it is the field the first rule is checked against.

    ``height_ft`` is the piece's real height in the world, which is what makes
    scaling a downloaded mesh a stated fact rather than a fudge factor: the
    loader fits the model's bounding box to (footprint x square_ft) and reports
    how far the resulting height lands from this number.
    """

    slug: str
    name: str
    #: ``None`` means **no mesh**: the piece is drawn entirely by the board's
    #: own per-square geometry from the codes it stamps. A stepped pyramid is
    #: the case that forced this field to be optional — its faces are rock and
    #: its terraces are floor at a stated height, which the renderer has drawn
    #: since elevation went in, so a model of a pyramid would be a second,
    #: rival answer to a shape the rules already fix. The mesh is for things a
    #: hash genuinely cannot produce.
    source: Optional[Source]
    tiles: tuple[str, ...]
    height_ft: float
    #: What the painter is told about it. One clause, joined into the iso
    #: prompt exactly as :attr:`vtt.skins.Skin.words` is.
    words: str = ""
    #: Per-square floor height in feet, ``{"x,y": ft}`` keyed within the
    #: footprint. Any passable square of a piece must appear here — a walkable
    #: square with no elevation is a creature standing inside the model.
    elevation: dict[str, int] = field(default_factory=dict)
    #: Squares the mesh fills at floor level. Empty = derive from ``tiles``.
    fills: tuple[str, ...] = ()
    #: Squares of one body draw no side between them — the ``skins`` rule, so a
    #: multi-square landmark is one mass rather than a row of blocks.
    body: str = ""
    #: Quarter-turns the piece may take when placed. A statue faces a way;
    #: a heap of rubble does not care.
    turns: tuple[int, ...] = (0, 90, 180, 270)
    #: How the source mesh is authored. Packs disagree, and a wrong guess is a
    #: landmark lying on its side.
    up: str = "y"
    #: Degrees to spin the source mesh so its own front faces +z, applied
    #: before any placement turn.
    yaw_fix: int = 0
    #: Ground the piece may stand on, as tile codes. Empty = anywhere passable.
    on: tuple[str, ...] = ()

    # ----- derived -----

    @property
    def width(self) -> int:
        return len(self.tiles[0]) if self.tiles else 0

    @property
    def depth(self) -> int:
        return len(self.tiles)

    def code_at(self, cx: int, cy: int) -> str:
        return self.tiles[cy][cx]

    def filled(self, cx: int, cy: int) -> bool:
        """Does the mesh close this square at floor level?"""
        if self.code_at(cx, cy) == KEEP:
            return False
        if self.fills:
            return self.fills[cy][cx] not in " ."
        return tile(self.code_at(cx, cy)).move_cost_ft is None

    def squares(self) -> Iterable[tuple[int, int]]:
        for cy in range(self.depth):
            for cx in range(self.width):
                yield cx, cy

    @property
    def stamped_code(self) -> str:
        """The code this piece most stands for, for anything colouring it flat.

        Its ORIGIN square is the wrong answer and was the first one tried: now
        that footprints are mostly reserved ground, a landmark's top-left
        corner is usually a square it never touched, so a tree came out painted
        grass-green and a fountain with it. What a piece is made of is the code
        it stamps most often.
        """
        counts: dict[str, int] = {}
        for cx, cy in self.squares():
            code = self.code_at(cx, cy)
            if code != KEEP:
                counts[code] = counts.get(code, 0) + 1
        if not counts:
            return "#"
        return max(sorted(counts), key=lambda c: counts[c])


# --------------------------------------------------------------------------
# Built shapes
#
# A pyramid's tiers are a pattern, not a picture, so they are computed. The
# terraces have to be REAL height the rules honour — a creature on the top step
# is thirty feet up for every distance, cover and falling check — which is
# exactly why the pyramid is elevation and tiles rather than a model dropped on
# flat ground. A decorative one would put the picture and the rules in
# disagreement at the moment somebody decides whether to jump.
# --------------------------------------------------------------------------

def _stepped(size: int, tiers: int, rise_ft: int,
             face: str = "R", top: str = ".",
             stair: str = "u") -> tuple[tuple[str, ...], dict[str, int]]:
    """A square stepped mass: alternating faces and terraces, one way up.

    Ring ``r`` of the square is a FACE when ``r`` is even and a TERRACE when it
    is odd, so every terrace has a wall of rock below it and a creature climbs
    rather than strolls. The stair is a single column cut through the north
    faces — without it the summit is unreachable except by flight, which is a
    fine set piece and a poor place to put a fight.
    """
    mid = size // 2
    rows: list[list[str]] = []
    elev: dict[str, int] = {}
    for y in range(size):
        row: list[str] = []
        for x in range(size):
            ring = min(x, y, size - 1 - x, size - 1 - y)
            step = min(ring // 2 + (ring % 2), tiers)
            if ring % 2 == 0 and ring // 2 < tiers:
                # A face. Impassable, and its own height is the mesh's.
                if x == mid and y < size - 1 - mid:
                    row.append(stair)
                    # Halfway up the face it cuts through, which is what a
                    # flight of steps is.
                    elev[f"{x},{y}"] = rise_ft * (ring // 2) + rise_ft // 2
                else:
                    row.append(face)
            else:
                row.append(top)
                elev[f"{x},{y}"] = rise_ft * step
        rows.append(row)
    return tuple("".join(r) for r in rows), elev


#: Two terraces of ten feet. A third would want a 13-square footprint to keep
#: a summit worth standing on, which is wider than the default combat board.
#:
#: That used to be the end of the argument — a board is sized for the FIGHT and
#: never for the scenery, so a landmark which only fits on a board sized for it
#: is one that mostly does not appear. It stopped being true when height became
#: authoritative and footprints grew to match: a jungle giant reserves nine
#: squares by nine, and a rule that rejects everything that large would reject
#: most of the catalogue. ``triggers.board_size_for`` now adds the scenery's
#: own squares to the ones the fight asked for. Two terraces stays the choice
#: here anyway, because a third buys a wider summit and no new rule.
_PYRAMID_TILES, _PYRAMID_ELEV = _stepped(9, tiers=2, rise_ft=10)
_PLINTH_TILES, _PLINTH_ELEV = _stepped(5, tiers=1, rise_ft=10)


def _flat(w: int, d: int, code: str) -> tuple[str, ...]:
    return tuple(code * w for _ in range(d))


def _island(size: int, inner: int, code: str,
            ground: str = KEEP) -> tuple[tuple[str, ...], dict[str, int]]:
    """A block of ``inner`` squares centred in a ``size`` mesh footprint.

    The shape a set piece takes whenever its MESH is wider than the part of it
    a creature cannot walk through, which — now that height is authoritative
    and the footprint gives way — is most of them. The jungle giant is the
    extreme: scaled to the sixty feet its entry claims, a palm's crown is nine
    squares across and every one of those squares is open ground, because the
    canopy is far over a creature's head and screens nothing. Only the trunk is
    a tile. A fountain is the same shape one step in: a basin you cannot cross,
    and paving round it that you can.

    Written as a function because the alternative is eighty hand-typed
    elevation entries, and the guard demanding one per passable square is
    exactly what a hand-typed block gets wrong.
    """
    lo = (size - inner) // 2
    hi = lo + inner
    solid = lambda x, y: lo <= x < hi and lo <= y < hi        # noqa: E731
    rows = tuple("".join(code if solid(x, y) else ground
                         for x in range(size)) for y in range(size))
    # A reserved square keeps its own elevation, so it declares none here. Only
    # a square this piece actually stamps a passable code onto needs one, which
    # is the guard's whole point.
    elev = {f"{x},{y}": 0 for y in range(size) for x in range(size)
            if not solid(x, y) and ground != KEEP}
    return rows, elev


_CANOPY_TILES, _CANOPY_ELEV = _island(9, 1, "T")
_FOUNTAIN_TILES, _FOUNTAIN_ELEV = _island(5, 3, "O")


# --------------------------------------------------------------------------
# The catalogue
#
# Small and NAMED on purpose. A general "prefab anything" facility would invite
# the model to author layouts, and the project's standing line is that the LLM
# decides fiction and the code decides mechanics — the DM says a ruined temple
# stands here, and the code decides its footprint, its tiles and where it goes.
# --------------------------------------------------------------------------

CATALOGUE: dict[str, SetPiece] = {p.slug: p for p in (

    # ---- the temple in the jungle -------------------------------------
    SetPiece(
        "step-pyramid", "step pyramid",
        None,          # tiles and elevation are the whole shape — see above
        _PYRAMID_TILES, height_ft=20.0, elevation=_PYRAMID_ELEV,
        body="pyramid", turns=(0, 90, 180, 270),
        words="a tiered stone pyramid, its terraces climbing to a flat summit",
        # Rubble is ground too, and leaving it out kept every one of these off
        # the RUINS boards — the archetype whose floor is eight hundred squares
        # of broken stone, and the one whose whole pool is temple furniture.
        on=("g", "\"", ".", "s", ","),
    ),
    SetPiece(
        "great-statue", "colossal guardian",
        # This asked for a seated guardian with a HUMAN face and was marked a
        # stand-in, because open kits carry columns, arches and rubble in
        # quantity and no named landmark creature at all — a search for a free
        # sphinx returns marketplaces whose "free" models may not be
        # redistributed, which is the one thing a public repository needs.
        #
        # The ruins pack has two statues and both are BEASTS. So, the fountain's
        # lesson again: the entry became the thing the mesh is. A colossal stag
        # carved in stone is a landmark in its own right rather than a
        # apology for a missing sphinx, and it is no longer a stand-in.
        Source("quat-ruins", ("statue_stag", "statue")),
        ("OOO", "OOO"), height_ft=22.0, body="colossus",
        words="a colossal stone stag, antlers broken, staring down the approach",
        on=("g", "\"", ".", "s", ","),
    ),
    SetPiece(
        "ruined-arch", "ruined arch",
        # Gothic FIRST: at eighteen feet it is three squares where the round
        # arch is four, so the declared footprint is the honest one for it.
        Source("quat-ruins", ("arch_gothic", "arch", "gate", "archway")),
        ("O-O",), height_ft=18.0, body="arch",
        fills=("X X",),
        words="a broken ceremonial arch, its span still standing on two piers",
        turns=(0, 90),
    ),
    SetPiece(
        "broken-pillar", "broken pillar",
        Source("quat-ruins", ("pillar_broken", "column_broken", "pillar", "column")),
        ("O",), height_ft=12.0,
        words="a snapped stone column, its top long gone",
    ),
    SetPiece(
        "temple-plinth", "stepped plinth",
        None,          # likewise: rock faces and floors at a stated height
        _PLINTH_TILES, height_ft=10.0, elevation=_PLINTH_ELEV, body="plinth",
        words="a low stepped platform of dressed stone",
    ),
    SetPiece(
        "ruined-wall", "ruined wall",
        Source("quat-ruins", ("wall_broken", "wall_damaged", "wall")),
        ("###",), height_ft=12.0, body="ruin", turns=(0, 90),
        words="a run of collapsed temple wall",
    ),

    # ---- the jungle around it ----------------------------------------
    SetPiece(
        "jungle-giant", "jungle giant",
        Source("kenney-nature", ("tree_palmDetailedTall", "tree_tall", "tree_default")),
        # Nine by nine of MESH and one square of rules: the canopy is far over
        # a creature's head, so it screens nothing and blocks nothing, and only
        # the trunk is a tile. A canopy that stamped tiles would be cover
        # nobody could reach.
        #
        # It was 3x3 and the audit caught it: a palm scaled to the sixty feet
        # this entry claims has a crown nine squares across, and a footprint
        # narrower than the mesh is a picture overhanging squares the board
        # never reserved for it.
        _CANOPY_TILES, height_ft=60.0, elevation=_CANOPY_ELEV,
        fills=tuple("    X    " if y == 4 else " " * 9 for y in range(9)),
        words="an enormous buttressed jungle tree, its canopy far overhead",
        on=("g", "\""),
    ),
    SetPiece(
        "standing-stone", "standing stone",
        # NOT the Ultimate Nature Pack, whose rocks are all boulders — the
        # narrowest needs two squares by two at eleven feet, and a monolith
        # standing on end is by definition tall and narrow. Kenney's obelisk
        # is under four feet across at that height.
        Source("kenney-nature", ("statue_obelisk", "monolith", "rock_tall")),
        ("O",), height_ft=11.0,
        words="a weathered monolith standing on end",
    ),
    SetPiece(
        "boulder-heap", "fallen boulders",
        # ``rock_largeA`` came first and is a PEBBLE — a foot and a half tall
        # in its own units, so scaled to the fourteen feet this entry claims it
        # covered nine squares by eleven. The cliff blocks are the pack's
        # actual masses, and one of them at 14 ft is three squares square.
        Source("kenney-nature", ("cliff_block_rock", "cliff_block", "rock_large")),
        ("RRR", "RRR", "RR-"), height_ft=14.0, body="boulders",
        words="a tumble of house-sized boulders",
        on=("g", "\"", ",", "s", "."),
    ),

    # ---- other boards this vocabulary already serves -------------------
    SetPiece(
        "mausoleum", "mausoleum",
        Source("kenney-graveyard", ("crypt", "tomb", "mausoleum", "grave_large")),
        ("###", "###", "###", "###", "###"), height_ft=14.0, body="crypt",
        words="a sealed stone mausoleum, its door long since barred",
        turns=(0, 90),
    ),
    SetPiece(
        "gatehouse-tower", "gate tower",
        # The Castle Kit is MODULAR — it ships a base, a mid and a top, and no
        # whole tower — so the old fragments could only ever match a segment.
        # They matched the crenellated cap, which is nine inches tall in its
        # own units and wanted twenty-seven squares to reach forty feet. A
        # single-mesh set piece needs a pack that sells the tower assembled.
        Source("kenney-pirate", ("tower-complete-large", "tower-complete",
                                 "tower-watch")),
        ("###", "###", "###"), height_ft=40.0, body="tower",
        words="a squat stone gate tower",
    ),
    SetPiece(
        "village-fountain", "village fountain",
        # This was a WELL, and no open kit has one at a well's proportions.
        # The Medieval Village MegaKit is 304 models of modular architecture —
        # walls, floors, doors, roofs — with no free-standing prop in it at
        # all, which is the Castle Kit's lesson twice over: a kit sold for
        # assembling buildings is :mod:`vtt.structures` territory. The Fantasy
        # Town Kit does have water, but a fountain is a WIDE SHALLOW BASIN, so
        # scaled to a well's six feet of height it came out forty-three feet
        # across. Rather than force a mesh to be a thing it is not, the entry
        # became the thing the mesh IS — a plaza fountain is a better landmark
        # than a well anyway, being something a fight happens around.
        Source("kenney-fantasy-town", ("fountain-round-detail", "fountain-round",
                                       "fountain")),
        _FOUNTAIN_TILES, height_ft=6.0, elevation=_FOUNTAIN_ELEV,
        body="fountain",
        words="a broad stone fountain, its basin brimming",
    ),
    # A market stall was written here and REMOVED by the rule above: a stall is
    # about as tall as the overturned table the tile table already quotes three
    # feet for, and a mesh scaled to one height cannot honour a quoted height
    # per square. Furniture-sized things are tiles. The guard caught it, which
    # is the only reason to write a guard.
    SetPiece(
        "shipwreck", "beached wreck",
        Source("kenney-pirate", ("ship_wreck", "shipWreck", "ship_dark")),
        # A hull is LONG and narrow, and the footprint was square-ish: two
        # squares abeam and five from stem to stern is what the mesh actually
        # measures at twenty feet.
        ("##", "##", "##", "##", "#-"), height_ft=20.0, body="wreck",
        words="the broken-backed hull of a wrecked ship",
        # Deep water included: an open-water board is deep water from edge to
        # edge, and a wreck that may only lie on the shallows is a wreck that
        # never appears on the one archetype named after the sea.
        on=("s", "~", "g", ".", "W"),
    ),
    SetPiece(
        "cave-pillar", "cave column",
        # NOT from the Modular Cave Kit, which turns out to be corridor and
        # room segments and carries no free-standing anything.
        #
        # ``statue_column`` over the nature kit's ``rock_tall`` spires despite
        # being the wrong fiction, because PROPORTION is the thing a mesh
        # actually contributes here and the rocks have the wrong one: a rock
        # tall enough to reach the ceiling is four squares wide, and a cave
        # column is by definition slender. What is carved on it is repainted —
        # ``words`` is what tells the painter this is dripstone.
        Source("kenney-nature", ("statue_column", "rock_tall", "stone_tall")),
        ("O",), height_ft=16.0,
        words="a joined stalactite and stalagmite, floor to ceiling",
    ),
)}


# --------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------

def _check_packs() -> None:
    bad = {p.slug: p.license for p in PACKS.values()
           if p.license not in ALLOWED_LICENSES}
    if bad:
        raise ValueError(
            f"set-piece packs must be redistributable: {bad}. This repository "
            f"is public, so a committed mesh is one we hand on; allowed: "
            f"{ALLOWED_LICENSES}.")


def _check_catalogue() -> None:
    for p in CATALOGUE.values():
        if not p.tiles or any(len(r) != p.width for r in p.tiles):
            raise ValueError(f"{p.slug}: tiles must be a rectangle")
        if p.fills and (len(p.fills) != p.depth
                        or any(len(r) != p.width for r in p.fills)):
            raise ValueError(f"{p.slug}: fills must match the tile footprint")
        if p.source is not None and p.source.pack not in PACKS:
            raise ValueError(
                f"{p.slug}: unknown pack {p.source.pack!r}. Every mesh has to "
                "name a licence — see PACKS.")
        if p.up not in ("y", "z"):
            raise ValueError(f"{p.slug}: up must be 'y' or 'z'")
        for cx, cy in p.squares():
            code = p.code_at(cx, cy)
            if code == KEEP:
                # Reserved, not stamped. It keeps its own terrain and its own
                # elevation, so none of the checks below have anything to say
                # about it — they are all about a code this piece IMPOSES.
                if p.fills and p.fills[cy][cx] not in " .":
                    raise ValueError(
                        f"{p.slug} at {cx},{cy}: a reserved square cannot also "
                        "be one the mesh fills — filling it would close a "
                        "square whose terrain the piece never set.")
                continue
            t = tile(code)
            walkable = t.move_cost_ft is not None
            if p.filled(cx, cy) and walkable:
                raise ValueError(
                    f"{p.slug} at {cx},{cy}: the mesh fills a square the rules "
                    f"leave open ({code!r} = {t.name}). A picture may not close "
                    "a square a creature can stand on — see skins.occludes_floor. "
                    "Either stamp an impassable code or give the square an "
                    "elevation and let the mesh sit under it.")
            if walkable and f"{cx},{cy}" not in p.elevation:
                raise ValueError(
                    f"{p.slug} at {cx},{cy}: a passable square of a set piece "
                    "must declare its elevation, even if that elevation is 0. "
                    "Left out, a creature stands at ground level inside the "
                    "model.")
            if t.cover_height_ft > 0:
                raise ValueError(
                    f"{p.slug} at {cx},{cy}: {code!r} ({t.name}) is a tile "
                    f"whose height the rules QUOTE ({t.cover_height_ft} ft), "
                    "and one mesh scaled to one height cannot honour a quoted "
                    "height per square. If it deserves to be cover, it is a "
                    "tile, not a set piece.")


_check_packs()
_check_catalogue()


# --------------------------------------------------------------------------
# What the DM's words name
#
# The channel this table opens is the whole reason the catalogue is worth
# having: the DM narrates a stepped ziggurat swallowed in vines, the code HAS a
# step pyramid, and without a way across the board comes back a plain patch of
# forest — a landmark the fiction has already promised the table and the
# picture then denies. It is the same division of labour as everywhere else,
# not a loosening of it: the DM says WHAT stands here, and the code still owns
# the footprint, the tiles it stamps, where it goes and whether it fits.
#
# Matched on WORD BOUNDARIES, unlike ``mapgen._KEYWORDS``, because this
# vocabulary is full of short nouns that live inside other words — "arch" is in
# "archer", and a board full of archers is exactly the board a DM is describing
# when a fight starts.
# --------------------------------------------------------------------------

#: Loose DM language -> catalogue slug, most specific phrasing first.
#:
#: Deliberately made of NOUN PHRASES rather than bare stems: a group whose key
#: word is "ruin" fires on "a ruined arch" as well, and the DM then gets two
#: landmarks for one thing they described.
_LANDMARK_WORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("step pyramid", "stepped pyramid", "ziggurat", "pyramid",
      "tiered temple"), "step-pyramid"),
    (("cave column", "cave pillar", "stalactite", "stalagmite", "dripstone",
      "flowstone"), "cave-pillar"),
    # "stone stag" is in here because it is what the MESH is, and the catalogue
    # entry says so in its own words — a DM describing what the board already
    # told them is standing there must key it.
    (("colossal statue", "colossus", "great statue", "giant statue",
      "stone statue", "guardian statue", "stone stag", "idol", "effigy",
      "statue", "stag"), "great-statue"),
    (("ruined arch", "broken arch", "ceremonial arch", "stone arch",
      "archway", "arch"), "ruined-arch"),
    (("broken pillar", "snapped column", "fallen pillar", "toppled pillar",
      "broken column", "pillar", "column"), "broken-pillar"),
    (("stepped plinth", "stepped platform", "plinth", "dais",
      "raised platform"), "temple-plinth"),
    (("ruined wall", "ruined walls", "collapsed wall", "broken wall",
      "crumbling wall", "temple wall"), "ruined-wall"),
    (("jungle giant", "great tree", "giant tree", "enormous tree",
      "ancient tree", "huge tree", "banyan", "kapok"), "jungle-giant"),
    (("standing stone", "standing stones", "monolith", "obelisk", "menhir"),
     "standing-stone"),
    (("boulder", "boulders", "fallen rocks", "rockfall", "rubble heap"),
     "boulder-heap"),
    (("mausoleum", "sepulchre", "sepulcher", "tomb house", "tomb"),
     "mausoleum"),
    (("gate tower", "gatehouse", "guard tower", "stone tower", "gate house"),
     "gatehouse-tower"),
    (("fountain",), "village-fountain"),
    (("shipwreck", "wrecked ship", "beached wreck", "broken hull", "wreck"),
     "shipwreck"),
)


def _check_landmark_words() -> None:
    unknown = {slug for _w, slug in _LANDMARK_WORDS if slug not in CATALOGUE}
    if unknown:
        raise ValueError(
            f"_LANDMARK_WORDS names landmarks that do not exist: {unknown}. "
            "A word that keys nothing is a DM asking for a landmark and "
            "silently getting none.")


_check_landmark_words()


def landmark_for(text: Optional[str], *, limit: int = 3) -> list[str]:
    """Catalogue slugs for a scrap of DM language. Never raises.

    Accepts a slug or a catalogue name outright, so a DM (or a caller) who
    knows the vocabulary can be exact; otherwise every phrase in the text that
    keys a landmark is returned, in the order they are written above.

    A word is only read once: a match CONSUMES the span it sat on, so "a ruined
    arch" is an arch rather than an arch plus a length of ruined wall. ``limit``
    is the last guard — a lavish sentence naming five landmarks would otherwise
    ask for more scenery than a board has room to fight on, and the budget in
    ``mapgen._place_setpieces`` would drop the tail silently anyway.
    """
    t = (text or "").strip().lower()
    if not t:
        return []
    if t in CATALOGUE:
        return [t]
    by_name = {p.name.lower(): p.slug for p in CATALOGUE.values()}
    if t in by_name:
        return [by_name[t]]

    taken: list[tuple[int, int]] = []

    def _free(a: int, b: int) -> bool:
        return not any(a < end and start < b for start, end in taken)

    out: list[str] = []
    for words, slug in _LANDMARK_WORDS:
        if slug in out:
            continue
        for w in words:
            m = re.search(rf"\b{re.escape(w)}s?\b", t)
            if m and _free(m.start(), m.end()):
                taken.append((m.start(), m.end()))
                out.append(slug)
                break
        if len(out) >= max(1, limit):
            break
    return out


#: Landmarks the DM invented, by slug. Ad-hoc pieces the catalogue does not
#: have — see :func:`named_feature`. Process-local and deliberately not
#: persisted: once a piece is PLACED the board's own row carries its name, its
#: words and its square, so this only has to live long enough to lay it down.
_ADHOC: dict[str, "SetPiece"] = {}


def piece(slug: str, name: str = "") -> Optional["SetPiece"]:
    """A landmark by slug — from the catalogue, or one the DM described.

    ``name`` rebuilds an invented piece this process has never seen. The
    register is in memory and a board outlives the process that drew it, so a
    row read back tomorrow hands its stored name here and gets the same piece:
    :func:`named_feature` is deterministic, so the phrase IS the identity.
    """
    got = CATALOGUE.get(slug) or _ADHOC.get(slug)
    if got is None and name:
        made = named_feature(name)
        if made is not None and made.slug == slug:
            return made
    return got


#: How tall a described feature stands, in feet, and how many squares it takes.
#:
#: TWO squares across, and the reason is the word LANDMARK. The prompt asks the
#: DM for "something big enough that the fight happens around it", and one
#: square of a twenty-four by eighteen board is a four-hundredth of the frame —
#: rendered, a one-square feature is a smudge you would have to be told about.
#: Ten feet of gilded sow on a plinth is a thing a room is named for; five feet
#: of it is furniture, and furniture is what ``decor`` and the tile codes are
#: already for.
FEATURE_HEIGHT_FT = 9.0
FEATURE_SQUARES = 2

#: Words that make a phrase a description of the ROOM rather than of a thing
#: standing in it. "a smoky taproom" is not a feature; "a gilded sow" is.
_NOT_A_FEATURE = {
    "room", "chamber", "hall", "cave", "cavern", "street", "road", "clearing",
    "forest", "wood", "woods", "swamp", "marsh", "field", "meadow", "deck",
    "ship", "sewer", "tunnel", "mine", "crypt", "tomb", "dungeon", "tavern",
    "taproom", "inn", "camp", "ruins", "bridge", "pass", "reef", "sky",
    "island", "islands", "arena", "pit", "yard", "square", "market",
    "chapel", "church", "shrine", "temple", "keep", "castle", "fort", "barn",
    "mill", "cellar", "vault", "den", "lair", "nest", "courtyard", "garden",
    "grove", "shore", "beach", "path", "track", "stair", "stairs", "landing",
    "gallery", "kitchen", "store", "storeroom", "corridor", "passage", "bar",
    "cabin", "hold", "galley", "quarters", "warren", "burrow", "grotto",
}


def describes_its_own(text: Optional[str], matched: Sequence[str]) -> bool:
    """Is the DM describing a thing of their OWN rather than naming a catalogue one?

    The catalogue matches on word boundaries, so any phrase containing "statue"
    resolves to `great-statue` — which is one specific colossal seated guardian
    with a human face. Handed "a gilded sow, a life-size statue of a pig in gold
    leaf", that is emphatically the wrong object, and the DM has said so in nine
    other words.

    So the test is COVERAGE: a catalogue name is one or two words, and if the
    matched names account for most of what was written then the DM named a
    catalogue piece. If the phrase is mostly words the catalogue never claimed,
    they described something else and the description is the point.

    Nothing matched at all is the easy case and also true here.
    """
    words = [w for w in re.split(r"[^a-z]+", (text or "").lower()) if w
             and w not in _FILLER]
    if not words:
        return False
    covered = 0
    for slug in matched or ():
        pc = piece(slug)
        if pc is None:
            continue
        covered += len([w for w in re.split(r"[^a-z]+", pc.name.lower())
                        if w and w not in _FILLER])
    return covered < len(words) / 2


#: Words that carry no naming weight, so a phrase is judged on its nouns.
_FILLER = {"a", "an", "the", "of", "in", "on", "at", "with", "and", "its",
           "his", "her", "their", "it", "is", "that", "this", "some", "one"}


def named_feature(text: Optional[str]) -> Optional["SetPiece"]:
    """A one-square landmark the DM described and the catalogue does not have.

    The catalogue exists so a model cannot ask for a mesh nobody shipped. That
    guarantee is about MESHES, and it says nothing about a thing the board can
    already draw: one square of worked stone with a name on it. So a phrase that
    matches no catalogue entry becomes a piece with no mesh — the same
    ``source=None`` a stepped pyramid uses — stamping ``A``, which is already
    the tile for a worked object standing on a floor that screens four feet and
    can be broken.

    This exists because of an accident worth keeping. A board whose only prompt
    was its own name, The Gilded Sow, came back with golden pigs standing in it:
    the model draws a described thing readily, and the board had no way to MEAN
    one. Now the DM can say what stands in the room and the code decides where,
    how big, and what it does to a fight — the same division of labour every
    other landmark keeps.

    Returns None for anything that reads as a description of the ROOM rather
    than of a thing in it, because ``landmark=`` is also fed loose place text
    and "a smoky taproom" must not become a statue of one.
    """
    phrase = " ".join((text or "").strip().split())
    if not (3 <= len(phrase) <= 80):
        return None
    words = [w for w in re.split(r"[^a-z]+", phrase.lower()) if w]
    if not words or set(words) & _NOT_A_FEATURE:
        return None
    slug = "feature-" + hashlib.sha1(phrase.lower().encode()).hexdigest()[:10]
    got = _ADHOC.get(slug)
    if got is not None:
        return got
    made = SetPiece(
        slug=slug, name=phrase, source=None,
        tiles=("A" * FEATURE_SQUARES,) * FEATURE_SQUARES,
        height_ft=FEATURE_HEIGHT_FT,
        words=f"{phrase} stands in the room, a single object on its own square",
        turns=(0, 90, 180, 270),
    )
    _ADHOC[slug] = made
    return made


def landmark_vocabulary() -> list[tuple[str, str]]:
    """``(slug, name)`` for every landmark the board can stand.

    For the DM prompt. A model cannot ask for a ziggurat it was never told
    exists, and listing the catalogue is cheaper than teaching it the whole
    keyword table — the resolver above is forgiving about the wording.
    """
    return [(p.slug, p.name) for p in CATALOGUE.values()]


# --------------------------------------------------------------------------
# Fitting the mesh to the squares
#
# The scale is computed HERE, on the server, and shipped in ``state()``. That
# is the :mod:`vtt.hull` argument arriving from a third direction: the shape
# tables are data and so can be generated, the camera is arithmetic and so has
# to be gated, and this is a measurement of a FILE — so the only way to keep
# two languages from disagreeing about it is to have one of them do it. A
# browser that recomputed the scale from its own parse of the same OBJ would
# be a second answer to "how big is this", and the failure mode is the one the
# grid-is-truth rule exists to prevent: the painting conditioned on a landmark
# a different size from the one the player is looking at.
# --------------------------------------------------------------------------

def rotate_xz(x: float, z: float, deg: int) -> tuple[float, float]:
    """A point of a landmark's mesh after its quarter turn.

    The one definition of which way a set piece turns, and it is fixed by the
    TILES rather than chosen: :func:`_turned` sends a footprint square at
    ``(u, v)`` to ``(-v, u)`` at ninety degrees, so the mesh must do the same
    or the picture turns and the cover does not — a creature takes
    three-quarters cover from a face of the statue that is now behind it.

    Mirrored by ``setpieceRotate`` in ``activity-ui/src/lib/boardView.ts``,
    which has to NEGATE the angle to get here because three.js's ``rotation.y``
    is the other handedness. Gated by ``scripts/iso_alignment_check.py``.
    """
    a = math.radians(deg % 360)
    c, s = math.cos(a), math.sin(a)
    return (x * c - z * s, x * s + z * c)


def _obj_bounds(path: Path) -> Optional[tuple[tuple[float, float, float],
                                              tuple[float, float, float]]]:
    """The mesh's bounding box. Deliberately not a mesh loader — ``v`` lines
    and nothing else, which is why the catalogue prefers OBJ."""
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    seen = False
    try:
        with path.open("r", errors="ignore") as fh:
            for line in fh:
                if not line.startswith("v "):
                    continue
                parts = line.split()
                if len(parts) < 4:
                    continue
                try:
                    xyz = [float(parts[1]), float(parts[2]), float(parts[3])]
                except ValueError:
                    continue
                seen = True
                for i in range(3):
                    lo[i] = min(lo[i], xyz[i])
                    hi[i] = max(hi[i], xyz[i])
    except OSError:
        return None
    return (tuple(lo), tuple(hi)) if seen else None   # type: ignore[return-value]


def mesh_path(slug: str, root: Optional[Path] = None) -> Optional[Path]:
    """The mesh for this piece, or None if there isn't one on this machine.

    None is not an error: a catalogue entry whose pack nobody has unzipped is a
    landmark the board draws from its tiles alone, which is exactly what a
    piece with ``source=None`` does on purpose. Degrading to the geometry the
    board has always had beats a missing model leaving a hole in the room.

    Two roots, searched COLLECTED FIRST. A pack mesh is a modeller's answer to
    what the thing is and a generated one is a diffusion model's guess at it,
    so where both exist the modeller wins — and a catalogue entry can never be
    quietly displaced by something this machine invented under the same slug.
    """
    if root is not None:
        return _in_root(root, slug)
    here = Path(__file__).resolve().parents[1]
    return _in_root(here / MESH_ROOT, slug) or _in_root(generated_root(), slug)


def _in_root(base: Path, slug: str) -> Optional[Path]:
    for ext in FORMATS:
        p = base / f"{slug}.{ext}"
        if p.exists():
            return p
    return None


def generated_root() -> Path:
    """Where a mesh this installation made is kept."""
    return Path(__file__).resolve().parents[1] / GENERATED_ROOT


def is_generated(path: Optional[Path]) -> bool:
    """Did this file come out of the generator rather than out of a pack?

    Asked because the two are served over different URLs, and answered by
    where the file IS rather than by what the piece says about itself — a
    catalogue entry whose pack is missing may still have a generated stand-in,
    and the honest answer for that one is "generated".
    """
    if path is None:
        return False
    try:
        return path.resolve().parent == generated_root().resolve()
    except OSError:
        return False


def mesh_fit(slug: str, square_ft: int = 5) -> Optional[dict]:
    """How to put this piece's mesh on its squares, in BOARD units.

    Returns ``{"scale", "pivot"}`` where ``scale`` takes the mesh's own
    arbitrary units to squares, and ``pivot`` is the point (already scaled) to
    subtract so the model stands centred on its footprint with its base on the
    floor. A renderer applies them and needs to know nothing else:

        v -> (v * scale - pivot), then yaw, then + footprint centre

    The lookup is deliberately OUTSIDE the cache. An invented landmark is
    registered by :func:`named_feature` when its phrase is first seen, which in
    a fresh process happens AFTER something has already asked about the slug —
    and a cached ``None`` from that first miss would leave the landmark flat
    for the life of the run. Only the measurement is cached, keyed on what it
    actually depends on.
    """
    p = CATALOGUE.get(slug) or _ADHOC.get(slug)
    if p is None:
        return None
    path = mesh_path(slug)
    if path is None or path.suffix.lower() != ".obj":
        return None
    # A piece that declares no source draws itself from its tiles — UNLESS this
    # machine has actually made a mesh for it. That is the whole of the
    # invented-landmark path: the DM's gilded sow keeps stamping the square it
    # always stamped, and gains a shape once one exists. Judged on the FILE,
    # so a machine that has never rendered one behaves exactly as before.
    made_here = is_generated(path)
    if p.source is None and not made_here:
        return None
    # An AUTHORED height is fiction to protect; a DEFAULT one is not. See
    # _measure_fit.
    return _measure_fit(str(path), p.height_ft, p.up, int(square_ft or 5),
                        p.width if made_here and p.source is None else 0,
                        p.depth if made_here and p.source is None else 0)


@lru_cache(maxsize=64)
def _measure_fit(path_s: str, height_ft: float, up: str, square_ft: int,
                 max_w: int = 0, max_d: int = 0) -> Optional[dict]:
    """The measurement itself, cached on exactly what it depends on.

    ``max_w``/``max_d`` are a footprint the mesh may not overhang, in squares,
    and are passed ONLY for a landmark the DM invented. The catalogue's rule is
    the opposite and stays the opposite: there, height wins and the footprint
    gives way, because fitting width to the footprint made every tall thing a
    dwarf — a 60-ft jungle giant came out 21 ft — and a catalogued height is a
    stated fact about the fiction.

    An invented landmark has no stated fact to protect. Both its numbers are
    DEFAULTS (``FEATURE_HEIGHT_FT``, ``FEATURE_SQUARES``), nobody chose either
    for this thing, and the first real one measured 1.00 x 0.44 x 1.00 — a sow
    on a broad plinth — which at nine feet tall spills five feet onto every
    square around it. That is the picture contradicting the grid in the
    direction the ``KEEP`` rule exists to prevent, and being four feet shorter
    is the cheaper of the two prices.
    """
    bounds = _obj_bounds(Path(path_s))
    if bounds is None:
        return None
    (x0, y0, z0), (x1, y1, z1) = bounds
    # Which way is up is the producer's choice and a wrong guess is a landmark
    # lying on its side, so it is declared rather than sniffed.
    tall = (y1 - y0) if up == "y" else (z1 - z0)
    if tall <= 0:
        return None
    sq = float(square_ft or 5)
    # UNIFORM, always: scaling one axis alone distorts anything organic.
    scale = (height_ft / tall) / sq
    if max_w and max_d:
        wide = max(x1 - x0, 1e-9)
        deep = max(z1 - z0, 1e-9) if up == "y" else max(y1 - y0, 1e-9)
        scale = min(scale, float(max_w) / wide, float(max_d) / deep)
    return {
        "scale": scale,
        "pivot": [(x0 + x1) / 2.0 * scale,
                  (y0 if up == "y" else z0) * scale,
                  (z0 + z1) / 2.0 * scale],
    }


def forget_mesh(slug: str = "") -> None:
    """Drop the cached fit, because a mesh has just appeared or changed.

    :func:`mesh_fit` is cached on the reasoning that the collected meshes are
    committed and immutable within a run. A GENERATED one breaks that: it lands
    in the middle of a session, minutes after the board that wanted it was
    drawn, and a remembered ``None`` would leave that board flat until the
    process restarted. Cheap — the cache holds at most 64 measurements.
    """
    _measure_fit.cache_clear()


# --------------------------------------------------------------------------
# Placing one
# --------------------------------------------------------------------------

@dataclass
class Placed:
    """What a set piece put on the board, for a generator to record.

    Deliberately the same shape of answer as :class:`vtt.structures.Built`: a
    generator gets skins and elevation to fold into the map row, squares to
    keep its scatter off, and — the one thing structures never needed — a mesh
    instance for ``state()`` to ship to both renderers.
    """

    slug: str
    x: int
    y: int
    yaw: int
    skins: dict[str, str] = field(default_factory=dict)
    elevation: dict[str, int] = field(default_factory=dict)
    occupied: list[tuple[int, int]] = field(default_factory=list)

    def instance(self) -> dict:
        """The record both renderers read. Never a shape, always a reference.

        The mesh is loaded from one file by both sides, so unlike the board's
        prismatoid tables there is nothing here to keep in step by generation —
        which is the :mod:`vtt.hull` argument arriving at the same place from
        the other direction.
        """
        p = piece(self.slug)
        if p is None:
            return {}
        # No mesh means the board's own geometry draws it from the tiles this
        # piece stamped, which is what it has always done. That covers BOTH
        # a piece authored without one (the pyramid) and a piece whose pack
        # nobody has unzipped — the second must degrade the same way as the
        # first, or an uncollected mesh leaves a hole in the room.
        # mesh_fit answers for both kinds now — a pack mesh that was collected,
        # and one this machine generated for a landmark the DM invented — and
        # returns None for a piece that has neither, which is the case the
        # board has always drawn from its tiles alone.
        fit = mesh_fit(self.slug)
        path = mesh_path(self.slug) if fit is not None else None
        out = {
            "slug": self.slug, "name": p.name,
            "x": self.x, "y": self.y, "yaw": self.yaw,
            "w": p.width, "d": p.depth,
            "height_ft": p.height_ft, "up": p.up, "yaw_fix": p.yaw_fix,
            "words": p.words, "code": p.stamped_code,
            # Two roots, two URLs: vite serves the committed packs out of
            # ``public/``, and it has never heard of a file this machine made
            # five minutes ago — so a generated mesh comes through the backend.
            "mesh": (None if path is None else
                     (f"/vtt/setpiece/{self.slug}{path.suffix}" if is_generated(path)
                      else f"/assets/setpieces/{self.slug}{path.suffix}")),
        }
        if fit is not None:
            out.update(fit)
        return out


def _turned(p: SetPiece, yaw: int) -> tuple[tuple[str, ...], dict[str, int],
                                            tuple[str, ...]]:
    """The footprint rotated a quarter turn at a time, tiles and all.

    Rotating the mesh without rotating its tiles is the bug this exists to
    prevent: the picture turns and the cover does not, so a creature takes
    three-quarters cover from a face of the statue that is now behind it.
    """
    tiles, elev, fills = p.tiles, dict(p.elevation), p.fills or ()
    for _ in range((yaw // 90) % 4):
        w, d = len(tiles[0]), len(tiles)
        tiles = tuple("".join(tiles[d - 1 - y][x] for y in range(d))
                      for x in range(w))
        if fills:
            fills = tuple("".join(fills[d - 1 - y][x] for y in range(d))
                          for x in range(w))
        elev = {f"{d - 1 - int(k.split(',')[1])},{int(k.split(',')[0])}": v
                for k, v in elev.items()}
    return tiles, elev, fills


def fits(g: Grid, p: SetPiece, x0: int, y0: int, yaw: int = 0,
         margin: int = 1, mode: str = "walk", clear: bool = False) -> bool:
    """Room for this landmark here, on ground it may stand on?

    The margin is the same precaution :func:`vtt.structures.shelter` takes and
    for the same reason: a landmark jammed against a wall seals a pocket, and
    the generator's connectivity net then carves a corridor through it.

    "Clear" is judged in the board's own MEDIUM, exactly as ``_connect_regions``
    judges connectivity. Deep water is impassable to a walker and is the entire
    floor of an open-water board, so reading it as "something already standing
    here" refused every landmark on every sea board — a wreck could not lie in
    the sea.

    ``clear`` lets the piece count SCATTER as ground it may take — see
    :data:`CLEARABLE`. Off by default, because a landmark the board happened to
    have room for should not go rearranging the furniture; on for one the DM
    asked for by name, which otherwise fails on exactly the boards most worth
    having one. A nine-by-nine piece wants an eleven-by-eleven clearing, and a
    ruin scattered with broken pillars has none.
    """
    tiles, _elev, _fills = _turned(p, yaw)
    w, d = len(tiles[0]), len(tiles)
    if x0 < 0 or y0 < 0 or x0 + w > g.width or y0 + d > g.height:
        return False
    for y in range(y0 - margin, y0 + d + margin):
        for x in range(x0 - margin, x0 + w + margin):
            if not g.in_bounds(x, y):
                continue
            inside = x0 <= x < x0 + w and y0 <= y < y0 + d
            code = g.get(x, y)
            scatter = clear and code in CLEARABLE
            if inside:
                if p.on and code not in p.on and not scatter:
                    return False
                if not g.passable(x, y, mode=mode) and not scatter:
                    return False          # already something standing here
            elif not g.passable(x, y, mode=mode) and code != " " and not scatter:
                return False

    return True


def _ground_under(g: Grid, p: SetPiece, mode: str) -> str:
    """What a cleared square becomes: the commonest ground this piece stands on.

    Read off the board rather than named in the entry, because a piece stands on
    grass in a meadow and on flagstone in a ruin, and the entry has no way to
    know which board it landed on.
    """
    counts: dict[str, int] = {}
    for x, y in g.squares():
        code = g.get(x, y)
        if not g.passable(x, y, mode=mode):
            continue
        if p.on and code not in p.on:
            continue
        counts[code] = counts.get(code, 0) + 1
    if counts:
        return max(counts, key=lambda c: counts[c])
    return (p.on[0] if p.on else FLOOR)


def place(g: Grid, p: SetPiece, x0: int, y0: int, yaw: int = 0,
          clear: bool = False, mode: str = "walk") -> Placed:
    """Stamp the piece onto the grid. The only place a set piece touches rules.

    Everything mechanical happens in these few lines — codes onto the grid,
    feet onto the elevation map — and everything after it is drawing. That
    separation is what lets the mesh come from a stranger's zip file.

    ``clear`` sweeps scatter off the RESERVED squares. Only there, and only
    scatter: every other square is about to be overwritten by the piece's own
    code anyway, and a ziggurat whose plaza still has a broken pillar standing
    on it is a picture nobody would draw. It can never open a way through
    anything, since :data:`CLEARABLE` is the set of codes a generator scatters
    without touching connectivity.
    """
    tiles, elev, _fills = _turned(p, yaw)
    out = Placed(slug=p.slug, x=x0, y=y0, yaw=yaw % 360)
    ground = _ground_under(g, p, mode) if clear else ""
    for cy, row in enumerate(tiles):
        for cx, code in enumerate(row):
            x, y = x0 + cx, y0 + cy
            if not g.in_bounds(x, y):
                continue
            # Reserved squares are recorded as OCCUPIED — so nothing else is
            # placed under the landmark — and otherwise left exactly as they
            # were: same terrain, same elevation, and no set-piece skin, since
            # there is no per-square geometry here to suppress.
            if code == KEEP:
                if ground and g.get(x, y) in CLEARABLE:
                    g.set(x, y, ground)
                out.occupied.append((x, y))
                continue
            g.set(x, y, code)
            out.occupied.append((x, y))
            out.skins[f"{x},{y}"] = f"{_skins.SETPIECE_PREFIX}{p.slug}"
            ft = elev.get(f"{cx},{cy}")
            if ft is not None:
                out.elevation[f"{x},{y}"] = int(ft)
    return out


def _spots(g: Grid, p: SetPiece, rng: random.Random) -> Iterable[tuple[int, int, int]]:
    """Every square this piece could stand on, inner ground first.

    Two passes rather than one shuffle, because where a landmark goes is not a
    uniform question. A set piece is something a fight happens AROUND, and one
    shoved against the edge of the board is scenery you can only ever have
    behind you — the edge is also where it fits most easily, since ``fits``
    skips the margin ring where it runs off the board. So the inner two thirds
    are offered first and the rim is the fallback.
    """
    tiles, _e, _f = _turned(p, 0)
    inset_x = max(1, (g.width - len(tiles[0])) // 6)
    inset_y = max(1, (g.height - len(tiles)) // 6)
    inner: list[tuple[int, int, int]] = []
    rim: list[tuple[int, int, int]] = []
    for yaw in p.turns:
        t, _e2, _f2 = _turned(p, yaw)
        w, d = len(t[0]), len(t)
        for y in range(0, max(1, g.height - d + 1)):
            for x in range(0, max(1, g.width - w + 1)):
                bucket = (inner if (inset_x <= x and x + w <= g.width - inset_x
                                    and inset_y <= y and y + d <= g.height - inset_y)
                          else rim)
                bucket.append((x, y, yaw))
    rng.shuffle(inner)
    rng.shuffle(rim)
    return inner + rim


def setpieces_for(g: Grid, slugs: Sequence[str], *, seed: int = 0,
                  mode: str = "walk",
                  clear: Sequence[str] = ()) -> list[Placed]:
    """Place the named landmarks on this board, deterministically.

    Derived from (layout, seed) and never stored — the :mod:`vtt.decor`
    arrangement, for the same reason: a board's landmarks are then a pure
    function of the board, so they survive a regeneration and cost no column.
    Which landmarks a board WANTS is the caller's business; a DM naming a
    ruined temple is fiction, and choosing where it goes is not.

    Every candidate square is TRIED, in a seeded order. Forty random darts came
    first and measured badly for the reason a dart game is hard: a nine-by-nine
    piece needs an eleven-by-eleven clearing, a wooded board has few, and a
    miss is indistinguishable from a board that had no room at all. Two of
    three forests refused a pyramid the DM had already narrated; scanning
    finds one on nearly every board, and where it still finds none, that is now
    a real answer rather than bad luck.
    """
    rng = random.Random((seed * 2654435761) & 0xFFFFFFFF)
    out: list[Placed] = []
    taken: set[tuple[int, int]] = set()
    sweep = set(clear)
    for slug in slugs:
        p = piece(slug)
        if p is None:
            continue
        may_clear = slug in sweep
        for x0, y0, yaw in _spots(g, p, rng):
            tiles, _e, _f = _turned(p, yaw)
            w, d = len(tiles[0]), len(tiles)
            cells = {(x0 + cx, y0 + cy) for cy in range(d) for cx in range(w)}
            if cells & taken or not fits(g, p, x0, y0, yaw, mode=mode,
                                         clear=may_clear):
                continue
            out.append(place(g, p, x0, y0, yaw, clear=may_clear, mode=mode))
            taken |= cells
            break
    return out


# --------------------------------------------------------------------------
# Attribution
# --------------------------------------------------------------------------

def packs_in_use() -> list[Pack]:
    """Only the packs a catalogue entry actually draws on.

    ATTRIBUTION.md lists what is REDISTRIBUTED, not what was browsed. A pack
    nobody takes a mesh from is a bookmark.
    """
    used = {p.source.pack for p in CATALOGUE.values() if p.source}
    return [PACKS[s] for s in sorted(used)]


def attribution_markdown() -> str:
    """The register beside the meshes, generated so it cannot drift.

    Same discipline as the cultural typefaces' ATTRIBUTION.md, which exists
    because the OFL requires the notice to travel with the font. CC0 requires
    nothing; the file records it anyway, because "where did this come from and
    may we ship it" is a question somebody will ask about a binary blob long
    after everyone who added it has forgotten.
    """
    rows = ["| Pack | Author | Models | Licence | Source |",
            "|------|--------|--------|---------|--------|"]
    for p in packs_in_use():
        rows.append(f"| {p.name} | {p.author} | {p.models} | {p.license} | "
                    f"<{p.url}> |")
    by_pack: dict[str, list[SetPiece]] = {}
    for sp in CATALOGUE.values():
        if sp.source:
            by_pack.setdefault(sp.source.pack, []).append(sp)
    used = ["| Set piece | Squares | Height | Pack |",
            "|-----------|---------|--------|------|"]
    for slug in sorted(by_pack):
        for sp in sorted(by_pack[slug], key=lambda s: s.slug):
            mark = " *(nearest match — no model of this thing exists open)*" \
                if sp.source and sp.source.stand_in else ""
            used.append(f"| `{sp.slug}`{mark} | {sp.width}x{sp.depth} | "
                        f"{sp.height_ft:g} ft | {PACKS[slug].name} |")
    return _ATTRIBUTION_TEMPLATE.format(
        packs="\n".join(rows), pieces="\n".join(used))


_ATTRIBUTION_TEMPLATE = """# Board set pieces

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

{packs}

CC0 1.0 waives attribution entirely — <https://creativecommons.org/publicdomain/zero/1.0/>.
The register is kept regardless, so that a binary in the tree can always be
traced to where it came from and the terms it came under.

## What is used

{pieces}

## What a mesh is, and is not

A set piece contributes **volume and silhouette only**. It contributes no
mechanics: cover, movement, sight and breakability are read off the tile codes
the piece stamps onto the grid, exactly as they are everywhere else on the
board. Once the painted layer is present the geometry stops drawing colour
altogether and becomes a depth occluder for a diffusion render — which is also
why a mesh whose art style does not match the game costs very little here.
"""
