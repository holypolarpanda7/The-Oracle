"""Per-biome motif tables for frontier stubs.

A "motif" is a small, evocative, POI-scale seed a traveler could stumble on —
not a fully detailed location, just a hook the DM can improvise from if play
ever wanders that way. These are self-authored, generic fantasy-frontier
dressing (no copied setting text), grouped by biome so the world-graph seeder
and extractor can roll a handful for an unexplored stub without inventing a
fully-formed settlement out of nothing.

Usage::

    from dm_guide.motifs import roll_motifs
    roll_motifs("forest", 3)
    roll_motifs("forest", 3, rng=random.Random(42))  # deterministic
"""
from __future__ import annotations

import random
from typing import Iterable, Optional

# Biome -> list of short motif phrases (~10-14 each). Mundane, eerie, social,
# and ancient in roughly even measure so a random pull feels varied.
MOTIF_TABLES: dict[str, list[str]] = {
    "farmland": [
        "a scarecrow dressed in someone's old militia coat",
        "a toll bridge nobody collects on anymore",
        "a burned-out granary with the smell of smoke still in it",
        "a well with a coin-crusted bottom, tossed for luck",
        "a farmer plowing a field that turns up old bones",
        "a shrine to the harvest, freshly tended by unseen hands",
        "a fence line of gourds carved into leering faces",
        "an itinerant tinker's cart, wheel broken, owner nowhere in sight",
        "a fallow field that livestock refuse to graze",
        "a crossroads gallows, empty, kept in good repair",
        "a child's kite tangled in a lone dead tree",
        "a peddler selling suspiciously good weather charms",
    ],
    "forest": [
        "a ring of mushrooms that wasn't there yesterday",
        "charcoal burners' camp, embers still warm",
        "a hunter's blind lashed high in the branches",
        "a stag with an old arrow still lodged in its flank",
        "a shrine grown over with moss, offerings long since rotted",
        "a trapper's line of snares, one still twitching",
        "trees blazed with a marking no local claims to know",
        "a hollow trunk someone has been using as a mailbox",
        "birdsong that stops all at once, then doesn't resume",
        "a woodcutter's abandoned cabin, table still set for two",
        "a deer trail worn unnaturally straight, like a road",
        "a hanging lantern, lit, with no one tending it",
    ],
    "hills": [
        "a cairn stacked higher than any one traveler could manage alone",
        "a shepherd's cairn marking a lightning-struck ewe",
        "sheep grazing a slope too steep to be natural",
        "a toppled statue of a forgotten god, half-swallowed by turf",
        "an old boundary stone in a language no one reads anymore",
        "a windmill with sails that turn against the wind",
        "a switchback trail with a shrine at every third bend",
        "a shepherd who hasn't aged, by the locals' account, in decades",
        "a cave mouth just wide enough for one, breathing faint mist",
        "terraced ruins of a vineyard no one plants anymore",
        "a ruined watchtower, its bell still rung by the wind",
        "a flock of sheep marked with a brand nobody in the region uses",
    ],
    "river": [
        "a ferryman who only takes payment in stories",
        "a drowned bell tower, chiming faintly at low water",
        "fishing weirs strung with charms against something unnamed",
        "a millwheel turning with no one inside to grind",
        "a washerwoman's stone worn smooth by generations",
        "driftwood piled into a crude, deliberate-looking effigy",
        "a barge run aground, cargo intact, crew missing",
        "an eel-trap larger than any eel could fill",
        "a shrine to the river built on a sandbar, half-submerged",
        "children swimming near a bend the elders call unlucky",
        "an old ford marked by a rope none dare cut down",
        "a heron that watches travelers a beat too long",
    ],
    "swamp": [
        "a will-o'-wisp trail leading conveniently toward solid ground",
        "a hermit's stilt-house, moss-grown, chimney still smoking",
        "a sunken statue visible only when the water's low",
        "a trapper's canoe tied up with no trapper in sight",
        "frogs that go silent in a perfect, spreading ring",
        "a corduroy road half-swallowed by the mire",
        "a witch-light lantern hung as a warning, or a lure",
        "bog-iron diggings, recently worked, tools left behind",
        "a shrine wrapped in strung bones and river-glass",
        "a bloated toll-keeper's shack, ledger still on the desk",
        "reeds bent in a perfect circle, as if something landed",
        "a peat-cutter's trench that goes deeper than peat should",
    ],
    "mountains": [
        "a switchback shrine to a mountain god, cairns for offerings",
        "an abandoned mine adit, timbers still sound",
        "a rope bridge that sways with no wind blowing",
        "a hermit hermitage carved into the cliff face",
        "goats grazing a ledge no goat should be able to reach",
        "a cache of climbing gear, decades old, still usable",
        "a watch-beacon unlit for longer than anyone remembers",
        "an avalanche scar that exposed old, worked stone",
        "a frozen waterfall with something dark suspended inside",
        "a prospector's claim marker driven into bare rock",
        "an eagle's nest built around a rusted helm",
        "a pass-shrine where travelers leave a boot for luck",
    ],
    "desert": [
        "a caravan skeleton, wagons intact, sand-scoured to bone",
        "an oasis palm grove ringed with old campfire scars",
        "a buried obelisk, one corner still catching the sun",
        "a well-diviner who hasn't been wrong yet",
        "dunes that sing faintly when the wind is right",
        "a salt-caravan's abandoned load, crystallized and glittering",
        "a sun-bleached shrine tended by a single silent monk",
        "tracks that circle a dune and simply stop",
        "a mirage that repeats at the same hour every day",
        "nomad way-cairns spaced a day's walk apart",
        "a sand-scoured statue buried to the waist",
        "vultures circling a spot with nothing visibly dead below",
    ],
    "coast": [
        "a shipwreck's ribs exposed at low tide",
        "a lighthouse keeper who claims never to sleep",
        "a smuggler's cave sealed with an unconvincing rockfall",
        "tide pools that glow faintly after dark",
        "a fisherman's shrine strung with net-floats and bones",
        "gulls that won't land on one particular stretch of sand",
        "a bell buoy tolling with no swell to move it",
        "a beached whale being methodically, quietly harvested",
        "driftwood carved into tally marks, hundreds of them",
        "a rope ladder down a sea-cliff to nowhere visible",
        "a tide-locked causeway to an islet no map names",
        "a net hauled up heavier than any catch should be",
    ],
}

