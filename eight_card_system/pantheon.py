"""
The world's powers — ORIGINAL setting canon (not Faerûn).

Cosmic powers are grouped into **families**, each with its own LABEL, home
PLANE, member CLASS (what its members *are* — god, giant-god, celestial,
archfey, elder, archdevil, demon-lord), and its own independent CAP. Giant gods
are their own family, separate from the mortal gods; and "powers that aren't
gods" (archdevils of the Nine, demon princes of the Abyss, the archfey, the
sleeping Old Gods) are first-class families with their own labels and counters —
NOT lumped in with the pantheon of mortal deities.

All of this is our own invention and is safe to commit. Every family is a CLOSED
set seeded up front so the narration->extraction loop reuses these powers instead
of inventing new ones. To reshape the canon, edit ``POWER_FAMILIES`` (the family
definitions + caps) and ``_ROSTER`` (the members) below — the seeder is
data-driven and idempotent (each power upserts by slug).

All powers are stored as ``EntityType.DEITY`` (the graph's "Powers & faiths"
bucket), tagged with ``attributes.family`` and ``subtype = power_class`` so the
label/class is explicit and each family can be counted separately for the
(future) per-family world-law. ``worshipable`` distinguishes powers mortals
pray to from patrons they only bargain/pact with or cults that revere them.
"""
from __future__ import annotations

from typing import Optional

from .graph import WorldGraph
from .models import EntityType, RelationType

# --- Family definitions: label, plane, member class, per-family cap ---------
# ``cap`` is the ceiling the (coming) pantheon world-law enforces PER FAMILY:
# routine play may not push a family's count past its cap; only a DM-gated
# divine/planar event may raise it. ``worship`` describes how mortals relate to
# the family ("temples" | "cults" | "pacts" | "allies").
POWER_FAMILIES: dict[str, dict] = {
    "sovereign": {
        "label": "The Sovereign Powers", "plane": "the Material Realm & the Heavens",
        "power_class": "god", "cap": 14, "worship": "temples",
        "blurb": "The younger gods most mortals worship — one for each domain a life needs.",
    },
    "ymmarch": {
        "label": "The Ymmarch (giant gods)", "plane": "the primordial world",
        "power_class": "giant-god", "cap": 12, "worship": "temples",
        "blurb": "The elder giant powers, children of the World-Anvil, ranked under the Great Measure.",
    },
    "celestial": {
        "label": "The Celestial Choir", "plane": "the Upper Heavens",
        "power_class": "celestial", "cap": 8, "worship": "allies",
        "blurb": "Archangelic paragons who serve the Sovereign Powers' ideals and war on the fiends.",
    },
    "archfey": {
        "label": "The Twofold Court (archfey)", "plane": "the Feywild",
        "power_class": "archfey", "cap": 10, "worship": "pacts",
        "blurb": "The capricious rulers of the Seelie and Unseelie courts — patrons, not gods.",
    },
    "old_gods": {
        "label": "The Old Gods (the Elder Ones)", "plane": "before-time / the Outside",
        "power_class": "elder", "cap": 8, "worship": "cults",
        "blurb": "Primordial, mostly imprisoned or sleeping powers from before the current age.",
    },
    "archdevils": {
        "label": "The Lords of the Nine", "plane": "the Nine Hells",
        "power_class": "archdevil", "cap": 9, "worship": "pacts",
        "blurb": "The archdevils of the Nine Hells — tyrants of contract and hierarchy.",
    },
    "demon_lords": {
        "label": "The Demon Princes", "plane": "the Abyss",
        "power_class": "demon-lord", "cap": 12, "worship": "cults",
        "blurb": "The archdemons of the Abyss — lords of ruin, filth, and chaos.",
    },
}

# Total ceiling across all families (handy for the world-law + any UI).
PANTHEON_CAP = sum(f["cap"] for f in POWER_FAMILIES.values())

