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

import random
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from .terrain import Grid, tile

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
        if self.fills:
            return self.fills[cy][cx] not in " ."
        return tile(self.code_at(cx, cy)).move_cost_ft is None

    def squares(self) -> Iterable[tuple[int, int]]:
        for cy in range(self.depth):
            for cx in range(self.width):
                yield cx, cy


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
#: a summit worth standing on, which is wider than the default combat board —
#: and ``triggers.board_size_for`` grows a board for the FIGHT, not for the
#: scenery, so a landmark that only fits on a board sized for it is a landmark
#: that mostly does not appear.
_PYRAMID_TILES, _PYRAMID_ELEV = _stepped(9, tiers=2, rise_ft=10)
_PLINTH_TILES, _PLINTH_ELEV = _stepped(5, tiers=1, rise_ft=10)


def _flat(w: int, d: int, code: str) -> tuple[str, ...]:
    return tuple(code * w for _ in range(d))


def _canopy(size: int, trunk: str = "T",
            ground: str = ".") -> tuple[tuple[str, ...], dict[str, int]]:
    """A single tile of rules under a mesh many squares across.

    The jungle giant is the case: scaled to the sixty feet its entry claims, a
    palm's crown is nine squares wide, and every one of those squares is open
    ground a creature walks through — the canopy is far over their head, so it
    screens nothing and blocks nothing. Only the trunk is a tile.

    Written as a function because the alternative is eighty hand-typed
    elevation entries, and the import guard that demands one per passable
    square is exactly the guard a hand-typed block gets wrong.
    """
    mid = size // 2
    rows = tuple("".join(trunk if (x == mid and y == mid) else ground
                         for x in range(size)) for y in range(size))
    elev = {f"{x},{y}": 0 for y in range(size) for x in range(size)
            if not (x == mid and y == mid)}
    return rows, elev


_CANOPY_TILES, _CANOPY_ELEV = _canopy(9)


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
        on=("g", "\"", ".", "s"),
    ),
    SetPiece(
        "great-statue", "colossal guardian",
        # The honest gap. Generic CC0 kits carry columns, arches and rubble in
        # quantity and carry no named landmark creature at all; a search for a
        # free sphinx returns marketplaces whose "free" models may not be
        # redistributed, which is the one thing this repository needs.
        Source("quat-ruins", ("statue", "colossus", "guardian"), stand_in=True),
        ("OOO", "OOO"), height_ft=22.0, body="colossus",
        words="a colossal seated guardian carved from one block of stone",
        on=("g", "\"", ".", "s"),
    ),
    SetPiece(
        "ruined-arch", "ruined arch",
        Source("quat-ruins", ("arch", "gate", "archway")),
        ("O.O",), height_ft=18.0, body="arch",
        elevation={"1,0": 0}, fills=("X X",),
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
        Source("quat-nature", ("rock_tall", "monolith", "stone_tall", "rock")),
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
        ("RRR", "RRR", "RR."), height_ft=14.0, body="boulders",
        elevation={"2,2": 0},
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
        "village-well", "well",
        Source("quat-village", ("well",)),
        ("O",), height_ft=6.0,
        words="a round stone well with a shingled roof over it",
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
        ("##", "##", "##", "##", "#."), height_ft=20.0, body="wreck",
        elevation={"1,4": 0},
        words="the broken-backed hull of a wrecked ship",
        on=("s", "~", "g", "."),
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
        p = CATALOGUE[self.slug]
        return {
            "slug": self.slug, "name": p.name,
            "x": self.x, "y": self.y, "yaw": self.yaw,
            "w": p.width, "d": p.depth,
            "height_ft": p.height_ft, "up": p.up, "yaw_fix": p.yaw_fix,
            # No mesh means the board's own geometry draws it from the tiles
            # this piece stamped, which is what it has always done.
            "mesh": (f"/assets/setpieces/{self.slug}.obj"
                     if p.source is not None else None),
        }


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
         margin: int = 1) -> bool:
    """Room for this landmark here, on ground it may stand on?

    The margin is the same precaution :func:`vtt.structures.shelter` takes and
    for the same reason: a landmark jammed against a wall seals a pocket, and
    the generator's connectivity net then carves a corridor through it.
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
            if inside:
                if p.on and code not in p.on:
                    return False
                if tile(code).move_cost_ft is None:
                    return False          # already something standing here
            elif tile(code).move_cost_ft is None and code != " ":
                return False


    return True


def place(g: Grid, p: SetPiece, x0: int, y0: int, yaw: int = 0) -> Placed:
    """Stamp the piece onto the grid. The only place a set piece touches rules.

    Everything mechanical happens in these few lines — codes onto the grid,
    feet onto the elevation map — and everything after it is drawing. That
    separation is what lets the mesh come from a stranger's zip file.
    """
    tiles, elev, _fills = _turned(p, yaw)
    out = Placed(slug=p.slug, x=x0, y=y0, yaw=yaw % 360)
    for cy, row in enumerate(tiles):
        for cx, code in enumerate(row):
            x, y = x0 + cx, y0 + cy
            if not g.in_bounds(x, y):
                continue
            g.set(x, y, code)
            out.occupied.append((x, y))
            out.skins[f"{x},{y}"] = f"setpiece:{p.slug}"
            ft = elev.get(f"{cx},{cy}")
            if ft is not None:
                out.elevation[f"{x},{y}"] = int(ft)
    return out


def setpieces_for(g: Grid, slugs: Sequence[str], *, seed: int = 0,
                  tries: int = 40) -> list[Placed]:
    """Place the named landmarks on this board, deterministically.

    Derived from (layout, seed) and never stored — the :mod:`vtt.decor`
    arrangement, for the same reason: a board's landmarks are then a pure
    function of the board, so they survive a regeneration and cost no column.
    Which landmarks a board WANTS is the caller's business; a DM naming a
    ruined temple is fiction, and choosing where it goes is not.
    """
    rng = random.Random((seed * 2654435761) & 0xFFFFFFFF)
    out: list[Placed] = []
    taken: set[tuple[int, int]] = set()
    for slug in slugs:
        p = CATALOGUE.get(slug)
        if p is None:
            continue
        for _ in range(tries):
            yaw = rng.choice(p.turns)
            tiles, _e, _f = _turned(p, yaw)
            w, d = len(tiles[0]), len(tiles)
            x0 = rng.randrange(0, max(1, g.width - w + 1))
            y0 = rng.randrange(0, max(1, g.height - d + 1))
            cells = {(x0 + cx, y0 + cy) for cy in range(d) for cx in range(w)}
            if cells & taken or not fits(g, p, x0, y0, yaw):
                continue
            out.append(place(g, p, x0, y0, yaw))
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
