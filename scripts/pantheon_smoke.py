"""Smoke test: the powers a character may swear to come from the LIVE world.

The point of this one is the loop that used to be broken: a god that rises in
play (a divine event) has to be offerable to the next character made, and a god
that is unmade has to stop being offered — without anyone editing a list.

Runs offline against a fresh scratch database.

    uv run python scripts/pantheon_smoke.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  \033[32m✓\033[0m {name}")
    else:
        print(f"  \033[31m✗\033[0m {name}{' — ' + detail if detail else ''}")
        FAILS.append(name)


def _load_backend(db_path: str):
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    spec = importlib.util.spec_from_file_location(
        "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # type: ignore[union-attr]
    return mod


def main() -> int:
    db = os.path.join(tempfile.gettempdir(), "oracle_pantheon_smoke.db")
    if os.path.exists(db):
        os.remove(db)

    print("\033[1mThe Oracle — pantheon / patron-choice smoke test\033[0m")
    m = _load_backend(db)

    from sqlmodel import SQLModel

    from eight_card_system import pantheon as pan
    SQLModel.metadata.create_all(m.engine)

    # ---- 1. an unseeded world still offers the canon ---------------------
    print("\n\033[1m1. before the world is seeded, the canon answers\033[0m")
    payload = m._pantheon_options()
    check("families are offered", len(payload["families"]) > 0)
    check("powers are offered", len(payload["powers"]) > 0,
          str(len(payload["powers"])))
    check("every power names its family",
          all(p.get("family") and p.get("family_label") for p in payload["powers"]))
    check("and how mortals deal with it",
          all(p.get("worship") for p in payload["powers"]))

    # ---- 2. the seeded world is the same roster, from the graph ----------
    print("\n\033[1m2. the seeded world serves the graph\033[0m")
    pan.seed_pantheon(m.world)
    seeded = m._pantheon_options()
    check("every power now has a graph slug",
          all(p.get("slug") for p in seeded["powers"]),
          str([p["name"] for p in seeded["powers"] if not p.get("slug")][:3]))
    check("the roster matches the canon",
          {p["name"] for p in seeded["powers"]} == {p["name"] for p in payload["powers"]})
    check("nothing is marked risen yet",
          not any(p.get("risen") for p in seeded["powers"]))

    # ---- 3. character creation offers them ------------------------------
    print("\n\033[1m3. character creation offers what the world has\033[0m")
    opts = m.cc_options()
    check("/cc/options carries the pantheon",
          bool(opts.get("deities", {}).get("powers")))
    check("…grouped into families for the picker",
          len(opts["deities"]["families"]) == len(seeded["families"]))

    # ---- 4. a god rises in play, and is choosable next ------------------
    print("\n\033[1m4. a god born in play joins the choices\033[0m")
    res = pan.apply_divine_event(
        m.world, family="sovereign", dying="Vesh",
        new_powers=[{"name": "The Kindled Name", "title": "who was a mortal",
                     "alignment": "neutral good",
                     "domains": "ascension, second chances, the lit lamp",
                     "symbol": "a lamp lit from a pyre",
                     "blurb": "A mortal raised to godhood in living memory."}],
        reason="the Vigil of Ash", event_kind="apotheosis")
    check("the event created the power", "the-kindled-name" in res["created"],
          str(res["created"]))
    after = m._pantheon_options()
    names = {p["name"] for p in after["powers"]}
    check("a character can now choose it", "The Kindled Name" in names)
    check("it is marked as risen in this age",
          any(p["risen"] for p in after["powers"] if p["name"] == "The Kindled Name"))
    check("the unmade god is no longer offered", "Vesh" not in names)
    check("the family count followed",
          next(f["count"] for f in after["families"] if f["key"] == "sovereign")
          == len([p for p in after["powers"] if p["family"] == "sovereign"]))

    # ---- 5. naming it works everywhere ---------------------------------
    print("\n\033[1m5. naming the new god resolves, everywhere it matters\033[0m")
    check("a loose name normalises to the canonical one",
          m._canonical_deity("kindled name") == "The Kindled Name",
          str(m._canonical_deity("kindled name")))
    check("a title-form name resolves too",
          m._canonical_deity("The Kindled Name who was a mortal") == "The Kindled Name")
    check("an unknown patron is kept verbatim",
          m._canonical_deity("Saint Hollis of the Ditch") == "Saint Hollis of the Ditch")
    check("no patron stays none", m._canonical_deity("  ") is None)
    import pvp
    check("the new god avenges its own worshipper",
          pvp.retributor_for("The Kindled Name", "neutral good",
                             graph=m.world)["name"] == "The Kindled Name")
    check("a slain god no longer answers the call",
          pvp.retributor_for("Vesh", "chaotic neutral",
                             graph=m.world)["name"] != "Vesh")

    # ---- 6. a character actually keeps it -------------------------------
    print("\n\033[1m6. the sheet keeps the patron\033[0m")
    import asyncio
    req = m.RegisterCharacterRequest(
        discord_user_id="pantheon-smoke", name="Wick", race="Human",
        char_class="Cleric", background="Acolyte", approve=True, source="guided",
        deity="kindled name",
        stats={"strength": 12, "dexterity": 12, "constitution": 14,
               "intelligence": 10, "wisdom": 16, "charisma": 10})
    out = asyncio.run(m.register_character(req))
    from sqlmodel import Session
    with Session(m.engine) as s:
        ch = s.get(m.Character, out["character_id"])
        check("stored under the canonical name", ch.deity == "The Kindled Name",
              str(ch.deity))

    print()
    if FAILS:
        print(f"\033[31m{len(FAILS)} check(s) failed:\033[0m " + ", ".join(FAILS))
        return 1
    print("\033[32mthe world's powers are the world's to change\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
