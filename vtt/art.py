"""
Battlemap art — the diffusion render that goes *under* the grid.

The layout is already decided by :mod:`vtt.mapgen`; this module only asks the
image model to paint it. That split is the whole point: the walls the engine
enforces are the walls the players see, because the picture is generated *from*
the tile grid, never the other way round.

    from vtt.art import render_battlemap
    art = render_battlemap(generated, biome="damp underdark", name="Sunken Shrine")
    art.image_id     # -> entity_image row, or None when ComfyUI is offline

Practical notes:

* **The art is a texture, not the truth.** A diffusion model can't be trusted to
  put a wall on the exact square we asked for, so the client stretches the image
  to the board rectangle and draws its own grid, walls-overlay and tokens on
  top. Mismatch is cosmetic; the rules always read the tile grid.
* **Aspect ratio matters.** The render canvas is sized to the board's ratio (in
  multiples of 64, at roughly the configured megapixel budget) so a 30x12
  corridor doesn't come back as a square.
* **Cached by layout signature.** The bucket key is a hash of archetype + seed
  + size + terrain, so re-entering the same room reuses the same picture, and a
  regenerated identical layout doesn't burn another render.
* **Offline is fine.** With no GPU up, ``image_id`` is ``None`` and the overlay
  falls back to drawing the tiles itself — the tactical layer never depends on
  the art being there.
"""
from __future__ import annotations

import hashlib
import math
import os
from dataclasses import dataclass
from typing import Optional

from . import skins
from .mapgen import GeneratedMap
from .terrain import Grid

# The style is deliberately close to the house look (see ImageryConfig.style_prompt)
# but flattened: battlemaps read best with even light and honest materials.
_MAP_STYLE = (
    "hand-painted fantasy battlemap, rich painterly texture, "
    "clear readable shapes, muted natural palette with warm accents, "
    "subtle parchment grain"
)


@dataclass
class BattlemapArt:
    image_id: Optional[int]
    prompt: str
    caption: str
    width: int = 0
    height: int = 0
    offline: bool = False
    reused: bool = False


def layout_signature(grid: Grid, archetype: str, seed: int) -> str:
    """A stable handle for *this exact layout* — the art cache key."""
    body = "\n".join(grid.to_rows())
    h = hashlib.sha256(f"{archetype}|{seed}|{grid.width}x{grid.height}|{body}"
                       .encode("utf-8")).hexdigest()[:16]
    return f"{archetype}-{h}"


def canvas_size(width_sq: int, height_sq: int, *, budget_px: int = 1_100_000,
                multiple: int = 64, max_side: int = 2048) -> tuple[int, int]:
    """Pick a render canvas matching the board's aspect at a pixel budget.

    Rounded to a multiple of 64 because latent-space models want it, and clamped
    so a very long corridor doesn't ask the GPU for a 4096-px strip.
    """
    width_sq = max(1, int(width_sq))
    height_sq = max(1, int(height_sq))
    ratio = width_sq / float(height_sq)
    h = math.sqrt(budget_px / ratio)
    w = h * ratio
    def snap(v: float) -> int:
        return max(multiple, min(max_side, int(round(v / multiple)) * multiple))
    return snap(w), snap(h)


def build_map_prompt(gen: GeneratedMap, *, name: str = "",
                     biome: Optional[str] = None,
                     lighting: Optional[str] = None,
                     extra: str = "",
                     conditions: str = "") -> tuple[str, str, str]:
    """Compose (subject, look, context) for the imagery prompt builder.

    ``subject`` is what the room *is*, ``look`` is the terrain the generator
    actually laid down (so the picture matches the tiles), and ``context`` is
    the biome/lighting bucket.
    """
    subject = (name or gen.description or gen.archetype).strip()
    terrain = gen.grid.describe()
    bits = [gen.description]
    if terrain:
        bits.append(terrain)
    scale = f"{gen.width} by {gen.height} squares of five feet"
    bits.append(scale)
    if extra:
        bits.append(extra)
    look = ", ".join(b for b in bits if b)

    light = (lighting or gen.lighting or "bright").lower()
    light_phrase = {"dark": "unlit, deep shadow, cold moonlight",
                    "dim": "low guttering light, long shadows",
                    "bright": "clear even daylight"}.get(light, "clear even daylight")
    # Conditions ride in the CONTEXT, which is also the art cache's bucket key.
    # That is the whole mechanism for "the same room, in snow": matching
    # conditions reuse the picture, changed ones earn a new one, and neither
    # needs a cache-busting decision anywhere else. Interiors report a stable
    # condition string, so a tavern isn't repainted every time the weather turns.
    context = ", ".join(p for p in ((biome or "").strip(), light_phrase,
                                    (conditions or "").strip()) if p)
    return subject, look, context


def render_battlemap(gen: GeneratedMap, *, store=None, name: str = "",
                     biome: Optional[str] = None, lighting: Optional[str] = None,
                     extra: str = "", conditions: str = "",
                     ref_slug: Optional[str] = None,
                     controlnet: Optional[str] = None,
                     controlnet_strength: float = 0.8,
                     force_new: bool = False,
                     store_width: int = 1280,
                     budget_px: int = 1_100_000) -> BattlemapArt:
    """Render (or reuse) the top-down art for a generated layout.

    Never raises: a missing/offline image backend just yields
    ``image_id=None, offline=True`` and the overlay draws tiles instead.
    """
    subject, look, context = build_map_prompt(
        gen, name=name, biome=biome, lighting=lighting, extra=extra,
        conditions=conditions)
    # The caller may pin the art to a layout other than the CURRENT grid —
    # which is how a board keeps its picture after the party smashes a pillar,
    # instead of re-rendering the whole room over one changed square.
    ref = ref_slug or layout_signature(gen.grid, gen.archetype, gen.seed)
    w_px, h_px = canvas_size(gen.width, gen.height, budget_px=budget_px)

    if store is None:
        try:
            from imagery import ImageStore  # local import: imagery is optional
            store = ImageStore()
        except Exception as e:  # pragma: no cover - environment dependent
            print(f"[vtt.art] imagery unavailable: {e}")
            return BattlemapArt(image_id=None, prompt="", caption=subject,
                                offline=True)

    try:
        res = store.ensure_image(
            "map", subject,
            look=look, context=context, ref_slug=ref,
            extra=_MAP_STYLE,
            force_new=force_new,
            width=w_px, height=h_px,
            # The layout seed drives the render seed too, so the same room
            # regenerated from its row comes back looking the same.
            seed=gen.seed & 0x7FFFFFFF,
            max_per_bucket=1,      # one picture per layout — it IS the room
            store_width=store_width,
            # The layout goes to the model as a PICTURE. Without this the
            # prompt describes a room and the model paints a different one —
            # walls nowhere near the grid's walls, and the players are looking
            # at somewhere else entirely.
            control_image=(control_image(gen.grid) if controlnet else None),
            controlnet=controlnet,
            controlnet_strength=controlnet_strength,
            # Forbid the picture terrain the RULES don't have. Without this a
            # dungeon comes back with a pool painted across dry flagstone, and
            # a player asking how deep it is gets told there's no water — the
            # DM only ever sees the grid. Derived from that same grid, so it
            # can't disagree with the cache key.
            negative_extra=gen.grid.absent_terrain_negative(),
        )
    except TypeError as e:
        # An older ImageStore without the sizing kwargs — degrade, don't crash.
        print(f"[vtt.art] image store lacks map sizing support ({e}); "
              "falling back to default render size")
        try:
            res = store.ensure_image("map", subject, look=look, context=context,
                                     ref_slug=ref, extra=_MAP_STYLE)
        except Exception as e2:
            print(f"[vtt.art] render failed: {e2}")
            return BattlemapArt(image_id=None, prompt="", caption=subject,
                                offline=True)
    except Exception as e:
        print(f"[vtt.art] render failed: {e}")
        return BattlemapArt(image_id=None, prompt="", caption=subject, offline=True)

    if res is None:
        # Imagery disabled by config — a legitimate, silent, tiles-only mode.
        return BattlemapArt(image_id=None, prompt="", caption=subject, offline=True)

    return BattlemapArt(
        image_id=res.image_id,
        prompt=(res.meta or {}).get("prompt", "") if isinstance(res.meta, dict) else "",
        caption=res.caption,
        width=res.width, height=res.height,
        offline=bool(res.offline),
        reused=bool(res.reused),
    )


#: Wreckage sprites are RENDERED large and stored small. SDXL is trained at
#: 1024, so asking it for 320px gives a muddy 320px; asking for 512 and
#: downscaling gives a sharp one. Measured warm on this rig: 320px 6.1s,
#: 256px 8.2s (worse — too far off-distribution), 512px 9.2s, against 13.5s
#: for a full ~1.1MP battlemap.
#:
#: So a sprite is roughly 1.5x cheaper than a whole map, nowhere near
#: proportional to its pixels, because a diffusion call carries fixed overhead
#: that dominates at small sizes. The real economy is elsewhere: a sprite is
#: keyed by (what it became, its material, the board's look), so the first
#: shattered pillar pays for every shattered pillar in every room that looks
#: like it — the same trick that made the item-art catalogue affordable.
DEBRIS_RENDER_PX = 512      # what the model is asked for
DEBRIS_PX = 256             # what gets stored, after the background is cut

