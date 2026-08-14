"""Prove a buff spell adds damage to your ATTACKS, without the DM narrating it.

Spirit Shroud, Conjure Minor Elementals, Hunter's Mark and their kin deal no
damage themselves — they add dice to your attacks for the duration. The engine
had no concept of "an active spell that modifies a later attack", so casting one
from the hotbar spent the slot, set concentration, and then changed nothing at
all. The whole point of the spell landed only if the DM remembered it.

Nothing here writes a die count: the numbers come from each spell's own row, so
an upcast scales them and a house rule in the overrides slot changes them.

Offline: a scratch copy of the rules DB, no GPU, no LLM.
"""
from __future__ import annotations
import atexit, os, shutil, sys, tempfile
from pathlib import Path

ROOT = Path("/mnt/d/Projects/The Oracle")
sys.path.insert(0, str(ROOT))

live = ROOT / "oracle-dm-backend" / "oracle.db"
db = Path(tempfile.gettempdir()) / "oracle_rider_check.db"
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
from sqlmodel import SQLModel
from rules.query import RulesLibrary
from rules.spell_scaling import rider_dice, rider_type
from combat.engine import CombatEngine, _SPELL_EFFECTS
from combat.tracker import CombatTracker

lib = RulesLibrary()
fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        fails.append(name)


# ---------------------------------------------------------------------------
# 1. the dice come from the SPELL, not from a number written in the engine
# ---------------------------------------------------------------------------
for slug in ("conjure minor elementals", "spirit shroud", "hunter's mark",
             "hex", "divine favor", "elemental weapon"):
    check(f"'{slug}' is registered as a rider",
          bool(_SPELL_EFFECTS.get(slug, {}).get("attack_rider")))

check("no rider dice are hard-coded in the engine",
      not any("dice" in v for k, v in _SPELL_EFFECTS.items()
              if v.get("attack_rider")),
      "the numbers must come from the spell row")

cme = lib.get_spell("Conjure Minor Elementals")
ss = lib.get_spell("Spirit Shroud")
hm = lib.get_spell("Hunter's Mark")
if cme is not None:
    got = [rider_dice(cme, slot_level=s) for s in (4, 5, 7, 9)]
    check("Conjure Minor Elementals' rider scales with the slot",
          got == ["1d8", "2d8", "3d8", "4d8"], str(got))
if ss is not None:
    check("Spirit Shroud matches it at every slot",
          [rider_dice(ss, slot_level=s) for s in (4, 5, 7, 9)]
          == [rider_dice(cme, slot_level=s) for s in (4, 5, 7, 9)],
          str([rider_dice(ss, slot_level=s) for s in (4, 5, 7, 9)]))
if hm is not None:
    check("Hunter's Mark keeps its type and does NOT scale with the slot",
          rider_dice(hm) == "1d6" and rider_type(hm) == "force"
          and rider_dice(hm, slot_level=5) == "1d6",
          f"{rider_dice(hm)} {rider_type(hm)}")

fb = lib.get_spell("Fireball")
check("an ordinary damage spell is not a rider",
      fb is None or rider_dice(fb) is None, str(rider_dice(fb) if fb else None))

# ---------------------------------------------------------------------------
# 2. it lands on a real attack, through the real engine
# ---------------------------------------------------------------------------
with lib.engine.begin() as conn:
    for tbl in ("combat_combatant", "combat_encounter", "combat_log"):
        conn.execute(_text(f"DROP TABLE IF EXISTS {tbl}"))
SQLModel.metadata.create_all(lib.engine)

tracker = CombatTracker(engine=lib.engine)
eng = CombatEngine(tracker=tracker)
enc = tracker.start_encounter("rider:test", "Field")
hero = tracker.add_combatant(enc.id, "Hero", kind="pc", max_hp=40, armor_class=15,
                             initiative=20)
foe = tracker.add_combatant(enc.id, "Foe", kind="monster", max_hp=60,
                            armor_class=10, initiative=1)


