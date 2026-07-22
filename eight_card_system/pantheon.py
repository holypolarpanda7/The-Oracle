"""
The world's pantheon — ORIGINAL setting canon (not Faerûn).

A CLOSED set of powers, seeded up front so the narration->extraction loop
reuses gods instead of endlessly inventing new ones. Two families:

  * The Sovereign Powers — the younger gods most mortals worship, one for each
    domain a session tends to need (life, death, war, magic, sea, nature,
    trade, night, craft, law, art, and the dark tyrant).
  * The Ymmarch — the elder GIANT powers, children of the World-Anvil, ranked
    under their cosmic hierarchy "the Great Measure" (this world's ordning).
    Original analogs of the giant ordning archetype; the giant enclaves and
    giant-kind monsters swear by these.

Everything here is our own invention and is safe to commit. To reshape the
canon, edit ``_SOVEREIGN`` / ``_YMMARCH`` below — the seeder is data-driven and
idempotent (deities upsert by slug).

``PANTHEON_CAP`` is the intended ceiling for the (future) pantheon world-law:
routine play may not push the deity count past it; only a deliberate, DM-gated
divine event (schism/apotheosis) can raise it.
"""
from __future__ import annotations

from typing import Optional

from .graph import WorldGraph
from .models import EntityType, RelationType

PANTHEON_CAP = 30  # ~22 seeded + headroom for regional cults / a schism

# --- The Sovereign Powers (younger gods; mortal pantheon) ------------------
# Each: (name, title, alignment, domains, symbol, blurb)
_SOVEREIGN = [
    ("Serath", "the Dawnmother", "neutral good",
     "sun, life, harvest, healing, hope",
     "a golden sheaf crowned by a rising sun",
     "The kindly mother of dawn and the fields; the most widely kept faith among "
     "farming folk. She wakes the sun and blesses the harvest."),
    ("Nyssa", "the Pale Warden", "lawful neutral",
     "death, rest, fate, the final gate",
     "a silver key on a closed eye",
     "The stern, impartial keeper of the dead — not cruel, but unbribable. She "
     "shepherds souls through the last gate and abhors the undead who cheat it."),
    ("Kael", "the Iron Oath", "lawful good",
     "war, honor, courage, protection, soldiers",
     "an unbroken shield over crossed spears",
     "The soldier's god of the honorable blade and the oath kept under fire. "
     "Guardians, knights, and defenders of the small call his name."),
    ("Ilvaris", "the Weaver", "neutral",
     "magic, knowledge, the arcane, secrets kept",
     "an eight-pointed star caught in silver thread",
     "The keeper of the arcane weave from which all spellcraft is drawn. To "
     "wound the Weave is the one heresy every wizard fears."),
    ("Sydrelle", "of the Deep Tides", "chaotic neutral",
     "sea, storms at sea, sailors, the depths",
     "a spiral shell wreathed in foam",
     "The fickle mistress of the sea — generous and drowning by turns. Sailors "
     "pour the first cup overboard for her before any voyage."),
    ("Cernow", "the Green Father", "neutral",
     "wild nature, beasts, the untamed, druids",
     "a stag's skull antlered with living branches",
     "The old god of the deep wood and the beasts within it, indifferent to "
     "cities. Druids and rangers keep his rites; farmers leave him the field's edge."),
    ("Halene", "the Coinwright", "neutral good",
     "trade, roads, travelers, honest wealth, fortune",
     "two clasped hands over a gold coin",
     "Patron of merchants, wayfarers, and fair bargains; luck follows those who "
     "deal honestly in her sight and abandons the cheat."),
    ("Vesh", "the Veil", "chaotic neutral",
     "night, the moon, dreams, secrets, thieves, the merciful dark",
     "a crescent moon veiling a single eye",
     "The soft-footed power of moonlight and shadow — refuge to dreamers, "
     "outcasts, and thieves alike. What is hidden under her veil is hers to keep."),
    ("Duran", "the Hammerbound", "lawful good",
     "craft, the forge, stone, artisans, makers",
     "an anvil struck by a single spark",
     "The maker-god of the forge and the well-set stone, worshipped by smiths, "
     "masons, and every guild that takes pride in good work."),
    ("Auren", "the Judge", "lawful neutral",
     "law, justice, oaths, civilization, judgment",
     "a set of balanced scales bound in iron",
     "The cold arbiter of law and sworn word who holds cities to their charters. "
     "Magistrates and lawgivers invoke him; oathbreakers dread his ledger."),
    ("Maowen", "the Brightsong", "chaotic good",
     "art, music, love, joy, revelry, bards",
     "a lyre strung with a rainbow",
     "The laughing muse of songs, lovers, and festivals; where her name is sung, "
     "grief is briefly forgotten. Bards are her wandering clergy."),
    ("Sith'ra", "the Whisper", "lawful evil",
     "tyranny, deceit, ambition, murder, domination",
     "a black crown split by a dagger",
     "The tyrant-god of ambition without conscience — the hand behind poisoned "
     "cups and stolen thrones. Her cults hide inside every court she means to own."),
]

