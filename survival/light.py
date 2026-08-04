"""Light sources and vision. Torches burn down; darkness has teeth.

Burn times come from ``config.survival``. The vision helper resolves what a
creature can effectively see given the ambient light level and darkvision.
"""
from __future__ import annotations

import re
from typing import Dict, Optional

from game_config import get_config

# radius in feet (bright, dim). Minutes are pulled from config where applicable.
_SOURCES = {
    "torch":     {"bright": 20, "dim": 40, "minutes_key": "torch_minutes"},
    "lantern":   {"bright": 30, "dim": 60, "minutes_key": "lantern_minutes"},
    "candle":    {"bright": 5, "dim": 10, "minutes_key": "candle_minutes"},
    "campfire":  {"bright": 20, "dim": 40, "minutes": 480},
    "everburning": {"bright": 20, "dim": 40, "minutes": None},  # magical, never runs out
}

LIGHT_LEVELS = ("bright", "dim", "dark")

#: Brightest wins. A torch in a dark room makes its own square bright; nothing
#: makes a bright square darker except obscurement, which is handled apart.
LIGHT_ORDER = {"dark": 0, "dim": 1, "bright": 2}


def brighter(a: str, b: str) -> str:
    """The lighter of two light levels."""
    return a if LIGHT_ORDER.get(a, 2) >= LIGHT_ORDER.get(b, 2) else b


def darker(a: str, b: str) -> str:
    """The darker of two light levels — what obscurement does to a square."""
    return a if LIGHT_ORDER.get(a, 2) <= LIGHT_ORDER.get(b, 2) else b


#: The senses that come with a range in feet. Passive Perception is not one of
#: them, and neither is "blind beyond this radius" prose.
_RANGED_SENSES = ("darkvision", "blindsight", "truesight", "tremorsense")

#: "Blindsight 30ft.", "Darkvision 60 ft.", "tremorsense 60'" — one pattern for
#: every way a book prints it.
_SENSE_TEXT = re.compile(
    r"(darkvision|blindsight|truesight|tremorsense)\s*[:\-]?\s*(\d+)\s*(?:ft|feet|')",
    re.I)


def parse_senses(raw: Optional[Dict]) -> Dict[str, int]:
    """Normalise a stat block's senses into ``{sense: feet}``.

    The bestiary stores them the way a book prints them
    (``{"darkvision": "60 ft.", "passive_perception": 15}``) and a species
    stores darkvision as a bare bool. Both arrive here and leave as numbers,
    because the board measures in feet and cannot do anything with "60 ft.".
    Anything that isn't a sense with a range — passive Perception above all —
    is dropped rather than guessed at.
    """
    out: Dict[str, int] = {}
    for key, value in (raw or {}).items():
        name = str(key).strip().lower().replace(" ", "_")
        if name not in _RANGED_SENSES:
            # Not every stat block arrives tidy. A monster parsed out of a PDF
            # keeps its senses line whole ("Blindsight 30ft.;PassivePerception
            # 13"), and reading only the well-formed rows quietly costs the
            # wolf its darkvision and the grimlock its blindsight — a large
            # share of the bestiary, failing silently in the direction of
            # "sees nothing in the dark".
            if name in ("raw", "text", "senses") and isinstance(value, str):
                for sense, feet in _SENSE_TEXT.findall(value):
                    out.setdefault(sense.lower(), int(feet))
            continue
        if value is True:                       # a species flag: the 5e default
            out[name] = 60
            continue
        if isinstance(value, (int, float)):
            feet = int(value)
        else:
            digits = "".join(c for c in str(value) if c.isdigit())
            if not digits:
                continue
            feet = int(digits)
        if feet > 0:
            out[name] = feet
    return out


