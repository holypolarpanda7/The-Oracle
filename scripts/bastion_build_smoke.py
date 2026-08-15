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
check(bool(res["bastion"].get("place_slug")),
      "and the world has a PLACE for it — which is what lets the party be "
      "inside it, and a flying one move without the world layer learning a "
      "new idea",
      f"{res['bastion'].get('place_slug')}")

print("\n\033[1m5. and only one\033[0m")
again = m._activity_bastion_build(SESSION, USER, {
    "kind": "keep", "name": "Second Hall", "facilities": []})
check(not again.get("ok") and "already" in (again.get("detail") or "").lower(),
      "a second bastion is refused", f"{again.get('detail')}")
plan2 = m._activity_bastion(SESSION, USER)
check(plan2 and plan2.get("existing", {}).get("name") == "The Gilded Sow",
      "and the screen shows what you already hold instead of an empty form")

print()
if _fails:
    print(f"{BAD}{_fails} FAILED{OFF}")
    sys.exit(1)
print(f"{OK}the rules decide what may be built; the player decides what it is{OFF}")
