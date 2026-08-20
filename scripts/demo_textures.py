"""Put the board's REAL swatches into the offline demo, for looking at.

The demo feed is what a browser shows with no backend, and its board is
flat-COLOURED: a swatch is a stored render and there is nothing here to serve
one. That is right for the offline fallback and useless for judging how the
board looks, which is the one thing a screenshot harness is for.

So this stages the real thing without changing what ships: it copies a handful
of material swatches out of the database into the BUILD (`dist/`, which is not
committed), derives their normal and roughness maps beside them, and writes a
tiny JSON the harnesses inject through `__ORACLE_DEMO_SURFACES`. No rebuild, no
patching of `demo.ts`, and no megabytes of art committed to make a demo
prettier.

    npm run build --prefix activity-ui
    uv run python scripts/demo_textures.py --stage
    (cd activity-ui/dist && python3 -m http.server 4191)
    npx node turn-shot.mjs http://localhost:4191/

`--clear` removes them again. Everything it writes lives under `dist/`, so a
rebuild clears it anyway.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlmodel import Session, select                      # noqa: E402

from imagery import ImageStore                            # noqa: E402
from imagery.models import EntityImage, slugify           # noqa: E402
from vtt import surface as S                              # noqa: E402
from vtt.art import material_ref                          # noqa: E402

#: The tile codes the demo board is built from. Read off DEMO_TERRAIN rather
#: than guessed — a code with no swatch simply stays flat, which is also what a
#: real board does.
DEMO_CODES = ("#", ".", "o", "~", "O", ",", "n", "/")

DIST = ROOT / "activity-ui" / "dist" / "imagery"
SEAM = ROOT / "activity-ui" / "dist" / "demo-surfaces.json"


def stage() -> int:
    store = ImageStore()
    with Session(store.engine) as s:
        rows = {r.ref_slug: r for r in s.exec(
            select(EntityImage).where(EntityImage.kind == "material")).all()}
    (DIST / "image").mkdir(parents=True, exist_ok=True)
    materials: dict[str, int] = {}
    surfaces: dict[str, dict] = {}
    for code in DEMO_CODES:
        slug = slugify(material_ref(code, ""))
        row = rows.get(slug)
        if row is None:
            print(f"  {code!r:>4} -> no swatch ({slug})")
            continue
        raw = store.get_image_bytes(row.id)
        if not raw:
            continue
        (DIST / "image" / str(row.id)).write_bytes(raw)
        substance = slug.replace("material-v2-", "").replace("substance-", "")
        out = DIST / "surface" / str(row.id)
        out.mkdir(parents=True, exist_ok=True)
        for chan, data in (("normal", S.normal_map(raw)),
                           ("rough", S.roughness_map(raw, substance))):
            if data:
                (out / chan).write_bytes(data)
        rough, metal = S.properties_for(substance)
        materials[code] = row.id
        surfaces[code] = {
            "substance": substance, "roughness": round(rough, 3),
            "metalness": round(metal, 3),
            "normal": f"/imagery/surface/{row.id}/normal",
            "rough_map": f"/imagery/surface/{row.id}/rough",
        }
        print(f"  {code!r:>4} -> {substance:<18} roughness {rough:.2f} "
              f"metal {metal:.0f}")
    SEAM.write_text(json.dumps({"materials": materials, "surfaces": surfaces},
                               indent=2), encoding="utf-8")
    kb = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file()) / 1024
    print(f"\n{len(materials)} material(s), {kb:.0f} KB, staged into "
          f"{DIST.relative_to(ROOT)} and {SEAM.name}")
    return 0


def clear() -> int:
    shutil.rmtree(DIST, ignore_errors=True)
    SEAM.unlink(missing_ok=True)
    print("cleared")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", action="store_true")
    ap.add_argument("--clear", action="store_true")
    a = ap.parse_args()
    raise SystemExit(clear() if a.clear else stage())