#: Bump when the sprite FRAMING changes, not when a subject is reworded.
#:
#: Sprites are cached by ref slug, which is what makes them cheap — so a
#: reworded prompt reaches nobody who already has the old picture, and a board
#: keeps serving sprites drawn to a framing that no longer matches. Versioning
#: the slug retires the old bucket without touching the store, and the
#: pre-render script refills it. (Rev 2: the whole catalogue was drawn in
#: elevation — side-on doors and crates on a top-down map.)
SPRITE_REV = 2

#: Said to the model on every sprite, object and wreckage alike. It is the same
#: instruction the subject already carries, repeated where the sampler weights
#: it differently — orthography is the one thing a battlemap sprite cannot get
#: wrong and still be usable.
#: NOT "filling the frame" — that was in here, and it fought the matting. A
#: subject painted edge to edge has no background for rembg to find, so the cut
#: either kept the whole square (a pale box round a pillar) or erased the
#: subject with it (an empty square where a crate should be). A clear margin
#: costs a little sprite resolution and gives the matte something to grip.
_SPRITE_FRAMING = (
    ", orthographic top-down battlemap token, camera directly overhead "
    "pointing straight down at the floor, flat plan view, no perspective, "
    "one single object centred in the frame with a clear even margin of plain "
    "empty ground all around it, nothing else in view, "
    "no walls, no room, no scene, no border"
)


# ---------------------------------------------------------------------------
# Surface materials: what the isometric board is built out of
# ---------------------------------------------------------------------------
#
# The flat board is a painting of a whole room, rendered per layout. The
# isometric board is geometry, and geometry wants MATERIALS — one swatch per
# (what this surface is made of, what the room looks like), reused by every
# square that is made of it.
#
# That is the same move the item-art catalogue and the debris sprites already
# made, and it is the one that changes the arithmetic: a room's picture costs a
# render every time the party finds a new room, and a catalogue costs a render
# once, ever. `mapgen` is deterministic code, so with the swatches on disk a
# brand-new room costs nothing at all.
#
# These are NOT matted. A sprite is a thing standing on the floor and needs its
# background cut away; a material IS the floor, edge to edge, and rembg would
# only find a subject in it and throw the rest away.

MATERIAL_RENDER_PX = 512    # SDXL is happiest here, and a swatch stores small

#: Bump when the FRAMING changes, exactly as with SPRITE_REV — the slug is the
#: cache, so a reworded prompt reaches nobody who already holds the old swatch.
#: Rev 2: objects stopped being materials — see SUBSTANCE below.
MATERIAL_REV = 2

#: Materials whose look does not depend on the room around them.
#:
#: Lava is lava in a cavern and in a ruin; a floor is not. Keeping these out of
#: the per-look cross product is the `debris_ref` lesson applied to surfaces —
#: key on the axes that actually change the picture and no others.
LOOK_AGNOSTIC: frozenset[str] = frozenset({
    "~", "W", "l", "x", "i", "%", "m", "f",
})

#: The look bucket a material with no setting of its own is filed under.
ANY_LOOK = "any"

#: Codes with no surface to draw: the board renders nothing there.
#:
#: Every HOLE belongs here. A chasm is the absence of floor, so there is nothing
#: to photograph — and asking anyway is not free: "a surface of yawning chasm"
#: came back as an open MOUTH WITH TEETH, filed in the catalogue and drawn by
#: nothing, which is the pure form of this module's category error.
NO_MATERIAL: frozenset[str] = frozenset({" ", "^", "x"})

#: Discrete objects, and what they are MADE OF.
#:
#: An object is not a material and asking for one as though it were gets a
#: PICTURE OF THE THING: "a surface of heavy closed door" came back as a door
#: seen face-on with a barrel beside it, which is a fine illustration and
#: useless as a texture. The mistake was mine and it is a category error — a
#: crate is a box the board builds out of geometry, and what it needs from this
#: catalogue is a swatch of the timber it is planked from.
#:
#: It also collapses the catalogue: nine object kinds share four substances,
#: and none of them care what room they are in, so they cost four swatches
#: between them instead of ninety.
SUBSTANCE: dict[str, str] = {
    "O": "stone",   # pillar
    "A": "stone",   # altar
    "w": "stone",   # low wall
    "o": "wood",    # crates
    "n": "wood",    # furniture
    "+": "wood",    # door
    "/": "wood",    # open door (the panel; its square's floor is separate)
    # A tree is FOLIAGE, not bark. The swatch colours the whole square, and
    # what a square of tree presents to a camera on the ceiling is its crown —
    # so bark painted the canopy brown, and the model, handed brown cones on
    # green ground, made them violet. The trunk is under the leaves from up
    # here, which is what makes one colour per square an honest answer.
    "T": "foliage",  # tree
    "p": "iron",    # portcullis
}

#: What a swatch is ASKED for. Name the colour: "grey stone" is a request for
#: an achromatic image, the model obliges, and a square of dead neutral grey is
#: the one thing the painter feels free to invent a hue for — the ruins' columns
#: came back BLUE, and the tree crowns came back violet before their swatch went
#: green. A real stone has a colour, so say which.
SUBSTANCE_ART: dict[str, str] = {
    "stone": "close-up of cut limestone blocks, warm pale sandy grey with "
             "ochre and buff weathering",
    "wood": "old planked timber, close-up of the boards and their grain",
    "iron": "dark pitted wrought iron, close-up of the bare metal",
    "bark": "rough tree bark, close-up of the bark itself",
    "foliage": "dense green leaf canopy seen from directly above, close-up of "
               "the leaves themselves",
}

#: Concrete nouns for surfaces whose `tile.art` is too vague to render.
#:
#: `tile.art` is written for battlemap prompts, where "open floor" sits inside
#: a whole scene and reads fine. Alone, as the entire subject of a swatch, it
#: says almost nothing — and a diffusion model handed an under-specified frame
#: fills it with composition, which is how "a surface of open floor" came back
#: as a circular medallion. Naming the actual stuff underfoot leaves it nothing
#: to invent.
MATERIAL_SUBJECT: dict[str, str] = {
    ".": "the bare ground underfoot, close-up of the paving or packed earth",
    "=": "close-up of a cobbled road surface, the cobbles themselves",
    "b": "close-up of weathered wooden deck planking",
    "u": "close-up of worn stone step treads",
    "g": "close-up of grass turf",
    "s": "close-up of rippled packed sand",
}

#: The style a swatch is painted in. Deliberately NOT ``_MAP_STYLE``, which
#: says "battlemap" — the one word this render must not hear.
#:
#: "Seamless" is asked for and not relied on: SDXL does not tile without help.
#: It earns its place anyway because it biases toward flat even coverage with
#: nothing composed in the middle, which is what a swatch needs. The board
#: handles the rest by giving each square its own copy and varying the UVs, so
#: an imperfect edge reads as grout between flagstones rather than as a seam.
_MATERIAL_STYLE = (
    "hand-painted texture, rich painterly surface detail, honest materials, "
    "muted natural palette, even flat lighting"
)

#: What a swatch must not be. Anything implying a viewpoint turns a material
#: into a photograph of a place, and anything implying a light source bakes a
#: shadow into a surface that the board is going to light for itself.
MATERIAL_NEGATIVE = (
    "battlemap, map, floorplan, dungeon map, room, scene, composition, "
    "border, frame, ornate border, decorative frame, corner ornament, "
    "medallion, cartouche, inset panel, "
    "perspective, isometric, three-quarter view, side view, elevation, "
    "horizon, vanishing point, depth of field, object, furniture, creature, "
    "person, text, label, watermark, vignette, "
    "dramatic lighting, strong directional shadow, spotlight, gradient"
)


#: The looks the catalogue is pre-rendered for. Kept in step with
#: scripts/material_prerender.py's CONTEXTS and debris_prerender.py's, so all
#: three agree about what a "dungeon" looks like.
BOARD_LOOKS: tuple[str, ...] = (
    "underground", "dungeon", "woodland", "town", "cavern", "ruins",
    "interior", "snow", "desert", "wetland",
)

#: Archetype -> the look its surfaces are drawn in. An archetype is a much
#: better signal than a biome string: it is a closed set the generator chose,
#: where `biome` is whatever the DM happened to type.
_ARCH_LOOK: dict[str, str] = {
    "dungeon-room": "dungeon", "dungeon-complex": "dungeon",
    "crypt": "dungeon", "sewer": "underground", "cave": "cavern",
    "mine": "underground", "forest": "woodland", "clearing": "woodland",
    "swamp": "wetland", "reef": "wetland", "open-water": "wetland",
    "street": "town", "tavern": "interior", "camp": "woodland",
    "ship": "interior", "skyship": "interior", "arena": "town",
    "ruins": "ruins", "bridge": "town", "mountain-pass": "snow",
    "sky-islands": "woodland", "open": "woodland",
}

#: Words in a free-text biome that outrank the archetype. A dungeon room cut
#: into a glacier is a snow board whatever the generator called it.
_BIOME_WORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("snow", "ice", "glacier", "frozen", "tundra", "arctic"), "snow"),
    (("desert", "dune", "sand", "arid", "badlands"), "desert"),
    (("swamp", "marsh", "bog", "fen", "wetland", "mangrove"), "wetland"),
    (("ruin", "derelict", "abandoned", "overgrown"), "ruins"),
    (("cavern", "cave", "grotto"), "cavern"),
    (("underdark", "underground", "tunnel", "mine", "sewer"), "underground"),
    (("forest", "wood", "jungle", "grove", "thicket"), "woodland"),
    (("city", "town", "village", "street", "market"), "town"),
    (("tavern", "hall", "chamber", "indoor", "interior", "manor"), "interior"),
    (("crypt", "dungeon", "vault", "keep"), "dungeon"),
)


