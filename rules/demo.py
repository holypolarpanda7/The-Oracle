"""
Runnable demo for the SRD rules reference.

    uv run python -m rules.demo

Ingests monsters + spells into a throwaway SQLite file, then prints a couple of
formatted lookups (goblin stat line, fireball) plus an encounter-style search.
"""
from __future__ import annotations

from pathlib import Path

from .ingest import ingest_srd
from .query import RulesLibrary, format_monster_brief, format_spell_brief

DEMO_DB = Path(__file__).resolve().parent / "demo_rules.db"


def _rule(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main() -> None:
    if DEMO_DB.exists():
        DEMO_DB.unlink()
    url = f"sqlite:///{DEMO_DB}"

    _rule("Ingesting SRD dataset (network required)")
    counts = ingest_srd(database_url=url)
    print(counts)

    lib = RulesLibrary(database_url=url)
    print("stored:", lib.count())

    _rule("Monster lookup: goblin")
    print(format_monster_brief(lib.get_monster("goblin")))

    _rule("Spell lookup: fireball")
    print(format_spell_brief(lib.get_spell("fireball")))

    _rule("Encounter search: CR 0-1 beasts")
    for m in lib.search_monsters(cr_min=0, cr_max=1, type="beast", limit=8):
        print(f"- {m.name} (CR {m.challenge_rating}, AC {m.armor_class}, HP {m.hit_points})")

    print(f"\n(Demo DB written to: {DEMO_DB})")


if __name__ == "__main__":
    main()