# --- Rosters: each member is a dict {name, title, alignment, domains, symbol,
# blurb, + optional family-specific keys (rank, layer)}. All original. ---------
_ROSTER: dict[str, list[dict]] = {
    "sovereign": [
        {"name": "Serath", "title": "the Dawnmother", "alignment": "neutral good",
         "domains": "sun, life, harvest, healing, hope", "symbol": "a golden sheaf crowned by a rising sun",
         "blurb": "The kindly mother of dawn and the fields; the common faith of farming folk."},
        {"name": "Nyssa", "title": "the Pale Warden", "alignment": "lawful neutral",
         "domains": "death, rest, fate, the final gate", "symbol": "a silver key on a closed eye",
         "blurb": "The stern, unbribable keeper of the dead who abhors the undead that cheat her gate."},
        {"name": "Kael", "title": "the Iron Oath", "alignment": "lawful good",
         "domains": "war, honor, courage, protection", "symbol": "an unbroken shield over crossed spears",
         "blurb": "The soldier's god of the honorable blade and the oath kept under fire."},
        {"name": "Ilvaris", "title": "the Weaver", "alignment": "neutral",
         "domains": "magic, knowledge, the arcane", "symbol": "an eight-pointed star in silver thread",
         "blurb": "Keeper of the arcane Weave from which all spellcraft is drawn."},
        {"name": "Sydrelle", "title": "of the Deep Tides", "alignment": "chaotic neutral",
         "domains": "sea, storms, sailors, the depths", "symbol": "a spiral shell wreathed in foam",
         "blurb": "The fickle mistress of the sea — generous and drowning by turns."},
        {"name": "Cernow", "title": "the Green Father", "alignment": "neutral",
         "domains": "wild nature, beasts, the untamed, druids", "symbol": "a stag's skull antlered with branches",
         "blurb": "The old god of the deep wood and its beasts, indifferent to cities."},
        {"name": "Halene", "title": "the Coinwright", "alignment": "neutral good",
         "domains": "trade, roads, travelers, fortune", "symbol": "two clasped hands over a gold coin",
         "blurb": "Patron of merchants and fair bargains; luck follows the honest dealer."},
        {"name": "Vesh", "title": "the Veil", "alignment": "chaotic neutral",
         "domains": "night, the moon, dreams, secrets, thieves", "symbol": "a crescent moon veiling an eye",
         "blurb": "The soft-footed power of moonlight and shadow — refuge to dreamers and outcasts."},
        {"name": "Duran", "title": "the Hammerbound", "alignment": "lawful good",
         "domains": "craft, the forge, stone, artisans", "symbol": "an anvil struck by a single spark",
         "blurb": "The maker-god of the forge and well-set stone, worshipped by every proud guild."},
        {"name": "Auren", "title": "the Judge", "alignment": "lawful neutral",
         "domains": "law, justice, oaths, civilization", "symbol": "balanced scales bound in iron",
         "blurb": "The cold arbiter of law and sworn word who holds cities to their charters."},
        {"name": "Maowen", "title": "the Brightsong", "alignment": "chaotic good",
         "domains": "art, music, love, revelry", "symbol": "a lyre strung with a rainbow",
         "blurb": "The laughing muse of songs, lovers, and festivals."},
        {"name": "Sith'ra", "title": "the Whisper", "alignment": "lawful evil",
         "domains": "tyranny, deceit, ambition, murder", "symbol": "a black crown split by a dagger",
         "blurb": "The tyrant-god of ambition without conscience; her cults hide inside every court."},
    ],
    "ymmarch": [
        {"name": "Vaskrun", "title": "the World-Anvil", "alignment": "neutral", "rank": 0,
         "domains": "creation, giantkind, cosmic order, the Great Measure", "symbol": "a mountain struck flat on an anvil",
         "blurb": "All-Father of giants, who hammered the world into shape and sank into dreaming sleep."},
        {"name": "Skarnhault", "title": "the Storm-Crowned", "alignment": "chaotic good", "rank": 1,
         "domains": "sky, sea-storm, prophecy, giant kingship", "symbol": "a thundercloud crowned with lightning",
         "blurb": "Eldest child of the World-Anvil; king of storm giants and seer of cloud-written futures."},
        {"name": "Orethun", "title": "the Forgefather", "alignment": "lawful neutral", "rank": 2,
         "domains": "fire, the forge, war-craft, mastery", "symbol": "a hammer wreathed in white flame",
         "blurb": "Lord of fire giants and the deep forges, who prizes skill and discipline above all."},
        {"name": "Yssame", "title": "the Verdant", "alignment": "neutral good", "rank": 3,
         "domains": "nature, the hunt, fertility, the young, giant-kin", "symbol": "an oak in a giant's cupped hands",
         "blurb": "Green-handed mother of wild giant-kin and elder beasts; kept by firbolgs and gentle giants."},
        {"name": "Hrimvel", "title": "the White Fury", "alignment": "chaotic evil", "rank": 3,
         "domains": "ice, conquest, raw strength, the hunt", "symbol": "a broken spear rimed in frost",
         "blurb": "The frost giants' merciless winter, who reckons all worth by strength alone."},
        {"name": "Kavdras", "title": "the Stonewise", "alignment": "lawful neutral", "rank": 4,
         "domains": "earth, stone, deep secrets, art, memory", "symbol": "a spiral carved in grey stone",
         "blurb": "Dour patron of stone giants; keeper of buried knowledge, wards, and the deep art."},
        {"name": "Maelivar", "title": "the Gilded", "alignment": "neutral evil", "rank": 5,
         "domains": "wealth, fortune, pride, cunning", "symbol": "a coin-scale weighing a cloud",
         "blurb": "The cloud giants' smiling schemer, who measures all things by their price."},
        {"name": "Ghorroth", "title": "the Ever-Hungry", "alignment": "chaotic evil", "rank": 6,
         "domains": "hunger, appetite, brute survival, ruin", "symbol": "a gaping maw ringed in tusks",
         "blurb": "The hill giants' idol of endless want, who takes by force and never has enough."},
        {"name": "Vhorrek", "title": "the Broken Crown", "alignment": "chaotic evil", "rank": -1,
         "domains": "monsters, deformity, spite, the exiled", "symbol": "a shattered crown over a warped eye",
         "blurb": "Outcast of the Ymmarch; father of fomorians and every twisted thing, and he hates his kin."},
        {"name": "Diell", "title": "the Wayward", "alignment": "chaotic neutral", "rank": -2,
         "domains": "luck, mischief, wandering, defiance", "symbol": "a die mid-tumble, one face blank",
         "blurb": "A giant-born trickster who walks at mortal size, mocking the rigid Great Measure."},
    ],
    "celestial": [
        {"name": "Auravel", "title": "the Radiant Herald", "alignment": "lawful good",
         "domains": "light, revelation, the dawn host", "symbol": "a trumpet of white fire",
         "blurb": "First of the Choir, who carries the Sovereign Powers' word to the mortal world."},
        {"name": "Solenne", "title": "the Mercy", "alignment": "neutral good",
         "domains": "healing, compassion, sanctuary", "symbol": "cupped hands holding a tear of light",
         "blurb": "The gentle balm of the Upper Heavens, guardian of the innocent and the wounded."},
        {"name": "Myrrath", "title": "the Sword of Judgment", "alignment": "lawful good",
         "domains": "righteous war, the smiting of fiends, valor", "symbol": "a burning sword on a sunburst",
         "blurb": "The Choir's blade, who leads the celestial host against the legions of the Hells and Abyss."},
        {"name": "Caelith", "title": "the Watcher at the Threshold", "alignment": "lawful neutral",
         "domains": "vigilance, wards, guardianship", "symbol": "an unsleeping eye over an archway",
         "blurb": "Sentinel of the planar gates, who bars the dark from crossing where it isn't invited."},
        {"name": "Veyanna", "title": "the Consoler", "alignment": "neutral good",
         "domains": "grief, guiding the lost, hope in death", "symbol": "a lantern held to an open door",
         "blurb": "Who walks beside the dying and lights the road to Nyssa's gate."},
        {"name": "Orimel", "title": "the Chronicler", "alignment": "lawful neutral",
         "domains": "memory, witnessed oaths, the record of deeds", "symbol": "a quill writing on a star",
         "blurb": "Keeper of the celestial record, before whom no oath is ever truly forgotten."},
    ],
    "archfey": [
        {"name": "Queen Verdaine", "title": "the Summer Crown", "alignment": "chaotic good", "court": "Seelie",
         "domains": "summer, growth, glamour, the radiant court", "symbol": "a rose in full bloom, gold-edged",
         "blurb": "Sovereign of the Seelie Court — generous, proud, and dangerous to slight."},
        {"name": "Ashelwin", "title": "the Lord of Blossoms", "alignment": "chaotic good", "court": "Seelie",
         "domains": "spring, first love, renewal, mischief", "symbol": "a branch of white blossom",
         "blurb": "The Seelie herald of spring; his bargains bloom sweet but bind fast."},
        {"name": "Bramblethorn", "title": "the Hollow King", "alignment": "neutral evil", "court": "Unseelie",
         "domains": "winter, thorns, cruel bargains, the frozen court", "symbol": "a crown of black thorns",
         "blurb": "Sovereign of the Unseelie Court, whose gifts always cost more than they seem."},
        {"name": "Lady Mothmourn", "title": "the Autumn Widow", "alignment": "neutral", "court": "Unseelie",
         "domains": "autumn, decay, forgotten things, melancholy", "symbol": "a grey moth on a wilted leaf",
         "blurb": "Keeper of endings and forgotten names; she trades in what others have let slip away."},
        {"name": "The Piper at the Hedge", "title": "the Between-Walker", "alignment": "chaotic neutral", "court": "Wild",
         "domains": "the paths between, music, lured travelers", "symbol": "a bone flute at a stile",
         "blurb": "The unaligned power of the fey roads, who leads the unwary off every known path."},
        {"name": "Karn", "title": "the Antlered Hunt", "alignment": "chaotic neutral", "court": "Wild",
         "domains": "the Wild Hunt, pursuit, primal terror", "symbol": "an antlered skull and a horn",
         "blurb": "Master of the Wild Hunt, whose horn on an autumn night means something is already prey."},
    ],
    "old_gods": [
        {"name": "Morloth", "title": "the Unmade", "alignment": "neutral evil",
         "domains": "entropy, the void before creation, unmaking", "symbol": "a torn hole in a black circle",
         "blurb": "The greatest of the imprisoned Elder Ones, who was old when the world was hammered new."},
        {"name": "Yshara", "title": "the Deep-Dreaming", "alignment": "chaotic evil",
         "domains": "the ocean deeps, madness, drowning dreams", "symbol": "a spiral of closed eyes",
         "blurb": "She sleeps in the black trenches and dreams; those who share her dreams do not return whole."},
        {"name": "Ghulra", "title": "the Hunger Below", "alignment": "neutral evil",
         "domains": "the devouring earth, famine, the pit", "symbol": "a downward maw of stone teeth",
         "blurb": "The starving dark beneath the roots of mountains, ever eating its way upward."},
        {"name": "Ssythra", "title": "the Coiled Dark", "alignment": "lawful evil",
         "domains": "serpents, forbidden knowledge, the first lie", "symbol": "a serpent swallowing a star",
         "blurb": "Who whispered the first forbidden truth and coils still around every secret worth killing for."},
        {"name": "Vael", "title": "the Silent Star", "alignment": "neutral evil",
         "domains": "the far cold void, isolation, the space between stars", "symbol": "a single black star",
         "blurb": "The cold intelligence of the outer dark, patient beyond mortal reckoning."},
        {"name": "Orovoreth", "title": "the Worm Unending", "alignment": "neutral evil",
         "domains": "decay, the cycle that eats itself, undeath's true source", "symbol": "a worm devouring its own tail",
         "blurb": "From whose endless coils, the cults say, the first undead crawled — the rot beneath Nyssa's law."},
    ],
    "archdevils": [
        {"name": "Belisar", "title": "the Iron Throne", "alignment": "lawful evil", "layer": 9,
         "domains": "tyranny, dominion, the ultimate contract", "symbol": "an iron throne on a black sun",
         "blurb": "Supreme lord of the Nine, to whom every devil's contract ultimately answers."},
        {"name": "Maltezar", "title": "the Climbing Duke", "alignment": "lawful evil", "layer": 8,
         "domains": "ambition, betrayal, the ladder of power", "symbol": "a dagger through a coronet",
         "blurb": "Ever scheming to unseat those above him; the model of every damned social climber."},
        {"name": "Vexthys", "title": "the Advocate", "alignment": "lawful evil", "layer": 7,
         "domains": "contracts, loopholes, the letter of the law", "symbol": "a quill dripping red ink",
         "blurb": "Who never breaks a bargain — and never writes one a mortal can safely sign."},
        {"name": "Grivane", "title": "the Warden of Chains", "alignment": "lawful evil", "layer": 6,
         "domains": "punishment, torment, the prisons of the damned", "symbol": "a chain knotted around a key",
         "blurb": "Keeper of the Hells' torments, who believes all suffering is merely order enforced."},
        {"name": "Lady Ashkeron", "title": "the Poisoned Gift", "alignment": "lawful evil", "layer": 5,
         "domains": "seduction, corruption of the noble, temptation", "symbol": "a rose with iron thorns",
         "blurb": "Who ruins the virtuous not with force but with exactly what they most wanted."},
        {"name": "Halphur", "title": "the Iron General", "alignment": "lawful evil", "layer": 4,
         "domains": "the Blood War, disciplined legions, conquest", "symbol": "a legion standard of black iron",
         "blurb": "Marshal of the Hells' armies against the Abyss; discipline made into a weapon."},
        {"name": "Ozramoth", "title": "the Miser", "alignment": "lawful evil", "layer": 3,
         "domains": "greed, hoarded souls, debt", "symbol": "a coffer overflowing with coins and eyes",
         "blurb": "Who counts souls like coins and forgives no debt, ever."},
        {"name": "Nyssial", "title": "the Whispering Flame", "alignment": "lawful evil", "layer": 2,
         "domains": "secrets sold, spies, the informant's coin", "symbol": "a candle flame shaped like an ear",
         "blurb": "Broker of every secret in the Hells; knows what you would pay anything to keep hidden."},
        {"name": "Caizel", "title": "the Fallen", "alignment": "lawful evil", "layer": 1,
         "domains": "pride, the fall from grace, ruined glory", "symbol": "a broken halo of black iron",
         "blurb": "Once of the Celestial Choir; the first through the gate of the Hells, and the proudest."},
    ],
    "demon_lords": [
        {"name": "Kzarruk", "title": "the Ruin-Maw", "alignment": "chaotic evil",
         "domains": "destruction, slaughter, the end of all things", "symbol": "a jagged maw over cracked earth",
         "blurb": "Mightiest of the Princes; wants nothing, builds nothing, and would unmake everything."},
        {"name": "Vhasst", "title": "the Rotting Prince", "alignment": "chaotic evil",
         "domains": "plague, decay, filth, disease", "symbol": "a weeping green sore",
         "blurb": "Whose gift is contagion and whose court is a charnel feast."},
        {"name": "Zurhaine", "title": "the Web-Mother", "alignment": "chaotic evil",
         "domains": "spiders, betrayal, ambush, poison", "symbol": "a black web strung with fangs",
         "blurb": "Patron of assassins and traitors, who spins loyalty only to cut it."},
        {"name": "Ghol", "title": "the Devourer", "alignment": "chaotic evil",
         "domains": "cannibalism, hunger, the feast of flesh", "symbol": "a ring of gnashing teeth",
         "blurb": "Whose worship is always, in the end, a meal — and the worshippers are the courses."},
        {"name": "Malephar", "title": "the Bloodhorn", "alignment": "chaotic evil",
         "domains": "rage, the berserker, mindless war", "symbol": "a bleeding horn on a red field",
         "blurb": "The scream that turns a battle into a massacre; reason drowned in blood."},
        {"name": "Xibeth", "title": "the Queen of Wounds", "alignment": "chaotic evil",
         "domains": "pain, torture as ecstasy, mutilation", "symbol": "a heart pierced by a dozen pins",
         "blurb": "For whom agony is the highest art and her own flesh the first canvas."},
        {"name": "Orruth", "title": "the Fly-King", "alignment": "chaotic evil",
         "domains": "vermin, corruption, buzzing madness", "symbol": "a crown of black flies",
         "blurb": "Prince of swarms and spoilage, whose droning is the sound of things falling apart."},
        {"name": "Sable", "title": "the Shadow-Serpent", "alignment": "chaotic evil",
         "domains": "darkness, fear, the hunting nightmare", "symbol": "a serpent of pure shadow",
         "blurb": "The dread in the dark that hunts for the joy of the terror, not the kill."},
    ],
}


