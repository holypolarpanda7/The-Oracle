"""Equipped / grip smoke test — a body has two hands, and now the game knows it.

Offline, no GPU, no LLM, fresh scratch database. Four layers:

    1. READ    — an inventory adds up to a loadout: what is worn, what is held,
                 in which hand, with older ``equipped`` rows inferred.
    2. CHANGE  — equipping something the hands can't hold DISPLACES what was
                 there rather than failing, and an impossible loadout is put
                 right instead of reaching the rules.
    3. CAST    — the free-hand rule for Somatic and Material components, which
                 spent this project's whole life so far as a note on the DM's
                 board asking them to remember it.
    4. REACH   — the same answer arrives at the DM's board, the sheet, the
                 armour class and the [[GRIP]] hook.

    uv run python scripts/grip_smoke.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="oracle-grip-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'scratch.db'}"

from sqlmodel import Session, SQLModel      # noqa: E402

from rules import components as rc          # noqa: E402
from rules import equipment as gear         # noqa: E402
from rules.models import Item, Spell, Feat  # noqa: E402

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


# The catalogue this test reasons over. Small on purpose: the loadout model
# reads item ROWS, so the rows have to be real, but nothing here needs the SRD.
CATALOG = [
    dict(index_slug="greatsword", name="Greatsword", category="weapon",
         item_type="Martial", damage_dice="2d6", properties=["Heavy", "Two-Handed"]),
    dict(index_slug="longsword", name="Longsword", category="weapon",
         item_type="Martial", damage_dice="1d8", properties=["Versatile"],
         two_handed_damage_dice="1d10"),
    dict(index_slug="mace", name="Mace", category="weapon", item_type="Simple",
         damage_dice="1d6"),
    dict(index_slug="dagger", name="Dagger", category="weapon",
         item_type="Simple", damage_dice="1d4", properties=["Finesse", "Light"]),
    dict(index_slug="shield", name="Shield", category="armor",
         item_type="Shield", armor_class_base=2),
    dict(index_slug="chain-mail", name="Chain Mail", category="armor",
         item_type="Heavy", armor_class_base=16, armor_dex_bonus=False),
    dict(index_slug="leather-armor", name="Leather Armor", category="armor",
         item_type="Light", armor_class_base=11, armor_dex_bonus=True),
    dict(index_slug="quarterstaff", name="Quarterstaff", category="weapon",
         item_type="Simple", damage_dice="1d6", properties=["Versatile"],
         two_handed_damage_dice="1d8"),
    dict(index_slug="holy-symbol", name="Holy Symbol",
         category="adventuring-gear", item_type="Holy Symbol"),
    dict(index_slug="component-pouch", name="Component Pouch",
         category="adventuring-gear", item_type="Standard Gear"),
    dict(index_slug="torch", name="Torch", category="adventuring-gear",
         item_type="Standard Gear"),
]


def _row(name: str):
    for c in CATALOG:
        if c["name"].lower() == str(name or "").lower():
            return Item(**c)
    return None


def held(items):
    return gear.read_loadout(items, _row)


# ----------------------------------------------------------------------
section("1. an inventory adds up to a body")

check("a pack full of swords is a pack full of swords",
      held([{"name": "Greatsword", "quantity": 1}]).free_hands == 2,
      "nothing equipped means nothing held — which is why turning this on "
      "can't break an imported sheet")

sword_board = [{"name": "Longsword", "quantity": 1, "equipped": True},
               {"name": "Shield", "quantity": 1, "equipped": True},
               {"name": "Chain Mail", "quantity": 1, "equipped": True}]
L = held(sword_board)
check("an older row with no grip is placed anyway",
      L.at("main").name == "Longsword" and L.at("off").name == "Shield",
      L.describe_hands())
check("...deterministically — a shield goes to the off hand",
      all(h.inferred for h in L.held))
check("armour is worn, not held", L.armor == "Chain Mail" and L.free_hands == 0)

two = held([{"name": "Greatsword", "quantity": 1, "equipped": True}])
check("a two-handed weapon takes both hands",
      two.at("main") is two.at("off") and two.free_hands == 0,
      two.describe_hands())

check("an unrecognised item never eats a hand",
      held([{"name": "Mysterious Cube", "quantity": 1,
             "equipped": True}]).free_hands == 2,
      "the permissive direction: a wrong guess here would refuse a spell")

check("a torch is held, a holy symbol is worn",
      gear.hands_needed(_row("Torch"), "Torch") == 1
      and gear.hands_needed(_row("Holy Symbol"), "Holy Symbol") == 0)

# ----------------------------------------------------------------------
section("2. changing what is in the hands")

pack = [{"name": "Longsword", "quantity": 1, "equipped": True, "grip": "main"},
        {"name": "Shield", "quantity": 1, "equipped": True, "grip": "off"},
        {"name": "Greatsword", "quantity": 1}]
plan = gear.plan_equip(pack, "Greatsword", _row)
check("a two-hander displaces both hands rather than failing",
      plan.ok and sorted(plan.displaced) == ["Longsword", "Shield"], plan.note)
plan.apply(pack)
L = held(pack)
check("...and the sheet says so afterwards",
      L.at("main").name == "Greatsword" and L.free_hands == 0, L.describe_hands())

plan = gear.plan_equip(pack, "Longsword", _row, grip="both")
plan.apply(pack)
check("a versatile weapon may be taken in both hands",
      held(pack).at("main").grip == "both", held(pack).describe_hands())
plan = gear.plan_equip(pack, "Longsword", _row, grip="main")
plan.apply(pack)
check("...and dropped back to one, freeing the other",
      held(pack).free_hands == 1, held(pack).describe_hands())

rapier = [{"name": "Dagger", "quantity": 1}]
gear.plan_equip(rapier, "Dagger", _row, grip="both").apply(rapier)
check("a weapon that gains nothing from two hands is never gripped that way",
      held(rapier).at("main").grip == "main",
      "recording it would eat a hand and buy nothing")

impossible = [{"name": "Greatsword", "quantity": 1, "equipped": True},
              {"name": "Shield", "quantity": 1, "equipped": True},
              {"name": "Chain Mail", "quantity": 1, "equipped": True},
              {"name": "Leather Armor", "quantity": 1, "equipped": True}]
stowed = gear.normalize(impossible, _row)
check("a bulk outfitter can't leave a body wearing more than it has room for",
      sorted(stowed) == ["Leather Armor", "Shield"], f"stowed {stowed}")
check("...and what remains is written down, not inferred again",
      impossible[0].get("grip") == "both", impossible[0])

# ----------------------------------------------------------------------
section("3. the rule this whole model exists for")

full = held([{"name": "Longsword", "quantity": 1, "equipped": True},
             {"name": "Shield", "quantity": 1, "equipped": True}])
one_free = held([{"name": "Longsword", "quantity": 1, "equipped": True}])

VS = rc.SpellComponents(verbal=True, somatic=True)
VSM = rc.SpellComponents(verbal=True, somatic=True, material=True,
                         text="a pinch of soot")
V = rc.SpellComponents(verbal=True)

check("one free hand is all any spell needs",
      gear.casting_hands(one_free, VSM).ok)
check("a spell with neither S nor M never asks about hands",
      gear.casting_hands(full, V).ok)
r = gear.casting_hands(full, VS)
check("both hands full stops a Somatic component", not r.ok, r.reason)
check("...and the refusal names the one thing to put away",
      r.stow == "Shield", r.remedy)
check("War Caster waives it", gear.casting_hands(full, VS, somatic_waived=True).ok)

staff = held([{"name": "Quarterstaff", "quantity": 1, "equipped": True,
               "grip": "main"},
              {"name": "Shield", "quantity": 1, "equipped": True, "grip": "off"}])
check("the hand holding a focus does the gesturing too",
      gear.casting_hands(staff, VSM).ok,
      "the same hand may serve the Material and the Somatic of one spell")
check("...but only for a spell that HAS a material component",
      not gear.casting_hands(staff, VS).ok)

symbol = held([{"name": "Mace", "quantity": 1, "equipped": True, "grip": "main"},
               {"name": "Shield", "quantity": 1, "equipped": True, "grip": "off"},
               {"name": "Holy Symbol", "quantity": 1, "equipped": True}])
M = rc.SpellComponents(verbal=True, material=True, text="a holy symbol")
check("a WORN holy symbol pays a material component with no hand",
      gear.casting_hands(symbol, M).ok,
      "held in hand, worn visibly, or borne on a shield")
check("...but nothing is gesturing, so it does not do the Somatic",
      not gear.casting_hands(symbol, VS).ok)

costly = rc.SpellComponents(verbal=True, somatic=True, material=True,
                            text="a diamond worth 300+ GP, which the spell consumes")
check("a costly component in hand casts the spell a pack cannot",
      gear.casting_hands(
          held([{"name": "Diamond", "quantity": 1, "equipped": True,
                 "grip": "main", "hands": 1}]), costly,
          component_name="Diamond").ok)

# ----------------------------------------------------------------------
section("4. through the backend — the board, the sheet, the AC and the hook")
_spec = importlib.util.spec_from_file_location(
    "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)                                     # type: ignore
SQLModel.metadata.create_all(m.engine)

with Session(m.engine) as s:
    for c in CATALOG:
        s.add(Item(**c))
    s.add(Spell(index_slug="cure-wounds", name="Cure Wounds", level=1,
                school="Abjuration", casting_time="Action", range="Touch",
                duration="Instantaneous", components=["V", "S"],
                classes=["cleric"]))
    s.add(Spell(index_slug="healing-word", name="Healing Word", level=1,
                school="Abjuration", casting_time="Bonus Action",
                range="60 feet", duration="Instantaneous",
                components=["V"], classes=["cleric"]))
    s.add(Feat(index_slug="war-caster", name="War Caster", category="general",
               benefit="You can perform the somatic components of spells even "
                       "when you have weapons or a shield in one or both hands."))
    knight = m.Character(
        discord_user_id="grip", name="Sera", char_class="Cleric", level=5,
        stats={"strength": 14, "dexterity": 10, "constitution": 14,
               "intelligence": 10, "wisdom": 18, "charisma": 12},
        spells=["Cure Wounds", "Healing Word"],
        prepared_spells=["Cure Wounds", "Healing Word"],
        inventory=[{"name": "Mace", "quantity": 1, "equipped": True, "grip": "main"},
                   {"name": "Shield", "quantity": 1, "equipped": True, "grip": "off"},
                   {"name": "Chain Mail", "quantity": 1, "equipped": True},
                   {"name": "Holy Symbol", "quantity": 1, "equipped": True},
                   {"name": "Longsword", "quantity": 1}])
    s.add(knight)
    s.commit()
    s.refresh(knight)


def cast(spell, level=1):
    return m.resolve_cast_hooks(f"[[CAST: {spell} | {level}]]", knight,
                                None, None).strip()


out = cast("Cure Wounds")
check("[[CAST]] refuses a Somatic spell with both hands full",
      "won't come" in out and "somatic" in out, out)
check("...and tells the table what to put down", "stowing Shield" in out, out)
check("a spell with no Somatic component is untouched",
      "won't come" not in cast("Healing Word"), cast("Healing Word"))

check("the shield is worth its +2 while it is in a hand",
      m._compute_ac(knight) == 18, f"AC {m._compute_ac(knight)}")

out = m.resolve_grip_hooks("[[GRIP: stow | Shield]]", knight)
check("[[GRIP: stow]] frees the hand", "1 hand free" in out, out.strip())
check("...and the shield stops adding to AC once it's slung",
      m._compute_ac(knight) == 16, f"AC {m._compute_ac(knight)}")
knight.spell_slots_used = {}
check("...and now the spell goes off", "won't come" not in cast("Cure Wounds"),
      cast("Cure Wounds"))

out = m.resolve_grip_hooks("[[GRIP: draw | Longsword | both]]", knight)
check("[[GRIP: draw]] takes a versatile blade in both hands",
      "both hands" in out and "0 hands free" in out, out.strip())
check("...displacing what was in the way", "Mace" in out, out.strip())

knight.tags = ["feat:war-caster"]
knight.spell_slots_used = {}
check("War Caster reaches the cast hook, read off the feat's own text",
      "won't come" not in cast("Cure Wounds"), cast("Cure Wounds"))
knight.tags = []

sheet = m._build_character_sheet(knight)
check("the sheet carries the loadout",
      sheet["loadout"]["free_hands"] == 0
      and sheet["loadout"]["hands"][0]["name"] == "Longsword",
      sheet["loadout"]["text"])
check("...and the armour it is wearing", sheet["loadout"]["armor"] == "Chain Mail")

summary = m._equipment_summary(knight)
check("the DM's board separates worn-and-wielded from carried",
      "Worn & wielded:" in summary and "Inventory:" in summary,
      summary.splitlines()[-1][:100])
check("...and teaches the hook that changes it", "[[GRIP: draw" in summary)

block = m._character_resource_block(knight.id)
check("the spellcasting board states the hands are ENFORCED",
      "Hands (ENFORCED)" in block,
      next((l[:110] for l in block.splitlines() if "Hands (" in l), "(absent)"))
check("...and no longer calls it the DM's own call to make",
      "that call is yours" not in block,
      "the last mechanic asked for by prose is now asked for by code")

# ----------------------------------------------------------------------
section("5. the combat engine swings what is in the hand")
import random                                                    # noqa: E402
from combat import CombatTracker, CombatEngine                    # noqa: E402
from rules.models import Feat as _Feat                            # noqa: E402


def fighter(inventory, *, feats=(), cls="Ranger", level=5, name="Rill"):
    with Session(m.engine) as s:
        ch = m.Character(
            discord_user_id="grip2", name=name, char_class=cls, level=level,
            stats={"strength": 12, "dexterity": 18, "constitution": 14,
                   "intelligence": 10, "wisdom": 12, "charisma": 10},
            max_hp=40, current_hp=40, inventory=inventory,
            tags=[f"feat:{f}" for f in feats])
        s.add(ch)
        s.commit()
        s.refresh(ch)
        return ch


TWO_BLADES = [
    {"name": "Dagger", "quantity": 1, "equipped": True, "grip": "main"},
    {"name": "Dagger", "quantity": 1, "equipped": True, "grip": "off"},
    {"name": "Greatsword", "quantity": 1},
]
rill = fighter(TWO_BLADES)
prof = m._combat_pc_profile(rill)
names = [(w.name, w.grip, w.stowed, w.damage, w.offhand_damage) for w in prof.weapons]
check("the held weapons come first, the packed one last",
      names[0][1] == "main" and names[-1][0] == "Greatsword" and names[-1][2],
      str(names))
check("two Light weapons in two hands grant the bonus attack",
      "bonus attack" in prof.features,
      "a fact about the hands, not about a class")
check("...and the off-hand swing drops the ability modifier",
      names[1][4] == "1d4", f"main {names[0][3]}, off-hand {names[1][4]}")

with Session(m.engine) as s:
    s.add(_Feat(index_slug="two-weapon-fighting", name="Two-Weapon Fighting",
                category="fighting-style",
                benefit="When you make an extra attack from the Light "
                        "property, you may add your ability modifier to that "
                        "attack's damage."))
    s.add(_Feat(index_slug="dual-wielder", name="Dual Wielder",
                category="general", benefit="Enhanced Dual Wielding."))
    s.commit()

styled = m._combat_pc_profile(
    fighter(TWO_BLADES, feats=["two-weapon-fighting"], name="Styled"))
check("the Two-Weapon Fighting style gives the modifier back",
      next(w.offhand_damage for w in styled.weapons if w.grip == "off") == "1d4+4")

one_light = [{"name": "Dagger", "quantity": 1, "equipped": True, "grip": "main"},
             {"name": "Mace", "quantity": 1, "equipped": True, "grip": "off"}]
check("a non-Light off hand is NOT two-weapon fighting by itself",
      "bonus attack" not in m._combat_pc_profile(
          fighter(one_light, name="Plain")).features)
check("...but Dual Wielder allows it",
      "bonus attack" in m._combat_pc_profile(
          fighter(one_light, feats=["dual-wielder"], name="Dualist")).features)
check("a two-handed weapon is never two-weapon fighting",
      "bonus attack" not in m._combat_pc_profile(fighter(
          [{"name": "Greatsword", "quantity": 1, "equipped": True},
           {"name": "Dagger", "quantity": 1}], name="Hewer")).features)

versa = m._combat_pc_profile(fighter(
    [{"name": "Longsword", "quantity": 1, "equipped": True, "grip": "both"}],
    name="Vera"))
check("a versatile weapon in both hands rolls its bigger die",
      versa.weapons[0].damage == "1d10+1",
      "two_handed_damage_dice has been in the database since the first ingest")
one_hand = m._combat_pc_profile(fighter(
    [{"name": "Longsword", "quantity": 1, "equipped": True, "grip": "main"},
     {"name": "Shield", "quantity": 1, "equipped": True, "grip": "off"}],
    name="Vane"))
check("...and its smaller one in a single hand",
      one_hand.weapons[0].damage == "1d8+1")

check("a character holding NOTHING has every weapon available",
      not any(w.stowed for w in m._combat_pc_profile(fighter(
          [{"name": "Dagger", "quantity": 1},
           {"name": "Greatsword", "quantity": 1}], name="Ghost")).weapons),
      "unknown, not empty-handed — the same rule the pack check uses")

# A stack has one grip: dual-wielding two identical blades has to split it.
stack = [{"name": "Dagger", "quantity": 2, "equipped": True, "grip": "main"}]
gear.plan_equip(stack, "Dagger", _row, grip="off").apply(stack)
L = held(stack)
check("a stack splits so both hands can hold one",
      L.at("main") is not None and L.at("off") is not None
      and L.at("main") is not L.at("off"), L.describe_hands())
check("...and the pile is one lighter", stack[0]["quantity"] == 1, str(stack))
check("...which is what makes two identical blades a real build",
      "bonus attack" in m._combat_pc_profile(fighter(stack, name="Twin")).features)

# A book weapon the catalogue never heard of still has to be holdable.
exotic = [{"name": "Double-Bladed Scimitar", "quantity": 1}]
p = gear.plan_equip(exotic, "Double-Bladed Scimitar", _row, grip="main")
p.apply(exotic)
check("an unknown item is held when a hand is named",
      held(exotic).at("main") is not None, held(exotic).describe_hands())
check("...and worn when one isn't, so it can never eat a hand by accident",
      gear.plan_equip([{"name": "Odd Relic", "quantity": 1}],
                      "Odd Relic", _row).note.endswith("is worn."))

# ---- through a real encounter -----------------------------------------
tracker = CombatTracker(database_url=os.environ["DATABASE_URL"])
tracker.create_tables()
engine_ = CombatEngine(tracker, rng=random.Random(11))
enc = tracker.start_encounter("grip:fight", "Two blades")
# Level 3: no Extra Attack, so the second swing of the turn can only be the
# bonus-action one the off hand buys.
duellist = fighter(TWO_BLADES, name="Duellist", level=3)
prof = m._combat_pc_profile(duellist)
tracker.add_pc(enc.id, name="Duellist", max_hp=40, armor_class=15, dex_mod=4,
               character_id=duellist.id)
straw = tracker.add_combatant(enc.id, "Straw Man", max_hp=200, armor_class=1,
                              dex_mod=-5)
tracker.set_position(straw.id, "melee with Duellist")
tracker.roll_initiative(enc.id, rng=random.Random(1))
while (cur := tracker.current_combatant(enc.id)) and cur.name != "Duellist":
    tracker.next_turn(enc.id)

profs = {duellist.id: prof}
rep = engine_.resolve(enc.id, [{"verb": "attack", "target": "Straw Man"}], profs)
check("the Attack action swings the MAIN hand",
      rep.events and rep.events[0].get("weapon") == "Dagger", str(rep.rejections))
rep = engine_.resolve(enc.id, [{"verb": "attack", "target": "Straw Man"}], profs)
note = " ".join(rep.events[0].get("notes") or []) if rep.events else ""
check("the second swing is the off-hand bonus attack", "off-hand" in note, note)
check("...and a third is refused — the turn is spent",
      bool(engine_.resolve(enc.id, [{"verb": "attack", "target": "Straw Man"}],
                           profs).rejections))

# Swinging something in the pack is refused, and the refusal names the draw.
tracker.next_turn(enc.id)
while (cur := tracker.current_combatant(enc.id)) and cur.name != "Duellist":
    tracker.next_turn(enc.id)
rep = engine_.resolve(enc.id, [{"verb": "attack", "target": "Straw Man",
                                "arg": "Greatsword"}], profs)
reason = rep.rejections[0]["reason"] if rep.rejections else ""
check("a weapon in the pack cannot be swung", "not in" in reason, reason)
check("...and the refusal names the draw", "[[GRIP: draw" in reason)

summary = m._equipment_summary(duellist)
check("the DM's board says two-weapon fighting is live",
      "Two-weapon fighting is LIVE" in summary,
      summary.splitlines()[-1][:120])
check("...and that the off-hand damage carries no modifier",
      "no ability modifier" in summary)

print()
if _failures:
    print(f"{RED}{_failures} check(s) failed{OFF}")
    sys.exit(1)
print(f"{GREEN}a body has two hands, and the game knows what is in them{OFF}")
