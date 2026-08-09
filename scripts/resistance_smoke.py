"""Damage types and resistance — the arithmetic the engine never did.

Offline, no GPU, no LLM, fresh scratch database. Five layers:

    1. TYPES    — damage has a type, read out of prose an OCR pass mangled.
    2. DEFENCES — a bestiary row's resistances, in BOTH shapes it comes in,
                  with the condition immunities untangled from the damage ones.
    3. MATHS    — immunity zeroes, resistance halves, vulnerability doubles,
                  and each typed lump of one blow meets them separately.
    4. ENGINE   — through the real combat engine: a skeleton and a mace, a
                  fire elemental and a fireball, a raging barbarian.
    5. SPELLS   — a spell's damage and its type, derived from its own text,
                  because 17 of 430 rows carry a structured one.

    uv run python scripts/resistance_smoke.py
"""
from __future__ import annotations

import importlib.util
import os
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="oracle-resist-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'scratch.db'}"

from sqlmodel import Session, SQLModel            # noqa: E402

from rules import damage as dmg                   # noqa: E402
from rules.models import Item, Monster, Spell     # noqa: E402

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
_failures = 0


def check(label: str, ok: bool, detail: str = "") -> bool:
    global _failures
    if not ok:
        _failures += 1
    print(f"  {GREEN}✓{OFF}" if ok else f"  {RED}✗{OFF}",
          label, f"{DIM}— {detail}{OFF}" if detail else "")
    return ok


def section(title: str) -> None:
    print(f"\n{BOLD}{title}{OFF}")


# ----------------------------------------------------------------------
section("1. damage has a type, even when the page was scanned badly")

check("a clean line reads", [(p.dice, p.type) for p in
                            dmg.parse_damage("taking 8d6 Fire damage")]
      == [("8d6", "fire")])
check("...and so does one the OCR chewed",
      [(p.dice, p.type) for p in
       dmg.parse_damage("the target takes ldlO Necr otic damage")]
      == [("1d10", "necrotic")],
      "'ldlO' is 1d10 and 'Necr otic' is necrotic — the book text is damaged "
      "in consistent ways")
check("several lumps come back in order",
      [(p.dice, p.type) for p in dmg.parse_damage(
          "1d8 slashing damage plus 2d6 fire damage")]
      == [("1d8", "slashing"), ("2d6", "fire")])
check("dice with no type are NOT returned",
      dmg.parse_damage("takes 4d6 damage") == [],
      "untyped damage is resisted by nobody, so a missed type is safer as no "
      "packet than as a packet that ignores every resistance in the game")
check("a save that halves is recognised",
      dmg.save_halves("taking 8d6 Fire damage on a failed save or half as "
                      "much damage on a successful one"))
check("...and one that doesn't, isn't",
      not dmg.save_halves("On a hit, the target takes 1d10 Fire damage."))

# ----------------------------------------------------------------------
section("2. what a creature resists, in both shapes the bestiary uses")

srd = dmg.parse_defenses(
    resistances=["acid", "cold",
                 "bludgeoning, piercing, and slashing from nonmagical weapons"],
    immunities=["necrotic", "poison"])
check("the tidy SRD list reads", set(srd.resist) ==
      {"acid", "cold", "bludgeoning", "piercing", "slashing"}
      and set(srd.immune) == {"necrotic", "poison"}, srd.describe())
check("...and the qualifier rides with it",
      srd.resist["slashing"].nonmagical_only and not srd.resist["acid"].nonmagical_only)

book = dmg.parse_defenses(immunities=["Fire,Poison;Exhaustion,Grappled,"])
check("the PDF's semicolon shape splits damage from CONDITIONS",
      set(book.immune) == {"fire", "poison"}
      and book.condition_immunities == {"exhaustion", "grappled"},
      "read naively a skeleton comes out immune to 'exhaustion damage'")
bare = dmg.parse_defenses(immunities=["Exhaustion,Petrifed"])
check("...and a bare condition list with no separator is still rescued",
      not bare.immune and bare.condition_immunities == {"exhaustion", "petrified"},
      "'Petrifed' is a typo in the source — a lookup that demands the correct "
      "spelling drops a real immunity")

# ----------------------------------------------------------------------
section("3. the arithmetic")

D = dmg.parse_defenses(resistances=["fire"], immunities=["poison"],
                       vulnerabilities=["cold"])


def one(kind, n, **kw):
    return dmg.reduce_one(D, n, dmg.Packet(dice="", type=kind, **kw))[0]


