"""What a character brings WITH them out of creation.

Offline, no GPU, no LLM. A scratch DB carrying a copy of the real rules tables,
so the keepsake is a real catalogued item and the species/feats are the ones
this machine actually has.

What it pins:

1. The CC payload survives the wire. Both creation paths (the Activity's and
   the Proving Grounds') build one request now, because each used to build its
   own and each forgot a different half — a wizard arrived with no spells, no
   tools, no languages and no feat picks.
2. A keepsake can be NAMED and DESCRIBED: the sheet shows the player's name for
   it, `base` still points at the catalogue row (or every stat lookup breaks),
   and their words are kept with it for the drawing.
3. An ORIGIN is world state, not prose. A homeland the world already has ties
   the character to that place; a people they invent becomes a real faction
   entity with a real edge, which is what lets the DM use it.
4. The backstory itself lands on the sheet.

Usage:  uv run python scripts/cc_story_smoke.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REAL_DB = ROOT / "oracle-dm-backend" / "oracle.db"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "oracle-dm-backend"))

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
_fails: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = f"{GREEN}✓{OFF}" if ok else f"{RED}✗{OFF}"
    print(f"  {mark} {label}" + (f" {DIM}— {detail}{OFF}" if detail else ""))
    if not ok:
        _fails.append(label)


def _scratch_db() -> str:
    path = os.path.join(tempfile.gettempdir(), "oracle_cc_story.db")
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    con = sqlite3.connect(path)
    con.execute("ATTACH DATABASE ? AS src", (str(REAL_DB),))
    for table in ("rules_race", "rules_feat", "rules_item", "rules_spell"):
        con.execute(f"CREATE TABLE {table} AS SELECT * FROM src.{table}")
    con.commit()
    con.close()
    return path


def main() -> int:
    db = _scratch_db()
    os.environ["DATABASE_URL"] = f"sqlite:///{db}"
    os.environ.setdefault("ORACLE_IMAGERY_ENABLED", "0")   # no GPU in a test
    spec = importlib.util.spec_from_file_location(
        "dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
    dm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dm)

    from sqlmodel import Session, SQLModel, select
    SQLModel.metadata.create_all(dm.engine)
    from eight_card_system.seed import seed_starter_world
    from eight_card_system.models import Entity as _Ent, Relation as _Rel, RelationType
    seed_starter_world(dm.world)

    opts = dm.cc_options()
    keepsake = (opts["common_items"] or [None])[0]
    print(f"\n{BOLD}1. the payload survives the wire{OFF}")
    p = {"name": "Vashti", "race": "Human", "char_class": "Wizard",
         "background": (opts["backgrounds"][0]["slug"]),
         "stats": {"strength": 10, "dexterity": 14, "constitution": 12,
                   "intelligence": 15, "wisdom": 11, "charisma": 10},
         "skills": ["Arcana"], "tools": ["Calligrapher's Supplies"],
         "languages": ["Draconic"], "feat_options": ["Fire Strike"],
         "cantrips": ["fire-bolt"], "spells": ["absorb-elements"],
         "wondrous_item": keepsake["slug"] if keepsake else None,
         "wondrous_name": "Kettle-Wind",
         "wondrous_desc": "Sooty grey wool, mended at the shoulder with copper wire.",
         "backstory": "Raised by the tinkers of the Kettle.",
         "homeland": "Greenfields", "homeland_new": False,
         "faction": "The Hollow Kettle", "faction_new": True,
         "gear_mode": "buy", "bought_items": []}
    req = dm._cc_request("story-smoke", p)
    check("every field the wizard asks for is carried",
          req.cantrips == ["fire-bolt"] and req.spells == ["absorb-elements"]
          and req.tools and req.languages and req.feat_options
          and req.wondrous_desc and req.backstory and req.homeland,
          "cantrips + spells + tools + languages + options + story")

    result = asyncio.run(dm.register_character(req))
    with Session(dm.engine) as s:
        ch = s.exec(select(dm.Character).where(
            dm.Character.name == "Vashti")).first()
    tags = list((ch.tags if ch else None) or [])
    check("the spells reach the sheet",
          "Fire Bolt" in (ch.spells or []) and "Absorb Elements" in (ch.spells or []),
          ", ".join(ch.spells or []))
    check("…and so do the tools and languages",
          any(t.startswith("tool:") for t in tags)
          and "language: Draconic" in tags)

    print(f"\n{BOLD}2. a keepsake made your own{OFF}")
    if keepsake:
        entry = next((it for it in (ch.inventory or [])
                      if isinstance(it, dict) and it.get("name") == "Kettle-Wind"), None)
        check("the sheet shows the player's name for it", entry is not None,
              str(result.get("wondrous_item")))
        check("…and `base` still points at the catalogue row",
              bool(entry) and entry.get("base") == keepsake["name"],
              str(entry and entry.get("base")))
        check("…so every mechanical lookup still resolves",
              dm._item_base_name(ch, "Kettle-Wind") == keepsake["name"])
        check("…and their words are kept with it",
              bool(entry) and "copper wire" in str((entry.get("art") or {}).get("desc")))

    print(f"\n{BOLD}3. an origin is world state{OFF}")
    check("the backstory is on the sheet",
          "tinkers of the Kettle" in (ch.backstory or ""), (ch.backstory or "")[:40])
    pc = dm.world.find_pc("story-smoke", "Vashti")
    check("the character exists in the world", pc is not None)
    with Session(dm.world.engine) as s:
        fac = s.exec(select(_Ent).where(_Ent.slug == "the-hollow-kettle")).first()
        rels = s.exec(select(_Rel).where(_Rel.src_id == (pc.id if pc else -1))).all()
    kinds = {r.rel_type: r for r in rels}
    check("a people they invented is a REAL faction now",
          fac is not None and fac.type == "faction",
          str(fac and fac.name))
    check("…and they belong to it", RelationType.MEMBER_OF in kinds)
    check("a homeland the world already had ties them to that place",
          RelationType.PART_OF in kinds,
          ", ".join(sorted(kinds)))
    check("…and it is the SAME Greenfields, not a second one",
          len([e for e in Session(dm.world.engine).exec(
              select(_Ent).where(_Ent.name == "Greenfields")).all()]) == 1)

    print()
    if _fails:
        print(f"{RED}{len(_fails)} check(s) failed:{OFF} " + "; ".join(_fails))
        return 1
    print(f"{GREEN}a character walks out of creation with everything they made{OFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
