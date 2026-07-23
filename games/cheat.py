"""Server-held cheat adjudication.

Given how brazen the method is, the cheater's resolved skill check, the current
table heat, and — for a spell — whether its casting is perceptible, this rules the
attempt on a graded scale and splits the result into a *public* line (what
tablemates perceive) and a *private* line (the truth, whispered to the cheater).
The rule the user asked for lives here: a non-obvious method can slip by, but a
spell with a verbal, somatic, or material component is *seen* — the table catches
the casting regardless of the roll. Keeping this server-side means the model can't
fudge whether a cheat worked or leak that one is underway.
"""
from __future__ import annotations

from .models import CheatAttempt, CheatOutcome, CheatRuling
from .suspicion import dc_modifier

# Heat added by each outcome — cleaner cheats barely register; sloppy ones burn.
_HEAT_BY_OUTCOME = {
    CheatOutcome.CLEAN: 1,
    CheatOutcome.TRACE: 4,
    CheatOutcome.SUSPECTED: 12,
    CheatOutcome.CAUGHT: 25,
}


def adjudicate(attempt: CheatAttempt, heat: int) -> CheatRuling:
    """Rule on one cheat attempt against the current table heat."""
    base = attempt.detectability.base_dc
    mod = dc_modifier(heat)
    dc = base + mod

    # A spell betrays itself if any component is perceptible (and it isn't subtle).
    if attempt.spell is not None and attempt.spell.perceptible:
        seen = [n for n, on in (("verbal incantation", attempt.spell.verbal),
                                ("somatic gesture", attempt.spell.somatic),
                                ("material component", attempt.spell.material)) if on]
        reason = "the " + " and ".join(seen) + " gave it away"
        return CheatRuling(
            outcome=CheatOutcome.CAUGHT, effective_dc=dc,
            suspicion_delta=_HEAT_BY_OUTCOME[CheatOutcome.CAUGHT],
            public=f"The spellcasting is unmistakable — {reason}; every eye snaps over.",
            private=f"Caught cold: {reason}. A perceptible spell is no quiet cheat.",
            reason=reason)

    total = attempt.skill_total
    if total >= dc + 5:
        outcome = CheatOutcome.CLEAN
    elif total >= dc:
        outcome = CheatOutcome.TRACE
    elif total >= dc - 5:
        outcome = CheatOutcome.SUSPECTED
    else:
        outcome = CheatOutcome.CAUGHT

    delta = _HEAT_BY_OUTCOME[outcome]
    public, private = _describe(outcome, attempt, dc, total)
    return CheatRuling(outcome=outcome, effective_dc=dc, suspicion_delta=delta,
                       public=public, private=private)


def _describe(outcome: CheatOutcome, attempt: CheatAttempt, dc: int, total: int):
    method = attempt.method or "your ploy"
    if outcome is CheatOutcome.CLEAN:
        return ("", f"{method} lands perfectly — nobody saw a thing "
                    f"(rolled {total} vs DC {dc}). It works.")
    if outcome is CheatOutcome.TRACE:
        return ("Something about the last hand sat slightly wrong, though no one "
                "could say what.",
                f"{method} works, but you left a faint tell "
                f"({total} vs DC {dc}). Heat ticks up.")
    if outcome is CheatOutcome.SUSPECTED:
        return ("A player's gaze lingers on your hands a moment too long.",
                f"{method} works — but you were nearly made "
                f"({total} vs DC {dc}). Someone is suspicious now.")
    return (f"Your {method} is spotted mid-move — the table sees it.",
            f"Caught: {method} failed ({total} vs DC {dc}). It did NOT work and "
            f"the table knows.")