def perceives(light_level: str, distance_ft: float, senses: Optional[Dict] = None,
              *, obscured: str = "", grounded: bool = True) -> Dict:
    """Can a creature with these senses make something out, at this distance?

    THE one answer to "can it see that", so the board, the DM's board text and
    the combat engine's advantage calculation cannot drift apart. Returns
    ``{sees, via, obscured, note}`` — ``via`` names the sense that carried it,
    which is what the narration needs ("you hear it moving, you can't see it").

    The order matters and is 5e's: senses that don't use light are checked
    first, because blindsight is not improved vision, it is a different way of
    knowing where something is. Heavy obscurement (a fog cloud) blinds anything
    relying on light no matter how bright the square is; light obscurement
    (dim light, thin smoke) does not blind, it only costs you a Perception
    check. Darkvision does not turn night into day — it turns dark into dim,
    which still carries that penalty.
    """
    s = {k: int(v) for k, v in (senses or {}).items() if int(v or 0) > 0}
    d = max(0.0, float(distance_ft))
    level = light_level if light_level in LIGHT_LEVELS else "bright"

    if s.get("blindsight", 0) >= d:
        return {"sees": True, "via": "blindsight", "obscured": "",
                "note": "perceived without sight"}
    if s.get("truesight", 0) >= d:
        return {"sees": True, "via": "truesight", "obscured": "",
                "note": "truesight"}
    if grounded and s.get("tremorsense", 0) >= d:
        return {"sees": True, "via": "tremorsense", "obscured": "",
                "note": "felt through the ground, not seen"}

    if obscured == "heavy":
        return {"sees": False, "via": "", "obscured": "heavy",
                "note": "heavily obscured — effectively blinded"}
    if level == "bright":
        return {"sees": True, "via": "sight", "obscured": obscured,
                "note": "clearly visible"}
    if level == "dim":
        return {"sees": True, "via": "sight", "obscured": "light",
                "note": "dim light — lightly obscured, Perception at disadvantage"}
    if s.get("darkvision", 0) >= d:
        return {"sees": True, "via": "darkvision", "obscured": "light",
                "note": "darkvision — seen as if in dim light, in shades of grey"}
    return {"sees": False, "via": "", "obscured": "heavy",
            "note": "darkness beyond its sight — effectively blinded"}


def light_sources() -> Dict:
    return _SOURCES


def source_spec(kind: str) -> Optional[Dict]:
    spec = _SOURCES.get(kind)
    if not spec:
        return None
    cfg = get_config().survival
    minutes = spec.get("minutes")
    if "minutes_key" in spec:
        minutes = getattr(cfg, spec["minutes_key"])
    return {
        "kind": kind,
        "bright_radius": spec["bright"],
        "dim_radius": spec["dim"],
        "minutes": minutes,  # None = never runs out
    }


def burn(kind: str, minutes_remaining: Optional[int], minutes_elapsed: int) -> Dict:
    """Advance a lit source. Returns remaining fuel and whether it went out."""
    spec = source_spec(kind)
    if not spec:
        return {"error": f"Unknown light source '{kind}'."}
    if spec["minutes"] is None:
        return {"kind": kind, "minutes_remaining": None, "went_out": False,
                "note": f"{kind} is inexhaustible."}
    if minutes_remaining is None:
        minutes_remaining = spec["minutes"]
    remaining = max(0, int(minutes_remaining) - int(minutes_elapsed))
    return {
        "kind": kind,
        "minutes_remaining": remaining,
        "went_out": remaining <= 0,
        "note": (f"{kind} sputters out." if remaining <= 0
                 else f"{kind}: {remaining} min of fuel left."),
    }


def effective_vision(light_level: str, *, has_darkvision: bool = False,
                     darkvision_ft: int = 60) -> Dict:
    """What a creature effectively perceives at a light level."""
    level = light_level if light_level in LIGHT_LEVELS else "bright"
    if level == "bright":
        return {"sees": "normally", "perception_disadvantage": False}
    if level == "dim":
        # Dim light is lightly obscured -> disadvantage on sight Perception.
        if has_darkvision:
            return {"sees": "normally (darkvision treats dim as bright)",
                    "perception_disadvantage": False}
        return {"sees": "lightly obscured", "perception_disadvantage": True}
    # dark
    if has_darkvision:
        return {"sees": f"dim within {darkvision_ft} ft (darkvision)",
                "perception_disadvantage": True,
                "note": "Beyond darkvision range the creature is effectively blinded."}
    return {"sees": "blinded", "perception_disadvantage": True,
            "note": "Attacks vs unseen creatures have disadvantage; attacks against you have advantage."}
