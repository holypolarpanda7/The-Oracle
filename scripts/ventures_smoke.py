"""Prove NPC ventures: an NPC steps out of their role, the world moves it while
nobody watches, a party can walk it and stop, and the ending marks the world.

Offline: fresh scratch DB, no GPU, no LLM. Drives the real backend module so the
hooks, the prompt block and the Chronicle are checked as wired, not in theory.
"""
from __future__ import annotations
import importlib.util, os, sys, tempfile
from pathlib import Path

ROOT = Path("/mnt/d/Projects/The Oracle")
sys.path.insert(0, str(ROOT))

db = os.path.join(tempfile.gettempdir(), "oracle_ventures_check.db")
if os.path.exists(db):
    os.remove(db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"

spec = importlib.util.spec_from_file_location(
    "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

from sqlmodel import Session, SQLModel, select
SQLModel.metadata.create_all(m.engine)

from eight_card_system import ventures as V
from eight_card_system import census, entropy
from eight_card_system.models import (Entity, EntityType, PlaceScale, QuestState,
                                      QuestTier, Relation, RelationType)
from eight_card_system.seed import seed_minimal_world, place_pc

W = m.world
seed_minimal_world(W)
fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        fails.append(name)


# ---------------------------------------------------------------------------
# 0. a world with people in it, and a PC standing among them
# ---------------------------------------------------------------------------
session_id, user_id = "vent:table", "vent-user"
with Session(m.engine) as s:
    char = m.Character(discord_user_id=user_id, name="Wren", race="Human",
                       char_class="Ranger", level=3, approved=True,
                       max_hp=28, current_hp=28, home_region="Greenfields",
                       stats={"strength": 12, "dexterity": 16, "constitution": 14,
                              "intelligence": 10, "wisdom": 14, "charisma": 10})
    s.add(char); s.commit(); s.refresh(char)
    char_id = char.id
place_pc(W, "Wren", discord_user_id=user_id)
m._set_session_meta(session_id, {
    "user_id": user_id, "character_id": char_id, "character_name": "Wren",
    "pc_slug": "wren",
    "members": {user_id: {"character_id": char_id, "character_name": "Wren",
                          "pc_slug": "wren"}},
})
pc = W.find_pc(user_id, "Wren")

# A wilderness for a venture to go OUT to, and a town full of trades.
wilds = W.upsert_entity("The Hollow Barrows", EntityType.PLACE,
                        subtype=PlaceScale.WILDS,
                        attributes={"scale": "wilds", "biome": "hills",
                                    "danger": "moderate",
                                    "denizens": ["wolf", "bandit"]},
                        tags=["wilds"])
W.add_relation("millbrook", RelationType.ADJACENT_TO, wilds)
census.flesh_settlement(W, "millbrook")
# The party has been here — the emergence filter needs somebody who can HEAR.
W.upsert_entity("Millbrook", EntityType.PLACE, slug="millbrook",
                attributes={"last_visited_day": W.current_day()})

smith = W.create_entity("Halda Ironmonger", EntityType.NPC,
                        attributes={"role": "blacksmith", "level": 2,
                                    "description": "Millbrook's smith."},
                        tags=["npc"])
W.add_relation(smith, RelationType.LOCATED_IN, "millbrook")

check("family_for reads a trade",
      V.family_for("blacksmith") == "craft"
      and V.family_for("guard sergeant") == "martial"
      and V.family_for("rat-catcher") == "underworld"
      and V.family_for("wandering nobody") == "common",
      f"{V.family_for('rat-catcher')}")

# ---------------------------------------------------------------------------
# 1. a venture opens, with real stages at real places
# ---------------------------------------------------------------------------
got = V.open_venture(W, smith.slug, depth=3, seed="smoke:halda")
check("open_venture returns a venture", bool(got))
q = V.venture_of(W, smith.slug)
a = (q.attributes or {}) if q else {}
check("it is a QUEST entity of tier venture",
      q is not None and q.type == EntityType.QUEST
      and a.get("tier") == QuestTier.VENTURE and V.is_venture(q))
check("depth 3 gives three stages ending in the climax",
      len(a.get("stages") or []) == 3, f"{len(a.get('stages') or [])}")
check("stage text names REAL places, not a placeholder",
      all("{" not in st["text"] for st in a["stages"])
      and any("Millbrook" in st["text"] or "Hollow Barrows" in st["text"]
              for st in a["stages"]),
      "; ".join(st["text"] for st in a["stages"]))
check("DCs rise toward the climax",
      [st["dc"] for st in a["stages"]] == sorted(st["dc"] for st in a["stages"]),
      str([st["dc"] for st in a["stages"]]))
# `away` must be somewhere WILD next door — an errand that ends in the
# neighbouring market square is not a venture. (Which wild place is the code's
# to choose; the starter world offers several.)
away_ent = W.get_entity(a.get("away_slug") or "")
away_attrs = (away_ent.attributes or {}) if away_ent is not None else {}
check("the climax is anchored on wild country away from home",
      a.get("home_slug") == "millbrook" and away_ent is not None
      and away_ent.slug != "millbrook"
      and (away_ent.status == "unexplored" or away_attrs.get("stub")
           or str(away_attrs.get("danger", "")).lower()
           in ("moderate", "high", "deadly")),
      f"{a.get('home_slug')} -> {a.get('away_slug')} ({away_ent.status if away_ent else '?'})")
check("nobody runs two errands at once",
      V.open_venture(W, smith.slug) is None)

# ---------------------------------------------------------------------------
# 2. entropy's main-cast protection covers a venturer for free
# ---------------------------------------------------------------------------
with Session(W.engine) as s:
    npc_row = s.exec(select(Entity).where(Entity.slug == smith.slug)).first()
    protected = entropy._is_protected(s, npc_row, set())
check("a venturer is protected from being aged out mid-errand", protected)

# ---------------------------------------------------------------------------
# 3. it moves while NOBODY is watching, on the world clock
# ---------------------------------------------------------------------------
start_stage = int(a.get("stage", 0))
W.ratchet_day(W.current_day() + V.STEP_DAYS + 1)
res = V.advance_ventures(W, W.current_day())
check("an unwatched venture takes a step on the clock",
      res["stepped"] >= 1, str(res))
after = (V.venture_of(W, smith.slug) or q).attributes or {}
check("the step either advanced it or cost it a setback",
      int(after.get("stage", 0)) > start_stage or int(after.get("setbacks", 0)) > 0,
      f"stage {after.get('stage')} setbacks {after.get('setbacks')}")

# ... and not twice inside one interval.
before = dict((V.venture_of(W, smith.slug) or q).attributes or {})
again = V.advance_ventures(W, W.current_day())
check("the stage clock rations it", again["stepped"] == 0, str(again))

# ---------------------------------------------------------------------------
# 4. walking with them: the party joins, and the world stops rolling it
# ---------------------------------------------------------------------------
joined = V.accompany(W, pc.slug, smith.slug)
check("a PC can accompany a venture", bool(joined))
check("accompanying is NOT recruitment",
      not W.list_companions(pc.slug), str(W.list_companions(pc.slug)))
rows = V.accompanying(W, pc.slug)
check("accompanying() reports the goal and the current step",
      len(rows) == 1 and rows[0]["npc_slug"] == smith.slug
      and rows[0]["goal"] and rows[0]["stage"] is not None)
check("followers() sees it from the NPC's side",
      [e.slug for e in V.followers(W, smith.slug)] == [pc.slug])

W.ratchet_day(W.current_day() + V.STEP_DAYS + 1)
held = dict((V.venture_of(W, smith.slug) or q).attributes or {})
res2 = V.advance_ventures(W, W.current_day())
now = dict((V.venture_of(W, smith.slug) or q).attributes or {})
check("an ACCOMPANIED venture is never resolved behind the party's back",
      res2["stepped"] == 0
      and now.get("stage") == held.get("stage")
      and now.get("setbacks") == held.get("setbacks"), str(res2))

# ---------------------------------------------------------------------------
# 5. the DM's block says who leads, and the hooks move a step at the table
# ---------------------------------------------------------------------------
ctx = W.get_world_context(pc.slug, "")
block = V.render_block(W, ctx.entities, pc.slug)
check("the DM block names the venturer and their step",
      "Halda Ironmonger" in block and "TRAVELLING WITH" in block
      and "step " in block, block[:160])
check("the block says outright they are not companions",
      "NOT" in block and "companions" in block)

clean, ops = m.extract_venture_hooks(
    "The hammer rings out.\n[[VENTURE: step | Halda Ironmonger | success | "
    "the party held the door]]")
check("the venture hook parses and leaves the prose clean",
      ops and ops[0]["action"] == "step" and ops[0]["npc"] == "Halda Ironmonger"
      and "[[" not in clean, f"{ops} / {clean!r}")

stage_before = int((V.venture_of(W, smith.slug).attributes or {}).get("stage", 0))
notes = m.apply_venture_hooks(session_id, ops, pc_slug=pc.slug)
stage_after = int((V.venture_of(W, smith.slug).attributes or {}).get("stage", 0))
check("a settled step advances it at the table",
      stage_after == stage_before + 1, f"{stage_before} -> {stage_after}")
check("and the players are told", bool(notes), str(notes))

# ---------------------------------------------------------------------------
# 6. stopping at any time — and it carries on without them
# ---------------------------------------------------------------------------
check("a PC can stop following", V.part_ways(W, pc.slug, smith.slug))
check("the venture is still alive after they walk away",
      V.venture_of(W, smith.slug) is not None)
check("and they are no longer on it", V.accompanying(W, pc.slug) == [])
W.ratchet_day(W.current_day() + V.STEP_DAYS + 1)
res3 = V.advance_ventures(W, W.current_day())
check("an abandoned-by-the-party venture rolls again on its own",
      res3["stepped"] >= 1, str(res3))

# ---------------------------------------------------------------------------
# 7. it ENDS, and the ending marks the world
# ---------------------------------------------------------------------------
# A `safer` venture, driven to a success, must make its place safer.
guard = W.create_entity("Otho Pyke", EntityType.NPC,
                        attributes={"role": "guard captain", "level": 4},
                        tags=["npc"])
W.add_relation(guard, RelationType.LOCATED_IN, "millbrook")
V.open_venture(W, guard.slug, depth=1, goal="hunt down the raiders")
gq = V.venture_of(W, guard.slug)
W.upsert_entity(gq.name, EntityType.QUEST, slug=gq.slug,
                attributes={"outcome_kind": "safer", "location_slug": wilds.slug})
danger_before = (W.get_entity(wilds.slug).attributes or {}).get("danger")
V.accompany(W, pc.slug, guard.slug)
out = V.resolve_venture(W, guard.slug, True, note="the band broke and ran")
danger_after = (W.get_entity(wilds.slug).attributes or {}).get("danger")
check("a `safer` success actually calms the place",
      danger_before == "moderate" and danger_after == "low",
      f"{danger_before} -> {danger_after}")
check("resolving reports who walked it",
      out and out["walked_with"] == ["Wren"], str(out))
check("walking it to a good end earns real standing",
      (W.get_trust(guard.slug, pc.slug) or 0) >= V.TRUST_ON_SUCCESS,
      str(W.get_trust(guard.slug, pc.slug)))
check("a resolved venture releases everyone who was on it",
      V.accompanying(W, pc.slug) == [] and V.venture_of(W, guard.slug) is None)

# A `standing` venture changes who the NPC IS — the role every other system reads.
clerk = W.create_entity("Ida Wold", EntityType.NPC,
                        attributes={"role": "clerk"}, tags=["npc"])
W.add_relation(clerk, RelationType.LOCATED_IN, "millbrook")
V.open_venture(W, clerk.slug, depth=1)
cq = V.venture_of(W, clerk.slug)
W.upsert_entity(cq.name, EntityType.QUEST, slug=cq.slug,
                attributes={"outcome_kind": "standing", "family": "learned"})
V.resolve_venture(W, clerk.slug, True)
check("a `standing` success promotes them for good",
      (W.get_entity(clerk.slug).attributes or {}).get("role") == "magistrate",
      str((W.get_entity(clerk.slug).attributes or {}).get("role")))

# A `peril` venture can cost the venturer their life, and the post gets filled.
scout = W.create_entity("Kell Dunmoor", EntityType.NPC,
                        attributes={"role": "shepherd"}, tags=["npc"])
W.add_relation(scout, RelationType.LOCATED_IN, "millbrook")
V.open_venture(W, scout.slug, depth=1)
sq = V.venture_of(W, scout.slug)
W.upsert_entity(sq.name, EntityType.QUEST, slug=sq.slug,
                attributes={"outcome_kind": "peril"})
V.resolve_venture(W, scout.slug, False, note="the barrows kept them")
check("a `peril` failure claims the venturer",
      W.get_entity(scout.slug).status == "dead",
      W.get_entity(scout.slug).status)
with Session(W.engine) as s:
    heirs = [e for e in s.exec(select(Entity).where(Entity.type == EntityType.NPC)).all()
             if "Took over after Kell Dunmoor" in
             str((e.attributes or {}).get("description", ""))]
check("and somebody takes the post they left empty", len(heirs) == 1,
      str([e.name for e in heirs]))

# A `relic` success puts a real thing in their hands.
sage = W.create_entity("Vess Harrow", EntityType.NPC,
                       attributes={"role": "sage"}, tags=["npc"])
W.add_relation(sage, RelationType.LOCATED_IN, "millbrook")
V.open_venture(W, sage.slug, depth=1)
rq = V.venture_of(W, sage.slug)
W.upsert_entity(rq.name, EntityType.QUEST, slug=rq.slug,
                attributes={"outcome_kind": "relic", "family": "learned",
                            "away_slug": wilds.slug})
V.resolve_venture(W, sage.slug, True)
with Session(W.engine) as s:
    owned = [s.get(Entity, r.dst_id) for r in s.exec(select(Relation).where(
        Relation.rel_type == RelationType.OWNS, Relation.valid_to == None)).all()]  # noqa: E711
    owned = [e for e in owned if e is not None and (e.attributes or {}).get("won_by") == sage.slug]
check("a `relic` success creates a real item they own", len(owned) == 1,
      str([e.name for e in owned]))

# The venturer comes HOME. Without this they stand in the wilds forever: the
# town loses its trade, and the pool of people who could ever set out drains.
drover = W.create_entity("Harl Reedmoor", EntityType.NPC,
                         attributes={"role": "carter"}, tags=["npc"])
W.add_relation(drover, RelationType.LOCATED_IN, "millbrook")
V.open_venture(W, drover.slug, depth=1)
dq = V.venture_of(W, drover.slug)
W.move_entity(drover.slug, (dq.attributes or {}).get("away_slug") or wilds.slug)
V.resolve_venture(W, drover.slug, True)
check("a surviving venturer comes home when it is over",
      (W.location_of(drover.slug) or wilds).slug == "millbrook",
      (W.location_of(drover.slug) or wilds).slug)
check("and waits a season before setting out again",
      drover.slug not in {e.slug for e in V._candidates(W, W.current_day())})

# A person may set out twice in a long life, and the second time is a NEW
# thread — the record of the first must survive it.
V.open_venture(W, drover.slug, depth=1, seed="second-run")
second = V.venture_of(W, drover.slug)
check("a second venture is a separate quest row",
      second is not None and second.slug != dq.slug,
      f"{dq.slug} vs {second.slug if second else None}")
check("and the first one stays resolved",
      (W.get_entity(dq.slug).attributes or {}).get("state") == QuestState.COMPLETED)
V.abandon_venture(W, drover.slug, reason="thought better of it")
check("abandoning ends it with no world mutation",
      V.venture_of(W, drover.slug) is None
      and (W.get_entity(second.slug).attributes or {}).get("abandoned") is True)

check("depth decides how much may go wrong",
      V.setback_limit({"depth": 1}) < V.setback_limit({"depth": 3}),
      f"{V.setback_limit({'depth': 1})} vs {V.setback_limit({'depth': 3})}")

# ---------------------------------------------------------------------------
# 7b. working AGAINST one
# ---------------------------------------------------------------------------
rival = W.create_entity("Salla Pyke", EntityType.NPC,
                        attributes={"role": "merchant"}, tags=["npc"])
W.add_relation(rival, RelationType.LOCATED_IN, "millbrook")
V.open_venture(W, rival.slug, depth=3)
rv = V.venture_of(W, rival.slug)
plain_dc = int((V.current_stage(rv.attributes or {}) or {})["dc"])
check("with nobody against them a step costs what the world asks",
      V.effective_dc(W, rival.slug, V.current_stage(rv.attributes or {})) == plain_dc)

stance = V.oppose(W, pc.slug, rival.slug, reason="the road is ours", covert=False)
check("a PC can set themselves against a venture", bool(stance))
check("opposition lifts the bar on every step",
      V.effective_dc(W, rival.slug, V.current_stage(rv.attributes or {}))
      == plain_dc + V.OPPOSED_DC)
check("and it is NOT written into the stored stage",
      int((V.current_stage(V.venture_of(W, rival.slug).attributes or {}) or {})["dc"])
      == plain_dc)
check("opposing() reports it from the party's side",
      [r["npc_slug"] for r in V.opposing(W, pc.slug)] == [rival.slug])

# Unlike accompanying, opposition does NOT pause the world — sabotage is a
# thing you do and walk away from.
W.ratchet_day(W.current_day() + V.STEP_DAYS + 1)
res_op = V.advance_ventures(W, W.current_day())
check("an OPPOSED venture keeps rolling on the clock",
      res_op["stepped"] >= 1, str(res_op))

# One concrete act of sabotage costs them a step and leaves a trail.
before_sb = int((V.venture_of(W, rival.slug).attributes or {}).get("setbacks", 0))
V.hinder_venture(W, rival.slug, note="their carts were cut loose in the night",
                 by=pc.slug)
rva = V.venture_of(W, rival.slug).attributes or {}
check("a hindrance costs a setback", int(rva.get("setbacks", 0)) == before_sb + 1)
check("and is counted, because it is what gets traced back",
      int(rva.get("hindrances", 0)) == 1)

check("relenting gives them their own DC back",
      V.relent(W, pc.slug, rival.slug)
      and V.effective_dc(W, rival.slug, V.current_stage(rva)) == int(
          V.current_stage(rva)["dc"]))

# THWART: the race, not a roll. The goal is gone, and the world still pays for
# the failure — sabotage has a price somebody bears.
racer = W.create_entity("Colm Tanner", EntityType.NPC,
                        attributes={"role": "guard captain", "level": 3},
                        tags=["npc"])
W.add_relation(racer, RelationType.LOCATED_IN, "millbrook")
V.open_venture(W, racer.slug, depth=2)
raq = V.venture_of(W, racer.slug)
W.upsert_entity(raq.name, EntityType.QUEST, slug=raq.slug,
                attributes={"outcome_kind": "safer", "location_slug": wilds.slug})
W.upsert_entity("The Hollow Barrows", EntityType.PLACE, slug=wilds.slug,
                attributes={"danger": "moderate"})
out_t = V.thwart_venture(W, racer.slug, reason="the party took the bounty first",
                         by=pc.slug)
check("thwarting ends the venture as a failure",
      out_t and out_t["state"] == QuestState.FAILED, str(out_t and out_t["state"]))
check("and the world still pays for the failure",
      (W.get_entity(wilds.slug).attributes or {}).get("danger") == "high",
      str((W.get_entity(wilds.slug).attributes or {}).get("danger")))
check("the thwarter is recorded",
      (W.get_entity(raq.slug).attributes or {}).get("thwarted_by") == pc.slug)

# Discovery: a covert hand is either traced or it is not, and a betrayal —
# working against somebody you are walking beside — is named as one.
turncoat = W.create_entity("Ysolde Fenwick", EntityType.NPC,
                           attributes={"role": "priest"}, tags=["npc"])
W.add_relation(turncoat, RelationType.LOCATED_IN, "millbrook")
V.open_venture(W, turncoat.slug, depth=1)
V.accompany(W, pc.slug, turncoat.slug)
V.oppose(W, pc.slug, turncoat.slug, reason="their god is not ours", covert=True)
# Interfere hard, so discovery is near-certain rather than a coin flip.
tcq = V.venture_of(W, turncoat.slug)
W.upsert_entity(tcq.name, EntityType.QUEST, slug=tcq.slug,
                attributes={"hindrances": 5})
check("a saboteur may also be walking beside them",
      len(V.followers(W, turncoat.slug)) == 1
      and len(V.opponents(W, turncoat.slug)) == 1)
out_b = V.resolve_venture(W, turncoat.slug, False, note="it came to nothing")
sent = None
try:
    from sqlmodel import select as _sel
    with Session(W.engine) as s:
        npc_row = s.exec(_sel(Entity).where(Entity.slug == turncoat.slug)).first()
        pc_row = s.exec(_sel(Entity).where(Entity.slug == pc.slug)).first()
        edge = s.exec(_sel(Relation).where(Relation.src_id == npc_row.id,
                                           Relation.dst_id == pc_row.id,
                                           Relation.valid_to == None)).first()  # noqa: E711
        sent = (edge.attributes or {}).get("sentiment") if edge else None
        ledger = (edge.attributes or {}).get("ledger") if edge else []
except Exception as e:
    ledger = []
    print(e)
check("a heavily-interfered covert hand is traced",
      out_b and out_b.get("exposed") == ["Wren"], str(out_b and out_b.get("exposed")))
check("and working against somebody you walk beside is BETRAYAL",
      any(x.get("g") == "betrayal" for x in (ledger or [])), str(ledger))
check("an exposed saboteur earns no gratitude for the company",
      (sent or 0) < 0, str(sent))
check("the opposition edge closes with the venture",
      V.opposing(W, pc.slug) == [])

# ---------------------------------------------------------------------------
# 8. the world does this on its OWN account
# ---------------------------------------------------------------------------
# Birth is gated per PASS, not per candidate, so a single pass may rightly
# produce nothing — walk a season of them and require that the world acted.
born: list[dict] = []
for _ in range(20):
    W.ratchet_day(W.current_day() + V.STEP_DAYS)
    born += V.spawn_ventures(W, W.current_day())
check("the world starts ventures nobody asked for", len(born) >= 1,
      "; ".join(f"{b['npc']}: {b['goal']}" for b in born))
check("and rations them — a season of passes is not a stampede",
      len(born) <= 8, str(len(born)))
check("ventures are rationed", len(V.live_ventures(W)) <= V.MAX_LIVE,
      str(len(V.live_ventures(W))))

# An NPC nobody could ever hear about is not rolled at all.
far = W.upsert_entity("Farhold", EntityType.PLACE, subtype=PlaceScale.SETTLEMENT,
                      attributes={"scale": "village"}, tags=["village"])
stranger = W.create_entity("Anka Fenwick", EntityType.NPC,
                           attributes={"role": "miller"}, tags=["npc"])
W.add_relation(stranger, RelationType.LOCATED_IN, far)
cands = {e.slug for e in V._candidates(W, W.current_day())}
check("a stranger in a town nobody has entered is never given a venture",
      stranger.slug not in cands)

# ---------------------------------------------------------------------------
# 9. the party's own quest clock never touches somebody else's errand
# ---------------------------------------------------------------------------
tinker = W.create_entity("Nock Brackwater", EntityType.NPC,
                         attributes={"role": "cooper"}, tags=["npc"])
W.add_relation(tinker, RelationType.LOCATED_IN, "millbrook")
V.open_venture(W, tinker.slug, depth=2)
tq = V.venture_of(W, tinker.slug)
W.upsert_entity(tq.name, EntityType.QUEST, slug=tq.slug,
                attributes={"stakes": "the barrels rot", "last_touched_day": 0})
W.ratchet_day(W.current_day() + entropy.STAKES_PERIOD_DAYS * 4)
qc = world_clock = entropy.advance_quest_clocks(W)
live_after = V.venture_of(W, tinker.slug)
check("the party stakes clock leaves a venture alone",
      live_after is not None
      and int((live_after.attributes or {}).get("stakes_level", 0)) == 0,
      str(qc))

# ---------------------------------------------------------------------------
# 10. the Chronicle carries it, marked as somebody else's
# ---------------------------------------------------------------------------
V.accompany(W, pc.slug, tinker.slug)
# The stakes-clock check above deliberately backdated this row to day 0; the
# journal keeps only the most recently touched dozen, so put it back in view.
W.upsert_entity(tq.name, EntityType.QUEST, slug=tq.slug,
                attributes={"last_touched_day": W.current_day()})
journal = m._activity_journal(session_id, user_id)
vrows = journal.get("ventures") or []
mine = [r for r in vrows if r["owner_slug"] == tinker.slug]
check("the Chronicle lists ventures separately from the party's quests",
      bool(vrows) and all(not any(qq["name"] == v["name"]
                                  for qq in journal.get("quests") or [])
                          for v in vrows))
check("a venture row names its owner, its goal and how far along it is",
      mine and mine[0]["owner"] == "Nock Brackwater" and mine[0]["goal"]
      and mine[0]["steps"] >= 1, str(mine[:1]))
check("and marks the one the party is actually walking",
      mine and mine[0]["with_you"] is True, str(mine[:1]))

# ---------------------------------------------------------------------------
# 11. catching up on a thread you left
# ---------------------------------------------------------------------------
V.part_ways(W, pc.slug, tinker.slug)
day_left = W.current_day()
V.resolve_venture(W, tinker.slug, True, note="the barrels went out on time")
lines = V.catch_up_lines(W, pc.slug, day_left - 1)
check("a PC who walked away is told how it ended",
      any("Nock Brackwater" in ln for ln in lines), str(lines))

print()
print(f"{len(fails)} failure(s)" if fails else "ALL PASS")
if fails:
    print("\n".join(f"  - {f}" for f in fails))
sys.exit(1 if fails else 0)
