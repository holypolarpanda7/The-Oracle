"""Saying a different thing about each part of the board.

The other half of the "what is this square" problem, and the half a fixed class
vocabulary cannot reach. A segmentation map (see :mod:`vtt.segmap`) is spatially
exact and speaks ADE20K's 150 nouns; this project's own vocabulary is a growing
catalogue of skins — ``field-stone``, ``broken-column``, ``chitin-rail``,
``taproom-post`` — and almost none of those are ADE20K classes. What they DO
have is a sentence each, written into ``Skin.words`` and already saying exactly
the right thing.

Today every one of those sentences is concatenated into a single global clause
where they compete for the model's attention. That is why "square oak posts,
chamfered … they are TIMBER POSTS, not stone columns and not candles" does not
land: it is the twelfth clause in a paragraph about a room.

Regional prompting binds each sentence to the squares that own it — one mask
per skin, rasterized by the same camera as the depth map, so the words about
the posts are applied where the posts are and nowhere else.

**Where this is weak, and it is worth knowing before measuring.** Attention
masking works on the LATENT, which is a sixty-fourth of the pixels, and a mask
of eleven scattered single squares is a scattering of latent cells. It is
strongest on large contiguous regions — a floor, a wall run, the water — and
weakest on exactly the small scattered objects a seg map handles best. The two
methods fail on opposite axes, which is why both exist and why they are measured
apart before being measured together.
"""
from __future__ import annotations

from typing import Callable

from . import skins as _skins
from .mapgen import GeneratedMap

#: How hard a region's own words push, as a `ConditioningSetMask` strength.
#: Below 1.0 on purpose — a region is a modifier on a scene, not a scene of its
#: own, and at full strength each mask starts trying to compose its own picture.
REGION_STRENGTH = 0.85

#: A skin has to cover at least this fraction of the board to get its own
#: region. Under it the mask is a handful of latent cells that cost a text
#: encode and a conditioning branch to say nothing — its words stay in the
#: global clause, where they at least reach the model.
MIN_REGION_FRACTION = 0.012


def _white_where(target: str) -> Callable[[str, str], tuple[int, int, int]]:
    """Colour function painting one skin white and everything else black."""
    def colour(_code: str, skin: str) -> tuple[int, int, int]:
        return (255, 255, 255) if skin == target else (0, 0, 0)
    return colour


def region_mask(skin: str, **depth_kw) -> bytes:
    """A black-and-white PNG of the squares wearing this skin.

    Same rasterizer, same camera and the same kwargs as the depth map, so a
    region lands exactly on the geometry it is describing — and ``_flat``,
    because a mask must not be shaded any more than a class colour must.
    """
    from . import isocam

    return isocam.terrain_image(colour_of=_white_where(skin), _flat=True,
                               **depth_kw)


def region_share(gen: GeneratedMap, skin_of: Callable[[str, int, int], str],
                 ) -> dict[str, float]:
    """What fraction of the board's squares each present skin covers."""
    total = 0
    seen: dict[str, int] = {}
    for x, y in gen.grid.squares():
        code = gen.grid.get(x, y)
        total += 1
        name = skin_of(code, x, y)
        if name:
            seen[name] = seen.get(name, 0) + 1
    if not total:
        return {}
    return {k: v / total for k, v in seen.items()}


