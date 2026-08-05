"""The Proving Grounds' environment catalog.

Each environment is one thing a player can point at and say "fight me there":
a name, the domain it belongs to (land / sea / air), the tactical-board layout
it generates, and the medium creatures move through in it.

The catalog is deliberately small and hand-picked. It is not a list of every
board the game can make — it's the set of *distinct tactical problems* worth
rehearsing: open ground, a corridor, a room full of cover, water you can wade,
water you can only swim, air with footing, air with none.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: The three domains, in menu order.
DOMAINS: tuple[str, ...] = ("land", "sea", "air")


@dataclass(frozen=True)
class Environment:
    slug: str
    name: str
    domain: str            # land | sea | air
    archetype: str         # a vtt.mapgen generator name
    #: How creatures get around here — matches the generator's own mode.
    mode: str              # walk | swim | fly
    #: One line for the picker, and for the DM's framing of the fight.
    blurb: str
    #: Monsters must be able to move like this to belong here ("" = anything).
    requires_speed: str = ""
    lighting: Optional[str] = None
    #: Extra sentence handed to the DM when the board is unusual.
    dm_note: str = ""


_ENVIRONMENTS: tuple[Environment, ...] = (
    # ---------------------------------------------------------------- land
    Environment(
        "training-yard", "The Sand Ring", "land", "arena", "walk",
        "A bare sand pit under a hot white sky. No cover, no excuses.",
        lighting="bright",
        dm_note="Nothing to hide behind — this is a straight test of arms."),
    Environment(
        "old-forest", "Blackroot Wood", "land", "forest", "walk",
        "Close trunks and tangled undergrowth. Sight lines die at twenty feet.",
        lighting="dim"),
    Environment(
        "deep-cavern", "The Weeping Cavern", "land", "cave", "walk",
        "A wet natural cave — stalagmites, standing water, and the dark."),
    Environment(
        "fallen-ruin", "The Toppled Colonnade", "land", "ruins", "walk",
        "Broken masonry at every height. Cover for whoever thinks fastest."),
    Environment(
        "barrow-crypt", "The Ranked Dead", "land", "crypt", "walk",
        "Aisles of stone coffins. Corners, chokepoints, and no room to run."),
    Environment(
        "mountain-shelf", "The Screaming Shelf", "land", "mountain-pass", "walk",
        "A narrow track along a cliff. The drop is part of the fight.",
        dm_note="Ledges here are real: a shove near the edge is a long fall."),
    Environment(
        "undercity", "The Green Channel", "land", "sewer", "walk",
        "Vaulted brick, a slow channel of filth, ledges either side."),
    Environment(
        "open-field", "The Long Field", "land", "open", "walk",
        "Open country with scattered rock and scrub. Distance is the weapon."),

    # ----------------------------------------------------------------- sea
    Environment(
        "ship-deck", "The Rolling Deck", "sea", "ship", "walk",
        "A ship's deck at sea — rigging, lashed crates, water past the rail.",
        dm_note="Going over the rail means deep water and a swim check."),
    Environment(
        "coral-reef", "The Sunlit Shelf", "sea", "reef", "swim",
        "A coral shelf beneath the waves. Sand flats, deep channels, coral heads.",
        requires_speed="swim", lighting="dim",
        # The weapon rules used to be spelled out here for the DM to apply by
        # hand. The engine enforces them now, so repeating them would only
        # invite a second penalty on top of the one already rolled — the board
        # states what it is doing, and this says what the place is like.
        dm_note="Coral heads break up every line. Currents make the channels "
                "a poor place to stand still."),
    Environment(
        "open-water", "The Blue Deep", "sea", "open-water", "swim",
        "Open sea. Drifting wreckage, ribbons of kelp, black falling away below.",
        requires_speed="swim", lighting="dim",
        dm_note="This fight is underwater and there is no floor — position is "
                "three-dimensional and nothing here holds still."),
    Environment(
        "drowned-mire", "The Drowned Mire", "sea", "swamp", "walk",
        "Black bog water between reed hummocks and dead trees. Every step sucks.",
        lighting="dim"),

    # ----------------------------------------------------------------- air
    Environment(
        "sky-islands", "The Hanging Stones", "air", "sky-islands", "fly",
        "Islands of broken rock adrift in open sky, cloud far below.",
        requires_speed="fly", lighting="bright",
        dm_note="Everything that leaves stone is flying. A creature that loses "
                "its flight falls."),
    Environment(
        "skyship", "The Skyship Argent", "air", "skyship", "fly",
        "The deck of a flying ship under sail. Past the rail there is nothing.",
        requires_speed="fly", lighting="bright",
        dm_note="The deck is solid footing; everything off it is open air."),
    Environment(
        "storm-span", "The Rope Over Nothing", "air", "bridge", "walk",
        "A rope-and-plank span over a black chasm, in wind. Footing is a choice.",
        dm_note="A fall from the span is long and final."),
)

#: slug -> Environment.
ENVIRONMENTS: dict[str, Environment] = {e.slug: e for e in _ENVIRONMENTS}


def get_environment(slug: str) -> Optional[Environment]:
    return ENVIRONMENTS.get((slug or "").strip().lower())


def environments_by_domain() -> dict[str, list[Environment]]:
    """The catalog grouped for the picker, domains in menu order."""
    out: dict[str, list[Environment]] = {d: [] for d in DOMAINS}
    for env in _ENVIRONMENTS:
        out.setdefault(env.domain, []).append(env)
    return out


def environment_payload() -> list[dict]:
    """The catalog as plain data for the Activity's environment picker."""
    return [
        {"slug": e.slug, "name": e.name, "domain": e.domain, "mode": e.mode,
         "blurb": e.blurb, "archetype": e.archetype}
        for e in _ENVIRONMENTS
    ]


def sibling_environments(slug: str) -> list[Environment]:
    """Other environments in the same domain — "somewhere else like this"."""
    env = get_environment(slug)
    if env is None:
        return []
    return [e for e in _ENVIRONMENTS if e.domain == env.domain and e.slug != env.slug]
