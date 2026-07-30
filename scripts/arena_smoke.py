"""End-to-end smoke test of the Proving Grounds (arena/) through the backend.

``arena/demo.py`` shows what the Grounds would field. This proves the wiring:
that a slot holds a level-1 character, that a run copy climbs to the chosen
level through the REAL level-up path while the saved character stays untouched,
that a bout seats a rostered encounter on a board of the chosen environment,
that sea and sky bouts actually swim and fly, that the DM prompt is told where
it is, and that victory and defeat are both called correctly.

The LLM is stubbed with canned narration, so this needs no model, no GPU and no
network — just a scratch copy of the database.

    uv run python scripts/arena_smoke.py

It always builds a FRESH scratch database: the backend's startup migrations only
run under the real lifespan, and an empty world is enough to prove the wiring.
"""
from __future__ import annotations

import asyncio
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


def _seed_bestiary(m) -> None:
    """A few stat blocks to roster from — one per medium."""
    from sqlmodel import Session

    from rules.models import Monster

    rows = [
        # slug, name, cr, xp, hp, ac, dex, speed
        ("smoke-goblin", "Smoke Goblin", 0.25, 50, 7, 15, 14, {"walk": "30 ft."}),
        ("smoke-ogre", "Smoke Ogre", 2.0, 450, 59, 11, 8, {"walk": "40 ft."}),
        ("smoke-shark", "Smoke Shark", 0.5, 100, 22, 13, 13, {"swim": "40 ft."}),
        ("smoke-eel", "Smoke Eel", 0.25, 50, 14, 12, 13, {"swim": "30 ft."}),
        ("smoke-crab", "Smoke Crab", 1.0, 200, 30, 15, 10,
         {"walk": "20 ft.", "swim": "30 ft."}),
        ("smoke-eagle", "Smoke Eagle", 0.25, 50, 12, 12, 15,
         {"walk": "10 ft.", "fly": "60 ft."}),
        ("smoke-wyvern", "Smoke Wyvern", 1.0, 200, 40, 13, 12,
         {"walk": "20 ft.", "fly": "80 ft."}),
        ("smoke-imp", "Smoke Imp", 0.5, 100, 10, 13, 17,
         {"walk": "20 ft.", "fly": "40 ft."}),
    ]
    with Session(m.engine) as s:
        for slug, name, cr, xp, hp, ac, dex, speed in rows:
            s.add(Monster(
                index_slug=slug, name=name, size="Medium", type="humanoid",
                challenge_rating=cr, xp=xp, hit_points=hp, hit_points_roll="2d6",
                armor_class=ac, dexterity=dex, strength=12, constitution=12,
                intelligence=8, wisdom=10, charisma=8, speed=speed,
                actions=[{"name": "Slam", "desc": "Melee Weapon Attack: +4 to hit, "
                          "reach 5 ft., one target. Hit: 5 (1d6 + 2) bludgeoning damage."}],
            ))
        s.commit()
    m.rules_lib.refresh_index()
    m._ARENA_CARDS = []                    # force a reload of the bestiary


def _run_socket(m, *, channel: str, user_id: str, script: list[dict]) -> list[dict]:
    """Play a scripted client against the real Activity WebSocket handler.

    ``starlette.testclient`` needs httpx, which this project doesn't carry, so
    the socket itself is ducked: the handler only ever calls ``accept``,
    ``receive_json``, ``send_json`` and reads ``query_params``. Running out of
    script disconnects, which is exactly how a real client leaves.
    """
    from starlette.websockets import WebSocketDisconnect

    class _Socket:
        def __init__(self) -> None:
            self.query_params = {"user_id": user_id, "username": "Smoke"}
            self.inbox = list(script)
            self.sent: list[dict] = []

        async def accept(self) -> None:
            pass

        async def receive_json(self) -> dict:
            if not self.inbox:
                raise WebSocketDisconnect(1000)
            return self.inbox.pop(0)

        async def send_json(self, ev: dict) -> None:
            self.sent.append(ev)

    sock = _Socket()
    asyncio.run(m.activity_ws(sock, channel))
    return sock.sent


