"""Pre-render the board's surface materials, so no room ever waits for one.

The isometric board is geometry, and geometry is built out of MATERIALS: one
swatch per (what a surface is made of, what the room looks like), reused by
every square made of that stuff. This draws the whole catalogue once.

    ./.venv/Scripts/python.exe scripts/material_prerender.py --audit
    ./.venv/Scripts/python.exe scripts/material_prerender.py --render
    ./.venv/Scripts/python.exe scripts/material_prerender.py --render --contexts dungeon
    ./.venv/Scripts/python.exe scripts/material_prerender.py --sheet

MUST run under the WINDOWS interpreter — ComfyUI is a Windows process and WSL
cannot reach it (see CLAUDE.md -> Environment).

**Why this exists at all.** A painted battlemap costs a render every time the
party walks into a room nobody has seen, forever. A material catalogue costs a
render once, ever — `mapgen` is deterministic code, so with the swatches on
disk a brand-new room costs nothing. That is the item-art and debris-sprite
lesson applied to the floor: move the cost from per-room to per-catalogue.

``--audit`` says what is missing without drawing anything; ``--render`` fills
the gaps and is resumable, so an interrupted run costs nothing. ``--sheet``
writes a contact sheet to ``material-probe/`` so the swatches can actually be
looked at — a texture that reads as a photograph of a place rather than a
sample of a surface is the failure mode here, and it is only visible by eye.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: The board looks worth having ready. Anything else renders on demand and
#: joins the cache — the same list debris_prerender.py covers, for the same
#: reason and so the two catalogues agree about what a "dungeon" looks like.
CONTEXTS = [
    "underground", "dungeon", "woodland", "town", "cavern", "ruins",
    "interior", "snow", "desert", "wetland", "sea", "sky",
]

OUT_DIR = ROOT / "material-probe"


def _catalogue(contexts: list[str]) -> list[tuple[str, str, str]]:
    """Every (tile code, skin, look) worth having a swatch for.

    Look-agnostic materials appear ONCE rather than once per look: lava is lava
    in a cavern and in a ruin, and rendering ten identical pools of it is ten
    renders to store one picture. That split is what keeps the catalogue near
    200 swatches instead of 270.

    SKINS are the same trick one level up (see vtt/skins.py). A skin names its
    substance outright — coral, canvas, riveted brass — so it is look-agnostic
    by construction and costs exactly one swatch each however many tile codes
    wear it and however many boards it turns up on.
    """
    from vtt.art import material_look, material_ref, material_subject
    from vtt.skins import SKINS
    from vtt.terrain import TILES

    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(code: str, skin: str, look: str) -> None:
        # Deduped by (REF, look), not by code: the nine object kinds resolve to
        # four substances between them, so a pillar and an altar are one job and
        # rendering both would draw the same stone twice.
        key = (material_ref(code, skin), look)
        if key in seen:
            return
        seen.add(key)
        out.append((code, skin, look))

    for code in sorted(TILES):
        if not material_subject(code):
            continue
        agnostic = material_look(code)
        for look in ([agnostic] if agnostic else contexts):
            add(code, "", look)
    for name in sorted(SKINS):
        add(".", name, material_look(".", name))
    return out


def _sheet(store, jobs) -> int:
    """Write one contact sheet per look, so the batch can be judged by eye."""
    import io

    from PIL import Image, ImageDraw

    from imagery.models import ImageKind, context_key, slugify
    from vtt.art import material_ref

    by_look: dict[str, list[tuple[str, str, str]]] = {}
    for code, skin, look, label, _t in jobs:
        by_look.setdefault(look, []).append((code, skin, label))

    OUT_DIR.mkdir(exist_ok=True)
    written = 0
    for look, entries in sorted(by_look.items()):
        cells = []
        for code, skin, label in entries:
            # list_for hands back METADATA dicts, not ORM rows — the bytes are a
            # second lookup on purpose, so listing a catalogue doesn't drag
            # every picture in it through memory.
            found = store.list_for(ImageKind.MATERIAL,
                                   slugify(material_ref(code, skin)),
                                   context_key(look))
            if not found:
                continue
            data = store.get_image_bytes(found[0]["image_id"])
            if not data:
                continue
            cells.append((label, Image.open(io.BytesIO(data)).convert("RGB")))
        if not cells:
            continue
        cols = min(6, len(cells))
        rows_n = (len(cells) + cols - 1) // cols
        px, pad, cap = 192, 8, 16
        sheet = Image.new("RGB", (cols * (px + pad) + pad,
                                  rows_n * (px + pad + cap) + pad), (16, 18, 26))
        d = ImageDraw.Draw(sheet)
        for i, (label, img) in enumerate(cells):
            cx = pad + (i % cols) * (px + pad)
            cy = pad + (i // cols) * (px + pad + cap)
            sheet.paste(img.resize((px, px)), (cx, cy))
            d.text((cx, cy + px + 3), label[:30], fill=(196, 206, 232))
        path = OUT_DIR / f"materials-{look}.png"
        sheet.save(path)
        written += 1
        print(f"  wrote {path.relative_to(ROOT)}  ({len(cells)} swatches)")
    return written


#: Under this, a cover square and the floor it stands on are worth LOOKING at:
#: reported, not failed. The median pair that actually co-occurs on a board is
#: about 70 apart, so thirty is already unusual.
CONTRAST_MIN = 30.0

#: Under this, two DIFFERENT materials have come out the same colour and a
#: prompt is at fault. Measured rather than chosen, and the line sits in a real
#: gap: the wood swatch a player reported — "the tables and crates are
#: basically the same colour as the road" — was 12.7 from cobbles, and the
#: closest pair that is legitimately similar is a pale limestone pillar on sand
#: at 14.8. Everything below the line has been a prompt that named a material
#: and named no COLOUR; everything above it is two things genuinely made of
#: much the same stuff, which is a question for a skin and not for a prompt.
CONTRAST_FAIL = 14.0


def _contrast(store) -> int:
    """Can a player tell COVER from the floor it stands on, on a real board?

    A crate is half cover and four feet tall, a low wall is three, a tree is
    three-quarters — and a player deciding whether they can break line of sight
    reads that off the board. When the crate and the road under it average the
    same colour, that decision is being made on shading alone. It is the exact
    complaint `SUBSTANCE_ART` already answers for hue drift, arriving from the
    other side: `wood` named its grain and named no colour, so the sampler
    picked one, and it picked grey-green.

    Only pairs that ACTUALLY MEET are compared, which is why this generates
    boards rather than crossing the catalogue with itself: a tree on sand and a
    crate on snow are pairs no archetype can produce, and reporting them buries
    the ones that matter. Same-substance pairs are reported separately — a
    wooden crate on a wooden deck IS one material, and the answer there is a
    skin rather than a prompt.
    """
    import io as _io

    import numpy as np
    from PIL import Image

    from imagery.models import ImageKind, context_key, slugify
    from vtt import skins as _sk
    from vtt.art import board_look, material_look, material_ref, material_subject
    from vtt.mapgen import ARCHETYPES, generate_map
    from vtt.terrain import APERTURES, tile

    cache: dict[tuple[str, str], object] = {}

    def avg(code: str, skin: str, look: str):
        ref = material_ref(code, skin)
        key = (ref, material_look(code, skin) or look)
        if key in cache:
            return cache[key]
        found = store.list_for(ImageKind.MATERIAL, slugify(key[0]),
                               context_key(key[1]))
        val = None
        if found:
            raw = store.get_image_bytes(found[0]["image_id"])
            if raw:
                val = np.asarray(Image.open(_io.BytesIO(raw)).convert("RGB")
                                 ).reshape(-1, 3).mean(axis=0)
        cache[key] = val
        return val

    worst: dict[tuple, tuple] = {}
    for arch in sorted(ARCHETYPES):
        look = board_look(archetype=arch)
        for seed in (1, 4):
            gen = generate_map(arch, width=46, height=34, seed=seed)
            codes = _sk.skins_for(arch, style=gen.style or "")
            squares = dict(gen.skins or {})
            here = {(c, _sk.skin_at(c, x, z, codes=codes, squares=squares))
                    for z, row in enumerate(gen.grid.to_rows())
                    for x, c in enumerate(row)}
            here = {(c, k) for c, k in here
                    if c not in APERTURES and material_subject(c, k)}
            cover = [(c, k) for c, k in here
                     if tile(c).cover in ("half", "three-quarters")]
            floor = [(c, k) for c, k in here if tile(c).move_cost_ft is not None]
            for c, ck in cover:
                a = avg(c, ck, look)
                if a is None:
                    continue
                for f, fk in floor:
                    b = avg(f, fk, look)
                    if b is None:
                        continue
                    d = float(np.linalg.norm(a - b))
                    key = (look, material_ref(c, ck), material_ref(f, fk))
                    if key not in worst or d < worst[key][0]:
                        worst[key] = (d, arch, c, ck, f, fk)

    same = [(d, *rest) for (lk, ra, rb), (d, *rest) in worst.items() if ra == rb]
    thin = sorted((d, lk, *rest) for (lk, ra, rb), (d, *rest) in worst.items()
                  if ra != rb and d < CONTRAST_MIN)
    print(f"\n{len(worst)} cover/floor pairs actually meet on a generated board")
    if same:
        print(f"  {len(same)} of them are the SAME material — geometry and "
              f"shading are all that tell them apart:")
        for d, arch, c, ck, f, fk in sorted(same)[:6]:
            print(f"    {arch:14} {c}{'@' + ck if ck else '':18} on "
                  f"{f}{'@' + fk if fk else ''}")
    if thin:
        print(f"  {len(thin)} pair(s) under {CONTRAST_MIN:.0f} — worth looking "
              f"at; a * is below {CONTRAST_FAIL:.0f} and is a prompt at fault:")
        for d, look, arch, c, ck, f, fk in thin:
            print(f"   {'*' if d < CONTRAST_FAIL else ' '}{d:5.1f}  {look:10} "
                  f"{arch:14} "
                  f"{tile(c).name} ({material_ref(c, ck).split('-')[-1]}) on "
                  f"{tile(f).name} ({material_ref(f, fk).split('-')[-1]})")
    bad = [t for t in thin if t[0] < CONTRAST_FAIL]
    if bad:
        print(f"\n  {len(bad)} pair(s) below {CONTRAST_FAIL:.0f}: two different "
              f"materials the same colour")
        return 1
    print(f"  nothing below {CONTRAST_FAIL:.0f} — every pair that close is two "
          f"things made of the same stuff")
    return 0


#: Above this, a swatch has too much LARGE-SCALE structure to be a surface.
#:
#: Measured, and the number means something concrete: it is the standard
#: deviation of the picture reduced to 8x8 — everything but the big shapes
#: thrown away — over the standard deviation of what a heavy blur leaves
#: behind, which is the detail. A material is detail nearly everywhere and
#: has little big structure; a PICTURE OF A PLACE is one big shape with detail
#: on it.
#:
#: The line sits in a real gap, measured after the catalogue was cleaned: the
#: median is 0.46, the blobbiest honest swatch is FIRE at 1.30 (flame is big
#: soft shapes and there is nothing to be done about that), and the least bad
#: of the compositions was tarred planking at 1.41. Everything above the line
#: so far has been a boundary between two materials or a subject too vague to
#: paint — never a material that simply has large features.
SURFACE_MAX = 1.40


def _surface(store) -> int:
    """Is every swatch a picture of a SURFACE, or is one a picture of a PLACE?

    A swatch is tiled across the board, so a composition tiles too: the mud
    swatch was one dark pool in the middle of a cracked plain, which came back
    on a swamp as a regular grid of salmon-pink pills. The average colour was a
    perfectly reasonable brown the whole time, so `--contrast` passed it — a
    material can be exactly the right colour and still be the wrong picture.

    Measured across the catalogue this found eight, and the four worst were
    prompts asking for "stair treads", which is a noun that means an
    ELEVATION: four flights of steps drawn in perspective, one of them with a
    man's legs walking down it. The others were a green field under a SKY, a
    snowy roof with icicles, breaking waves, and a stream between rocks.

    `MATERIAL_NEGATIVE` has forbidden perspective, horizons and vanishing
    points since it was written and none of that stopped any of them, which is
    the finding: a negative is a nudge and the subject noun is the
    instruction. The fix is in `_MATERIAL_STYLE`, which now says the view in
    the POSITIVE and says it first.
    """
    import io as _io

    import numpy as np
    from PIL import Image, ImageFilter

    from imagery.models import ImageKind, context_key, slugify
    from vtt.art import BOARD_LOOKS, material_look, material_ref, material_subject
    from vtt.skins import SKINS
    from vtt.terrain import TILES

    seen: set = set()
    rows: list = []

    def look_at(code: str, skin: str, look: str) -> None:
        ref = material_ref(code, skin)
        lk = material_look(code, skin) or look
        if (ref, lk) in seen:
            return
        seen.add((ref, lk))
        found = store.list_for(ImageKind.MATERIAL, slugify(ref), context_key(lk))
        if not found:
            return
        raw = store.get_image_bytes(found[0]["image_id"])
        if not raw:
            return
        im = Image.open(_io.BytesIO(raw)).convert("L")
        arr = np.asarray(im, dtype=float)
        blob = np.asarray(im.resize((8, 8), Image.BOX), dtype=float).std()
        detail = (arr - np.asarray(im.filter(ImageFilter.GaussianBlur(8)),
                                   dtype=float)).std()
        rows.append((blob / max(detail, 1e-6), ref.split("-", 2)[-1], lk))

    for code in sorted(TILES):
        if not material_subject(code):
            continue
        agnostic = material_look(code)
        for look in ([agnostic] if agnostic else BOARD_LOOKS):
            look_at(code, "", look)
    for name in sorted(SKINS):
        look_at(".", name, material_look(".", name))

    rows.sort(reverse=True)
    bad = [r for r in rows if r[0] > SURFACE_MAX]
    vals = [r[0] for r in rows]
    print(f"\n{len(rows)} swatches; big-structure over detail, "
          f"median {np.median(vals):.2f}, worst {vals[0]:.2f}")
    if not bad:
        for r, ref, lk in rows[:3]:
            print(f"    {r:5.2f}  {ref:26} @{lk}")
        print(f"  nothing above {SURFACE_MAX:.2f} — every swatch is a surface")
        return 0
    print(f"  {len(bad)} above {SURFACE_MAX:.2f} — a picture of a PLACE, not a "
          f"material. Look at them:")
    for r, ref, lk in bad:
        print(f"    {r:5.2f}  {ref:26} @{lk}")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit", action="store_true",
                    help="report what's missing without drawing anything")
    ap.add_argument("--render", action="store_true", help="draw the gaps")
    ap.add_argument("--sheet", action="store_true",
                    help="write contact sheets of what has been drawn")
    ap.add_argument("--contrast", action="store_true",
                    help="check a player can tell a square that gives COVER "
                         "from the floor it stands on, on every archetype. "
                         "Exits non-zero on a pair too close to call.")
    ap.add_argument("--surface", action="store_true",
                    help="check every swatch is a picture of a SURFACE rather "
                         "than of a place. Exits non-zero on a composition.")
    ap.add_argument("--prune", action="store_true",
                    help="delete stored swatches no longer in the catalogue "
                         "(after a MATERIAL_REV bump, or a probe's leftovers)")
    ap.add_argument("--contexts", help="comma-separated board looks to cover")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N renders — for probing before committing "
                         "to the whole batch")
    ap.add_argument("--redraw", default="",
                    help="comma-separated substances/labels to draw AGAIN even "
                         "though they exist. The swatch prompt is the thing you "
                         "iterate on, and without this the only way to see a "
                         "changed one was to rename the substance — which "
                         "renames it in the code forever to fix a picture.")
    a = ap.parse_args(argv)
    if not (a.audit or a.render or a.sheet or a.prune or a.contrast
            or a.surface):
        a.audit = True

    from imagery import ImageStore
    from imagery.models import ImageKind, context_key, slugify
    from vtt.art import material_ref, render_material
    from vtt.terrain import tile

    store = ImageStore()
    contexts = ([c.strip() for c in a.contexts.split(",") if c.strip()]
                if a.contexts else CONTEXTS)

    from vtt.art import SUBSTANCE
    from vtt.skins import SKINS

    jobs = []       # (code, skin, look, label, thunk)
    for code, skin, look in _catalogue(contexts):
        # Name a shared substance by the SUBSTANCE, not by whichever tile code
        # happened to sort first — the wood swatch is not "the door swatch",
        # and labelling it that way makes the sheet unreadable.
        if skin:
            label = f"{SKINS[skin].substance} (skin)"
        elif code in SUBSTANCE:
            label = f"{SUBSTANCE[code]} (substance)"
        else:
            label = tile(code).name
        jobs.append((code, skin, look, label,
                     lambda c=code, k=skin, x=look: render_material(
                         c, store=store, context=x, skin=k)))

    n_codes = len({(c, k) for c, k, _l in _catalogue(contexts)})
    print(f"{n_codes} surface kinds over {len(contexts)} looks "
          f"(look-agnostic ones once) = {len(jobs)} swatches\n")

    redraw = {w.strip().lower() for w in a.redraw.split(",") if w.strip()}
    missing, have, again = [], 0, []
    for job in jobs:
        code, skin, look, label = job[0], job[1], job[2], job[3]
        drawn = bool(store.list_for(ImageKind.MATERIAL,
                                    slugify(material_ref(code, skin)),
                                    context_key(look)))
        wanted = redraw and any(w in label.lower() for w in redraw)
        if drawn and not wanted:
            have += 1
        elif drawn:
            again.append(job)
        else:
            missing.append(job)
    if again:
        # Drop the stored ones first, so the render below is the plain
        # missing-swatch path and nothing has to know about overwriting.
        from sqlmodel import Session, select
        from imagery.models import EntityImage
        slugs = {slugify(material_ref(c, k)) for c, k, _l, _lb, _t in again}
        with Session(store.engine) as sess:
            for r in sess.exec(select(EntityImage)).all():
                if r.ref_slug in slugs:
                    sess.delete(r)
            sess.commit()
        print(f"redrawing {len(again)}: "
              + ", ".join(sorted({j[3] for j in again})))
        missing.extend(again)

    if a.prune:
        from sqlmodel import Session, select
        from imagery.models import EntityImage
        keep = {slugify(material_ref(c, k)) for c, k, _l, _lb, _t in jobs}
        with Session(store.engine) as sess:
            # Two things are stale, not one. A swatch whose slug left the
            # catalogue (a REV bump, a probe's leftovers) — and a swatch filed
            # under the WRONG KIND, which is what every material drawn before
            # the MATERIAL kind existed is: stored as a "map", unreachable by
            # every lookup here, and invisible to a slug-only sweep.
            rows = [r for r in sess.exec(select(EntityImage)).all()
                    if str(r.ref_slug).startswith("material-")
                    and (r.ref_slug not in keep or r.kind != ImageKind.MATERIAL)]
            for r in rows:
                sess.delete(r)
            sess.commit()
        print(f"pruned {len(rows)} swatch(es) outside the catalogue")
        if not (a.render or a.sheet):
            return 0

    print(f"already drawn: {have}   missing: {len(missing)}")

    if a.render and missing:
        todo = missing[:a.limit] if a.limit else missing
        t0 = time.time()
        for i, (_code, _skin, look, label, thunk) in enumerate(todo, 1):
            print(f"[{i}/{len(todo)}] {label} in {look} ...", end="", flush=True)
            print(" ok" if thunk() else " FAILED")
        print(f"\n{len(todo)} drawn in {time.time() - t0:.0f}s "
              f"({(time.time() - t0) / max(1, len(todo)):.1f}s each)")
    elif a.audit and not a.sheet:
        for _code, _skin, look, label, _t in missing[:40]:
            print(f"   {label:<28} in {look}")
        if len(missing) > 40:
            print(f"   ... and {len(missing) - 40} more")

    if a.sheet:
        print("\ncontact sheets:")
        if not _sheet(store, jobs):
            print("  nothing drawn yet — run --render first")
    rc = 0
    if a.contrast:
        rc |= _contrast(store)
    if a.surface:
        rc |= _surface(store)
    return rc


if __name__ == "__main__":
    sys.exit(main())