def iter_powers():
    """Yield (family_key, member_dict) for every power in the closed pantheon."""
    for fam, members in _ROSTER.items():
        for m in members:
            yield fam, m


def power_by_name(name: str) -> Optional[dict]:
    """Look a power up by name or 'Name the Title' (case-insensitive).

    Returns a copy of the roster member with its ``family`` key added, or None.
    """
    q = (name or "").strip().lower()
    if not q:
        return None
    for fam, m in iter_powers():
        full = f"{m['name']} {m.get('title', '')}".strip().lower()
        if q == m["name"].lower() or q == full or (q in full and len(q) > 3):
            return {**m, "family": fam}
    return None


def worshipable_powers() -> list[dict]:
    """Powers mortals actually pray to (temple/cult families) — each with ``family``.
    Used to pick an avenging god for a slain worshipper who named no patron."""
    out = []
    for fam, m in iter_powers():
        if POWER_FAMILIES[fam].get("worship") in ("temples", "cults"):
            out.append({**m, "family": fam})
    return out


def _power_attrs(fam: str, m: dict) -> dict:
    meta = POWER_FAMILIES[fam]
    attrs = {
        "description": m["blurb"],
        "title": m["title"],
        "domain": m["domains"],
        "alignment": m["alignment"],
        "symbol": m["symbol"],
        "family": fam,
        "family_label": meta["label"],
        "power_class": meta["power_class"],
        "plane": meta["plane"],
        "worship": meta["worship"],
    }
    if "rank" in m:
        attrs["great_measure_rank"] = m["rank"]     # giant ordning standing
    if "layer" in m:
        attrs["hell_layer"] = m["layer"]            # which of the Nine
    if "court" in m:
        attrs["fey_court"] = m["court"]             # Seelie | Unseelie | Wild
    return attrs


