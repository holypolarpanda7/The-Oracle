"""Pre-render the board's sprite catalogue, so no fight ever waits for one.

Both halves: the OBJECTS that stand on a square (pillar, crate, door, altar…)
and the WRECKAGE they leave when they break. Each is a small, shared set — a
sprite is keyed by what the thing is, what it became, its material and the
board's look — so the whole catalogue is a couple of hundred renders that can
be done once, in advance, exactly like the item-art catalogue.

    ./.venv/Scripts/python.exe scripts/debris_prerender.py --audit
    ./.venv/Scripts/python.exe scripts/debris_prerender.py --render

MUST run under the WINDOWS interpreter — ComfyUI is a Windows process and WSL
cannot reach it (see CLAUDE.md -> Environment).

``--audit`` says what is missing without drawing anything; ``--render`` fills
the gaps and is resumable, so an interrupted run costs nothing. After it, a
smashed pillar mid-combat costs a cache lookup instead of ten seconds of GPU.
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
#: joins the cache — this is the common case, not an exhaustive list.
CONTEXTS = [
    "underground", "dungeon", "woodland", "town", "cavern", "ruins",
    "interior", "snow", "desert", "wetland",
]


def _catalogue():
    """Every (becomes, material, was) the breakable tiles can actually produce.

    One row per BREAKABLE, not per (becomes, material): a pillar, an altar and
    a wall all leave stone rubble, and folding them together is what made a
    smashed crate come back looking like a smashed wall.
    """
    from vtt.terrain import _BREAKABLE, tile
    return [(becomes, material, tile(code).name)
            for code, (becomes, _ac, _hp, material) in sorted(_BREAKABLE.items())]


def _object_catalogue():
    """Every discrete object that gets a sprite of its own."""
    from vtt.terrain import OBJECT_SPRITES
    return sorted(OBJECT_SPRITES)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit", action="store_true",
                    help="report what's missing without drawing anything")
    ap.add_argument("--render", action="store_true", help="draw the gaps")
    ap.add_argument("--prune", action="store_true",
                    help="delete stored debris sprites no longer in the catalogue "
                         "(a wreckage kind that changed, or a probe's leftovers)")
    ap.add_argument("--contexts", help="comma-separated board looks to cover")
    a = ap.parse_args(argv)
    if not (a.audit or a.render):
        a.audit = True

    from imagery import ImageStore
    from imagery.models import context_key, slugify
    from vtt.art import debris_ref, object_ref, render_debris, render_object
    from vtt.terrain import sprite_label, tile

    store = ImageStore()
    contexts = ([c.strip() for c in a.contexts.split(",") if c.strip()]
                if a.contexts else CONTEXTS)

    # Two catalogues, one script. Objects are the half that has to exist BEFORE
    # anything breaks — you cannot recognise rubble as a broken pillar unless
    # the pillar was visibly standing there first — so they are pre-rendered on
    # the same terms as the wreckage they turn into.
    jobs = []       # (ref, label, context, thunk)
    for code in _object_catalogue():
        for ctx in contexts:
            jobs.append((object_ref(code), f"{sprite_label(code)} (standing)",
                         ctx, lambda c=code, x=ctx: render_object(
                             code=c, store=store, context=x)))
    for becomes, material, was in _catalogue():
        for ctx in contexts:
            jobs.append((debris_ref(becomes, material, was),
                         f"broken {was} -> {tile(becomes).name} ({material})",
                         ctx, lambda b=becomes, m=material, w=was, x=ctx:
                             render_debris(b, store=store, material=m,
                                           was=w, context=x)))

    n_obj = len(_object_catalogue())
    n_deb = len(_catalogue())
    print(f"{n_obj} object kinds + {n_deb} wreckage kinds x {len(contexts)} "
          f"looks = {len(jobs)} sprites\n")

    missing, have = [], 0
    for ref, label, ctx, thunk in jobs:
        if store.list_for("map", slugify(ref), context_key(ctx)):
            have += 1
        else:
            missing.append((ref, label, ctx, thunk))

    if a.prune:
        from sqlmodel import Session, select
        from imagery.models import EntityImage
        keep = {slugify(ref) for ref, _l, _c, _t in jobs}
        with Session(store.engine) as sess:
            rows = [r for r in sess.exec(select(EntityImage)).all()
                    if (str(r.ref_slug).startswith(("debris-", "object-"))
                        and r.ref_slug not in keep)]
            for r in rows:
                sess.delete(r)
            sess.commit()
        print(f"pruned {len(rows)} sprite(s) outside the catalogue"
              + (f": {sorted({r.ref_slug for r in rows})}" if rows else ""))
        if not a.render:
            return 0

    print(f"already drawn: {have}   missing: {len(missing)}")
    if a.audit and not a.render:
        for _ref, label, ctx, _t in missing[:40]:
            print(f"   {label:<44} in {ctx}")
        if len(missing) > 40:
            print(f"   ... and {len(missing) - 40} more")
        return 0

    t0 = time.time()
    for i, (_ref, label, ctx, thunk) in enumerate(missing, 1):
        print(f"[{i}/{len(missing)}] {label} in {ctx} ...", end="", flush=True)
        print(" ok" if thunk() else " FAILED")
    if missing:
        print(f"\n{len(missing)} drawn in {time.time() - t0:.0f}s "
              f"({(time.time() - t0) / len(missing):.1f}s each)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
