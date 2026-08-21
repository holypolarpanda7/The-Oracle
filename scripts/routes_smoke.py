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

# 5. ONE terrain vocabulary. `placelore` decides what country a place is in —
#    the closed set the scene art, the battlemap floor and the drawn map all
#    read — and `survival.travel` keeps its own, older, half-overlapping set.
#    `TERRAIN.get(name, TERRAIN["grassland"])` never complains about a word it
#    has not got, so farmland, river, coast, SEA, underdark, dungeon and
#    interior were each costed as a stroll over a meadow. This is the check
#    that fails when the two drift apart again.
from eight_card_system.placelore import RELIEF, _TERRAIN, relief_of, travel_terrain
from survival.travel import TERRAIN as TRAVEL_TERRAIN, travel as _tv

missing = sorted(set(_TERRAIN) - set(RELIEF))
if missing:
    fails.append(f"terrain with no relief: {missing}")
strays = sorted(set(RELIEF) - set(_TERRAIN))
if strays:
    fails.append(f"relief for a terrain that does not exist: {strays}")
unknown = sorted(t for t in RELIEF if travel_terrain(t) not in TRAVEL_TERRAIN)
if unknown:
    fails.append(f"terrain travel.TERRAIN has never heard of: {unknown}")
# ...and the mapping has to MEAN something: rough country must cost more than
# easy country, or naming it changed nothing.
easy = _tv(60, terrain=travel_terrain("farmland"))["days"]
hard = _tv(60, terrain=travel_terrain("mountains"))["days"]
if not hard > easy:
    fails.append(f"mountains cost no more than farmland ({hard} vs {easy} days)")
if _tv(60, terrain=travel_terrain("sea"))["days"] <= 0:
    fails.append("a sea crossing takes no time")
# Every country says how its ground lies, for the boards and the cartographer.
for t in RELIEF:
    r = relief_of(t)
    lo, hi = r["fall_ft"]
    if lo > hi or hi < 0:
        fails.append(f"{t} has a nonsense fall {r['fall_ft']}")
    if t not in ("interior",) and not r["map_words"]:
        fails.append(f"{t} tells the cartographer nothing about its relief")

print("\nFAILS:", fails or "none")
sys.exit(1 if fails else 0)
