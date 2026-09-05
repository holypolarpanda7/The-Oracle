"""Is three.js still out of the first load?

The Activity is a character sheet, a narration column and a chat box until a
board comes out — and it was shipping a WebGL renderer to every one of them.
three.js is about two thirds of this application's JavaScript and nothing
before a fight can draw a triangle with it, so `VttOverlay` imports the
isometric board dynamically and vite gives it a chunk of its own.

That split is one `import(` away from being undone by accident: add a
top-level `import ... from "../lib/vttScene3d"` anywhere in the eager graph and
the chunk folds silently back into the entry. Nothing looks wrong — the board
still works, the app is just heavy again. So this is a budget, measured off the
built output.

    npm run build --prefix activity-ui && uv run python scripts/bundle_budget.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "activity-ui" / "dist" / "assets"

#: What the first load may weigh, in KB. Measured after the split at 427 and
#: set with room to grow: this is a guard against the CHUNK COLLAPSING (which
#: puts it back over a thousand), not a diet.
ENTRY_MAX_KB = 560

#: The board chunk has to exist at all. If vite stopped emitting it, the split
#: is gone whatever the entry weighs.
BOARD_CHUNK = "vttScene3d"


def entry_scripts(html: Path) -> list[str]:
    """The scripts the PAGE loads, which is not the same as the files vite
    emitted. Everything else in `assets/` is reached through an `import()` and
    is fetched when something asks for it — this build has one such chunk
    besides the board's. Counting the directory instead was the first version
    of this check and it failed the budget on a build that was fine."""
    import re
    return re.findall(r'<script[^>]+src="\.?/?(?:assets/)?([^"]+\.js)"',
                      html.read_text(encoding="utf-8"))


def main() -> int:
    if not DIST.is_dir():
        print(f"no build to measure at {DIST.relative_to(ROOT)} — run "
              f"`npm run build` in activity-ui first")
        return 1
    js = sorted(DIST.glob("*.js"), key=lambda p: -p.stat().st_size)
    if not js:
        print("no javascript in the build")
        return 1
    html = DIST.parent / "index.html"
    if not html.is_file():
        print("no index.html in the build")
        return 1
    eager_names = set(entry_scripts(html))
    board = [p for p in js if BOARD_CHUNK in p.name]
    entry_kb = sum(p.stat().st_size for p in js
                   if p.name in eager_names) / 1024
    board_kb = sum(p.stat().st_size for p in board) / 1024

    for p in js:
        mark = ("" if p.name in eager_names
                else "  (deferred)")
        print(f"  {p.stat().st_size / 1024:8.0f} KB  {p.name}{mark}")
    print(f"\n  first load {entry_kb:.0f} KB, board chunk {board_kb:.0f} KB")

    if not board:
        print(f"\nFAIL no {BOARD_CHUNK} chunk — the dynamic import has been "
              f"turned back into a static one, so three.js is in the first "
              f"load again")
        return 1
    if entry_kb > ENTRY_MAX_KB:
        print(f"\nFAIL the first load is {entry_kb:.0f} KB, over the "
              f"{ENTRY_MAX_KB} KB budget")
        return 1
    print(f"\nthe renderer stays out of the first load until a board opens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
