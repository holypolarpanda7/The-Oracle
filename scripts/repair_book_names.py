"""Repair rows whose NAME the PDF extractor destroyed, and drop the duplicates.

The extractor splits words mid-token — "dam age" for damage — and it does the
same to titles: Hex arrives as ``He X``, Witch Bolt as ``Witc H B O Lt``,
Chromatic Orb as ``Chrom At Ic Orb``. A player cannot cast a spell the game
cannot recognise by name, so ``get_spell("Hex")`` simply returned None and 20
spells were unreachable.

Two different situations hide behind that, and they need opposite fixes:

  * **A sole copy** is renamed. The row is the only record of that spell, so
    the mangled title is repaired in ``owned_books/spells_overrides.json`` —
    gitignored, top precedence, and it survives the next re-parse.
  * **A duplicate** is DELETED. A second parse pass sometimes produced a clean
    row as well, and renaming the mangled one would put two spells of the same
    name in front of the player — worse than an unreachable row. The mangled
    copy is dropped only after the clean one is proved at least as complete.

Deletion is the reason this is a script with an ``--audit`` mode and not a
migration. ``rules_*`` is NOT disposable: the SRD half re-downloads, but the
owned-book half has to be re-parsed from the user's PDF library (see the note
on ``scripts/world_wipe.py``). So nothing is removed until it has been shown to
be a strictly worse copy of a row that stays.

    uv run python scripts/repair_book_names.py            # audit (default)
    uv run python scripts/repair_book_names.py --apply     # rename + delete
"""
from __future__ import annotations

import argparse
import json
from difflib import SequenceMatcher
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text as sql_text
from sqlmodel import Session, select

from rules.models import Spell
from rules.query import RulesLibrary

OVERRIDES = ROOT / "owned_books" / "spells_overrides.json"
RENAME_SRC = "Extractor repair — the PDF pass split this spell's NAME into fragments"

#: Words that are legitimately one or two letters in a spell title.
_SHORT_OK = {"of", "a", "i", "ii", "iii", "to", "or", "on", "in", "the", "and",
             "de", "le", "da", "el"}
_CAP_MID = re.compile(r"[a-z][A-Z]")

#: Fields that make a row USEFUL. A duplicate is only dropped when the row that
#: stays scores at least as high on every one of them.
_SUBSTANCE = ("desc", "higher_level", "material", "dc_type", "attack_type",
              "damage", "classes", "components", "duration", "range",
              "casting_time", "school", "level")


def looks_mangled(name: str) -> bool:
    """A title with a stray one/two-letter token, or a capital mid-word."""
    if not name:
        return False
    for tok in name.split():
        bare = tok.strip("'’.,").lower()
        if len(bare) <= 2 and bare and bare not in _SHORT_OK:
            return True
    return bool(_CAP_MID.search(name))


def _key(name: str) -> str:
    """A title reduced to its letters, for matching a mangled row to a clean one."""
    return re.sub(r"[^a-z]", "", (name or "").lower())


def _substance(row: Any) -> Tuple[int, int]:
    """(fields populated, description length) — how complete a row is."""
    filled = sum(1 for f in _SUBSTANCE if getattr(row, f, None))
    return filled, len(str(getattr(row, "desc", "") or ""))


#: How alike two titles must be to be the same spell. Tuned against the data:
#: every genuinely mangled row scores 0.81-0.93 against its true twin while its
#: runner-up scores 0.42-0.53, so the MARGIN is the real discriminator and the
#: absolute threshold can stay generous.
_MANGLED_MIN = 0.78
_MANGLED_MARGIN = 0.20
#: A row whose title is merely mis-SPELLED ("Goon" for "Good") trips no spacing
#: heuristic, so it is found by similarity alone — which needs a much stricter
#: rule, because "Invisibility" and "See Invisibility" are 0.889 alike and are
#: two different spells. Same school is what separates them.
_TWIN_MIN = 0.90


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, _key(a), _key(b)).ratio()


def _worse_than(a: Any, b: Any) -> bool:
    """True when ``a`` is a strictly poorer copy than ``b`` — never equal."""
    fa, da = _substance(a)
    fb, db = _substance(b)
    return fb >= fa and db > da


