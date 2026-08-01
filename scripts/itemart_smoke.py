"""Item-art policy: catalog art is shared, a named piece is the player's own,
and renaming must never cost the item its mechanics."""
from __future__ import annotations
import importlib.util, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))
db = os.path.join(tempfile.gettempdir(), "oracle_itemart_check.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"
spec = importlib.util.spec_from_file_location(
    "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

from sqlmodel import Session, SQLModel
SQLModel.metadata.create_all(m.engine)

# A tiny catalog: one real item to rename, so stat preservation is testable.
from rules.models import Item
with Session(m.engine) as s:
    s.add(Item(index_slug="longsword", name="Longsword", item_type="Martial",
               category="weapon", damage_dice="1d8", damage_type="slashing",
               weight=3, cost_gp=15, desc="A straight-bladed sword."))
    s.commit()

with Session(m.engine) as s:
    ch = m.Character(discord_user_id="u1", name="Kara Emberfall", race="Human",
                     char_class="Fighter", level=3, approved=True,
                     max_hp=28, current_hp=28,
                     stats={"strength": 16, "dexterity": 14, "constitution": 14,
                            "intelligence": 10, "wisdom": 12, "charisma": 8},
                     inventory=[{"name": "Longsword", "quantity": 1},
                                {"name": "Grandfather's Locket", "quantity": 1}])
    s.add(ch); s.commit(); s.refresh(ch)
    cid = ch.id

fails = []

def reload():
    with Session(m.engine) as s:
        return s.get(m.Character, cid)

ch = reload()

# 1. Policy: a catalog item with no art waits for the batch; an unknown one
#    must be described. Neither renders during play.
st_known = m._item_art_state(ch, "Longsword")
st_unknown = m._item_art_state(ch, "Grandfather's Locket")
print("1: longsword ->", st_known, "| locket ->", st_unknown)
if st_known != "pending":
    fails.append(f"catalog item should wait for the batch, got {st_known}")
if st_unknown != "describe":
    fails.append(f"unknown item should ask for a description, got {st_unknown}")

# 2. A plain item shares the global ref, so one render serves everyone.
ref, desc = m._item_art_ref(ch, "Longsword")
print("2: shared ref ->", ref, "| desc:", desc)
if ref != "Longsword" or desc is not None:
    fails.append(f"a plain item must use the shared catalog ref, got {ref}")

# 3. Name it. The piece becomes this character's own.
with Session(m.engine) as s:
    ch = s.get(m.Character, cid)
    new_name, new_ref = m._name_and_describe_item(
        ch, "Longsword", "a pale blade with a sunburst pommel, edge worn bright",
        "Dawnbreaker")
    s.add(ch); s.commit()
print("3: renamed to", new_name, "ref", new_ref)
ch = reload()
if new_name != "Dawnbreaker":
    fails.append("rename did not take")
if new_ref == "Longsword" or "kara" not in new_ref:
    fails.append(f"named piece must get its OWN ref, got {new_ref}")

ref2, desc2 = m._item_art_ref(ch, "Dawnbreaker")
if ref2 != new_ref or not desc2:
    fails.append(f"named piece lost its ref/description: {ref2} {desc2!r}")

# 4. THE IMPORTANT ONE: renaming must not cost the item its mechanics.
if m._item_base_name(ch, "Dawnbreaker") != "Longsword":
    fails.append("the catalog base name was lost on rename")
detail = m._activity_item_detail(ch, "Dawnbreaker")
print("4: detail ->", detail.get("name"), "|", detail.get("type"),
      "|", detail.get("stats"))
if detail.get("type") != "Martial":
    fails.append(f"renamed item lost its type: {detail.get('type')}")
stats = " ".join(detail.get("stats") or [])
for want in ("1d8", "15 gp", "3 lb"):
    if want not in stats:
        fails.append(f"renamed item lost a stat ({want}): {stats!r}")

# 5. The pack still shows it, under the new name, with the base's mechanics.
inv = m._activity_inventory(ch)
row = next((r for r in inv if r["name"] == "Dawnbreaker"), None)
print("5: pack row ->", row)
if not row or row.get("type") != "Martial" or not row.get("named"):
    fails.append(f"the pack row is wrong for a named piece: {row}")

# 6. A named piece no longer waits on the catalog batch — it is the player's,
#    and asking again should offer to (re)draw it, not to describe it afresh.
st_named = m._item_art_state(ch, "Dawnbreaker")
print("6: named piece state ->", st_named)
if st_named != "describe":
    fails.append(f"expected a named-but-undrawn piece to be drawable, got {st_named}")

# 7. Two characters' identically-named pieces must not collide.
with Session(m.engine) as s:
    other = m.Character(discord_user_id="u2", name="Aldric the Bold", race="Human",
                        char_class="Fighter", level=3, approved=True,
                        max_hp=28, current_hp=28, stats={"strength": 16},
                        inventory=[{"name": "Longsword", "quantity": 1}])
    s.add(other); s.commit(); s.refresh(other)
    _n, ref_other = m._name_and_describe_item(
        other, "Longsword", "a black blade, notched near the hilt", "Dawnbreaker")
    s.add(other); s.commit()
print("7: refs ->", new_ref, "vs", ref_other)
if new_ref == ref_other:
    fails.append("two characters' named pieces collided on one ref")

print("\nFAILS:", fails or "none")
sys.exit(1 if fails else 0)
