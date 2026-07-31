"""Wipe the WORLD without destroying everything that shares its database.

Deleting ``oracle.db`` does work — the backend recreates the schema, re-seeds
Greenfields and re-ingests the SRD on the next boot — which is why it was the
standing advice. The problem is what else is in that file. One database holds
three kinds of data with three completely different lifecycles:

* **world state** — the graph, characters, combat, bastions, economy, hazards,
  reputation, tactical boards. This is what "wipe the world" means, and it is
  meant to be disposable.
* **reference data** — the ``rules_*`` tables. The SRD half re-downloads on the
  next boot IF the machine is online, but the OWNED-BOOK half does not come
  back on its own: it has to be re-parsed out of the PDF library. Species like
  khoravar, kalashtar, hexblood, reborn and the shifter lineages live only
  there.
* **generated art** — ``entity_image``, which is hours of GPU time and has no
  source to re-derive from except rendering it all again.

So the file-delete charges book-parsing and a full re-render for something that
is a ``DELETE FROM`` on a known set of tables. This script does that instead,
and leaves the other two categories alone.

    uv run python scripts/world_wipe.py            # show the plan, change nothing
    uv run python scripts/world_wipe.py --yes      # do it
    uv run python scripts/world_wipe.py --yes --images   # ...and drop the art too

Start the backend afterwards: it re-seeds the minimal world (Greenfields, its
tavern, the frontier stubs) the moment it finds ``greenfields`` missing.

**An unrecognised table is a hard error, never a guess.** A wipe that silently
keeps a new subsystem's rows leaves a haunted world; one that silently deletes
an unknown table could throw away the next irreplaceable thing. When a
subsystem adds a table, it gets classified here on purpose.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Disposable: the world and everything that happened in it. Matched by prefix,
# so a new table inside one of these subsystems is covered automatically.
WORLD_PREFIXES = (
    "world_",       # eight_card_system: entities, relations, events, the clock
    "combat_",      # encounters, combatants, logs
    "bastion_",     # bastions, facilities, their events
    "economy_",     # downtime, crafting projects
    "hazard_",      # afflictions
    "reputation_",  # standings
    "vtt_",         # tactical maps, tokens, effects, events
)
# Disposable, but not prefixed — the backend's own tables.
WORLD_EXACT = {"character", "sessionmemory"}

# Expensive to rebuild, and not world state. Never touched by a wipe.
REFERENCE_PREFIXES = ("rules_",)

# Generated art. Survives a wipe by default: it is GPU hours, and a picture of
# a longsword is not world state. --images opts into clearing it.
CACHE_EXACT = {"entity_image"}

# SQLite's own bookkeeping, not ours.
INTERNAL = {"sqlite_sequence", "sqlite_stat1"}


def classify(table: str) -> str:
    if table in INTERNAL:
        return "internal"
    if table in WORLD_EXACT or table.startswith(WORLD_PREFIXES):
        return "world"
    if table.startswith(REFERENCE_PREFIXES):
        return "reference"
    if table in CACHE_EXACT:
        return "cache"
    return "unknown"


def db_path_from_url(url: str) -> str:
    return url.split("///", 1)[1] if "///" in url else url


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true",
                    help="actually delete. Without it, the plan is printed and "
                         "nothing changes.")
    ap.add_argument("--images", action="store_true",
                    help="also clear generated art (entity_image). This is GPU "
                         "hours and does NOT come back on the next boot.")
    ap.add_argument("--database-url", default=None,
                    help="override DATABASE_URL / the backend default")
    a = ap.parse_args(argv)

    from sqlalchemy import create_engine, inspect, text

    url = a.database_url or os.getenv("DATABASE_URL")
    if not url:
        backend_db = Path(__file__).resolve().parent.parent / "oracle-dm-backend" / "oracle.db"
        url = f"sqlite:///{backend_db}"
    engine = create_engine(url)
    path = db_path_from_url(url)
    if url.startswith("sqlite") and not Path(path).is_file():
        print(f"no database at {path}\n"
              "Nothing to wipe — the backend will create and seed one on boot.")
        return 0

    tables = sorted(inspect(engine).get_table_names())
    if not tables:
        print(f"{path} has no tables; the backend will create them on boot.")
        return 0

    buckets: dict[str, list[tuple[str, int]]] = {
        "world": [], "reference": [], "cache": [], "unknown": [], "internal": []}
    with engine.connect() as c:
        for t in tables:
            try:
                n = c.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar() or 0
            except Exception:
                n = -1
            buckets[classify(t)].append((t, n))

    if buckets["unknown"]:
        print("UNRECOGNISED TABLE(S) — refusing to wipe:\n")
        for t, n in buckets["unknown"]:
            print(f"  {t:<28} {n:>8} rows")
        print("\nClassify each in scripts/world_wipe.py (WORLD_PREFIXES / "
              "WORLD_EXACT / REFERENCE_PREFIXES / CACHE_EXACT) and re-run.\n"
              "Guessing here either haunts the new world with stale rows or "
              "throws away something irreplaceable.")
        return 2

    doomed = list(buckets["world"]) + (list(buckets["cache"]) if a.images else [])
    kept = list(buckets["reference"]) + ([] if a.images else list(buckets["cache"]))

    print(f"database: {path}\n")
    print(f"WIPE ({len(doomed)} tables, {sum(n for _, n in doomed if n > 0)} rows)")
    for t, n in doomed:
        print(f"   - {t:<28} {n:>8} rows")
    print(f"\nKEEP ({len(kept)} tables, {sum(n for _, n in kept if n > 0)} rows)")
    for t, n in kept:
        print(f"   . {t:<28} {n:>8} rows")
    if not a.images:
        print("\n   (generated art kept — pass --images to clear it too)")

    if not a.yes:
        print("\nDry run. Nothing changed. Re-run with --yes to apply.")
        return 0

    with engine.begin() as c:
        for t, _ in doomed:
            c.execute(text(f'DELETE FROM "{t}"'))
        # Restart identity columns so a fresh world doesn't begin at id 4197.
        if "sqlite_sequence" in {t for t, _ in buckets["internal"]}:
            for t, _ in doomed:
                c.execute(text("DELETE FROM sqlite_sequence WHERE name = :n"),
                          {"n": t})
    try:
        with engine.connect() as c:
            c.exec_driver_sql("VACUUM")
    except Exception as e:
        print(f"(vacuum skipped: {e})")

    print(f"\nWiped {len(doomed)} table(s). Rules and "
          f"{'no art' if a.images else 'generated art'} kept.")
    print("Start the backend to re-seed Greenfields.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