check("resistance halves, rounding down", one("fire", 7) == 3, "7 → 3")
check("immunity zeroes", one("poison", 40) == 0)
check("vulnerability doubles", one("cold", 6) == 12)
check("an unlisted type is untouched", one("acid", 9) == 9)
check("an UNTYPED lump is never reduced",
      dmg.reduce_one(D, 9, dmg.Packet(dice="", type=None))[0] == 9)

both = dmg.parse_defenses(resistances=["fire"], vulnerabilities=["fire"])
check("resistant AND vulnerable cancel",
      dmg.reduce_one(both, 10, dmg.Packet(dice="", type="fire"))[0] == 10)

nm = dmg.parse_defenses(resistances=["slashing from nonmagical attacks"])
check("a nonmagical qualifier halves a plain blade",
      dmg.reduce_one(nm, 10, dmg.Packet(dice="", type="slashing"))[0] == 5)
check("...and a magic one goes through whole",
      dmg.reduce_one(nm, 10,
                     dmg.Packet(dice="", type="slashing", magical=True))[0] == 10)
silver = dmg.parse_defenses(
    resistances=["bludgeoning from nonmagical attacks that aren't silvered"])
check("...and silver beats the silvered exception",
      dmg.reduce_one(silver, 10, dmg.Packet(
          dice="", type="bludgeoning", materials={"silvered"}))[0] == 10)

flame = dmg.apply(dmg.parse_defenses(immunities=["fire"]),
                  [(dmg.Packet(dice="1d8", type="slashing", label="Flame Tongue"), 6),
                   (dmg.Packet(dice="2d6", type="fire", label="Flame Tongue"), 7)])
check("one blow's lumps meet the defences SEPARATELY",
      flame.total == 6,
      "a flame tongue is slashing AND fire; a fire elemental takes the steel "
      "and none of the flame")