def main() -> int:  # noqa: C901 - a smoke test is a straight line by design
    db = os.path.join(tempfile.gettempdir(), "oracle_arena_smoke.db")
    if os.path.exists(db):
        os.remove(db)

    print("\033[1mThe Oracle — Proving Grounds wiring smoke test\033[0m")
    m = _load_backend(db)

    from fastapi import BackgroundTasks
    from sqlmodel import Session, SQLModel

    from arena import ENVIRONMENTS, get_environment
    from vtt.mapgen import ARCHETYPES, generate_map

    SQLModel.metadata.create_all(m.engine)
    from rules.ingest import seed_classes_and_subclasses
    seed_classes_and_subclasses(engine=m.engine)
    _seed_bestiary(m)

    user_id = "arena-smoke-user"
    channel = "smoke-channel"

    # ---- 0. the catalog is honest about itself --------------------------
    print("\n\033[1m0. every environment is a board we can actually build\033[0m")
    bad_arch = [e.slug for e in ENVIRONMENTS.values() if e.archetype not in ARCHETYPES]
    check("every environment names a real layout", not bad_arch, str(bad_arch))
    bad_mode = [e.slug for e in ENVIRONMENTS.values()
                if generate_map(e.archetype, seed=5).mode != e.mode]
    check("and agrees with that layout's medium", not bad_mode, str(bad_mode))

    # ---- 1. a slot holds a level-1 character ---------------------------
    print("\n\033[1m1. three slots, overwritable, level 1\033[0m")

    def make_slot(slot: int, name: str, klass: str = "Fighter") -> int:
        req = m.RegisterCharacterRequest(
            discord_user_id=user_id, name=name, race="Human", char_class=klass,
            background="Soldier", approve=True, source="guided",
            stats={"strength": 16, "dexterity": 14, "constitution": 14,
                   "intelligence": 10, "wisdom": 12, "charisma": 8})
        res = asyncio.run(m.register_character(req))
        cid = res["character_id"]
        with Session(m.engine) as s:
            ch = s.get(m.Character, cid)
            ch.arena_slot = slot
            s.add(ch)
            s.commit()
        return cid

    saved_id = make_slot(1, "Practice Kara")
    slots = m._arena_slots(user_id)
    check("the slot is filled", slots[0]["character"] is not None)
    check("at level 1", (slots[0]["character"] or {}).get("level") == 1)
    check("the other slots are empty",
          slots[1]["character"] is None and slots[2]["character"] is None)
    check("three slots exactly", len(slots) == m.ARENA_MAX_SLOTS)

    # Overwriting a slot replaces what was there — practice is disposable.
    m._arena_clear_slot(user_id, 1)
    check("clearing a slot empties it",
          m._arena_slots(user_id)[0]["character"] is None)
    saved_id = make_slot(1, "Practice Kara")

    # A practice character is not a person in the world.
    check("it stays off the world roster",
          all(c["id"] != saved_id for c in m._activity_characters(user_id, channel)))

    # ---- 2. the run copy climbs; the saved character does not -----------
    print("\n\033[1m2. the climb to the chosen level runs the real level-up\033[0m")
    run_char = m._arena_clone_for_run(user_id, 1)
    check("a run copy exists", run_char is not None and run_char.id != saved_id)
    check("it starts at level 1", run_char is not None and run_char.level == 1)

    target = 4
    with Session(m.engine) as s:
        ch = s.get(m.Character, run_char.id)
        ch.pending_level_up = True
        s.add(ch)
        s.commit()
    applied = 0
    for _ in range(25):
        with Session(m.engine) as s:
            cur = s.get(m.Character, run_char.id).level
        if cur >= target:
            break
        sub = None
        with Session(m.engine) as s:
            prog = m._progression(s, s.get(m.Character, run_char.id),
                                  target_subclass=None, apply=False)
        if prog.get("subclass_options") and (prog.get("report") or {}).get(
                "subclass_choice_due"):
            sub = prog["subclass_options"][0]["slug"]
        # An ASI level won't land until the player spends it — the climb makes
        # the same call a real one does (here: +2 to the primary stat).
        asi = {"str": 2} if prog.get("asi_due") else None
        res = asyncio.run(m.level_up(m.LevelUpRequest(
            character_id=run_char.id, subclass=sub, ability_increases=asi)))
        if res.get("applied"):
            applied += 1
            with Session(m.engine) as s:
                ch = s.get(m.Character, run_char.id)
                if ch.level < target:
                    ch.pending_level_up = True
                    s.add(ch)
                    s.commit()
        elif sub is None and res.get("subclass_options"):
            continue    # the loop re-reads and supplies one
        else:
            break
    with Session(m.engine) as s:
        run_level = s.get(m.Character, run_char.id).level
        saved_level = s.get(m.Character, saved_id).level
        run_hp = s.get(m.Character, run_char.id).max_hp
    check(f"the copy climbed to level {target}", run_level == target,
          f"level {run_level} after {applied} level-ups")
    check("its hit points grew with it", run_hp > 10, f"{run_hp} HP")
    check("the saved character is still level 1", saved_level == 1)

    # ---- 3. a bout seats a rostered fight on the chosen board -----------
    print("\n\033[1m3. a bout: roster, initiative, and a board of the right place\033[0m")
    session_id = m._arena_session_id(channel, user_id)
    m._set_session_meta(session_id, {
        "user_id": user_id, "character_id": run_char.id,
        "character_name": run_char.name, "activity_channel": channel,
        "members": {user_id: {"character_id": run_char.id,
                              "character_name": run_char.name}},
        "arena": {"slot": 1, "character_id": run_char.id, "target_level": target,
                  "environment": "training-yard", "difficulty": "medium",
                  "phase": "leveling", "fights": 0, "wins": 0},
    })
    res = m._arena_open_fight(session_id, user_id)
    check("the bout opened", res.get("ok"), str(res.get("reason")))
    enc = m.combat.get_active(session_id)
    check("an encounter is live", enc is not None)
    order = m.combat.order(enc.id) if enc else []
    check("the fighter is in it", any(c.kind == "pc" for c in order))
    check("and so are the foes", any(c.kind != "pc" for c in order))
    check("initiative is rolled", all(c.initiative for c in order),
          str([(c.name, c.initiative) for c in order]))
    scene = m.vtt_engine.active_scene(session_id)
    check("a board is out", scene is not None)
    check("of the place that was asked for",
          scene is not None and scene.archetype == "arena", str(scene and scene.archetype))
    check("everyone on the board has a token",
          scene is not None and len(m.vtt_engine.tokens(scene.id)) == len(order))

    # ---- 4. the DM is told where it is ---------------------------------
    print("\n\033[1m4. the DM prompt knows this is the Grounds\033[0m")
    scripted: list[str] = []

    def fake_dm(messages):
        m.LAST_PROMPT = "\n".join(x.get("content", "") for x in messages)
        return scripted.pop(0) if scripted else "Steel rings."

    m.call_openrouter_dm = fake_dm
    m._call_extractor_llm = lambda messages: "[]"
    extractions: list = []
    m._run_world_extraction = lambda *a, **k: extractions.append(a)

    def say(text: str, narration: str = "Steel rings."):
        scripted.append(narration)
        bt = BackgroundTasks()
        out = m.chat_endpoint(m.ChatRequest(
            session_id=session_id, user_id=user_id, username="Smoke",
            message=text), bt)
        asyncio.run(bt())
        return out

    say("I set my feet and swing.")
    prompt = getattr(m, "LAST_PROMPT", "")
    check("the Grounds are named in the prompt", "PROVING GROUNDS" in prompt)
    check("with the place it is fighting in", "The Sand Ring" in prompt)
    check("the tactical board is in the prompt", "# Board:" in prompt)
    check("the initiative board too", "# Combat:" in prompt)
    check("no world slice is claimed", "# Where you are" not in prompt)
    check("and nothing is written back to the world", not extractions,
          f"{len(extractions)} extraction(s)")

    # ---- 5. victory is called by the Grounds, not the narration ---------
    print("\n\033[1m5. the bout ends when one side is down\033[0m")
    check("mid-fight there is no result", m._arena_outcome(session_id) is None)
    for c in m.combat.order(enc.id):
        if c.kind != "pc":
            m.combat.apply_damage(c.id, c.max_hp + 10)
    check("with every foe down it reads as victory",
          m._arena_outcome(session_id) == "victory")
    m._arena_finish_fight(session_id, "victory")
    check("the encounter is closed",
          not m.combat.get_encounter(enc.id).active)
    run = m._arena_run(session_id)
    check("the run counted the win", run.get("wins") == 1 and run.get("fights") == 1)
    with Session(m.engine) as s:
        ch = s.get(m.Character, run_char.id)
        check("the fighter is whole again", ch.current_hp == ch.max_hp,
              f"{ch.current_hp}/{ch.max_hp}")

    # ---- 6. another bout, somewhere else, and defeat --------------------
    print("\n\033[1m6. the next bout can be somewhere else entirely\033[0m")
    res = m._arena_open_fight(session_id, user_id, environment="coral-reef",
                              difficulty="hard")
    check("a sea bout opened", res.get("ok"), str(res.get("reason")))
    scene = m.vtt_engine.active_scene(session_id)
    check("on a board under the sea",
          scene is not None and scene.archetype == "reef", str(scene and scene.archetype))
    check("the board knows it is swum",
          scene is not None and m.vtt_engine.board_mode(scene) == "swim")
    modes = {t.movement_mode for t in m.vtt_engine.tokens(scene.id)} if scene else set()
    check("and everything in it swims, the fighter included", modes == {"swim"},
          str(modes))
    foes = [c for c in m.combat.order(m.combat.get_active(session_id).id)
            if c.kind != "pc"]
    swimmers = [c for c in foes
                if "swim" in ((m.rules_lib.get_monster(c.monster_slug).speed or {})
                              if c.monster_slug else {})]
    check("every foe can swim", bool(foes) and len(swimmers) == len(foes),
          str([c.name for c in foes]))

    # …and when nothing native fits, the Grounds say so instead of pretending.
    from arena import build_roster
    landlocked = [c for c in m._arena_cards() if not c.moves("swim")]
    conjured = build_roster(get_environment("coral-reef"), 3, landlocked)
    check("a bout with no native creatures is marked conjured",
          conjured is not None and conjured.conjured)

    enc2 = m.combat.get_active(session_id)
    for c in m.combat.order(enc2.id):
        if c.kind == "pc":
            m.combat.apply_damage(c.id, c.max_hp + 10)
    check("a downed fighter reads as defeat",
          m._arena_outcome(session_id) == "defeat")
    m._arena_finish_fight(session_id, "defeat")
    with Session(m.engine) as s:
        ch = s.get(m.Character, run_char.id)
        check("who is put back on their feet for the next one",
              ch.current_hp == ch.max_hp and ch.level == target)
    check("the loss did not count as a win", m._arena_run(session_id)["wins"] == 1)

    # ---- 7. a sky bout flies -------------------------------------------
    print("\n\033[1m7. a bout in open air\033[0m")
    res = m._arena_open_fight(session_id, user_id, environment="sky-islands",
                              difficulty="easy")
    check("a sky bout opened", res.get("ok"), str(res.get("reason")))
    scene = m.vtt_engine.active_scene(session_id)
    modes = {t.movement_mode for t in m.vtt_engine.tokens(scene.id)} if scene else set()
    check("everything in it flies", modes == {"fly"}, str(modes))
    if scene is not None:
        tok = next((t for t in m.vtt_engine.tokens(scene.id) if t.kind == "pc"), None)
        opts = m.vtt_engine.movement_options(tok.id) if tok else {"squares": []}
        check("the fighter can actually move up there",
              len(opts.get("squares") or []) > 0)

    # ---- 8. walking out leaves nothing running -------------------------
    print("\n\033[1m8. leaving the Grounds\033[0m")
    m._arena_leave(session_id)
    check("no board is out", m.vtt_engine.active_scene(session_id) is None)
    check("no fight is running", m.combat.get_active(session_id) is None)
    check("the world never heard about any of it", not extractions)

    # ---- 9. the same thing over the Activity's own socket ---------------
    # Everything above drives the engine directly. This drives the WebSocket
    # handler the browser talks to, so the glue is proven too.
    print("\n\033[1m9. over the Activity socket, as the client speaks it\033[0m")
    sent = _run_socket(m, channel="ws-smoke", user_id=user_id, script=[
        {"t": "arena_state"},
        {"t": "arena_create", "slot": 3, "payload": {
            "name": "Socket Sable", "race": "Human", "char_class": "Fighter",
            "background": "Soldier",
            "stats": {"strength": 16, "dexterity": 14, "constitution": 14,
                      "intelligence": 10, "wisdom": 12, "charisma": 8}}},
        {"t": "arena_begin", "slot": 3, "environment": "ship-deck",
         "level": 2, "difficulty": "easy"},
        {"t": "levelup_apply"},
        {"t": "arena_leave"},
    ])
    kinds = [e["t"] for e in sent]
    arenas = [e["state"] for e in sent if e["t"] == "arena"]
    check("the socket answers arena_state with the catalog",
          bool(arenas) and len(arenas[0]["environments"]) > 0)
    check("creating fills the slot over the wire",
          any(a["slots"][2]["character"] for a in arenas),
          str([bool(a["slots"][2]["character"]) for a in arenas]))
    check("beginning a bout enters the Grounds",
          any(e["t"] == "entered" and e.get("arena") for e in sent), str(kinds))
    levelups = [e for e in sent if e["t"] == "levelup"]
    check("the level-up overlay is pushed for the climb",
          any(e.get("data") for e in levelups), str(len(levelups)))
    check("the last level-up opens the bout by itself",
          any(e["t"] == "narration" and "wards close" in (e.get("text") or "")
              for e in sent),
          str([e.get("text", "")[:40] for e in sent if e["t"] == "narration"]))
    check("the board is pushed to the client",
          any(e["t"] == "vtt" and e.get("scene") for e in sent))
    check("and the initiative order with it",
          any(e["t"] == "combat" and e.get("encounter") for e in sent))
    final = arenas[-1] if arenas else {}
    check("leaving ends the run", (final.get("run") or {}).get("phase") == "idle",
          str((final.get("run") or {}).get("phase")))

    print()
    if FAILS:
        print(f"\033[31m{len(FAILS)} check(s) failed:\033[0m " + ", ".join(FAILS))
        return 1
    print("\033[32mthe whole loop holds\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
