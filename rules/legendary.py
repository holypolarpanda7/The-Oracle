"""What makes a boss a boss: Legendary Resistance and legendary actions.

59 monsters in this bestiary carry ``legendary_actions`` — every adult dragon,
the aboleth, the liches. The combat engine contained no reference to the column
at all, and ``format_monster_brief`` did not print it either, so a CR 14 dragon
both fought like a brute AND gave the DM nothing to narrate one with.

Legendary Resistance was worse, because it silently changes outcomes rather than
just flavour: it lives as a sentence inside ``special_abilities`` —

    "Legendary Resistance (3/Day, or 4/Day in Lair). If the dragon fails a
     saving throw, it can choose to succeed instead."

— and nothing read it, so every save-or-suck landed on the first try. A Hold
Monster on an ancient dragon simply worked.

Both are parsed out of the stat block's own prose, OCR damage and all (the
extractor writes "1f" for "If" and strips the spaces out of "3/Day,or"). What
the engine then does with them is deliberately split:

  * **Resistance is ENFORCED** — a failed save is converted to a success and a
    use is spent, because that is arithmetic and the engine owns arithmetic.
  * **Actions are SURFACED** — they are narrative options taken between other
    creatures' turns, so the DM is given them, with their budget, and spends
    them through the ordinary combat hooks. The engine does not schedule them.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

#: "Legendary Resistance (3/Day", "(3/Day,or 4/Day in Lair)" — the first number
#: is the one that applies outside a lair, which is where fights usually happen.
_LR_RX = re.compile(r"Legendary\s+Resistance\s*\(\s*(\d+)\s*/\s*Day", re.I)

#: The 2024 stat block prints "Legendary Action Uses: 3 (4 in Lair)". This
#: bestiary's parse dropped that line, so the near-universal 3 is the default
#: and is stated as one rather than pretended to be read.
DEFAULT_ACTION_USES = 3
_USES_RX = re.compile(r"Legendary\s+Action\s+Uses?\s*:?\s*(\d+)", re.I)


def _texts(monster: Any) -> str:
    """Every prose field a stat block might hide a legendary line in."""
    parts: List[str] = []
    for attr in ("special_abilities", "legendary_actions", "actions", "desc"):
        val = getattr(monster, attr, None)
        if isinstance(val, str):
            parts.append(val)
        elif isinstance(val, list):
            for row in val:
                if isinstance(row, dict):
                    parts.append(f"{row.get('name', '')} {row.get('desc', '')}")
                elif isinstance(row, str):
                    parts.append(row)
    return " ".join(parts)


def resistance_uses(monster: Any) -> int:
    """Legendary Resistances per day, or 0 for a creature that has none."""
    m = _LR_RX.search(_texts(monster))
    return int(m.group(1)) if m else 0


def actions_of(monster: Any) -> List[Dict[str, str]]:
    """The creature's legendary actions as ``{name, desc}`` rows."""
    raw = getattr(monster, "legendary_actions", None)
    out: List[Dict[str, str]] = []
    if isinstance(raw, list):
        for row in raw:
            if isinstance(row, dict) and (row.get("name") or row.get("desc")):
                out.append({"name": str(row.get("name") or "").strip(),
                            "desc": str(row.get("desc") or "").strip()})
            elif isinstance(row, str) and row.strip():
                out.append({"name": "", "desc": row.strip()})
    return out


def action_uses(monster: Any) -> int:
    """How many legendary actions it may spend per round."""
    if not actions_of(monster):
        return 0
    m = _USES_RX.search(_texts(monster))
    return int(m.group(1)) if m else DEFAULT_ACTION_USES


def is_legendary(monster: Any) -> bool:
    return bool(actions_of(monster)) or resistance_uses(monster) > 0


def _tidy(text: str, limit: int = 150) -> str:
    """The extractor strips spaces after punctuation; put enough back to read."""
    t = re.sub(r"­\s*", "", text or "")
    t = re.sub(r"([,.:;])(?=[A-Za-z])", r"\1 ", t)
    t = re.sub(r"(?<=[a-z])(?=[A-Z][a-z])", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return (t[:limit].rsplit(" ", 1)[0] + "…") if len(t) > limit else t


def brief(monster: Any) -> str:
    """The block a DM needs to actually run the thing. "" when it isn't a boss."""
    lines: List[str] = []
    lr = resistance_uses(monster)
    if lr:
        lines.append(f"Legendary Resistance: {lr}/day — a failed save becomes a "
                     f"success (the engine spends these automatically).")
    acts = actions_of(monster)
    if acts:
        n = action_uses(monster)
        lines.append(f"Legendary actions: {n} per round, one at a time, at the "
                     f"end of ANOTHER creature's turn (not its own):")
        for a in acts[:4]:
            name = a["name"] or "Action"
            lines.append(f"  - {name}: {_tidy(a['desc'])}")
    return "\n".join(lines)