# --- The Ymmarch (elder giant powers) --------------------------------------
# rank keys the Great Measure (giant ordning): 0 = the World-Anvil above all.
# (name, title, alignment, domains, symbol, rank, blurb)
_YMMARCH = [
    ("Vaskrun", "the World-Anvil", "neutral",
     "creation, giantkind, cosmic order, the Great Measure",
     "a mountain struck flat upon a colossal anvil",
     0,
     "The All-Father of giants, who hammered the raw world into shape and then "
     "sank into a dreaming sleep. From his sons and daughters descends the Great "
     "Measure — the sacred order by which every giant knows its worth."),
    ("Skarnhault", "the Storm-Crowned", "chaotic good",
     "sky, sea-storm, prophecy, giant kingship",
     "a thundercloud crowned with lightning",
     1,
     "Eldest of the World-Anvil's children and highest in the Great Measure; king "
     "of the storm giants, seer of futures written in the clouds."),
    ("Orethun", "the Forgefather", "lawful neutral",
     "fire, the forge, war-craft, mastery, ambition",
     "a hammer wreathed in white flame",
     2,
     "Lord of the fire giants and the deep forges, who prizes skill and discipline "
     "above all. What Orethun's folk make, they make to outlast empires."),
    ("Hrimvel", "the White Fury", "chaotic evil",
     "ice, conquest, raw strength, the hunt",
     "a broken spear rimed in frost",
     3,
     "The frost giants' merciless power of winter and conquest, who reckons worth "
     "by strength alone. Warbands raid in her name when the snows come down."),
    ("Kavdras", "the Stonewise", "lawful neutral",
     "earth, stone, deep secrets, art, memory",
     "a spiral carved into grey stone",
     4,
     "Patron of the stone giants: a dour keeper of buried knowledge, wards, and "
     "the slow art of the deep places. He speaks least among the Ymmarch and "
     "remembers most."),
    ("Maelivar", "the Gilded", "neutral evil",
     "wealth, fortune, pride, cunning, judgment of worth",
     "a coin-scale weighing a cloud",
     5,
     "The cloud giants' god of splendor and cunning, who measures all things by "
     "their price. Beloved and distrusted in equal part — a smiling schemer."),
    ("Ghorroth", "the Ever-Hungry", "chaotic evil",
     "hunger, appetite, brute survival, ruin",
     "a gaping maw ringed in tusks",
     6,
     "Lowest of the true powers in the Great Measure: the hill giants' idol of "
     "endless want, who takes by force and never has enough."),
    ("Yssame", "the Verdant", "neutral good",
     "nature, the hunt, fertility, the young, giant-kin and beasts",
     "an oak in a giant's cupped hands",
     3,
     "The green-handed mother of wild giant-kin and the beasts of the elder world, "
     "protector of the young and the growing. Firbolgs and gentle giants keep her rites."),
    ("Vhorrek", "the Broken Crown", "chaotic evil",
     "monsters, deformity, spite, the exiled and the twisted",
     "a shattered crown over a warped eye",
     -1,
     "The outcast of the Ymmarch, cast from the Great Measure for his cruelty. "
     "Fomorians, twisted giants, and every monstrous thing spat out of giant-kind "
     "call him father. He hates his kin above all."),
    ("Diell", "the Wayward", "chaotic neutral",
     "luck, mischief, wandering, defiance of the Measure",
     "a die mid-tumble, one face blank",
     -2,
     "A giant-born trickster who walks the mortal world at a mortal's size, mocking "
     "the rigid Great Measure. Half-demigod, half-legend — the patron of those who "
     "refuse the place they were born to."),
]


