"""Prove the five things a SUBCLASS tells the ENGINE actually change the game.

Each of these was a number the code declined to compute, so the feature existed
on the sheet and changed nothing when it mattered. Three of them broke SRD
subclasses, not just book ones — Champion, Arcane Trickster and Eldritch Knight
are checked here by name for exactly that reason.

  1. crit range          — attack_roll hard-coded natural 20
  2. Extra Attack        — read off the class table only
  3. Unarmored Defense   — _compute_ac named barbarian and monk literally
  4. third-caster slots  — Spellcasting is a SUBCLASS feature; slots keyed on class
  5. senses              — a granted Darkvision never reached the board

Offline: a scratch copy of the rules DB, no GPU, no LLM.
"""
from __future__ import annotations
import importlib.util, os, random, shutil, sys, tempfile
from pathlib import Path

ROOT = Path("/mnt/d/Projects/The Oracle")
sys.path.insert(0, str(ROOT))

live = ROOT / "oracle-dm-backend" / "oracle.db"
db = Path(tempfile.gettempdir()) / "oracle_subengine_check.db"
if db.exists():
    db.unlink()
if live.is_file():
    shutil.copy(live, db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"

spec = importlib.util.spec_from_file_location(
    "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

from sqlmodel import Session, SQLModel
SQLModel.metadata.create_all(m.engine)

from dice.mechanics import attack_roll
from rules import subclass_grants as sg
from rules.query import RulesLibrary

fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        fails.append(name)


STATS = {"strength": 16, "dexterity": 16, "constitution": 14,
         "intelligence": 16, "wisdom": 14, "charisma": 16}


def sheet(cls: str, sub: str | None, lvl: int, **kw) -> int:
    with Session(m.engine) as s:
        c = m.Character(discord_user_id=f"eng-{cls}-{sub}-{lvl}", name="E",
                        race="Human", char_class=cls, subclass=sub, level=lvl,
                        approved=True, max_hp=40, current_hp=40,
                        stats=dict(STATS), **kw)
        s.add(c); s.commit(); s.refresh(c)
        return c.id


# ---------------------------------------------------------------------------
# 0. the parser reads what a feature says
# ---------------------------------------------------------------------------
g = sg.grants_from_features(
    [{"level": 3, "name": "Improved Critical",
      "summary": "Your attack rolls with weapons can score a Critical Hit on a "
                 "roll of 19 or 20."}])
check("a 19-20 range parses as a threshold",
      g.crit_on == 19 and not g.crit_extra, f"crit_on={g.crit_on}")

g = sg.grants_from_features(
    [{"level": 6, "name": "Lucky Number Seven",
      "summary": "Your attack rolls can score a Critical Hit on a roll of 7 or "
                 "20 on the d20."}])
check("'7 or 20' is NOT read as a threshold",
      g.crit_on == 20 and g.crit_extra == {7},
      f"crit_on={g.crit_on} extra={sorted(g.crit_extra)}")
check("...and its crit set is exactly {7, 20}",
      g.crit_naturals() == {7, 20}, str(sorted(g.crit_naturals())))

g = sg.grants_from_features(
    [{"level": 15, "name": "Executioner's Vow",
      "summary": "It scores a Critical Hit on an 18-20."}])
check("an 18-20 range parses as a threshold", g.crit_on == 18, str(g.crit_on))

# ---------------------------------------------------------------------------
# 1. the DICE honour the range — measured, not asserted
# ---------------------------------------------------------------------------
def crit_rate(n=6000, **kw) -> float:
    return sum(1 for i in range(n)
               if attack_roll(5, 10, rng=random.Random(i), **kw).is_crit) / n


check("a widened crit range really crits more often",
      abs(crit_rate() - 0.05) < 0.012
      and abs(crit_rate(crit_on=19) - 0.10) < 0.015
      and abs(crit_rate(crit_on=18) - 0.15) < 0.02,
      f"{crit_rate():.3f} / {crit_rate(crit_on=19):.3f} / {crit_rate(crit_on=18):.3f}")
check("an extra natural crits without widening the range",
      abs(crit_rate(crit_extra={7}) - 0.10) < 0.015, f"{crit_rate(crit_extra={7}):.3f}")
check("a natural 1 is never a critical hit",
      not any(a.is_crit for a in
              (attack_roll(5, 10, crit_on=1, rng=random.Random(i))
               for i in range(400)) if a.natural == 1))

# ---------------------------------------------------------------------------
# 2. the SRD Champion — the subclass this bug made pointless
# ---------------------------------------------------------------------------
lib = RulesLibrary(engine=m.engine)
if lib.get_subclass("champion") is not None:
    cid = sheet("Fighter", "Champion", 3)
    with Session(m.engine) as s:
        prof = m._combat_pc_profile(s.get(m.Character, cid))
    check("a level-3 Champion crits on 19-20 in the combat profile",
          prof.crit_on == 19, f"crit_on={prof.crit_on}")
    cid = sheet("Fighter", "Champion", 5)
    with Session(m.engine) as s:
        base = m._combat_pc_profile(s.get(m.Character, cid))
    check("...and still gets the fighter's own Extra Attack",
          base.attacks_per_action >= 2, str(base.attacks_per_action))
else:
    print("skip  Champion not in this rules DB")

cid = sheet("Rogue", "Thief", 3)
with Session(m.engine) as s:
    plain = m._combat_pc_profile(s.get(m.Character, cid))
check("a subclass with no crit rule leaves the base rule alone",
      plain.crit_on == 20 and not plain.crit_extra, f"crit_on={plain.crit_on}")

# ---------------------------------------------------------------------------
# 3. Extra Attack granted by a SUBCLASS
# ---------------------------------------------------------------------------
g = sg.grants_from_features([{"level": 6, "name": "Extra Attack",
                              "summary": "You can attack twice instead of once."}])
check("a subclass Extra Attack is read", g.extra_attack)
for sub in ("bladesinger", "alchemical-mutation"):
    row = lib.get_subclass(sub)
    if row is None:
        continue
    lvl = min([int(f["level"]) for f in (row.features or [])
               if "extra attack" in str(f.get("name", "")).lower()] or [0])
    if not lvl:
        continue
    cid = sheet(row.class_name, row.name, lvl)
    with Session(m.engine) as s:
        p = m._combat_pc_profile(s.get(m.Character, cid))
    check(f"{row.name} attacks twice at level {lvl}",
          p.attacks_per_action >= 2, str(p.attacks_per_action))

# ---------------------------------------------------------------------------
# 4. Unarmored Defense set by a SUBCLASS
# ---------------------------------------------------------------------------
g = sg.grants_from_features(
    [{"level": 3, "name": "Cursed Form",
      "summary": "Unarmored Defense — base AC 10 + your DEX + your CHA."}])
check("a subclass Unarmored Defense is read",
      g.unarmored_ac == ("dexterity", "charisma"), str(g.unarmored_ac))
g = sg.grants_from_features(
    [{"level": 3, "name": "Genie's Splendor",
      "summary": "While wearing no armor, base AC = 10 + DEX + CHA."}])
check("...in the terser phrasing too",
      g.unarmored_ac == ("dexterity", "charisma"), str(g.unarmored_ac))
g = sg.grants_from_features(
    [{"level": 3, "name": "Earthen Resilience",
      "summary": "Gain a bonus to AC equal to your CHA modifier."}])
check("a flat AC bonus from an ability is read",
      g.ac_bonus_ability == "charisma", str(g.ac_bonus_ability))

for sub, want in (("lycanthropy", 10 + 3 + 3), ("oath-of-the-noble-genies", 10 + 3 + 3)):
    row = lib.get_subclass(sub)
    if row is None:
        continue
    cid = sheet(row.class_name, row.name, 3)
    with Session(m.engine) as s:
        ac = m._compute_ac(s.get(m.Character, cid))
    check(f"{row.name} computes its own unarmoured AC", ac == want,
          f"AC {ac}, expected {want}")

row = lib.get_subclass("stoneheart")
if row is not None:
    cid = sheet(row.class_name, row.name, 3)
    with Session(m.engine) as s:
        ac = m._compute_ac(s.get(m.Character, cid))
    check("Stoneheart adds its ability bonus to AC", ac == 10 + 3 + 3,
          f"AC {ac}, expected {10 + 3 + 3}")

cid = sheet("Fighter", "Champion", 3)
with Session(m.engine) as s:
    ac = m._compute_ac(s.get(m.Character, cid))
check("a subclass with no AC rule is left at 10 + Dex", ac == 13, f"AC {ac}")

# ---------------------------------------------------------------------------
# 5. third-caster spell slots — the SRD's own two, and the pack's
# ---------------------------------------------------------------------------
check("the third-caster table starts at level 3 and tops out at 4th-level slots",
      sg.third_caster_slots(2) == [] and sg.third_caster_slots(3) == [2]
      and sg.third_caster_slots(20) == [4, 3, 3, 1],
      f"L3={sg.third_caster_slots(3)} L20={sg.third_caster_slots(20)}")

for sub in ("arcane-trickster", "eldritch-knight", "glimmershade"):
    row = lib.get_subclass(sub)
    if row is None:
        continue
    cid = sheet(row.class_name, row.name, 7)
    with Session(m.engine) as s:
        c = s.get(m.Character, cid)
        slots = m._spell_slots_for(c.char_class, c.level, c.spell_slots_used,
                                   third_caster=m._subclass_grants(c).third_caster)
        caster = m._is_caster(c)
    check(f"{row.name} has spell slots at level 7",
          bool(slots) and caster,
          f"{[(s['level'], s['total']) for s in slots]}")

cid = sheet("Rogue", "Thief", 7)
with Session(m.engine) as s:
    c = s.get(m.Character, cid)
    slots = m._spell_slots_for(c.char_class, c.level, None,
                               third_caster=m._subclass_grants(c).third_caster)
check("a non-casting subclass still has no slots", not slots, str(slots))

cid = sheet("Wizard", "Alchemical Mutation", 7)
with Session(m.engine) as s:
    c = s.get(m.Character, cid)
    full = m._spell_slots_for(c.char_class, c.level, None,
                              third_caster=m._subclass_grants(c).third_caster)
check("a FULL caster is never demoted to the third-caster table",
      any(s["level"] == 4 for s in full), str([s["level"] for s in full]))

# ---------------------------------------------------------------------------
# 6. senses reach the sheet as a tag the board can read
# ---------------------------------------------------------------------------
g = sg.grants_from_features(
    [{"level": 9, "name": "Nightborne Predator",
      "summary": "Child of the Night: Darkvision 60 ft, or +60 ft if you already have it."}])
check("a granted Darkvision is read", g.senses.get("darkvision") == 60,
      str(g.senses))

row = lib.get_subclass("sanguine-stalker")
if row is not None:
    cid = sheet("Rogue", row.name, 9)
    with Session(m.engine) as s:
        c = s.get(m.Character, cid)
        added = m._sync_subclass_senses(c)
        s.add(c); s.commit(); s.refresh(c)
        tags = [t for t in (c.tags or []) if str(t).startswith("sense:")]
    check("it is written onto the sheet as a sense: tag",
          any("darkvision" in t for t in tags), f"{added} -> {tags}")
    # ...and re-running must not pile up duplicates.
    with Session(m.engine) as s:
        c = s.get(m.Character, cid)
        m._sync_subclass_senses(c)
        s.add(c); s.commit(); s.refresh(c)
        tags2 = [t for t in (c.tags or []) if str(t).startswith("sense:")]
    check("re-applying it does not duplicate the tag", len(tags2) == len(tags),
          str(tags2))
    # A better sense already on the sheet must survive.
    cid = sheet("Rogue", row.name, 9, tags=["sense:darkvision 120 ft"])
    with Session(m.engine) as s:
        c = s.get(m.Character, cid)
        m._sync_subclass_senses(c)
        tags3 = [t for t in (c.tags or []) if str(t).startswith("sense:")]
    check("a longer-ranged sense already held is not downgraded",
          any("120" in t for t in tags3), str(tags3))

print()
print(f"{len(fails)} failure(s)" if fails else "ALL PASS")
if fails:
    print("\n".join(f"  - {f}" for f in fails))
sys.exit(1 if fails else 0)
