"""
Runnable demo for the persistent world graph.

    uv run python -m eight_card_system.demo

Seeds a starter world in a throwaway SQLite file, places a PC, prints the
relevance-scoped world slice the DM would see, then applies a hand-written delta
(standing in for the extractor LLM) to show the world evolving over in-world time.
"""
from __future__ import annotations

from pathlib import Path

from .graph import WorldGraph
from .seed import seed_starter_world, place_pc
from .extraction import WorldDelta, EntityDelta, RelationAdd, EventDelta, apply_world_delta

DEMO_DB = Path(__file__).resolve().parent / "demo_world.db"


def _rule(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main() -> None:
    # Fresh DB each run so the demo is deterministic.
    if DEMO_DB.exists():
        DEMO_DB.unlink()

    graph = WorldGraph(database_url=f"sqlite:///{DEMO_DB}")
    seed_starter_world(graph)
    place_pc(graph, "Aeryn Vale", location_slug="the-silver-tankard",
             attributes={"description": "A wandering scout new to Millbrook."})

    _rule("DM context — action: 'I ask Marta about the missing merchant'")
    ctx = graph.get_world_context("Aeryn Vale", "I ask Marta about the missing merchant")
    print(ctx.render())

    _rule("Applying a world change (PC travels to Duskwood, finds a track)")
    delta = WorldDelta(
        entities=[
            EntityDelta(
                name="Broken Cart Wheel", type="item",
                attributes={"description": "A shattered wheel from the missing trader's cart, just off the Duskwood path."},
                tags=["clue"],
            ),
        ],
        relations_add=[
            RelationAdd(src="Aeryn Vale", rel_type="located_in", dst="Duskwood"),
            RelationAdd(src="Broken Cart Wheel", rel_type="located_in", dst="Duskwood"),
            RelationAdd(src="The Missing Merchant", rel_type="involves", dst="Broken Cart Wheel"),
        ],
        events=[
            EventDelta(
                summary="Aeryn follows the trader's trail into Duskwood and finds a broken cart wheel.",
                location="Duskwood",
                involved=["Aeryn Vale", "The Missing Merchant", "Broken Cart Wheel"],
            ),
        ],
        advance_days=1,
    )
    summary = apply_world_delta(graph, delta, session_id="demo")
    print(f"applied: {summary}")

    _rule("DM context after the change — action: 'I search the area'")
    ctx2 = graph.get_world_context("Aeryn Vale", "I search the area")
    print(ctx2.render())

    print(f"\n(Current in-world day: {graph.current_day()})")
    print(f"(Demo DB written to: {DEMO_DB})")


if __name__ == "__main__":
    main()