def board_look(biome: str = "", archetype: str = "") -> str:
    """Which of the ten catalogue looks this board's surfaces are drawn in.

    The catalogue only pays for itself if boards actually HIT it, and a board's
    `biome` is free text a DM typed ("damp underdark", "the old mill"). Slugged
    straight into a context key that matches nothing, every board would miss and
    re-render its own materials on demand — which is the per-room cost this
    whole design exists to remove. So free text is mapped onto the closed set,
    with the archetype as the fallback signal and "dungeon" as the floor.
    """
    b = (biome or "").strip().lower()
    for words, look in _BIOME_WORDS:
        if any(w in b for w in words):
            return look
    return _ARCH_LOOK.get((archetype or "").strip().lower(), "dungeon")


#: Bump when the CAMERA, the geometry's silhouettes, or the DEPTH ENCODING
#: change — a painting is baked to one view, and a board holding art drawn to a
#: camera that has since moved is worse than a board with no art at all.
#:
#: Rev 2: the depth map became relief (height above the floor) instead of raw
#: distance. Worth noting that the slug keys on the LAYOUT, so a rasterizer
#: change is invisible to it — the first re-render after the switch came back
#: from cache in one second, unchanged, and would have been mistaken for the
#: fix not working.
#: How much of the terrain image the sampler may throw away on a FLAT board.
#:
#: Below ~0.6 the flat facets of the terrain image survive and the result reads
#: as a tinted diagram; above ~0.8 the model starts inventing ground again,
#: which is the thing the terrain image exists to stop.
ISO_DENOISE_FLAT = 0.72

#: A board at least this built-up is painted from its DEPTH MAP ALONE.
#:
#: Measured, and it went the opposite way to what I expected. Scaling the
#: denoise up for walled rooms did not recover their quality: a tavern given the
#: terrain image at 0.86 came back a bare plank floor with its hearth and
#: benches gone, worse than the same room with no init image at all. The two
#: signals are not interchangeable — a walled room is fully described by its
#: depth map, and handing the model a flat-coloured plan on top only tells it
#: the room is flat. Outdoors there is no depth to describe anything with, and
#: the terrain image is the only thing that knows where the water is.
#:
#: So the init image is used where it is NEEDED rather than everywhere.
BUILT_UP_FRACTION = 0.20


def standing_fraction(grid: Grid) -> float:
    """How much of this board stands up off the floor."""
    from .terrain import tile_height_ft

    rows = grid.to_rows()
    total = sum(1 for r in rows for c in r if c != " ")
    if not total:
        return 0.0
    return sum(1 for r in rows for c in r
               if c != " " and tile_height_ft(c) > 0) / total


#: Denoise for a BUILT board's ground-only init. See `_ground_init`.
#:
#: High on purpose, and MEASURED — four arms on the street, the taproom, the
#: crypt and the mountain pass:
#:
#: * 1.00 (no init, what a built board used to get): the street is planked, and
#:   the model paints a ROOM around the diorama — the pass came back standing on
#:   a wooden table against green wallpaper;
#: * 0.90: the street is paved stone, the invented surround is gone, the pass
#:   stands on a plain plinth, the crypt keeps its painting;
#: * 0.84: colours start sliding toward the init's flat tints — the taproom's
#:   floor goes mustard;
#: * 0.72: exactly what ISO_DENOISE_FLAT warns about — grey walls, flat fill, no
#:   painted detail anywhere. The old finding holds; it is the strength that was
#:   wrong, not the idea.
#:
#: Tunable through ORACLE_ISO_GROUND_DENOISE. **Name it in WSLENV or the arm is
#: a lie**: the probe runs under the WINDOWS interpreter, an env var does not
#: cross by default, and three "different" arms came back BIT-IDENTICAL because
#: every one of them had quietly used this default. A pixel diff of exactly 0.00
#: between two arms is never a result — it means they were the same run.
GROUND_INIT_DENOISE = float(os.environ.get("ORACLE_ISO_GROUND_DENOISE") or 0.90)

#: What a standing square is painted in the ground-only init: a mid grey with
#: no hue at all, so the model takes no colour cue from it. NOT black — black
#: is a value the model follows, and the walls would come back in shadow.
_GROUND_INIT_NEUTRAL = (128, 128, 128)


def _ground_init(gen: GeneratedMap, look: str, store, skin_of, depth_kw):
    """The terrain image with everything that STANDS UP painted out.

    A built board is handed no init image at all — measured, twice, and for a
    good reason: given the whole terrain image it stops painting and starts
    tinting. But the rule is one number for the whole picture, and it takes the
    FLOOR down with the walls. A street's roadway is nearly half the frame, its
    material is a fact the grid holds (`skins.cobbles`), and the only channel
    left to carry it is the prompt — which loses, every time, to whatever the
    model thinks a strip of ground between two rows of houses is made of. It
    came back planked.

    Depth cannot help: cobbles have no relief, so there is no silhouette to hand
    over, and the shape-is-the-sentence fix that worked everywhere else has
    nothing to work with here.

    So the ground gets its own channel and nothing else does. Every square that
    stands up — wall, post, crate, tree, landmark — is painted flat neutral
    grey, which carries no composition and no colour, and the model goes on
    taking all of that from the depth map exactly as before. What survives is
    the one thing depth could never say: what the floor is made of, and where
    the floor is.

    **What it bought, and what it did not.** The street is paved instead of
    planked, and every built board stopped having a ROOM painted around it — a
    diorama on a bare canvas reads to the model as a diorama on a TABLE, and it
    obliged with wallpaper and floorboards behind the mountain pass. The crypt
    pays a little of its painted detail for that, which is the trade the earlier
    full-init experiment lost on badly and this one wins on narrowly.

    It did NOT fix a taproom. A tavern now declares oak boards
    (`skins.taproom-floor`) and the painting still comes back pale flagstone:
    the model's prior for an isometric room interior is stone, and the only
    lever strong enough to overcome it is a denoise that destroys the painting.
    Its posts read as CANDLES for the same reason, and thickening them to two
    feet with a bracket at the head — the shape-is-the-sentence fix that worked
    on cliffs and trees — changed nothing measurable. Both are recorded rather
    than guessed at again. The geometry board draws them correctly, which is the
    surface a player actually plays on.
    """
    from . import isocam
    from .terrain import tile_height_ft

    def colour(code: str, sk: str) -> tuple[int, int, int]:
        if code.startswith("decor:") or code.startswith("setpiece"):
            return _GROUND_INIT_NEUTRAL
        sh = skins.skin(sk) if sk else None
        if tile_height_ft(code) > 0 or (sh is not None and sh.height_ft):
            return _GROUND_INIT_NEUTRAL
        return material_colour(code, look, store, skin=sk)

    return isocam.terrain_image(colour_of=colour, **depth_kw)


def iso_denoise_for(grid: Grid, skinned: bool = False) -> float:
    """Denoise for this board; 1.0 means "no init image, depth alone".

    ``skinned`` is accepted and deliberately IGNORED. Two findings, and the
    second corrects the first.

    A skin says a board's materials are not the ones the model would guess, so
    it looked as though a skinned board should always get its terrain image,
    overriding the built-up rule that hands a walled room no init image at all.
    Rendered across the whole gallery that made six boards worse to help one:
    the dungeon, the crypt, the arena and the cave all lost their painted
    detail and came back as flat tinted geometry, which is exactly what the
    built-up rule was measured to prevent. Dropping the denoise to 0.60 to
    compensate made it worse again, for the reason ISO_DENOISE_FLAT records.
    Both reverted. THAT much holds.

    What was ALSO concluded from the same session — that a depth ControlNet
    simply cannot convey a material against a strong silhouette prior — was
    WRONG, and the evidence for it was poisoned. A skyship's three styles
    change materials and not one tile, and `isoboard_ref` hashed only the
    tiles, so all three shared a cache slug and the first render was served to
    the other two. They looked identical because they were one picture. With
    the skins in the key they come back as three plainly different vessels at
    this very denoise: a tarred caravel, a verdigris brass contraption, a green
    chitin hull. The technique was fine; the cache was lying.

    **Second-guessed a second time, from the other side, and it held again.** A
    mountain pass is three quarters solid and one percent BUILT, so it is handed
    no init image and comes back a snowy village with wooden doors in it — the
    board's densest, most defensible failure. Three alternatives were rendered
    against it and the cave (``scene_probe --paint --force --tag``), and all
    three are worse:

    * measuring how BUILT UP a board is by its built codes rather than by how
      much of it stands, so raw country gets its terrain image at 0.72: the
      village goes, and the pass comes back as untinted grey CUBES — the flat
      tinted diagram ISO_DENOISE_FLAT exists to prevent, and the same loss the
      cave took when it was measured before;
    * 0.85 as a middle ground, on the theory that the depth carries the shapes
      and the init need only carry the material: the architecture came straight
      back, timber walls and doors;
    * forbidding architecture in the NEGATIVE prompt (no houses, no doors, no
      village) at full denoise: the model simply built it out of something else
      — carved stone pilasters and arched gateways.

    The negative was then tried a SECOND time, after the cliff geometry was
    fixed and the pass was coming back as crags, on the theory that a nudge only
    lands on a model that is not already certain. It measured worse than no
    negative at all: same board, same seed, one extra clause, and the picture
    gained a timber shrine, a gilded stupa and a row of stone plinths. Naming a
    thing a dozen times to forbid it is still naming it. Reverted; the fix that
    worked was the SHAPE, twice over.

    Which locates the fault, and it is not here. **The model paints the
    silhouette it is handed**, and a mountain pass was handed a field of
    cuboids; a field of cuboids IS a village. That is the crypt-of-dice lesson
    (see ``isocam``: a crypt of four-foot cubes read as a board game however
    loudly the prompt said "stone coffins") arriving outdoors. The fix was in
    the SHAPES — see ``skins._CLIFF``, now battered and canted prismatoids —
    and it worked: the pass comes back as snowy crags.
    """
    return (1.0 if standing_fraction(grid) >= BUILT_UP_FRACTION
            else ISO_DENOISE_FLAT)


