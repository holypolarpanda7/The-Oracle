"""Prove `_activity_locale` returns a real payload against a seeded world."""
from __future__ import annotations
import importlib.util, json, os, sys, tempfile
from pathlib import Path

ROOT = Path("/mnt/d/Projects/The Oracle")
sys.path.insert(0, str(ROOT))

db = os.path.join(tempfile.gettempdir(), "oracle_locale_check.db")
if os.path.exists(db):
    os.remove(db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"

spec = importlib.util.spec_from_file_location(
    "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

from sqlmodel import Session, SQLModel
SQLModel.metadata.create_all(m.engine)

from eight_card_system.seed import seed_minimal_world, place_pc
from eight_card_system.models import RelationType, EntityType

seed_minimal_world(m.world)

session_id, user_id = "locale:table", "locale-user"
with Session(m.engine) as s:
    char = m.Character(
        discord_user_id=user_id, name="Sable", race="Human", char_class="Fighter",
        level=3, approved=True, max_hp=28, current_hp=28, home_region="Greenfields",
        stats={"strength": 16, "dexterity": 14, "constitution": 14,
               "intelligence": 10, "wisdom": 12, "charisma": 8})
    s.add(char); s.commit(); s.refresh(char)
    char_id = char.id

place_pc(m.world, "Sable", discord_user_id=user_id)
m._set_session_meta(session_id, {
    "user_id": user_id, "character_id": char_id, "character_name": "Sable",
    "members": {user_id: {"character_id": char_id, "character_name": "Sable"}},
})

# Someone to find in the room: an NPC who knows Sable, and one who doesn't.
pc_slug = m.world.find_pc(user_id, "Sable").slug
barkeep = m.world.upsert_entity("Hesta Vell", EntityType.NPC, subtype="innkeeper")
m.world.move_entity(barkeep.slug, "the-silver-tankard")
m.world.adjust_trust(barkeep.slug, pc_slug, 4, reason="paid the tab")
stranger = m.world.upsert_entity("A Hooded Traveller", EntityType.NPC)
m.world.move_entity(stranger.slug, "the-silver-tankard")
# ...and one standing somewhere else entirely, who must NOT show up.
elsewhere = m.world.upsert_entity("Far Gorm", EntityType.NPC)
m.world.move_entity(elsewhere.slug, "millbrook")

loc = m._activity_locale(session_id, user_id)
print(json.dumps(loc, indent=2))

fails = []
if not loc:
    fails.append("no payload at all")
else:
    for key in ("place", "date", "time_of_day", "weather"):
        if not loc.get(key):
            fails.append(f"missing {key}")
    if "present" not in loc:
        fails.append("missing present list")

# The room should list the NPCs standing in the same place, not the PC.
names = [p["name"] for p in (loc or {}).get("present", [])]
if "Sable" in names:
    fails.append("the PC lists themselves as present")
if "Hesta Vell" not in names:
    fails.append("an NPC in the room is missing")
if "Far Gorm" in names:
    fails.append("an NPC standing elsewhere leaked into the room")
hesta = next((p for p in (loc or {}).get("present", []) if p["name"] == "Hesta Vell"), {})
if not hesta.get("attitude"):
    fails.append("a known NPC carries no attitude")
if hesta.get("role") != "innkeeper":
    fails.append("the NPC's role is missing")

print("\npresent:", names or "(nobody)")
print("FAILS:", fails or "none")
sys.exit(1 if fails else 0)
