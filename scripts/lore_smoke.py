"""The durable WHY behind a fact — recorded once, told the same way forever.

A DM improvises the origin of a feud, and the next player who asks gets a
different origin unless somebody wrote the first one down. `[[LORE:]]` is that
somebody: a bounded reason stamped on the relationship edge that already exists,
or a short legend on a single entity — no new nodes, no new edges.

What this pins, in the order it broke:

1. the hook parses in both shapes (a pair's reason, and one entity's legend);
2. a recorded reason REACHES THE DM. It did not: the world-context renderer
   skipped any edge that was not `allied_with`/`hostile_to`/`member_of`/`owns`,
   testing the edge's TYPE rather than its contents — and `record_lore` opens a
   plain `knows` edge whenever the sentiment cues do not fire, which is most
   sentences. Every neutral piece of lore ever recorded was written to the
   database and shown to nobody;
3. the sentiment cues fire on what people actually write ("they burned the
   orchard", "he sheltered her") and not only on the word "enmity";
4. and re-recording overwrites rather than growing the graph.

    uv run python scripts/lore_smoke.py
"""
from __future__ import annotations

import os
import re
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

db = os.path.join(tempfile.gettempdir(), "oracle_lore_smoke.db")
if os.path.exists(db):
    os.remove(db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"

from sqlmodel import Session, create_engine, select              # noqa: E402

from eight_card_system.graph import WorldGraph                    # noqa: E402
from eight_card_system.models import EntityType, Relation         # noqa: E402

OK, BAD, OFF, DIM = "\033[32m", "\033[31m", "\033[0m", "\033[2m"
_fails = 0


def check(cond: bool, what: str, detail: str = "") -> None:
    global _fails
    print(f"  {OK}OK{OFF}  {what}" if cond else f"  {BAD}FAIL{OFF}  {what}")
    if detail:
        print(f"      {DIM}{detail}{OFF}")
    if not cond:
        _fails += 1


# --- 1. the hook, both shapes ------------------------------------------------
# Parsed here rather than imported: loading the backend module for a regex costs
# a database, an LLM client and every subsystem's import.
LORE_HOOK = re.compile(r"\[\[\s*LORE\s*:\s*(.+?)\]\]", re.I | re.S)


def parse(text: str) -> list[dict]:
    out = []
    for m in LORE_HOOK.finditer(text):
        parts = [p.strip() for p in m.group(1).split("|")]
        if not parts or not parts[0]:
            continue
        if len(parts) >= 3 and parts[1]:
            out.append({"subject": parts[0], "object": parts[1],
                        "reason": " | ".join(parts[2:]).strip()})
        else:
            out.append({"subject": parts[0], "object": None,
                        "reason": " | ".join(parts[1:]).strip()})
    return [o for o in out if o["reason"]]


print("\n\033[1m1. the hook\033[0m")
ops = parse(
    "The priest sighs. [[LORE: Maerin Vale | House Corvath | They burned her "
    "mother's orchard the winter the river froze.]] She will not say more. "
    "[[LORE: Maerin Vale | She keeps a cracked signet nobody has seen her wear.]]")
check(len(ops) == 2, "both shapes parse out of one reply")
check(ops[0]["object"] == "House Corvath", "a PAIR's reason keeps its object")
check(ops[1]["object"] is None, "a single entity's legend has none")

# --- the world ---------------------------------------------------------------
eng = create_engine(f"sqlite:///{db}")
g = WorldGraph(engine=eng)
g.create_tables()
maerin = g.upsert_entity("Maerin Vale", EntityType.NPC)
corvath = g.upsert_entity("House Corvath", EntityType.FACTION)
ilm = g.upsert_entity("Brother Ilm", EntityType.NPC)
tanners = g.upsert_entity("The Tanner's Guild", EntityType.FACTION)

print("\n\033[1m2. what the sentiment reads\033[0m")
hostile = g.record_lore(maerin.slug, corvath.slug,
                        reason="They burned her mother's orchard the winter "
                               "the river froze.")
allied = g.record_lore(maerin.slug, ilm.slug,
                       reason="He sheltered her the night the militia came "
                              "looking.")
neutral = g.record_lore(maerin.slug, tanners.slug,
                        reason="She sold them a bad hide once and neither has "
                               "mentioned it since.")
legend = g.record_lore(maerin.slug,
                       reason="She keeps a cracked signet nobody has seen her wear.")
check(hostile and hostile["rel_type"] == "hostile_to",
      "a burned orchard opens a HOSTILE edge",
      f"got {hostile and hostile['rel_type']}")
check(allied and allied["rel_type"] == "allied_with",
      "being sheltered opens an ALLIED one",
      f"got {allied and allied['rel_type']}")
check(neutral and neutral["rel_type"] == "knows",
      "and a sentence with no sentiment in it stays neutral")
check(legend and legend["mode"] == "entity",
      "a lone subject stamps the entity, opening no edge")

print("\n\033[1m3. and the DM is told\033[0m")
world = g.get_world_context(maerin.slug, "asks about House Corvath").render()
check("## Relationships" in world, "the slice carries a Relationships block")
check("burned her mother's orchard" in world,
      "the hostile reason is stated")
check("sheltered her the night" in world, "so is the allied one")
check("bad hide" in world,
      "AND SO IS THE NEUTRAL ONE — the bug this test exists for",
      "a `knows` edge carrying a reason IS recorded history; the filter "
      "used to test the edge's type and drop it")
check("cracked signet" in world, "the entity's own legend is on its line")

print("\n\033[1m4. cheap, and it stays cheap\033[0m")
with Session(eng) as s:
    before = len(s.exec(select(Relation)).all())
g.record_lore(maerin.slug, corvath.slug,
              reason="Her mother died of it two winters after.")
with Session(eng) as s:
    rels = s.exec(select(Relation)).all()
check(len(rels) == before, "re-recording overwrites rather than growing the graph",
      f"{before} edges before, {len(rels)} after")
again = g.get_world_context(maerin.slug, "asks again").render()
check("died of it two winters after" in again and "burned her mother" not in again,
      "and the newest telling is the one the DM gets")

print()
if _fails:
    print(f"{BAD}{_fails} FAILED{OFF}")
    sys.exit(1)
print(f"{OK}what the world was told once, it says the same way twice{OFF}")
