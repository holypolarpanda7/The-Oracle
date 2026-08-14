"""Prove a boss fights like a boss: Legendary Resistance and legendary actions.

59 monsters here carry ``legendary_actions`` and the combat engine contained no
reference to the column — and ``format_monster_brief`` did not print it either,
so a CR 14 dragon both fought like a brute and gave the DM nothing to run one
with. Legendary Resistance was worse, because it changes OUTCOMES: it lives as
a sentence inside ``special_abilities`` and nothing read it, so every
save-or-suck landed on the first try.

Offline: a scratch copy of the rules DB, no GPU, no LLM.
"""
from __future__ import annotations
import os, shutil, sys, tempfile
from pathlib import Path

ROOT = Path("/mnt/d/Projects/The Oracle")
sys.path.insert(0, str(ROOT))

live = ROOT / "oracle-dm-backend" / "oracle.db"
db = Path(tempfile.gettempdir()) / "oracle_legendary_check.db"
if db.exists():
    db.unlink()
if live.is_file():
    shutil.copy(live, db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"

# The rules DB is a couple of hundred megabytes and /tmp is a tmpfs, so a run
# that leaves its copy behind is a run that eventually fills the disk.
import atexit


@atexit.register
def _cleanup() -> None:
    for suffix in ("", "-shm", "-wal"):
        try:
            Path(str(db) + suffix).unlink(missing_ok=True)
        except Exception:
            pass

from sqlmodel import Session, SQLModel, select
from rules import legendary as lg
from rules.models import Monster
from rules.query import RulesLibrary, format_monster_brief
from combat.tracker import CombatTracker
from combat.engine import CombatEngine

lib = RulesLibrary()
fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        fails.append(name)


# ---------------------------------------------------------------------------
# 1. reading the stat block's prose
# ---------------------------------------------------------------------------
dragon = lib.get_monster("Adult Black Dragon")
check("a dragon's Legendary Resistance is found in its prose",
      dragon is not None and lg.resistance_uses(dragon) == 3,
      str(lg.resistance_uses(dragon)) if dragon else "no dragon")
check("its legendary actions are found", bool(lg.actions_of(dragon)),
      str(len(lg.actions_of(dragon))))
check("and it gets a per-round budget", lg.action_uses(dragon) >= 1,
      str(lg.action_uses(dragon)))

goblin = lib.get_monster("Goblin") or lib.get_monster("Wolf")
if goblin is not None:
    check("an ordinary creature has neither",
          lg.resistance_uses(goblin) == 0 and not lg.actions_of(goblin)
          and lg.action_uses(goblin) == 0 and not lg.is_legendary(goblin),
          goblin.name)

# The OCR writes "1f" for "If" and strips spaces; the count must still parse.
class _Fake:
    special_abilities = [{"name": "X",
                          "desc": "Legendary Resistance (4/Day,or 5/Day in Lair). "
                                  "1f the creature fails a saving throw..."}]
    legendary_actions = None
    actions = None
    desc = None


check("the OCR's mangled spacing still parses the count",
      lg.resistance_uses(_Fake()) == 4, str(lg.resistance_uses(_Fake())))

with Session(lib.engine) as s:
    mons = s.exec(select(Monster)).all()
lr = [m for m in mons if lg.resistance_uses(m)]
la = [m for m in mons if lg.actions_of(m)]
check("a real share of the bestiary is legendary",
      len(lr) >= 15 and len(la) >= 40, f"{len(lr)} with LR, {len(la)} with actions")

# ---------------------------------------------------------------------------
# 2. the DM can actually SEE them
# ---------------------------------------------------------------------------
brief = format_monster_brief(dragon)
check("the monster brief prints the legendary block",
      "Legendary Resistance" in brief and "Legendary actions" in brief)
check("...and says when they may be taken",
      "end of ANOTHER creature's turn" in brief)
if goblin is not None:
    check("an ordinary creature's brief gains nothing",
          "Legendary" not in format_monster_brief(goblin), goblin.name)

# ---------------------------------------------------------------------------
# 3. the ENGINE spends them — a failed save becomes a success
# ---------------------------------------------------------------------------
# The live DB's combat tables can predate a column the code now has (SQLModel
# creates tables but never ALTERs them). This test only wants the RULES tables
# from the copy, so the combat ones are dropped and rebuilt at the current
# schema — which is also what makes the run independent of any live encounter.
from sqlalchemy import text as _text
with lib.engine.begin() as _conn:
    for _tbl in ("combat_combatant", "combat_encounter", "combat_log"):
        _conn.execute(_text(f"DROP TABLE IF EXISTS {_tbl}"))
SQLModel.metadata.create_all(lib.engine)
tracker = CombatTracker(engine=lib.engine)
enc = tracker.start_encounter("legendary:test", "Lair")
def _one(res):
    return res[0] if isinstance(res, list) else res


boss = _one(tracker.add_from_monster(enc.id, dragon.index_slug, initiative=20))
mook = _one(tracker.add_from_monster(enc.id, (goblin or dragon).index_slug, initiative=1))

eng = CombatEngine(tracker=tracker)


class _Save:
    """A failed saving throw, shaped like dice.mechanics.CheckResult."""
    def __init__(self):
        self.success = False
        self.detail = "WIS save [3]+2 = 5 vs DC 18 → FAIL"


fresh = next(c for c in tracker.order(enc.id) if c.id == boss.id)
sv = _Save()
notes: list[str] = []
used = eng._legendary_rescue(fresh, sv, notes)
check("a boss turns its failed save into a success", used and sv.success is True,
      sv.detail)
check("...and the roll says so", "Legendary Resistance" in sv.detail, sv.detail)
check("...and the table is told", bool(notes), str(notes))

# It must run OUT.
total = lg.resistance_uses(dragon)
spent = 1
for _ in range(total + 2):
    fresh = next(c for c in tracker.order(enc.id) if c.id == boss.id)
    s2 = _Save()
    if eng._legendary_rescue(fresh, s2):
        spent += 1
check("it can only be spent as often as the block allows", spent == total,
      f"spent {spent}, block says {total}")

fresh = next(c for c in tracker.order(enc.id) if c.id == boss.id)
s3 = _Save()
check("once exhausted, a failed save stays failed",
      not eng._legendary_rescue(fresh, s3) and s3.success is False)

# A successful save is never "rescued" (and never wastes a use).
tracker2 = CombatTracker(engine=lib.engine)
enc2 = tracker2.start_encounter("legendary:test2", "Lair")
boss2 = _one(tracker2.add_from_monster(enc2.id, dragon.index_slug, initiative=20))
eng2 = CombatEngine(tracker=tracker2)
ok_save = _Save(); ok_save.success = True
fresh2 = next(c for c in tracker2.order(enc2.id) if c.id == boss2.id)
check("a save that already succeeded spends nothing",
      not eng2._legendary_rescue(fresh2, ok_save)
      and not any("legres" in str(x) for x in (fresh2.conditions or [])))

# An ordinary monster is untouched.
if goblin is not None and goblin.index_slug != dragon.index_slug:
    fresh3 = next(c for c in tracker.order(enc.id) if c.id == mook.id)
    s4 = _Save()
    check("an ordinary monster gets no rescue",
          not eng._legendary_rescue(fresh3, s4) and s4.success is False)

print()
print(f"{len(fails)} failure(s)" if fails else "ALL PASS")
if fails:
    print("\n".join(f"  - {f}" for f in fails))
sys.exit(1 if fails else 0)
