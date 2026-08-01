"""
What a character brings to drawing a map — computed, not narrated.

The cartography check used to be the LLM's arithmetic: the DM prompt asked it
to emit ``[[ROLL: 1d20+<Wis mod, +PB if proficient>]]`` and fill in the number
itself. That is exactly backwards for this codebase ("the LLM decides FICTION,
the code decides MECHANICS"), and it has a concrete cost: a feature only counts
when the model happens to remember it. A dragonmark, a tool expertise, a spell
burned to steady the hand — all invisible.

This module answers "how good is this character at this, and why" from the
sheet, as plain data. It takes primitives (a modifier, a proficiency bonus, the
character's tags, the text of their features) rather than a Character, so it
stays testable and the world package doesn't grow a dependency on the backend.

DISCOVERY IS DELIBERATELY OPEN. Beyond the named table below, ANY feature whose
text talks about cartographer's tools or map-making grants its bearer a bonus
die — so a subclass dropped into the owned-book overrides later starts
complementing this system without anyone editing code here. That is the
intended extension point, not an accident.

Where a book's own wording names a NEIGHBOURING tool rather than this one, the
entry is marked ``house_rule=True`` and says so in its reason. Those are rulings
this table is making, they are visible to the table when the check is reported,
and they are meant to be edited by whoever runs the game.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

#: The tool the work is done with, and the skills that mean you know the land
#: well enough to draw it. Survival/Nature proficiency grants ADVANTAGE, which
#: is the SRD's own suggestion for a tool check backed by a relevant skill.
CARTOGRAPHY_TOOL = "cartographer's tools"
SURVEY_SKILLS = ("survival", "nature")

#: Base DC for a serviceable map of country you have actually walked.
BASE_DC = 15
#: A wider sheet is a harder sheet: compiling provinces from memory is not the
#: same job as sketching the valley you are standing in.
DC_BY_SCALE = {"local": 15, "regional": 17, "provincial": 19, "world": 21}


@dataclass
class Boon:
    """One thing a character brings to the check, and why it counts."""
    name: str
    reason: str
    bonus_die: str = ""          # e.g. "1d4"
    flat: int = 0
    advantage: bool = False
    house_rule: bool = False

    def describe(self) -> str:
        bits = []
        if self.bonus_die:
            bits.append(f"+{self.bonus_die}")
        if self.flat:
            bits.append(f"{self.flat:+d}")
        if self.advantage:
            bits.append("advantage")
        effect = ", ".join(bits) or "—"
        tail = " [house ruling]" if self.house_rule else ""
        return f"{self.name} ({effect}): {self.reason}{tail}"


#: Feats whose own text names an intuition adjacent to this work. Dragonmarks
#: are FEATS in this build, not subraces, so they are matched by feat tag.
#:
#: Each of these is a RULING. The books attach these dice to Perception and
#: Survival, to Calligrapher's Supplies, to Navigator's Tools — near neighbours
#: of drawing a map, none of them literally this check. They are included
#: because a table that owns these books plainly expects them to matter here,
#: and they are marked so nobody mistakes them for RAW.
_FEAT_BOONS = {
    "mark-of-finding": Boon(
        "Mark of Finding", "Hunter's Intuition reads the land you're surveying",
        bonus_die="1d4", house_rule=True),
    "mark-of-scribing": Boon(
        "Mark of Scribing", "Gifted Scribe steadies the fair copy",
        bonus_die="1d4", house_rule=True),
    "mark-of-storm": Boon(
        "Mark of Storm", "Windwright's Intuition covers charting a passage",
        bonus_die="1d4", house_rule=True),
    "mark-of-passage": Boon(
        "Mark of Passage", "Intuitive Motion keeps your pacing honest",
        bonus_die="1d4", house_rule=True),
    "mark-of-detection": Boon(
        "Mark of Detection", "Deductive Intuition catches what doesn't line up",
        bonus_die="1d4", house_rule=True),
    # Not a dragonmark, and not a ruling: perfect recall of where you have been
    # is the whole job.
    "keen-mind": Boon(
        "Keen Mind", "you recall every direction you have travelled",
        bonus_die="1d4"),
}

#: Text that marks a feature as being ABOUT this work, whatever grants it.
#: This is how a subclass added to the owned-book overrides later starts
#: counting here with no change to this file — the Artificer's Cartographer
#: subclass lands through this door rather than through a name check.
_CARTOGRAPHY_TEXT = re.compile(
    r"cartograph|map-?mak|mapmak|chart(?:ing|s)?\b|survey(?:ing|or)?\b", re.I)

#: A feature that GRANTS the tool proficiency outright. Read from the text
#: rather than trusted to the character's tag list, because a subclass-granted
#: proficiency is only tagged if whatever imported the sheet thought to do it —
#: and the Cartographer artificer's whole first feature is this grant.
#: Not a ruling: proficiency is proficiency, wherever it came from.
_GRANTS_TOOL = re.compile(
    r"proficien\w*\s+with\b[^.]{0,120}?cartographer.{0,3}s\s+tools", re.I)

#: A feature that puts Find the Path in reach without a slot (the Cartographer's
#: level-15 Unerring Path). It is never automatic — it's once per long rest, so
#: the DM declares it when the player spends it. This only marks it AVAILABLE.
_OFFERS_FIND_PATH = re.compile(r"find\s+the\s+path", re.I)

#: Situational help the DM declares because they narrated it. Kept to an
#: allowlist so the hook can't be talked into arbitrary bonuses, and separate
#: from sheet discovery because these are spent resources, not standing traits.
DECLARABLE = {
    "guidance": Boon("Guidance", "a cantrip steadies the hand", bonus_die="1d4"),
    "bardic-inspiration": Boon("Bardic Inspiration", "someone believes in you",
                               bonus_die="1d6"),
    "enhance-ability": Boon("Enhance Ability", "Owl's Wisdom on the survey",
                            advantage=True),
    "owls-wisdom": Boon("Enhance Ability", "Owl's Wisdom on the survey",
                        advantage=True),
    "inspiration": Boon("Heroic Inspiration", "you spend your inspiration",
                        advantage=True),
}

#: Declared conditions that change WHAT can be surveyed rather than how well.
#: A high vantage is the oldest surveying trick there is; scrying magic is the
#: same trick by other means.
VANTAGE = {
    # name -> how much further the sheet can reach than the drafter's feet
    "fly": 2.0, "flight": 2.0, "high vantage": 1.5, "vantage": 1.5,
    "levitate": 1.4, "clairvoyance": 2.0, "arcane eye": 2.5, "scrying": 2.5,
}

#: Declared effects that make the sheet TRUE regardless of the roll. Find the
#: Path doesn't help you draw — it means you cannot be wrong about the way.
INFALLIBLE = {"find the path", "find-the-path"}


@dataclass
class CheckSpec:
    """The assembled check: what to roll, against what, and why."""
    modifier: int = 0
    dc: int = BASE_DC
    advantage: bool = False
    bonus_dice: list[str] = field(default_factory=list)
    boons: list[Boon] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    proficient: bool = False
    reach_multiplier: float = 1.0
    infallible: bool = False
    #: Boons this character COULD spend but hasn't declared — surfaced to the
    #: DM so they can offer them rather than the player having to know.
    available: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """One line for the table: the sum, and what went into it."""
        dice = "".join(f"+{d}" for d in self.bonus_dice)
        adv = " with advantage" if self.advantage else ""
        return (f"d20{self.modifier:+d}{dice} vs DC {self.dc}{adv} — "
                + ("; ".join(self.reasons) if self.reasons else "unaided"))


def _tag_values(tags: Iterable, prefix: str) -> set[str]:
    out = set()
    for t in tags or []:
        if isinstance(t, str) and t.lower().startswith(f"{prefix}:"):
            out.add(t.split(":", 1)[1].strip().lower())
    return out


def slugify_feat(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


def check_spec(
    *,
    wis_mod: int,
    proficiency_bonus: int,
    tags: Optional[Iterable] = None,
    feature_texts: Optional[Iterable[str]] = None,
    declared: Optional[Iterable[str]] = None,
    scale: str = "local",
) -> CheckSpec:
    """Assemble the cartography check for one character, from their sheet.

    ``tags`` are the Character's tag list (``skill:``/``tool:``/``feat:``/
    ``expertise:`` entries). ``feature_texts`` is free text from anything they
    have — subclass features, feat benefits — scanned for this work being named
    outright. ``declared`` is what the DM says is in play right now.
    """
    tags = list(tags or [])
    spec = CheckSpec(modifier=int(wis_mod),
                     dc=DC_BY_SCALE.get(str(scale).lower(), BASE_DC))
    if wis_mod:
        spec.reasons.append(f"Wis {wis_mod:+d}")

    tools = _tag_values(tags, "tool")
    expertise = _tag_values(tags, "expertise")
    skills = _tag_values(tags, "skill")
    feats = _tag_values(tags, "feat") | {slugify_feat(f) for f in _tag_values(tags, "feat")}

    # Read the character's own features BEFORE scoring: one of them may be the
    # thing that grants the tool proficiency in the first place.
    texts = [str(t) for t in (feature_texts or []) if t]
    trained_by_feature = any(_GRANTS_TOOL.search(t) for t in texts)
    if trained_by_feature:
        tools = tools | {CARTOGRAPHY_TOOL}

    if CARTOGRAPHY_TOOL in tools:
        spec.proficient = True
        if CARTOGRAPHY_TOOL in expertise:
            spec.modifier += 2 * proficiency_bonus
            spec.reasons.append(f"tool expertise +{2 * proficiency_bonus}")
        else:
            spec.modifier += proficiency_bonus
            spec.reasons.append(f"tool proficiency +{proficiency_bonus}")
    else:
        # Not a hard gate here — the CALLER decides whether an untrained hand
        # may draft at all (the MAP hook requires the tools in the pack). This
        # only says the check gets no proficiency.
        spec.reasons.append("untrained with the tools")

    backing = sorted(s for s in SURVEY_SKILLS if s in skills)
    if backing:
        spec.advantage = True
        spec.boons.append(Boon(backing[0].title(), "you know this country",
                               advantage=True))
        spec.reasons.append(f"{backing[0].title()} backs the work (advantage)")

    for slug, boon in _FEAT_BOONS.items():
        if slug in feats:
            spec.boons.append(boon)
            if boon.bonus_die:
                spec.bonus_dice.append(boon.bonus_die)
            spec.modifier += boon.flat
            if boon.advantage:
                spec.advantage = True
            spec.reasons.append(boon.describe())

    # The open door: anything the character HAS that talks about this work.
    # One die however many features qualify — this is a bonus for being a
    # cartographer, not a stacking loophole.
    if any(_CARTOGRAPHY_TEXT.search(t) for t in texts):
        boon = Boon("Cartographer's training",
                    "a feature of yours is about exactly this work",
                    bonus_die="1d4", house_rule=True)
        spec.boons.append(boon)
        spec.bonus_dice.append(boon.bonus_die)
        spec.reasons.append(boon.describe())

    # Find the Path reachable without a slot (the Cartographer artificer's
    # level-15 Unerring Path). Offered, never assumed: it is once per long rest
    # and spending it is the player's call.
    if any(_OFFERS_FIND_PATH.search(t) for t in texts):
        spec.available.append("find the path")

    for raw in (declared or []):
        key = str(raw).strip().lower()
        if key in INFALLIBLE:
            spec.infallible = True
            spec.reasons.append("Find the Path — the way cannot be mistaken")
            continue
        if key in VANTAGE:
            spec.reach_multiplier = max(spec.reach_multiplier, VANTAGE[key])
            spec.reasons.append(f"{key} widens the survey "
                                f"(x{VANTAGE[key]:g} reach)")
            continue
        boon = DECLARABLE.get(key) or DECLARABLE.get(key.replace(" ", "-"))
        if boon is None:
            continue
        spec.boons.append(boon)
        if boon.bonus_die:
            spec.bonus_dice.append(boon.bonus_die)
        spec.modifier += boon.flat
        if boon.advantage:
            spec.advantage = True
        spec.reasons.append(boon.describe())

    return spec


def standing(spec: CheckSpec) -> str:
    """A short phrase for the DM prompt: how this character rates at the work."""
    if spec.infallible:
        return "cannot be wrong about the way"
    if not spec.proficient:
        return "untrained — the sheet will be rough"
    if spec.advantage or spec.bonus_dice:
        return "a practised hand with something extra going for them"
    return "a practised hand"