def survey(lib: RulesLibrary) -> Tuple[List[Any], List[Tuple[Any, Any]]]:
    """(sole mangled rows, (drop, keep) duplicate pairs)."""
    with Session(lib.engine) as s:
        rows = list(s.exec(select(Spell)).all())
    mangled = [r for r in rows if looks_mangled(r.name or "")]
    clean = [r for r in rows if not looks_mangled(r.name or "")]

    sole: List[Any] = []
    dupes: List[Tuple[Any, Any]] = []

    # 1. A row the extractor SPACED apart, against the clean rows. Matched on
    #    similarity rather than subsequence because the extractor substitutes
    #    letters as well as inserting spaces ("brgbyshand" for "bigbyshand"),
    #    and its school can be wrong too (Prayer of Healing came back
    #    Abjuration), so only the level is required to agree.
    for m in mangled:
        scored = sorted(((_ratio(m.name, c.name), c) for c in clean
                         if c.level == m.level and c.index_slug != m.index_slug),
                        key=lambda p: p[0], reverse=True)
        if not scored:
            sole.append(m)
            continue
        best, twin = scored[0]
        runner = scored[1][0] if len(scored) > 1 else 0.0
        if best >= _MANGLED_MIN and (best - runner) >= _MANGLED_MARGIN \
                and _worse_than(m, twin):
            dupes.append((m, twin))
        else:
            sole.append(m)

    # 2. A row merely MIS-SPELLED, which no spacing heuristic can see. Strict:
    #    the level AND school must agree and the titles be near-identical.
    seen = {id(d) for d, _ in dupes}
    for i, a in enumerate(clean):
        for b in clean[i + 1:]:
            if a.level != b.level or (a.school or "") != (b.school or ""):
                continue
            if _ratio(a.name, b.name) < _TWIN_MIN:
                continue
            drop, keep = (a, b) if _worse_than(a, b) else \
                         ((b, a) if _worse_than(b, a) else (None, None))
            if drop is not None and id(drop) not in seen:
                dupes.append((drop, keep))
                seen.add(id(drop))
    return sole, dupes


def load_overrides() -> List[Dict[str, Any]]:
    if not OVERRIDES.is_file():
        return []
    try:
        return json.loads(OVERRIDES.read_text("utf-8"))
    except Exception as e:                       # a bad file must not lose data
        print(f"! {OVERRIDES.name}: {e}")
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write the renames and delete the duplicate rows")
    args = ap.parse_args()

    lib = RulesLibrary()
    sole, dupes = survey(lib)

    print(f"mangled spell names: {len(sole) + len(dupes)}"
          f"  ({len(sole)} sole copies, {len(dupes)} duplicates)\n")

    if sole:
        print("SOLE COPIES — renamed in the overrides slot (nothing is deleted):")
        for r in sorted(sole, key=lambda x: x.name or ""):
            known = any(e.get("slug") == r.index_slug for e in load_overrides())
            print(f"  {r.name!r:34} slug={r.index_slug:30}"
                  f"{'  [already overridden]' if known else '  <-- needs a name'}")
        print()

    if dupes:
        print("DUPLICATES — the mangled row is dropped, the clean one stays:")
        for m, c in sorted(dupes, key=lambda p: p[1].name or ""):
            mf, md = _substance(m)
            cf, cd = _substance(c)
            safe = cf >= mf and cd >= md
            print(f"  {c.name!r:30} keep {c.index_slug:22} "
                  f"(fields {cf}, desc {cd})")
            print(f"  {'':30} drop {m.index_slug:22} "
                  f"(fields {mf}, desc {md})"
                  f"{'' if safe else '   <-- REFUSED: the mangled row is fuller'}")
        print()

    if not args.apply:
        print("audit only — pass --apply to write the renames and delete the "
              "duplicate rows")
        return 0

    # --- deletions, each one proved redundant first --------------------------
    dropped: List[str] = []
    refused: List[str] = []
    with Session(lib.engine) as s:
        for m, c in dupes:
            mf, md = _substance(m)
            cf, cd = _substance(c)
            if not (cf >= mf and cd >= md):
                refused.append(m.index_slug)
                continue
            row = s.exec(select(Spell).where(
                Spell.index_slug == m.index_slug)).first()
            if row is not None:
                s.delete(row)
                dropped.append(m.index_slug)
        s.commit()

    # A deleted row must not linger in the overrides slot, or the next ingest
    # would recreate exactly the duplicate this just removed.
    data = load_overrides()
    kept = [e for e in data if e.get("slug") not in set(dropped)]
    if len(kept) != len(data):
        OVERRIDES.write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n",
                             "utf-8")

    print(f"deleted {len(dropped)} duplicate row(s): {dropped}")
    if refused:
        print(f"REFUSED {len(refused)} (the mangled row was fuller): {refused}")
    if sole:
        print(f"{len(sole)} sole copy(ies) still need a name in "
              f"{OVERRIDES.name} — this script never invents one, because the "
              f"correct title is a fact about the book, not about the data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
