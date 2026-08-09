"""
End-to-end smoke test of TARGETING and the action bar.

`vtt/selftest.py` proves the board's geometry and `scripts/vtt_smoke.py` proves
the board's wiring. This proves the layer between them: that a spell says what
it targets, that the board says who may legally be targeted and WHY the rest
may not, that a template lands where the rules put it, and that an act chosen
on the bar reaches the combat engine as an intent instead of a sentence.

The three things it is really guarding:

  * "a creature you can see within range" is enforced by CODE. Before the
    targeting layer, `[[CAST]]` carried no target at all, so range and sight
    were checked by nobody.
  * the bar is a THIRD INTENT SOURCE, not a second resolution path. The same
    engine, the same economy, the same certified block for the DM.
  * a refusal NAMES ITS REASON. A greyed-out target the player can see the
    cause of is information; one silently missing is indistinguishable from
    a bug.

The LLM is stubbed, so this needs no model, no GPU and no network.

    uv run python scripts/targeting_smoke.py
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
        print(f"  \033[32m✓\033[0m {name}"
              + (f" \033[2m— {detail}\033[0m" if detail and cond else ""))
    else:
        print(f"  \033[31m✗\033[0m {name}{' — ' + detail if detail else ''}")
        FAILS.append(name)


def rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")


def _load_backend(db_path: str):
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    spec = importlib.util.spec_from_file_location(
        "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # type: ignore[union-attr]
    return mod


#: The rules tables are REFERENCE, not world state: they are the same for every
#: table and nothing in this test writes to them. Copying them into the scratch
#: database is what lets the bar be built from the real corpus — the actual
#: OCR-damaged Fireball, the actual Quarterstaff row — instead of from fixtures
#: written to agree with the parser. World state stays empty, which is the
#: point of a scratch DB.
_RULES_TABLES = ("rules_spell", "rules_item", "rules_monster", "rules_class",
                 "rules_race", "rules_subclass")


def _copy_rules_tables(scratch_db: str) -> tuple[int, str]:
    """Clone the rules reference tables into the scratch file. (rows, note)

    Runs BEFORE the backend is imported, which is the only moment nothing
    holds the scratch file open: the backend brings up several engines
    (characters, combat, the board) against the same sqlite file, and any of
    them is enough to make this copy deadlock. The tables are created with the
    live schema, so the backend's own ``create_all`` finds them already there
    and leaves them alone.
    """
    import sqlite3
    from rules.query import RulesLibrary
    src_engine = RulesLibrary().engine
    src = src_engine.url.database
    if not src or not os.path.exists(src):
        return 0, f"no rules database at {src!r}"
    src_engine.dispose()
    total = 0
    con = sqlite3.connect(scratch_db)
    try:
        con.execute("ATTACH DATABASE ? AS src", (src,))
        schema = dict(con.execute(
            "SELECT name, sql FROM src.sqlite_master WHERE type='table'"))
        for t in _RULES_TABLES:
            if not schema.get(t):
                continue
            con.execute(schema[t])
            con.execute(f"INSERT INTO {t} SELECT * FROM src.{t}")
            total += con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        con.commit()
    except sqlite3.Error as e:
        return total, str(e)[:140]
    finally:
        con.close()
    return total, ""


# ---------------------------------------------------------------- the rules
def part_rules() -> None:
    """A spell knows how far it reaches and what shape it throws."""
    from rules import targeting as T
    from rules.query import RulesLibrary
    lib = RulesLibrary()

    rule("1. a spell says what it targets")
    cases = [
        # name,             kind,       range, shape,       size, origin
        ("Fire Bolt",       "creature",   120, "",             0, "point"),
        ("Cure Wounds",     "creature",     5, "",             0, "point"),
        ("Healing Word",    "creature",    60, "",             0, "point"),
        ("Fireball",        "area",       150, "sphere",      20, "point"),
        ("Burning Hands",   "area",        15, "cone",        15, "self"),
        ("Cone of Cold",    "area",        60, "cone",        60, "self"),
        ("Lightning Bolt",  "area",       100, "line",       100, "self"),
        ("Thunderwave",     "area",        15, "cube",        15, "self"),
        ("Spirit Guardians", "area",       15, "emanation",   15, "self"),
        ("Grease",          "area",        60, "cube",        10, "point"),
        ("Shield",          "self",         0, "",             0, "point"),
        ("Misty Step",      "self",         0, "",             0, "point"),
    ]
    for name, kind, rng, shape, size, origin in cases:
        sp = lib.get_spell(name)
        if sp is None:
            check(f"{name} is in the library", False)
            continue
        s = T.spec_for(sp)
        got_size = s.radius_ft or s.length_ft
        ok = (s.kind == kind and s.range_ft == rng and s.shape == shape
              and got_size == size and s.origin == origin)
        check(f"{name}", ok,
              f"{s.kind} {s.range_ft}ft {s.shape or '—'}{got_size or ''} from {s.origin}")

    rule("2. …through the damage the PDF did to it")
    # Each of these was silently costing a spell its area before the parser
    # learned the shape of the damage. They are the regression tests.
    sp = lib.get_spell("Cone of Cold")
    check("a zero read as the letter O", T.spec_for(sp).length_ft == 60,
          "'6O-foot Cone' is a 60-foot cone")
    sp = lib.get_spell("Grease")
    check("a space dropped inside the word", T.spec_for(sp).shape == "cube",
          "'10-foot squ are' is a cube")
    sp = lib.get_spell("Spirit Guardians")
    check("a soft hyphen inside the word", T.spec_for(sp).shape == "emanation",
          "'Ema\\xad nation' is an emanation")
    sp = lib.get_spell("Magic Missile")
    check("the NEXT spell's text bled into the cell",
          T.spec_for(sp).kind == "creature" and not T.spec_for(sp).shape,
          "Magic Mouth's 20-foot Cube does not belong to Magic Missile")
    sp = lib.get_spell("Plane Shift")
    check("…even when it runs on with no header",
          not T.spec_for(sp).shape,
          "a Touch spell does not get Plant Growth's 100-foot Sphere")
    sp = lib.get_spell("Teleportation Circle")
    check("a circle DRAWN on the ground is not an area",
          not T.spec_for(sp).shape)

    rule("3. a damaged RANGE is repaired too — it is shared with Metamagic")
    from rules import metamagic
    for name, want in (("Animate Dead", 10), ("Befuddlement", 150),
                       ("Sleet Storm", 150)):
        sp = lib.get_spell(name)
        got = metamagic.facts_for(sp).range_ft if sp else None
        check(f"{name}: {sp.range!r} reads as {want} ft" if sp else name,
              got == want, str(got))


# ---------------------------------------------------------------- the board
def part_board() -> int:
    """The board decides who may be targeted, and says why not."""
    import tempfile as _tf
    from combat import CombatTracker
    from vtt import VttEngine

    db = os.path.join(_tf.gettempdir(), "oracle_targeting_board.db")
    if os.path.exists(db):
        os.remove(db)
    url = f"sqlite:///{db}"
    ct = CombatTracker(database_url=url)
    ct.create_tables()
    v = VttEngine(database_url=url, tracker=ct)
    v.create_tables()
    sc = v.open_scene("tgt:table", kind="combat", archetype="field",
                      name="Open field", seed=7, width=30, height=20,
                      render_art=False)
    v.add_token(sc.id, "Kara", kind="pc", team="party", x=5, y=10, character_id=1)
    v.add_token(sc.id, "Goblin", team="foe", x=8, y=10)      # 15 ft
    v.add_token(sc.id, "Archer", team="foe", x=15, y=10)     # 50 ft
    v.add_token(sc.id, "Ogre", team="foe", x=28, y=10)       # 115 ft
    v.add_token(sc.id, "Bram", kind="pc", team="party", x=5, y=12)

    def by_name(res, name):
        return next(t for t in res["targets"] if t["name"] == name)

    rule("4. who a creature may target, and why not the rest")
    res = v.targets_for(sc.id, "Kara", range_ft=120)
    check("everything on an open field is in a 120-ft spell's reach",
          all(t["legal"] for t in res["targets"]),
          ", ".join(f"{t['name']} {t['distance_ft']}ft" for t in res["targets"]))

    res = v.targets_for(sc.id, "Kara", range_ft=30)
    check("a 30-ft spell reaches the goblin", by_name(res, "Goblin")["legal"])
    far = by_name(res, "Ogre")
    check("…and not the ogre", not far["legal"])
    check("the refusal names the distance AND the reach",
          "115" in far["reason"] and "30" in far["reason"], far["reason"])

    rule("5. sight is half the rule, and a wall proves it")
    v.set_terrain(sc.id, [(11, 9), (11, 10), (11, 11)], "#")
    res = v.targets_for(sc.id, "Kara", range_ft=120)
    check("the goblin on this side is still targetable",
          by_name(res, "Goblin")["legal"])
    arch = by_name(res, "Archer")
    check("the archer behind the wall is not — though he is well in range",
          not arch["legal"] and arch["distance_ft"] == 50, arch["reason"])
    check("and the reason is sight, not distance",
          "sight" in arch["reason"].lower() or "cover" in arch["reason"].lower(),
          arch["reason"])
    # An effect that does not require sight still cannot reach through a wall:
    # total cover is a wall, not a modifier.
    res_ns = v.targets_for(sc.id, "Kara", range_ft=120, needs_sight=False)
    check("an effect needing no sight is still stopped by total cover",
          not by_name(res_ns, "Archer")["legal"],
          by_name(res_ns, "Archer")["reason"])

    rule("6. a template lands where the rules put it")
    ap = v.area_preview(sc.id, "Kara", 8, 10, shape="sphere", radius_ft=20,
                        range_ft=150)
    caught = {c["name"] for c in ap["caught"]}
    check("a fireball on the goblin catches the goblin", "Goblin" in caught)
    check("…and Kara and Bram, who are standing in it",
          {"Kara", "Bram"} <= caught,
          "friendly fire is visible BEFORE the slot is spent: " + ", ".join(sorted(caught)))
    ap = v.area_preview(sc.id, "Kara", 15, 10, shape="sphere", radius_ft=20,
                        range_ft=150)
    check("a template with no line of effect is refused", not ap["ok"], ap["reason"])
    ap = v.area_preview(sc.id, "Kara", 8, 10, shape="sphere", radius_ft=20,
                        range_ft=10)
    check("…so is one thrown past the spell's range", not ap["ok"], ap["reason"])
    ap = v.area_preview(sc.id, "Kara", 9, 10, shape="cone", length_ft=15)
    check("a cone from the caster is aimed by the same click",
          {c["name"] for c in ap["caught"]} == {"Goblin"},
          f"{len(ap['squares'])} squares, catching "
          + ", ".join(c["name"] for c in ap["caught"]))

    rule("7. the movement wash knows its floor and its threatened ground")
    kara = v.find_token(sc.id, "Kara")
    opts = v.movement_options(kara.id)
    threat = {(s["x"], s["y"]) for s in opts["threatened"]}
    check("the wash reports the level it was costed on", opts["level"] == 0)
    check("the square beside the goblin is threatened", (7, 10) in threat)
    check("the far side of the field is not", (25, 10) not in threat)
    pp = v.path_preview(kara.id, 7, 10)
    check("a path preview returns the SERVER's route, not a guess",
          pp["ok"] and pp["path"][0] == [kara.x, kara.y]
          and pp["path"][-1] == [7, 10],
          f"{len(pp['path'])} steps, {pp['cost_ft']} ft")
    return 0


# ------------------------------------------------------------ the action bar
def part_bar() -> int:
    db = os.path.join(tempfile.gettempdir(), "oracle_targeting_smoke.db")
    if os.path.exists(db):
        os.remove(db)
    rows, note = _copy_rules_tables(db)
    rule("8. the bar is built from the REAL rules, in a scratch world")
    check("the rules reference is available to the bar", rows > 0 and not note,
          note or f"{rows} reference rows cloned")
    if not rows:
        print("     (without it there is no Fireball to put on a bar)")
        return 1
    m = _load_backend(db)

    from fastapi import BackgroundTasks
    from sqlmodel import Session, SQLModel
    SQLModel.metadata.create_all(m.engine)

    session_id = "tgt:table"
    user_id = "tgt-user"
    with Session(m.engine) as s:
        char = m.Character(
            discord_user_id=user_id, name="Sable", race="Human",
            char_class="Wizard", level=5, approved=True,
            max_hp=28, current_hp=28,
            # "Sleep" is in the spellbook and NOT prepared; "Fire Bolt" is a
            # cantrip, which needs no preparation and must stay offered.
            spells=["Fire Bolt", "Fireball", "Magic Missile", "Shield", "Sleep"],
            prepared_spells=["Fireball", "Magic Missile", "Shield"],
            inventory=[{"name": "Quarterstaff", "quantity": 1,
                        "equipped": True, "grip": "both"}],
            stats={"strength": 10, "dexterity": 14, "constitution": 14,
                   "intelligence": 17, "wisdom": 12, "charisma": 8})
        s.add(char)
        s.commit()
        s.refresh(char)
        char_id = char.id
    m._set_session_meta(session_id, {
        "user_id": user_id, "character_id": char_id, "character_name": "Sable",
        "members": {user_id: {"character_id": char_id, "character_name": "Sable"}},
    })

    scripted: list[str] = []

    def fake_dm(messages):
        m.LAST_PROMPT = "\n".join(x.get("content", "") for x in messages)
        return scripted.pop(0) if scripted else "The dust settles."

    m.call_openrouter_dm = fake_dm
    m._call_extractor_llm = lambda messages: "[]"
    m._run_world_extraction = lambda *a, **k: None

    def say(text: str, narration: str, intents=None):
        scripted.append(narration)
        req = m.ChatRequest(session_id=session_id, user_id=user_id,
                            username="Smoke", message=text, intents=intents)
        return m.chat_endpoint(req, BackgroundTasks())

    rule("8b. the bar holds what this character can actually do")
    say("I kick the door in.",
        "Two bandits spring up.\n[[COMBAT: start | Guardroom]]\n"
        "[[COMBAT: add | bandit | x2]]")
    bar = m._activity_actions(session_id, user_id)
    if not bar:
        check("an action bar exists", False)
        return 1
    ids = {a["id"] for a in bar["actions"]}
    names = {a["name"] for a in bar["actions"]}
    check("an action bar exists", True, f"{len(bar['actions'])} acts")
    check("the weapon in hand is on it", "Quarterstaff" in names, str(sorted(names))[:120])
    check("so are the prepared spells", {"Fireball", "Magic Missile"} <= names)
    check("a cantrip needs no preparation, so it stays offered",
          "Fire Bolt" in names)
    check("a leveled spell in the book but NOT prepared is not offered",
          "Sleep" not in names,
          "a wizard casts its prepared subset")
    check("the board verbs are there", {"Dash", "Dodge", "Hide"} <= names)

    fb = next(a for a in bar["actions"] if a["name"] == "Fireball")
    check("Fireball is aimed as an area", fb["targeting"] == "area"
          and fb["shape"] == "sphere" and fb["radius_ft"] == 20,
          f"{fb['shape']} {fb['radius_ft']}ft, range {fb['range_ft']}")
    check("…and offers the slots that could pay for it", fb["slots"] == [3],
          f"level-5 wizard, slots {bar['slots']}")
    mm = next(a for a in bar["actions"] if a["name"] == "Magic Missile")
    check("Magic Missile is aimed at a creature", mm["targeting"] == "creature")
    check("…and can be upcast from the bar", mm["slots"] == [1, 2, 3],
          str(mm["slots"]))

    rule("9. the economy is reported, and it is the engine's")
    econ = bar["economy"]
    check("the bar knows a fight is on", econ["in_combat"])
    check("…and whether it is my turn", "my_turn" in econ,
          f"my_turn={econ['my_turn']}, whose={econ.get('whose_turn')!r}")

    rule("10. an act chosen on the bar reaches the engine as an INTENT")
    enc = m.combat.get_active(session_id)
    scene = m.vtt_engine.active_scene(session_id)
    if enc is None or scene is None:
        check("a fight and a board are running", False)
        return 1
    # Put our PC's turn up, so the act is legal to attempt.
    me = next(c for c in m.combat.order(enc.id) if c.character_id == char_id)
    foe = next(c for c in m.combat.order(enc.id) if c.character_id is None)
    while m.combat.current_combatant(enc.id).id != me.id:
        m.combat.next_turn(enc.id)
    foe_tok = m.vtt_engine.token_for_combatant(scene.id, foe.id)
    my_tok = m.vtt_engine.token_for_combatant(scene.id, me.id)
    check("both are on the board", foe_tok is not None and my_tok is not None)
    # Generated boards put the two sides at opposite spawns, which on a wide
    # map is further apart than a 120-ft spell reaches. Stand them near each
    # other so this step tests the INTENT, not the range rule (step 11 does
    # that on purpose).
    #
    # `move_token(teleport=True)`, never `update_token` — that method refuses
    # x/y on purpose so nothing can sidestep the movement rules by editing a
    # position, and a test that quietly failed to move anything would pass
    # step 11 for entirely the wrong reason.
    # …and stand it somewhere the PC can actually SEE. A generated cave will
    # happily put a pillar between two adjacent squares, and being refused for
    # total cover here would be the rules working correctly on a badly placed
    # fixture — a confusing way to fail.
    spot = None
    for dx, dy in ((2, 0), (-2, 0), (0, 2), (0, -2), (1, 1), (-1, -1),
                   (1, 0), (-1, 0), (0, 1), (0, -1)):
        sq = (my_tok.x + dx, my_tok.y + dy)
        if m.vtt_engine.move_token(foe_tok.id, sq[0], sq[1],
                                   teleport=True, free=True).get("ok") \
                and m.vtt_engine.can_see(scene.id, my_tok.name, foe_tok.name):
            spot = sq
            break
    foe_tok = m.vtt_engine.get_token(foe_tok.id)
    check("the foe is within reach and in plain sight",
          spot is not None,
          f"PC at {my_tok.x},{my_tok.y}; foe at {foe_tok.x},{foe_tok.y}")

    before = m.combat.get_combatant(me.id).action_used
    plan, sentence, err = m._board_action_plan(session_id, user_id, {
        "action_id": mm["id"], "target_token_id": foe_tok.id, "slot": 1})
    check("the bar plans a real intent", plan is not None and not err,
          f"{plan} :: {sentence!r}" if plan else err)
    if plan:
        check("it is the engine's own verb and arg",
              plan[0]["verb"] == "cast" and plan[0]["arg"] == "Magic Missile"
              and plan[0]["target"] == foe_tok.name, str(plan[0]))
        check("and the DM is handed a line to narrate",
              foe_tok.name in sentence, sentence)
        hp_before = m.combat.get_combatant(foe.id).current_hp
        say(sentence, "Darts of force streak across the room.", intents=plan)
        after = m.combat.get_combatant(me.id)
        check("the Action was actually spent",
              after.action_used and not before, f"action_used {before} -> {after.action_used}")
        hp_after = m.combat.get_combatant(foe.id).current_hp
        check("and the target actually took damage", hp_after < hp_before,
              f"{hp_before} -> {hp_after} HP")
        check("the DM saw a certified result, not a request to invent one",
              "CERTIFIED" in getattr(m, "LAST_PROMPT", "").upper()
              or "Magic Missile" in getattr(m, "LAST_PROMPT", ""))

    rule("11. the client is not the authority")
    # A creature that dropped while the player was choosing gets a reason of
    # its OWN. Silently vanishing from the answer is the failure the reasons
    # exist to prevent, so this is checked rather than assumed.
    #
    # Put it down explicitly rather than relying on step 10's damage roll:
    # 3d4+3 against an 11-HP bandit kills it most of the time but not always,
    # and a test that asserts "is down" on a creature that happens to be
    # standing is a flake, not a finding.
    m.vtt_engine.update_token(foe_tok.id, defeated=True)
    plan, sentence, err = m._board_action_plan(session_id, user_id, {
        "action_id": mm["id"], "target_token_id": foe_tok.id, "slot": 1})
    check("a target that has already dropped is refused, and says so",
          plan is None and "down" in err.lower(), err)

    # Aim the same spell at a LIVING target out of its range, by hand — the
    # sort of thing a stale highlight or a hand-built message produces.
    # Explicitly NOT the one just put down — it is picked by the combatant's
    # own defeat flag, and the token was forced down independently above, so
    # "the first living monster" can otherwise resolve back to the same one.
    other = next(c for c in m.combat.order(enc.id)
                 if c.character_id is None and not c.defeated
                 and c.id != foe.id)
    other_tok = m.vtt_engine.token_for_combatant(scene.id, other.id)
    m.vtt_engine.move_token(other_tok.id, 0, 0, teleport=True, free=True)
    m.vtt_engine.move_token(my_tok.id, scene.width - 1, scene.height - 1,
                            teleport=True, free=True)
    gap = m.vtt_engine.measure(scene.id, my_tok.name, other_tok.name)
    check("the two are now genuinely far apart", (gap or 0) > 120, f"{gap} ft")
    plan, sentence, err = m._board_action_plan(session_id, user_id, {
        "action_id": mm["id"], "target_token_id": other_tok.id, "slot": 1})
    check("a target out of range is refused", plan is None and bool(err), err)
    check("…and the refusal names the reason", "range" in err.lower(), err)

    plan, sentence, err = m._board_action_plan(session_id, user_id, {
        "action_id": "cast:not-a-real-spell", "target_token_id": other_tok.id})
    check("an action that isn't on the bar is refused", plan is None and bool(err), err)

    # Someone else's turn.
    while m.combat.current_combatant(enc.id).id == me.id:
        m.combat.next_turn(enc.id)
    plan, sentence, err = m._board_action_plan(session_id, user_id, {
        "action_id": mm["id"], "target_token_id": other_tok.id, "slot": 1})
    check("acting out of turn is refused", plan is None and "turn" in err.lower(), err)

    rule("12. the DM's own [[CAST]] is gated the same way")
    # The bar is not the only way a spell gets cast — the DM narrates most of
    # them. `[[CAST]]` carries a target now, so the same range-and-sight rule
    # reaches the narrated path.
    with Session(m.engine) as s:
        ch = s.get(m.Character, char_id)
        before_slots = dict(ch.spell_slots_used or {})
        far = m.resolve_cast_hooks(
            f"[[CAST: Magic Missile | 1 | {other_tok.name}]]", ch,
            session_id=session_id)
        check("a spell at a target out of range is refused",
              "finds nothing" in far, far.strip())
        check("…and the slot was NOT burned by the refusal",
              dict(ch.spell_slots_used or {}) == before_slots,
              f"{before_slots} -> {dict(ch.spell_slots_used or {})}")

        # Bring the target back into reach and the same hook goes off. Re-read
        # the caster's token: it was moved above, and the local object still
        # carries the coordinates it had before that.
        me_now = m.vtt_engine.get_token(my_tok.id)
        m.vtt_engine.move_token(other_tok.id, me_now.x, me_now.y - 1,
                                teleport=True, free=True)
        near = m.resolve_cast_hooks(
            f"[[CAST: Magic Missile | 1 | {other_tok.name}]]", ch,
            session_id=session_id)
        check("the same spell at a reachable target goes off",
              "finds nothing" not in near, near.strip())
        check("…and THAT one spent a slot",
              dict(ch.spell_slots_used or {}) != before_slots,
              f"{before_slots} -> {dict(ch.spell_slots_used or {})}")

        # A spell naming nobody is not refused: theater-of-the-mind casting
        # has no position to check, and inventing a refusal would be worse
        # than not enforcing one.
        anon = m.resolve_cast_hooks("[[CAST: Magic Missile | 1]]", ch,
                                    session_id=session_id)
        check("a casting that names no target is left alone",
              "finds nothing" not in anon, anon.strip())
    return 0


def main() -> int:
    print("\033[1mThe Oracle — targeting & action-bar smoke test\033[0m")
    part_rules()
    part_board()
    part_bar()
    print()
    if FAILS:
        print(f"\033[31m{len(FAILS)} check(s) failed:\033[0m " + ", ".join(FAILS))
        return 1
    print("\033[32mthe board decides who can be hit, and the engine decides "
          "what happens\033[0m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
