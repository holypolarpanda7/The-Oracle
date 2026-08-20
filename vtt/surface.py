"""What a surface does to LIGHT, as opposed to what colour it is.

The board's swatches have always been albedo and nothing else: one diffusion
render of "dressed limestone", tiled over every square of it, lit by a
directional lamp and an ambient fill. That is a picture of stone laid flat on a
shape, and it is why the geometry reads as coloured cardboard however good the
swatch is — every face of every block returns exactly the same amount of light
for its orientation, so mortar courses, grain and pitting are painted ON the
surface rather than being surface. A wall a foot from a torch looks the same as
one across the room except for a multiplier.

Two things are missing and they come from different places.

**The bumps are in the picture already** and can be recovered from it. A normal
map derived from a swatch is an old technique with one classic way of being
wrong: an albedo photograph contains the LIGHTING it was shot under, so
luminance-as-height turns a shadow into a valley and bakes somebody else's sun
into the geometry. The fix is a HIGH-PASS — subtract a wide blur of the image
from itself, which removes exactly the slow gradients that shading lives in and
keeps the fast detail that mortar lines and grain live in. That is why
:func:`height_field` is not simply "convert to grey", and it is measurable:
after the pass, the low-frequency energy of the height field is near zero.

**How SHINY a thing is cannot be recovered from it at all**, and guessing from
contrast is how wet stone and dry stone come out identical. Roughness and
metalness are facts about what a square is MADE OF — which is what a skin
already is — so they are declared per substance and looked up here, defaulting
to the honest answer for an unknown material: rough, and not metal.

Every filter here WRAPS. The swatch is tiled with ``RepeatWrapping``, so a blur
that clamps at the edges leaves a visible seam every five feet, in a grid, over
the whole board. ``numpy.roll`` costs nothing and makes the seam impossible.

Derived, never stored beside the swatch — the :mod:`rules.components` doctrine
one layer down. A stored normal map and the picture it came from drift the
moment somebody re-renders the material.
"""
from __future__ import annotations

import io
from functools import lru_cache
from typing import Optional

#: How pronounced the recovered relief is. Tuned to be visible at the board's
#: fixed overhead camera without turning a flagstone floor into a rock garden;
#: a swatch is a five-foot square seen from thirty feet up, and relief that
#: reads well in a close-up material preview is grotesque at that distance.
DEFAULT_STRENGTH = 2.2

#: Size of the derived maps. Smaller than the swatch on purpose: a normal map
#: carries the same detail at half the resolution because it is a DERIVATIVE of
#: the image, and this is bytes over a socket to a Discord Activity.
DEFAULT_SIZE = 256

#: Fraction of the image the high-pass blur spans. A twelfth is wide enough to
#: contain any lighting gradient a swatch has and narrow enough to leave mortar
#: courses alone.
HIGHPASS_FRACTION = 1.0 / 12.0

