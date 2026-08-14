"""Two AI sides, a real board, and a record of how they actually fought.

``vtt/selftest.py`` proves the board's rules and ``scripts/arena_smoke.py``
proves the Proving Grounds wiring. Neither watches a FIGHT: whether the
creatures move like players, whether they spend a whole turn's economy, whether
they use the ground they are standing on. That is not something a pass/fail
check can answer, so this prints a turn-by-turn log and a table of numbers, and
the numbers are the point — "the AI feels smarter" is not a measurement.

    uv run python scripts/ai_arena.py                    # one bout, dungeon-room
    uv run python scripts/ai_arena.py --board terraces   # somewhere vertical
    uv run python scripts/ai_arena.py --bouts 20 --quiet # just the table

Everything is offline: no LLM, no GPU, no network. Both sides are driven by the
engine's own monster AI, which is exactly the code that runs a real encounter's
monsters — so what this measures is what a table gets.
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

B, D, OFF = "\033[1m", "\033[2m", "\033[0m"

#: Two even sides, described the way a DM would think of them rather than as
#: stat blocks: something that holds the line, something that shoots, something
#: that is fast and fragile. The point is that they want DIFFERENT ground.
RED = [("Red Blade", 30, 15, 2, "melee"), ("Red Archer", 22, 13, 3, "ranged"),
       ("Red Skirmisher", 18, 14, 4, "melee")]
BLUE = [("Blue Blade", 30, 15, 2, "melee"), ("Blue Archer", 22, 13, 3, "ranged"),
        ("Blue Skirmisher", 18, 14, 4, "melee")]


#: Two stat blocks the harness owns, written here rather than pulled from the
#: bestiary. The scratch database has no `rules_*` tables in it (they are the
#: half of oracle.db that does NOT re-seed — see CLAUDE.md), so a fight seated
#: against real slugs came back "has no attack to make" for twenty turns. A
#: test harness that depends on ingested content is a test harness that only
#: runs on one machine.
_BLOCKS = (
    dict(index_slug="ai-thug", name="AI Thug", armor_class=15, hit_points=30,
         strength=15, dexterity=12, constitution=14, speed={"walk": "30 ft."},
         actions=[{"name": "Mace", "attack_bonus": 4,
                   "damage": [{"damage_dice": "2d6+3",
                               "damage_type": {"name": "bludgeoning"}}]}]),
    dict(index_slug="ai-scout", name="AI Scout", armor_class=13, hit_points=22,
         strength=11, dexterity=16, constitution=12, speed={"walk": "30 ft."},
         actions=[{"name": "Shortbow", "attack_bonus": 5, "desc": "Ranged Weapon "
                   "Attack: +5 to hit, range 80/320 ft., one target.",
                   "damage": [{"damage_dice": "2d6+2",
                               "damage_type": {"name": "piercing"}}]}]),
)


def _seed_blocks(db_path: str) -> None:
    from sqlmodel import Session, SQLModel, create_engine, select

    from rules.models import Monster
    eng = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        for blk in _BLOCKS:
            if s.exec(select(Monster).where(
                    Monster.index_slug == blk["index_slug"])).first():
                continue
            s.add(Monster(**blk))
        s.commit()


def _engine(db_path: str):
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    from combat import CombatEngine, CombatTracker
    from vtt import VttEngine, bridge

    tracker = CombatTracker(database_url=f"sqlite:///{db_path}")
    tracker.create_tables()
    vtt = VttEngine(database_url=f"sqlite:///{db_path}", tracker=tracker)
    vtt.create_tables()
    engine = CombatEngine(tracker=tracker)
    return tracker, vtt, engine, bridge


def bout(tracker, vtt, engine, bridge, *, board: str, seed: int,
         rounds: int, log: bool) -> dict:
    """Run one fight to a finish (or a round cap). Returns its telemetry."""
    enc = tracker.start_encounter(f"ai:{seed}", name=f"AI bout {seed}")
    for name, hp, ac, dex, role in RED:
        tracker.add_combatant(enc.id, name, max_hp=hp, armor_class=ac,
                              dex_mod=dex, side="party",
                              monster_slug=("ai-scout" if role == "ranged" else "ai-thug"))
    for name, hp, ac, dex, role in BLUE:
        tracker.add_combatant(enc.id, name, max_hp=hp, armor_class=ac,
                              dex_mod=dex, side="foe",
                              monster_slug=("ai-scout" if role == "ranged" else "ai-thug"))
    tracker.roll_initiative(enc.id)

    scene = vtt.open_scene(f"ai:{seed}", kind="combat", archetype=board,
                           width=30, height=22, seed=seed, encounter_id=enc.id,
                           render_art=False)
    vtt.sync_from_encounter(scene.id, enc.id)
    bridge.sync_bands(vtt, scene.id, tracker=tracker)
    # THE BOARD, attached. Without this the harness measured an AI that could
    # not see the room it was standing in — reach and weapon ranges fell back
    # to the band model, `can_see` never answered, and nothing could ever find
    # a gap to jump. The backend has attached one per exchange since the bridge
    # was built (`_attach_spatial`); the arena simply never did, so every
    # number it printed was for a blindfolded engine.
    engine.spatial = bridge.BoardSpatial(vtt, scene.id)

    stat = Counter()
    heights: list[int] = []
    moved_any = 0
    turns = 0
    row = vtt.get_scene(scene.id)
    high_ground = bool(row.elevation)

    for _rnd in range(rounds):
        living = {c.id: c for c in tracker.order(enc.id) if not c.defeated}
        sides = {(c.side or "") for c in living.values()}
        if len(sides) < 2:
            break
        cur = tracker.current_combatant(enc.id)
        if cur is None:
            break
        before = {c.id: c.current_hp for c in tracker.order(enc.id)}
        was = vtt.token_for_combatant(scene.id, cur.id)
        was_sq = (was.x, was.y) if was else None

        rep = engine.run_monster_turn(enc.id)
        turns += 1

        # Whatever the engine decided about position, put it on the board.
        band = None
        for ev in rep.events:
            if ev.get("kind") == "move":
                band = ev.get("to") or ev.get("band")
        if band:
            bridge.apply_band_move(vtt, scene.id, cur.id, band, tracker=tracker)
        bridge.sync_after_turn(vtt, scene.id, tracker=tracker)

        tok = vtt.token_for_combatant(scene.id, cur.id)
        if tok is not None:
            heights.append(vtt.token_height_ft(vtt.get_scene(scene.id), tok))
            if was_sq and (tok.x, tok.y) != was_sq:
                moved_any += 1
        for ev in rep.events:
            stat[ev.get("kind", "?")] += 1
            # A LEAP is a move, and it is the move worth counting separately:
            # the board grew chasms and channels long before anything without a
            # player behind it would cross one.
            if ev.get("jumped"):
                stat["jumped"] += 1
        after = {c.id: c.current_hp for c in tracker.order(enc.id)}
        dealt = sum(max(0, before.get(i, 0) - after.get(i, 0)) for i in before)
        stat["damage"] += dealt
        fresh = tracker.get_combatant(cur.id)
        if fresh is not None:
            stat["bonus_used"] += bool(fresh.bonus_used)
            stat["reaction_used"] += bool(fresh.reaction_used)
        for rj in rep.rejections:
            stat["refused"] += 1
            if log:
                why = str(rj.get("reason") or "")[:70]
                print(f"  {D}r{_rnd:<3}{OFF} {cur.name:<16} {'':>6}{'':>7}  "
                      f"{D}refused: {why}{OFF}")
        if log:
            kinds = ", ".join(sorted({e.get("kind", "?") for e in rep.events})) or "nothing"
            where = f"{tok.x},{tok.y}" if tok else "?"
            hgt = f" {heights[-1]:+d}ft" if heights and heights[-1] else ""
            print(f"  {D}r{_rnd:<3}{OFF} {cur.name:<16} {where:>6}{hgt:>7}  "
                  f"{kinds:<28} {D}dmg {dealt}{OFF}")

    survivors = [c for c in tracker.order(enc.id) if not c.defeated]
    by_side = Counter((c.side or "?") for c in survivors)
    return {
        "board": board, "seed": seed, "turns": turns,
        "damage": stat["damage"], "attacks": stat["attack"],
        "moves": stat["move"], "moved_any": moved_any,
        "jumped": stat["jumped"],
        "bonus": stat["bonus_used"], "reaction": stat["reaction_used"],
        "refused": stat["refused"],
        "high_ground_board": high_ground,
        "avg_height": (statistics.mean(heights) if heights else 0.0),
        "winner": (by_side.most_common(1)[0][0] if len(by_side) == 1 else "draw"),
        "survivors": len(survivors),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--board", default="dungeon-room",
                    help="archetype to fight on (terraces, ruins, cave …)")
    ap.add_argument("--bouts", type=int, default=1)
    ap.add_argument("--rounds", type=int, default=40, help="turn cap per bout")
    ap.add_argument("--quiet", action="store_true", help="table only")
    a = ap.parse_args()

    db = os.path.join(tempfile.gettempdir(), "oracle_ai_arena.db")
    if os.path.exists(db):
        os.remove(db)
    _seed_blocks(db)
    tracker, vtt, engine, bridge = _engine(db)

    rows = []
    for i in range(a.bouts):
        if not a.quiet:
            print(f"\n{B}bout {i + 1} on {a.board}{OFF}")
        rows.append(bout(tracker, vtt, engine, bridge, board=a.board,
                         seed=7 + i, rounds=a.rounds, log=not a.quiet))

    n = len(rows)
    def avg(k): return sum(r[k] for r in rows) / max(1, n)
    print(f"\n{B}{n} bout(s) on {a.board}{OFF}")
    print(f"  turns taken        {avg('turns'):.1f}")
    print(f"  attacks made       {avg('attacks'):.1f}")
    print(f"  damage dealt       {avg('damage'):.1f}")
    print(f"  turns that MOVED   {avg('moved_any'):.1f}   "
          f"{D}(a poor proxy on its own: a creature already on the best square"
          f" is right to stand){OFF}")
    print(f"  bouts RESOLVED     {sum(1 for r in rows if r['winner'] != 'draw')}"
          f"/{n}   {D}(a fight that never ends is an AI that never closes){OFF}")
    print(f"  bonus actions      {avg('bonus'):.1f}")
    print(f"  reactions          {avg('reaction'):.1f}")
    print(f"  leaps taken        {avg('jumped'):.1f}   "
          f"{D}(a gap crossed instead of walked round){OFF}")
    print(f"  intents REFUSED    {avg('refused'):.1f}   "
          f"{D}(the AI tried and the engine said no){OFF}")
    print(f"  avg height held    {avg('avg_height'):.1f} ft"
          + ("" if rows[0]["high_ground_board"] else f"   {D}(flat board){OFF}"))
    wins = Counter(r["winner"] for r in rows)
    print(f"  results            {dict(wins)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
