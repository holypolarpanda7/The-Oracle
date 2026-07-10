"""
Game-aware dice mechanics — d20 checks, saving throws, attack rolls, damage.

Built on the core roller. These encode the 5e rules the DM brain needs so combat
and checks resolve with real numbers instead of guesses:

  * ability checks / saving throws: success = (roll + modifier) >= DC.
    Natural 20/1 are reported but do NOT auto-succeed/fail (per RAW).
  * attack rolls: natural 20 auto-hits and crits; natural 1 auto-misses.
  * critical damage: dice are doubled (not the total), the 5e-standard way.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from .roller import RollResult, double_dice, roll


def ability_modifier(score: Optional[int]) -> int:
    if score is None:
        return 0
    return (score - 10) // 2


def proficiency_bonus_for_level(level: int) -> int:
    return 2 + (max(1, level) - 1) // 4


def _roll_d20(rng: random.Random, advantage: bool, disadvantage: bool) -> tuple[int, list[int]]:
    """Return (kept natural, all rolled). Advantage+disadvantage cancel out."""
    r1 = rng.randint(1, 20)
    if advantage == disadvantage:  # neither, or both (cancel)
        return r1, [r1]
    r2 = rng.randint(1, 20)
    rolls = [r1, r2]
    return (max(rolls) if advantage else min(rolls)), rolls


@dataclass
class CheckResult:
    natural: int
    d20_rolls: list[int]
    modifier: int
    total: int
    dc: Optional[int]
    success: Optional[bool]
    is_nat20: bool
    is_nat1: bool
    label: str = ""
    detail: str = ""

    def __str__(self) -> str:  # pragma: no cover
        return self.detail


@dataclass
class AttackResult:
    natural: int
    d20_rolls: list[int]
    attack_bonus: int
    total: int
    target_ac: Optional[int]
    hit: Optional[bool]
    is_crit: bool
    is_fumble: bool
    label: str = ""
    detail: str = ""

    def __str__(self) -> str:  # pragma: no cover
        return self.detail


def ability_check(
    modifier: int = 0,
    *,
    dc: Optional[int] = None,
    advantage: bool = False,
    disadvantage: bool = False,
    label: str = "",
    rng: Optional[random.Random] = None,
) -> CheckResult:
    rng = rng or random
    natural, rolls = _roll_d20(rng, advantage, disadvantage)
    total = natural + modifier
    success = None if dc is None else total >= dc
    adv = " (adv)" if advantage and not disadvantage else " (dis)" if disadvantage and not advantage else ""
    shown = f"[{max(rolls)}/{min(rolls)}]{adv}" if len(rolls) > 1 else f"[{natural}]"
    lbl = f"{label}: " if label else ""
    outcome = ""
    if dc is not None:
        outcome = f" vs DC {dc} → {'SUCCESS' if success else 'FAIL'}"
    detail = f"{lbl}d20{shown}{modifier:+d} = {total}{outcome}"
    return CheckResult(natural, rolls, modifier, total, dc, success,
                       natural == 20, natural == 1, label, detail)


# Saving throws share the ability-check mechanic.
saving_throw = ability_check


def attack_roll(
    attack_bonus: int = 0,
    target_ac: Optional[int] = None,
    *,
    advantage: bool = False,
    disadvantage: bool = False,
    label: str = "",
    rng: Optional[random.Random] = None,
) -> AttackResult:
    rng = rng or random
    natural, rolls = _roll_d20(rng, advantage, disadvantage)
    total = natural + attack_bonus
    is_crit = natural == 20
    is_fumble = natural == 1
    if is_crit:
        hit: Optional[bool] = True
    elif is_fumble:
        hit = False
    elif target_ac is not None:
        hit = total >= target_ac
    else:
        hit = None
    adv = " (adv)" if advantage and not disadvantage else " (dis)" if disadvantage and not advantage else ""
    shown = f"[{max(rolls)}/{min(rolls)}]{adv}" if len(rolls) > 1 else f"[{natural}]"
    lbl = f"{label}: " if label else ""
    tag = " CRIT!" if is_crit else " (nat 1)" if is_fumble else ""
    outcome = ""
    if target_ac is not None:
        outcome = f" vs AC {target_ac} → {'HIT' if hit else 'MISS'}"
    detail = f"{lbl}atk d20{shown}{attack_bonus:+d} = {total}{outcome}{tag}"
    return AttackResult(natural, rolls, attack_bonus, total, target_ac, hit,
                        is_crit, is_fumble, label, detail)


def damage_roll(
    expression: str,
    *,
    crit: bool = False,
    rng: Optional[random.Random] = None,
) -> RollResult:
    """Roll damage. On a crit, dice are doubled (flat modifiers are not)."""
    expr = double_dice(expression) if crit else expression
    result = roll(expr, rng=rng)
    if crit:
        result.detail = f"CRIT {result.detail}"
    return result
