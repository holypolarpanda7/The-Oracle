"""Prove the Chronicle reads real world state, and the SUGGEST hook parses."""
from __future__ import annotations
import importlib.util, json, os, sys, tempfile
from pathlib import Path

ROOT = Path("/mnt/d/Projects/The Oracle")
sys.path.insert(0, str(ROOT))

db = os.path.join(tempfile.gettempdir(), "oracle_chronicle_check.db")
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
from eight_card_system.models import EntityType, QuestState
from eight_card_system.relationships import record_deed

seed_minimal_world(m.world)
session_id, user_id = "chr:table", "chr-user"
other_sid = "chr:other-table"

with Session(m.engine) as s:
    char = m.Character(discord_user_id=user_id, name="Sable", race="Human",
                       char_class="Fighter", level=3, approved=True,
                       max_hp=28, current_hp=28, home_region="Greenfields",
                       stats={"strength": 16, "dexterity": 14, "constitution": 14,
                              "intelligence": 10, "wisdom": 12, "charisma": 8})
    s.add(char); s.commit(); s.refresh(char)
    char_id = char.id

place_pc(m.world, "Sable", discord_user_id=user_id)
m._set_session_meta(session_id, {
    "user_id": user_id, "character_id": char_id, "character_name": "Sable",
    "members": {user_id: {"character_id": char_id, "character_name": "Sable"}},
})
pc = m.world.find_pc(user_id, "Sable")

fails = []

# ---- events: this table's, ours by involvement, and someone else's ----
m.world.add_event("The party rode out of Millbrook at dawn.",
                  location="millbrook", session_id=session_id)
m.world.add_event("Sable broke the reeve's nose.", involved=[pc.slug],
                  session_id=other_sid)
m.world.add_event("A stranger's business, elsewhere entirely.",
                  session_id="chr:not-ours")

# ---- quests ----
m.world.upsert_entity("The Mill That Grinds No Grain", EntityType.QUEST, attributes={
    "state": QuestState.ACTIVE, "tier": "main", "last_touched_day": 3,
    "conflict": "Someone works the mill at midnight.",
    "stakes": "Another field blights each night.",
    "objectives": [{"text": "Get inside unseen", "done": False},
                   {"text": "Ask Marla", "done": True}]})
m.world.upsert_entity("An Old Debt", EntityType.QUEST,
                      attributes={"state": QuestState.COMPLETED, "tier": "side",
                                  "last_touched_day": 1})

# ---- bonds ----
marla = m.world.upsert_entity("Old Marla", EntityType.NPC, subtype="miller")
m.world.move_entity(marla.slug, "the-silver-tankard")
record_deed(m.world, pc.slug, marla.slug, tag="rescue",
            text="Pulled her from the millrace.")
reeve = m.world.upsert_entity("Garrick Vane", EntityType.NPC, subtype="reeve")
record_deed(m.world, pc.slug, reeve.slug, tag="cruelty",
            text="Named him a coward before the hall.")
# A deity holds no personal bond and must not appear in the list.
god = m.world.upsert_entity("Vashra the Unlit", EntityType.DEITY)
m.world.add_relation(god.slug, "knows", pc.slug)

jr = m.journal = m._activity_journal(session_id, user_id)
bonds = m._activity_bonds(session_id, user_id)
print(json.dumps({"journal": jr, "bonds": bonds}, indent=2)[:2600])

texts = [e["text"] for e in jr["entries"]]
if not any("rode out of Millbrook" in t for t in texts):
    fails.append("this table's own event is missing")
if not any("broke the reeve's nose" in t for t in texts):
    fails.append("an event the PC was involved in (other session) is missing")
if any("stranger's business" in t for t in texts):
    fails.append("another table's unrelated event leaked in")
if not any(e.get("place") == "Millbrook" for e in jr["entries"]):
    fails.append("event place name not resolved")

qnames = {q["name"]: q for q in jr["quests"]}
if "The Mill That Grinds No Grain" not in qnames:
    fails.append("the active quest is missing")
else:
    q = qnames["The Mill That Grinds No Grain"]
    if q.get("objectives") != ["Get inside unseen"]:
        fails.append(f"objectives wrong (done step leaked?): {q.get('objectives')}")
    if not q.get("stakes"):
        fails.append("quest stakes missing")
if "An Old Debt" not in qnames:
    fails.append("the settled quest is missing")

bn = {b["name"]: b for b in bonds}
if "Old Marla" not in bn or "Garrick Vane" not in bn:
    fails.append(f"a bond is missing: {list(bn)}")
if "Vashra the Unlit" in bn:
    fails.append("a deity leaked into the bonds list")
if bn.get("Old Marla", {}).get("sentiment", 0) <= 0:
    fails.append("the rescued NPC does not feel positively")
if bn.get("Garrick Vane", {}).get("sentiment", 0) >= 0:
    fails.append("the insulted NPC does not feel negatively")
if not bn.get("Old Marla", {}).get("reason"):
    fails.append("no reason recorded for a bond")
if bn.get("Old Marla", {}).get("role") != "miller":
    fails.append("bond role missing")

# ---- the SUGGEST hook ----
clean, actions = m.extract_suggest_hooks(
    "The mill door hangs open.\n"
    "[[SUGGEST: follow the tracks | sneak to the door |  | " + "x" * 90 + "]]\n"
    "[[SUGGEST: wait for dark | follow the tracks | one | two | three]]")
if "SUGGEST" in clean or "[[" in clean:
    fails.append("the hook was not stripped from the prose")
if "mill door hangs open" not in clean:
    fails.append("the hook stripper ate the narration")
if actions != ["follow the tracks", "sneak to the door", "wait for dark", "one"]:
    fails.append(f"suggestions wrong (dedupe/cap/length): {actions}")
print("\nsuggestions:", actions)
print("clean:", repr(clean))

print("\nFAILS:", fails or "none")
sys.exit(1 if fails else 0)
