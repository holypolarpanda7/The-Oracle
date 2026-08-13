"""
End-to-end smoke test of the tactical board *through the backend chat path*.

``vtt/selftest.py`` proves the board's rules. This proves the wiring: that a
fight opened in narration really produces a board, that the DM's ``[[VTT: ...]]``
hooks reach it, that the board is fed back into the next prompt, that a Discord
table gets a picture, and that the board goes away when the fight does.

The LLM is stubbed with canned narration, so this needs no model, no GPU and no
network — just a scratch copy of the database.

    uv run python scripts/vtt_smoke.py

It always builds a FRESH scratch database. Pointing it at a copy of a live
oracle.db would need the backend's startup column migrations, which only run
under the real lifespan — and an empty world is enough to prove the wiring.
"""
from __future__ import annotations

import importlib.util
import os
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# A board with no prior at its place picks its seed from the global RNG, so
# every run of this test drew a different layout — and a check about a landmark
# is a check about whether one FITS, which some layouts genuinely refuse. An
# intermittent smoke test is worse than no smoke test: the one failure it is
# certain to be blamed for is the change somebody is holding at the time.
random.seed(20260812)

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
    db = os.path.join(tempfile.gettempdir(), "oracle_vtt_smoke.db")
    if os.path.exists(db):
        os.remove(db)

    print("\033[1mThe Oracle — tactical board wiring smoke test\033[0m")
    m = _load_backend(db)

    from fastapi import BackgroundTasks
    from sqlmodel import Session, SQLModel

    # The backend creates its tables in the lifespan, which doesn't run when the
    # module is imported directly — do the same first thing it does.
    SQLModel.metadata.create_all(m.engine)

    session_id = "smoke:table"
    user_id = "smoke-user"

    # ---- a player with a character, seated at a table -------------------
    with Session(m.engine) as s:
        char = m.Character(
            discord_user_id=user_id, name="Sable", race="Human",
            char_class="Fighter", level=3, approved=True,
            max_hp=28, current_hp=28,
            stats={"strength": 16, "dexterity": 14, "constitution": 14,
                   "intelligence": 10, "wisdom": 12, "charisma": 8})
        s.add(char)
        s.commit()
        s.refresh(char)
        char_id = char.id
    m._set_session_meta(session_id, {
        "user_id": user_id, "character_id": char_id, "character_name": "Sable",
        "members": {user_id: {"character_id": char_id, "character_name": "Sable"}},
    })

    # ---- stub the DM ----------------------------------------------------
    scripted: list[str] = []

    def fake_dm(messages):
        m.LAST_PROMPT = "\n".join(x.get("content", "") for x in messages)
        return scripted.pop(0) if scripted else "The dust settles."

    m.call_openrouter_dm = fake_dm
    # The combat intent extractor and the world extractor are separate LLM calls;
    # stub them too so this runs offline and fast.
    m._call_extractor_llm = lambda messages: "[]"
    m._run_world_extraction = lambda *a, **k: None      # background noise

    def say(text: str, narration: str):
        scripted.append(narration)
        req = m.ChatRequest(session_id=session_id, user_id=user_id,
                            username="Smoke", message=text)
        return m.chat_endpoint(req, BackgroundTasks())

    # ---- 1. a fight opens a board --------------------------------------
    print("\n\033[1m1. a fight opens a board and seats the party\033[0m")
    resp = say("I kick the door in.",
               "The door bursts inward and two bandits spring up.\n"
               "[[COMBAT: start | Ambush in the guardroom]]\n"
               "[[COMBAT: add | bandit | x2]]")
    scene = m.vtt_engine.active_scene(session_id)
    check("a board is out", scene is not None)
    if scene is None:
        return 1
    names = {t.name for t in m.vtt_engine.tokens(scene.id)}
    check("the PC is on it", "Sable" in names, str(names))
    check("so are the foes", any(n.lower().startswith("bandit") for n in names),
          str(names))
    check("it is tied to the encounter", scene.encounter_id is not None)
    check("a Discord table gets the board as a picture",
          any((i or {}).get("mime") == "image/png" for i in (resp.images or [])),
          str([(i or {}).get("mime") for i in (resp.images or [])]))

    # ---- 2. the board reaches the next prompt ---------------------------
    print("\n\033[1m2. the DM is told where everyone stands\033[0m")
    say("I look around.", "You take in the room.")
    prompt = getattr(m, "LAST_PROMPT", "")
    check("the board is injected into the prompt", "# Board:" in prompt)
    check("with the creatures listed",
          "Sable" in prompt and "bandit" in prompt.lower())
    check("and the hook guidance", "[[VTT:" in prompt)

    # ---- 3. the DM's board verbs are applied ---------------------------
    # Effects are position-independent, so they're safe to assert mid-fight.
    # Movement is checked in step 6 on a board with no combat engine running:
    # during a fight the engine may legitimately walk a monster (its AI closing
    # to melee) between the hook being written and being applied, which is
    # correct behaviour and would make a coordinate assertion here a lie.
    print("\n\033[1m3. the DM's [[VTT]] hooks land on the board\033[0m")
    tok = m.vtt_engine.find_token(scene.id, "Bandit 1")
    at = (tok.x, tok.y) if tok else (5, 5)
    say("I wait.",
        f"Caltrops scatter across the floor.\n[[VTT: effect | Caltrops | shape=cube | "
        f"at={at[0]},{at[1]} | size=10 | difficult]]")
    eff = m.vtt_engine.find_effect(scene.id, "Caltrops")
    check("the effect landed with resolved squares",
          eff is not None and len(eff.squares or []) > 0)
    check("and it is difficult ground", bool(eff and eff.difficult_terrain))

    # ---- 4. an illegal move is refused, out loud ------------------------
    print("\n\033[1m4. an illegal move comes back as a correction\033[0m")
    far = (scene.width - 1, scene.height - 1)
    resp = say("I wait again.",
               f"The bandit sprints impossibly far.\n[[VTT: move | Bandit 1 | {far[0]},{far[1]}]]")
    check("the refusal reaches the narration",
          "cannot move there" in (resp.reply or ""), (resp.reply or "")[-160:])

    # ---- 5. the fight ends, the board goes away ------------------------
    print("\n\033[1m5. the board closes with the fight\033[0m")
    say("I finish them.", "The last bandit drops.\n[[COMBAT: end]]")
    check("no board is out", m.vtt_engine.active_scene(session_id) is None)
    check("the scene left a replay log", len(m.vtt_engine.events(scene.id)) > 0)

    # ---- 6. a board the DM opens by hand, with nothing else moving -------
    print("\n\033[1m6. the DM opens a board and moves a piece on it\033[0m")
    say("I study the mechanism.",
        "Glyphs wake across the flagstones.\n"
        "[[VTT: open | puzzle | crypt | The Glyph Floor]]\n"
        "[[VTT: place | Stone Guardian | 5,5 | team=neutral]]")
    puzzle = m.vtt_engine.active_scene(session_id)
    check("the DM's own board opened", puzzle is not None
          and puzzle.kind == "puzzle", str(puzzle and puzzle.kind))
    if puzzle is not None:
        guard = m.vtt_engine.find_token(puzzle.id, "Stone Guardian")
        check("the placed piece is on it", guard is not None)
        if guard is not None:
            # One step, chosen now, on a board with no engine to disturb it.
            step = next((s for s in m.vtt_engine.movement_options(guard.id)["squares"]
                         if s["cost"] == 5), None)
            if step:
                say("I step back.",
                    "The guardian grinds one pace forward.\n"
                    f"[[VTT: move | Stone Guardian | {step['x']},{step['y']}]]")
                moved = m.vtt_engine.get_token(guard.id)
                check("the piece moved exactly where the DM said",
                      (moved.x, moved.y) == (step["x"], step["y"]),
                      f"{(moved.x, moved.y)} vs {(step['x'], step['y'])}")
            else:
                check("a one-step square exists for the move test", False)
        say("I leave it be.", "The glyphs dim.\n[[VTT: close]]")
        check("the DM can put the board away",
              m.vtt_engine.active_scene(session_id) is None)

    print("\n\033[1m7. the board's HEIGHT reaches the DM\033[0m")
    # Nearly every archetype generates high ground now, and a DM who is not
    # told about it will fight on the floor forever. The board reported height
    # only on a creature's own line until this was added, which is no use for
    # deciding whether anyone should take it.
    say("We push into the old hall.",
        "Stone steps climb to a dais at the far end.\n"
        "[[VTT: open | combat | a pillared stone chamber | The Long Hall]]")
    hall = m.vtt_engine.active_scene(session_id)
    check("a board opened", hall is not None)
    if hall is not None:
        raised = bool(m.vtt_engine.get_scene(hall.id).elevation)
        board = m.vtt_engine.render(hall.id)
        check("the generated chamber has high ground on it", raised)
        check("…and the DM board says so, with what it costs",
              "ground height:" in board and "fall" in board,
              board.split("\n")[2][:120] if raised else "no elevation")
        say("We back out.", "The hall goes quiet.\n[[VTT: close]]")

    print("\n\033[1m8. the DM narrates a landmark, and it is really there\033[0m")
    # The whole chain for one sentence of fiction: loose words -> a catalogue
    # slug -> a board grown to hold it -> squares that stop a creature. Until
    # this channel existed the ziggurat was in the narration and nowhere else.
    say("We climb toward the temple.",
        "The canopy breaks on a stepped ziggurat swallowed in vines.\n"
        "[[VTT: open | combat | an overgrown temple in the jungle | "
        "The Sunken Shrine | landmark=a stepped ziggurat]]")
    board = m.vtt_engine.active_scene(session_id)
    check("the DM's landmark board opened", board is not None)
    if board is not None:
        slugs = [p.get("slug") for p in (board.setpieces or [])]
        check("the ziggurat the narration promised is on the board",
              "step-pyramid" in slugs, str(slugs))
        state = m.vtt_engine.state(board.id)
        marks = [p.get("slug") for p in (state.get("setpieces") or [])]
        check("…and it is shipped to the players' view",
              "step-pyramid" in marks, str(marks))
        say("We back off.", "The steps stay silent.\n[[VTT: close]]")

    print()
    if FAILS:
        print(f"\033[31m{len(FAILS)} check(s) failed:\033[0m " + ", ".join(FAILS))
        return 1
    print("\033[32mthe whole loop holds\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