def count_by_family(graph: WorldGraph) -> dict[str, int]:
    """Live census: how many LIVING powers sit in each family. Dead powers
    (status "dead" — slain/unmade in a divine event) stay in the graph as
    history but no longer consume their family's cap."""
    from sqlmodel import Session, select
    from .models import Entity
    counts = {fam: 0 for fam in POWER_FAMILIES}
    with Session(graph.engine) as s:
        for ent in s.exec(select(Entity).where(Entity.type == EntityType.DEITY)):
            if ent.status == "dead":
                continue
            fam = (ent.attributes or {}).get("family")
            if fam in counts:
                counts[fam] += 1
    return counts


def cap_for(family: str) -> Optional[int]:
    """The STATIC member ceiling for a family (None if the family isn't known)."""
    meta = POWER_FAMILIES.get(family)
    return meta["cap"] if meta else None


def _cap_overrides(graph: WorldGraph) -> dict:
    from sqlmodel import Session
    from .models import WorldMeta
    with Session(graph.engine) as s:
        meta = s.get(WorldMeta, 1)
        return dict((meta.pantheon_caps or {})) if meta else {}


def effective_cap(graph: WorldGraph, family: str) -> Optional[int]:
    """A family's live ceiling: its static cap, or a higher override raised by a
    divine event. This is what the world-law checks."""
    base = cap_for(family)
    ov = _cap_overrides(graph).get(family)
    if ov is None:
        return base
    return max(base or 0, int(ov))


