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
#: Rev 3: the house art direction came off the swatch — see MATERIAL_STYLE_PROMPT.
MATERIAL_REV = 3

#: Materials whose look does not depend on the room around them.
#:
#: Lava is lava in a cavern and in a ruin; a floor is not. Keeping these out of
#: the per-look cross product is the `debris_ref` lesson applied to surfaces —
#: key on the axes that actually change the picture and no others.
LOOK_AGNOSTIC: frozenset[str] = frozenset({
    "~", "W", "l", "x", "i", "%", "m", "f",
    # DECK PLANKING, SAND AND BARE ROCK joined the list once the swatches were
    # measured rather than glanced at. The look reaches the prompt as the
    # literal words "in {look}" — a SCENE instruction — and against a subject
    # that names a made thing standing on ground, the model paints the join:
    # `b` in the snow look came back as a corner of decking meeting a
    # snowfield along a diagonal, `s` in the wetland look as a pond in a sand
    # frame, `R` in the snow look as a rock ledge with drifts on it. Tiled,
    # each of those is a lattice of boundaries.
    #
    # They belong here on the merit anyway, by this module's own sentence: a
    # plank deck is a plank deck in a cavern and in a snowfield, and the room
    # it stands in is the board's LIGHTING job rather than the swatch's. It
    # takes 33 swatches out of the catalogue.
    "b", "s", "R",
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
    # `bark` USED TO BE HERE AND NOTHING RENDERED IT: a tree is FOLIAGE (see
    # above), and no tile or skin names bark as its substance, so the entry was
    # a prompt nobody could reach — and it was a prompt with the same fault as
    # the two below it, which is how it was found.
    #
    # WOOD AND IRON BOTH BROKE THE RULE WRITTEN DIRECTLY ABOVE THEM, and
    # a player found the first of them: the crates and tables on a street were
    # "basically the same colour as the road". Measured, the wood swatch
    # averaged (116,133,121) — a grey-GREEN — against cobbles at (112,121,119),
    # which is a difference of thirteen out of 255 between a square that gives
    # half cover and the road it stands on. Bark came back (53,58,50), a
    # near-black grey; iron came back steel BLUE. Named grain, named pitting,
    # named no colour, and the sampler picked one.
    # PINE, and specifically not oak: crates, doors and furniture are the
    # cheap timber, and `taproom-boards` is already a dark waxed oak floor. Two
    # different woods described as one wood come back the same colour, and a
    # crate standing on a taproom floor is then the same complaint one room
    # over from the street.
    # ...AND "resinous yellow-white" OVERSHOT INTO LEMON. Naming a hue stops
    # the sampler inventing one; it does not bound how hard it lays it on. This
    # came back (182,151,60) — blue at a third of red, which is mustard, not
    # pine — and since a crate is the commonest object in the game it was 4.5%
    # of a dungeon board and 3.4% of a street, in gold. The direction was right
    # and the distance was not, which is the failure mode a warm-side
    # measurement cannot see: `--palette` only fails a COOL drift, so this sat
    # at -77 and passed. Pale is now said twice and the hue is named as a
    # blush rather than as the colour of the wood.
    # The MINIMAL edit, on the granite lesson: keep the sentence that is
    # already producing a surface and change only the colour word. The first
    # attempt rewrote the clause as well ("with only a faint warm blush to it")
    # and came back as a planked panel inside a floral border — 1.40 on
    # `--surface`, which is the composition `MATERIAL_NEGATIVE` has forbidden
    # in as many words since it was written. A negative is a nudge.
    # "BOARDS" IS A PANEL OF BOARDS, and a panel wants a frame: two redraws
    # running came back as planking inside an ornate border, the composition
    # `MATERIAL_NEGATIVE` has forbidden in as many words since it was written
    # and one that `--surface` scores under its line because the grain supplies
    # plenty of high-frequency detail. The `u` lesson one noun over — "step
    # treads" meant an elevation and drew a flight of stairs — so this opens
    # the way the swatches that have never framed open (`canvas`,
    # `spar-timber`, `plaster-timber`): a flat EXPANSE, which is a quantity of
    # stuff and not a made object.
    "wood": "a flat expanse of pale sawn pine, close straight grain all "
            "running one way, pale creamy tan softwood with dark knots",
    "iron": "dark pitted wrought iron, close-up of the bare metal, near-black "
            "with a warm rust bloom at the edges",
    # DARKER AND BLUER THAN TURF, deliberately. A canopy and a lawn are both
    # "green", and asked for as both they came back within eight of each other
    # — which is a tree that gives THREE-QUARTERS cover and reads as grass. A
    # canopy really is the darker of the two: it is deep in its own shadow
    # between the leaves, where turf is lit all over.
    "foliage": "dense leaf canopy seen from directly above, close-up of the "
               "leaves themselves, deep blue-green in heavy shade with "
               "near-black gaps between the crowns",
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
    # `#` IS THE COMMONEST TILE ON THE BOARD AND HAD NO ENTRY — the third of
    # four instances of the fault `R` and `m` are annotated with below (`,` is
    # the fourth), and by far the most expensive of them, because it fell
    # through to `tile("#").art`: the two
    # words "stone wall", written for a battlemap prompt where a whole scene
    # carries them. Measured across the catalogue's thirteen looks it came back
    # at (84,106,104) — a cold teal grey — while `dressed-stone`, which is the
    # SAME MATERIAL in the fiction and does name its colour, sits at
    # (153,142,127). Forty-eight points of cast between a wall and the ashlar
    # it is built of. Named into the same warm family; the look still shifts it
    # (`in dungeon` against `in desert`), it just no longer picks the hue.
    "#": "close-up of a rough stone wall, undressed blocks in washed-out "
         "mortar, warm pale grey stone with buff and ochre weathering",
    # Rubble is what fell off the wall, so it is the wall's colour or the two
    # read as different rock lying against each other. It had no entry either
    # and came back (95,106,105), cool, against a floor at (92,95,85).
    ",": "close-up of a bed of loose broken stone, many small angular "
         "fragments and grit of mixed sizes filling the whole frame evenly, "
         "warm pale grey and buff, dry and dusty",
    "=": "close-up of a cobbled road surface, the cobbles themselves, "
         "warm grey-brown granite setts, dusty",
    "b": "close-up of weathered wooden deck planking",
    # WARM, and named — `u` said only "worn stone step treads" and came back
    # a cold grey that sat 12 from the blue-grey granite standing on it and 21
    # from a ruin's masonry. A tread is the one stone on a board that has been
    # rubbed by feet, so warm and polished is what it should look like anyway.
    # NOT "step treads": that is a noun that means an elevation, and it drew
    # four flights of steps in perspective — one of them with a man's legs in
    # it — while the view was only ever asked for in the negative prompt.
    "u": "close-up of worn stone paving, warm buff limestone rubbed smooth "
         "and hollowed by feet",
    # `R` had no entry and fell through to its tile art — the two words "rough
    # rock face" — which is the under-specified frame this table exists for. It
    # came back as a dark pool in a ring of slabs: handed almost nothing, the
    # model composes.
    "R": "close-up of bare rock, rough grey-brown stone broken into angular "
         "facets, dry and spotted with lichen",
    "g": "close-up of grass turf, bright sunlit yellow-green blades",
    # `m` had no entry, so it fell through to the tile's own `art` — the words
    # "sucking mud", which name no colour and no surface. It came back GREEN
    # (67,84,53), which is within thirty of the grass beside it.
    "m": "close-up of thick wet mud, churned and rutted all over, dark "
         "grey-brown ooze",
    "s": "close-up of rippled packed sand",
    # THE LAST FOUR THAT FELL THROUGH TO A TILE'S OWN `art`, and the same fault
    # as `R`, `m`, `#` and `,` above: two or three words written for a
    # battlemap prompt, where a whole scene carries them, standing alone as the
    # entire subject of a swatch.
    #
    # A WATER SQUARE'S SWATCH IS THE BED, NOT THE SURFACE. The board draws the
    # water itself as a separate translucent sheet in its own colour
    # (`vttScene3d`'s WATER_TINT at 0.72, depth-write off) over the ground
    # underneath, so what this picture has to be is the thing you see THROUGH
    # that — and "shallow water" asks a diffusion model for a photograph of a
    # pond, which is a picture of a place with a far bank in it. The same
    # correction `seabed-shallow` already carries one level up.
    "~": "close-up of a clear streambed of wet pebbles and gravel, warm "
         "grey-brown stones with pale sand between them",
    "W": "close-up of a deep riverbed of dark silt and drowned stones, "
         "cold near-black brown, the detail fading into the dark",
    # Ice IS the surface here — you stand on it — so this one is the exception
    # that names the water and not the bed. Cold is correct and is now SAID,
    # which is what the guard is asking for: the exemption is granted by naming
    # the hue, never by an allowlist.
    "i": "close-up of thick cracked lake ice, pale blue-white with white "
         "fracture lines and frozen bubbles",
    '"': "close-up of dense low undergrowth and brambles seen from above, "
         "deep olive and forest green leaves with dark woody stems",
}

#: The style a swatch is painted in. Deliberately NOT ``_MAP_STYLE``, which
#: says "battlemap" — the one word this render must not hear.
#:
#: "Seamless" is asked for and not relied on: SDXL does not tile without help.
#: It earns its place anyway because it biases toward flat even coverage with
#: nothing composed in the middle, which is what a swatch needs. The board
#: handles the rest by giving each square its own copy and varying the UVs, so
#: an imperfect edge reads as grout between flagstones rather than as a seam.
#:
#: THE VIEW IS SAID IN THE POSITIVE, and it leads. The same lesson the wreckage
#: sprites already carry — "name the VIEW before the thing, because the model's
#: prior for any object is its ELEVATION and a trailing modifier loses to it" —
#: and it had never been applied here, where the negative prompt was left to
#: carry it alone. It cannot: `MATERIAL_NEGATIVE` has forbidden perspective,
#: horizons and vanishing points since it was written, and the catalogue still
#: held a landscape with a sky in it (grass), a snowy roof with icicles
#: (bridge), breaking waves (open sea), and a flight of steps with A PERSON'S
#: LEGS walking down it (stairs). A negative is a nudge; the subject noun is
#: the instruction, and "stair treads" means an elevation to a model unless
#: something says otherwise first.
#: ONE MATERIAL, EDGE TO EDGE is the other half, and it is what the LOOK
#: makes necessary. A swatch is rendered per (subject, look), and the look's
#: own words are a second material standing next to the first: "deck planking"
#: in the SNOW look came back as a corner of decking meeting a snowfield along
#: a diagonal, which is a picture of a boundary and tiles into a lattice of
#: them. Sand in the wetland look came back as a pond, the open sea as
#: breaking waves, sludge as a green medallion in a ring of moss. Saying the
#: frame holds one substance is what stops the look becoming a SCENE.
_MATERIAL_STYLE = (
    "seen from directly overhead looking straight down, flat-on surface, "
    "one single material filling the entire frame edge to edge, "
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


#: The house art direction, MINUS the half of it that is about a PICTURE.
#:
#: `ImageStore` appends `cfg.style_prompt` to every render, and it is written
#: for the thing most renders are — a character, a place, an item seen in
#: dramatic light. On a swatch four of its clauses are not merely unhelpful,
#: they are the exact opposites of what `MATERIAL_NEGATIVE` is asking for IN
#: THE SAME RENDER:
#:
#:   "dynamic composition"          vs  negative: composition, focal point
#:   "ornate engraved details"      vs  negative: border, ornate border,
#:                                      corner ornament, medallion, cartouche
#:   "high contrast dramatic rim
#:    lighting"                     vs  negative: dramatic lighting, spotlight,
#:                                      strong directional shadow
#:   "saturated jewel tones"        vs  the whole palette rule, and the reason
#:                                      the catalogue over-saturates
#:
#: The positive and the negative of one render contradicting each other is a
#: fight the POSITIVE wins — this file already says so about the view, and it
#: is why `wood` came back three redraws running as planking inside an ornate
#: border while "no border, no frame, no ornament" sat in its negative.
#:
#: This was also the hole in the style probe. `scripts/material_style_probe.py`
#: swept `_MATERIAL_STYLE` and the LoRA stack across nine configurations and
#: every one of them still carried this, which is why its `nostyle` column —
#: no LoRAs at all — came back MORE saturated rather than less. The house
#: direction was the constant nobody varied.
#:
#: What is KEPT is the half that is about the HAND: painterly, and the bold ink
#: line that is this game's signature and reads correctly on a flat sample of
#: stone. What goes is everything about light, composition, ornament and
#: saturation, because a swatch is lit, composed and graded by the BOARD.
#: AFFIRMATIVE ONLY. The first version ended "one continuous material, no
#: composition and no focal point", which restates two clauses the MATERIAL
#: kind framing in `imagery/prompt_build.py` already carries AND puts them
#: where they are weakest — a negation in a POSITIVE prompt, which this file
#: says elsewhere is a nudge at best. What a swatch must not be belongs in
#: `MATERIAL_NEGATIVE`. This says only what the hand is.
MATERIAL_STYLE_PROMPT = (
    "painterly digital illustration, bold ink outlines, flat even illumination"
)

#: The looks the catalogue is pre-rendered for. Kept in step with
#: scripts/material_prerender.py's CONTEXTS and debris_prerender.py's, so all
#: three agree about what a "dungeon" looks like.
BOARD_LOOKS: tuple[str, ...] = (
    "underground", "dungeon", "woodland", "town", "cavern", "ruins",
    "interior", "snow", "desert", "wetland", "sea", "sky",
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
    # A DECK IS NOT AN INTERIOR. Both vessels were filed under `interior`, so
    # the open sea around a caravel was drawn with an indoor water swatch (a
    # dark green, which painted as a lawn the ship sat on) and the prompt told
    # the model it was inside a building. A skin is look-agnostic, so the deck,
    # hull, rail and mast swatches are untouched by this — what it fixes is the
    # water, the sky and what the room is said to BE.
    "ship": "sea", "skyship": "sky", "arena": "town",
    "ruins": "ruins", "bridge": "town", "mountain-pass": "snow",
    "sky-islands": "woodland", "open": "woodland",
    # The one archetype that had no entry, and so was drawn as a DUNGEON:
    # stacked plateaus whose own description is "dry rock — flats of scree and
    # scrub", floored and rubbled out of a crypt. `desert` is the arid half of
    # what mountain-pass takes from `snow`; a DM who says the plateau is snowy
    # or wooded still outranks this, as they do for every other archetype here.
    "terraces": "desert",
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
    # After the interior words, so "the hall of a ship" is still a hall, and
    # before nothing — these are the last resort before the archetype.
    (("sea", "ocean", "open water", "deck", "shore"), "sea"),
    (("sky", "cloud", "aloft", "airship", "skyship"), "sky"),
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
# THE ISOMETRIC PAINTED BOARD WAS HERE, AND IT IS GONE ON PURPOSE.
#
# A painting is a photograph of the room from ONE place. The board's camera
# turns a full 360 now, and no transform makes a picture taken at 45 degrees
# into a picture taken at 137 — so the painted layer could only ever be seen
# in a narrow cone, and every board a player saw while actually using the
# rotation was the geometry underneath it. The two features were in direct
# conflict and the camera won.
#
# What went with it: the depth and segmentation conditioning, the regional
# prompts, the terrain init image, the underwater grade, and `vtt/isocam.py`
# — a whole second implementation of the board's geometry in Python, which
# existed only so a ControlNet could be handed a picture of the same room.
#
# The TOP-DOWN battlemap above is NOT this and stays: a Discord table has no
# camera to turn, so a painted picture is the whole of what it looks at.

#: Codes drawn as full-square STRUCTURE rather than as an object standing on a
#: square. It lived beside the depth rasterizer, because the depth map and the
#: geometry had to be the same room; the rasterizer is gone and this is now
#: what it always really was — shape data, read by `gen_board_shapes.py` and
#: mirrored into `activity-ui/src/lib/boardShapes.generated.ts`.
STRUCTURE_CODES = {"#", "R"}


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
            # The house direction is for pictures. See MATERIAL_STYLE_PROMPT.
            style_prompt=MATERIAL_STYLE_PROMPT,
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
