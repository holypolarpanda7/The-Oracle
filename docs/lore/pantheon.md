# The Pantheon — Setting Canon

This is the world's **own** pantheon (not Faerûn). It is a **closed set**: the
gods below are seeded into every world up front (`eight_card_system/pantheon.py`,
via `seed_pantheon`, called from `seed_minimal_world`). Play is meant to *reuse*
these powers rather than invent new ones — see "Keeping the pantheon closed."

There are two families of powers: the younger **Sovereign Powers** most mortals
worship, and the elder **Ymmarch** — the giant gods.

---

## Cosmology (the short version)

Before the world there was only the **Unmade Silence**. The first act of making
was not a god's but a giant's: **Vaskrun the World-Anvil** hammered the raw stuff
of creation into land, sea, and sky, then sank into a dreaming sleep. From his
children came the **Great Measure** — the giants' sacred order of rank, by which
every giant knows its worth.

The **Sovereign Powers**, the younger gods, rose after — some say born of the
sleeping World-Anvil's dreams, some say they simply *arrived*. They took up the
worship of the mortal races the giants had ignored, and it is their names that
are spoken in most temples today. The giants call this a theft; the younger gods
call it stewardship. The quarrel is old and unfinished.

---

## The Sovereign Powers (younger gods; mortal pantheon)

| God | Title | Alignment | Domains |
|---|---|---|---|
| **Serath** | the Dawnmother | NG | sun, life, harvest, healing, hope |
| **Nyssa** | the Pale Warden | LN | death, rest, fate, the final gate |
| **Kael** | the Iron Oath | LG | war, honor, courage, protection |
| **Ilvaris** | the Weaver | N | magic, knowledge, the arcane |
| **Sydrelle** | of the Deep Tides | CN | sea, storms, sailors, the depths |
| **Cernow** | the Green Father | N | wild nature, beasts, druids |
| **Halene** | the Coinwright | NG | trade, roads, travelers, fortune |
| **Vesh** | the Veil | CN | night, moon, dreams, secrets, thieves |
| **Duran** | the Hammerbound | LG | craft, forge, stone, artisans |
| **Auren** | the Judge | LN | law, justice, oaths, civilization |
| **Maowen** | the Brightsong | CG | art, music, love, revelry |
| **Sith'ra** | the Whisper | LE | tyranny, deceit, ambition, murder |

**Serath** is the common faith of farming folk (Millbrook's shrine is hers).
**Sith'ra** is the setting's default villain-god: her cults hide inside courts.

---

## The Ymmarch (elder giant powers)

Ranked by their standing in the **Great Measure** (0 = the World-Anvil, above all;
negative ranks are the outcast and the rebel, outside the order).

| God | Title | Rank | Domains | Giant kind |
|---|---|---|---|---|
| **Vaskrun** | the World-Anvil | 0 | creation, giantkind, cosmic order | All-Father |
| **Skarnhault** | the Storm-Crowned | 1 | sky, sea-storm, prophecy, kingship | storm |
| **Orethun** | the Forgefather | 2 | fire, forge, war-craft, mastery | fire |
| **Yssame** | the Verdant | 3 | nature, the hunt, fertility, the young | giant-kin / firbolg |
| **Hrimvel** | the White Fury | 3 | ice, conquest, strength, the hunt | frost |
| **Kavdras** | the Stonewise | 4 | earth, stone, deep secrets, art | stone |
| **Maelivar** | the Gilded | 5 | wealth, fortune, pride, cunning | cloud |
| **Ghorroth** | the Ever-Hungry | 6 | hunger, appetite, brute survival | hill |
| **Vhorrek** | the Broken Crown | −1 (exiled) | monsters, deformity, spite | fomorians / twisted giants |
| **Diell** | the Wayward | −2 (rebel) | luck, mischief, wandering, defiance | giant-born trickster |

**The Great Measure** is this world's *ordning* — a giant's rank is a religious
fact, not just politics. **Vhorrek** was cast out for cruelty and fathers every
monstrous thing giant-kind spits out; he hates his kin. **Diell** walks the
mortal world at mortal size, mocking the whole rigid order — patron of anyone who
refuses the place they were born to.

---

## Relationships that shape play

- **Vhorrek** & **Diell** stand against **Vaskrun**'s order (from opposite ends:
  one monstrous, one rebellious).
- **Sith'ra** preys on the lawful mortal powers — **Auren** (law) and **Kael** (war).
- **Nyssa** (death) despises **Vhorrek**, whose creatures cheat the final gate.
- **Serath** ↔ **Cernow** (cultivated land ↔ wild land) and **Kael** ↔ **Auren**
  (honor ↔ law) are natural allies.

These are seeded as `HOSTILE_TO` / `ALLIED_WITH` edges between the deity entities.

---

## Keeping the pantheon closed

The pantheon is bounded on purpose so the narration→extraction loop can't inflate
it. The intended rules (see `PANTHEON_CAP = 30` in `pantheon.py`):

1. **Canon-first:** when narration names a "new god," resolve it to an existing
   power (an epithet/aspect) or to a **cult** (`FACTION`) before minting a new
   `DEITY`.
2. **Cap:** routine play may not push the deity count past `PANTHEON_CAP`; an
   over-budget new god is folded into the nearest existing power.
3. **Schism/apotheosis:** only a deliberate, DM-gated **divine event** may add or
   replace a god. Because the graph is append-only, a god's *death* closes its
   active worship edges but leaves it in the world as history — and a schism may
   mint 1–2 successor gods (and raise the cap). So "a god dies and is replaced by
   two" is allowed *as a rare authored event*, never as ambient world-gen.

> To reshape this canon, edit `_SOVEREIGN` / `_YMMARCH` in `pantheon.py` — the
> seeder is data-driven and idempotent.
