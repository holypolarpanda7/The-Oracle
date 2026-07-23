"""The retribution engine — what befalls a PC who spills another PC's blood.

Deterministic and dependency-light: given the act, the two levels, the avenging
power, whether the fight was authorized, and how many kinslayings the aggressor
already has, it returns a graded consequence package. The backend then applies the
pieces by reusing existing machinery (curse Afflictions, alignment drift, HP/death
writes, reputation). Authorized duels carry NO divine wrath — only the weight of the
fiction. Punching DOWN (killing someone far weaker) and repeat kinslaying escalate,
and a lawful/good avenger strikes hardest.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from . import deities


@dataclass
class PvpOutcome:
    act: str                       # "strike" | "kill"
    authorized: bool
    severity: int                  # 0–100
    smite: str = "none"            # none | hp | down | death
    smite_fraction: float = 0.0    # for smite=="hp": fraction of the killer's max HP
    curse: Optional[Dict] = None   # {name, effect, lift} for a Kinslayer's Mark
    align_shift: Dict[str, int] = field(default_factory=dict)  # {"good":-x,"law":-y}
    renown_delta: int = 0
    retributor: Dict = field(default_factory=dict)
    public: str = ""               # narrated to the table
    private: str = ""              # whispered to the aggressor


def _clamp(v: int, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(v)))


def assess(act: str, attacker_level: int, victim_level: int, retributor: Dict, *,
           authorized: bool, offense_count: int = 0,
           divine_retribution: bool = True, lethal_retribution: bool = True,
           punchdown_scaling: int = 4) -> PvpOutcome:
    """Compute the consequence package for one PvP act."""
    god = deities.full_name(retributor)
    gap = int(attacker_level or 1) - int(victim_level or 1)   # >0 = punching down

    # A fair, consented duel: no divine wrath, no curse — only the fiction's weight.
    if authorized:
        if act == "kill":
            return PvpOutcome(act, True, severity=0, retributor=retributor,
                              public=f"The duel is ended — a death met on agreed terms; "
                                     f"even {god} stays their hand.",
                              private="A sanctioned duel. No curse follows a fair kill — "
                                      "but the memory of it will.")
        return PvpOutcome(act, True, severity=0, retributor=retributor,
                          public="Blades ring in a sanctioned duel.",
                          private="A fair fight — no consequence follows.")

    if not divine_retribution:
        return PvpOutcome(act, False, severity=0, retributor=retributor,
                          public="", private="(Divine retribution is disabled at this table.)")

    # ---- Unsanctioned aggression ----
    if act == "strike":
        sev = _clamp(22 + max(0, gap) * 3 + offense_count * 8)
        out = PvpOutcome("strike", False, severity=sev, retributor=retributor,
                         align_shift={"good": -(sev // 12), "law": -(sev // 20)},
                         renown_delta=-(sev // 12))
        out.public = ("An unseen chill answers the blow — this was no sanctioned fight, "
                      "and something took note.")
        out.private = (f"⚠ Unsanctioned violence against another soul. {god} is watching. "
                       "Draw more blood and the price will come due.")
        if offense_count >= 1 or sev >= 40:
            out.curse = {
                "name": "Mark of the Aggressor",
                "effect": "a cold unease dogs you; those who see it distrust your hand",
                "lift": "make peace with the one you wronged, or atone before a shrine",
            }
        return out

    # act == "kill": full retribution, scaled.
    base = 60
    sev = base + max(0, gap) * int(punchdown_scaling) + offense_count * 15
    sev += deities.wrath_modifier(retributor)
    sev = _clamp(sev)

    if lethal_retribution and sev >= 85:
        smite, frac = "death", 1.0
    elif sev >= 60:
        smite, frac = "down", 0.0
    else:
        smite, frac = "hp", min(0.9, 0.4 + sev / 200.0)

    punch = " — and you struck down one far weaker" if gap >= 3 else ""
    out = PvpOutcome(
        "kill", False, severity=sev, smite=smite, smite_fraction=frac,
        retributor=retributor,
        align_shift={"good": -(sev // 4), "law": -(sev // 8)},
        renown_delta=-(sev // 3),
        curse={
            "name": "Kinslayer's Mark",
            "effect": ("penalty=2 your wounds knit slow and will not fully close; the "
                       "slain lingers at the edge of your sight"
                       + (" — you can't regain HP by any means until it lifts"
                          if sev >= 75 else "")),
            "lift": (f"atone for the murder — restore the slain to life, or complete a "
                     f"rite of penance at a temple of {god}"),
        },
    )
    smite_word = {"death": f"{god} strikes you dead where you stand",
                  "down": f"{god}'s wrath hurls you down, broken and dying",
                  "hp": f"{god}'s judgement sears through you"}[smite]
    out.public = (f"The heavens answer the murder{punch}. {smite_word} — a Kinslayer's "
                  f"Mark burns onto the killer, and the world will remember the deed.")
    out.private = (f"You have murdered another soul. {god} passes judgement: {smite_word}. "
                   f"The Kinslayer's Mark is upon you until you atone.")
    return out
