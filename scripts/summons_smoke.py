"""Summoning smoke test — a conjured spirit is a real creature.

Offline, no GPU, no LLM, fresh scratch database. Pins the whole path a summon
takes: the scaling arithmetic, the variant gates, the stat block that lands in
``rules_monster``, the shape the combat engine actually reads, the side a
summon fights on, and where it falls in the initiative order.

    uv run python scripts/summons_smoke.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP = Path(tempfile.mkdtemp(prefix="oracle-summons-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'scratch.db'}"

from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

from combat.models import Combatant, CombatantKind                # noqa: E402
from combat.tracker import CombatTracker                          # noqa: E402
from rules import summons                                         # noqa: E402
from rules.models import Monster                                  # noqa: E402

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
_failures = 0


def check(label: str, ok: bool, detail: str = "") -> bool:
    global _failures
    if not ok:
        _failures += 1
    mark = f"{GREEN}✓{OFF}" if ok else f"{RED}✗{OFF}"
    tail = f" {DIM}— {detail}{OFF}" if detail else ""
    print(f"  {mark} {label}{tail}")
    return ok


def section(title: str) -> None:
    print(f"\n{BOLD}{title}{OFF}")


engine = create_engine(os.environ["DATABASE_URL"])
SQLModel.metadata.create_all(engine)
tracker = CombatTracker(engine=engine)

# A caster: proficiency +3, spell attack +7, save DC 15.
ATK, DC, PB = 7, 15, 3


def conjure(ref, level, variant=""):
    return summons.materialize(ref, level=level, variant=variant, engine=engine,
                               attack_bonus=ATK, save_dc=DC, proficiency_bonus=PB)


# ----------------------------------------------------------------------
section("1. the one scaling shape")
# "AC 11 + the level of the spell" anchors at 0; "40 + 10 for each level above
# 4th" anchors at 4. Both are the same expression with a different `from`.
check("11 + the spell's level", summons.scaled({"base": 11, "per_level": 1}, 4) == 15,
      "level 4 → AC 15")
check("40 + 10 per level above 4th",
      [summons.scaled({"base": 40, "per_level": 10, "from": 4}, lv) for lv in (4, 5, 7)]
      == [40, 50, 70])
check("a slot below the spell's own level never subtracts",
      summons.scaled({"base": 40, "per_level": 10, "from": 4}, 2) == 40)
check("half the spell's level, rounded down",
      [summons.scaled({"per_level": 0.5}, lv, default=1) for lv in (3, 4, 6, 7)]
      == [1, 2, 3, 3])
check("a bare number is a constant", summons.scaled(16, 9) == 16)

# ----------------------------------------------------------------------
section("2. a spell names a creature nobody had")
before = None
with Session(engine) as s:
    before = s.exec(select(Monster).where(Monster.name == "Fey Spirit")).first()
check("the bestiary has no Fey Spirit to add", before is None,
      "which is why [[COMBAT: add | Fey Spirit]] seated a 10-HP blob")

fey = conjure("Summon Fey", 3, "Mirthful")
check("a spell name finds its spirit", fey is not None and fey.name == "Fey Spirit (Mirthful)")
check("AC is 12 + the slot", fey.armor_class == 15, f"AC {fey.armor_class}")
check("HP is the printed 30 at its own level", fey.hit_points == 30)
check("it is a real rules_monster row",
      conjure("summon-fey", 3, "Mirthful").id == fey.id, "re-summoning is a lookup")

fey5 = conjure("Summon Fey", 5, "Mirthful")
check("upcasting builds a different creature", fey5.id != fey.id)
check("...stronger by the slot", (fey5.armor_class, fey5.hit_points) == (17, 50),
      f"AC {fey5.armor_class}, {fey5.hit_points} HP")

# ----------------------------------------------------------------------
section("3. the shape combat/engine.py actually reads")
atk = [a for a in fey5.actions if a.get("attack_bonus") is not None]
check("an attack carries the CASTER's attack bonus",
      atk and atk[0]["attack_bonus"] == ATK)
check("damage is dice + 3 + the spell's level",
      atk and atk[0]["damage"][0]["damage_dice"] == "1d6+8",
      atk[0]["damage"][0]["damage_dice"] if atk else "—")
check("a rider damage type rides along",
      atk and atk[0]["damage"][1]["damage_dice"] == "1d6", "+1d6 force")
check("reach is parseable out of the desc",
      atk and "reach 5 ft." in atk[0]["desc"])
multi = [a for a in fey5.actions if a["name"] == "Multiattack"]
check("Multiattack says the WORD the engine matches",
      multi and "makes two attacks" in multi[0]["desc"])
check("...and is omitted entirely at one attack",
      not [a for a in fey.actions if a["name"] == "Multiattack"],
      "a 3rd-level fey gets 1 attack, and 'makes one attack' reads as 2")

# ----------------------------------------------------------------------
section("4. a variant is a different creature, not a label")
air = conjure("Summon Beast", 2, "Air")
land = conjure("Summon Beast", 2, "Land")
check("the choice changes hit points", (air.hit_points, land.hit_points) == (20, 30))
check("...and how it moves", air.speed.get("fly") == 60 and land.speed.get("climb") == 30)
check("a trait gated to one variant appears only there",
      [t["name"] for t in air.special_abilities] == ["Flyby"]
      and [t["name"] for t in land.special_abilities] == ["Pack Tactics"])
slaad = conjure("Summon Aberration", 4, "Slaad")
beholder = conjure("Summon Aberration", 4, "Beholderkin")
check("a gated ACTION appears only on its variant",
      [a["name"] for a in slaad.actions if a.get("attack_bonus")] == ["Claws"]
      and [a["name"] for a in beholder.actions if a.get("attack_bonus")] == ["Eye Ray"])
check("an unnamed variant still builds something legal",
      conjure("Summon Elemental", 4) is not None)

# ----------------------------------------------------------------------
section("5. the caster's save DC reaches the creature's own text")
starspawn = conjure("Summon Aberration", 4, "Star Spawn")
aura = next(t for t in starspawn.special_abilities if t["name"] == "Whispering Aura")
check("a trait's DC is the caster's, filled in by code", f"DC {DC}" in aura["desc"],
      aura["desc"][:60] + "…")
check("two casters of different skill get different creatures",
      summons.materialize("Summon Fey", level=3, variant="Mirthful", engine=engine,
                          attack_bonus=5, save_dc=13).index_slug != fey.index_slug)

# ----------------------------------------------------------------------
section("6. Devil's Sight arrives as a SENSE, not as prose")
devil = conjure("Summon Fiend", 6, "Devil")
check("the fiend's senses carry devils_sight",
      devil.senses.get("devils_sight") == 60, str(devil.senses))
from survival.light import perceives  # noqa: E402
seen = perceives("dark", 30, devil.senses, obscured="heavy", magical_dark=True)
check("...so the light engine lets it see through magical darkness", seen["sees"])
demon = conjure("Summon Fiend", 6, "Demon")
check("a demon gets no such sense", "devils_sight" not in (demon.senses or {}))

# ----------------------------------------------------------------------
section("7. it fights FOR you")
enc = tracker.start_encounter("smoke:summons", "Test")
pc = tracker.add_pc(enc.id, name="Kara", max_hp=30, armor_class=15,
                    dex_mod=2, initiative=17)
foe = tracker.add_combatant(enc.id, "Bandit", max_hp=11, armor_class=12,
                            dex_mod=1, initiative=12)
spirit = tracker.add_from_monster(enc.id, fey.index_slug, side="party",
                                  initiative=pc.initiative, dex_mod=pc.dex_mod)[0]
check("a summon is stored on the party's side", spirit.side == "party")
check("...while still being a monster row",
      spirit.kind == CombatantKind.MONSTER and spirit.monster_slug == fey.index_slug)


class _Rules:
    def get_monster(self, slug):
        with Session(engine) as s:
            return s.exec(select(Monster).where(Monster.index_slug == slug)).first()


from combat.engine import CombatEngine  # noqa: E402

eng = CombatEngine(tracker)
check("the engine agrees it is an ally",
      eng._side(spirit) == eng._side(pc) != eng._side(foe))
check("an unmarked monster is still a foe by default", eng._side(foe) == "foe")

order = [c.name for c in tracker.order(enc.id)]
check("it acts immediately after its summoner", order.index("Fey Spirit (Mirthful)")
      == order.index("Kara") + 1, " → ".join(order))
check("...ahead of a foe with lower initiative",
      order.index("Bandit") > order.index("Fey Spirit (Mirthful)"))

# ----------------------------------------------------------------------
section("8. the board reads it like any other creature")
from vtt.bridge import roster_for, _size_for, _speed_for  # noqa: E402

sizes = roster_for(tracker, enc.id, rules_lib=_Rules())
check("the spirit contributes a real size and speed to board sizing",
      (1, 40) in sizes, str(sizes))
large = conjure("Summon Celestial", 5, "Defender")
check("a Large spirit is two squares across", _size_for(large) == "large")
check("...and walks at its listed speed", _speed_for(large) == 30,
      "the board token takes the WALKING speed; the fly speed is on the row")
check("...with its flight recorded for anything that asks",
      large.speed.get("fly") == 40)

# ----------------------------------------------------------------------
section("9. nothing in the catalogue answers to a made-up spell")
check("an unknown spell conjures nothing",
      summons.spirit_for("Summon Accountant") is None)
check("the generic ships in the repo, so a bookless checkout still summons",
      "conjured-spirit" in summons.catalog())

# ----------------------------------------------------------------------
section("10. through the backend's own hooks")
import importlib.util  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)                                    # type: ignore
SQLModel.metadata.create_all(m.engine)

with Session(m.engine) as s:
    # Level 7 wizard, INT 18: proficiency +3, spell attack +7, save DC 15 —
    # the same caster the blocks above were built for.
    wiz = m.Character(discord_user_id="smoke", name="Perrin",
                      char_class="Wizard", level=7,
                      stats={"strength": 8, "dexterity": 14, "constitution": 12,
                             "intelligence": 18, "wisdom": 10, "charisma": 10},
                      spells=["Summon Fey", "Fire Bolt"],
                      prepared_spells=["Summon Fey"])
    s.add(wiz)
    # The scratch database carries no SRD, and resolve_cast_hooks reads the
    # slot level off the spell row — so the one spell being cast has to exist.
    from rules.models import Spell                              # noqa: E402
    s.add(Spell(index_slug="summon-fey", name="Summon Fey", level=3,
                school="Conjuration", casting_time="Action", range="90 feet",
                duration="Concentration, up to 1 hour", concentration=True,
                components=["V", "S", "M"], classes=["wizard"]))
    s.commit()
    s.refresh(wiz)

rec: dict = {}
out = m.resolve_cast_hooks("She calls. [[CAST: Summon Fey | 4]]", wiz, rec)
check("the cast spends a 4th-level slot", rec.get("summon fey") == 4, out.strip())
out = m.resolve_summon_hooks("[[SUMMON: Summon Fey | Tricksy | 3]]", wiz,
                             "smoke:summons", rec)
check("the summon reports a real creature", "AC 16" in out and "40 HP" in out,
      out.strip())
check("...built at the level the SLOT actually was, not the one the DM typed",
      "4th-level slot" in out)

# A cast that fails conjures nothing at all: the slot was never spent, so
# neither is the creature. A level-7 wizard has no 5th-level slot.
rec2: dict = {}
failed = m.resolve_cast_hooks("[[CAST: Summon Fey | 5]]", wiz, rec2)
check("a slot the caster doesn't have is refused",
      rec2.get("summon fey") is None and "sputters out" in failed)
check("...and the spell that sputtered out summons nothing",
      m.resolve_summon_hooks("[[SUMMON: Summon Fey | Fuming]]", wiz,
                             "smoke:summons", rec2).strip() == "")
check("an unknown spell says so instead of conjuring",
      "nothing answers" in m.resolve_summon_hooks(
          "[[SUMMON: Summon Accountant]]", wiz, "smoke:summons", {}))
check("no hook survives into the narration when there is no character",
      "SUMMON" not in m._strip_mechanic_hooks("x [[SUMMON: Summon Fey | Fuming]] y"))

# The hook binds the spirit to the concentration that is holding it up — which
# needs the summoner to be IN the fight, as a combatant the sheet points at.
enc_h = m.combat.start_encounter("smoke:hook", "Hook")
seat = m.combat.add_pc(enc_h.id, name="Perrin", max_hp=30, armor_class=12,
                       character_id=wiz.id, initiative=14)
rec3: dict = {}
m.resolve_cast_hooks("[[CAST: Summon Fey | 3]]", wiz, rec3)
out = m.resolve_summon_hooks("[[SUMMON: Summon Fey | Fuming]]", wiz,
                             "smoke:hook", rec3)
check("the hook starts the caster's concentration",
      m.combat.get_combatant(seat.id).concentration == "Summon Fey", out.strip())
bound = [c for c in m.combat.order(enc_h.id) if c.summoned_by == seat.id]
check("...and binds the spirit to that spell",
      len(bound) == 1 and bound[0].summon_spell == "Summon Fey")
dropped = m.combat.set_concentration(seat.id, None)
check("...so dropping concentration ends it",
      dropped["dismissed"] == [bound[0].name])

# ----------------------------------------------------------------------
section("11. it lasts exactly as long as the concentration does")
enc2 = tracker.start_encounter("smoke:conc", "Concentration")
caster = tracker.add_pc(enc2.id, name="Perrin", max_hp=40, armor_class=12,
                        dex_mod=1, initiative=15)


def summon(spell="Summon Fey"):
    return tracker.add_from_monster(
        enc2.id, fey.index_slug, side="party", initiative=caster.initiative,
        dex_mod=caster.dex_mod, summoned_by=caster.id, summon_spell=spell)[0]


tracker.set_concentration(caster.id, "Summon Fey")
spirit = summon()
check("damage that doesn't break concentration leaves it standing",
      not tracker.get_combatant(spirit.id).defeated)

# No modifier callback yet: the check is reported as pending, exactly as before.
out = tracker.apply_damage(caster.id, 8)
check("with no CON-save provider the save is still only REPORTED",
      out["concentration_check"] and out.get("concentration_roll") is None
      and out["dismissed"] == [], str(out.get("concentration_dc")))

# Now install one and make the save impossible to pass.
tracker.con_save_mod_for = lambda c: -50
out = tracker.apply_damage(caster.id, 8)
check("a failed save actually ends the concentration",
      out["concentration_roll"] is not None
      and not out["concentration_roll"]["success"]
      and tracker.get_combatant(caster.id).concentration is None)
check("...and the spirit it was holding up vanishes",
      out["dismissed"] == [spirit.name]
      and tracker.get_combatant(spirit.id).defeated, str(out["dismissed"]))

tracker.con_save_mod_for = lambda c: 50
tracker.set_concentration(caster.id, "Summon Fey")
spirit2 = summon()
out = tracker.apply_damage(caster.id, 8)
check("a save that HOLDS keeps the spirit",
      out["concentration_roll"]["success"] and out["dismissed"] == []
      and not tracker.get_combatant(spirit2.id).defeated)

moved = tracker.set_concentration(caster.id, "Haste")
check("moving concentration to another spell ends the summon too",
      moved["dismissed"] == [spirit2.name])

tracker.set_concentration(caster.id, "Summon Fey")
spirit3 = summon()
recast = tracker.set_concentration(caster.id, "Summon Fey")
check("re-casting the same spell replaces the old spirit",
      recast["dismissed"] == [spirit3.name])

tracker.set_concentration(caster.id, "Summon Fey")
spirit4 = summon()
out = tracker.apply_damage(caster.id, 500)
check("a summoner dropped to 0 takes their spirits with them — no save",
      out["defeated"] and out["dismissed"] == [spirit4.name]
      and out.get("concentration_roll") is None
      and tracker.get_combatant(spirit4.id).defeated)

# A spirit bound to nothing (a summon with no concentration) is untouched.
tracker.heal(caster.id, 40)
tracker.set_concentration(caster.id, "Summon Fey")
free = tracker.add_from_monster(enc2.id, fey.index_slug, side="party",
                                summoned_by=caster.id, summon_spell=None)[0]
ended = tracker.set_concentration(caster.id, None)
check("a spirit bound to no spell is never dismissed by one",
      ended["dismissed"] == [] and not tracker.get_combatant(free.id).defeated)

print()
if _failures:
    print(f"{RED}{_failures} check(s) failed{OFF}")
    sys.exit(1)
print(f"{GREEN}a conjured spirit is a real creature{OFF}")
