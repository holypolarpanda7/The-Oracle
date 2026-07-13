"""Compress generated images to WebP for compact DB storage.

Raw diffusion output (PNG, ~1-3 MB at 1024px) is downscaled and re-encoded to
WebP so a handful of pictures per subject stay small inside SQLite.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

from PIL import Image


@dataclass
class EncodedImage:
    data: bytes           # full-size WebP bytes
    thumb: bytes          # thumbnail WebP bytes
    width: int
    height: int
    byte_size: int


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "P", "LA"):
        # Flatten transparency onto black; scene art is opaque anyway.
        background = Image.new("RGB", img.size, (0, 0, 0))
        img = img.convert("RGBA")
        background.paste(img, mask=img.split()[-1])
        return background
    return img.convert("RGB")


def _resize_to_width(img: Image.Image, target_w: int) -> Image.Image:
    if target_w <= 0 or img.width <= target_w:
        return img
    ratio = target_w / float(img.width)
    target_h = max(1, int(round(img.height * ratio)))
    return img.resize((target_w, target_h), Image.LANCZOS)


def encode_webp(
    raw: bytes,
    *,
    store_width: int = 768,
    thumb_width: int = 256,
    quality: int = 82,
) -> EncodedImage:
    """Downscale + WebP-encode raw image bytes into a full + thumbnail pair."""
    with Image.open(io.BytesIO(raw)) as im:
        im.load()
        base = _to_rgb(im)

    full = _resize_to_width(base, store_width)
    full_buf = io.BytesIO()
    full.save(full_buf, format="WEBP", quality=quality, method=6)
    full_bytes = full_buf.getvalue()

    thumb = _resize_to_width(base, thumb_width)
    thumb_buf = io.BytesIO()
    thumb.save(thumb_buf, format="WEBP", quality=max(60, quality - 12), method=6)

    return EncodedImage(
        data=full_bytes,
        thumb=thumb_buf.getvalue(),
        width=full.width,
        height=full.height,
        byte_size=len(full_bytes),
    )


def make_placeholder(text: str = "image service offline",
                     size: tuple[int, int] = (768, 512)) -> bytes:
    """A plain WebP placeholder used when the diffusion backend is unavailable."""
    img = Image.new("RGB", size, (28, 24, 38))
    if text:
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)
        # Default bitmap font: no font files to ship, good enough for a notice.
        tw = draw.textlength(text)
        draw.text(((size[0] - tw) / 2, size[1] / 2 - 6), text,
                  fill=(140, 130, 160))
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=70, method=4)
    return buf.getvalue()