def _raise_cap(graph: WorldGraph, family: str, new_cap: int) -> None:
    from sqlmodel import Session
    from .models import WorldMeta
    with Session(graph.engine) as s:
        meta = s.get(WorldMeta, 1) or WorldMeta(id=1)
        caps = dict(meta.pantheon_caps or {})
        caps[family] = int(new_cap)
        meta.pantheon_caps = caps
        s.add(meta)
        s.commit()


def apply_divine_event(
    graph: WorldGraph, *,
    family: str,
    new_powers: Optional[list[dict]] = None,
    dying: Optional[str] = None,
    reason: str = "",
    event_kind: str = "schism",
    session_id: Optional[str] = None,
) -> dict:
    """A DM-GATED divine event: a power dies and/or new powers arise.

    This is the ONLY sanctioned way to change the roster after seeding, so it is
    privileged — it writes the powers directly rather than going through the
    extraction world-law. It keeps the graph append-only (a slain power is marked
    ``status="dead"`` and kept as history, its worship edges closed) and keeps the
    per-family cap invariant true (the family's cap is raised to hold the new
    living count, so "one god dies, two arise" is allowed exactly once, as canon).

    ``new_powers``: dicts like the _ROSTER members ({name, title, alignment,
    domains, symbol, blurb}). ``dying``: a name/slug of the power being unmade.
    Returns {"created": [...], "died": slug|None, "cap": n, "notes": [...]}.
    """
    from sqlmodel import Session, select
    from .models import Entity, Relation, WorldMeta, RelationType as RT

    if family not in POWER_FAMILIES:
        raise ValueError(f"unknown power family: {family!r}")
    graph.create_tables()
    meta = POWER_FAMILIES[family]
    day = graph.current_day()
    notes: list[str] = []
    created: list[str] = []
    died_slug: Optional[str] = None

    # 1. A dying power: close its open worship edges, mark it dead (kept as
    #    history), and log the unmaking.
    if dying:
        dent = graph.get_entity(dying) or next(
            iter(graph.find_entities_by_name(dying)), None)
        if dent is not None and dent.type == EntityType.DEITY:
            with Session(graph.engine) as s:
                open_worship = s.exec(select(Relation).where(
                    Relation.dst_id == dent.id,
                    Relation.rel_type == RT.WORSHIPS,
                    Relation.valid_to == None)).all()  # noqa: E711
                for r in open_worship:
                    r.valid_to = day
                    s.add(r)
                s.commit()
                closed = len(open_worship)
            graph.upsert_entity(
                dent.name, EntityType.DEITY, slug=dent.slug, status="dead",
                attributes={**(dent.attributes or {}),
                            "died_day": day, "death_reason": reason})
            died_slug = dent.slug
            graph.add_event(
                f"{dent.name} is unmade ({event_kind}): {reason}".strip(": "),
                involved=[dent.slug], session_id=session_id)
            notes.append(f"{dent.name} is slain/unmade — {closed} worship bond(s) severed "
                         f"(kept as history).")

    # 2. New powers arise — written directly, tagged to the family.
    for p in (new_powers or []):
        attrs = _power_attrs(family, {
            "title": p.get("title", ""), "domains": p.get("domains", ""),
            "alignment": p.get("alignment", "neutral"),
            "symbol": p.get("symbol", ""),
            "blurb": p.get("blurb", p.get("description", "")),
        })
        attrs.update({"divine_event": True, "born_day": day, "origin": reason})
        ent = graph.upsert_entity(
            p["name"], EntityType.DEITY, subtype=meta["power_class"],
            attributes=attrs,
            tags=["deity", family, meta["power_class"]]
                 + (p.get("domains", "").split(", ")[:1] if p.get("domains") else []))
        created.append(ent.slug)
        notes.append(f"{ent.name} rises among {meta['label']}.")

    # 3. Keep the cap invariant: raise the family cap to hold the new LIVING count.
    living = count_by_family(graph)[family]
    cur_cap = effective_cap(graph, family) or 0
    if living > cur_cap:
        _raise_cap(graph, family, living)
        notes.append(f"{meta['label']} cap raised to {living} by this {event_kind}.")

    # 4. Log the event itself.
    graph.add_event(
        f"Divine {event_kind} — {meta['label']}: {reason}".strip(": "),
        involved=created + ([died_slug] if died_slug else []),
        session_id=session_id)

    return {"created": created, "died": died_slug,
            "cap": effective_cap(graph, family), "notes": notes}


