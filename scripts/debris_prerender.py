"""Pre-render the wreckage sprite catalogue, so no fight ever waits for one.

Debris is a SMALL, shared set. A sprite is keyed by (what the square became,
its material, the board's look) — and there are only a handful of things a
breakable tile can leave behind, times a handful of looks. So the whole
catalogue is a few dozen renders that can be done once, in advance, exactly
like the item-art catalogue.

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
    """Every (becomes, material, was) the breakable tiles can actually produce."""
    from vtt.terrain import _BREAKABLE, tile
    seen = {}
    for code, (becomes, _ac, _hp, material) in _BREAKABLE.items():
        seen.setdefault((becomes, material), tile(code).name)
    return [(b, m, was) for (b, m), was in sorted(seen.items())]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit", action="store_true",
                    help="report what's missing without drawing anything")
    ap.add_argument("--render", action="store_true", help="draw the gaps")
    ap.add_argument("--contexts", help="comma-separated board looks to cover")
    a = ap.parse_args(argv)
    if not (a.audit or a.render):
        a.audit = True

    from imagery import ImageStore
    from imagery.models import context_key, slugify
    from vtt.art import render_debris
    from vtt.terrain import tile

    store = ImageStore()
    contexts = ([c.strip() for c in a.contexts.split(",") if c.strip()]
                if a.contexts else CONTEXTS)
    cat = _catalogue()
    print(f"{len(cat)} wreckage kinds x {len(contexts)} looks "
          f"= {len(cat) * len(contexts)} sprites\n")

    missing, have = [], 0
    for becomes, material, was in cat:
        ref = f"debris-{tile(becomes).name.replace(' ', '-')}-{material or 'any'}"
        for ctx in contexts:
            rows = store.list_for("map", slugify(ref), context_key(ctx))
            if rows:
                have += 1
            else:
                missing.append((becomes, material, was, ctx))

    print(f"already drawn: {have}   missing: {len(missing)}")
    if a.audit and not a.render:
        for becomes, material, _was, ctx in missing[:40]:
            print(f"   {tile(becomes).name:<12} {material:<6} in {ctx}")
        if len(missing) > 40:
            print(f"   ... and {len(missing) - 40} more")
        return 0

    t0 = time.time()
    for i, (becomes, material, was, ctx) in enumerate(missing, 1):
        print(f"[{i}/{len(missing)}] {tile(becomes).name} ({material}) in {ctx} ...",
              end="", flush=True)
        img = render_debris(becomes, store=store, material=material,
                            was=was, context=ctx)
        print(" ok" if img else " FAILED")
    if missing:
        print(f"\n{len(missing)} drawn in {time.time() - t0:.0f}s "
              f"({(time.time() - t0) / len(missing):.1f}s each)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
