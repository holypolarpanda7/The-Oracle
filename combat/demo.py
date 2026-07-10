"""
End-to-end demo of the combat tracker (offline, temp DB).

Run:  uv run python -m combat.demo
"""
from __future__ import annotations

import os
import random
import tempfile

from rules.ingest import ingest_srd, seed_classes_and_subclasses  # noqa: F401
from combat import CombatTracker, Condition


def main() -> None:
    db = os.path.join(tempfile.gettempdir(), "oracle_combat_demo.db")
    if os.path.exists(db):
        os.remove(db)
    url = f"sqlite:///{db}"

    # Need at least one monster in rules_monster to hydrate a monster combatant.
    try:
        ingest_srd(database_url=url, spells=False)
    except Exception as e:
        print(f"(SRD ingest failed, monster add will be skipped: {e})")

    ct = CombatTracker(database_url=url)
    ct.create_tables()

    enc = ct.start_encounter("demo:channel", "Ambush on the Mill Road")
    ct.add_pc(enc.id, name="Lyra", max_hp=11, armor_class=13, dex_mod=3, character_id=1)
    try:
        ct.add_from_monster(enc.id, "goblin", count=2)
    except Exception as e:
        print(f"(no goblin in rules db: {e}) — adding a plain combatant instead")
        ct.add_combatant(enc.id, "Bandit", max_hp=11, armor_class=12, dex_mod=1)

    ct.roll_initiative(enc.id, rng=random.Random(7))
    print(ct.render(enc.id))
    print()

    order = ct.order(enc.id)
    target = next((c for c in order if c.kind != "pc"), order[-1])
    print(f"-> Lyra hits {target.name} for 7 damage")
    ct.apply_damage(target.id, 7)
    ct.add_condition(target.id, Condition.PRONE)

    enc2, cur = ct.next_turn(enc.id)
    print(f"-> next turn: round {enc2.round}, {cur.name if cur else 'nobody'}")
    print()
    print(ct.render(enc.id))

    os.remove(db)


if __name__ == "__main__":
    main()
