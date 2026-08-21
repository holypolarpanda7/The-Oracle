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
from vtt import skins as _skins                          # noqa: E402
from vtt import surface as S                              # noqa: E402
from vtt.art import material_ref                          # noqa: E402

#: The tile codes the demo board is built from. Read off DEMO_TERRAIN rather
#: than guessed — a code with no swatch simply stays flat, which is also what a
#: real board does.
DEMO_CODES = ("#", ".", "o", "~", "O", ",", "n", "/")

DIST = ROOT / "activity-ui" / "dist" / "imagery"
SEAM = ROOT / "activity-ui" / "dist" / "demo-surfaces.json"


def stage(codes: tuple[str, ...] = DEMO_CODES, arch: str = "") -> int:
    """Copy the swatches for these tile codes into the build, with their
    derived surfaces. ``arch`` names the board they belong to, because a
    material is filed per (code, skin, look) and the tavern's floor is not the
    street's."""
    store = ImageStore()
    with Session(store.engine) as s:
        rows = {r.ref_slug: r for r in s.exec(
            select(EntityImage).where(EntityImage.kind == "material")).all()}
    (DIST / "image").mkdir(parents=True, exist_ok=True)
    materials: dict[str, int] = {}
    surfaces: dict[str, dict] = {}
    skinned = _skins.skins_for(arch) if arch else {}
    for code in codes:
        slug = slugify(material_ref(code, skinned.get(code, "")))
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


def board(arch: str, seed: int, size: tuple[int, int]) -> dict:
    """A REAL generated board, in the shape `state()` ships.

    The demo is a tavern, so judging how a STREET or a REEF looks in the
    browser used to mean standing up a backend and a session for it. This is
    the same geometry the engine would ship — including the pieces that are
    TRACED rather than derived per square, which are exactly the ones worth
    looking at in a browser, because nothing about them can be checked by the
    alignment gate (the server computes them and both renderers draw the
    answer).
    """
    from vtt import decor as _decor
    from vtt import hull as _hull
    from vtt import skins as _skins
    from vtt.mapgen import generate_map
    from vtt.terrain import tile, tile_height_ft

    w, h = size
    gen = generate_map(arch, width=w, height=h, seed=seed)
    codes = _skins.skins_for(arch, style=gen.style or "")
    squares = dict(gen.skins or {})

    def skin_of(c: str, x: int, z: int) -> str:
        return _skins.skin_at(c, x, z, codes=codes, squares=squares)

    rows = gen.grid.to_rows()
    return {
        "width": w, "height": h,
        "terrain": rows,
        "levels": [{"name": "Ground", "base_ft": 0, "terrain": rows,
                    "elevation": dict(gen.elevation or {}), "stairs": []}],
        "elevation": dict(gen.elevation or {}),
        "skins": {"codes": codes, "squares": squares},
        "decor": _decor.decor_for(rows, seed=gen.seed,
                                  standing=lambda c: tile_height_ft(c) > 0,
                                  archetype=arch),
        "shells": _hull.shells(rows, skin_of, gen.elevation),
        "roofs": _hull.roofs(rows, skin_of, gen.elevation,
                             footprints=gen.buildings or None),
        "objects": [{"x": x, "y": z, "code": rows[z][x],
                     "name": tile(rows[z][x]).name}
                    for z in range(h) for x in range(w)
                    if tile(rows[z][x]).name and rows[z][x] in "Oon Aw+/p"],
        "doors": [], "fog": None, "sight": None, "light": None,
        "description": gen.description or arch,
    }


def clear() -> int:
    shutil.rmtree(DIST, ignore_errors=True)
    SEAM.unlink(missing_ok=True)
    print("cleared")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", action="store_true")
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--board", default="",
                    help="stage a REAL generated board of this archetype over "
                         "the demo's tavern, so its geometry can be looked at "
                         "in a browser")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--size", default="24x18")
    a = ap.parse_args()
    if a.clear:
        raise SystemExit(clear())
    if a.board:
        import json as _json
        w, h = (int(v) for v in a.size.lower().split("x"))
        made = board(a.board, a.seed, (w, h))
        # The swatches for THIS board's codes, not the tavern's — a staged
        # street lit by a taproom's floorboards is a probe of nothing.
        rc = stage(tuple(sorted({c for r in made["terrain"] for c in r})),
                   arch=a.board)
        got = _json.loads(SEAM.read_text(encoding="utf-8")) if SEAM.exists() else {}
        got.update(made)
        SEAM.write_text(_json.dumps(got), encoding="utf-8")
        print(f"staged the {a.board} board ({w}x{h}, seed {a.seed}): "
              f"{len(got.get('roofs') or [])} roof(s), "
              f"{len(got.get('shells') or [])} hull(s)")
    else:
        rc = stage()
    raise SystemExit(rc)
