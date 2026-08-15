"""Bastion turn resolution and facility costing.

All money/time knobs route through ``game_config.bastion``:
  * ``bastion_turn_days``        -> length of one bastion turn
  * ``gold_income_multiplier``   -> scales facility income
  * ``cost_multiplier``          -> scales facility/upgrade costs
  * ``special_facility_base_cost``-> base cost to add a special facility
  * ``min_level``                -> level required to own a bastion
"""
from __future__ import annotations

from typing import Dict, List, Optional

from game_config import get_config

from economy import gp_to_cp

from .catalog import get_facility, space_capacity, space_output


def min_bastion_level() -> int:
    return get_config().bastion.min_level


def turn_length_days() -> int:
    return get_config().bastion.bastion_turn_days


def facility_cost_gp(slug: str) -> float:
    """Cost to add a special facility (base x config cost multiplier)."""
    cfg = get_config().bastion
    return round(cfg.special_facility_base_cost * cfg.cost_multiplier, 4)


def basic_facility_cost_gp(slug: str = "") -> float:
    """Cost to add a BASIC room. Cheap on purpose: a bedroom is not a smithy."""
    cfg = get_config().bastion
    return round(cfg.basic_facility_base_cost * cfg.cost_multiplier, 4)


def can_own_bastion(level: int) -> bool:
    return level >= min_bastion_level()


def resolve_bastion_turn(
    facilities: List[Dict],
    *,
    world_day: Optional[int] = None,
    turn_number: int = 1,
) -> Dict:
    """Resolve one bastion turn.

    ``facilities`` is a list of dicts like
    ``{"facility_slug": "gaming-hall", "current_order": "Trade (patrons)",
    "space": "roomy", "enlarging_to": "vast", "enlarge_done_turn": 4}``.
    Facilities carrying an order that matches their catalog ``income_gp``
    produce scaled gold; every ordered facility yields a narrative event line.

    SIZE is read here and nowhere else in the turn: a bigger facility produces
    more (``space_output``), which is what makes enlarging one worth its gold.
    Building work also LANDS here — an enlargement finishes on the turn it was
    costed to finish on — and the completions are returned rather than applied,
    because this function touches no database and the caller owns the rows.
    """
    cfg = get_config().bastion
    events: List[Dict] = []
    completions: List[Dict] = []
    total_income_cp = 0

    for f in facilities:
        slug = f.get("facility_slug") or f.get("slug")
        order = f.get("current_order")
        cat = get_facility(slug) if slug else None
        if cat is None:
            continue

        # Building work finishes BEFORE the turn's work is done, so the turn a
        # facility becomes vast is the first turn it produces like one. The
        # alternative — landing it after — means paying for a size that does
        # nothing on the turn it arrives, which reads at the table as the
        # money having been wasted.
        space = str(f.get("space") or "cramped")
        want = str(f.get("enlarging_to") or "")
        done_turn = f.get("enlarge_done_turn")
        if want and done_turn is not None and turn_number >= int(done_turn):
            space = want
            completions.append({"facility_slug": slug, "id": f.get("id"),
                                "space": want})
            events.append({
                "event_type": "built",
                "facility_slug": slug,
                "description": (f"{cat['name']} is finished: now {want}, "
                                f"with room for {space_capacity(want)}."),
                "cp_delta": 0,
            })

        # Income-producing facilities pay out when given any order.
        base_income = float(cat.get("income_gp") or 0)
        if base_income and order:
            income_gp = round(base_income * cfg.gold_income_multiplier
                              * space_output(space), 4)
            income_cp = gp_to_cp(income_gp)
            total_income_cp += income_cp
            events.append({
                "event_type": "income",
                "facility_slug": slug,
                "description": f"{cat['name']} ({order}) earned {income_gp:g} gp.",
                "cp_delta": income_cp,
            })
        elif order:
            events.append({
                "event_type": "order",
                "facility_slug": slug,
                "description": f"{cat['name']} carried out: {order}.",
                "cp_delta": 0,
            })

    days = turn_length_days()
    end_day = (world_day + days) if world_day is not None else None
    return {
        "turn_number": turn_number,
        "days": days,
        "start_day": world_day,
        "end_day": end_day,
        "income_cp": total_income_cp,
        "income_gp": round(total_income_cp / 100.0, 4),
        "events": events,
        # Building work that landed this turn: [{"id","facility_slug","space"}].
        # The caller writes the new size onto the row and clears the works.
        "completions": completions,
        "summary": (
            f"Bastion turn {turn_number}: {len(events)} facility action(s), "
            f"+{total_income_cp / 100.0:g} gp over {days} day(s)."
        ),
    }
