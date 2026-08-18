"""Unfinished business — the half of a backstory the DM can actually use.

Offline, no GPU, no LLM. A scratch DB carrying a copy of the real rules tables,
so registration runs the real path.

What it pins:

1. A thread survives creation as WORLD STATE, not prose: a real place with real
   coordinates, and — where the kind asks for one — a real person standing at
   it, both hung off the PC by an UNRESOLVED edge.
2. Anchors are placed from the CHARACTER's own seed, not beside where they
   start. This is the scale rule: a hundred backstories must scatter around the
   world and seed the map outward, or a hundred ruins pile on the starting
   village. Deterministic, and clear of what already exists.
3. Reach bands mean something — a debt is days away and a blood feud is a
   season away, so "go and settle up" and "go and find them" are visibly
   different offers.
4. The DM is told, and only when asked. The block is gated on the player
   casting about (or naming their own past); an ordinary turn carries none of
   it, which is the whole bargain — a DM who keeps raising your dead village is
   running your character for you.
5. Resolving one CLOSES it, so the world stops offering it. This is why a
   thread is state and not a paragraph: prose goes on saying the village is
   burned long after the party rebuilt it.

Usage:  uv run python scripts/threads_smoke.py
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
    path = os.path.join(tempfile.gettempdir(), "oracle_threads.db")
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
    os.environ.setdefault("ORACLE_IMAGERY_ENABLED", "0")
    spec = importlib.util.spec_from_file_location(
        "dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
    dm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dm)

    from sqlmodel import Session, SQLModel, select
    SQLModel.metadata.create_all(dm.engine)
    from eight_card_system.seed import seed_starter_world
    from eight_card_system.models import (Entity as _Ent, Relation as _Rel,
                                          RelationType, EntityType)
    from eight_card_system import geo, threads as th
    seed_starter_world(dm.world)

    opts = dm.cc_options()
    bg = opts["backgrounds"][0]["slug"]

    # ---------------------------------------------------------------
    print(f"\n{BOLD}1. a thread walks out of creation as world state{OFF}")
    payload = {
        "name": "Kara Emberfall", "race": "Human", "char_class": "Fighter",
        "background": bg,
        "stats": {"strength": 15, "dexterity": 13, "constitution": 14,
                  "intelligence": 10, "wisdom": 12, "charisma": 8},
        "backstory": "She was three days' walk away when the smoke went up.",
        "threads": [
            {"kind": "lost-home",
             "summary": "the village burned while I was away",
             "place": "Ashmere"},
            {"kind": "vengeance",
             "summary": "the captain who put my village to the torch",
             "subject": "Captain Vurn"},
            {"kind": "debt", "summary": "an oath I swore and walked away from",
             "subject": "The Grey Wardens"},
        ],
    }
    req = dm._cc_request("threads-smoke", payload)
    check("the payload carries them", len(req.threads or []) == 3,
          ", ".join(t["kind"] for t in (req.threads or [])))
    asyncio.run(dm.register_character(req))

    pc = dm.world.find_pc("threads-smoke", "Kara Emberfall")
    check("the character exists in the world", pc is not None)
    open_ = th.open_threads_for(dm.world, pc.slug)
    check("all three are open", len(open_) == 3,
          ", ".join(t["kind"] for t in open_))

    with Session(dm.world.engine) as s:
        ash = s.exec(select(_Ent).where(_Ent.name == "Ashmere")).first()
        vurn = s.exec(select(_Ent).where(_Ent.name == "Captain Vurn")).first()
    check("the place they named is a REAL place",
          ash is not None and ash.type == EntityType.PLACE,
          str(ash and ash.subtype))
    check("…with real coordinates, so the journey can be costed",
          ash is not None and geo.coords_from_attrs(ash.attributes) is not None,
          str(ash and (ash.attributes or {}).get("coords")))
    check("…and unexplored, because the world knows and the player does not",
          ash is not None and ash.status == "unexplored", str(ash and ash.status))
    check("a named person is a REAL npc standing there",
          vurn is not None and vurn.type == EntityType.NPC)

    # ---------------------------------------------------------------
    print(f"\n{BOLD}2. anchors scatter — they are not laid beside the party{OFF}")
    home = dm.world.get_entity("greenfields") or dm.world.get_entity("the-silver-tankard")
    hc = geo.coords_from_attrs(home.attributes) if home else None
    if hc is None:
        hc = (geo.ORIGIN_LAT, geo.ORIGIN_LON)
    dists = {t["kind"]: t["miles"] for t in open_}
    check("none of them landed on top of the starting village",
          all(v >= 40 for v in dists.values()), str(dists))
    bearings = {t["kind"]: t["bearing"] for t in open_}
    check("…and they do not all lie the same way",
          len(set(bearings.values())) > 1, str(bearings))

    # Determinism: the same character asked twice gets the same map.
    k = th.kind("lost-home")
    a1 = th.anchor_coords(dm.world, pc.slug, k)[0]
    a2 = th.anchor_coords(dm.world, pc.slug, k)[0]
    check("a retry lays the same map, not a second ruin", a1 == a2,
          f"{a1[0]:.3f},{a1[1]:.3f}")

    # A second character's threads land somewhere else entirely.
    p2 = dict(payload, name="Doran Vale")
    asyncio.run(dm.register_character(dm._cc_request("threads-smoke-2", p2)))
    pc2 = dm.world.find_pc("threads-smoke-2", "Doran Vale")
    o2 = th.open_threads_for(dm.world, pc2.slug)
    far = min(
        geo.distance_mi(
            geo.coords_from_attrs(dm.world.get_entity(a["place_slug"]).attributes),
            geo.coords_from_attrs(dm.world.get_entity(b["place_slug"]).attributes))
        for a in open_ for b in o2)
    check("another character's past is somewhere else", far >= th.ANCHOR_CLEARANCE_MI,
          f"nearest pair {far:.0f} mi apart")

    # ---------------------------------------------------------------
    print(f"\n{BOLD}3. reach bands make different offers{OFF}")
    check("a debt is days away, a blood feud a season",
          dists.get("debt", 0) < dists.get("vengeance", 0),
          f"debt {dists.get('debt')} mi vs vengeance {dists.get('vengeance')} mi")
    lines = th.hook_lines(dm.world, pc.slug, "Kara")
    check("the DM gets a line per thread", len(lines) == 3)
    check("…naming the PLACE, not the player's sentence",
          any("Ashmere" in ln for ln in lines),
          next((ln for ln in lines if "Ashmere" in ln), "")[:78])
    check("…quoting the player's own words rather than speaking them",
          all('Their words: "' in ln for ln in lines))
    check("…and no coordinates leak into it",
          not any(("lat" in ln or "lon" in ln or "°" in ln) for ln in lines))

    # ---------------------------------------------------------------
    print(f"\n{BOLD}4. the DM is told only when somebody asks{OFF}")
    asked = any(k in "what should we do next?" for k in dm._THREAD_KEYWORDS)
    quiet = any(k in "i swing at the goblin" for k in dm._THREAD_KEYWORDS)
    check("'what should we do next?' opens it", asked)
    check("…and an ordinary turn does not", not quiet)
    check("naming their own ruin opens it too",
          th.mentions_thread(left_before := th.open_threads_for(dm.world, pc.slug),
                             "do we know anything about Ashmere?"))
    check("…or the person they are hunting",
          th.mentions_thread(left_before, "ask around about Captain Vurn"))
    check("…but somebody ELSE's past does not open yours",
          not th.mentions_thread(left_before, "we ride for Waterdeep at dawn"))
    check("…and a short name never fires on a word that contains it",
          not th.mentions_thread([{"place": "Ford", "subject": None}],
                                 "we cannot afford the toll"))

    # ---------------------------------------------------------------
    print(f"\n{BOLD}5. settling one closes it{OFF}")
    clean, ops = dm.extract_thread_hooks(
        "The rooftrees are up again. [[THREAD: resolve | lost-home | "
        "the village is rebuilt]]")
    check("the hook parses", len(ops) == 1 and ops[0]["action"] == "resolve",
          str(ops))
    check("…and is pulled out of the narration", "[[" not in clean, clean[:40])
    closed = th.resolve_thread(dm.world, pc.slug, "lost-home", "rebuilt")
    check("the edge is closed", closed >= 1, f"{closed} edge(s)")
    left = th.open_threads_for(dm.world, pc.slug)
    check("…so the world stops offering it",
          "lost-home" not in {t["kind"] for t in left},
          ", ".join(t["kind"] for t in left))
    check("…and the others are untouched", len(left) == 2)
    with Session(dm.world.engine) as s:
        still = s.exec(select(_Ent).where(_Ent.name == "Ashmere")).first()
    check("the place itself survives — it is somewhere now", still is not None)

    # ---------------------------------------------------------------
    print(f"\n{BOLD}6. hitching to history the world already made{OFF}")
    # A village burns in play, with a recorded population, and a tiefling
    # quarter burns elsewhere. Both are real world state, not backstory.
    from eight_card_system import geo as _geo
    thorn = dm.world.create_entity(
        "Thornwick", EntityType.PLACE, subtype="settlement", status="destroyed",
        attributes={"description": "A river village.",
                    "coords": _geo.coords_attr(45.9, 1.2)})
    for nm, race in (("Alder Fenn", "Human"), ("Sylwen", "Elf (Wood Elf)")):
        n = dm.world.create_entity(nm, EntityType.NPC, attributes={"race": race})
        dm.world.add_relation(n.slug, RelationType.LOCATED_IN, thorn.slug)
    ember = dm.world.create_entity(
        "Emberrow", EntityType.PLACE, subtype="settlement", status="destroyed",
        attributes={"description": "A cliffside quarter.",
                    "coords": _geo.coords_attr(44.2, -1.9)})
    vk = dm.world.create_entity("Vashka", EntityType.NPC, attributes={"race": "Tiefling"})
    dm.world.add_relation(vk.slug, RelationType.LOCATED_IN, ember.slug)

    cands = th.candidates_for(dm.world, "lost-home")
    names = [c["name"] for c in cands]
    check("a place the world destroyed is offered as an anchor",
          "Thornwick" in names and "Emberrow" in names, ", ".join(names))
    check("…and it says WHY, in the world's own words",
          all(c["why"] for c in cands), cands[0]["why"] if cands else "")

    tf = th.candidates_for(dm.world, "lost-home", species="Tiefling")
    check("a tiefling is offered the tiefling quarter FIRST",
          tf and tf[0]["name"] == "Emberrow", " > ".join(c["name"] for c in tf))
    odd = next((c for c in tf if c["name"] == "Thornwick"), None)
    check("…the elf-and-human village is still offered, not hidden", odd is not None)
    check("…but marked, and it names the people",
          bool(odd) and odd["fit"] == "outsider"
          and "elves" in odd["fit_note"] and "humans" in odd["fit_note"],
          str(odd and odd["fit_note"]))
    we = th.candidates_for(dm.world, "lost-home", species="Elf (Wood Elf)")
    check("…and a wood elf gets the opposite order",
          we and we[0]["name"] == "Thornwick", " > ".join(c["name"] for c in we))
    he = th.candidates_for(dm.world, "lost-home", species="Half-Elf")
    check("a half-elf is at home among elves", 
          he and he[0]["name"] == "Thornwick", " > ".join(c["name"] for c in he))

    # The free ride: adopting one creates NO new place.
    with Session(dm.world.engine) as s:
        before = len(s.exec(select(_Ent).where(_Ent.type == EntityType.PLACE)).all())
    p3 = dict(payload, name="Kessa Dree",
              threads=[{"kind": "lost-home", "summary": "I was away when it burned",
                        "existing": "emberrow"}])
    asyncio.run(dm.register_character(dm._cc_request("threads-smoke-3", p3)))
    with Session(dm.world.engine) as s:
        after = len(s.exec(select(_Ent).where(_Ent.type == EntityType.PLACE)).all())
    check("adopting one costs the world NO new place", after == before,
          f"{before} -> {after}")
    pc3 = dm.world.find_pc("threads-smoke-3", "Kessa Dree")
    own3 = th.open_threads_for(dm.world, pc3.slug)
    check("…and the thread points at the real one",
          len(own3) == 1 and own3[0]["place"] == "Emberrow",
          str(own3 and own3[0]["place"]))
    check("…so the DM's line names a place with history",
          any("Emberrow" in ln for ln in th.hook_lines(dm.world, pc3.slug, "Kessa")))

    # Two characters out of the same ruin is the POINT, not a collision.
    p4 = dict(payload, name="Ordo Vane",
              threads=[{"kind": "lost-home", "summary": "my whole street went up",
                        "existing": "emberrow"}])
    asyncio.run(dm.register_character(dm._cc_request("threads-smoke-4", p4)))
    pc4 = dm.world.find_pc("threads-smoke-4", "Ordo Vane")
    check("two characters can share one ruin",
          th.open_threads_for(dm.world, pc4.slug)[0]["place"] == "Emberrow")
    th.resolve_thread(dm.world, pc3.slug, "lost-home", "rebuilt")
    check("…and settling one does not settle the other's",
          len(th.open_threads_for(dm.world, pc4.slug)) == 1
          and len(th.open_threads_for(dm.world, pc3.slug)) == 0)

    print()
    if _fails:
        print(f"{RED}{len(_fails)} check(s) failed:{OFF} " + "; ".join(_fails))
        return 1
    print(f"{GREEN}a backstory the DM can actually offer back{OFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
