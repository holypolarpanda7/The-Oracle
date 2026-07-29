"""The Proving Grounds — a practice mode outside the living world.

A player keeps up to three level-1 characters here, advances one to any level
through the real level-up flow, picks somewhere to fight (land, sea or air),
and fights encounters the code rosters against a real XP budget. Nothing that
happens here touches the world graph: no days pass, no one remembers it.

It exists for two reasons, and they're the same reason twice: it's the fastest
way for a player to try a build, and the fastest way for us to exercise the
three systems that have to be right on day one — character creation, level-up,
and combat on the tactical board.

    from arena import ENVIRONMENTS, build_roster, load_cards

    cards = load_cards(engine)
    roster = build_roster(ENVIRONMENTS["coral-reef"], level=5, cards=cards,
                          difficulty="hard")
"""
from .encounters import (ArenaRoster, MonsterCard, build_roster, candidates_for,
                         load_cards, suits_environment)
from .environments import (DOMAINS, ENVIRONMENTS, Environment,
                           environment_payload, environments_by_domain,
                           get_environment, sibling_environments)

#: How many characters a player may keep in the Grounds. Slots are overwritable
#: — a practice character is meant to be thrown away.
MAX_SLOTS = 3

#: Difficulties offered when choosing a fight.
DIFFICULTIES: tuple[str, ...] = ("easy", "medium", "hard", "deadly")

__all__ = [
    "ArenaRoster", "MonsterCard", "build_roster", "candidates_for", "load_cards",
    "suits_environment", "DOMAINS", "ENVIRONMENTS", "Environment",
    "environment_payload", "environments_by_domain", "get_environment",
    "sibling_environments", "MAX_SLOTS", "DIFFICULTIES",
]