def conds(cid: int) -> list[str]:
    return [str(x) for x in (tracker.get_combatant(cid).conditions or [])]


ev: dict = {"notes": []}
eng._apply_attack_rider(tracker.get_combatant(hero.id), cme,
                        _SPELL_EFFECTS["conjure minor elementals"], 4, None,
                        None, ev)
riders = [c for c in conds(hero.id) if c.startswith("rider:")]
check("casting it records a rider on the caster", len(riders) == 1, str(riders))
check("...and the table is told what it does", bool(ev["notes"]), str(ev["notes"]))
check("...carrying the dice the spell states, not a constant",
      "1d8" in riders[0], riders[0])

fresh_h = tracker.get_combatant(hero.id)
fresh_f = tracker.get_combatant(foe.id)
found = eng._attack_riders(fresh_h, fresh_f)
check("the rider applies to an attack on any creature (no radius provider)",
      len(found) == 1 and found[0][1] == "1d8", str(found))

# Re-casting replaces rather than stacks.
eng._apply_attack_rider(fresh_h, cme,
                        _SPELL_EFFECTS["conjure minor elementals"], 9, None,
                        None, {"notes": []})
riders = [c for c in conds(hero.id) if c.startswith("rider:")]
check("re-casting REPLACES the rider instead of stacking it",
      len(riders) == 1 and "4d8" in riders[0], str(riders))

# ---------------------------------------------------------------------------
# 3. a marking rider only pays out against what it marked
# ---------------------------------------------------------------------------
if hm is not None:
    other = tracker.add_combatant(enc.id, "Bystander", kind="monster",
                                  max_hp=20, armor_class=10, initiative=2)
    eng._apply_attack_rider(tracker.get_combatant(hero.id), hm,
                            _SPELL_EFFECTS["hunter's mark"], 1, None,
                            tracker.get_combatant(foe.id), {"notes": []})
    h = tracker.get_combatant(hero.id)
    on_marked = eng._attack_riders(h, tracker.get_combatant(foe.id))
    on_other = eng._attack_riders(h, tracker.get_combatant(other.id))
    check("Hunter's Mark pays out against its quarry",
          any(s == "hunter's mark" for s, _, _ in on_marked), str(on_marked))
    check("...and NOT against a bystander",
          not any(s == "hunter's mark" for s, _, _ in on_other), str(on_other))
    check("...while the emanation rider still applies to both",
          any(s == "conjure minor elementals" for s, _, _ in on_other),
          str(on_other))

# ---------------------------------------------------------------------------
# 4. it ends when the concentration holding it ends
# ---------------------------------------------------------------------------
tracker.set_concentration(hero.id, "Conjure Minor Elementals")
tracker.set_concentration(hero.id, None)
left = [c for c in conds(hero.id) if "conjure minor elementals" in c.lower()]
check("dropping concentration ends the rider", not left, str(left))
check("...and a DIFFERENT spell's rider survives it",
      any("hunter's mark" in c.lower() for c in conds(hero.id)),
      str([c for c in conds(hero.id) if c.startswith("rider:")]))

# ---------------------------------------------------------------------------
# 5. damaged book text is flagged, never invented
# ---------------------------------------------------------------------------
class _Unreadable:
    name = "Mystery Ward"
    level = 3
    desc = "Your attacks deal extra damage for the duration."
    higher_level = None
    damage = None


ev2: dict = {"notes": []}
eng._apply_attack_rider(tracker.get_combatant(hero.id), _Unreadable(),
                        {"attack_rider": True}, 3, None, None, ev2)
check("an unreadable rider adds no condition and says so",
      not any("mystery" in c.lower() for c in conds(hero.id))
      and any("unreadable" in n for n in ev2["notes"]), str(ev2["notes"]))

print()
print(f"{len(fails)} failure(s)" if fails else "ALL PASS")
if fails:
    print("\n".join(f"  - {f}" for f in fails))
sys.exit(1 if fails else 0)
