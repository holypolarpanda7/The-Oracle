"""Shared types for the in-game games subsystem.

Game *state* is deliberately kept as plain JSON-serializable dicts so it can live
in the backend's per-session meta (like ``meta["decks"]`` / ``meta["active_puzzle"]``)
with no new DB table. These dataclasses/enums are the typed vocabulary the engines,
the suspicion model, and the cheat adjudicator speak — they are never themselves
persisted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional


class Detectability(str, Enum):
    """How inherently exposed a cheat method is, before suspicion is layered on.

    The base DC to pull it off *unnoticed* rises with brazenness. A palmed die
    (SUBTLE) is far easier to hide than openly swapping a card (BRAZEN).
    """

    SUBTLE = "subtle"    # sleight so small it barely reads — loaded die, marked card
    RISKY = "risky"      # a real gamble — palming, a quick swap under cover
    BRAZEN = "brazen"    # bold and exposed — an obvious switch, colluding out loud

    @property
    def base_dc(self) -> int:
        return {"subtle": 10, "risky": 15, "brazen": 20}[self.value]


class CheatOutcome(str, Enum):
    """The graded result of a cheat attempt, from cleanest to worst."""

    CLEAN = "clean"          # nobody noticed a thing
    TRACE = "trace"          # it worked, but left a faint tell (heat ticks up)
    SUSPECTED = "suspected"  # it worked, yet an NPC's eyes lingered (heat jumps)
    CAUGHT = "caught"        # spotted in the act — the table knows

    @property
    def succeeded(self) -> bool:
        """Did the cheat actually alter the game? (Everything but CAUGHT.)"""
        return self is not CheatOutcome.CAUGHT


@dataclass
class SpellComponents:
    """The perceptible components of a spell used to cheat.

    Populated by the backend from the rules DB (``Spell.components`` / V-S-M).
    A spell with ANY perceptible component can't be a quiet cheat — the casting
    is seen/heard at the table — unless it's cast subtly (no components at all).
    """

    verbal: bool = False
    somatic: bool = False
    material: bool = False
    subtle: bool = False  # cast with Subtle Spell or an equivalent (no V/S)

    @property
    def perceptible(self) -> bool:
        if self.subtle:
            return False
        return bool(self.verbal or self.somatic or self.material)


@dataclass
class CheatAttempt:
    """Everything the adjudicator needs to rule on one cheat."""

    method: str                       # free-text description ("palm a die")
    detectability: Detectability
    skill_total: int                  # the actor's resolved check (d20 + mods)
    spell: Optional[SpellComponents] = None  # set only for spell-based cheats


@dataclass
class CheatRuling:
    """The adjudicator's verdict — split into a table-visible and a secret half."""

    outcome: CheatOutcome
    effective_dc: int
    suspicion_delta: int
    public: str                       # sanitized, what tablemates perceive
    private: str                      # the truth, whispered to the cheater only
    reason: str = ""                  # why (e.g. "the incantation was heard")


@dataclass
class MoveResult:
    """The envelope every ``GameEngine.apply_move`` returns.

    ``public`` lines go to the whole table; ``private`` maps a player id to lines
    only they may see (their own dice/hand). ``ok=False`` means the move was
    illegal and state is unchanged — ``error`` says why.
    """

    ok: bool
    public: List[str] = field(default_factory=list)
    private: Dict[str, List[str]] = field(default_factory=dict)
    error: str = ""

    @classmethod
    def illegal(cls, reason: str) -> "MoveResult":
        return cls(ok=False, error=reason)


@dataclass
class GameSpec:
    """Catalog entry describing one game and how to spin up its engine."""

    id: str
    name: str
    blurb: str
    min_players: int
    max_players: int
    factory: Callable[[], "object"]   # returns a fresh GameEngine
    aliases: List[str] = field(default_factory=list)
    # Does this game move real stakes by default? Friendly play always allowed.
    wagerable: bool = True
