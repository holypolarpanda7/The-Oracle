"""A spell's LEVEL, checked against the SRD's own clean text.

The PHB extraction writes a spell's level as a glyph, and one of those glyphs
is ambiguous: `J` is the OCR of both 1 and 3 in that typeface. Read as a 1 it
put Slow — a level 3 spell — on the level 1 list, where a fresh wizard could
take it. Nothing else in the game would ever notice: the spell has a level, the
level is a number, and it is wrong.

The SRD file is not an OCR of a scan, so its "Level 3 Transmutation" is exactly
that, and it covers ~330 of the spells here. This audits every row against it
and repairs the ones that disagree. Spells the SRD doesn't carry (owned-book
and homebrew) cannot be checked and are reported as such, never guessed at.

Usage:
  uv run python scripts/repair_spell_levels.py            # audit only
  uv run python scripts/repair_spell_levels.py --apply    # write the fixes
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GREEN, RED, YELLOW, DIM, BOLD, OFF = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m")


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    from sqlmodel import Session, select
    from rules.models import Spell
    from rules.query import RulesLibrary
    from rules.owned_ingest import srd_spell_levels, _collapse_key

    srd = srd_spell_levels()
    if not srd:
        print(f"{RED}No SRD text in owned_books/ — nothing to check against.{OFF}")
        return 1
    print(f"{DIM}{len(srd)} spell levels read from the SRD text{OFF}")

    lib = RulesLibrary()
    with Session(lib.engine) as s:
        rows = list(s.exec(select(Spell)))
        wrong = [(r, srd[_collapse_key(r.name)]) for r in rows
                 if _collapse_key(r.name) in srd
                 and srd[_collapse_key(r.name)] != r.level]
        unchecked = [r for r in rows if _collapse_key(r.name) not in srd]

        print(f"\n{BOLD}{len(rows)} spells · {len(rows) - len(unchecked)} checkable "
              f"· {len(wrong)} wrong{OFF}")
        for r, real in wrong:
            print(f"  {YELLOW}{r.name:36}{OFF} stored level {r.level} → {real}")
        if unchecked:
            print(f"\n{DIM}{len(unchecked)} not in the SRD (owned-book or homebrew) — "
                  f"can't be checked from here{OFF}")

        if not wrong:
            print(f"\n{GREEN}every checkable spell's level agrees with the SRD{OFF}")
            return 0
        if not apply:
            print(f"\n{DIM}re-run with --apply to write these{OFF}")
            return 0
        for r, real in wrong:
            r.level = int(real)
            s.add(r)
        s.commit()
    print(f"\n{GREEN}repaired {len(wrong)} spell level(s){OFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
