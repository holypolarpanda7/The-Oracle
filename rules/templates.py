"""Monster scaling & variant templates (fully original mechanical logic).

Reskin and rescale any existing stat block (SRD or owned) into a weaker minion, a
tougher elite, a young version, a swarm, or a solo boss — without hand-authoring a
new bestiary entry. All math here is generic and self-authored.

A "monster-like" input is a plain dict shaped like ``format_monster_brief`` expects
(the keys on the ``Monster`` model): name, armor_class, hit_points, ability scores,
challenge_rating, proficiency_bonus, xp, actions (SRD action shape), etc. The output
is a new dict; nothing is persisted unless the caller chooses to store it.
"""
from __future__ import annotations

import copy
import re
from typing import Optional

from .owned_monsters import XP_BY_CR, _pb_for_cr

# Ordered CR ladder used to snap a scaled CR to a legal value.
_CR_LADDER = sorted(XP_BY_CR.keys())


# Each template multiplies/offsets core numbers. ``cr_shift`` nudges CR up/down the
# ladder AFTER the hp/damage scaling, so XP and PB stay coherent.
MONSTER_TEMPLATES: dict[str, dict] = {
    "weak": {
        "label": "Weak", "hp_mult": 0.5, "ac_delta": -1, "attack_delta": -2,
        "damage_mult": 0.6, "cr_shift": -2,
        "note": "A frail or wounded specimen — good for minions and mooks.",
    },
    "tough": {
        "label": "Tough", "hp_mult": 1.5, "ac_delta": 1, "attack_delta": 1,
        "damage_mult": 1.2, "cr_shift": 1,
        "note": "A hardened, battle-scarred version that hits a little harder.",
    },
    "elite": {
        "label": "Elite", "hp_mult": 2.0, "ac_delta": 2, "attack_delta": 2,
        "damage_mult": 1.5, "cr_shift": 3,
        "note": "A champion of its kind; a serious threat to a small party.",
    },
    "young": {
        "label": "Young", "hp_mult": 0.75, "ac_delta": -1, "attack_delta": -1,
        "damage_mult": 0.75, "cr_shift": -1, "size_shift": -1,
        "note": "A juvenile — smaller, quicker to fell.",
    },
    "boss": {
        "label": "Boss", "hp_mult": 3.0, "ac_delta": 2, "attack_delta": 2,
        "damage_mult": 1.75, "cr_shift": 4, "add_multiattack_note": True,
        "note": "A solo antagonist built to headline an encounter; consider giving "
                "it legendary resistances and an extra action each round.",
    },
    "swarm": {
        "label": "Swarm", "hp_mult": 2.5, "ac_delta": -2, "attack_delta": 0,
        "damage_mult": 1.5, "cr_shift": 1, "swarm": True,
        "note": "Represents many of the creature acting as one; resistant to "
                "single-target damage, half damage while above half HP.",
    },
}

_SIZES = ["Tiny", "Small", "Medium", "Large", "Huge", "Gargantuan"]


def list_templates() -> list[dict]:
    return [{"key": k, "label": v["label"], "note": v["note"]}
            for k, v in MONSTER_TEMPLATES.items()]


def _snap_cr(target: float) -> float:
    """Return the nearest legal CR on the ladder to ``target``."""
    if target <= _CR_LADDER[0]:
        return _CR_LADDER[0]
    if target >= _CR_LADDER[-1]:
        return _CR_LADDER[-1]
    return min(_CR_LADDER, key=lambda cr: abs(cr - target))


def _shift_cr(cr: float, steps: int) -> float:
    """Move ``steps`` positions along the CR ladder from the nearest rung."""
    base = _snap_cr(cr if cr is not None else 1)
    idx = _CR_LADDER.index(base)
    idx = max(0, min(len(_CR_LADDER) - 1, idx + steps))
    return _CR_LADDER[idx]