def regions_for(gen: GeneratedMap, *, strength: float = REGION_STRENGTH,
                minimum: float = MIN_REGION_FRACTION,
                **depth_kw) -> tuple[list[dict], list[str]]:
    """The regions worth prompting separately, and the skins left in the global.

    Takes the SAME kwargs the depth map is rasterized from — ``skin_of`` is one
    of them, so it is read out rather than passed twice; a caller that had to
    supply it separately could supply a different one, and then the masks would
    describe a board nobody is looking at.

    Returns ``([{words, mask, strength}], [skin names not regioned])``. The
    second half matters: a skin too small to be worth a region must keep its
    sentence in the concatenated clause, or making the prompt regional would
    quietly DELETE what it used to say about that material.
    """
    skin_of = depth_kw.get("skin_of")
    if skin_of is None:
        return [], []
    share = region_share(gen, skin_of)
    out: list[dict] = []
    rest: list[str] = []
    said: set[str] = set()
    for name, frac in sorted(share.items(), key=lambda kv: -kv[1]):
        sk = _skins.SKINS.get(name)
        words = (sk.words if sk else "") or ""
        if not words:
            continue
        # Several skins share one sentence (a reef's three drowned-stone
        # skins). Saying it twice in two regions is two encodes for one claim;
        # the first region to want it takes it.
        if frac < minimum or words in said:
            rest.append(name)
            continue
        mask = region_mask(name, **depth_kw)
        if not mask:
            rest.append(name)
            continue
        said.add(words)
        out.append({"words": words, "mask": mask, "strength": float(strength)})
    return out, rest


def setpiece_regions(gen: GeneratedMap, *, strength: float = REGION_STRENGTH,
                     **depth_kw) -> list[dict]:
    """A region for every LANDMARK on the board, carrying its own description.

    The case regional prompting is actually good at, and the mirror of where it
    is weak. A skin's squares are scattered across the board — eleven posts, a
    hundred floor tiles — and a mask of scattered single squares is a scattering
    of latent cells. A landmark is one CONTIGUOUS block that somebody chose to
    put there, and it is fighting no strong wrong prior: the model has no
    opinion about what belongs on that square until it is told.

    It is also the only channel a DM-described feature has. A gilded sow is one
    stamp of worked stone until something says what it is, and saying it in the
    shared prompt puts it in a paragraph about a room.
    """
    from . import setpieces as _sp

    out: list[dict] = []
    for inst in (getattr(gen, "setpieces", None) or []):
        pc = _sp.piece(str(inst.get("slug") or ""))
        words = (pc.words if pc else "").strip()
        if not words:
            continue
        x0, y0 = int(inst.get("x") or 0), int(inst.get("y") or 0)
        turned = _sp.turned_tiles(pc, int(inst.get("yaw") or 0)) if hasattr(
            _sp, "turned_tiles") else pc.tiles
        squares = {(x0 + dx, y0 + dy)
                   for dy, row in enumerate(turned)
                   for dx, _c in enumerate(row)}
        if not squares:
            continue
        mask = _mask_of(squares, **depth_kw)
        if mask:
            out.append({"words": words, "mask": mask, "strength": float(strength)})
    return out


def _mask_of(squares: set, **depth_kw) -> bytes:
    """A black-and-white PNG of an arbitrary set of squares.

    ``terrain_image``'s colour callback is per (code, skin) and knows nothing
    about WHERE it is, so a set of squares needs the coordinates threaded in —
    which the rasterizer does supply, one level down, as the square it is
    drawing. Rather than change that signature for one caller, the mask is
    drawn by asking for a board where only those squares carry a code at all.
    """
    from . import isocam

    rows = list(depth_kw.get("rows") or [])
    if not rows:
        return b""
    keep = []
    for y, row in enumerate(rows):
        keep.append("".join(c if (x, y) in squares else " "
                            for x, c in enumerate(row)))
    kw = {**depth_kw, "rows": keep}
    return isocam.terrain_image(
        colour_of=lambda _c, _s: (255, 255, 255), _flat=True, **kw)


def global_words(present: list[str], regioned: list[dict],
                 leftover: list[str]) -> str:
    """What the SHARED prompt should still say about materials.

    Everything a region did not take. With no regions this is exactly
    ``skins.words_for(present)`` — the behaviour every board had before — so
    turning regional prompting off is a true revert rather than a different
    prompt that happens to look similar.
    """
    if not regioned:
        return _skins.words_for(present)
    return _skins.words_for(leftover)
