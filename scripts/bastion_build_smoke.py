"""Raising a stronghold: the rules refuse, the imagination does not.

The bastion layer could already RUN a place — facilities, orders, turns,
travel — and the only way to GET one was a REST call with a name in it. There
was no moment where a player decides what their place is.

The builder has two halves and they are governed completely differently, which
is the thing this test is really about:

* the CONSTRAINTS are the game's, decided in one place (`bastion/build.py`) so
  the screen and the DM cannot disagree, and re-checked on commit so a client
  that lies about its own arithmetic buys nothing;
* the EXPRESSION is the player's — name, look, motif — and nothing validates
  it, refuses it, or trims it into a template.

    uv run python scripts/bastion_build_smoke.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

db = os.path.join(tempfile.gettempdir(), "oracle_bastion_smoke.db")
if os.path.exists(db):
    os.remove(db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"

spec = importlib.util.spec_from_file_location(
    "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)                                     # noqa: E402

from sqlmodel import Session, SQLModel, select                  # noqa: E402

from airships.catalog import VESSELS                            # noqa: E402
from bastion import build as B                                  # noqa: E402
from bastion.models import Bastion, FacilityInstance            # noqa: E402

SQLModel.metadata.create_all(m.engine)

OK, BAD, OFF, DIM = "\033[32m", "\033[31m", "\033[0m", "\033[2m"
_fails = 0


def check(cond: bool, what: str, detail: str = "") -> None:
    global _fails
    print(f"  {OK}OK{OFF}  {what}" if cond else f"  {BAD}FAIL{OFF}  {what}")
    if detail:
        print(f"      {DIM}{detail}{OFF}")
    if not cond:
        _fails += 1


#: A mover THIS test's character can actually build. Picking the first in the
#: catalogue got the Lyrandar Helm, which is level 13, so the level-11 cases
#: were refused for the right reason and the wrong one at once.
_PROP = __import__("bastion.catalog", fromlist=["x"]).propulsion_facilities()
MOVER = next((f["slug"] for f in _PROP if int(f.get("min_level") or 5) <= 11), "")

print("\n\033[1m1. what the rules allow\033[0m")
low = B.plan(3, purse_gp=99999, vessels=VESSELS)
high = B.plan(11, purse_gp=99999, vessels=VESSELS)
check(not low["can_own"] and high["can_own"],
      "a bastion has a level, and the plan says so",
      f"level 3 no, level 11 yes (min {high['min_level']})")
check(len(high["facilities"]) > len(B.plan(5, vessels=VESSELS)["facilities"]),
      "a higher level unlocks more facilities",
      f"{len(B.plan(5, vessels=VESSELS)['facilities'])} at 5 vs "
      f"{len(high['facilities'])} at 11")
check(any(f["propulsion"] for f in high["facilities"]),
      "and the ones that can MOVE it are marked", f"mover: {MOVER}")
check(all(f["cost_gp"] > 0 for f in high["facilities"]),
      "everything is priced, by the live config rather than a constant here")

print("\n\033[1m2. what it refuses, and how it says so\033[0m")
v = B.check(B.Choice(kind="keep", name="Hollowhall"), 3, vessels=VESSELS)
check(not v.ok and "level" in " ".join(v.reasons).lower(),
      "too low a level is refused by level", f"{v.reasons}")
v = B.check(B.Choice(kind="keep", name=""), 11, vessels=VESSELS)
check(not v.ok and any("name" in r.lower() for r in v.reasons),
      "a nameless place is refused")
v = B.check(B.Choice(kind="airship", name="Sow", vessel_slug="skiff",
                     facilities=("arcane-study",)), 11, vessels=VESSELS)
check(not v.ok and any("move it" in r for r in v.reasons),
      "a flying bastion with nothing to move it is refused — and TOLD what "
      "would fix it",
      f"{v.reasons}")
v = B.check(B.Choice(kind="airship", name="Sow", facilities=(MOVER,)), 11,
            vessels=VESSELS)
check(not v.ok and any("vessel" in r.lower() for r in v.reasons),
      "…and one with no hull to be built into")
v = B.check(B.Choice(kind="keep", name="Hollowhall",
                     facilities=("arcane-study", "armory")), 11,
            purse_gp=10, vessels=VESSELS)
check(not v.ok and any("gp" in r for r in v.reasons),
      "an empty purse is refused at the quoted price", f"{v.reasons}")
v = B.check(B.Choice(kind="keep", name="Hollowhall", facilities=(MOVER,)), 11,
            purse_gp=99999, vessels=VESSELS)
check(v.ok and v.notes,
      "a fixed place with an engine in it is ALLOWED and merely noted",
      f"{v.notes} — advice is not a refusal")

print("\n\033[1m3. nothing argues with the imagination\033[0m")
wild = B.Choice(kind="keep", name="The Gilded Sow", facilities=("armory",),
                description="a brass hall slung under a whale-shaped envelope, "
                            "lamps at every rail and a smell of hot pennies",
                motif="pigs in gold leaf, everywhere, and one real one")
v = B.check(wild, 11, purse_gp=99999, vessels=VESSELS)
check(v.ok, "an extravagant description is not a refusal")
said = B.describe(wild, vessels=VESSELS)
check("whale-shaped envelope" in said and "gold leaf" in said,
      "and it reaches the world VERBATIM, leading the sentence",
      "the picture should be of the thing they imagined, not of a room list")
check(said.index("whale-shaped") < said.index("armory"),
      "the player's words come before the mechanical ones")

print("\n\033[1m4. raising one for real\033[0m")
SESSION, USER = "bastion:table", "bastion-user"
with Session(m.engine) as s:
    char = m.Character(discord_user_id=USER, name="Bryn", race="Human",
                       char_class="Fighter", level=11,
                       stats={"STR": 14, "DEX": 12, "CON": 13,
                              "INT": 10, "WIS": 11, "CHA": 9},
                       gp=60000, sp=0, cp=0, ep=0, pp=0, inventory=[])
    s.add(char)
    s.commit()
    s.refresh(char)
    char_id = char.id
m._set_session_meta(SESSION, {"character_id": char_id})

plan = m._activity_bastion(SESSION, USER)
check(plan is not None and plan["can_own"], "the screen gets a plan")
check(plan["purse_gp"] > 0, "quoted against the character's REAL purse",
      f"{plan['purse_gp']:g} gp")

res = m._activity_bastion_build(SESSION, USER, {
    "kind": "keep", "name": "The Gilded Sow",
    "description": "a brass hall under a whale-shaped envelope",
    "motif": "pigs in gold leaf", "facilities": ["armory", "barrack"]})
check(res.get("ok"), "it is raised", f"{res.get('detail','')}")
with Session(m.engine) as s:
    b = s.exec(select(Bastion)).first()
    rooms = s.exec(select(FacilityInstance)).all()
    after = s.get(m.Character, char_id)
check(b is not None and b.name == "The Gilded Sow", "the row exists")
check(len(rooms) == 2, "with both facilities installed",
      f"{[r.facility_slug for r in rooms]}")
# In COPPER, because a purse normalises: 50,000 gp of change comes back as
# 5,000 pp and a `gp` field reading zero, which looks like a bug and is not.
left_gp = m.to_cp(m._purse_of(after)) / 100.0
check(abs(left_gp - (60000 - res["bastion"]["cost_gp"])) < 1,
      "and the gold really left the purse",
      f"60000 -> {left_gp:g} gp after {res['bastion']['cost_gp']:g}")
check("whale-shaped" in (b.notes or ""),
      "the player's own words are on the bastion")
with Session(m.engine) as s:
    _b = s.exec(select(Bastion)).first()
check(_b.place_slug == res["bastion"].get("place_slug"),
      "the place is written BACK onto the bastion row",
      "every later question keys on that column — including which rooms a "
      "board aboard it gets — and returning the slug without storing it left "
      "a bastion unfindable from its own place, silently, in both directions")
check(bool(res["bastion"].get("place_slug")),
      "and the world has a PLACE for it — which is what lets the party be "
      "inside it, and a flying one move without the world layer learning a "
      "new idea",
      f"{res['bastion'].get('place_slug')}")

print("\n\033[1m5. the player's words reach the things that DRAW\033[0m")
# The point of writing a description at all. Before this the words went to the
# Bastion row and to lore — prose for the DM, read by nothing that renders —
# so a brass hall under a whale-shaped envelope was drawn as a generic room.
from eight_card_system import placelore                         # noqa: E402
slug = res["bastion"]["place_slug"]
ent = m.world.get_entity(slug)
attrs = (getattr(ent, "attributes", None) or {})
check("whale-shaped" in str(attrs.get("description") or ""),
      "the look is on the PLACE, where placelore reads one",
      "attributes['description'] -> PlaceCharacter.description")
check("gold leaf" in " ".join(str(x) for x in (attrs.get("motifs") or [])),
      "and what it is known for is a MOTIF, which is what that field is")
ch = placelore.character_of(m.world, slug)
check(ch is not None and "whale-shaped" in ch.scene_look(),
      "so the establishing render is of the thing they imagined",
      f"{(ch.scene_look() if ch else '')[:90]}…")

check("ploughed" not in ch.scene_look() and "hedgerow" not in ch.scene_look(),
      "and it is not described as the farmland it happens to be flying over",
      "a vessel presents its own surface — the tavern-in-farmland rule, one "
      "level up")

req = m._place_scene_request(ent)
check(req and "whale-shaped" in str(req.get("look") or ""),
      "…all the way into the arrival image request")

from vtt.mapgen import style_for                                # noqa: E402
check(style_for(m._place_look_words(slug)) == "steampunk",
      "and a BRASS hall builds a brass ship, rather than whatever the seed said",
      "the style was rolled: five times in ten a described steam contraption "
      "came out timber, and every material downstream followed it")
check(style_for("the deck of a ship") == "",
      "a vessel nobody described still lets the seed decide")

print("\n\033[1m6. a stronghold is never finished\033[0m")
# The rules answer the question the first version of this builder ducked: a
# bastion GAINS a special facility at 9, 13 and 17, so the second visit is an
# extension, not a locked card. Refusing it left a level-17 character living in
# the two rooms they could afford at level 5.
p2 = m._activity_bastion(SESSION, USER)
check(p2["special_used"] == 2 and p2["special_slots"] == 3,
      "the screen comes back knowing what is already built",
      f"{p2['special_used']} of {p2['special_slots']} special, "
      f"{p2['basic_used']} of {p2['basic_slots']} rooms")

more = m._activity_bastion_build(SESSION, USER, {
    "facilities": ["smithy"],
    "rooms": [{"slug": "bedroom", "name": "the master cabin",
               "description": "all brass and green glass"},
              {"slug": "kitchen", "name": "Ket's galley"}]})
check(more.get("ok") and more.get("added"),
      "and you can add to it", f"{more.get('detail','')}")
with Session(m.engine) as s:
    inst = s.exec(select(FacilityInstance)).all()
    ent2 = m.world.get_entity(slug)
check(sum(1 for r in inst if r.facility_type == "basic") == 2,
      "the ordinary rooms are installed as BASIC facilities",
      "`facility_type='basic'` has been in the table since it was written and "
      "nothing ever created one, so every bastion here was a list of workshops "
      "with nowhere to sleep")
check(any(r.name == "the master cabin" for r in inst),
      "under the names their owner gave them, not their kind",
      "'a bedroom' is a floor plan; 'the master cabin' is somebody's home")
check("master cabin" in str((getattr(ent2, "attributes", None) or {})
                            .get("description") or ""),
      "and the new rooms join the look the renderers read — appended, never "
      "replacing what they wrote about the hall")

print("\n\033[1m7. how many is the LEVEL talking, not the purse\033[0m")
over = m._activity_bastion_build(SESSION, USER, {"facilities": ["library"]})
check(not over.get("ok") and "level" in (over.get("detail") or "").lower(),
      "a fourth special facility is refused at level 11 — with money in hand",
      f"{over.get('detail')}")
check("9, 13 and 17" in (over.get("detail") or ""),
      "…and the refusal says when the next one comes")
v = B.check(B.Choice(kind="keep", name="Hollowhall",
                     facilities=("armory", "barrack", "smithy", "library")),
            5, purse_gp=999999, vessels=VESSELS)
check(not v.ok and any("holds 2" in r for r in v.reasons),
      "a rich level-5 character cannot buy the whole book",
      f"{v.reasons} — this was checked by NOTHING; gold decided the count, "
      f"which is a shop rather than a stronghold")
v = B.check(B.Choice(kind="keep", name="Hollowhall", facilities=("armory",),
                     rooms=(B.Room("bedroom", ""),)), 11,
            purse_gp=999999, vessels=VESSELS)
check(not v.ok and any("needs a name" in r for r in v.reasons),
      "and an UNNAMED room is the one thing the expressive half refuses",
      "a blank is not an expression; everything a player actually writes is "
      "taken as written")

print("\n\033[1m8. and still only one bastion\033[0m")
check(not m._activity_bastion_build(SESSION, USER, {
          "kind": "keep", "name": "Second Hall"}).get("ok"),
      "a bare second raising adds nothing and says so")
plan2 = m._activity_bastion(SESSION, USER)
check(plan2 and plan2.get("existing", {}).get("name") == "The Gilded Sow",
      "and the screen shows what you already hold instead of an empty form")

print()
if _fails:
    print(f"{BAD}{_fails} FAILED{OFF}")
    sys.exit(1)
print(f"{OK}the rules decide what may be built; the player decides what it is{OFF}")
