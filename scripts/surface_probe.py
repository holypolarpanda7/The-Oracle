"""See what the derivation actually does to the board's REAL swatches.

`scripts/surface_smoke.py` proves the arithmetic against a synthetic swatch,
which is the right way to test a filter and is no help at all with the question
that matters: does a high-pass of an actual diffusion render of dressed
limestone leave mortar courses, or mush? A contact sheet is the only honest
answer to that, and it is the same reason `map_composite_check.py` and
`style_lora_probe.py` exist.

Three columns per material — the swatch as rendered, the normal map derived
from it, and the roughness map with its substance's own base — plus a LIT
render of the normal map under a raking light, because a normal map is
unreadable as a picture and perfectly readable as a surface.

    uv run python scripts/surface_probe.py [--limit 12] [--out DIR]

No GPU: everything here is numpy over pictures already in the database.
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
from PIL import Image, ImageDraw
from sqlmodel import Session, select

from imagery import ImageStore
from imagery.models import EntityImage
from vtt import surface as S

CELL = 220
PAD = 10
LABEL = 26


def lit(normal_png: bytes, rough_png: bytes, albedo: bytes) -> Image.Image:
    """The surface under a raking light — what the board will actually show.

    A raking light (low, from the left) is the standard way to read relief,
    and the point of the whole exercise: if this looks like flat paper the
    high-pass took too much, and if it looks like a rock garden the strength
    is too high for a five-foot square seen from thirty feet up.
    """
    n = np.asarray(Image.open(io.BytesIO(normal_png)).convert("RGB"),
                   dtype=np.float32) / 255.0 * 2.0 - 1.0
    r = np.asarray(Image.open(io.BytesIO(rough_png)).convert("L"),
                   dtype=np.float32) / 255.0
    a = np.asarray(Image.open(io.BytesIO(albedo)).convert("RGB")
                   .resize(n.shape[1::-1], Image.LANCZOS), dtype=np.float32) / 255.0
    L = np.array([-0.6, 0.35, 0.72], dtype=np.float32)
    L /= np.linalg.norm(L)
    ndl = np.clip((n * L).sum(-1), 0.0, 1.0)[..., None]
    # A cheap Blinn specular, narrowed by roughness. Not physically exact and
    # not pretending to be — it is here to make the difference between wet and
    # dry visible in a still image.
    V = np.array([0.0, 0.0, 1.0], dtype=np.float32)
    H = L + V
    H /= np.linalg.norm(H)
    ndh = np.clip((n * H).sum(-1), 0.0, 1.0)[..., None]
    shine = np.power(ndh, np.clip(2.0 / np.maximum(r[..., None], 0.03) ** 2, 2, 400))
    out = a * (0.25 + 0.85 * ndl) + shine * (1.0 - r[..., None]) * 0.6
    return Image.fromarray((np.clip(out, 0, 1) * 255).astype("uint8"), "RGB")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--out", default="style-probe/review")
    args = ap.parse_args()

    store = ImageStore()
    with Session(store.engine) as s:
        rows = list(s.exec(select(EntityImage)
                           .where(EntityImage.kind == "material")).all())
    rows.sort(key=lambda r: r.ref_slug or "")
    seen: dict[str, EntityImage] = {}
    for r in rows:
        seen.setdefault(r.ref_slug or str(r.id), r)
    picked = list(seen.values())[:args.limit]
    if not picked:
        print("no material swatches in the database yet")
        return 1

    cols = 4
    w = PAD + cols * (CELL + PAD)
    h = PAD + len(picked) * (CELL + PAD + LABEL)
    sheet = Image.new("RGB", (w, h), (18, 17, 22))
    draw = ImageDraw.Draw(sheet)
    draw.text((PAD, 2), "swatch    normal    roughness    lit (raking light)",
              fill=(210, 205, 190))

    for i, row in enumerate(picked):
        raw = store.get_image_bytes(row.id)
        if not raw:
            continue
        substance = (row.ref_slug or "").replace("material-v2-", "") \
                                        .replace("substance-", "")
        nm = S.normal_map(raw)
        rm = S.roughness_map(raw, substance)
        if not nm or not rm:
            print(f"  {substance}: no derivation")
            continue
        y = PAD + i * (CELL + PAD + LABEL) + LABEL
        cells = [Image.open(io.BytesIO(raw)).convert("RGB"),
                 Image.open(io.BytesIO(nm)).convert("RGB"),
                 Image.open(io.BytesIO(rm)).convert("RGB"),
                 lit(nm, rm, raw)]
        for c, im in enumerate(cells):
            sheet.paste(im.resize((CELL, CELL), Image.LANCZOS),
                        (PAD + c * (CELL + PAD), y))
        rough, metal = S.properties_for(substance)
        draw.text((PAD, y - LABEL + 6),
                  f"{substance}   roughness {rough:.2f}  metal {metal:.0f}",
                  fill=(190, 185, 170))
        print(f"  {substance:<28} roughness {rough:.2f} metal {metal:.0f}")

    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    path = out / "surfaces.png"
    sheet.save(path)
    print(f"\n{len(picked)} material(s) -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
