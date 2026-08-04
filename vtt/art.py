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
from dataclasses import dataclass
from typing import Optional

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


def cutout(png_bytes: bytes, *, size_px: int = DEBRIS_PX) -> Optional[bytes]:
    """Cut a sprite free of its background and downscale it. None on failure.

    A diffusion model always paints something behind the subject, and a sprite
    dropped on a battlemap has to be the subject ALONE — otherwise it reads as
    a picture stuck on the floor rather than debris lying on it. rembg does the
    matting; the downscale to a stored size happens after, because the model
    draws a sharper 512 than it draws a 256.

    Optional: with rembg absent this returns None and the caller keeps the
    uncut render, which still works (feathered) but reads less cleanly.
    """
    try:
        import io as _io
        from PIL import Image as _Image
        from rembg import remove, new_session
    except Exception as e:
        print(f"[vtt.art] no background removal available ({e}); "
              "keeping the sprite as rendered")
        return None
    try:
        global _REMBG_SESSION
        if _REMBG_SESSION is None:
            # u2netp: the small model. A pile of rubble does not need the big
            # one, and this keeps the first call from stalling a turn.
            _REMBG_SESSION = new_session("u2netp")
        cut = remove(png_bytes, session=_REMBG_SESSION)
        im = _Image.open(_io.BytesIO(cut)).convert("RGBA")
        im = im.resize((size_px, size_px), _Image.LANCZOS)
        buf = _io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        print(f"[vtt.art] background removal failed: {e}")
        return None


_REMBG_SESSION = None


def render_object(code: str, *, store=None, context: str = "",
                  size_px: int = DEBRIS_RENDER_PX) -> Optional[int]:
    """A top-down sprite of a discrete object, for the square it stands on.

    Same economics as wreckage: keyed by (what it is, the board's look), so
    every pillar in every underground room is one picture. This is the half
    that makes destruction legible — you cannot recognise rubble as a broken
    pillar unless the pillar was visibly there first.
    """
    from .terrain import sprite_subject, tile
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
            ref_slug=f"object-{tile(code).name.replace(' ', '-')}",
            extra=(_MAP_STYLE + ", a single object seen from directly overhead, "
                   "centred and isolated, filling the frame, on plain ground, "
                   "no walls, no room, no scene, no border"),
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
    from .terrain import tile
    left = tile(becomes)
    subject = f"{left.art}, the remains of a destroyed {was or 'object'}"
    look = ", ".join(p for p in (material, left.art) if p)
    # Keyed on the tile's NAME, never its code: codes are punctuation ("/", ",",
    # "#") and the store's slugifier strips punctuation, so every one of them
    # collapsed to the same bucket — a smashed door and a smashed crate shared
    # one picture.
    try:
        res = store.ensure_image(
            "map", subject, look=look, context=context or "wreckage",
            ref_slug=f"debris-{left.name.replace(' ', '-')}-{(material or 'any')}",
            extra=(_MAP_STYLE + ", a single small heap of broken debris lying "
                   "on bare ground, seen from directly overhead, centred and "
                   "isolated, filling the frame, no walls, no room, no scene, "
                   "no border"),
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
#: Drawn as open gaps in a wall run, so the model paints a doorway there.
_APERTURE = {"+", "/", "p"}


def control_image(grid: Grid, *, px_per_square: int = 32,
                  line_px: int = 0) -> bytes:
    """Draw the layout as a floorplan for ControlNet to follow. PNG bytes.

    An architectural line drawing, white on black: a stroke wherever open floor
    meets solid structure — which is to say, along every wall FACE. Filling the
    wall squares instead was the first attempt and it reads as a silhouette,
    with a solid ring round the map and a black hole where the room is; what a
    scribble model wants is the sketch a person would draw.

    Apertures (doors, portcullises) leave a GAP in the run, so the model paints
    an opening there rather than an unbroken wall. Discrete objects are
    deliberately absent: they are drawn afterwards as sprites, because a pillar
    that can be smashed has to be able to change.
    """
    from PIL import Image, ImageDraw

    w = max(1, grid.width) * px_per_square
    h = max(1, grid.height) * px_per_square
    img = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(img)
    line_px = line_px or max(2, px_per_square // 8)

    def solid(x, y) -> bool:
        """Structure the drawing should show a wall face for."""
        if not grid.in_bounds(x, y):
            return True                       # the world ends: draw the edge
        return grid.get(x, y) in _STRUCTURAL and grid.get(x, y) not in _APERTURE

    def open_floor(x, y) -> bool:
        if not grid.in_bounds(x, y):
            return False
        code = grid.get(x, y)
        return code not in _STRUCTURAL or code in _APERTURE

    for x, y in grid.squares():
        if not open_floor(x, y):
            continue
        x0, y0 = x * px_per_square, y * px_per_square
        x1, y1 = x0 + px_per_square - 1, y0 + px_per_square - 1
        # A stroke on each side of this floor square that abuts structure.
        if solid(x, y - 1):
            d.line([x0, y0, x1, y0], fill=(255, 255, 255), width=line_px)
        if solid(x, y + 1):
            d.line([x0, y1, x1, y1], fill=(255, 255, 255), width=line_px)
        if solid(x - 1, y):
            d.line([x0, y0, x0, y1], fill=(255, 255, 255), width=line_px)
        if solid(x + 1, y):
            d.line([x1, y0, x1, y1], fill=(255, 255, 255), width=line_px)

    import io as _io
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
