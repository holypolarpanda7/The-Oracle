"""
End-to-end demo of the tactical board (offline, temp DB, no GPU needed).

Run:  uv run python -m vtt.demo

Walks the whole life of a scene: a fight starts, a board is generated and the
combatants are seated on it, someone walks into trouble, a fireball lands, the
ground burns, and the board closes when the fight does. Every number printed
comes from the same code the backend calls.
"""
from __future__ import annotations

import os
import tempfile

from combat import CombatTracker

from . import VttEngine, bridge
from .mapgen import ARCHETYPES, generate_map


def _rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n" + "─" * len(title))


def main() -> None:
    db = os.path.join(tempfile.gettempdir(), "oracle_vtt_demo.db")
    if os.path.exists(db):
        os.remove(db)
    url = f"sqlite:///{db}"

    _rule("1. Layouts are generated, never improvised")
    for arch in ("cave", "tavern", "street", "bridge"):
        m = generate_map(arch, width=22, height=12, seed=42)
        walk = sum(1 for x, y in m.grid.squares() if m.grid.passable(x, y))
        print(f"  {arch:<8} {m.width}x{m.height}  {walk:>3} walkable  · {m.description}")
    print(f"  ({len(ARCHETYPES)} archetypes; same seed always rebuilds the same board)")

    ct = CombatTracker(database_url=url)
    ct.create_tables()
    v = VttEngine(database_url=url, tracker=ct)
    v.create_tables()

    _rule("2. A fight starts — the board comes out and seats everyone")
    enc = ct.start_encounter("demo:table", "Ambush in the shrine")
    ct.add_pc(enc.id, name="Kara", max_hp=28, armor_class=15, dex_mod=3, character_id=1)
    ct.add_pc(enc.id, name="Bram", max_hp=34, armor_class=17, dex_mod=1, character_id=2)
    ct.add_combatant(enc.id, "Ogre", max_hp=59, armor_class=11, dex_mod=-1)
    ct.add_combatant(enc.id, "Cultist", max_hp=9, armor_class=12, dex_mod=1)
    ct.roll_initiative(enc.id)

    scene = v.open_scene("demo:table", kind="combat", archetype="cave",
                         name="The Sunken Shrine", seed=1234,
                         encounter_id=enc.id, render_art=False)
    seated = v.sync_from_encounter(scene.id, enc.id)
    print(f"  board: {scene.name} ({scene.archetype}) "
          f"{scene.width}x{scene.height}, {scene.lighting} light")
    for t in seated:
        print(f"    {t.name:<9} {t.team:<5} at {t.x},{t.y}")

    _rule("3. Movement is pathed and costed against a real speed budget")
    kara = v.find_token(scene.id, "Kara")
    ogre = v.find_token(scene.id, "Ogre")
    opts = v.movement_options(kara.id)
    print(f"  Kara can reach {len(opts['squares'])} squares with {opts['budget_ft']} ft")
    dash = v.movement_options(kara.id, dash=True)
    print(f"  …and {len(dash['squares'])} if she Dashes")

    # Walk toward the ogre, as far as the budget allows.
    target = min(
        (s for s in opts["squares"]),
        key=lambda s: (abs(s["x"] - ogre.x) + abs(s["y"] - ogre.y)))
    res = v.move_token(kara.id, target["x"], target["y"])
    print(f"  Kara -> {target['x']},{target['y']}: {res['cost_ft']} ft spent, "
          f"{res['remaining_ft']} ft left")
    # A square she could only have reached by Dashing — the budget refuses it.
    reachable = {(s["x"], s["y"]) for s in opts["squares"]}
    too_far = next((s for s in dash["squares"]
                    if (s["x"], s["y"]) not in reachable), None)
    if too_far:
        over = v.move_token(kara.id, too_far["x"], too_far["y"])
        print(f"  trying to keep going to {too_far['x']},{too_far['y']}: "
              f"{over.get('reason')}")

    _rule("4. Distance, line of sight and cover come off the grid")
    print(f"  Kara -> Ogre: {v.measure(scene.id, 'Kara', 'Ogre')} ft, "
          f"line of sight {v.can_see(scene.id, 'Kara', 'Ogre')}, "
          f"cover {v.cover_for(scene.id, 'Kara', 'Ogre')}")

    _rule("5. Spell areas resolve to exact squares (walls stop them)")
    caught = v.tokens_in_area(scene.id, "sphere", ogre.x, ogre.y, radius_ft=20)
    print(f"  a 20-ft fireball centred on the Ogre would catch: "
          f"{', '.join(t.name for t in caught) or 'nobody'}")
    fire = v.add_effect(scene.id, "Fireball", shape="sphere", x=ogre.x, y=ogre.y,
                        radius_ft=20, damage="8d6 fire", save_ability="dex",
                        save_dc=15, duration_rounds=1)
    print(f"  it covers {len(fire.squares)} squares")
    web = v.add_effect(scene.id, "Web", shape="cube", x=ogre.x, y=ogre.y,
                       length_ft=20, difficult_terrain=True, kind="zone",
                       duration_rounds=10)
    print(f"  Web makes {len(web.squares)} squares difficult ground")

    _rule("6. The ground itself can change")
    v.set_terrain(scene.id, [(ogre.x, ogre.y + 1), (ogre.x + 1, ogre.y + 1)], "f")
    print("  two squares are now burning")

    _rule("7. The board and the gridless combat engine stay in step")
    cultist = [c for c in ct.order(enc.id) if c.name == "Cultist"][0]
    ct.set_position(cultist.id, "melee with Bram")     # as the engine's AI would
    moved = bridge.reconcile_bands(v, scene.id, tracker=ct)
    bridge.sync_bands(v, scene.id, tracker=ct)
    print(f"  the engine closed the Cultist to melee; {moved} token(s) walked to match")
    for c in ct.order(enc.id):
        print(f"    {c.name:<9} band: {c.position}")

    _rule("8. What the DM's prompt actually sees")
    print(v.render(scene.id))

    _rule("9. The fight ends, the board goes away")
    ct.end_encounter(enc.id)
    v.close_scene(scene.id)
    print(f"  active board for this table: {v.active_scene('demo:table')}")
    print(f"  replay log: {len(v.events(scene.id))} events recorded")
    print(f"\n(temp db: {db})")


if __name__ == "__main__":
    main()