#: Rev 19: skins. A square's material and silhouette can now depend on the
#: archetype (see vtt.skins), so a mountainside is granite drawn as rock mass
#: rather than masonry drawn as wall panels. That changes both conditioning
#: images for most boards.
#: Rev 20: rock and coral pick their arrangement from a COARSE hash (see
#: isocam.variant_smooth), a skyship's DECK follows its style, and six swatches
#: were redrawn — canvas came back as planks, which is why every camp had
#: timber pens in it instead of tents.
#: Rev 21: the material clauses carry CLIP emphasis — see render_iso_board.
#: Rev 22-24: an experiment and its reversal — a skinned board was given its
#: terrain image regardless of how built up it is, and it cost six boards their
#: painted detail to help one. See iso_denoise_for.
#: Rev 25: towers stopped being solid boxes with their doorways bricked up,
#: skirts slope and a vessel carries its own hull, and a sea ship no longer
#: shares a deck skin with a skyship.
#: Rev 27: shapes stopped being boxes. Parts may now be PRISMATOIDS, so a
#: tent's canvas is one pitched face rather than four terraces, a timber
#: watchtower is four raked legs under a platform, and a hull's stair-stepped
#: outline is cut into a continuous diagonal. Every board's silhouette changed,
#: so every painting conditioned on the old one is stale.
#: Rev 28: a built board gets a GROUND-ONLY init image (see `_ground_init`)
#: instead of no init at all, so its floor material finally has a channel; and
#: a taproom declares its own — boards, plaster-and-timber walls, square oak
#: posts. Every built board's floor changes, so their paintings are stale.
#: Rev 29: a SEGMENTATION control image beside the depth one (vtt/segmap.py) —
#: what each square IS, in ADE20K class colours, on a union ControlNet told it
#: is being handed `segment`. Depth could only ever say where a thing is and how
#: tall, so a two-foot shaft was a post, a candle or a bollard; it painted
#: candles. Every conditioned board changes.
ISOBOARD_REV = 29


#: Retained for callers that still ask, and for the gallery's reporting. The
#: threshold is 0 because the question it answered no longer decides anything:
#: an OPEN board used to be refused because a depth map of flat ground carries
#: almost nothing, and that is now the terrain image's job.
MIN_STANDING_FRACTION = 0.0


def worth_painting(grid: Grid) -> bool:
    """Can this board be conditioned well enough to be worth painting?

    Yes, now, for every board with any ground on it — which is the point of the
    terrain image. Depth alone could only describe a room with walls in it, so
    forests, decks, reefs and open sky were refused and kept their geometry.
    With layout and terrain TYPE arriving as an img2img base, a flat board is as
    describable as a walled one.

    Kept as a function rather than deleted: a board with nothing on it at all
    still has nothing to paint.
    """
    from .terrain import tile

    return any(tile(c).art for row in grid.to_rows() for c in row if c != " ")


def isoboard_ref(grid: Grid, archetype: str, seed: int,
                 skins: str = "") -> str:
    """Cache slug for the painted isometric view of this exact layout.

    ``skins`` is what the board is MADE of, and leaving it out was a real bug
    rather than an omission: a skyship's three styles change materials and not
    one tile, so all three hashed identically and the first one rendered was
    served to the other two. They came back looking the same because they WERE
    the same picture — which sat underneath a genuine limit of the technique
    and made it look worse than it is.

    Same lesson as ``layout_signature`` following the CURRENT grid: the cache
    key has to name everything the picture depends on, or it quietly serves the
    wrong one.
    """
    base = layout_signature(grid, archetype, seed)
    if not skins:
        return f"iso-v{ISOBOARD_REV}-{base}"
    tag = hashlib.sha256(skins.encode("utf-8")).hexdigest()[:8]
    return f"iso-v{ISOBOARD_REV}-{base}-{tag}"


def _setpiece_instances(gen) -> list[dict]:
    """This board's landmarks, ready for the depth rasterizer.

    Rebuilt from the generator's own record rather than taken from ``state()``,
    because the art is rendered at OPEN time, before there is a row to read.
    """
    if not getattr(gen, "setpieces", None):
        return []
    from . import setpieces as _sp
    out = []
    for p in gen.setpieces:
        slug = str(p.get("slug") or "")
        if slug in _sp.CATALOGUE:
            out.append(_sp.Placed(slug=slug, x=int(p.get("x") or 0),
                                  y=int(p.get("y") or 0),
                                  yaw=int(p.get("yaw") or 0)).instance())
    return out


def conditioning_kwargs(gen: GeneratedMap, *, skin_of=None) -> dict:
    """Everything the rasterizers need to draw THIS board, in one bundle.

    One bundle, used for the depth map, for the terrain image AND for the mask,
    so the cut can never be taken from different geometry than the picture was
    conditioned on. Extracted so a caller that only wants to LOOK at the
    conditioning (``scene_probe --depth``) gets the same geometry the render
    got, rather than a second construction of it that can quietly diverge.
    """
    from . import hull as _hull
    from . import skins as _skins
    from .decor import decor_for
    from .terrain import cover_height_ft, tile_height_ft

    if skin_of is None:
        code_skins = _skins.skins_for(gen.archetype, style=gen.style)
        square_skins = dict(gen.skins or {})

        def skin_of(c: str, x: int, z: int) -> str:      # noqa: E306
            return _skins.skin_at(c, x, z, codes=code_skins,
                                  squares=square_skins)

    return dict(
        rows=gen.grid.rows, height_ft=tile_height_ft, cover_ft=cover_height_ft,
        decor=decor_for(gen.grid.to_rows(), seed=gen.seed,
                        standing=lambda c: tile_height_ft(c) > 0,
                        archetype=gen.archetype),
        square_ft=5, structure=STRUCTURE_CODES, skin_of=skin_of,
        elevation=gen.elevation,
        shells=_hull.shells(gen.grid.rows, skin_of, gen.elevation),
        # The landmarks, or the painter is conditioned on a depth map with a
        # HOLE where the colossus stands — it would paint open ground there and
        # the geometry would then be a statue nothing in the picture agrees
        # with. The tiles a set piece stamps are already in ``rows``; its mesh
        # is not, and the mesh is most of its volume. This was missing once, and
        # the symptom was landmarks that reached the board and the Discord PNG
        # and never the painting.
        setpieces=_setpiece_instances(gen))


def conditioning_images(gen: GeneratedMap, *, store=None,
                        biome: Optional[str] = None,
                        lighting: Optional[str] = None) -> dict:
    """The two pictures the painter is conditioned on, for inspection.

    ``{"depth": png, "terrain": png}``. Nothing in play calls this — it is for
    looking at, because a landmark that never reached the depth map is
    invisible in exactly the same way as one the model chose to ignore, and
    telling those apart by staring at the result is not possible.
    """
    from . import isocam

    kw = conditioning_kwargs(gen)
    look = board_look(biome or "", gen.archetype)
    out = {"depth": isocam.depth_image(**kw)}
    if store is None:
        try:
            from imagery import ImageStore
            store = ImageStore()
        except Exception:
            store = None
    out["terrain"] = isocam.terrain_image(
        colour_of=lambda c, sk: material_colour(c, look, store, skin=sk), **kw)
    return {k: v for k, v in out.items() if v}


