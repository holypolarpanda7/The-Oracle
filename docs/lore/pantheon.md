# The Powers of the World — Setting Canon

This world's **own** cosmology (not Faerûn). Cosmic powers are grouped into
**families**, each with its own **label**, home **plane**, member **class**, and
its own independent **cap**. Giant gods are their own family, separate from the
mortal gods — and the powers that *aren't* gods (archfey, the Old Gods, the
archdevils, the demon princes) are first-class families with their own labels and
counters, not filed under "the pantheon."

Every family is a **closed set**, seeded up front (`eight_card_system/pantheon.py`,
`seed_pantheon`, called from the world seeders). Play *reuses* these powers; it
does not invent new ones (see "Keeping it closed").

All powers are stored as `DEITY` entities tagged `attributes.family` +
`subtype = power_class`, so each family is labeled and counted separately.

---

## The families at a glance

| Family (label) | Plane | Class | Cap | Mortals relate by |
|---|---|---|---|---|
| **The Sovereign Powers** | the Material Realm & the Heavens | god | 14 | temples |
| **The Ymmarch** (giant gods) | the primordial world | giant-god | 12 | temples |
| **The Celestial Choir** | the Upper Heavens | celestial | 8 | allies |
| **The Twofold Court** (archfey) | the Feywild | archfey | 10 | pacts |
| **The Old Gods** (Elder Ones) | before-time / the Outside | elder | 8 | cults |
| **The Lords of the Nine** | the Nine Hells | archdevil | 9 | pacts |
| **The Demon Princes** | the Abyss | demon-lord | 12 | cults |

Total ceiling `PANTHEON_CAP` = **73** (currently ~57 seeded, leaving headroom for
regional cults and a rare schism).

---

## Cosmology (the short version)

Before the world was the **Unmade Silence**, where the **Old Gods** already
stirred. The first act of making was a giant's: **Vaskrun the World-Anvil**
hammered creation into land, sea, and sky, then sank into dreaming sleep — and
his children became the **Ymmarch**, ranked under the **Great Measure** (this
world's *ordning*). The younger **Sovereign Powers** rose after and took up the
worship of the mortal races; the giants call it theft. The **Celestial Choir**
serves the Sovereign ideals from the Upper Heavens and wars endlessly on the
fiends — the **Lords of the Nine** (lawful tyranny, by contract) and the **Demon
Princes** (chaotic ruin), who also war on *each other* in the eternal Blood War.
Sideways to all of it lies the **Feywild** and its **Twofold Court**, and beneath
everything the **Old Gods** wait for the making to end.

---

## The Sovereign Powers (mortal gods) — cap 14

Serath the Dawnmother (sun/life/harvest — the common faith), Nyssa the Pale
Warden (death/fate), Kael the Iron Oath (war/honor), Ilvaris the Weaver (magic),
Sydrelle of the Deep Tides (sea), Cernow the Green Father (wild nature), Halene
the Coinwright (trade/luck), Vesh the Veil (night/thieves), Duran the Hammerbound
(craft), Auren the Judge (law), Maowen the Brightsong (art), **Sith'ra the
Whisper** (tyranny/murder — the setting's villain-god).

## The Ymmarch (giant gods) — cap 12

Ranked by the **Great Measure**: **Vaskrun** the World-Anvil (All-Father, 0),
then the giant-kind powers — Skarnhault (storm, 1), Orethun (fire, 2), Yssame
(nature/giant-kin, 3), Hrimvel (frost, 3), Kavdras (stone, 4), Maelivar (cloud,
5), Ghorroth (hill, 6) — plus **Vhorrek** the Broken Crown (exiled, −1; father of
fomorians and monsters) and **Diell** the Wayward (rebel trickster, −2).

## The Celestial Choir — cap 8

Auravel the Radiant Herald, Solenne the Mercy, Myrrath the Sword of Judgment,
Caelith the Watcher at the Threshold, Veyanna the Consoler, Orimel the
Chronicler. Servants and allies of the Sovereign Powers; the host against the
Hells and the Abyss. Rarely worshipped directly — invoked, allied, served.

## The Twofold Court — archfey — cap 10

**Seelie:** Queen Verdaine the Summer Crown, Ashelwin the Lord of Blossoms. **Un­seelie:**
Bramblethorn the Hollow King, Lady Mothmourn the Autumn Widow. **Wild:** The Piper
at the Hedge, Karn the Antlered Hunt (the Wild Hunt). Not gods — **patrons** who
strike bargains (warlock pacts). The two courts are at war with each other.

## The Old Gods (the Elder Ones) — cap 8

Morloth the Unmade (entropy), Yshara the Deep-Dreaming (drowning madness), Ghulra
the Hunger Below (the devouring earth), Ssythra the Coiled Dark (forbidden lore),
Vael the Silent Star (the outer void), Orovoreth the Worm Unending (decay / the
true source of undeath). Mostly imprisoned or sleeping; reached only through
**cults**. Not gods in the Sovereign sense — older, and hungrier.

## The Lords of the Nine — archdevils — cap 9

One lord per layer of the Hells: **Belisar** the Iron Throne (supreme, 9),
Maltezar (8), Vexthys the Advocate (7), Grivane the Warden (6), Lady Ashkeron (5),
Halphur the Iron General (4), Ozramoth the Miser (3), Nyssial the Whispering Flame
(2), **Caizel** the Fallen (1 — once of the Celestial Choir). Powers of tyranny by
**contract**; warlock patrons who never break a bargain and never write a safe one.

## The Demon Princes — archdemons — cap 12

Kzarruk the Ruin-Maw (destruction), Vhasst the Rotting Prince (plague), Zurhaine
the Web-Mother (betrayal/poison), Ghol the Devourer (hunger), Malephar the
Bloodhorn (rage), Xibeth the Queen of Wounds (pain), Orruth the Fly-King
(vermin), Sable the Shadow-Serpent (fear). Lords of chaos and ruin, reached
through **cults**. At eternal war with the Nine.

---

## Keeping it closed (the per-family world-law)

The powers are bounded on purpose so the narration→extraction loop can't inflate
any family. Intended rules (see `POWER_FAMILIES` caps + `count_by_family` /
`cap_for` in `pantheon.py`):

1. **Canon-first:** a "new god/patron/demon" from narration resolves to an
   existing power (an epithet/aspect) or becomes a **cult** (`FACTION`) before a
   new `DEITY` is minted.
2. **Per-family cap:** each family has its own ceiling; an over-cap new power is
   folded into the nearest existing member of *that* family. Counters and labels
   are per-family, so the Abyss filling up never blocks a new mortal saint.
3. **Schism / apotheosis / summoning:** only a deliberate, DM-gated event may add
   or replace a power. The graph is append-only, so a power's *death* closes its
   worship edges but leaves it as history; a schism may mint 1–2 heirs and raise
   that family's cap. "A god dies and is replaced by two" is allowed as a rare
   authored event — never as ambient world-gen.

> To reshape the canon, edit `POWER_FAMILIES` (families + caps) and `_ROSTER`
> (members) in `pantheon.py`. The seeder is data-driven and idempotent.
