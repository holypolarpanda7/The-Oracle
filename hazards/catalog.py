"""Owned hazard catalogs: diseases, traps, and madness.

These are self-authored, concise *mechanical* summaries of DMG-style hazards (the
player owns the book) — never verbatim prose. Save DCs default to the values in
``config.hazard`` but individual entries may override. Tagged ``OWNED_SOURCE``.
"""
from __future__ import annotations

from typing import Dict, List, Optional

try:
    from rules import OWNED_SOURCE
except Exception:  # pragma: no cover
    OWNED_SOURCE = "Owned (non-SRD)"


# --- Diseases -------------------------------------------------------------
# incubation_days: onset delay; save: Con unless noted; effect: what failure does.
_DISEASES: List[Dict] = [
    {"slug": "sewer-plague", "name": "Sewer Plague", "incubation_days": 1,
     "save": "constitution",
     "effect": "On each failed save after a long rest: no HP recovered and gain 1 exhaustion. "
               "Recover after two consecutive successful saves.",
     "onset": "Fatigue and cramps 1d4 days after infection."},
    {"slug": "sight-rot", "name": "Sight Rot", "incubation_days": 1,
     "save": "constitution",
     "effect": "-1 penalty to attack rolls and sight-based checks each day (cumulative); "
               "blindness at -5. Cured by a rare ointment (eyebright).",
     "onset": "Bleary, aching eyes within a day."},
    {"slug": "cackle-fever", "name": "Cackle Fever", "incubation_days": 1,
     "save": "constitution",
     "effect": "1d10 psychic damage on a failed save; while infected, a sudden shock can trigger "
               "fits of mad laughter (Con save or incapacitated 1 minute).",
     "onset": "High fever and disorientation; targets humanoids."},
    {"slug": "filth-fever", "name": "Filth Fever", "incubation_days": 1,
     "save": "constitution",
     "effect": "While infected, disadvantage on attack rolls and Strength checks/saves.",
     "onset": "Feverish weakness from a filthy wound."},
    {"slug": "mindfire", "name": "Mindfire", "incubation_days": 1,
     "save": "constitution",
     "effect": "While infected, disadvantage on Intelligence checks and saves; may act as if confused.",
     "onset": "Burning behind the eyes; thoughts turn to fog."},
]


# --- Traps ---------------------------------------------------------------
_TRAPS: List[Dict] = [
    {"slug": "pit", "name": "Simple Pit", "detect_dc": 10, "disarm_dc": 12,
     "trigger": "Stepping on the covered opening.",
     "effect": "Fall into a 10-ft pit (1d6 bludgeoning); DC 12 Dex save to catch the edge."},
    {"slug": "poison-darts", "name": "Poison Darts", "detect_dc": 15, "disarm_dc": 15,
     "trigger": "A pressure plate releases a volley of darts.",
     "effect": "Each dart: +8 to hit, 1d4 piercing + 2d6 poison; DC 13 Con half poison."},
    {"slug": "poison-needle", "name": "Poison Needle", "detect_dc": 20, "disarm_dc": 15,
     "trigger": "Opening a lock or drawer without disarming the needle.",
     "effect": "1 piercing damage and 1d4 poison; DC 15 Con save or be poisoned 1 hour."},
    {"slug": "fire-rune", "name": "Fire Glyph", "detect_dc": 15, "disarm_dc": 17,
     "trigger": "Reading or crossing the warded threshold.",
     "effect": "20-ft fire burst, 4d6 fire; DC 15 Dex half."},
    {"slug": "collapsing-roof", "name": "Collapsing Roof", "detect_dc": 15, "disarm_dc": 15,
     "trigger": "A tripwire pulls the supports.",
     "effect": "Falling debris in a 10-ft area, 4d10 bludgeoning; DC 15 Dex half; area becomes rubble."},
    {"slug": "rolling-sphere", "name": "Rolling Stone Sphere", "detect_dc": 15, "disarm_dc": 20,
     "trigger": "A pressure plate releases a great rolling boulder.",
     "effect": "DC 15 Dex save or 10d10 bludgeoning and be knocked prone; must outrun it (Dash)."},
]


# --- Madness -------------------------------------------------------------
# Short madness lasts 1d10 minutes; long madness 1d10 x 10 hours; indefinite until cured.
_SHORT_MADNESS = [
    "Retreats into the mind, paralyzed with fear, until shaken out of it.",
    "Becomes incapacitated and screams, weeps, or laughs uncontrollably.",
    "Is frightened and must use its action and movement to flee.",
    "Begins babbling and is incapable of normal speech or spellcasting.",
    "Must use its action to attack the nearest creature.",
    "Experiences vivid hallucinations and has disadvantage on ability checks.",
    "Does whatever anyone tells it to that isn't obviously self-destructive.",
    "Is stunned by an overwhelming compulsion to eat something strange.",
    "Falls unconscious.",
]

_LONG_MADNESS = [
    "Feels compelled to repeat a specific activity over and over.",
    "Experiences vivid hallucinations and has disadvantage on ability checks.",
    "Suffers extreme paranoia; has disadvantage on Wisdom and Charisma checks.",
    "Regards something (often the source) with intense revulsion or fear.",
    "Experiences a powerful delusion; convinced of an untrue and dangerous belief.",
    "Becomes attached to a 'lucky charm' and is incapacitated without it.",
    "Is blinded (or deafened) by psychosomatic response.",
    "Suffers uncontrollable tremors or tics: disadvantage on attacks and some checks.",
]

_INDEFINITE_MADNESS = [
    "'Being drunk keeps the nightmares away.' — must indulge to function.",
    "'I must hide the truth; no one can know what I've seen.'",
    "'That thing is watching me. It's always watching.'",
    "'I am the only one who can be trusted to do this right.'",
    "'I will do anything to avoid being touched.'",
    "'Fortune favors me; I take reckless risks.'",
    "'I am not worthy of anyone's help.'",
    "'I can't stop thinking about the horror I witnessed.'",
]

MADNESS_TABLES = {
    "short": _SHORT_MADNESS,
    "long": _LONG_MADNESS,
    "indefinite": _INDEFINITE_MADNESS,
}


DISEASES: Dict[str, Dict] = {d["slug"]: {**d, "type": "disease", "source": OWNED_SOURCE}
                             for d in _DISEASES}
TRAPS: Dict[str, Dict] = {t["slug"]: {**t, "type": "trap", "source": OWNED_SOURCE}
                          for t in _TRAPS}


def get_disease(slug: str) -> Optional[Dict]:
    return DISEASES.get(slug)


def get_trap(slug: str) -> Optional[Dict]:
    return TRAPS.get(slug)


def list_diseases() -> List[Dict]:
    return list(DISEASES.values())


def list_traps() -> List[Dict]:
    return list(TRAPS.values())