def render_iso_board(gen: GeneratedMap, *, store=None, name: str = "",
                     biome: Optional[str] = None, lighting: Optional[str] = None,
                     extra: str = "", conditions: str = "",
                     controlnet: Optional[str] = None,
                     controlnet_strength: float = 0.55,
                     controlnet_union_type: str = "",
                     seg_controlnet: Optional[str] = None,
                     seg_strength: float = 0.45,
                     force_new: bool = False,
                     store_width: int = 1536) -> BattlemapArt:
    """Paint the board as an isometric diorama, conditioned on its own depth.

    The Baldur's-Gate layer. Those backgrounds were pre-rendered images with a
    depth map shipped beside them so characters could walk behind things; this
    is the same arrangement with a diffusion model standing in for the offline
    renderer, and the browser's own geometry standing in for the depth map —
    which is why `isocam` has to mean the same thing on both sides.

    Never raises: with no GPU or no depth model configured the board keeps its
    geometry, which is playable and was always the point of building that first.
    """
    from . import isocam
    from . import skins as _skins
    from .terrain import tile_height_ft

    # What this board is MADE of. Derived from the archetype, with whatever
    # exceptions the generator built recorded per square — and told to the
    # painter, because a depth map can say a thing is ten feet tall and never
    # that it is coral rather than quarried stone.
    code_skins = _skins.skins_for(gen.archetype, style=gen.style)
    square_skins = dict(gen.skins or {})
    present = sorted({
        _skins.skin_at(gen.grid.get(x, y), x, y,
                       codes=code_skins, squares=square_skins)
        for x, y in gen.grid.squares()} - {""})

    def _skin_of(c: str, x: int, z: int) -> str:
        return _skins.skin_at(c, x, z, codes=code_skins, squares=square_skins)

    # The material clauses carry CLIP emphasis, and they have to.
    #
    # A skyship's three styles reach the model as three genuinely different
    # terrain images — measured: brown oak, teal brass, green chitin — and all
    # three came back as the same wooden deck, because at 0.72 denoise the
    # sampler keeps less than a third of the init and the depth map's
    # silhouette says "ship" loudly enough that the model falls back on its
    # prior for one. The same lesson as the sky, one turn further: colouring
    # the conditioning image was necessary and is not sufficient. What a thing
    # is MADE of is the subject of these boards, so it is weighted like one.
    material_words = _skins.words_for(present)
    subject, look, context = build_map_prompt(
        gen, name=name, biome=biome, lighting=lighting,
        extra=", ".join(p for p in (
            extra,
            f"({material_words}:1.35)" if material_words else "",
            _void_reads_as(gen.grid, lighting, gen.mode)) if p),
        conditions=conditions)
    # The skins go into the cache key. Two boards with identical tiles and
    # different materials are two pictures, and hashing only the tiles served
    # one of them to both.
    ref = isoboard_ref(
        gen.grid, gen.archetype, gen.seed,
        skins="|".join(f"{k}={v}" for k, v in sorted(code_skins.items()))
        + "#" + "|".join(f"{k}={v}" for k, v in sorted(square_skins.items())))

    if not worth_painting(gen.grid):
        # An OPEN board is refused, and this is a limit of the technique rather
        # than a tuning failure. A depth map encodes HEIGHT and nothing else, so
        # on a board that is mostly flat ground it carries almost no
        # information — a forest clearing rasterizes to a smooth gradient with a
        # dozen thin posts in it. Worse, the terrain that actually distinguishes
        # such a board is FLAT: water, grass, road and ice all sit at height
        # zero and are invisible to depth. Handed that, the model invents, and
        # what it invents disagrees with the grid — a pond painted where there
        # is none, trees rendered as sawn-off stumps, a street's walls as trays
        # of produce. All three were measured.
        #
        # So enclosed boards get a painting and open ones keep their geometry,
        # which is terrain-accurate, instant and already good. Conveying flat
        # terrain TYPE would need a second conditioning image (a segmentation
        # map), which is a separate piece of work and not a knob on this one.
        return BattlemapArt(image_id=None, prompt="", caption=subject, offline=True)

    if store is None:
        try:
            from imagery import ImageStore
            store = ImageStore()
        except Exception as e:
            print(f"[vtt.art] imagery unavailable: {e}")
            return BattlemapArt(image_id=None, prompt="", caption=subject,
                                offline=True)

    if not controlnet:
        # Without depth conditioning the model paints a plausible isometric room
        # that is not THIS room — worse than no painting, because the board
        # would then disagree with itself. Refuse rather than mislead.
        return BattlemapArt(image_id=None, prompt="", caption=subject, offline=True)

    depth_kw = conditioning_kwargs(gen, skin_of=_skin_of)
    depth = isocam.depth_image(**depth_kw)
    # WHAT each square is, beside where it is. Same rasterizer, same camera,
    # same kwargs — so the two control images cannot describe different rooms.
    # See vtt/segmap.py for why depth alone could never say "post" rather than
    # "candle", and why a class colour must not be shaded.
    seg_controls: list[dict] = []
    if seg_controlnet:
        from . import segmap
        seg = segmap.seg_image(**depth_kw)
        if seg:
            seg_controls.append({"name": seg_controlnet, "image": seg,
                                 "union_type": "segment",
                                 "strength": float(seg_strength)})
    # The half depth cannot carry: what the ground is MADE of. Outdoors that is
    # the whole board, so without it the model invents terrain the grid does not
    # have. See isocam.terrain_image.
    look = board_look(biome or "", gen.archetype)
    denoise = iso_denoise_for(gen.grid, skinned=bool(present))
    if denoise >= 1.0:
        terrain = _ground_init(gen, look, store, _skin_of, depth_kw)
        denoise = GROUND_INIT_DENOISE
    else:
        terrain = isocam.terrain_image(
            colour_of=lambda c, sk: material_colour(c, look, store, skin=sk),
            **depth_kw)
    if not depth:
        return BattlemapArt(image_id=None, prompt="", caption=subject, offline=True)

    # The canvas has to be the depth map's own aspect, or the conditioning is
    # stretched across a frame it was not drawn for and every wall leans.
    from PIL import Image as _Image
    from io import BytesIO as _BytesIO
    dw, dh = _Image.open(_BytesIO(depth)).size
    w_px, h_px = canvas_size(dw, dh, budget_px=1_400_000)

    from imagery.models import ImageKind
    try:
        res = store.ensure_image(
            ImageKind.ISOBOARD, subject, look=look, context=context,
            ref_slug=ref, extra=_ISO_STYLE, force_new=force_new,
            width=w_px, height=h_px, store_width=store_width,
            seed=gen.seed & 0x7FFFFFFF, max_per_bucket=1,
            control_image=depth,
            controlnet=controlnet, controlnet_strength=controlnet_strength,
            controlnet_union_type=controlnet_union_type,
            controls=seg_controls,
            init_image=terrain,
            init_denoise=denoise,
            negative_extra=", ".join(p for p in (
                gen.grid.absent_terrain_negative(), _ISO_NEGATIVE) if p),
        )
    except Exception as e:
        print(f"[vtt.art] iso board render failed: {e}")
        return BattlemapArt(image_id=None, prompt="", caption=subject, offline=True)

    if res is None or res.offline or not res.image_id:
        return BattlemapArt(image_id=None, prompt="", caption=subject, offline=True)
    _mask_to_board(store, res.image_id, depth_kw)
    if gen.mode == "swim":
        _underwater_grade(store, res.image_id)
    return BattlemapArt(image_id=res.image_id, prompt="", caption=res.caption,
                        width=w_px, height=h_px, reused=bool(getattr(res, "reused", False)))


def _void_reads_as(grid: Grid, lighting: Optional[str],
                   mode: str = "walk") -> str:
    """What the empty parts of the board are, in words.

    The depth map can only say a square is FAR. On a board with open sky or a
    chasm in it that is most of the picture, and "far" alone came back as flat
    black — the model followed the depth into darkness. Depth says how far;
    only the words can say what is out there.

    Said only when the board actually HAS such squares, so an ordinary room is
    not told about a sky it does not have.

    ``mode`` is the medium the board is FOUGHT in, and it changes what water
    is. On a walking board a stretch of water is a surface you look at; on a
    SWIM board you are inside it, and the same clause — "its surface catching
    the light" — put the camera above the sea and turned a reef into a pond.
    """
    rows = grid.to_rows()
    has = {c for row in rows for c in row}
    bits = []
    if mode == "swim":
        # Said first and about the whole board, because it is the one fact
        # every square of it shares.
        bits.append("the ENTIRE scene is underwater, looked down on from above "
                    "through clear water — sunlight comes down in shafts and "
                    "dapples the sea floor, silt hangs in it, and NO water "
                    "surface is anywhere in the picture")
    if "^" in has:
        night = (lighting or "").lower() == "dark"
        # "Sky" alone was not enough: a flat expanse under an overhead camera
        # reads as water, and the board came back with the islands reflected in
        # it. Naming the cloud deck is what makes it air — the mottling in the
        # terrain image says the same thing in pixels.
        bits.append("the empty space between the platforms is OPEN SKY seen from "
                    + ("high above at night — a broken deck of moonlit cloud far "
                       "below, stars above, NOT water and nothing reflected in it"
                       if night else
                       "high above — a broken deck of soft white cloud far below "
                       "with blue air between, NOT water, no reflections, no "
                       "shoreline, no waves"))
    if "x" in has:
        bits.append("the gaps are a deep chasm dropping away into shadow, "
                    "its far walls lost in darkness")
    if "W" in has and mode != "swim":
        bits.append("the open water is deep and dark, its surface catching the light")
    elif "W" in has:
        bits.append("the deep water is a channel in the sea floor dropping away "
                    "into blue darkness")
    return ", ".join(bits)


#: How hard the underwater grade pulls, at the near edge and the far edge.
#:
#: Two numbers because water is a DEPTH effect: the far side of an isometric
#: board has more of it between the camera and the sand than the near side, so
#: the tint strengthens toward the top of the frame and the board reads as
#: receding into haze rather than as a flat sheet of tinted paper.
SUBSEA_TINT = (0.42, 0.78, 0.98)
SUBSEA_NEAR, SUBSEA_FAR = 0.26, 0.66
#: How much light the far side has lost. Water ABSORBS: distance underwater is
#: darker and bluer, not paler, and the first grade without this washed the top
#: of every board toward white — which reads as mist, and mist is a thing that
#: happens in air.
SUBSEA_FALLOFF = 0.34


