"""
Core dice engine — parse and roll standard tabletop dice expressions.

Supports:
  * ``NdM``           e.g. 2d6, d20 (N defaults to 1)
  * flat modifiers    e.g. +3, -1
  * multiple terms    e.g. 1d8+1d6+2
  * keep highest/low  e.g. 4d6kh3 (keep highest 3), 2d20kl1 (keep lowest 1)

Results are structured (individual die faces are preserved) so callers can narrate
the breakdown, detect crits, or apply house rules.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Optional

# One signed term: optional NdM with optional keep-highest/lowest, or a flat int.
_TERM = re.compile(r"^(\d*)d(\d+)(?:(kh|kl)(\d+))?$", re.IGNORECASE)
_DICE_ONLY = re.compile(r"(\d*)d(\d+)", re.IGNORECASE)


@dataclass
class RollResult:
    expression: str
    total: int
    rolls: list[int] = field(default_factory=list)      # kept die faces
    dropped: list[int] = field(default_factory=list)    # dropped by kh/kl
    modifier: int = 0                                    # sum of flat terms
    detail: str = ""

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.detail or f"{self.expression} = {self.total}"


def roll(expression: str, *, rng: Optional[random.Random] = None) -> RollResult:
    """Roll a dice expression like ``"2d6+3"`` and return a structured result."""
    rng = rng or random
    expr = expression.replace(" ", "")
    if not expr:
        raise ValueError("Empty dice expression")

    tokens = re.findall(r"[+-]?[^+-]+", expr)
    total = 0
    all_rolls: list[int] = []
    all_dropped: list[int] = []
    modifier = 0
    parts: list[str] = []

    for tok in tokens:
        sign = 1
        body = tok
        if body[0] == "+":
            body = body[1:]
        elif body[0] == "-":
            sign = -1
            body = body[1:]

        m = _TERM.match(body)
        if m:
            n = int(m.group(1)) if m.group(1) else 1
            sides = int(m.group(2))
            if n <= 0 or sides <= 0:
                raise ValueError(f"Invalid dice term: {tok}")
            faces = [rng.randint(1, sides) for _ in range(n)]
            kept, dropped = faces, []
            keep_mode = m.group(3)
            if keep_mode:
                keep_n = int(m.group(4))
                ordered = sorted(faces, reverse=keep_mode.lower() == "kh")
                kept, dropped = ordered[:keep_n], ordered[keep_n:]
            total += sign * sum(kept)
            all_rolls.extend(kept)
            all_dropped.extend(dropped)
            shown = f"{tok}{faces}" + (f" drop{dropped}" if dropped else "")
            parts.append(shown)
        else:
            try:
                val = int(body)
            except ValueError as exc:  # noqa: TRY003
                raise ValueError(f"Unrecognized term '{tok}' in '{expression}'") from exc
            total += sign * val
            modifier += sign * val
            parts.append(tok)

    detail = f"{expression} → {'  '.join(parts)} = {total}"
    return RollResult(expression, total, all_rolls, all_dropped, modifier, detail)


def double_dice(expression: str) -> str:
    """Return ``expression`` with every dice count doubled (5e critical-hit damage)."""
    def repl(m: re.Match) -> str:
        n = int(m.group(1)) if m.group(1) else 1
        return f"{n * 2}d{m.group(2)}"

    return _DICE_ONLY.sub(repl, expression)


def contains_dice(expression: str) -> bool:
    return bool(_DICE_ONLY.search(expression.replace(" ", "")))
