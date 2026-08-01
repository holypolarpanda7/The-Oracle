"""Affixes end to end: a drop rolls properties, they are mechanically real,
and the forge takes coin to reroll one."""
import importlib.util, os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))
db = os.path.join(tempfile.gettempdir(), "oracle_affix_check.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"
spec = importlib.util.spec_from_file_location(
    "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
from sqlmodel import Session, SQLModel
SQLModel.metadata.create_all(m.engine)
from rules.models import Item
with Session(m.engine) as s:
    s.add(Item(index_slug="chain-mail", name="Chain Mail", item_type="Armor",
               category="armor", armor_class_base=16, armor_dex_bonus=False,
               weight=55, cost_gp=75, desc="Interlocking metal rings."))
    s.commit()

fails = []
with Session(m.engine) as s:
    ch = m.Character(discord_user_id="u1", name="Sable", race="Human",
                     char_class="Fighter", level=3, approved=True,
                     max_hp=28, current_hp=28,
                     stats={"strength": 16, "dexterity": 10, "constitution": 14,
                            "intelligence": 10, "wisdom": 12, "charisma": 8},
                     inventory=[])
    s.add(ch); s.commit(); s.refresh(ch); cid = ch.id

sid = "affix:table"
m._set_session_meta(sid, {"user_id": "u1", "character_id": cid,
                          "character_name": "Sable",
                          "members": {"u1": {"character_id": cid,
                                             "character_name": "Sable"}}})

# 1. The hook is stripped from the prose and drops the gear.
clean, ops = m.extract_loot_hooks(
    "The chest gives up its due.\n[[LOOT: Chain Mail | rare]]\nDust settles.")
print("1: clean=", repr(clean), "ops=", ops)
if "LOOT" in clean or "[[" in clean:
    fails.append("the hook was not stripped from the prose")
if ops != [{"name": "Chain Mail", "rarity": "rare"}]:
    fails.append(f"hook parsed wrong: {ops}")

notes = m.apply_loot_hooks(sid, ops, cid)
print("2: notes=", notes)
with Session(m.engine) as s:
    ch = s.get(m.Character, cid)
    entry = next(iter(m._inventory_items(ch)), None)
print("3: entry=", entry)
if not entry or not entry.get("affixes"):
    fails.append("the drop rolled no properties")
if entry and entry.get("base") != "Chain Mail":
    fails.append("the drop lost its catalog base name")
if entry and entry.get("name") == "Chain Mail":
    fails.append("a rolled piece should not keep the plain name")

# 4. Renamed by its affixes, it must STILL resolve its catalog mechanics.
with Session(m.engine) as s:
    ch = s.get(m.Character, cid)
    detail = m._activity_item_detail(ch, entry["name"])
print("4: detail type=", detail.get("type"), "stats=", detail.get("stats"),
      "affixes=", [a["name"] for a in detail.get("affixes", [])])
if detail.get("type") != "Armor":
    fails.append("a rolled piece lost its catalog type")
if not detail.get("affixes"):
    fails.append("the inspector shows no properties")
if not any("temper_gp" in a for a in detail.get("affixes", [])):
    fails.append("no temper price offered")

# 5. An AC affix must reach the ACTUAL armour class, not just the text.
from loot import mechanical_bonuses
with Session(m.engine) as s:
    ch = s.get(m.Character, cid)
    inv = list(ch.inventory)
    inv[0] = {**inv[0], "equipped": True}
    ch.inventory = inv
    # Force a known AC affix so the assertion is deterministic.
    inv[0]["affixes"] = ["adamant"]          # +2 AC
    inv[0]["name"] = "Adamant Chain Mail"
    ch.inventory = list(inv)
    s.add(ch); s.commit()
with Session(m.engine) as s:
    ch = s.get(m.Character, cid)
    ac = m._compute_ac(ch)
print("5: AC with Adamant (+2) chain mail, Dex 0 =", ac, "(want 18)")
if ac != 18:
    fails.append(f"affix AC did not reach the armour class: {ac} (want 16+2)")

# 6. The forge charges, and rerolls only the chosen property.
from loot import temper_cost_gp, temper_swap
cost = temper_cost_gp("rare", 3)
before = ["adamant", "of-embers"]
after = temper_swap(before, "adamant", item_name="Chain Mail", rarity="rare",
                    item_type="Armor", category="armor", seed="t")
print("6: cost", cost, "gp |", before, "->", after)
if "of-embers" not in after:
    fails.append("tempering disturbed a property it was not asked to touch")
if "adamant" in after:
    fails.append("tempering did not replace the chosen property")
if len(after) != len(before):
    fails.append("tempering changed how many properties the piece carries")

print("\nFAILS:", fails or "none")
sys.exit(1 if fails else 0)

