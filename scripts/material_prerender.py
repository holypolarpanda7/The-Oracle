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
    "interior", "snow", "desert", "wetland",
]

OUT_DIR = ROOT / "material-probe"


def _catalogue(contexts: list[str]) -> list[tuple[str, str]]:
    """Every (tile code, look) worth having a swatch for.

    Look-agnostic materials appear ONCE rather than once per look: lava is lava
    in a cavern and in a ruin, and rendering ten identical pools of it is ten
    renders to store one picture. That split is what keeps the catalogue near
    200 swatches instead of 270.
    """
    from vtt.art import material_look, material_ref, material_subject
    from vtt.terrain import TILES

    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for code in sorted(TILES):
        if not material_subject(code):
            continue
        agnostic = material_look(code)
        for look in ([agnostic] if agnostic else contexts):
            # Deduped by (REF, look), not by code: the nine object kinds
            # resolve to four substances between them, so a pillar and an altar
            # are one job and rendering both would draw the same stone twice.
            key = (material_ref(code), look)
            if key in seen:
                continue
            seen.add(key)
            out.append((code, look))
    return out


def _sheet(store, jobs) -> int:
    """Write one contact sheet per look, so the batch can be judged by eye."""
    import io

    from PIL import Image, ImageDraw

    from imagery.models import ImageKind, context_key, slugify
    from vtt.art import material_ref

    by_look: dict[str, list[tuple[str, str]]] = {}
    for code, look, label, _t in jobs:
        by_look.setdefault(look, []).append((code, label))

    OUT_DIR.mkdir(exist_ok=True)
    written = 0
    for look, entries in sorted(by_look.items()):
        cells = []
        for code, label in entries:
            # list_for hands back METADATA dicts, not ORM rows — the bytes are a
            # second lookup on purpose, so listing a catalogue doesn't drag
            # every picture in it through memory.
            found = store.list_for(ImageKind.MATERIAL, slugify(material_ref(code)), context_key(look))
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit", action="store_true",
                    help="report what's missing without drawing anything")
    ap.add_argument("--render", action="store_true", help="draw the gaps")
    ap.add_argument("--sheet", action="store_true",
                    help="write contact sheets of what has been drawn")
    ap.add_argument("--prune", action="store_true",
                    help="delete stored swatches no longer in the catalogue "
                         "(after a MATERIAL_REV bump, or a probe's leftovers)")
    ap.add_argument("--contexts", help="comma-separated board looks to cover")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N renders — for probing before committing "
                         "to the whole batch")
    a = ap.parse_args(argv)
    if not (a.audit or a.render or a.sheet or a.prune):
        a.audit = True

    from imagery import ImageStore
    from imagery.models import ImageKind, context_key, slugify
    from vtt.art import material_ref, render_material
    from vtt.terrain import tile

    store = ImageStore()
    contexts = ([c.strip() for c in a.contexts.split(",") if c.strip()]
                if a.contexts else CONTEXTS)

    from vtt.art import SUBSTANCE

    jobs = []       # (code, look, label, thunk)
    for code, look in _catalogue(contexts):
        # Name a shared substance by the SUBSTANCE, not by whichever tile code
        # happened to sort first — the wood swatch is not "the door swatch",
        # and labelling it that way makes the sheet unreadable.
        label = (f"{SUBSTANCE[code]} (substance)" if code in SUBSTANCE
                 else tile(code).name)
        jobs.append((code, look, label,
                     lambda c=code, x=look: render_material(
                         c, store=store, context=x)))

    n_codes = len({c for c, _l in _catalogue(contexts)})
    print(f"{n_codes} surface kinds over {len(contexts)} looks "
          f"(look-agnostic ones once) = {len(jobs)} swatches\n")

    missing, have = [], 0
    for job in jobs:
        code, look = job[0], job[1]
        if store.list_for(ImageKind.MATERIAL, slugify(material_ref(code)), context_key(look)):
            have += 1
        else:
            missing.append(job)

    if a.prune:
        from sqlmodel import Session, select
        from imagery.models import EntityImage
        keep = {slugify(material_ref(c)) for c, _l, _lb, _t in jobs}
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
        for i, (_code, look, label, thunk) in enumerate(todo, 1):
            print(f"[{i}/{len(todo)}] {label} in {look} ...", end="", flush=True)
            print(" ok" if thunk() else " FAILED")
        print(f"\n{len(todo)} drawn in {time.time() - t0:.0f}s "
              f"({(time.time() - t0) / max(1, len(todo)):.1f}s each)")
    elif a.audit and not a.sheet:
        for _code, look, label, _t in missing[:40]:
            print(f"   {label:<28} in {look}")
        if len(missing) > 40:
            print(f"   ... and {len(missing) - 40} more")

    if a.sheet:
        print("\ncontact sheets:")
        if not _sheet(store, jobs):
            print("  nothing drawn yet — run --render first")
    return 0


if __name__ == "__main__":
    sys.exit(main())
