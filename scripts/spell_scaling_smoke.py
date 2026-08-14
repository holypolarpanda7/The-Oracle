"""Prove a spell GROWS — with the caster's level, and with the slot it was cast in.

Both rules were in the data and neither reached a die roll. Only 17 of 430
spells here carry the structured `damage_at_slot_level` rows the engine already
knew how to read; every other spell fell to a description parse that returned
the BASE dice forever. So a level-17 Fire Bolt rolled 1d10 instead of 4d10, and
Fireball from a 5th-level slot dealt exactly what it deals from a 3rd.

The two traps this pins, because a generic "one more die per tier" rule breaks
both: Eldritch Blast scales BEAMS and Magic Missile scales DARTS.

Offline: reads the real rules DB, no GPU, no LLM.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path("/mnt/d/Projects/The Oracle")
sys.path.insert(0, str(ROOT))

from sqlmodel import Session, select
from rules import damage as dmg
from rules.models import Spell
from rules.query import RulesLibrary, format_spell_brief
from rules.spell_scaling import (_add_dice, parse_scaling, scaled_dice,
                                 scaling_note)

lib = RulesLibrary()
fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def base_of(sp) -> str | None:
    pk = dmg.parse_damage(getattr(sp, "desc", None))
    return pk[0].dice if pk else None


# ---------------------------------------------------------------------------
# 1. the dice arithmetic itself
# ---------------------------------------------------------------------------
check("adding like dice grows the count", _add_dice("8d6", "1d6", 2) == "10d6",
      str(_add_dice("8d6", "1d6", 2)))
check("a trailing modifier survives", _add_dice("2d8+3", "1d8", 1) == "3d8+3",
      str(_add_dice("2d8+3", "1d8", 1)))
check("unlike dice are appended, not merged",
      _add_dice("1d10", "1d4", 2) == "1d10+2d4", str(_add_dice("1d10", "1d4", 2)))
check("zero steps changes nothing", _add_dice("8d6", "1d6", 0) == "8d6")

# ---------------------------------------------------------------------------
# 2. cantrips grow with the CASTER's level
# ---------------------------------------------------------------------------
fb = lib.get_spell("Fire Bolt")
if fb is not None:
    got = [scaled_dice(fb, base_of(fb), character_level=L) for L in (1, 4, 5, 10, 11, 16, 17, 20)]
    check("Fire Bolt steps at 5, 11 and 17",
          got == ["1d10", "1d10", "2d10", "2d10", "3d10", "3d10", "4d10", "4d10"],
          str(got))

for name, want17 in (("Acid Splash", "4d6"), ("Poison Spray", "4d12"),
                     ("Ray of Frost", "4d8")):
    sp = lib.get_spell(name)
    if sp is None:
        continue
    check(f"{name} reaches {want17} at level 17",
          scaled_dice(sp, base_of(sp), character_level=17) == want17,
          str(scaled_dice(sp, base_of(sp), character_level=17)))

# ---------------------------------------------------------------------------
# 3. the two spells a generic rule would WRECK
# ---------------------------------------------------------------------------
eb = lib.get_spell("Eldritch Blast")
if eb is not None:
    check("Eldritch Blast is NOT scaled (it adds beams, not dice)",
          scaled_dice(eb, base_of(eb), character_level=17) == base_of(eb),
          f"{base_of(eb)} -> {scaled_dice(eb, base_of(eb), character_level=17)}")
mm = lib.get_spell("Magic Missile")
if mm is not None:
    check("Magic Missile is NOT scaled (it adds darts, not dice)",
          scaled_dice(mm, base_of(mm), slot_level=5) == base_of(mm),
          f"{base_of(mm)} -> {scaled_dice(mm, base_of(mm), slot_level=5)}")
    check("...and it is not falsely flagged as unreadable",
          not parse_scaling(mm).upcast_unreadable)

# ---------------------------------------------------------------------------
# 4. leveled spells grow with the SLOT
# ---------------------------------------------------------------------------
fbl = lib.get_spell("Fireball")
if fbl is not None:
    got = [scaled_dice(fbl, base_of(fbl), slot_level=s) for s in (3, 4, 5, 9)]
    check("Fireball grows a d6 per slot above 3rd",
          got == ["8d6", "9d6", "10d6", "14d6"], str(got))
    check("a slot BELOW the base level never shrinks it",
          scaled_dice(fbl, base_of(fbl), slot_level=1) == "8d6")

gb = lib.get_spell("Guiding Bolt")
if gb is not None:
    check("Guiding Bolt upcasts from 1st",
          scaled_dice(gb, base_of(gb), slot_level=3) == "6d6",
          str(scaled_dice(gb, base_of(gb), slot_level=3)))

cw = lib.get_spell("Cure Wounds")
if cw is not None:
    check("a cure's scaling is read as HEALING, not damage",
          parse_scaling(cw).upcast_kind == "healing",
          parse_scaling(cw).upcast_kind)

# ---------------------------------------------------------------------------
# 4b. "for every TWO slot levels" — a step wider than one
# ---------------------------------------------------------------------------
# Synthetic on purpose: this is the PARSER's arithmetic, so the test needs no
# book text to state it. A spell written this way used to parse as no scaling
# at all, which is a silent 4x understatement at the top slot.
class _Every2:
    level = 3
    higher_level = "+1d8 for every two slot levels above 3rd."
    desc = "Any attack you make deals an extra 1d8 damage."


sc = parse_scaling(_Every2())
check("a two-level step is read as a step of two",
      sc.upcast_dice == "1d8" and sc.upcast_from == 3 and sc.upcast_per == 2,
      f"{sc.upcast_dice} per {sc.upcast_per} above {sc.upcast_from}")
got = [scaled_dice(_Every2(), "1d8", slot_level=n) for n in range(3, 10)]
check("it only steps on every second slot",
      got == ["1d8", "1d8", "2d8", "2d8", "3d8", "3d8", "4d8"], str(got))
check("...and the note says how wide the step is",
      "2 slot levels" in scaling_note(_Every2()), scaling_note(_Every2()))


class _Every1:
    level = 3
    higher_level = None
    desc = "The damage increases by 1d6 for each spell slot level above 3."


check("a one-level step is unchanged by the wider pattern",
      parse_scaling(_Every1()).upcast_per == 1
      and scaled_dice(_Every1(), "8d6", slot_level=5) == "10d6",
      str(scaled_dice(_Every1(), "8d6", slot_level=5)))

# ---------------------------------------------------------------------------
# 5. damaged book text is FLAGGED, never guessed
# ---------------------------------------------------------------------------
cc = lib.get_spell("Cone of Cold")
if cc is not None:
    sc = parse_scaling(cc)
    check("an unreadable upcast rule is flagged for the DM",
          sc.upcast_unreadable and sc.upcast_dice is None,
          f"dice={sc.upcast_dice} flagged={sc.upcast_unreadable}")
    check("...and it is never silently scaled",
          scaled_dice(cc, base_of(cc), slot_level=9) == base_of(cc))
    check("...and the DM's brief says so",
          "damaged" in format_spell_brief(cc))

# ---------------------------------------------------------------------------
# 6. the DM sees the rule at all (the desc is truncated at 300 chars)
# ---------------------------------------------------------------------------
if fb is not None:
    check("a cantrip's brief states its whole table",
          "L5" in format_spell_brief(fb) and "4d10" in format_spell_brief(fb))
if fbl is not None:
    check("an upcast brief states the per-slot rule",
          "per slot level above 3" in format_spell_brief(fbl))

# ---------------------------------------------------------------------------
# 7. it reaches the ENGINE's damage expression
# ---------------------------------------------------------------------------
from combat.engine import CombatEngine, PCProfile
eng = CombatEngine.__new__(CombatEngine)          # no DB needed for this method
if fb is not None:
    lo = eng._spell_damage(fb, PCProfile(character_id=1, name="A", level=1))
    hi = eng._spell_damage(fb, PCProfile(character_id=1, name="A", level=17))
    check("the engine rolls a level-17 cantrip at its real size",
          (lo, hi) == ("1d10", "4d10"), f"L1={lo} L17={hi}")
if fbl is not None:
    p = PCProfile(character_id=1, name="A", level=9)
    lo = eng._spell_damage(fbl, p, slot=3)
    hi = eng._spell_damage(fbl, p, slot=6)
    check("the engine upcasts a spell it was given a bigger slot for",
          (lo, hi) == ("8d6", "11d6"), f"slot3={lo} slot6={hi}")

# ---------------------------------------------------------------------------
# 8. nothing regressed for the spells that DO carry structured rows
# ---------------------------------------------------------------------------
with Session(lib.engine) as s:
    rows = s.exec(select(Spell)).all()
structured = [r for r in rows if isinstance(r.damage, dict) and r.damage]
bad = []
for r in structured[:20]:
    prof = PCProfile(character_id=1, name="A", level=5)
    if eng._spell_damage(r, prof, slot=r.level or 1) is None and r.damage:
        bad.append(r.name)
check("structured spells still resolve through their own rows", not bad,
      str(bad[:4]))

grew = sum(1 for r in rows
           if parse_scaling(r).cantrip_tiers or parse_scaling(r).upcast_dice)
check("a meaningful share of the book now scales", grew >= 60, f"{grew} spells")

print()
print(f"{len(fails)} failure(s)" if fails else "ALL PASS")
if fails:
    print("\n".join(f"  - {f}" for f in fails))
sys.exit(1 if fails else 0)
