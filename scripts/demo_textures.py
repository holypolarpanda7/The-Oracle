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
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


from imagery import ImageStore                            # noqa: E402
from imagery.models import ImageKind, context_key, slugify  # noqa: E402
from vtt import skins as _skins                          # noqa: E402
from vtt import surface as S                              # noqa: E402
from vtt.art import (board_look, material_look, material_ref,
                     material_subject)  # noqa: E402

#: The tile codes the demo board is built from. Read off DEMO_TERRAIN rather
#: than guessed — a code with no swatch simply stays flat, which is also what a
#: real board does.
DEMO_CODES = ("#", ".", "o", "~", "O", ",", "n", "/")

DIST = ROOT / "activity-ui" / "dist" / "imagery"
SEAM = ROOT / "activity-ui" / "dist" / "demo-surfaces.json"


def stage(codes: tuple[str, ...] = DEMO_CODES, arch: str = "",
          slots: Iterable[tuple[str, str]] = ()) -> int:
    """Copy the swatches for these tile codes into the build, with their
    derived surfaces. ``arch`` names the board they belong to, because a
    material is filed per (code, skin, look) and the tavern's floor is not the
    street's.

    ``slots`` names (code, skin) pairs outright. A skin is not always the
    archetype's: `skin_at` reads a PER-SQUARE table too, and everything built
    out of composed tiles uses it — a camp's tents are `#` wearing canvas on
    their own squares while the palisade round them is `#` wearing logs. Staged
    from the archetype map alone those squares found no swatch and fell back to
    their flat tile colour, so a field of tents came back as a field of dark
    holes. The board knows its own slots; it is asked.
    """
    store = ImageStore()
    # A slug is not a swatch. `material-v2-floor` holds TWELVE rows, one per
    # look, and picking by slug alone took whichever the database happened to
    # hand back last — the SKY floor on a cave board, the sky's rubble and the
    # sky's stairs with it. Resolved the way `scene.materials_for` resolves it:
    # a substance is filed under "any" and everything else under the board's
    # own look. Same lesson as the slot key one line down — a probe that
    # quietly shows something other than the app is worse than no probe.
    look = board_look(archetype=arch)
    print(f"look: {look}" + ("" if arch else "  (no archetype given)"))
    (DIST / "image").mkdir(parents=True, exist_ok=True)
    materials: dict[str, int] = {}
    surfaces: dict[str, dict] = {}
    skinned = _skins.skins_for(arch) if arch else {}
    want = {(c, skinned.get(c, "")) for c in codes} | set(slots)
    for code, skin in sorted(want):
        # The board looks a material up by SLOT, not by tile code:
        # `materialSlot(code, skin)` is "#@townhouse" wherever a skin is on.
        # Keyed by the bare code, every skinned square on a staged board missed
        # and fell back to its flat tile colour — which is why a street came
        # back a field of untextured dark grey while a swamp, whose codes
        # mostly wear no skin, looked fine. A probe that quietly shows
        # something other than what the app shows is worse than no probe.
        slot = f"{code}@{skin}" if skin else code
        # A void square is not a missing swatch. `^` (open sky), a chasm and
        # blank space are in NO_MATERIAL by design, and reporting them as gaps
        # sends whoever reads this output looking for a render nobody owes.
        if not material_subject(code, skin):
            continue
        slug = slugify(material_ref(code, skin))
        bucket = material_look(code, skin) or look
        found = store.list_for(ImageKind.MATERIAL, slug, context_key(bucket))
        if not found:
            print(f"  {slot:>16} -> no swatch ({slug} @ {bucket})")
            continue
        image_id = found[0]["image_id"]
        raw = store.get_image_bytes(image_id)
        if not raw:
            continue
        (DIST / "image" / str(image_id)).write_bytes(raw)
        substance = slug.replace("material-v2-", "").replace("substance-", "")
        out = DIST / "surface" / str(image_id)
        out.mkdir(parents=True, exist_ok=True)
        for chan, data in (("normal", S.normal_map(raw)),
                           ("rough", S.roughness_map(raw, substance))):
            if data:
                (out / chan).write_bytes(data)
        rough, metal = S.properties_for(substance)
        materials[slot] = image_id
        surfaces[slot] = {
            "substance": substance, "roughness": round(rough, 3),
            "metalness": round(metal, 3),
            "normal": f"/imagery/surface/{image_id}/normal",
            "rough_map": f"/imagery/surface/{image_id}/rough",
        }
        print(f"  {slot:>16} -> {substance:<18} @{bucket:<11} "
              f"roughness {rough:.2f} metal {metal:.0f}")
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
    from vtt import setpieces as _sp
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
    slots = {(rows[z][x], skin_of(rows[z][x], x, z))
             for z in range(h) for x in range(w)}
    # A roof's material belongs to no square (Skin.roof_skin), so the square
    # walk can never reach it — the same hole `scene.materials_for` had.
    slots |= {("#", getattr(_skins.skin(k), "roof_skin", ""))
              for _c, k in list(slots)
              if k and getattr(_skins.skin(k), "roof_skin", "")}
    # The LANDMARKS this board placed, read back out of the catalogue exactly
    # as `scene.setpieces_for` reads them — the mesh, the footprint and the fit
    # measured off the file. Without them the demo's own broken pillar was left
    # standing at the mill's coordinates on every staged board, which on a
    # meadow is a stone column in mid-air.
    pieces: list[dict] = []
    for rec in (gen.setpieces or []):
        if not isinstance(rec, dict):
            continue
        slug = str(rec.get("slug") or "")
        if _sp.piece(slug, str(rec.get("name") or "")) is None:
            continue
        pieces.append(_sp.Placed(slug=slug, x=int(rec.get("x") or 0),
                                 y=int(rec.get("y") or 0),
                                 yaw=int(rec.get("yaw") or 0)).instance())
    return {
        # Every (code, skin) actually standing on this board, so the staging
        # pass asks for what the renderer will ask for. Popped before the seam
        # is written; the client builds its own slots from `skins`.
        "_slots": sorted(slots),
        "width": w, "height": h,
        # What a creature has to do to be here. The browser puts the water
        # column back on a swim board, so a staged reef without this is a reef
        # drawn as dry land — which is exactly what it looked like.
        "mode": gen.mode,
        "terrain": rows,
        "levels": [{"name": "Ground", "base_ft": 0, "terrain": rows,
                    "elevation": dict(gen.elevation or {}),
                    "water": dict(gen.water or {}), "stairs": []}],
        "elevation": dict(gen.elevation or {}),
        # The level sheet over any pool. Without it a staged swamp is a board
        # full of sunken basins with nothing in them — which is what the
        # geometry looks like before the water goes back on top of it, and
        # exactly the thing this seam exists to let somebody look at.
        "water": dict(gen.water or {}),
        "skins": {"codes": codes, "squares": squares},
        "setpieces": pieces,
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
                   arch=a.board, slots=made.pop("_slots"))
        got = _json.loads(SEAM.read_text(encoding="utf-8")) if SEAM.exists() else {}
        got.update(made)
        SEAM.write_text(_json.dumps(got), encoding="utf-8")
        print(f"staged the {a.board} board ({w}x{h}, seed {a.seed}): "
              f"{len(got.get('roofs') or [])} roof(s), "
              f"{len(got.get('shells') or [])} hull(s)")
    else:
        rc = stage()
    raise SystemExit(rc)
