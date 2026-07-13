"""Generate The Oracle's multi-resolution .ico from the source artwork.

Usage:
    python make_icon.py [source_image] [output.ico]

Defaults to the D&D picture in the user's OneDrive and writes oracle.ico
next to this script.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

DEFAULT_SRC = Path(
    r"C:\Users\holyp\OneDrive\Pictures\D&DPics\d4ce32f98c869b34c4b0c80f6c36fe46.webp"
)
DEFAULT_OUT = Path(__file__).resolve().parent / "oracle.ico"

# Windows icon sizes (the "downsized" versions of the picture).
ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def make_icon(src: Path, out: Path) -> None:
    img = Image.open(src).convert("RGBA")

    # Crop to a centered square so the icon isn't stretched.
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))

    img.save(out, format="ICO", sizes=ICON_SIZES)
    print(f"Wrote {out} ({', '.join(f'{s[0]}px' for s in ICON_SIZES)})")


if __name__ == "__main__":
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    make_icon(source, output)
