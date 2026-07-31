"""Give two characters of the same species and class different faces.

``_portrait_base_look`` builds ``"<gender>, <race>, <class>"`` and, for a PC
whose player never wrote a description, that is the WHOLE prompt: nothing about
age, build, face shape or features. Measured, six seeds of "male, human,
fighter" came back as six pictures of the same slim, handsome, dark-haired man
in his late twenties — a thin descriptor lets the model's own prior fill the
vacuum, the same failure the goliath descriptor had.

A random face per render would fix the sameness and break something more
important: a character's portrait has to be STABLE. The gear looks, and every
re-render, are built from this seed, and a face that re-rolls hands back a
stranger wearing the right armour. So the traits are drawn deterministically
from the character's identity — same character, same face, forever; different
characters, different faces.

**The roll only covers what a species descriptor does NOT.** Species looks
already dictate skin and hair for everyone except plain humans (a tiefling is
red with dark hair; a goliath is ashen grey), so rolling "auburn hair, olive
complexion" onto one would fight the descriptor that four passes of work went
into. Age, build, face shape, nose, eye set and distinguishing marks are safe
on ANY species and are what actually makes faces tell apart; colouring is added
only for the human tier, where nothing else claims it.
"""
from __future__ import annotations

import hashlib
import random
from typing import List, Optional

# Safe on every species — a goliath and a halfling can both be heavy-jawed and
# past fifty. These carry the variety.
_AGE = [
    "barely out of their teens", "in their early twenties",
    "in their late twenties", "around thirty", "in their mid thirties",
    "in their forties, lined", "past fifty and weathered",
    "old, grizzled and deeply lined",
]
_BUILD = [
    "wiry and lean", "broad and heavy-set", "tall and rangy",
    "short and thick-necked", "stocky and barrel-chested",
    "narrow-shouldered and slight", "solid and square", "gaunt and long-limbed",
]
_FACE = [
    "a long narrow face", "a broad square face", "a round full face",
    "a gaunt hollow-cheeked face", "a heavy-jawed face", "a soft oval face",
    "a sharp angular face", "a wide flat face with high cheekbones",
]
_NOSE = [
    "a hooked nose", "a broad flat nose", "a straight fine nose",
    "a crooked once-broken nose", "a snub nose", "a long aquiline nose",
    "a bulbous nose",
]
_EYES = [
    "deep-set eyes", "wide-set eyes", "small close-set eyes",
    "heavy-lidded eyes", "large round eyes", "narrow hooded eyes",
]
# Roughly a third get nothing: a table where EVERY face has a scar reads as
# costume, and the absence is what makes the marked ones land.
_MARK = [
    "", "", "", "",
    "a pale scar across one cheek", "one eyebrow split by an old scar",
    "heavy freckling", "a crooked half-smile", "deep laugh lines",
    "a birthmark at the temple", "a notched ear", "a heavy scowling brow",
    "a broken-toothed grin", "sun-squint lines at the eyes",
]

# Human tier ONLY — every other species has its colouring claimed by its own
# descriptor, and a second opinion in the same prompt is how you get a
# two-tone blend (see the goliath's "blue-grey (or a dull brick red)").
_HAIR_COLOUR = [
    "black", "dark brown", "chestnut", "sandy", "ash-blond", "red",
    "auburn", "iron-grey", "white", "greying at the temples",
]
_HAIR_STYLE = [
    "cropped short", "shaved to stubble", "shoulder-length and loose",
    "tied back in a tail", "thick and unruly", "receding", "braided",
    "lank and shoulder-length", "close-curled",
]
_COMPLEXION = [
    "pale", "fair and freckled", "ruddy", "olive", "tanned and weathered",
    "deep brown", "dark", "sallow",
]


def _rng(key: str) -> random.Random:
    """A stable RNG for a character key.

    ``hash()`` is salted per process in Python 3 and would give a character a
    different face on every backend restart, which is precisely the bug this
    module exists to avoid. Hash explicitly.
    """
    digest = hashlib.sha256(key.strip().lower().encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def roll_appearance(key: str, race: str = "") -> str:
    """A deterministic face for ``key`` — same key, same face, every time.

    ``race`` decides only whether hair/complexion are included; the structural
    traits apply to every species.
    """
    r = _rng(key)
    parts: List[str] = [
        r.choice(_AGE), r.choice(_BUILD), r.choice(_FACE),
        r.choice(_NOSE), r.choice(_EYES),
    ]
    if _is_human_tier(race):
        parts.append(f"{r.choice(_HAIR_COLOUR)} hair {r.choice(_HAIR_STYLE)}")
        parts.append(f"{r.choice(_COMPLEXION)} complexion")
    mark = r.choice(_MARK)
    if mark:
        parts.append(mark)
    return ", ".join(parts)


def _is_human_tier(race: str) -> bool:
    try:
        from .species_portraits import species_tier
        return species_tier(_slug(race)) == "human"
    except Exception:
        return _slug(race) in {"human", "variant-human", "custom-lineage",
                               "half-elf", "khoravar"}


def _slug(race: str) -> str:
    return (race or "").strip().lower().replace(" ", "-")


#: Weight on the appearance clause. Measured on three deliberately
#: contradictory bone structures (jowly / hawk-faced / round) at a fixed seed:
#:
#:   1.2   hair, colouring and age vary; bone structure still converges — all
#:         three came back the same idealised face
#:   1.35  structure SEPARATES: real jowls, a real hooked nose, a genuinely
#:         round young face
#:   1.5   slightly stronger, but the nose starts to deform outright
#:
#: Dropping "heroic"/"adventurer" from the PC framing was the obvious suspect
#: and did NOTHING — tested twice, at 1.2 and again at 1.35. The weight is the
#: whole lever, so the framing is left alone.
APPEARANCE_WEIGHT = 1.35

#: Weighting facial descriptors hard enough to shape bone structure has one
#: side effect: "bulbous"/"snub" noses come back RED and inflamed, with
#: flushed blotchy cheeks — a drinker's face. The cure is to veto the symptom
#: rather than back off the weight, which would take the structure with it.
#: Append to the negative on any render that carries a weighted face.
FACE_NEGATIVE = ("red nose, inflamed nose, rosacea, clown nose, blotchy skin, "
                 "flushed cheeks, sunburn, broken capillaries")


def appearance_clause(key: str, race: str = "",
                      described: Optional[str] = None) -> str:
    """The appearance fragment for a portrait prompt, CLIP-weighted.

    A player's OWN description always wins — the roll exists for the characters
    that have none, not to argue with someone who wrote one. Either way it is
    weighted: unweighted, an explicit "hooked broken nose, receding sandy hair"
    was measurably dropped in favour of the model's preferred handsome hero.

    Anything rendering this MUST also append `FACE_NEGATIVE`, or the weight
    that buys the bone structure also buys a red nose.
    """
    text = (described or "").strip() or roll_appearance(key, race)
    return f"({text}:{APPEARANCE_WEIGHT})" if text else ""