def _blurb_attrs(alignment: str, domains: str, symbol: str, blurb: str,
                 family: str, title: str, rank: Optional[int] = None) -> dict:
    attrs = {
        "description": blurb,
        "title": title,
        "domain": domains,
        "alignment": alignment,
        "symbol": symbol,
        "pantheon": family,          # "sovereign" | "ymmarch"
    }
    if rank is not None:
        attrs["great_measure_rank"] = rank   # giant ordning standing
    return attrs


def seed_pantheon(graph: WorldGraph) -> dict:
    """Seed the closed pantheon as DEITY entities plus a few defining relations.

    Idempotent (upsert by slug). Returns {"sovereign": [...], "ymmarch": [...],
    "total": n, "cap": PANTHEON_CAP}.
    """
    graph.create_tables()
    e = graph.upsert_entity
    by_slug: dict[str, object] = {}

    sovereign_slugs, ymmarch_slugs = [], []
    for name, title, align, domains, symbol, blurb in _SOVEREIGN:
        ent = e(name, EntityType.DEITY,
                attributes=_blurb_attrs(align, domains, symbol, blurb,
                                        "sovereign", title),
                tags=["deity", "sovereign-power"] + domains.split(", ")[:2])
        by_slug[ent.slug] = ent
        sovereign_slugs.append(ent.slug)

    for name, title, align, domains, symbol, rank, blurb in _YMMARCH:
        ent = e(name, EntityType.DEITY,
                attributes=_blurb_attrs(align, domains, symbol, blurb,
                                        "ymmarch", title, rank),
                tags=["deity", "giant-power", "ymmarch"] + domains.split(", ")[:1])
        by_slug[ent.slug] = ent
        ymmarch_slugs.append(ent.slug)

    # --- A few defining relations (only the ones that shape play) ---
    def rel(a: str, r: str, b: str) -> None:
        if a in by_slug and b in by_slug:
            graph.add_relation(by_slug[a], r, by_slug[b])

    # The exile and the trickster stand against the World-Anvil's order.
    rel("vhorrek", RelationType.HOSTILE_TO, "vaskrun")
    rel("diell", RelationType.HOSTILE_TO, "vaskrun")
    rel("vhorrek", RelationType.HOSTILE_TO, "yssame")   # monsters vs. the green mother
    # The tyrant preys on the lawful powers of mortals.
    rel("sith-ra", RelationType.HOSTILE_TO, "auren")
    rel("sith-ra", RelationType.HOSTILE_TO, "kael")
    # Natural alliances the DM can lean on.
    rel("serath", RelationType.ALLIED_WITH, "cernow")
    rel("kael", RelationType.ALLIED_WITH, "auren")
    rel("nyssa", RelationType.HOSTILE_TO, "vhorrek")    # death vs. the undead-maker's ilk

    return {"sovereign": sovereign_slugs, "ymmarch": ymmarch_slugs,
            "total": len(sovereign_slugs) + len(ymmarch_slugs),
            "cap": PANTHEON_CAP}