#: (roughness, metalness) for a substance the catalogue knows. Only entries
#: that differ meaningfully from the default are worth writing down — the point
#: is not to describe every material, it is to stop wet things and metal things
#: being drawn as dry stone.
#:
#: Roughness runs 0 (a mirror) to 1 (chalk). Nothing here is a true mirror:
#: everything on a battlefield is dirty, and a roughness of 0 on a real board
#: reflects an environment that does not exist and comes back black.
SURFACE_PROPERTIES: dict[str, tuple[float, float]] = {
    # Wet, and the only things on a board that genuinely shine.
    "water": (0.10, 0.0),
    "seawater": (0.12, 0.0),
    "seabed": (0.72, 0.0),
    "ice": (0.18, 0.0),
    "blood": (0.30, 0.0),
    # Worked metal. Metalness is a switch, not a dial — a material either
    # conducts or it does not — so anything genuinely metal gets 1.0 and
    # everything else 0.0, and the LOOK is carried by roughness.
    "brass": (0.34, 1.0),
    "iron": (0.46, 1.0),
    "steel": (0.38, 1.0),
    "gold": (0.28, 1.0),
    "bronze": (0.40, 1.0),
    # Polished stone still catches a highlight; rough stone does not.
    "marble": (0.42, 0.0),
    "obsidian": (0.24, 0.0),
    "glass": (0.12, 0.0),
    # Organic, and the reason a default of "rough" is right: none of these
    # should ever hold a highlight.
    "foliage": (0.86, 0.0),
    "grass": (0.90, 0.0),
    "moss": (0.92, 0.0),
    "bark": (0.94, 0.0),
    "timber": (0.78, 0.0),
    "canvas": (0.92, 0.0),
    "thatch": (0.95, 0.0),
    "sand": (0.94, 0.0),
    "dirt": (0.95, 0.0),
    "mud": (0.62, 0.0),
    "coral": (0.70, 0.0),
    "chitin": (0.52, 0.0),
    "bone": (0.66, 0.0),
    # Fire and light emit rather than reflect; roughness is nearly irrelevant
    # but a low number would give lava a specular highlight it should not have.
    "lava": (0.80, 0.0),
}

#: Rough and not metal. Correct for stone, rubble, plaster, flagstones and
#: anything nobody has classified — which is most of the catalogue, and is the
#: reason the table above stays short.
DEFAULT_PROPERTIES = (0.84, 0.0)


def properties_for(substance: str) -> tuple[float, float]:
    """``(roughness, metalness)`` for a substance slug.

    Matched on the WHOLE slug first and then on its words, so ``wet-flagstone``
    and ``ship-timber`` find their material without either needing an entry.
    Word-boundary matching for the reason ``setpieces.landmark_for`` uses it:
    "brass" lives inside "embrasure".
    """
    s = (substance or "").strip().lower()
    if not s:
        return DEFAULT_PROPERTIES
    if s in SURFACE_PROPERTIES:
        return SURFACE_PROPERTIES[s]
    words = {w for w in s.replace("_", "-").split("-") if w}
    for key, val in SURFACE_PROPERTIES.items():
        if key in words:
            return val
    return DEFAULT_PROPERTIES


# --------------------------------------------------------------------------
# Recovering the relief
# --------------------------------------------------------------------------

def _wrap_blur(a, radius: int):
    """A separable box blur that WRAPS. Three passes ~ a gaussian.

    ``numpy.roll`` rather than a padded convolution, because the swatch is
    tiled: a blur that clamps at the edge darkens the last few pixels, and
    tiled at five feet a square that is a grid of visible seams.
    """
    import numpy as np

    if radius < 1:
        return a
    out = a
    k = 2 * radius + 1
    for _ in range(3):
        for axis in (0, 1):
            acc = np.zeros_like(out)
            for shift in range(-radius, radius + 1):
                acc += np.roll(out, shift, axis=axis)
            out = acc / k
    return out


def height_field(image_bytes: bytes, *, size_px: int = DEFAULT_SIZE):
    """A tiling height field from a swatch: fine detail only, mean zero.

    The high-pass is the whole method. Luminance alone is not height — a
    swatch's luminance is albedo TIMES the lighting it was rendered under, and
    the lighting is all low-frequency, so subtracting a wide blur leaves the
    part that really is surface. What remains is signed and centred on zero,
    which is also what makes it safe to scale.
    """
    import numpy as np
    from PIL import Image

    im = (Image.open(io.BytesIO(image_bytes)).convert("L")
          .resize((size_px, size_px), Image.LANCZOS))
    a = np.asarray(im, dtype=np.float32) / 255.0
    lo = _wrap_blur(a, max(1, int(size_px * HIGHPASS_FRACTION)))
    h = a - lo
    peak = float(np.abs(h).max())
    return h / peak if peak > 1e-6 else h


