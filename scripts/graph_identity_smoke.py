"""Entity identity — when the world graph thinks two records are one person.

Offline, no GPU, no LLM, no rules tables: a scratch world graph and nothing
else.

`find_entities_by_name` decides whether a name the DM just narrated refers to
somebody the world already has. Everything about that is silent when it goes
wrong: a false MISS quietly creates a second Kara standing beside the first
(the collision census._spawn_npc already reroll-guards against), and a false
HIT merges two different people into one record. Nothing raises either way,
which is why the contract is pinned here rather than left to the callers —
extraction, hoards, pantheon, the origin ties and the goal resolver all lean on
it.

The function was rewritten for speed (it used to build a full ORM object for
every entity in the world, attribute JSON and all, to compare one string —
~20 ms per call at 2,000 entities, and 87% of the cost of placing a thread
anchor). These checks are the equivalence the rewrite had to preserve.

Usage:  uv run python scripts/graph_identity_smoke.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
_fails: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = f"{GREEN}✓{OFF}" if ok else f"{RED}✗{OFF}"
    print(f"  {mark} {label}" + (f" {DIM}— {detail}{OFF}" if detail else ""))
    if not ok:
        _fails.append(label)


def main() -> int:
    from eight_card_system.graph import WorldGraph
    from eight_card_system.models import EntityType

    db = os.path.join(tempfile.mkdtemp(), "identity.db")
    g = WorldGraph(database_url=f"sqlite:///{db}")
    g.create_tables()

    def slugs(name: str) -> list[str]:
        return [e.slug for e in g.find_entities_by_name(name)]

    print(f"\n{BOLD}1. the same name is the same name{OFF}")
    kara = g.create_entity("Kara Emberfall", EntityType.NPC)
    check("an exact name finds it", slugs("Kara Emberfall") == [kara.slug])
    check("…and case does not matter",
          slugs("kara emberfall") == [kara.slug]
          and slugs("KARA EMBERFALL") == [kara.slug])
    check("…and the caller's surrounding space is ignored",
          slugs("  Kara Emberfall  ") == [kara.slug])
    check("a name nobody has finds nothing", slugs("Nobody At All") == [])
    check("…and neither does an empty one", slugs("") == [] and slugs("   ") == [])

    print(f"\n{BOLD}2. two people may share a name, and both come back{OFF}")
    kara2 = g.create_entity("Kara Emberfall", EntityType.PC, discord_user_id="u2")
    found = slugs("Kara Emberfall")
    check("both records are returned", set(found) == {kara.slug, kara2.slug},
          ", ".join(found))
    check("…with distinct slugs, so they stay two people",
          kara.slug != kara2.slug, f"{kara.slug} / {kara2.slug}")

    print(f"\n{BOLD}3. a SLUG is accepted, and it leads{OFF}")
    # `_apply_origin_ties` and the goal resolver both take the FIRST result, so
    # an exact slug match has to sort ahead of the people merely named that.
    place = g.create_entity("Greenfields", EntityType.PLACE)
    g.create_entity("Greenfields", EntityType.FACTION)
    by_slug = slugs(place.slug)
    check("an exact slug resolves", by_slug and by_slug[0] == place.slug,
          ", ".join(by_slug))
    check("…and it is FIRST when the name is shared",
          slugs("Greenfields")[0] == place.slug, ", ".join(slugs("Greenfields")))

    print(f"\n{BOLD}4. case-folding is not ASCII-only{OFF}")
    # SQLite's lower() folds A-Z and nothing else, so pushing this comparison
    # into SQL would silently stop matching these — and a miss here invents a
    # second person rather than raising.
    # The STORED name has to carry the uppercase non-ASCII letter for this to
    # bite: the caller's string is folded by Python before it ever reaches the
    # database, so a probe like "KAËLITH" arrives already lowercased and an
    # ASCII-only fold on the stored side never notices. "Ærik" is the case
    # that does — SQLite's lower() leaves the Æ exactly where it is.
    fancy = g.create_entity("Ærik Stonehand", EntityType.NPC)
    check("an accented name matches its own case",
          slugs("Ærik Stonehand") == [fancy.slug])
    check("…and matches when the STORED name is the odd-cased one",
          slugs("ærik stonehand") == [fancy.slug], str(slugs("ærik stonehand")))
    umlaut = g.create_entity("Kaëlith Ysande", EntityType.NPC)
    check("…in both directions", slugs("KAËLITH YSANDE") == [umlaut.slug])

    print(f"\n{BOLD}5. it does not match things it shouldn't{OFF}")
    g.create_entity("Kara", EntityType.NPC)
    check("a shorter name is not the longer one",
          set(slugs("Kara")) == {"kara"}, ", ".join(slugs("Kara")))
    check("…and a longer one is not the shorter",
          "kara" not in slugs("Kara Emberfall"))

    print()
    if _fails:
        print(f"{RED}{len(_fails)} check(s) failed:{OFF} " + "; ".join(_fails))
        return 1
    print(f"{GREEN}the world knows who is who{OFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
