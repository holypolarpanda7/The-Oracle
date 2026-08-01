"""Routes: costed from the world's REAL geography, and never a map."""
import importlib.util, os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))
db = os.path.join(tempfile.gettempdir(), "oracle_routes_check.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"
spec = importlib.util.spec_from_file_location(
    "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
from sqlmodel import Session, SQLModel
SQLModel.metadata.create_all(m.engine)
from eight_card_system.seed import seed_minimal_world, place_pc
seed_minimal_world(m.world)

fails = []
with Session(m.engine) as s:
    ch = m.Character(discord_user_id="u1", name="Sable", race="Human",
                     char_class="Fighter", level=3, approved=True, max_hp=28,
                     current_hp=28, stats={"strength": 16})
    s.add(ch); s.commit()
place_pc(m.world, "Sable", discord_user_id="u1")
pc = m.world.find_pc("u1", "Sable")

# 1. The hook is stripped and parsed.
clean, dests = m.extract_routes_hooks(
    "You settle the tab.\n[[ROUTES: Millbrook]]\nThe door swings.")
print("1: clean=", repr(clean), "dests=", dests)
if "[[" in clean or "ROUTES" in clean: fails.append("hook not stripped")
if dests != ["Millbrook"]: fails.append(f"hook parsed wrong: {dests}")

# 2. Routes from somewhere to somewhere else exist and are costed.
from eight_card_system.models import EntityType
far = m.world.upsert_entity("Ashford", EntityType.PLACE, subtype="settlement",
                            attributes={"terrain": "forest", "danger": "moderate"})
from eight_card_system import geo
m.world.upsert_entity("Ashford", EntityType.PLACE, slug=far.slug,
                      attributes={**(far.attributes or {}),
                                  "coords": geo.coords_attr(45.4, 0.5)})
routes = m._routes_to(pc.slug, "Ashford")
for r in routes:
    print(f"   {r['label']:<14} {r['miles']:>6} mi  {r['days']:>4} d  {r['danger']}")
if len(routes) != 3: fails.append(f"expected 3 roads, got {len(routes)}")
if routes:
    # The shortcut must genuinely be shorter, and the high road longer.
    by = {r["label"]: r for r in routes}
    if not (by["the shortcut"]["miles"] < by["the old track"]["miles"]
            < by["the high road"]["miles"]):
        fails.append("the roads are not actually different lengths")
    if by["the shortcut"]["danger"] != "high":
        fails.append("the shortcut is not the dangerous one")
    if any(r["days"] <= 0 for r in routes):
        fails.append("a road takes no time at all")

# 3. NOT a map: no coordinate FIELDS may leak into the payload. Checked on the
#    keys, not on a substring of the prose — "longer" contains "lon".
BANNED_KEYS = {"lat", "lon", "coords", "bearing", "compass", "heading"}
for r in routes:
    leaked = BANNED_KEYS & set(r)
    if leaked:
        fails.append(f"a route leaked map fields: {sorted(leaked)}")
    for v in r.values():
        if isinstance(v, dict) and BANNED_KEYS & set(v):
            fails.append("a route nested map fields in a value")

# 4. Nowhere to nowhere, and here-to-here, give nothing rather than nonsense.
if m._routes_to(pc.slug, "Nowhere At All"): fails.append("routed to an unknown place")
here = m.world.location_of(pc.slug)
if m._routes_to(pc.slug, here.slug): fails.append("routed to where the PC already stands")

print("\nFAILS:", fails or "none")
sys.exit(1 if fails else 0)
