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


def roll_beauty(key: str) -> str:
    """The comeliness band a character gets when nobody chose one.

    Drawn from the same stable key as the face, and gated on the age the face
    roll produced: "weathered" means sun-damage and deep creases, which is
    nonsense on someone barely out of their teens.
    """
    r = _rng(key)
    age = r.choice(_AGE)
    older = any(w in age for w in ("forties", "past fifty", "old"))
    pool = _BEAUTY_ROLL if older else [b for b in _BEAUTY_ROLL if b != "weathered"]
    # Draw from a SECOND stream so the band doesn't shift when the trait pools
    # are edited — a character's face should survive a content tweak.
    return _rng(key + "|beauty").choice(pool)


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
#:
#: It is ALSO an idealising force — it vetoes blotchy skin and broken
#: capillaries, which are exactly the markers that make a face plain. So it is
#: applied only to the flattering bands; see ``BEAUTY_BANDS``.
FACE_NEGATIVE = ("red nose, inflamed nose, rosacea, clown nose, blotchy skin, "
                 "flushed cheeks, sunburn, broken capillaries")

#: Negating the beauty vocabulary. On its own this does almost nothing — see
#: the note on BEAUTY_BANDS — but paired with concrete features it roughly
#: doubles the effect, so both halves ship together.
_NOT_PRETTY = ("beautiful, handsome, pretty, glamorous, model good looks, "
               "idealized, flawless skin, perfect symmetry, chiselled, "
               "heroic beauty, attractive")

#: How good-looking a character is, as a PLAYER-FACING CHOICE rather than a
#: house default. Ordered flattering -> not.
#:
#: **Abstract comeliness words are no-ops.** "a PLAIN ORDINARY face,
#: unremarkable and forgettable" — even with every beauty token negated —
#: measured as the same handsome man as "strikingly beautiful". The model has
#: no visual referent for a value judgement. Only CONCRETE FEATURES move it: a
#: thick shapeless nose, a receding chin, protruding ears, thinning hair. Every
#: band below is therefore written as anatomy, never as an opinion, which is
#: the same lesson the goliath taught about naming the colour instead of
#: gesturing at it.
#:
#: Each band is (positive features, extra negative, apply FACE_NEGATIVE).
BEAUTY_BANDS: dict[str, tuple[str, str, bool]] = {
    "striking": ("fine even features, clear smooth skin, a clean strong "
                 "jawline, bright clear eyes", "", True),
    "comely": ("agreeable regular features, clear skin", "", True),
    "plain": ("a thick shapeless nose, small dull close-set eyes, a weak "
              "receding chin, thin lips, a low forehead, dull uneven skin, "
              "patchy stubble", _NOT_PRETTY, False),
    "homely": ("a large bulbous pitted nose, heavy jowls, protruding ears, "
               "crooked teeth, a heavy undershot jaw, coarse blotchy skin, "
               "sparse thinning hair, puffy eyelids", _NOT_PRETTY, False),
    "weathered": ("leathery sun-damaged skin, deep creases, a coarse "
                  "broken-veined nose, thinning hair, a hard set mouth",
                  _NOT_PRETTY, False),
}

#: What a rolled character gets. Deliberately NOT uniform and deliberately not
#: centred on flattering: a table where everyone is striking reads as a
#: catalogue. "weathered" is gated on age below — it is nonsense on a
#: nineteen-year-old.
_BEAUTY_ROLL = (["striking"] + ["comely"] * 3 + ["plain"] * 4
                + ["homely"] * 2 + ["weathered"] * 2)


def appearance_prompt(key: str, race: str = "", described: Optional[str] = None,
                      beauty: Optional[str] = None) -> tuple[str, str]:
    """``(positive_clause, negative_extra)`` for one character's face.

    Both halves must travel together: the comeliness band picks its own
    negative, and a caller that renders the clause while appending a fixed
    negative would veto the very features that make a plain face plain.

    A player's OWN description always wins over the rolled face — the roll
    exists for characters that have none, not to argue with someone who wrote
    one. ``beauty`` is likewise a CHOICE and is honoured whether or not they
    described anything; it is only rolled for a character who specified
    neither, so the world gets a spread instead of a cast of models.
    """
    described = (described or "").strip()
    text = described or roll_appearance(key, race)

    band = (beauty or "").strip().lower()
    if band not in BEAUTY_BANDS:
        # Someone who wrote their own face has already said how comely they
        # are, in their own words. Only roll one when nothing was said at all.
        band = "" if described else roll_beauty(key)

    negatives: List[str] = []
    if band:
        features, band_neg, use_face_neg = BEAUTY_BANDS[band]
        text = f"{text}, {features}"
        if band_neg:
            negatives.append(band_neg)
        if use_face_neg:
            negatives.append(FACE_NEGATIVE)
    else:
        negatives.append(FACE_NEGATIVE)

    clause = f"({text}:{APPEARANCE_WEIGHT})" if text else ""
    return clause, ", ".join(negatives)


def appearance_clause(key: str, race: str = "",
                      described: Optional[str] = None,
                      beauty: Optional[str] = None) -> str:
    """Just the positive half. Prefer `appearance_prompt` — see its note on why
    the negative has to travel with it."""
    return appearance_prompt(key, race, described, beauty)[0]