def seed_pantheon(graph: WorldGraph) -> dict:
    """Seed every power family as a CLOSED set of DEITY entities plus the
    defining cross-family relations. Idempotent (upsert by slug).

    Returns {"families": {fam: {"label", "cap", "seeded"}}, "total", "cap"}.
    """
    graph.create_tables()
    e = graph.upsert_entity
    by_slug: dict[str, object] = {}
    seeded: dict[str, int] = {}

    for fam, members in _ROSTER.items():
        meta = POWER_FAMILIES[fam]
        for m in members:
            ent = e(m["name"], EntityType.DEITY, subtype=meta["power_class"],
                    attributes=_power_attrs(fam, m),
                    tags=["deity", fam, meta["power_class"]]
                         + m["domains"].split(", ")[:1])
            by_slug[ent.slug] = ent
        seeded[fam] = len(members)

    # --- Defining cross-family relations (only ones that shape play) ---
    # Each carries a pre-authored "why" (established lore) so the DM narrates the
    # SAME origin every time a priest or sage is asked — never re-improvises it.
    # These reasons are rendered inline in the world-context Relationships block.
    def rel(a: str, r: str, b: str, why: str = "") -> None:
        if a in by_slug and b in by_slug:
            graph.add_relation(by_slug[a], r, by_slug[b],
                               attributes={"reason": why} if why else None)

    # Ymmarch: the exile and the rebel stand against the World-Anvil's order.
    rel("vhorrek", RelationType.HOSTILE_TO, "vaskrun",
        "Cast out of the Great Measure for the monsters he sired, Vhorrek hates the All-Father who unmade his rank.")
    rel("diell", RelationType.HOSTILE_TO, "vaskrun",
        "Diell mocks the rigid Great Measure and defies the World-Anvil's cosmic order out of sheer spite.")
    rel("vhorrek", RelationType.HOSTILE_TO, "yssame",
        "Vhorrek warps the wild giant-kin Yssame nurtures, and she names his brood an abomination.")
    # Mortal gods: the tyrant preys on the lawful powers.
    rel("sith-ra", RelationType.HOSTILE_TO, "auren",
        "The Whisper's hidden cults exist to subvert every charter and law the Judge upholds.")
    rel("sith-ra", RelationType.HOSTILE_TO, "kael",
        "Sith'ra prizes ambition without conscience; Kael's kept oaths are the one thing her murders cannot buy.")
    rel("serath", RelationType.ALLIED_WITH, "cernow",
        "The Dawnmother's tilled fields and the Green Father's wild wood share one border, and long ago struck a truce to keep it.")
    rel("kael", RelationType.ALLIED_WITH, "auren",
        "The honorable blade and the sworn word: Kael defends what Auren's law declares just.")
    # Celestials serve the Sovereign ideals and war on the fiends.
    rel("myrrath", RelationType.ALLIED_WITH, "kael",
        "The Choir's blade fights for the same honorable war Kael blesses.")
    rel("veyanna", RelationType.ALLIED_WITH, "nyssa",
        "Veyanna lights the dying to the very gate Nyssa keeps; they share the road of the dead.")
    rel("myrrath", RelationType.HOSTILE_TO, "belisar",
        "The Sword of Judgment leads the celestial host against the Nine Hells and its Iron Throne.")
    rel("myrrath", RelationType.HOSTILE_TO, "kzarruk",
        "Myrrath wars without end on the Ruin-Maw whose only desire is to unmake all creation.")
    rel("caizel", RelationType.HOSTILE_TO, "auravel",
        "Caizel was of the Choir before his pride cast him into the Hells; he hates Auravel, the First he once stood beside.")
    # The Blood War: the Nine and the Abyss are eternal enemies.
    rel("halphur", RelationType.HOSTILE_TO, "kzarruk",
        "The Blood War: the disciplined legions of the Nine grind endlessly against the Abyss's mindless ruin.")
    rel("belisar", RelationType.HOSTILE_TO, "kzarruk",
        "Order against entropy — the Iron Throne's dominion cannot abide a prince who would unmake everything to rule.")
    # The Twofold Court is at war with itself.
    rel("bramblethorn", RelationType.HOSTILE_TO, "queen-verdaine",
        "Winter against summer: the Hollow King's frozen court and the Summer Crown's radiant one are the two halves of one endless feud.")
    # Death's law against the source of undeath.
    rel("nyssa", RelationType.HOSTILE_TO, "orovoreth",
        "The Pale Warden abhors the undead that cheat her gate — and the Worm Unending is the rot from which they first crawled.")
    rel("nyssa", RelationType.HOSTILE_TO, "vhorrek",
        "The keeper of the final gate names Vhorrek's warped, deathless brood a mockery of her law.")

    total = sum(seeded.values())
    return {
        "families": {fam: {"label": POWER_FAMILIES[fam]["label"],
                           "cap": POWER_FAMILIES[fam]["cap"], "seeded": seeded[fam]}
                     for fam in seeded},
        "total": total, "cap": PANTHEON_CAP,
    }
