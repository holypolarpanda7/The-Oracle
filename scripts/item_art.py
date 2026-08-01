"""Pre-render the item catalog — a fixed, one-time cost instead of a per-player one.

Item pictures are keyed by the item's SLUG, so one render of "Longsword" serves
every character in every campaign, forever. That makes the whole rules catalog a
BOUNDED job: render it once here, and play never has to draw an ordinary item
again. The only render play pays for after this is a piece a player names and
describes themselves (see ``describe_item`` in the backend).

This is deliberately resumable and interruptible: it skips anything already
drawn, so you can stop it, play, and pick it up later.

WINDOWS interpreter — ComfyUI is a Windows process and WSL cannot reach it
(see CLAUDE.md -> Environment). Env vars must be named in WSLENV to cross over:

    # what's missing, no GPU touched
    DATABASE_URL="sqlite:///D:/Projects/The Oracle/oracle-dm-backend/oracle.db" \\
      WSLENV=DATABASE_URL ./.venv/Scripts/python.exe scripts/item_art.py --audit

    # draw them
    ... ./.venv/Scripts/python.exe scripts/item_art.py --render
    ... ./.venv/Scripts/python.exe scripts/item_art.py --render --limit 25
    ... ./.venv/Scripts/python.exe scripts/item_art.py --render --rarity Legendary
    ... ./.venv/Scripts/python.exe scripts/item_art.py --render --only Potion

The backend module is loaded whole rather than reimplementing the prompt logic:
the batch MUST build prompts exactly the way the live inspector does, or
pre-rendered art would not match art drawn during play.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time
from collections import Counter
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BAR_W = 40


def human(sec: float) -> str:
    sec = int(max(sec, 0))
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def load_backend():
    """Import fastapi-dm.py as a module (the same trick the smoke tests use)."""
    spec = importlib.util.spec_from_file_location(
        "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # type: ignore[union-attr]
    return mod


def catalog(m, only: str | None, rarity: str | None) -> list:
    from sqlmodel import Session, select
    from rules.models import Item
    with Session(m.engine) as s:
        rows = list(s.exec(select(Item)).all())
    if only:
        n = only.lower()
        rows = [r for r in rows
                if n in ((r.item_type or "") + " " + (r.category or "")).lower()]
    if rarity:
        n = rarity.lower()
        rows = [r for r in rows if (r.rarity or "").lower() == n]
    # Deterministic order so a resumed run picks up where it left off.
    return sorted(rows, key=lambda r: (r.name or "").lower())


def has_art(m, name: str) -> bool:
    try:
        return m.image_store.get_any_latest("item", name) is not None
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit", action="store_true",
                    help="report coverage and exit (touches no GPU)")
    ap.add_argument("--render", action="store_true", help="draw what is missing")
    ap.add_argument("--limit", type=int, default=0, help="stop after N renders")
    ap.add_argument("--only", help="substring match on item type/category")
    ap.add_argument("--rarity", help="exact rarity match, e.g. Legendary")
    ap.add_argument("--force", action="store_true",
                    help="redraw even items that already have art")
    args = ap.parse_args()
    if not (args.audit or args.render):
        ap.print_help()
        return 2

    print("loading the backend…", flush=True)
    m = load_backend()
    rows = catalog(m, args.only, args.rarity)
    if not rows:
        print("No catalog items matched. Has the SRD been ingested?")
        return 1

    missing = [r for r in rows if args.force or not has_art(m, r.name)]
    done = len(rows) - len(missing)

    print(f"\ncatalog: {len(rows)} items    drawn: {done}    missing: {len(missing)}")
    if args.audit:
        by_type = Counter((r.item_type or r.category or "?") for r in missing)
        by_rar = Counter((r.rarity or "mundane") for r in missing)
        print("\nmissing by type:")
        for k, n in by_type.most_common(15):
            print(f"  {n:>4}  {k}")
        print("\nmissing by rarity:")
        for k, n in by_rar.most_common():
            print(f"  {n:>4}  {k}")
        print(f"\nAt ~20s each that is about {human(len(missing) * 20)} of GPU time,")
        print("paid once, then shared by every character in every campaign.")
        return 0

    if args.limit:
        missing = missing[:args.limit]
    if not missing:
        print("Nothing to draw — the catalog is fully illustrated.")
        return 0

    print(f"drawing {len(missing)} items. Ctrl-C is safe: this is resumable.\n")
    start = time.time()
    ok = fail = 0
    for i, row in enumerate(missing, 1):
        elapsed = time.time() - start
        rate = elapsed / max(1, i - 1) if i > 1 else 0
        eta = rate * (len(missing) - i + 1)
        filled = int(BAR_W * (i - 1) / len(missing))
        bar = "█" * filled + "·" * (BAR_W - filled)
        print(f"\r[{bar}] {i - 1}/{len(missing)}  {human(elapsed)} elapsed"
              f"  ~{human(eta)} left   ", end="", flush=True)
        try:
            # Exactly the call the live inspector makes, so batch art and
            # on-demand art are indistinguishable.
            res = m._item_art(row.name, row.name)
            if res and not getattr(res, "offline", False):
                ok += 1
            else:
                fail += 1
                print(f"\n  offline/no result: {row.name}", flush=True)
        except KeyboardInterrupt:
            print("\n\nstopped — rerun to resume where this left off.")
            return 130
        except Exception as e:
            fail += 1
            print(f"\n  failed: {row.name}: {e}", flush=True)

    print(f"\r[{'█' * BAR_W}] {len(missing)}/{len(missing)}"
          f"  {human(time.time() - start)}                 ")
    print(f"\ndrawn: {ok}    failed: {fail}")
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