# ----------------------------------------------------------------------
section("4. through the real combat engine")
_spec = importlib.util.spec_from_file_location(
    "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)                                       # type: ignore
SQLModel.metadata.create_all(m.engine)

from combat import CombatTracker, CombatEngine                    # noqa: E402

with Session(m.engine) as s:
    s.add(Item(index_slug="mace", name="Mace", category="weapon",
               item_type="Simple", damage_dice="1d6",
               damage_type="Bludgeoning", range_normal=5))
    s.add(Item(index_slug="rapier", name="Rapier", category="weapon",
               item_type="Martial", damage_dice="1d8", damage_type="Piercing",
               range_normal=5, properties=["Finesse"]))
    # A skeleton: vulnerable to bludgeoning, immune to poison — and the
    # condition immunities tangled into the same column, as the PDF wrote it.
    s.add(Monster(index_slug="skeleton", name="Skeleton", size="Medium",
                  type="undead", armor_class=1, hit_points=200,
                  challenge_rating=0.25,
                  damage_vulnerabilities=["Bludgeoning"],
                  damage_immunities=["Poison;Exhaustion,Poisoned"]))
    s.add(Monster(index_slug="fire-elemental", name="Fire Elemental",
                  size="Large", type="elemental", armor_class=1,
                  hit_points=400, challenge_rating=5,
                  damage_immunities=["Fire,Poison;Exhaustion,Grappled,"],
                  damage_resistances=["Bludgeoning,Piercing,Slashing"]))
    s.add(Spell(index_slug="fireball", name="Fireball", level=3,
                school="Evocation", casting_time="Action", range="150 feet",
                duration="Instantaneous", components=["V", "S", "M"],
                classes=["wizard"], dc_type="dex",
                desc="Each creature in a 20-foot-radius Sphere makes a "
                     "Dexterity saving throw, taking 8d6 Fire damage on a "
                     "failed save or half as much damage on a successful one."))
    s.commit()

tracker = CombatTracker(database_url=os.environ["DATABASE_URL"],
                        defenses_for=m._combat_defenses_for)
tracker.create_tables()
engine_ = CombatEngine(tracker, rng=random.Random(12))


def pc(name, inv, cls="Fighter", level=5, tags=()):
    with Session(m.engine) as s:
        ch = m.Character(discord_user_id="res", name=name, char_class=cls,
                         level=level, max_hp=50, current_hp=50,
                         stats={"strength": 16, "dexterity": 14,
                                "constitution": 14, "intelligence": 10,
                                "wisdom": 10, "charisma": 10},
                         inventory=inv, tags=list(tags))
        s.add(ch); s.commit(); s.refresh(ch)
        return ch


def swing(char, foe_slug, weapon=None, seed=3):
    e = tracker.start_encounter(f"res:{char.name}{foe_slug}", "test")
    tracker.add_pc(e.id, name=char.name, max_hp=50, armor_class=15,
                   dex_mod=2, character_id=char.id)
    foe = tracker.add_from_monster(e.id, foe_slug, count=1)[0]
    tracker.set_position(foe.id, f"melee with {char.name}")
    tracker.roll_initiative(e.id, rng=random.Random(seed))
    while (c := tracker.current_combatant(e.id)) and c.name != char.name:
        tracker.next_turn(e.id)
    intent = {"verb": "attack", "target": foe.name}
    if weapon:
        intent["arg"] = weapon
    rep = engine_.resolve(e.id, [intent], {char.id: m._combat_pc_profile(char)})
    return rep, e, foe


basher = pc("Basher", [{"name": "Mace", "quantity": 1, "equipped": True,
                        "grip": "main"}])
rep, e, foe = swing(basher, "skeleton")
ev = rep.events[0]
rolled = next((r["total"] for r in ev["rolls"] if "damage" in r["label"]), None)
check("a mace against a skeleton is DOUBLED",
      ev.get("damage") == (rolled or 0) * 2,
      f"rolled {rolled}, dealt {ev.get('damage')} — " + "; ".join(ev["notes"]))

poker = pc("Poker", [{"name": "Rapier", "quantity": 1, "equipped": True,
                      "grip": "main"}])
rep, e, foe = swing(poker, "skeleton", seed=5)
ev = rep.events[0]
rolled = next((r["total"] for r in ev["rolls"] if "damage" in r["label"]), None)
check("...and a rapier is not",
      ev.get("damage") == rolled, f"rolled {rolled}, dealt {ev.get('damage')}")

rep, e, foe = swing(poker, "fire-elemental", seed=7)
ev = rep.events[0]
rolled = next((r["total"] for r in ev["rolls"] if "damage" in r["label"]), None)
check("a fire elemental HALVES a piercing blow",
      ev.get("damage") == (rolled or 0) // 2,
      f"rolled {rolled}, dealt {ev.get('damage')} — " + "; ".join(ev["notes"]))
check("...and the table is told why, not left to read a bad roll",
      any("resist" in n for n in ev["notes"]), "; ".join(ev["notes"]))

# A PC's own defences: Rage is not a trait, it is what they are doing now.
barb = pc("Grull", [{"name": "Mace", "quantity": 1, "equipped": True,
                     "grip": "main"}], cls="Barbarian", level=5)
d0 = m._pc_defenses(barb)
check("a barbarian standing still resists nothing", d0.empty, d0.describe())
barb.conditions = ["raging"]
d1 = m._pc_defenses(barb)
check("...and raging resists all three physical types",
      set(d1.resist) == set(dmg.PHYSICAL), d1.describe())

tag_pc = pc("Zeal", [], tags=["resist:fire", "immune:poison"])
dt = m._pc_defenses(tag_pc)
check("a resist: tag is the explicit record",
      "fire" in dt.resist and "poison" in dt.immune, dt.describe())

# ----------------------------------------------------------------------
section("5. a spell's damage, out of its own sentence")

with Session(m.engine) as s:
    fb = s.exec(__import__("sqlmodel").select(Spell).where(
        Spell.index_slug == "fireball")).first()
check("the dice are found where the only copy of them is",
      engine_._spell_damage(fb, None) == "8d6",
      "17 of 430 spell rows carry a structured damage dict; the rest carry a "
      "sentence")
check("...and so is the type", engine_._spell_type(fb) == "fire")

burn = pc("Pyra", [], cls="Wizard", level=5)
e = tracker.start_encounter("res:fireball", "boom")
tracker.add_pc(e.id, name="Pyra", max_hp=30, armor_class=12, dex_mod=1,
               character_id=burn.id)
elem = tracker.add_from_monster(e.id, "fire-elemental", count=1)[0]
tracker.set_position(elem.id, "near")
tracker.roll_initiative(e.id, rng=random.Random(2))
while (c := tracker.current_combatant(e.id)) and c.name != "Pyra":
    tracker.next_turn(e.id)
before = tracker.get_combatant(elem.id).current_hp
prof = m._combat_pc_profile(burn)
prof.slots = {3: 2}
rep = engine_.resolve(e.id, [{"verb": "cast", "arg": "Fireball",
                              "target": elem.name}], {burn.id: prof})
after = tracker.get_combatant(elem.id).current_hp
check("a fireball on a fire elemental does NOTHING", before == after,
      f"{before} → {after} HP; " + "; ".join(
          (rep.events[0].get("notes") if rep.events else rep.rejections) or []))

print()
if _failures:
    print(f"{RED}{_failures} check(s) failed{OFF}")
    sys.exit(1)
print(f"{GREEN}damage has a type, and a creature can resist it{OFF}")