# Fallback table for biomes not in MOTIF_TABLES.
_GENERIC_MOTIFS: list[str] = [
    "a weathered waystone with a name worn illegible",
    "a lone traveler's camp, fire still smoldering, packs still there",
    "a shrine to a god no one nearby can name",
    "a stretch of road the local guides go quiet about",
    "a peddler's abandoned cart, goods untouched",
    "birds circling a fixed point for no obvious reason",
    "a marker cairn rebuilt every year by unknown hands",
    "an old milestone giving a distance to somewhere unmapped",
    "a ring of stones just large enough to sit a party of six",
    "footprints that end mid-stride",
    "a lantern left burning in broad daylight",
    "a grave marked only with a date, no name",
]


def roll_motifs(
    biome: str,
    n: int = 3,
    *,
    exclude: Iterable[str] = (),
    rng: Optional[random.Random] = None,
) -> list[str]:
    """Pick ``n`` distinct motifs for a biome, skipping anything in ``exclude``.

    Falls back to a generic table when the biome isn't recognized. Deterministic
    when given a seeded ``rng``; otherwise uses the module-level RNG.
    """
    r = rng or random
    key = (biome or "").strip().lower()
    table = MOTIF_TABLES.get(key, _GENERIC_MOTIFS)
    excluded = {m.strip().lower() for m in exclude}
    pool = [m for m in table if m.strip().lower() not in excluded]
    if not pool:
        pool = list(table)
    count = max(0, min(n, len(pool)))
    return r.sample(pool, count)
