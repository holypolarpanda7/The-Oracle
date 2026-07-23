"""Player-vs-player consequences + authorized-PvP recognition.

Two halves: a server-recorded PACT model (a consented duel authorizes a fight so no
divine wrath follows), and a retribution engine (an unsanctioned attack draws a
warning; an unsanctioned KILL brings the victim's god down on the killer — scaled by
level gap and repeat kinslaying — with a curse, alignment collapse, and ruined
standing). State lives as a dict in session meta (``meta["pvp"]``); no new DB table.
"""
from . import pacts, deities, consequences
from .pacts import (
    TERMS, new_state, normalize_terms, open_pact, authorized, close_pact,
    active_pacts, record_offense, offense_count, describe_pacts,
)
from .deities import retributor_for, wrath_modifier, full_name
from .consequences import PvpOutcome, assess

__all__ = [
    "pacts", "deities", "consequences",
    "TERMS", "new_state", "normalize_terms", "open_pact", "authorized",
    "close_pact", "active_pacts", "record_offense", "offense_count",
    "describe_pacts", "retributor_for", "wrath_modifier", "full_name",
    "PvpOutcome", "assess",
]