def _underwater_grade(store, image_id: int) -> None:
    """Put the water back between the camera and the sea floor.

    Everything else on this board is decided per SQUARE — a tile code, a skin,
    a swatch — and the one thing that makes a reef read as a reef is not a
    property of any square: it is the water column in front of all of them.
    Measured, four ways, and none of the per-square levers reached it. The
    seabed swatch was corrected until it was a genuine sea floor, the coral was
    rebuilt from stalks into domes and plates, the prompt was told in as many
    words that the entire scene is underwater and that no surface is in view —
    and the board still came back as a green pond with reeds, because ninety
    percent of it is flat, a flat green expanse in a fantasy diorama is a pond,
    and no amount of conditioning outvotes that prior.

    So the grade is APPLIED rather than requested. This is not a retouch of the
    model's judgement; it is the one part of the picture the pipeline never gave
    it any way to paint. It is deterministic, costs no GPU, and lands on every
    swim board including the ones nobody has looked at.
    """
    from io import BytesIO

    try:
        import numpy as np
        from PIL import Image

        raw = store.get_image_bytes(image_id)
        if not raw:
            return
        img = Image.open(BytesIO(raw)).convert("RGBA")
        arr = np.asarray(img).astype(np.float32)
        rgb, alpha = arr[..., :3], arr[..., 3:]

        h = arr.shape[0]
        # 0 at the bottom of the frame (nearest the camera), 1 at the top.
        depth = np.linspace(1.0, 0.0, h, dtype=np.float32)[:, None, None]
        k = SUBSEA_NEAR + (SUBSEA_FAR - SUBSEA_NEAR) * depth

        tint = np.array(SUBSEA_TINT, dtype=np.float32)[None, None, :]
        # Toward the water's own colour, and toward each other: distance eats
        # contrast underwater before it eats brightness.
        haze = rgb.mean(axis=2, keepdims=True) * tint * (
            1.0 - SUBSEA_FALLOFF * depth)
        graded = rgb * (1.0 - k) + haze * k
        # A little of the light that got down here, falling from above.
        shafts = (1.0 + 0.05 * np.cos(
            np.linspace(0, 9 * np.pi, arr.shape[1], dtype=np.float32))[None, :, None])
        graded = np.clip(graded * shafts, 0, 255)

        out = Image.fromarray(
            np.concatenate([graded, alpha], axis=2).astype(np.uint8), "RGBA")
        buf = BytesIO()
        out.save(buf, format="PNG")
        data = buf.getvalue()

        from sqlmodel import Session

        from imagery.models import EntityImage
        with Session(store.engine) as sess:
            row = sess.get(EntityImage, image_id)
            if row is None:
                return
            row.image = data
            row.byte_size = len(data)
            sess.add(row)
            sess.commit()
    except Exception as e:      # an ungraded board beats no board
        print(f"[vtt.art] could not grade the underwater board: {e}")


def _mask_to_board(store, image_id: int, depth_kw: dict) -> None:
    """Cut the stored painting to the board's own silhouette.

    The board projects to a DIAMOND and the painting is its bounding RECTANGLE,
    so about half the canvas is corner the geometry never covers — and the model
    fills that margin with a SECOND room at its own scale, five or six times the
    board's, which makes the real room read as a doll's house inside a giant
    one. Asking for empty black in the framing and the negatives does not work;
    it paints a hearth out there anyway.

    So the corners are removed rather than requested, and it happens once here
    rather than every frame in every client. What is stored is RGBA with the
    surround transparent, which is also what lets a viewer lay it over its own
    background instead of over a black rectangle.
    """
    from io import BytesIO

    from PIL import Image

    from . import isocam

    try:
        raw = store.get_image_bytes(image_id)
        if not raw:
            return
        paint = Image.open(BytesIO(raw)).convert("RGBA")
        mask_png = isocam.coverage_mask(**depth_kw)
        if not mask_png:
            return
        mask = Image.open(BytesIO(mask_png)).convert("L").resize(
            paint.size, Image.LANCZOS)
        paint.putalpha(mask)
        out = BytesIO()
        paint.save(out, format="PNG")
        data = out.getvalue()

        from sqlmodel import Session

        from imagery.models import EntityImage
        with Session(store.engine) as sess:
            row = sess.get(EntityImage, image_id)
            if row is None:
                return
            row.image = data
            row.byte_size = len(data)
            sess.add(row)
            sess.commit()
    except Exception as e:      # a painting with corners beats no painting
        print(f"[vtt.art] could not mask the iso board: {e}")


#: Codes the depth rasterizer treats as full-square structure. Must match
#: STRUCTURE_CODES in activity-ui/src/lib/boardView.ts — the depth map and the
#: geometry are the same room or the painting sits on nothing.
STRUCTURE_CODES = {"#", "R"}


#: Painted, not photographed, and deliberately quiet about layout — the depth
#: map is saying all of that far more precisely than words could.
_ISO_STYLE = (
    "hand-painted fantasy diorama, rich painterly texture, honest materials, "
    "warm practical light sources, muted natural palette, deep readable shadow"
)

#: A figure painted into the board is a second, wrong party standing in the room
#: forever — the real creatures are DOM tokens drawn on top. The rest keeps the
#: frame clean of anything that would sit over the rules.
_ISO_NEGATIVE = (
    # NB: no "hearth", "fireplace" or "window" here, and that is deliberate.
    # They were, aimed at the second room the model used to invent in the
    # corners — and a negative applies to the WHOLE image, so they also deleted
    # the hearth that legitimately belongs in a taproom. The surround is now
    # removed deterministically by the coverage mask, which is the right tool:
    # it takes away what is outside the board without forbidding anything
    # inside it.
    "people, person, figure, character, adventurer, creature, monster, "
    "miniature, text, label, caption, watermark, border, frame, user interface, "
    "grid lines, arrows, top-down, overhead, floorplan, blueprint"
)


#: What the board's holes are made of, for the terrain image. Not swatches —
#: there is nothing to photograph — but they are emphatically not neutral grey
#: either, and leaving them black is how open sky came back as a night void.
#: Open sky is deliberately pale and near-desaturated rather than the mid blue
#: it started as. A uniform mid blue laid flat across 200 squares is exactly
#: what a body of WATER looks like from above — which is what the first
#: sky-islands boards came back as, reflections and all. The colour is half the
#: fix; the other half is that sky squares are MOTTLED rather than flat (see
#: `isocam._cloud`), because the thing that distinguishes sky from water at this
#: angle is cloud texture, not hue.
HOLE_COLOURS: dict[str, tuple[int, int, int]] = {
    "^": (176, 206, 233),   # daylight sky, seen from above the cloud deck
    "x": (26, 24, 30),      # a chasm dropping into shadow
    " ": (10, 12, 18),      # off the board entirely
}

#: (code, look) -> the average colour of that surface's swatch. Cached per
#: process; the swatches never change under a running server.
_MATERIAL_RGB: dict[tuple[str, str], tuple[int, int, int]] = {}


