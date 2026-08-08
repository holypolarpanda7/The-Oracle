"""Which spells on a pasted list are ACTUALLY missing from the rules DB.

Written for the Tasha's import, and the reason it exists is that "not in the
DB" is the easy half. A book from 2020 and a rules DB seeded from 2024 sources
overlap heavily, and the overlaps come in four shapes:

* **Same name, already present.** Nothing to do. The 2024 text supersedes the
  2020 one, so importing would be a DOWNGRADE, not a gap.
* **Renamed.** The SRD drops the wizard's name from a spell — Bigby's Hand is
  `arcane-hand`, Leomund's Secret Chest is `secret-chest`. Slug lookup misses;
  the spell is there.
* **Reworded.** "Tasha's Caustic Brew" vs "Caustic Brew". Same spell, different
  label between printings.
* **Genuinely absent.** The real gap, and usually a much shorter list than the
  raw slug diff suggests.

So this matches on four passes — exact slug, exact name, name with any
possessive prefix stripped, and a normalized-token comparison — and reports
each hit with WHICH pass found it, because a fuzzy match is a suggestion to be
checked, not a fact. Only the fourth bucket is a gap worth importing.

Usage:
    uv run python scripts/spell_gap.py <file>      # one spell name per line
    uv run python scripts/spell_gap.py -           # names on stdin

Lines may be bare names ("Absorb Elements") or "Name — level N school" — only
the part before the first comma / em-dash / tab is read as the name. Blank
lines and lines starting with # are ignored.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{(ROOT / 'oracle-dm-backend' / 'oracle.db').as_posix()}")
sys.path.insert(0, str(ROOT))

GREEN, YELLOW, RED, DIM, BOLD, OFF = (
    "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[1m", "\033[0m")

# "Tasha's Caustic Brew" -> "caustic brew"; "Bigby's Hand" -> "hand".
_POSSESSIVE = re.compile(r"^[A-Za-z]+['’]s\s+", re.IGNORECASE)
_NOISE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Apostrophes are DROPPED, not split on: the curated slug convention is
    `tashas-caustic-brew`, and splitting gives the `tasha-s-` the bulk parsers
    produce — which is how the DB ended up carrying both
    `boon-of-fortunes-favor` and `boon-of-fortune-s-favor`."""
    return _NOISE.sub("-", name.strip().lower().replace("'", "")
                      .replace("’", "")).strip("-")


def norm(name: str) -> str:
    """Tokens only, possessive prefix dropped — the loosest comparison here."""
    return _NOISE.sub(" ", _POSSESSIVE.sub("", name.strip().lower())).strip()


def read_names(source: str) -> list[str]:
    raw = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    out: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Take the name only: "Catapult, 1st-level" / "Catapult — Transmutation".
        name = re.split(r"\s*[,—–\t]\s*|\s{2,}", line, maxsplit=1)[0].strip()
        name = re.sub(r"^[-*•\d.]+\s*", "", name).strip()
        if name:
            out.append(name)
    return list(dict.fromkeys(out))


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    names = read_names(argv[0])

    from rules.query import RulesLibrary
    from rules.models import Spell
    from sqlmodel import Session, select
    lib = RulesLibrary()
    with Session(lib.engine) as s:
        rows = list(s.exec(select(Spell)).all())
    by_slug = {r.index_slug: r for r in rows}
    by_name = {(r.name or "").strip().lower(): r for r in rows}
    by_norm: dict[str, list] = {}
    for r in rows:
        by_norm.setdefault(norm(r.name or ""), []).append(r)

    present, renamed, fuzzy, gaps = [], [], [], []
    for name in names:
        slug = slugify(name)
        if slug in by_slug:
            present.append((name, by_slug[slug]))
            continue
        row = by_name.get(name.strip().lower())
        if row is not None:
            present.append((name, row))
            continue
        # Possessive stripped: "Tasha's Caustic Brew" -> "caustic-brew".
        bare = slugify(_POSSESSIVE.sub("", name))
        row = by_slug.get(bare) or by_name.get(_POSSESSIVE.sub("", name).strip().lower())
        if row is not None:
            renamed.append((name, row))
            continue
        cand = by_norm.get(norm(name)) or []
        if cand:
            fuzzy.append((name, cand[0]))
            continue
        gaps.append(name)

    def show(title, colour, items, note=""):
        print(f"\n{BOLD}{colour}{title} ({len(items)}){OFF}"
              + (f" {DIM}{note}{OFF}" if note else ""))
        for entry in items:
            if isinstance(entry, tuple):
                name, row = entry
                arrow = "" if name.strip().lower() == (row.name or "").lower() \
                    else f"  ->  {row.name}"
                print(f"  {name:34} {DIM}[{row.index_slug}] L{row.level}"
                      f" {row.school or ''}{OFF}{arrow}")
            else:
                print(f"  {entry}")

    show("ALREADY IN THE DB — do not import", GREEN, present,
         "the DB's version is the newer printing")
    show("SAME SPELL, DIFFERENT NAME — do not import", YELLOW, renamed,
         "matched after dropping the possessive")
    show("PROBABLY THE SAME — CHECK BY HAND", YELLOW, fuzzy,
         "matched on normalized tokens; confirm before skipping")
    show("GENUINELY MISSING — import these", RED, gaps,
         "paste-and-translate into spells_overrides.json")

    print(f"\n{DIM}{len(names)} listed · {len(present)} present · "
          f"{len(renamed)} renamed · {len(fuzzy)} to check · "
          f"{len(gaps)} real gaps{OFF}")
    if gaps:
        print(f"\n{DIM}slugs for the import:{OFF}")
        print("  " + ", ".join(slugify(g) for g in gaps))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
