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
    # The beat between creatures is a WATCHING thing; a test wants the order
    # and the split, not four seconds of theatre.
    os.environ.setdefault("ORACLE_COMBAT_STEP_PAUSE", "0")
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


def _seed_stall(m) -> None:
    """A handful of priced items for the Quartermaster to sell."""
    from sqlmodel import Session

    from rules.models import Item

    rows = [
        # mundane gear the stall prices from the catalog itself
        dict(index_slug="smoke-chain-mail", name="Smoke Chain Mail",
             category="armor", item_type="Heavy", cost_gp=75.0,
             armor_class_base=16, armor_dex_bonus=False),
        dict(index_slug="smoke-shield", name="Smoke Shield", category="armor",
             item_type="Shield", cost_gp=10.0, armor_class_base=2),
        dict(index_slug="smoke-rope", name="Smoke Rope",
             category="adventuring-gear", item_type="Standard Gear", cost_gp=1.0),
        dict(index_slug="smoke-unpriced", name="Smoke Curio",
             category="adventuring-gear", item_type="Standard Gear"),
        # magic, priced by the Grounds' own fee and gated by rarity
        dict(index_slug="smoke-cloak", name="Smoke Cloak of Protection",
             category="magic-item", item_type="Wondrous Item", rarity="uncommon",
             requires_attunement=True, desc="+1 to AC and saving throws."),
        dict(index_slug="smoke-ring", name="Smoke Ring of Protection",
             category="magic-item", item_type="Ring", rarity="rare",
             requires_attunement=True, desc="+1 to AC and saving throws."),
        dict(index_slug="smoke-band", name="Smoke Band of Protection",
             category="magic-item", item_type="Ring", rarity="rare",
             requires_attunement=True, desc="+1 to AC and saving throws."),
        dict(index_slug="smoke-crown", name="Smoke Crown", category="magic-item",
             item_type="Wondrous Item", rarity="legendary",
             requires_attunement=True, desc="A crown of the old kings."),
        dict(index_slug="smoke-relic", name="Smoke Relic", category="magic-item",
             item_type="Wondrous Item", rarity="artifact",
             desc="Never for sale at any price."),
    ]
    with Session(m.engine) as s:
        for row in rows:
            s.add(Item(**row))
        s.commit()
    m.rules_lib.refresh_index()
    m._ARENA_STOCK = {}                    # force a reload of the stall


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
    _seed_stall(m)

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

    # ---- 3. the Quartermaster: a stipend, a stall, and what gets strapped on ----
    print("\n\033[1m3. the Quartermaster stands between the gate and the sand\033[0m")
    session_id = m._arena_session_id(channel, user_id)
    m._set_session_meta(session_id, {
        "user_id": user_id, "character_id": run_char.id,
        "character_name": run_char.name, "activity_channel": channel,
        "members": {user_id: {"character_id": run_char.id,
                              "character_name": run_char.name}},
        "arena": {"slot": 1, "character_id": run_char.id, "target_level": target,
                  "environment": "training-yard", "difficulty": "medium",
                  "phase": "leveling", "fights": 0, "wins": 0,
                  "cart": [], "spent": 0.0},
    })
    from arena import loadout as _loadout

    check("the stipend grows with the level fought at",
          _loadout.purse_for(2) < _loadout.purse_for(11) < _loadout.purse_for(20))
    check("level 1 is paid what creation would have paid",
          _loadout.purse_for(1, class_gold=125) == 125)

    m._arena_open_stall(session_id)
    check("the stall opens as its own phase",
          m._arena_run(session_id).get("phase") == "outfitting")
    shop = m._arena_state(user_id, session_id).get("shop") or {}
    stock_names = {i["name"] for i in shop.get("items") or []}
    check("the state carries the board while it is open", bool(stock_names))
    check("with a purse for this level",
          shop.get("purse") == _loadout.purse_for(target), str(shop.get("purse")))
    check("mundane gear is priced from the catalog",
          any(i["name"] == "Smoke Chain Mail" and i["cost_gp"] == 75.0
              for i in shop["items"]))
    check("magic is priced by the Grounds' own fee",
          any(i["name"] == "Smoke Cloak of Protection"
              and i["cost_gp"] == _loadout.MAGIC_PRICE_BY_RARITY["uncommon"]
              for i in shop["items"]))
    check("rarity is gated by the level being fought at",
          "Smoke Ring of Protection" not in stock_names
          and "Smoke Crown" not in stock_names, str(sorted(stock_names)))
    check("an artifact is never for sale", "Smoke Relic" not in stock_names)
    check("and unpriced gear is never silently free",
          "Smoke Curio" not in stock_names)
    check("what the fighter already owns is offered to strap on",
          any(p["name"] for p in shop.get("pack") or []),
          str([p["name"] for p in shop.get("pack") or []]))

    with Session(m.engine) as s:
        ch = s.get(m.Character, run_char.id)
        ac_before = m._compute_ac(ch)
        kit_names = {i["name"] for i in m._inventory_items(ch)}
    res = m._arena_apply_loadout(session_id, [
        {"slug": "smoke-chain-mail", "name": "Smoke Chain Mail",
         "quantity": 1, "equipped": True},
        {"slug": "smoke-shield", "name": "Smoke Shield",
         "quantity": 1, "equipped": True},
        {"slug": "smoke-rope", "name": "Smoke Rope", "quantity": 2},
        {"slug": "smoke-crown", "name": "Smoke Crown",   # gated out at level 4
         "quantity": 1},
    ], [])
    check("the loadout applied", res.get("ok"), str(res.get("reason")))
    check("the cart is priced by the server",
          res.get("spent") == 75.0 + 10.0 + 2.0, str(res.get("spent")))
    check("and what the level can't buy is refused, not granted",
          any("Smoke Crown" in r for r in res.get("rejected") or []),
          str(res.get("rejected")))
    with Session(m.engine) as s:
        ch = s.get(m.Character, run_char.id)
        inv = {i["name"]: i for i in m._inventory_items(ch)}
        ac_after = m._compute_ac(ch)
    check("the gear is in the pack", "Smoke Chain Mail" in inv and "Smoke Rope" in inv)
    check("quantities carry", inv.get("Smoke Rope", {}).get("quantity") == 2)
    check("worn gear is worn", inv.get("Smoke Chain Mail", {}).get("equipped") is True)
    check("and the armor actually changes the fighter's AC",
          ac_after > ac_before, f"{ac_before} → {ac_after}")

    # Re-outfitting is a clean slate: the conjured coin was never real, so the
    # whole stipend is there again — a level-4 purse buys the uncommon cloak
    # only if the plate goes back on the shelf.
    res2 = m._arena_apply_loadout(session_id, [
        {"slug": "smoke-cloak", "name": "Smoke Cloak of Protection",
         "quantity": 1, "attuned": True},
    ], [])
    with Session(m.engine) as s:
        ch = s.get(m.Character, run_char.id)
        inv2 = {i["name"]: i for i in m._inventory_items(ch)}
    check("re-outfitting takes the last loadout back",
          "Smoke Chain Mail" not in inv2 and "Smoke Cloak of Protection" in inv2,
          str(sorted(inv2)))
    check("and refunds it in full — the whole purse is there again",
          res2.get("spent") == 600.0 == float(shop["purse"]), str(res2.get("spent")))
    check("attuned gear is attuned",
          inv2.get("Smoke Cloak of Protection", {}).get("attuned") is True)
    check("the fighter's own kit is never taken back",
          kit_names <= set(inv2), str(sorted(kit_names - set(inv2))))

    # The attunement limit is the rules' limit, not a suggestion.
    stock20 = m._arena_stock(20)
    priced = _loadout.price_cart(
        [{"slug": "smoke-cloak", "attuned": True},
         {"slug": "smoke-ring", "attuned": True},
         {"slug": "smoke-band", "attuned": True},
         {"slug": "smoke-crown", "attuned": True}],
        stock20, 200000)
    check("no one attunes to more than three things",
          sum(1 for ln in priced.lines if ln["attuned"]) == _loadout.ATTUNEMENT_LIMIT,
          str([(ln["name"], ln["attuned"]) for ln in priced.lines]))
    check("but the fourth is still bought, just not attuned",
          len(priced.lines) == 4)
    broke = _loadout.price_cart([{"slug": "smoke-crown", "quantity": 1}],
                                stock20, 100)
    check("a cart bigger than the purse is clamped, not honoured",
          not broke.lines and broke.rejected)
    unknown = _loadout.price_cart([{"name": "A Sword Of My Own Invention"}],
                                  stock20, 100)
    check("and an item the stall never stocked is refused",
          not unknown.lines and unknown.rejected)

    # Put the fighter back in fighting shape for the bouts below.
    m._arena_apply_loadout(session_id, [
        {"slug": "smoke-chain-mail", "quantity": 1, "equipped": True},
    ], [])

    # ---- 4. a bout seats a rostered fight on the chosen board -----------
    print("\n\033[1m4. a bout: roster, initiative, and a board of the right place\033[0m")
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

    # ---- 5. the DM is told where it is ---------------------------------
    print("\n\033[1m5. the DM prompt knows this is the Grounds\033[0m")
    scripted: list[str] = []

    def fake_dm(messages, max_tokens=None):
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
    check("the player's line is sent ONCE, not twice",
          prompt.count("Smoke: I set my feet and swing.") == 1,
          f"{prompt.count('Smoke: I set my feet and swing.')}x")

    # ---- 5b. a turn the ENGINE settled is a DESCRIPTION job -------------
    #
    # Measured on a real Eldritch Blast: the ordinary DM prompt for a resolved
    # turn was 45,158 chars, of which ~4,000 was the board, the certified
    # result and the contract, and the rest was instruction for things the
    # narration contract three blocks up forbids — 10,108 chars of it teaching
    # the model to move tokens on a turn where it may change nothing at all.
    # Prompt ingestion is roughly linear, so that was most of the wait a player
    # reads as "the spell didn't work".
    print("\n\033[1m5b. the prompt for a resolved turn is a narrator's, not a DM's\033[0m")
    full = prompt
    enc_now = m.combat.get_active(session_id)
    foe_now = next((c for c in m.combat.order(enc_now.id) if c.kind != "pc"),
                   None) if enc_now else None
    scripted.append("The blade bites.")
    bt2 = BackgroundTasks()
    m.chat_endpoint(m.ChatRequest(
        session_id=session_id, user_id=user_id, username="Smoke",
        message="I cut at it.",
        intents=[{"verb": "attack",
                  "target": foe_now.name if foe_now else ""}]), bt2)
    asyncio.run(bt2())
    lean = getattr(m, "LAST_PROMPT", "")
    check("it is a fraction of the size", 0 < len(lean) < len(full) // 3,
          f"{len(lean)} chars vs {len(full)}")
    check("...and still carries what happened",
          "# Combat resolution" in lean and "Narration contract" in lean)
    check("...and where everyone is standing", "# Board:" in lean)
    # NB the BOARD block legitimately names a hook or two in its legend (the
    # remedy for a square a creature's medium forbids). What must be gone is
    # the 10,108-character VOCABULARY that teaches the model to move tokens.
    check("...but not the hook vocabulary it may not use",
          "# Tactical board (a board is out" not in lean,
          "the tactical hook block is 10k chars")
    check("...nor the resources the engine already spent",
          "# Character resources" not in lean)

    # ---- 6. victory is called by the Grounds, not the narration ---------
    print("\n\033[1m6. the bout ends when one side is down\033[0m")
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

    # ---- 7. another bout, somewhere else, and defeat --------------------
    print("\n\033[1m7. the next bout can be somewhere else entirely\033[0m")
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

    # ---- 8. a sky bout flies -------------------------------------------
    print("\n\033[1m8. a bout in open air\033[0m")
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

    # ---- 9. walking out leaves nothing running -------------------------
    print("\n\033[1m9. leaving the Grounds\033[0m")
    m._arena_leave(session_id)
    check("no board is out", m.vtt_engine.active_scene(session_id) is None)
    check("no fight is running", m.combat.get_active(session_id) is None)
    check("the world never heard about any of it", not extractions)

    # ---- 10. the same thing over the Activity's own socket ---------------
    # Everything above drives the engine directly. This drives the WebSocket
    # handler the browser talks to, so the glue is proven too.
    print("\n\033[1m10. over the Activity socket, as the client speaks it\033[0m")
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
        {"t": "arena_outfit",
         "cart": [{"slug": "smoke-shield", "quantity": 1, "equipped": True}],
         "equip": []},
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
    check("the last level-up opens the stall, not the gate",
          any(e["t"] == "narration" and "Quartermaster" in (e.get("text") or "")
              for e in sent),
          str([e.get("text", "")[:40] for e in sent if e["t"] == "narration"]))
    check("and the stall's board rides along with the state",
          any((a.get("shop") or {}).get("items") for a in arenas))
    check("outfitting opens the bout",
          any(e["t"] == "narration" and "wards close" in (e.get("text") or "")
              for e in sent),
          str([e.get("text", "")[:40] for e in sent if e["t"] == "narration"]))
    check("carrying what was bought into it",
          any(e["t"] == "narration" and "Smoke Shield" in (e.get("text") or "")
              for e in sent))
    check("the board is pushed to the client",
          any(e["t"] == "vtt" and e.get("scene") for e in sent))
    check("and the initiative order with it",
          any(e["t"] == "combat" and e.get("encounter") for e in sent))
    final = arenas[-1] if arenas else {}
    check("leaving ends the run", (final.get("run") or {}).get("phase") == "idle",
          str((final.get("run") or {}).get("phase")))

    # ---- 11. a LEVEL-1 bout: the stall, then the sand ---------------------
    # The path a player takes first, and the one the climb tests never walk:
    # no level-up, so the stall opens straight out of `arena_begin`. It froze
    # there — the loadout was taken, the fight failed to open, and the panel
    # sat saying "the wards close" because the socket had died under it.
    print("\n\033[1m11. level 1: buy a couple of things and step through\033[0m")
    sent = _run_socket(m, channel="lvl1-smoke", user_id=user_id, script=[
        {"t": "arena_create", "slot": 2, "payload": {
            "name": "Sand Novice", "race": "Human", "char_class": "Fighter",
            "background": "Soldier",
            "stats": {"strength": 16, "dexterity": 14, "constitution": 14,
                      "intelligence": 10, "wisdom": 12, "charisma": 8}}},
        {"t": "arena_begin", "slot": 2, "environment": "training-yard",
         "level": 1, "difficulty": "easy"},
        # Bought, not spent out: money left in the purse is not a reason to stall.
        {"t": "arena_outfit",
         "cart": [{"slug": "smoke-shield", "quantity": 1, "equipped": True}],
         "equip": []},
    ])
    lvl1 = [e["state"] for e in sent if e["t"] == "arena"]
    check("a level-1 run opens the stall with no climb first",
          any((a.get("run") or {}).get("phase") == "outfitting" for a in lvl1))
    check("stepping through the gate starts the bout",
          (lvl1[-1].get("run") or {}).get("phase") == "fighting" if lvl1 else False,
          str((lvl1[-1].get("run") or {}).get("phase") if lvl1 else None))
    check("...and the socket lived to say so",
          any(e["t"] == "combat" and e.get("encounter") for e in sent))
    # A fight whose first initiative belongs to a monster used to sit there:
    # the board said "Cultist 1's turn" and the cultist never moved, because
    # the only thing that ran monsters was the player acting out of turn.
    last_combat = [e for e in sent if e["t"] == "combat" and e.get("encounter")]
    enc_state = last_combat[-1]["encounter"] if last_combat else {}
    combs = enc_state.get("combatants") or []
    turn_i = enc_state.get("turn_index")
    up = combs[turn_i] if isinstance(turn_i, int) and 0 <= turn_i < len(combs) else None
    check("the bout opens on the PLAYER's turn, whoever won initiative",
          up is not None and up.get("kind") == "pc",
          f"{up and up.get('name')} ({up and up.get('kind')})")

    # ---- the ENGINE's own log, apart from the narration and ahead of it ----
    #
    # Bundled with the prose, a resolved turn waits on a model to describe it —
    # which is why a spell that had already hit looked like it had not gone
    # off. And a whole side resolving into ONE message is a diff, not a round
    # of combat: each creature's turn is pushed on its own.
    print("\n\033[1m11b. the engine reports each turn as it resolves\033[0m")
    sid = m._arena_session_id("lvl1-smoke", user_id)
    enc = m.combat.get_active(sid)
    seen: list[dict] = []
    if enc is not None:
        # Wind the order round to a monster: the whole point is the turns the
        # PLAYER does not take.
        for _ in range(len(m.combat.order(enc.id))):
            cur = m.combat.current_combatant(enc.id)
            if cur is not None and cur.kind != "pc":
                break
            m.combat.next_turn(enc.id)
        tok = m._ACTIVITY_COMBAT.set(seen.append)
        try:
            m._combat_npc_catchup(sid)
        finally:
            m._ACTIVITY_COMBAT.reset(tok)
    check("each resolved turn is pushed on its own", len(seen) >= 1,
          f"{len(seen)} entries: {[e['actor'] for e in seen]}")
    check("...naming whose turn it was", all(e.get("actor") for e in seen))
    check("...one creature per entry, never a side at a time",
          len({e["actor"] for e in seen if e["kind"] != "note"})
          == len([e for e in seen if e["kind"] != "note"]),
          ", ".join(f"{e['actor']}({e['kind']})" for e in seen))
    check("...and it is the ENGINE's text, not a model's",
          all(any(ln.split(":")[0].isupper()
                  for ln in (e["text"] or "").splitlines() if ln.strip())
              for e in seen))
    check("a table with nobody watching pays nothing",
          m._combat_step("X", "pc", 1, "ATTACK: x") is None)

    # ---- 12. the schema self-heals for EVERY model column -----------------
    # `create_all` never ALTERs an existing table, so a column added to a model
    # simply never reached a database that already had that table — and the
    # failure surfaces at an INSERT deep inside a feature. Two had been missing
    # for months: no fight could start at all, in the world or in the Grounds.
    print("\n\033[1m12. a column added to a model reaches an old database\033[0m")
    from sqlmodel import SQLModel as _SQLModel
    with m.engine.begin() as conn:
        conn.exec_driver_sql(
            "ALTER TABLE combat_combatant DROP COLUMN awareness")
        gone = {r[1] for r in conn.exec_driver_sql(
            'PRAGMA table_info("combat_combatant")')}
    check("a database can be missing a column the model declares",
          "awareness" not in gone)
    asyncio.run(_boot_lifespan(m))
    with m.engine.begin() as conn:
        back = {r[1] for r in conn.exec_driver_sql(
            'PRAGMA table_info("combat_combatant")')}
    check("startup puts it back", "awareness" in back)
    missing = {}
    with m.engine.begin() as conn:
        for tname, table in _SQLModel.metadata.tables.items():
            have = {r[1] for r in conn.exec_driver_sql(
                f'PRAGMA table_info("{tname}")')}
            if not have:
                continue
            gap = {c.name for c in table.columns} - have
            if gap:
                missing[tname] = sorted(gap)
    check("and NO table is short of a column its model declares",
          not missing, str(missing))

    # ---- 13. one bad message does not take the socket down ----------------
    # A dead socket looks to a player like a frozen screen, with no error, on
    # whatever panel they were holding.
    print("\n\033[1m13. a handler that throws does not end the table\033[0m")
    real_state = m._arena_state
    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("a handler blew up")
        return real_state(*a, **k)

    m._arena_state = _boom
    try:
        sent = _run_socket(m, channel="boom-smoke", user_id=user_id, script=[
            {"t": "arena_state"},        # this one throws
            {"t": "arena_state"},        # the table is still here
        ])
    finally:
        m._arena_state = real_state
    check("the failure is reported where the player is looking",
          any(e["t"] == "narration" and "went wrong" in (e.get("text") or "")
              for e in sent))
    check("...the spinner is put down with it",
          any(e["t"] == "busy" and e.get("on") is False for e in sent))
    check("...and the next message is still answered",
          any(e["t"] == "arena" for e in sent))

    print()
    if FAILS:
        print(f"\033[31m{len(FAILS)} check(s) failed:\033[0m " + ", ".join(FAILS))
        return 1
    print("\033[32mthe whole loop holds\033[0m")
    return 0


async def _boot_lifespan(m) -> None:
    """Run the real startup, which is where the schema self-heal lives."""
    await m.lifespan(m.app).__aenter__()


if __name__ == "__main__":
    sys.exit(main())