def normal_map(image_bytes: bytes, *, size_px: int = DEFAULT_SIZE,
               strength: float = DEFAULT_STRENGTH) -> Optional[bytes]:
    """A tangent-space normal map derived from a swatch. PNG bytes, or None.

    Tangent space and OpenGL convention (+Y up), which is what three.js reads.
    The gradients are taken with wrapped differences for the tiling reason
    above, and the result is a unit vector per pixel — a normal map whose
    vectors are not unit length lights wrongly in a way that looks like the
    material being subtly the wrong colour.
    """
    try:
        import numpy as np
        from PIL import Image
    except Exception as e:                              # pragma: no cover
        print(f"[vtt.surface] numpy/Pillow unavailable: {e}")
        return None
    try:
        h = height_field(image_bytes, size_px=size_px)
    except Exception as e:
        print(f"[vtt.surface] unreadable swatch: {e}")
        return None
    # Central differences, wrapped. The sign puts a RIDGE where the swatch is
    # bright, which is the convention every albedo-derived map assumes.
    gx = (np.roll(h, -1, axis=1) - np.roll(h, 1, axis=1)) * 0.5 * strength
    gy = (np.roll(h, -1, axis=0) - np.roll(h, 1, axis=0)) * 0.5 * strength
    nx, ny, nz = -gx, gy, np.ones_like(h)
    length = np.sqrt(nx * nx + ny * ny + nz * nz)
    rgb = np.stack([(nx / length + 1.0) * 0.5,
                    (ny / length + 1.0) * 0.5,
                    (nz / length + 1.0) * 0.5], axis=-1)
    img = Image.fromarray((np.clip(rgb, 0.0, 1.0) * 255.0
                           + 0.5).astype("uint8"), mode="RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def roughness_map(image_bytes: bytes, substance: str = "", *,
                  size_px: int = DEFAULT_SIZE,
                  spread: float = 0.18) -> Optional[bytes]:
    """A roughness map: the substance's own value, varied by its detail.

    The BASE is declared, never guessed — see the module docstring. What the
    picture may honestly contribute is VARIATION: the same stone is smoother
    where it is worn and rougher where it is pitted, and that variation is
    exactly the fine detail the high-pass already isolated. So the map is the
    substance's roughness plus a small signed wobble, which keeps wet stone wet
    and stops every square of it holding an identical highlight.

    Greyscale: three.js reads the GREEN channel, and a single-channel PNG is a
    third of the bytes.
    """
    try:
        import numpy as np
        from PIL import Image
    except Exception as e:                              # pragma: no cover
        print(f"[vtt.surface] numpy/Pillow unavailable: {e}")
        return None
    base, _metal = properties_for(substance)
    try:
        h = height_field(image_bytes, size_px=size_px)
    except Exception as e:
        print(f"[vtt.surface] unreadable swatch: {e}")
        return None
    r = np.clip(base + h * spread, 0.03, 1.0)
    img = Image.fromarray((r * 255.0 + 0.5).astype("uint8"), mode="L")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# --------------------------------------------------------------------------
# Serving them
# --------------------------------------------------------------------------

_CACHE: dict[tuple[int, str, str], Optional[bytes]] = {}


def derived(image_id: int, channel: str, raw: bytes,
            substance: str = "") -> Optional[bytes]:
    """A derived channel for a stored swatch, memoised by (id, channel).

    Memoised for the reason ``art.sprite_png`` is: a stored swatch's picture
    never changes within a run, the derivation is a few numpy passes over a
    256x256, and both the Discord board and every Activity ask for the same
    handful of materials over and over.
    """
    key = (int(image_id), channel, substance or "")
    if key in _CACHE:
        return _CACHE[key]
    if channel == "normal":
        out = normal_map(raw)
    elif channel == "rough":
        out = roughness_map(raw, substance)
    else:
        out = None
    _CACHE[key] = out
    return out


@lru_cache(maxsize=1)
def channels() -> tuple[str, ...]:
    """What a client may ask for. An allowlist, because this is a URL."""
    return ("normal", "rough")