def _scale_damage_dice(expr: str, mult: float) -> str:
    """Scale the flat modifier and dice count of an 'NdM+K' expression.

    Dice count and the flat bonus scale with ``mult`` (rounded, min 1 die); the die
    size is unchanged. Non-standard expressions are returned unchanged.
    """
    if not expr:
        return expr
    m = re.fullmatch(r"\s*(\d+)d(\d+)\s*([+-]\s*\d+)?\s*", expr)
    if not m:
        return expr
    count = max(1, round(int(m.group(1)) * mult))
    faces = int(m.group(2))
    flat = 0
    if m.group(3):
        flat = int(m.group(3).replace(" ", ""))
    flat = round(flat * mult)
    out = f"{count}d{faces}"
    if flat:
        out += f"{flat:+d}"
    return out


def scale_monster(monster: dict, template: str, *,
                  name_override: Optional[str] = None) -> dict:
    """Return a new stat-block dict with ``template`` applied to ``monster``."""
    tpl = MONSTER_TEMPLATES.get(template)
    if not tpl:
        raise ValueError(
            f"Unknown template '{template}'. Options: {', '.join(MONSTER_TEMPLATES)}")

    out = copy.deepcopy(monster)

    # Name / label
    base_name = monster.get("name", "Creature")
    out["name"] = name_override or f"{tpl['label']} {base_name}"

    # Hit points
    if out.get("hit_points"):
        out["hit_points"] = max(1, round(out["hit_points"] * tpl["hp_mult"]))

    # Armor class
    if out.get("armor_class") is not None:
        out["armor_class"] = max(1, out["armor_class"] + tpl["ac_delta"])

    # Challenge rating / PB / XP
    new_cr = _shift_cr(monster.get("challenge_rating") or 1, tpl["cr_shift"])
    out["challenge_rating"] = new_cr
    out["proficiency_bonus"] = _pb_for_cr(new_cr)
    out["xp"] = XP_BY_CR.get(new_cr, monster.get("xp", 0))

    # Size (young shrinks a step)
    if tpl.get("size_shift") and out.get("size") in _SIZES:
        idx = _SIZES.index(out["size"])
        out["size"] = _SIZES[max(0, idx + tpl["size_shift"])]

    # Actions: shift to-hit and scale damage dice.
    scaled_actions = []
    for a in (out.get("actions") or []):
        a = dict(a)
        if a.get("attack_bonus") is not None:
            a["attack_bonus"] = a["attack_bonus"] + tpl["attack_delta"]
        if a.get("damage"):
            a["damage"] = [
                {**d, "damage_dice": _scale_damage_dice(d.get("damage_dice", ""),
                                                        tpl["damage_mult"])}
                for d in a["damage"]
            ]
        scaled_actions.append(a)
    out["actions"] = scaled_actions

    # Extra descriptive traits for swarm/boss variants.
    traits = list(out.get("special_abilities") or [])
    if tpl.get("swarm"):
        traits.insert(0, {
            "name": "Swarm",
            "desc": "The swarm can occupy another creature's space and vice versa, "
                    "and can move through any opening large enough for one of the "
                    "creatures. It has resistance to bludgeoning, piercing, and "
                    "slashing damage, and can't be grappled, restrained, or knocked "
                    "prone. While above half its hit points it takes half damage "
                    "from any single attack or effect that targets only it.",
        })
    if tpl.get("add_multiattack_note"):
        traits.insert(0, {
            "name": "Legendary Resistance (3/Day)",
            "desc": "If the boss fails a saving throw, it can choose to succeed "
                    "instead.",
        })
        traits.insert(1, {
            "name": "Boss Action",
            "desc": "The boss can take one extra action on its turn (a single attack "
                    "or a Dash, Disengage, or Hide), reflecting its solo threat.",
        })
    out["special_abilities"] = traits

    out["source"] = "Scaled variant (self-authored)"
    out["_template"] = template
    out["_base_name"] = base_name
    return out


def monster_to_dict(monster) -> dict:
    """Convert a ``Monster`` SQLModel row (or dict) into the plain dict this module
    and ``format_monster_brief`` operate on."""
    if isinstance(monster, dict):
        return dict(monster)
    keys = (
        "index_slug", "name", "size", "type", "subtype", "alignment",
        "armor_class", "ac_desc", "hit_points", "hit_dice", "strength",
        "dexterity", "constitution", "intelligence", "wisdom", "charisma",
        "challenge_rating", "proficiency_bonus", "xp", "languages", "speed",
        "senses", "special_abilities", "actions", "legendary_actions",
    )
    return {k: getattr(monster, k, None) for k in keys}
