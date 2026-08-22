"""A chase happens SOMEWHERE, and where changes what goes wrong.

The chase minigame picked its complications out of three buckets — urban,
wilderness, dungeon — chosen by `_guess_terrain`, a keyword scan over whatever
the DM wrote in the hook. The DM's words there are almost always about who is
being chased, so nearly every chase outside a city got "wilderness": a
fruit-seller's cart tipping across the way, a startled ox, a temple procession.
In a marsh. Meanwhile the world graph knew perfectly well that the party was
standing in a marsh, because `placelore` has decided what country every place
is in since the map-coherence layer went in, and five other systems read it.

What is checked: the party's real country wins over a guess; the DM naming a
terrain outright still wins over both; the complications differ by country and
are drawn deterministically; and the terrain vocabulary the world can produce
is entirely mapped, so no country falls through to the generic bucket.

Offline: a scratch copy of the database, no LLM, no GPU.

    uv run python scripts/chase_smoke.py
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
fails: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {GREEN}✓{OFF} {label}" if ok else f"  {RED}✗{OFF} {label}"
          + (f" {DIM}— {detail}{OFF}" if detail else ""))
    if not ok:
        fails.append(label)


db = Path(tempfile.gettempdir()) / "oracle_chase_smoke.db"
for suffix in ("", "-wal", "-shm"):
    Path(str(db) + suffix).unlink(missing_ok=True)
live = ROOT / "oracle-dm-backend" / "oracle.db"
if live.is_file():
    shutil.copy(live, db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"
os.environ["ORACLE_IMAGERY_ENABLED"] = "0"

spec = importlib.util.spec_from_file_location(
    "dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

print(f"\n{BOLD}1. the world's country reaches the chase{OFF}")
check("every terrain a place can be in maps to a bucket",
      not (set(__import__("eight_card_system.placelore", fromlist=["x"]).RELIEF)
           - set(m._CHASE_FOR_TERRAIN)),
      f"{sorted(m._CHASE_FOR_TERRAIN)}")
check("...and every bucket has complications written for it",
      not (set(m._CHASE_FOR_TERRAIN.values()) - set(m._CHASE_COMPLICATIONS)),
      f"{sorted(set(m._CHASE_FOR_TERRAIN.values()))}")

# A real place, in a real country, with a real PC standing in it.
W = m.world
marsh = W.upsert_entity("The Drowning Fen", "place", slug="drowning-fen",
                        attributes={"terrain": "swamp", "scale": "poi"})
pc = W.upsert_entity("Wick", "pc", slug="wick-chase")
W.move_entity(pc.slug, marsh.slug)
check("the party's own country is what the chase reads",
      m._chase_terrain_for(pc.slug) == "swamp",
      m._chase_terrain_for(pc.slug))
check("...and an unplaced character asks for nothing",
      m._chase_terrain_for("nobody-at-all") == "")

print(f"\n{BOLD}2. through the real hook{OFF}")
SID = "chase:smoke"
m._set_session_meta(SID, {"user_id": "smoke", "character_name": "Wick",
                          "pc_slug": pc.slug})


def start(said: str = "") -> str:
    st = m._load_session_state(SID)
    meta = dict(st.get("meta", {}) or {})
    meta.pop("active_chase", None)
    m._set_session_meta(SID, meta)
    m.process_chase_hooks(SID, [{"action": "start",
                                 "args": ["flee", "the fen-hags"]
                                 + ([said] if said else [])}])
    return str(((m._load_session_state(SID).get("meta") or {})
                .get("active_chase") or {}).get("terrain") or "")


got = start()
check("a chase started with no words at all is fought in the marsh",
      got == "swamp", got)
got = start("the hags come howling after you")
check("...and a sentence about the ENEMY does not move it",
      got == "swamp", got)
got = start("through the alleys of the lower town")
check("...while a sentence about the GROUND does",
      got == "urban", got)
got = start("mountains")
check("...and a DM naming a bucket outright wins over everything",
      got == "mountains", got)

print(f"\n{BOLD}3. and it changes what goes wrong{OFF}")
ac = {"role": "flee", "adversary": "the fen-hags", "round": 1,
      "progress": 0, "escape_at": 3, "caught_at": 3, "status": "active"}
seen = {}
for terrain in ("swamp", "urban", "mountains", "forest", "coast"):
    block = m._format_active_chase_block({**ac, "terrain": terrain}, SID)
    comp = next((ln for ln in block.splitlines()
                 if any(w in ln for w in m._CHASE_COMPLICATIONS[terrain])), "")
    seen[terrain] = comp.strip()
check("each country rolls a complication out of its own list",
      len({v for v in seen.values() if v}) == len(seen),
      "; ".join(f"{k}: {v[:44]}" for k, v in list(seen.items())[:3]))
check("...and the same chase on the same round rolls the same one",
      m._format_active_chase_block({**ac, "terrain": "swamp"}, SID)
      == m._format_active_chase_block({**ac, "terrain": "swamp"}, SID))
# The generic bucket is a FALLBACK, not where most chases end up.
check("a fruit-seller's cart does not tip over in a bog",
      not any("fruit-seller" in v for v in seen.values()),
      str(seen.get("swamp", ""))[:60])

print()
if fails:
    print(f"{RED}{len(fails)} check(s) failed:{OFF} " + "; ".join(fails))
    raise SystemExit(1)
print(f"{GREEN}a chase is fought in the country the party is standing in{OFF}")