def material_colour(code: str, look: str, store=None,
                    skin: str = "") -> tuple[int, int, int]:
    """The average colour of a surface's catalogue swatch.

    Derived from the swatch rather than written down beside it, so the terrain
    image the painter starts from and the board the player looks at cannot
    describe different ground. A code with no swatch falls back to a neutral,
    which is honest: we do not know what it looks like yet.
    """
    from .decor import DECOR_KINDS

    key = (f"{code}@{skin}" if skin else code, look)
    if key in _MATERIAL_RGB:
        return _MATERIAL_RGB[key]
    rgb = (118, 112, 102)
    # A hole has no swatch and is not neutral grey. Open sky is sky; a chasm is
    # the dark it drops into. Without these the terrain image leaves them black
    # and the painting follows it there.
    if code in HOLE_COLOURS:
        _MATERIAL_RGB[key] = HOLE_COLOURS[code]
        return HOLE_COLOURS[key[0]]
    if code.startswith("decor:"):
        # Scenery gets the colour its KIND declares, which is a fact about the
        # thing and not about the room. It used to be one flat brown for every
        # kind — the browser had a per-kind tint table and the server did not,
        # so a green shrub reached the painter as a brown lump, and brown lumps
        # on grass are what came back as crates.
        from .decor import colour_of as _decor_colour
        kind = code[6:]
        if kind in DECOR_KINDS:
            hexed = _decor_colour(kind).lstrip("#")
            rgb = tuple(int(hexed[i:i + 2], 16) for i in (0, 2, 4))
        _MATERIAL_RGB[key] = rgb
        return rgb
    try:
        from io import BytesIO

        from PIL import Image

        from imagery.models import ImageKind, context_key, slugify
        if store is None:
            from imagery import ImageStore
            store = ImageStore()
        bucket = material_look(code, skin) or look
        rows = store.list_for(ImageKind.MATERIAL,
                              slugify(material_ref(code, skin)),
                              context_key(bucket))
        if rows:
            data = store.get_image_bytes(rows[0]["image_id"])
            if data:
                im = Image.open(BytesIO(data)).convert("RGB").resize((8, 8))
                px = list(im.getdata())
                rgb = tuple(sum(c[i] for c in px) // len(px) for i in range(3))
    except Exception as e:
        print(f"[vtt.art] material colour unavailable for {code!r}: {e}")
    _MATERIAL_RGB[key] = rgb
    return rgb


def material_ref(code: str, skin: str = "") -> str:
    """The cache slug for whatever material this square is made of.

    Keyed like `object_ref`: the slug names the material and the CONTEXT names
    the room's look, so a dungeon's flagstones and a cavern's floor are two
    buckets of one slug rather than two slugs.

    Objects resolve to their SUBSTANCE, so every stone thing on the board —
    pillar, altar, low wall — shares one swatch and the dedup is automatic. A
    SKIN is the same trick one level up (see :mod:`vtt.skins`): every coral
    thing on every reef shares one swatch, whatever tile code it wears.
    """
    from .terrain import tile
    from . import skins as _skins
    sk = _skins.skin(skin)
    if sk is not None:
        return f"material-v{MATERIAL_REV}-substance-{sk.substance}"
    if code in SUBSTANCE:
        return f"material-v{MATERIAL_REV}-substance-{SUBSTANCE[code]}"
    return f"material-v{MATERIAL_REV}-{tile(code).name.replace(' ', '-')}"


def material_subject(code: str, skin: str = "") -> str:
    """What to ask the model for. Empty when this code has no surface."""
    from .terrain import tile
    from . import skins as _skins
    if code in NO_MATERIAL:
        return ""
    sk = _skins.skin(skin)
    if sk is not None:
        return sk.art
    if code in SUBSTANCE:
        return SUBSTANCE_ART[SUBSTANCE[code]]
    return MATERIAL_SUBJECT.get(code) or tile(code).art


def material_look(code: str, skin: str = "") -> str:
    """Which look bucket this material is filed under, for a given board.

    A substance is as look-agnostic as lava: oak is oak in a cavern and in a
    tavern, and the room it stands in is the board's lighting job, not the
    swatch's. A skin names its substance outright, so it is filed the same way.
    """
    from . import skins as _skins
    if _skins.skin(skin) is not None:
        return ANY_LOOK
    return ANY_LOOK if (code in LOOK_AGNOSTIC or code in SUBSTANCE) else ""


def _skin_negative(skin: str) -> str:
    """Extra negative terms this skin's material declares, if any."""
    from . import skins as _skins
    sk = _skins.skin(skin)
    return getattr(sk, "negative", "") if sk is not None else ""


def render_material(code: str, *, store=None, context: str = "",
                    skin: str = "",
                    size_px: int = MATERIAL_RENDER_PX) -> Optional[int]:
    """A tiling surface swatch for every square made of this stuff.

    Returns an ``entity_image`` id, or None when art is unavailable — in which
    case the board falls back to the flat tile colours it already has, which is
    correct and playable, just plainer.
    """
    subject = material_subject(code, skin)
    if not subject:
        return None
    from imagery.models import ImageKind
    if store is None:
        try:
            from imagery import ImageStore
            store = ImageStore()
        except Exception as e:
            print(f"[vtt.art] imagery unavailable for material: {e}")
            return None
    ctx = material_look(code, skin) or (context or "dungeon")
    try:
        res = store.ensure_image(
            # The MATERIAL kind, never "map": a kind carries LoRAs, and the map
            # kind's (SDXL-Battlemaps, HadesLevel@0.9) turn any surface into a
            # framed battlemap however the prompt is worded.
            ImageKind.MATERIAL, subject, look="", context=ctx,
            ref_slug=material_ref(code, skin),
            extra=_MATERIAL_STYLE,
            # A skin may add to the negative. See Skin.negative: what a
            # material must NOT be cannot be said in the positive prompt, which
            # is where it kept being attempted.
            negative_extra=", ".join(
                p for p in (MATERIAL_NEGATIVE, _skin_negative(skin)) if p),
            width=size_px, height=size_px, store_width=size_px,
            max_per_bucket=1)
    except Exception as e:
        print(f"[vtt.art] material render failed: {e}")
        return None
    if res is None or res.offline or not res.image_id:
        return None
    return res.image_id


def cutout(png_bytes: bytes, *, size_px: int = DEBRIS_PX) -> Optional[bytes]:
    """Cut a sprite free of its background and downscale it.

    A diffusion model always paints something behind the subject, and a sprite
    dropped on a battlemap has to be the subject ALONE — otherwise it reads as
    a picture stuck on the floor rather than debris lying on it. rembg does the
    matting; the downscale to a stored size happens after, because the model
    draws a sharper 512 than it draws a 256.

    The matte is CHECKED, not trusted. Told to cut a pillar out of a picture of
    the same stone in the same light, u2netp sometimes keeps everything (a pale
    box lands on the board) and sometimes keeps nothing (the square comes back
    empty, and an object the rules say is there is invisible). Both are worse
    than not cutting at all, so a matte that erased almost everything — or
    almost nothing — is discarded in favour of the render, and the corner
    softening below carries the join either way.

    Returns None only when the picture can't be opened at all.
    """
    try:
        import io as _io
        from PIL import Image as _Image
    except Exception as e:                       # pragma: no cover
        print(f"[vtt.art] Pillow unavailable: {e}")
        return None
    try:
        base = (_Image.open(_io.BytesIO(png_bytes)).convert("RGBA")
                .resize((size_px, size_px), _Image.LANCZOS))
    except Exception as e:
        print(f"[vtt.art] sprite unreadable: {e}")
        return None

    im, hold = base, 0.55       # uncut: a real vignette, or it reads as a tile
    try:
        from rembg import remove, new_session
        global _REMBG_SESSION
        if _REMBG_SESSION is None:
            # u2netp: the small model. A pile of rubble does not need the big
            # one, and this keeps the first call from stalling a turn.
            _REMBG_SESSION = new_session("u2netp")
        cut = (_Image.open(_io.BytesIO(remove(png_bytes, session=_REMBG_SESSION)))
               .convert("RGBA").resize((size_px, size_px), _Image.LANCZOS))
        alpha = cut.getchannel("A")
        kept = sum(alpha.point(lambda v: 255 if v > 32 else 0)
                   .convert("L").getdata()) / (255.0 * size_px * size_px)
        if 0.08 <= kept <= 0.97:
            cut.putalpha(_harden_alpha(cut.getchannel("A")))
            im, hold = _fit_to_square(cut), 0.90
        else:
            print(f"[vtt.art] matte kept {kept:.0%} of the sprite — "
                  "using the render instead")
    except Exception as e:
        print(f"[vtt.art] no usable background removal ({e}); "
              "keeping the sprite as rendered")

    im = im.copy()
    im.putalpha(_soften_corners(im.getchannel("A"), hold=hold))
    try:
        buf = _io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:                       # pragma: no cover
        print(f"[vtt.art] sprite save failed: {e}")
        return None


_REMBG_SESSION = None


def _fit_to_square(im, *, margin: float = 1.14, max_zoom: float = 3.0):
    """Re-centre and re-scale a matted sprite to fill its square.

    Told to leave a clear margin, the model sometimes leaves a very generous
    one and paints a crate occupying a sixth of the frame in one corner. On a
    battlemap that lands as a speck in the middle of a five-foot square, which
    is indistinguishable from nothing at all — and it is not a prompt problem,
    because how big the model draws its subject is not reliably steerable.

    The matte already knows exactly where the subject is, so use it: crop to
    the alpha's bounding box, square it off with a little breathing room, and
    scale back. Capped, because blowing a genuinely tiny render up to full size
    is mush rather than a sprite.
    """
    n = im.size[0]
    box = im.getchannel("A").point(lambda v: 255 if v > 40 else 0).getbbox()
    if not box:
        return im
    x0, y0, x1, y1 = box
    side = max(x1 - x0, y1 - y0) * margin
    side = max(side, n / max_zoom)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    crop = (int(cx - side / 2), int(cy - side / 2),
            int(cx + side / 2), int(cy + side / 2))
    if side >= n * 0.95:
        return im                       # already fills the frame; leave it be
    from PIL import Image as _Image
    return im.crop(crop).resize((n, n), _Image.LANCZOS)


def _harden_alpha(alpha, *, lo: int = 48, hi: int = 176):
    """Push a soft matte to a decisive one — mostly there, or not there at all.

    u2netp is happy to answer "about half" for every pixel of a sprite it isn't
    sure about, and a half-transparent crate on a battlemap doesn't read
    as a crate; it reads as a smudge on the floor. The check in :func:`cutout`
    catches a matte that kept nothing or everything, but not this — a matte
    that kept the right SHAPE at the wrong strength. A contrast curve fixes the
    strength without touching the shape.
    """
    return alpha.point(lambda v: 0 if v < lo else
                       (255 if v >= hi else int(255 * (v - lo) / (hi - lo))))


def _soften_corners(alpha, *, hold: float = 0.86):
    """Fade a sprite's alpha to nothing at the very edge of its square.

    Matting works on contrast, and a battlemap sprite is often the same stone,
    in the same light, as the ground it was painted on. When rembg can't tell
    them apart it keeps the background, and the sprite lands on the board as a
    pale BOX with a pillar in it — the join is more visible than the pillar.

    A radial falloff over the outer sliver costs nothing on a clean cut-out
    (which is already transparent out there) and dissolves the box on a bad
    one. It is a floor under the matting, not a replacement for it.
    """
    from PIL import Image as _Image, ImageChops as _ImageChops, ImageDraw as _ImageDraw
    n = alpha.size[0]
    mask = _Image.new("L", (n, n), 0)
    md = _ImageDraw.Draw(mask)
    steps = 24
    # SQUARE falloff, not radial. A crate lid and a door panel are square and a
    # circular vignette would clip their corners off — the sprite is a square
    # of board, and only its outermost sliver is in doubt.
    for i in range(steps, 0, -1):
        t = i / float(steps)                       # 1.0 = the full square
        inset = (n / 2.0) * (1.0 - t)
        v = 255 if t <= hold else int(255 * (1.0 - (t - hold) / (1.0 - hold)))
        md.rectangle([inset, inset, n - 1 - inset, n - 1 - inset], fill=v)
    return _ImageChops.multiply(alpha, mask)


#: Matted sprites, keyed by stored image id. A sprite's picture never changes
#: once it is in the store, so this only ever grows to the size of the
#: catalogue — and matting the same pillar for every board redraw, in every
#: view, would be pure waste.
_SPRITE_CACHE: dict[int, Optional[bytes]] = {}


def sprite_png(image_id: int, raw_bytes: bytes) -> Optional[bytes]:
    """A stored sprite, matted and fitted, as PNG bytes. Memoised by id.

    The one place a board sprite is prepared for drawing, whichever view is
    asking. The Discord PNG composites in-process and the Activity fetches it
    over HTTP, but a pillar has to be the same cut-out picture in both or the
    two boards disagree about the room — which is the whole reason the grid,
    not the art, is the truth.
    """
    if image_id in _SPRITE_CACHE:
        return _SPRITE_CACHE[image_id]
    out = cutout(raw_bytes) if raw_bytes else None
    _SPRITE_CACHE[image_id] = out
    return out


def object_ref(code: str) -> str:
    """The cache slug for an object sprite. One picture per KIND of thing."""
    from .terrain import tile
    return f"object-v{SPRITE_REV}-{tile(code).name.replace(' ', '-')}"


def debris_ref(becomes: str, material: str = "", was: str = "") -> str:
    """The cache slug for a wreckage sprite.

    ``was`` is part of the key, and has to be. Keyed on (becomes, material)
    alone, every stone thing on the board — pillar, altar, low wall, wall, rock
    face — shared one bucket, so whichever broke first supplied the picture for
    all of them ever after. That is why a smashed crate came back looking like
    a smashed wall. Wreckage is still SHARED, just along the right seam: one
    picture per (what broke, what it left, its material, the board's look).
    """
    from .terrain import tile
    parts = ["debris", f"v{SPRITE_REV}", tile(becomes).name.replace(" ", "-"),
             (material or "any")]
    if was:
        parts.append(was.replace(" ", "-"))
    return "-".join(parts)


def render_object(code: str, *, store=None, context: str = "",
                  size_px: int = DEBRIS_RENDER_PX) -> Optional[int]:
    """A top-down sprite of a discrete object, for the square it stands on.

    Same economics as wreckage: keyed by (what it is, the board's look), so
    every pillar in every underground room is one picture. This is the half
    that makes destruction legible — you cannot recognise rubble as a broken
    pillar unless the pillar was visibly there first.
    """
    from .terrain import SPRITE_NEGATIVE, sprite_subject, tile
    subject = sprite_subject(code)
    if not subject:
        return None
    if store is None:
        try:
            from imagery import ImageStore
            store = ImageStore()
        except Exception as e:
            print(f"[vtt.art] imagery unavailable for object sprite: {e}")
            return None
    try:
        res = store.ensure_image(
            "map", subject, look=tile(code).art, context=context or "object",
            ref_slug=object_ref(code),
            extra=_MAP_STYLE + _SPRITE_FRAMING,
            negative_extra=SPRITE_NEGATIVE,
            width=size_px, height=size_px, store_width=size_px,
            max_per_bucket=1)
    except Exception as e:
        print(f"[vtt.art] object sprite render failed: {e}")
        return None
    if res is None or res.offline or not res.image_id:
        return None
    return res.image_id


def render_debris(becomes: str, *, store=None, material: str = "",
                  context: str = "", was: str = "",
                  size_px: int = DEBRIS_RENDER_PX) -> Optional[int]:
    """A small top-down sprite of what a broken thing left behind.

    Shared, not per-map: rubble keyed by (what it is, what it was, the board's
    look) is the same picture in every room that looks the same, so the first
    smashed pillar pays for every smashed pillar after it. That is the item-art
    economics lesson applied to wreckage.

    Returns an ``entity_image`` id, or None when art is unavailable — the board
    simply shows the changed TILE in that case, which is already correct.
    """
    if store is None:
        try:
            from imagery import ImageStore
            store = ImageStore()
        except Exception as e:
            print(f"[vtt.art] imagery unavailable for debris: {e}")
            return None
    from .terrain import SPRITE_NEGATIVE, tile
    left = tile(becomes)
    # Name the WRECK of a specific thing, not "rubble". Debris that reads as
    # generic gravel is debris that "came from nowhere" — the sprite's whole
    # job is to look like the pillar that used to stand there. Asking for a
    # DARK wreck matters as much as asking for the right one: a pale heap on a
    # pale flagstone floor is invisible whatever it depicts, and the board's
    # own outline round the square is then doing all the work.
    subject = (f"a smashed broken {was or 'object'} collapsed into "
               f"{left.art}, its large pieces still recognisable as a "
               f"{was or 'thing'}, dark and heavily shadowed")
    look = ", ".join(p for p in (material, left.art,
                                 "high contrast against a pale floor, "
                                 "deep shadow between the pieces") if p)
    try:
        res = store.ensure_image(
            "map", subject, look=look, context=context or "wreckage",
            ref_slug=debris_ref(becomes, material, was),
            extra=_MAP_STYLE + _SPRITE_FRAMING,
            negative_extra=SPRITE_NEGATIVE,
            width=size_px, height=size_px, store_width=size_px,
            max_per_bucket=1)
    except Exception as e:
        print(f"[vtt.art] debris render failed: {e}")
        return None
    if res is None or res.offline or not res.image_id:
        return None
    return res.image_id


# ---------------------------------------------------------------------------
# Control images: making the painting obey the grid
# ---------------------------------------------------------------------------
#
# A text prompt cannot say WHERE a wall goes. Told "a dungeon room with stone
# walls and carved pillars", the model paints a plausible room — and its walls
# land nowhere near the grid's walls, so the picture and the rules describe
# different places. That is not a tuning problem; no wording fixes it.
#
# So the layout is drawn as a picture and handed to ControlNet. A scribble
# model turns a line drawing into art that FOLLOWS the lines, which is exactly
# the job: the grid draws its own floorplan, and the painting is conditioned on
# it. What comes back has walls where the rules have walls.

#: Tiles that must appear as STRUCTURE in the control image. Everything else is
#: floor as far as the layout is concerned — discrete objects are drawn as
#: sprites afterwards, because they have to change when they break.
_STRUCTURAL = {"#", "R", "+", "p", "w"}


def control_image(grid: Grid, *, px_per_square: int = 32,
                  line_px: int = 0) -> bytes:
    """Draw the layout as a floorplan for ControlNet to follow. PNG bytes.

    An architectural line drawing, white on black: a stroke wherever open floor
    meets solid structure — which is to say, along every wall FACE. Filling the
    wall squares instead was the first attempt and it reads as a silhouette,
    with a solid ring round the map and a black hole where the room is; what a
    scribble model wants is the sketch a person would draw.

    Apertures (doors, portcullises) leave a GAP in the run, so the model paints
    an opening there rather than an unbroken wall — and the gap is drawn the way
    an architect draws one: two jamb ticks across the wall's thickness, and the
    passage itself left open. Without that second half a door at the edge of the
    board got a stroke along its outer face, sealing the very opening it is.

    Discrete objects are deliberately absent: they are drawn afterwards as
    sprites, because a pillar that can be smashed has to be able to change.
    """
    from PIL import Image, ImageDraw

    from .terrain import APERTURES, aperture_axis

    w = max(1, grid.width) * px_per_square
    h = max(1, grid.height) * px_per_square
    img = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(img)
    line_px = line_px or max(2, px_per_square // 8)

    def solid(x, y) -> bool:
        """Structure the drawing should show a wall face for."""
        if not grid.in_bounds(x, y):
            return True                       # the world ends: draw the edge
        return grid.get(x, y) in _STRUCTURAL and grid.get(x, y) not in APERTURES

    def open_floor(x, y) -> bool:
        if not grid.in_bounds(x, y):
            return False
        code = grid.get(x, y)
        return code not in _STRUCTURAL or code in APERTURES

    for x, y in grid.squares():
        if not open_floor(x, y):
            continue
        code = grid.get(x, y)
        x0, y0 = x * px_per_square, y * px_per_square
        x1, y1 = x0 + px_per_square - 1, y0 + px_per_square - 1
        faces = {"n": solid(x, y - 1), "s": solid(x, y + 1),
                 "w": solid(x - 1, y), "e": solid(x + 1, y)}
        if code in APERTURES:
            # An aperture keeps its JAMBS (the wall it interrupts, on either
            # side) and opens its PASSAGE (the way through). Off-axis, or at
            # the board's edge, the passage face would otherwise be drawn from
            # the out-of-bounds rule and quietly wall the doorway shut.
            axis = aperture_axis(grid, x, y)
            if axis == "ew":
                faces["w"] = faces["e"] = True
                faces["n"] = faces["s"] = False
            elif axis == "ns":
                faces["n"] = faces["s"] = True
                faces["w"] = faces["e"] = False
        # A stroke on each side of this floor square that abuts structure.
        if faces["n"]:
            d.line([x0, y0, x1, y0], fill=(255, 255, 255), width=line_px)
        if faces["s"]:
            d.line([x0, y1, x1, y1], fill=(255, 255, 255), width=line_px)
        if faces["w"]:
            d.line([x0, y0, x0, y1], fill=(255, 255, 255), width=line_px)
        if faces["e"]:
            d.line([x1, y0, x1, y1], fill=(255, 255, 255), width=line_px)

    import io as _io
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
