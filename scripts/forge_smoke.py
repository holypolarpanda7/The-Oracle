"""A forge is a place you go to, not a button you own."""
import importlib.util, os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))
db = os.path.join(tempfile.gettempdir(), "oracle_forge_check.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"
spec = importlib.util.spec_from_file_location(
    "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
from sqlmodel import Session, SQLModel
SQLModel.metadata.create_all(m.engine)
from eight_card_system.seed import seed_minimal_world, place_pc
from eight_card_system.models import EntityType
seed_minimal_world(m.world)

with Session(m.engine) as s:
    ch = m.Character(discord_user_id="u1", name="Sable", race="Human",
                     char_class="Fighter", level=3, approved=True,
                     max_hp=28, current_hp=28, stats={"strength": 16})
    s.add(ch); s.commit(); s.refresh(ch); cid = ch.id
place_pc(m.world, "Sable", discord_user_id="u1")
pc = m.world.find_pc("u1", "Sable")
sid = "forge:table"
m._set_session_meta(sid, {"user_id": "u1", "character_id": cid,
                          "character_name": "Sable",
                          "members": {"u1": {"character_id": cid,
                                             "character_name": "Sable"}}})
fails = []

# A tavern cannot reforge a legendary.
where = m.world.location_of(pc.slug)
got = m._forge_here(sid, "u1")
print(f"in {where.name!r}: {got!r}")
if got:
    fails.append(f"a tavern offered to reforge gear ({got})")

# A smith standing here can.
smith = m.world.upsert_entity("Hob the Smith", EntityType.NPC, subtype="blacksmith")
m.world.move_entity(smith.slug, where.slug)
got = m._forge_here(sid, "u1")
print(f"with a blacksmith present: {got!r}")
if got != "Hob the Smith":
    fails.append(f"a smith standing here was not found: {got}")

# So can a smithy, with nobody named in it.
m.world.move_entity(smith.slug, "millbrook")
forge = m.world.upsert_entity("The Ironworks", EntityType.PLACE, subtype="smithy")
m.world.move_entity(pc.slug, forge.slug)
got = m._forge_here(sid, "u1")
print(f"standing in a smithy: {got!r}")
if got != "The Ironworks":
    fails.append(f"a smithy did not count as a forge: {got}")

print("\nFAILS:", fails or "none")
sys.exit(1 if fails else 0)
