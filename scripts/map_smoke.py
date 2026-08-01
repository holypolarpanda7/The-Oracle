"""Maps: one terrain answer shared by the scene, the board and the parchment.

Offline end to end — fresh scratch DB, no GPU, no LLM. Covers the contract that
keeps the three map surfaces agreeing with the world graph, and the cartography
artifact's own rules (tool gating, knowledge gating, accrual across revisions).
"""
import importlib.util, os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))
db = os.path.join(tempfile.gettempdir(), "oracle_map_check.db")
for suffix in ("", "-wal", "-shm"):
    if os.path.exists(db + suffix): os.remove(db + suffix)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"
spec = importlib.util.spec_from_file_location(
    "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

from sqlmodel import Session, SQLModel, select
SQLModel.metadata.create_all(m.engine)

from eight_card_system import geo, mapmaker, placelore
from eight_card_system.models import Entity, EntityType, PlaceScale, RelationType
from eight_card_system.seed import seed_starter_world, place_pc
from vtt import archetype_for_place

seed_starter_world(m.world)

fails = []


def check(ok, msg):
    print(("  ok   " if ok else "  FAIL ") + msg)
    if not ok:
        fails.append(msg)


# ---------------------------------------------------------------- 1. the seam
print("\n1. one place, one terrain answer")
tankard = placelore.character_of(m.world, "the-silver-tankard")
duskwood = placelore.character_of(m.world, "duskwood")
hills = placelore.character_of(m.world, "the-west-hills")

check(duskwood.biome == "forest" and duskwood.terrain == "forest",
      f"open country reports its own biome ({duskwood.biome})")
# A tavern stands IN farmland/river country but is fought in on floorboards.
check(tankard.biome == "interior" and tankard.terrain in placelore._OUTDOOR,
      f"an interior separates surface from country "
      f"({tankard.biome} in {tankard.terrain})")
check(tankard.indoors and not duskwood.indoors,
      "indoors is known, so weather can stop at the door")
check("snow" not in tankard.board_look() and "winter" not in tankard.board_look(),
      "no weather indoors")
check(tankard.map_terrain() == placelore.terrain_words(tankard.terrain, "map"),
      "a map draws the COUNTRY a building stands in, not its floor")
check(tankard.context_key() == "indoors",
      "an interior's art bucket doesn't churn on the weather")

# The bug this module exists for: a place narration invents with no biome.
invented = m.world.create_entity(
    "The Hollow Barrow", EntityType.PLACE, subtype=PlaceScale.POI,
    attributes={"description": "a turf-covered mound with a black doorway"})
m.world.add_relation(invented.slug, RelationType.PART_OF, "duskwood")
inv_ch = placelore.character_of(m.world, invented.slug)
check(inv_ch.terrain == "forest" and inv_ch.biome_inherited,
      f"a place invented with no biome inherits its surroundings ({inv_ch.terrain})")
with Session(m.world.engine) as s:
    row = s.exec(select(Entity).where(Entity.slug == invented.slug)).first()
    check((row.attributes or {}).get("biome") == "forest",
          "the inherited terrain is persisted, so later renders can't disagree")

# Reading a stub must never promote it (upsert_entity defaults status=active).
placelore.character_of(m.world, "the-north-fields")
with Session(m.world.engine) as s:
    stub = s.exec(select(Entity).where(Entity.slug == "the-north-fields")).first()
    check(stub.status == "unexplored",
          "characterising a frontier stub doesn't mark it explored")

# ------------------------------------------------------- 2. scene <-> board
print("\n2. the scene and the board read the same source")
loc = m.world.get_entity("duskwood")
req = m._place_scene_request(loc)
check(req["look"] == duskwood.scene_look() and req["context"] == duskwood.context_key(),
      "the arrival scene renders from placelore, not from raw attributes")
check(placelore.terrain_words("forest", "scene") in req["look"],
      "the scene look actually carries the graph's terrain")
check(placelore.terrain_words("forest", "board") in m._vtt_place_look("duskwood"),
      "the battlemap floor carries that same terrain")

print("\n3. the board's LAYOUT follows the place, not just its name")
cases = [
    ("duskwood", "forest"), ("the-west-hills", "open"),
    ("millbrook", "street"), ("the-silver-tankard", "tavern"),
]
for slug, expect in cases:
    ch = placelore.character_of(m.world, slug)
    got = archetype_for_place(hint=ch.name, biome=ch.biome, scale=ch.scale)
    check(got == expect, f"{ch.name} -> {got} (biome {ch.biome})")
# A name with no keyword in it must fall through to the biome, not the default.
check(archetype_for_place(hint="The Grey Tors", biome="mountains") == "mountain-pass",
      "an unmatched place NAME falls through to the terrain")
# ...but the DM's own words still win.
check(archetype_for_place(hint="a smoky taproom", biome="mountains") == "tavern",
      "the DM's explicit language still outranks the terrain")

# ------------------------------------------------------------ 4. the survey
print("\n4. the drawn map's country comes from the graph")
center, places = mapmaker.gather_mappable_places(
    m.world, "millbrook", radius_mi=mapmaker.PURCHASE_RADIUS_MI, include_rumored=True)
check(bool(places) and all("biome" in p for p in places),
      f"every mapped site carries a biome ({len(places)} sites)")
pts = [{**p, **dict(zip(("x", "y"), mapmaker._project(center, p["coords"])))}
       for p in places]
reach = max([max(abs(p["x"]), abs(p["y"])) for p in pts] + [5.0])
survey = mapmaker.survey_terrain(pts, reach, center)
check(survey.sectors["east"] == "forest",
      f"Duskwood lies east, so the sheet's east is forest ({survey.sectors['east']})")
check(survey.sectors["west"] == "hills",
      f"the West Hills lie west ({survey.sectors['west']})")
check(survey.sectors["north"] == "farmland",
      f"the North Fields lie north ({survey.sectors['north']})")
look = survey.prompt_look()
check(placelore.terrain_words("forest", "map") in look,
      "the wash prompt uses the cartographic register, not the scene one")
check(survey.signature == mapmaker.survey_terrain(pts, reach, center).signature,
      "the survey signature is stable, so identical country reuses one render")

# A sheet with nothing known still gets country, from the climate band.
empty = mapmaker.survey_terrain([], 25.0, center)
check(all(empty.sectors.values()), "an ignorant sheet still has terrain to paint")

# ------------------------------------------------- 5. renders, with no GPU
print("\n5. the artifact never depends on the GPU")
png = mapmaker.render_map(places, center, title="Map of Greenfields",
                          seed="smoke:0:greenfields:1", paint_terrain=False)
check(png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 2000,
      f"an offline sheet is still a real PNG ({len(png)} bytes)")
flawed = mapmaker.render_map(places, center, title="Map of Greenfields",
                             seed="smoke:0:greenfields:1", flawed=True,
                             paint_terrain=False)
check(flawed != png, "a failed draft is a different drawing")
again = mapmaker.render_map(places, center, title="Map of Greenfields",
                            seed="smoke:0:greenfields:1", flawed=True,
                            paint_terrain=False)
check(again == flawed, "the same bad map is the same bad map every time")
# paint_terrain=True with no image backend must degrade, not raise.
degraded = mapmaker.render_map(places, center, title="Map of Greenfields",
                               seed="smoke:0:greenfields:1", paint_terrain=True,
                               store=object(), area="Greenfields")
check(degraded[:8] == b"\x89PNG\r\n\x1a\n",
      "a broken image backend yields bare parchment, not an exception")

# ------------------------------------------------------------- 6. accrual
print("\n6. a cartographer adds to a map over time")
with Session(m.engine) as s:
    ch = m.Character(discord_user_id="u1", name="Kara", race="Human",
                     char_class="Ranger", level=3, approved=True, max_hp=25,
                     current_hp=25, gp=100, stats={"wisdom": 14})
    s.add(ch); s.commit(); s.refresh(ch)
    char_id = ch.id
pc = place_pc(m.world, "Kara", discord_user_id="u1")
sid = "guild:chan"
m._set_session_meta(sid, {"pc_slug": pc.slug, "character_id": char_id,
                          "character_name": "Kara"})

clean, ops = m.extract_map_hooks("You sketch the valley.\n"
                                 "[[MAP: draft-success | Greenfields]]\nDone.")
check("[[" not in clean and ops == [{"action": "draft-success",
                                     "area": "Greenfields", "scale": ""}],
      f"the draft hook parses and is stripped ({ops})")
_, ops_u = m.extract_map_hooks("[[MAP: update-success | Greenfields]]")
check(ops_u == [{"action": "update-success", "area": "Greenfields", "scale": ""}],
      f"the update hook parses ({ops_u})")

# No tools -> no map, however well they rolled.
check(m.process_map_hooks([{"action": "draft-success", "area": "Greenfields"}], sid) == [],
      "drafting without Cartographer's Tools is refused")

with Session(m.engine) as s:
    ch = s.get(m.Character, char_id)
    m._add_inventory_item(ch, mapmaker.TOOLS_ITEM)
    s.add(ch); s.commit()

out = m.process_map_hooks([{"action": "draft-success", "area": "Greenfields"}], sid)
check(len(out) == 1 and out[0]["kind"] == "map", "with tools, a sheet is produced")

with Session(m.engine) as s:
    ch = s.get(m.Character, char_id)
    entry = next((i for i in m._inventory_items(ch) if isinstance(i.get("map"), dict)), None)
    check(entry is not None, "the map is in the pack")
    rec = entry["map"]
    check(rec["revision"] == 1 and rec["reliable"] is True, "rev 1, sound")
    charted = set(rec["places"])
    check(bool(charted), f"the sheet records what it charts ({sorted(charted)})")
    check("center" in rec, "the sheet remembers its own frame")

# You can only chart what you KNOW: Duskwood isn't on it yet.
check("duskwood" not in charted, "an unvisited place is not on the drafter's sheet")

# Walk to Duskwood, learn the Barrow, then revise the SAME sheet.
m.world.move_entity(pc.slug, "duskwood")
m.world.add_relation(pc.slug, RelationType.KNOWS_ABOUT, invented.slug)
out2 = m.process_map_hooks([{"action": "update-success", "area": "Greenfields"}], sid)
check(len(out2) == 1, "the revision renders")

with Session(m.engine) as s:
    ch = s.get(m.Character, char_id)
    maps = [i for i in m._inventory_items(ch) if isinstance(i.get("map"), dict)]
    check(len(maps) == 1, f"revising redraws ONE sheet, never a second ({len(maps)})")
    rec2 = maps[0]["map"]
    check(rec2["revision"] == 2, f"the revision counter advanced ({rec2['revision']})")
    grew = set(rec2["places"])
    check(charted <= grew, "a revision never LOSES country the sheet already had")
    check("duskwood" in grew, "newly-walked country is added")
    check(rec2["day"] == rec["day"] and rec2["updated_day"] >= rec["day"],
          "the sheet keeps its original date and records the revision date")

# A bad hand spoils a sound sheet; a good one repairs it.
m.process_map_hooks([{"action": "update-failure", "area": "Greenfields"}], sid)
with Session(m.engine) as s:
    ch = s.get(m.Character, char_id)
    rec3 = next(i["map"] for i in m._inventory_items(ch) if isinstance(i.get("map"), dict))
    check(rec3["reliable"] is False, "a failed revision spoils the sheet")
    check(set(rec3["places"]) >= grew, "...but still keeps everything it charted")
m.process_map_hooks([{"action": "update-success", "area": "Greenfields"}], sid)
with Session(m.engine) as s:
    ch = s.get(m.Character, char_id)
    rec4 = next(i["map"] for i in m._inventory_items(ch) if isinstance(i.get("map"), dict))
    check(rec4["reliable"] is True, "a good revision corrects an earlier bad draft")
    check(rec4["revision"] == 4, f"revisions keep counting ({rec4['revision']})")

# Updating a map you don't own does nothing at all.
with Session(m.engine) as s:
    ch2 = m.Character(discord_user_id="u2", name="Bram", race="Dwarf",
                      char_class="Cleric", level=2, approved=True, max_hp=18,
                      current_hp=18, stats={"wisdom": 12})
    s.add(ch2); s.commit(); s.refresh(ch2)
    bram_id = ch2.id
    m._add_inventory_item(ch2, mapmaker.TOOLS_ITEM)
    s.add(ch2); s.commit()
pc2 = place_pc(m.world, "Bram", discord_user_id="u2")
m._set_session_meta("guild:other", {"pc_slug": pc2.slug, "character_id": bram_id,
                                    "character_name": "Bram"})
check(m.process_map_hooks([{"action": "update-success", "area": "Greenfields"}],
                          "guild:other") == [],
      "you cannot revise a map you do not have")

# ------------------------------------------------------ 7. granularity
print("\n7. a sheet holds only what it can carry")
check(mapmaker.prominence_of({"scale": "city"}) >
      mapmaker.prominence_of({"scale": "village"}) >
      mapmaker.prominence_of({"scale": "building"}),
      "prominence ranks the world's furniture")
check(mapmaker.prominence_of({"scale": "poi", "prominence": 5}) == 5,
      "a famous ruin can outrank its own size")
check(mapmaker.map_scale("known world").name == "world"
      and mapmaker.map_scale("").name == "local"
      and mapmaker.map_scale("nonsense").name == "local",
      "scale names resolve, and anything odd falls back to local")

# Build a crowded world: many hamlets, a few towns, one city, one region.
from eight_card_system import census  # noqa: E402  (kept near its use)
crowd = []
for i in range(40):
    crowd.append({"name": f"Hamlet {i}", "slug": f"hamlet-{i}",
                  "coords": geo.from_origin("north", 3 + i * 4),
                  "scale": "village", "rumored": False, "biome": "farmland"})
for i in range(4):
    crowd.append({"name": f"Town {i}", "slug": f"town-{i}",
                  "coords": geo.from_origin("east", 20 + i * 40),
                  "scale": "town", "rumored": False, "biome": "hills"})
crowd.append({"name": "Highhold", "slug": "highhold",
              "coords": geo.from_origin("west", 120),
              "scale": "city", "rumored": False, "biome": "hills"})
crowd.append({"name": "The Amber Vales", "slug": "amber-vales",
              "coords": geo.from_origin("south", 200),
              "scale": "region", "rumored": False, "biome": "farmland"})
crowd.append({"name": "The Weeping Stone", "slug": "weeping-stone",
              "coords": geo.from_origin("northeast", 90), "scale": "poi",
              "prominence": 5, "rumored": False, "biome": "hills"})
origin = (geo.ORIGIN_LAT, geo.ORIGIN_LON)

local = mapmaker.select_features(crowd, origin, mapmaker.map_scale("local"))
world_sheet = mapmaker.select_features(crowd, origin, mapmaker.map_scale("world"))
check(len(local) <= mapmaker.map_scale("local").feature_cap,
      f"a local sheet stays under its cap ({len(local)} features)")
check(len(world_sheet) <= mapmaker.map_scale("world").feature_cap,
      f"a world sheet stays under its cap ({len(world_sheet)} features)")
check(len(world_sheet) < len(crowd),
      "a world sheet is not simply everything")
check(not any(p["slug"] == "highhold" for p in local),
      "a sheet of one valley doesn't show a city 120 miles off")
check(all(geo.distance_mi(origin, p["coords"]) <= mapmaker.map_scale("local").radius_mi
          for p in local),
      "everything on a local sheet is genuinely local")
kinds = {p["scale"] for p in world_sheet}
check("village" not in kinds,
      f"zooming out drops hamlets rather than shrinking them ({sorted(kinds)})")
check(any(p["slug"] == "highhold" for p in world_sheet),
      "...but keeps the city")
check(any(p["slug"] == "weeping-stone" for p in world_sheet),
      "...and a renowned landmark, whatever its size")
check(all(p["scale"] != "region" or True for p in world_sheet)
      and any(p["slug"] == "amber-vales" for p in world_sheet),
      "...and neighbouring regions")

# The ruler has to be readable at both extremes.
check(mapmaker._scale_bar_miles(20) <= 10 < mapmaker._scale_bar_miles(3000),
      "the scale bar adapts instead of drawing 10 miles across a continent")

big = mapmaker.render_map(crowd, origin, title="The Known World",
                          seed="smoke:world", paint_terrain=False,
                          scale=mapmaker.map_scale("world"))
check(big[:8] == b"\x89PNG\r\n\x1a\n", "a world sheet renders")

# ------------------------------------------------------ 8. purposed maps
print("\n8. a found map is FOR something")
goal = {"name": "The Drowned Vault", "slug": "drowned-vault",
        "coords": geo.from_origin("east", 55), "scale": "dungeon",
        "rumored": False, "biome": "swamp"}
feats = mapmaker.treasure_features(crowd + [goal], origin, goal)
check(len(feats) == 1 + mapmaker.TREASURE_LANDMARKS,
      f"a chart carries its goal and a few landmarks ({len(feats)})")
check(feats[0]["mark"] == "goal" and feats[0]["slug"] == "drowned-vault",
      "the goal is the marked feature")
check(sum(1 for p in feats if p.get("mark") == "goal") == 1,
      "exactly one cross")
# The landmarks must be USEFUL — on the way east, not the city 120 mi west.
lands = {p["slug"] for p in feats[1:]}
check("highhold" not in lands,
      f"a prominent city in the wrong direction is not a landmark ({sorted(lands)})")
check(any(s.startswith("town-") for s in lands),
      "...but places along the way are")

# The cap must never eat the cross.
tiny = mapmaker.select_features(crowd + [{**goal, "mark": "goal"}], origin,
                                mapmaker.map_scale("world"))
check(any(p.get("mark") == "goal" for p in tiny),
      "a humble goal survives even a world sheet's cut")

chart = mapmaker.render_map(feats, origin, title="Map to The Drowned Vault",
                            seed="smoke:treasure", paint_terrain=False,
                            purpose=mapmaker.PURPOSE_TREASURE)
check(chart[:8] == b"\x89PNG\r\n\x1a\n", "a treasure chart renders")

# End to end through the hook, including the scale field.
_, t_ops = m.extract_map_hooks("[[MAP: treasure | a smuggler's chart | Duskwood]]")
check(t_ops == [{"action": "treasure", "area": "a smuggler's chart",
                 "goal": "Duskwood"}], f"the treasure hook parses ({t_ops})")
_, s_ops = m.extract_map_hooks("[[MAP: draft-success | my travels | world]]")
check(s_ops == [{"action": "draft-success", "area": "my travels",
                 "scale": "world"}], f"the scale field parses ({s_ops})")

out_t = m.process_map_hooks(
    [{"action": "treasure", "area": "a smuggler's chart", "goal": "Duskwood"}], sid)
check(len(out_t) == 1 and out_t[0]["caption"] == "a smuggler's chart",
      f"the chart is handed over under its own name ({out_t and out_t[0]['caption']})")
with Session(m.engine) as s:
    ch = s.get(m.Character, char_id)
    tm = next((i["map"] for i in m._inventory_items(ch)
               if isinstance(i.get("map"), dict)
               and i["map"].get("purpose") == "treasure"), None)
    check(tm is not None, "the chart is in the pack, marked as purposed")
    if tm:
        check(tm["provenance"] == "found" and "duskwood" in tm["places"],
              "it records what it leads to")
        check(len(tm["places"]) <= 1 + mapmaker.TREASURE_LANDMARKS,
              f"and stays small ({len(tm['places'])} sites)")

# A goal with no position cannot be drawn rather than being invented somewhere.
check(m._treasure_goal("A Place That Does Not Exist") is None,
      "a chart to nowhere is refused")

# A revision keeps the sheet's own grain.
with Session(m.engine) as s:
    ch = s.get(m.Character, char_id)
    surv = next(i for i in m._inventory_items(ch)
                if isinstance(i.get("map"), dict)
                and i["map"].get("purpose") == "survey")
    check(surv["map"].get("scale") == "local",
          f"the survey sheet recorded its scale ({surv['map'].get('scale')})")

print("\nFAILS:", fails or "none")
sys.exit(1 if fails else 0)
