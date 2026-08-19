"""A spell that hits or forces a save must actually DO something.

The combat engine had two damage branches and keyed them on `Spell.attack_type`
and `Spell.dc_type`. Those columns are populated on 7 and 20 rows out of 431 —
every spell in this project came out of a PDF, and the parser only ever filled
them from the tidy SRD shape — so a spell with NEITHER fell past both branches
and went off dealing nothing at all. No attack roll, no save, no damage, and no
complaint anywhere: the slot was spent, the narration said something happened,
and the target's hit points never moved. A player reported it as "damage not
being applied by my spells", which is exactly what it was.

`rules.targeting.resolution_for` reads the column first and the spell's own
prose after — the same doctrine as `rules/damage.py` and `rules/components.py`,
and derived rather than stored for the same reason. The OCR tolerance is the
job here too: Inflict Wounds arrives as "Constit ution saving th row", so the
words are spelled out letter by letter, anchored on a closed vocabulary.

Offline: a scratch copy of the rules DB, no GPU, no LLM.

    uv run python scripts/spell_resolve_smoke.py
"""
from __future__ import annotations
import atexit, os, shutil, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

live = ROOT / "oracle-dm-backend" / "oracle.db"
db = Path(tempfile.gettempdir()) / "oracle_spell_resolve.db"
if db.exists():
    db.unlink()
if live.is_file():
    shutil.copy(live, db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"


@atexit.register
def _cleanup() -> None:
    for suffix in ("", "-shm", "-wal"):
        try:
            Path(str(db) + suffix).unlink(missing_ok=True)
        except Exception:
            pass


from sqlalchemy import text as _text
from sqlmodel import Session, SQLModel, select
from rules.models import Spell
from rules.query import RulesLibrary
from rules import targeting
from combat.engine import CombatEngine, PCProfile
from combat.tracker import CombatTracker

lib = RulesLibrary()
fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        fails.append(name)


print("\n1. how a spell resolves, read off the spell")
with Session(lib.engine) as s:
    rows = list(s.exec(select(Spell)).all())
col_atk = [r for r in rows if r.attack_type]
col_dc = [r for r in rows if r.dc_type]
got_atk = [r for r in rows if targeting.attack_kind(r)]
got_dc = [r for r in rows if targeting.save_ability(r)]
check("the COLUMNS answer for almost nothing",
      len(col_atk) + len(col_dc) < len(rows) // 10,
      f"{len(col_atk)} attack + {len(col_dc)} save of {len(rows)} spells")
check("the spells' own prose answers for far more",
      len(got_atk) > 4 * len(col_atk) and len(got_dc) > 4 * len(col_dc),
      f"{len(got_atk)} attack + {len(got_dc)} save")

for name, want in (("Eldritch Blast", ("ranged", None)),
                   ("Fire Bolt", ("ranged", None)),
                   ("Shocking Grasp", ("melee", None)),
                   ("Fireball", (None, "dex")),
                   ("Sacred Flame", (None, "dex")),
                   ("Toll the Dead", (None, "wis"))):
    sp = lib.get_spell(name)
    if sp is None:
        continue
    check(f"{name} resolves the way the book says",
          targeting.resolution_for(sp) == want, str(targeting.resolution_for(sp)))

iw = lib.get_spell("Inflict Wounds")
check("…even through 'Constit ution saving th row'",
      iw is None or targeting.save_ability(iw) == "con",
      repr((iw.desc or "")[:44]) if iw else "absent")

for name in ("Cure Wounds", "Shield", "Bless", "Magic Missile"):
    sp = lib.get_spell(name)
    if sp is None:
        continue
    check(f"{name} is neither, and must stay neither",
          targeting.resolution_for(sp) == (None, None),
          str(targeting.resolution_for(sp)))

print("\n2. and the damage actually lands, through the real engine")
with lib.engine.begin() as conn:
    for tbl in ("combat_combatant", "combat_encounter", "combat_log"):
        conn.execute(_text(f"DROP TABLE IF EXISTS {tbl}"))
SQLModel.metadata.create_all(lib.engine)

tracker = CombatTracker(engine=lib.engine)
eng = CombatEngine(tracker=tracker)


def profile() -> PCProfile:
    return PCProfile(
        character_id=1, name="Wick", level=5,
        ability_mods={"str": 0, "dex": 2, "con": 2, "int": 1, "wis": 1, "cha": 4},
        prof=3, spell_attack_bonus=7, spell_dc=15, spell_mod="cha",
        slots={1: 4, 2: 3, 3: 2})


def cast(spell_name: str, *, target_ac: int = 1, target_hp: int = 80) -> tuple[int, list]:
    """Cast once at a sitting duck and report the HP it lost."""
    enc = tracker.start_encounter(f"resolve:{spell_name}", "Test")
    hero = tracker.add_combatant(enc.id, "Wick", kind="pc", max_hp=40,
                                 armor_class=15, initiative=20, character_id=1)
    foe = tracker.add_combatant(enc.id, "Dummy", kind="monster", max_hp=target_hp,
                                armor_class=target_ac, initiative=1)
    # Initiative 20 vs 1, so the caster is up first.
    tracker.roll_initiative(enc.id)
    rep = eng.resolve(enc.id, [{"verb": "cast", "arg": spell_name,
                                "target": "Dummy"}],
                      {1: profile()})
    after = tracker.get_combatant(foe.id)
    tracker.end_encounter(enc.id)
    return target_hp - after.current_hp, rep.events


# AC 1 so the attack cannot plausibly miss, and the save spells roll against a
# DC 15 the dummy has no hope of beating with a flat +0 — a legitimate miss
# would prove nothing either way, and this test is about whether damage is
# APPLIED at all.
lost, events = cast("Eldritch Blast")
check("Eldritch Blast rolls an attack and deals its damage", lost > 0,
      f"{lost} HP; events {[e['kind'] for e in events]}")
check("…and it is recorded as a HIT, not as a spell that merely went off",
      any(e.get("kind") == "cast" and e.get("hit") for e in events))

lost, _ = cast("Fire Bolt")
check("Fire Bolt too", lost > 0, f"{lost} HP")

lost, events = cast("Sacred Flame")
check("a save spell forces the save and applies what it deals", lost > 0,
      f"{lost} HP; events {[e['kind'] for e in events]}")

lost, _ = cast("Fireball")
check("Fireball too", lost > 0, f"{lost} HP")

# Nothing that heals or buffs should be dealing damage to its target.
lost, _ = cast("Bless")
check("a buff still deals nothing", lost == 0, f"{lost} HP")

print()
if fails:
    print(f"\033[31m{len(fails)} check(s) failed:\033[0m " + "; ".join(fails))
    raise SystemExit(1)
print("\033[32ma spell that hits or forces a save now does something\033[0m")
