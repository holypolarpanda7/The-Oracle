"""Spell components smoke test — V, S, M made legible, priced and enforced.

Offline, no GPU, no LLM, fresh scratch database. Three layers:

    1. LEGIBLE  — the components and the material reach the DM at all.
    2. PRICED   — a costly component is read out of the book's own prose, has
                  to be in the pack, and is destroyed when the spell says so.
    3. GATED    — a creature that can't act casts nothing, and a Verbal
                  component can't be spoken inside silence.

    uv run python scripts/components_smoke.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="oracle-components-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'scratch.db'}"

from sqlmodel import Session, SQLModel  # noqa: E402

from rules import components as rc      # noqa: E402
from rules.models import Spell          # noqa: E402

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
section("1. reading a material component out of the book's own prose")

revivify = rc.parse_material("a diamond worth 300+ GP, which the spell consumes")
check("a price is read", revivify.cost_gp == 300, f"{revivify.cost_gp} GP")
check("...and so is the fact that it burns", revivify.consumed
      and revivify.consumed_gp == 300)

clone = rc.parse_material(
    "a diamond worth 1,000+ GP, which the spell consumes, and a sealable "
    "vessel worth 2,000+ GP that is large enough to hold the creature")
check("two objects are two prices", clone.cost_gp == 3000, f"{clone.cost_gp} GP")
check("...and only the named one is destroyed", clone.consumed_gp == 1000,
      "the vessel survives; the diamond does not")

blade = rc.parse_material("a melee weapon worth at least 1 SP")
check("silver is not gold", blade.cost_gp == 0.1, rc.format_cost(blade.cost_gp))

guano = rc.parse_material("a ball of bat guano and sulfur")
check("an unpriced component costs nothing", guano.cost_gp == 0)
check("...and a focus stands in for it",
      rc.SpellComponents(material=True, text="x").focus_ok)
check("a priced one is never covered by a focus", not revivify.focus_ok)

soot = rc.parse_material("a pinch of soot, which the spell consumes")
check("a component can burn without being priced",
      soot.consumed and soot.cost_gp == 0)

check("an unknown spell is assumed to be seen and heard",
      rc.components_of(None).perceptible,
      "the safe direction for the cheating check and the gate alike")

# ----------------------------------------------------------------------
section("2. legible — the brief finally prints them")
from rules.query import format_spell_brief  # noqa: E402

row = Spell(index_slug="revivify", name="Revivify", level=3, school="Necromancy",
            casting_time="Action", range="Touch", duration="Instantaneous",
            components=["V", "S", "M"],
            material="a diamond worth 300+ GP, which the spell consumes",
            desc="The creature revives with 1 Hit Point.")
brief = format_spell_brief(row)
check("the DM is shown the component", "Components: V, S, M" in brief)
check("...the object", "a diamond worth 300+ GP" in brief)
check("...the price", "300 GP" in brief)
check("...and that the casting destroys it", "consumed" in brief, brief.splitlines()[2])

# ----------------------------------------------------------------------
section("3. enforced — through the backend's own cast hook")
_spec = importlib.util.spec_from_file_location(
    "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)                                    # type: ignore
SQLModel.metadata.create_all(m.engine)

with Session(m.engine) as s:
    s.add(Spell(index_slug="revivify", name="Revivify", level=3,
                school="Necromancy", casting_time="Action", range="Touch",
                duration="Instantaneous", components=["V", "S", "M"],
                material="a diamond worth 300+ GP, which the spell consumes",
                classes=["cleric"]))
    s.add(Spell(index_slug="fireball", name="Fireball", level=3,
                school="Evocation", casting_time="Action", range="150 feet",
                duration="Instantaneous", components=["V", "S", "M"],
                material="a ball of bat guano and sulfur", classes=["wizard"]))
    s.add(Spell(index_slug="mage-hand", name="Mage Hand", level=0,
                school="Conjuration", casting_time="Action", range="30 feet",
                duration="1 minute", components=["V", "S"], classes=["wizard"]))
    s.add(Spell(index_slug="healing-word", name="Healing Word", level=1,
                school="Abjuration", casting_time="Bonus Action",
                range="60 feet", duration="Instantaneous",
                components=["V"], classes=["cleric"]))
    cleric = m.Character(
        discord_user_id="smoke", name="Sera", char_class="Cleric", level=5,
        stats={"strength": 10, "dexterity": 12, "constitution": 14,
               "intelligence": 10, "wisdom": 18, "charisma": 12},
        spells=["Revivify", "Fireball", "Mage Hand", "Healing Word"],
        prepared_spells=["Revivify", "Fireball", "Mage Hand", "Healing Word"],
        inventory=[{"name": "Holy Symbol", "quantity": 1},
                   {"name": "Diamond", "quantity": 2},
                   {"name": "Explorer's Pack", "quantity": 1}])
    s.add(cleric)
    s.commit()
    s.refresh(cleric)


def cast(spell, level=3, ch=None, sid=None):
    return m.resolve_cast_hooks(f"[[CAST: {spell} | {level}]]", ch or cleric,
                                None, sid).strip()


out = cast("Revivify")
check("a caster holding the diamond may cast", "sputters" not in out
      and "won't come" not in out, out)
check("...and the casting destroys it", "Diamond is consumed" in out, out)
check("...leaving one behind", any(
    i["name"] == "Diamond" and i["quantity"] == 1
    for i in m._inventory_items(cleric)), str(m._inventory_items(cleric)))

cast("Revivify")                                    # spends the second one
check("the last diamond is gone from the pack",
      not any(i["name"] == "Diamond" for i in m._inventory_items(cleric)))
out = cast("Revivify")
check("and now the spell is refused, naming what it wants",
      "won't come" in out and "diamond worth 300+ GP" in out, out)

cleric.spell_slots_used = {}          # the diamonds cost two 3rd-level slots
out = cast("Fireball")
check("a costless material is covered by what's in the pack",
      "won't come" not in out and "sputters" not in out, out)
check("...and nothing is destroyed for it", "consumed" not in out)

# Strip the pack bare: no focus, no pouch, and a bat-guano spell fails.
cleric.inventory = [{"name": "Rope, Hempen (50 feet)", "quantity": 1}]
out = cast("Fireball")
check("no focus and no pouch means no material component at all",
      "won't come" in out and "focus" in out, out)
check("...but a spell with no material at all is unaffected",
      "won't come" not in cast("Healing Word", 1), cast("Healing Word", 1))
cleric.inventory = [{"name": "Holy Symbol", "quantity": 1}]

# An EMPTY pack is unknown, not empty-handed — imported sheets arrive that way
# and a false refusal stops play dead.
bare = m.Character(discord_user_id="smoke2", name="Ghost", char_class="Cleric",
                   level=5, stats={"wisdom": 16},
                   spells=["Fireball"], prepared_spells=["Fireball"])
check("an unknown pack is never used to refuse a spell",
      "won't come" not in m.resolve_cast_hooks("[[CAST: Fireball | 3]]", bare))

# ----------------------------------------------------------------------
section("4. gated — a caster who can't act, and one who can't speak")
cleric.conditions = ["paralyzed"]
out = cast("Mage Hand", 0)
check("a paralyzed caster casts nothing at all",
      "won't come" in out and "paralyzed" in out, out)
cleric.conditions = ["silenced"]
check("silence stops a Verbal component",
      "won't come" in cast("Healing Word", 1), cast("Healing Word", 1))
cleric.conditions = []
check("...and lifting it lets the spell through",
      "won't come" not in cast("Healing Word", 1))

# The board's half: a `silences` area, the same shape as `obscured`.
from vtt import VttEngine  # noqa: E402

check("an effect can be marked as silencing", hasattr(
    m.vtt_engine.add_effect, "__call__"))
scene = m.vtt_engine.open_scene("smoke:comp", kind="combat", width=12, height=12)
tok = m.vtt_engine.add_token(scene.id, "Sera", x=3, y=3)
m.vtt_engine.add_effect(scene.id, "Silence", shape="sphere", x=3, y=3,
                        radius_ft=20, silences=True)
check("the board knows which squares are quiet",
      m.vtt_engine.silenced_at(scene.id, 3, 3)
      and not m.vtt_engine.silenced_at(scene.id, 11, 11))
check("...and which creature is standing in one",
      m.vtt_engine.token_silenced(scene.id, "Sera"))
check("a silence zone ships in state() for both renderers",
      any(e.get("silences") for e in m.vtt_engine.state(scene.id)["effects"]))

# ----------------------------------------------------------------------
section("5. a refusal never costs the caster anything")
before = dict(cleric.spell_slots_used or {})
cleric.conditions = ["stunned"]
cast("Revivify")
check("a gated casting does not burn a slot",
      dict(cleric.spell_slots_used or {}) == before,
      str(cleric.spell_slots_used))
cleric.conditions = []
cleric.inventory = [{"name": "Holy Symbol", "quantity": 1}]
before = dict(cleric.spell_slots_used or {})
cast("Revivify")
check("nor does a missing material", dict(cleric.spell_slots_used or {}) == before)

# ----------------------------------------------------------------------
section("6. the DM's board says so before the refusal ever happens")
cleric.conditions = []
cleric.inventory = [{"name": "Holy Symbol", "quantity": 1}]
with Session(m.engine) as s:
    row = s.get(m.Character, cleric.id)
    row.inventory = cleric.inventory
    row.conditions = []
    s.add(row)
    s.commit()
block = m._character_resource_block(cleric.id)
check("the costly component is named on the DM's board",
      "Costly material components" in block and "diamond worth 300+ GP" in block,
      next((l[:110] for l in block.splitlines()
            if "Costly material" in l), "(absent)"))
check("...and that the casting destroys it", "DESTROYED" in block)
check("a costless one is NOT listed as a cost",
      "bat guano" not in block, "Fireball's component is covered by a focus")
check("the DM is told the game enforces the gate",
      "Incapacitated" in block and "magical silence" in block)
check("...and told which call is still theirs",
      "hand is free" in block)

print()
if _failures:
    print(f"{RED}{_failures} check(s) failed{OFF}")
    sys.exit(1)
print(f"{GREEN}a component is a cost, and the cost is paid{OFF}")
