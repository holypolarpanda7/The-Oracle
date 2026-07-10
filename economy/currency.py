"""Coin math for the SRD purse (cp/sp/ep/gp/pp).

All values are exact integers of copper internally so there is never a rounding
drift. A *purse* is a plain dict like ``{"cp": 0, "sp": 0, "ep": 0, "gp": 5, "pp": 0}``.
Missing keys are treated as 0.
"""
from __future__ import annotations

from typing import Dict, Iterable

# Value of one coin of each denomination, in copper pieces.
COIN_CP: Dict[str, int] = {"cp": 1, "sp": 10, "ep": 50, "gp": 100, "pp": 1000}

# Order used when making change (largest first). Electrum is uncommon, so by
# default we skip it when breaking a total back into coins.
_CHANGE_ORDER = ["pp", "gp", "sp", "cp"]

DENOMINATIONS = ["cp", "sp", "ep", "gp", "pp"]


def empty_purse() -> Dict[str, int]:
    return {d: 0 for d in DENOMINATIONS}


def to_cp(purse: Dict[str, int]) -> int:
    """Total value of a purse in copper pieces."""
    return sum(int(purse.get(d, 0)) * COIN_CP[d] for d in DENOMINATIONS)


def gp_value(purse: Dict[str, int]) -> float:
    """Total value of a purse expressed in gold pieces."""
    return to_cp(purse) / 100.0


def gp_to_cp(gp: float) -> int:
    """Convert a gold amount (may be fractional) to whole copper."""
    return round(gp * 100)


def from_cp(total_cp: int, *, order: Iterable[str] = _CHANGE_ORDER) -> Dict[str, int]:
    """Break a copper total into coins, largest denomination first."""
    if total_cp < 0:
        raise ValueError("Cannot make change from a negative amount")
    purse = empty_purse()
    remaining = int(total_cp)
    for d in order:
        value = COIN_CP[d]
        if remaining >= value:
            purse[d], remaining = divmod(remaining, value)
    purse["cp"] += remaining  # any leftover is copper
    return purse


def add_coins(purse: Dict[str, int], delta: Dict[str, int]) -> Dict[str, int]:
    """Return a new purse = purse + delta (per denomination)."""
    out = empty_purse()
    for d in DENOMINATIONS:
        out[d] = int(purse.get(d, 0)) + int(delta.get(d, 0))
    return out


def can_afford(purse: Dict[str, int], cost_cp: int) -> bool:
    return to_cp(purse) >= int(cost_cp)


def subtract_cost(purse: Dict[str, int], cost_cp: int) -> Dict[str, int]:
    """Pay ``cost_cp`` from a purse, auto-making change.

    Raises ``ValueError`` if the purse can't cover the cost. The returned purse
    is normalized (change is re-minted into standard coins, excluding electrum).
    """
    total = to_cp(purse)
    cost = int(cost_cp)
    if cost > total:
        raise ValueError(
            f"Insufficient funds: have {total} cp, need {cost} cp"
        )
    return from_cp(total - cost)


def format_purse(purse: Dict[str, int]) -> str:
    """Human-readable purse, e.g. ``'5 gp, 3 sp'`` (skips zero coins)."""
    parts = [
        f"{int(purse.get(d, 0))} {d}"
        for d in reversed(DENOMINATIONS)
        if int(purse.get(d, 0)) != 0
    ]
    return ", ".join(parts) if parts else "0 gp"
