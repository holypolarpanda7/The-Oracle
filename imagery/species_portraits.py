"""Generate generic species portraits for the character-creation menu.

One male + one female bust per playable species, so each species card in the CC
menu shows a player what that people looks like. Uses the project's own diffusion
backend (ComfyUI, via ``ComfyClient``) and house art style, and writes WebP art to
``activity-ui/public/assets/species/<slug>-<m|f>.webp``.

The species list is read from the LIVE rules DB, so whatever you've seeded —
including owned-book species — is covered automatically. Well-known SRD/PHB species
get hand-written, canon-accurate descriptors (a dwarf reads as a dwarf, a tiefling
has horns and a tail, a dragonborn is a scaled dragon-person…); anything else falls
back to a descriptor built from its name/size/type so it still renders on-theme.

Run (on the machine where ComfyUI is up):
    uv run python -m imagery.species_portraits              # all DB species, M+F
    uv run python -m imagery.species_portraits --dry-run    # print prompts only
    uv run python -m imagery.species_portraits --species dwarf,tiefling --force
    uv run python -m imagery.species_portraits --sex f --list
    uv run python -m imagery.species_portraits --audit [--prune]   # dead files

``--audit`` compares what's on disk against every filename the CC menu can ask
for (see ``expected_files``) and names the strays — a species that leaves the
rules DB leaves its art behind. ``--prune`` deletes them.

Nothing is committed automatically — review the art, then add the ones you want.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# The Windows console defaults to cp1252, which can't encode the ✓/✗/→ we print.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

from game_config import get_config
from .comfy_client import ImageServiceUnavailable, client_from_config
from .compress import encode_webp

_OUT_DIR = Path(__file__).resolve().parent.parent / "activity-ui" / "public" / "assets" / "species"

# Framing shared by every species portrait so the CC cards read as one set.
# Tuned to the reference art: a soft three-quarter bust against a blurred
# natural outdoor backdrop, warm gentle light.
_FRAMING = ("head and shoulders character portrait, three-quarter view facing "
            "the viewer, calm curious expression, softly blurred natural outdoor "
            "background with shallow depth of field, warm gentle rim light, "
            "single figure, no text, one uniform skin tone across the face, "
            "neck and shoulders")
# Species house style — matches the reference: a soft, warm, semi-realistic
# painterly illustration (NOT the bold graphic-novel scene style). Used in place
# of the global style_prompt for portraits so scenes/items keep their own look.
_SPECIES_STYLE = ("soft painterly digital painting, semi-realistic stylized "
                  "fantasy character portrait, warm naturalistic lighting, "
                  "smooth confident brushwork with fine detail, muted warm "
                  "earthy palette, gentle atmosphere, high-quality fantasy "
                  "character art")
# "An appealing face with large lively eyes" is lovely on a human and poison on
# a kenku — it is the single clause that quietly pulls every species back
# toward a pretty human. So it is only added for the peoples it suits.
_STYLE_HUMANLIKE = "appealing expressive face with large lively eyes"
_STYLE_CREATURE = ("believable non-human anatomy, the species' own skull shape "
                   "and eyes, creature-design integrity")
_STYLE_KINDRED = ("a real adult face rendered seriously, the species' own bone "
                  "structure and build, not a caricature")
# Light grounding so faces stay characterful rather than airbrushed. Males and
# non-small females get a touch of natural realism; the SMALL folk females read
# cuter (a weathered look is wrong on the little peoples).
_GRIT = "grounded semi-realism, natural skin texture, characterful weathered look"
# "Youthful" plus a small stature is how the gnome and halfling women came back
# as human CHILDREN. They are grown adults with soft round faces — say both.
_GRIT_FEM = ("an adult woman with a charming endearing face, soft rounded "
             "features, warm expressive eyes, smooth complexion, laugh lines, "
             "grown-up poise")
_NEG_EXTRA = ("full body, multiple people, crowd, nudity, nsfw, modern clothing, "
              "photograph, low detail, plastic skin, airbrushed, harsh, ugly, "
              "grimdark, horror")
# The small folk are the ones a "cute" style turns into children.
_NEG_CHILD = "child, little girl, little boy, teenager, baby face, toddler, youth"

# ---- keeping a species from collapsing into a human ------------------------
#
# The descriptors below are accurate and detailed, and the model still handed
# back a handsome human for firbolg, triton, goliath, aasimar, kalashtar,
# reborn, changeling and shifter. The reason is dilution, not description: the
# species clause is ~25 words at the head of a ~90-word prompt whose tail is all
# flattering-human-portrait language. The fix is to weight the species clause,
# repeat its name as an anchor, and say in the NEGATIVE what it must not be.
#
# Three tiers, because "not human" means different things:
#   human    — is a human; push nothing.
#   kindred  — a human-shaped face carrying unmistakable species traits; push
#              away from a PLAIN human, not from a human face.
#   creature — a non-human head; push away from a human face outright.
_HUMAN_SPECIES = {"human", "custom-lineage", "variant-human", "half-elf",
                  "khoravar"}
_CREATURE_SPECIES = {"dragonborn", "lizardfolk", "kobold", "kenku", "tabaxi",
                     "warforged"}

_NEG_KINDRED = ("plain ordinary human, generic human portrait, unmarked human "
                "face, the species traits missing, human cosplay, costume")
_NEG_CREATURE = (_NEG_KINDRED + ", human face, human head, human nose, "
                 "human skin, human hair, mostly human")
# Sex drifts under a style this flattering — changeling, tiefling and yuan-ti
# all came back as women in their MALE slot. Name it in the negative.
_NEG_BY_SEX = {
    "m": "woman, female, feminine face, lipstick, long eyelashes, breasts",
    # Dwarf art is so beard-dominated that a dwarven WOMAN comes back bearded
    # unless it is negated outright. No species' women want facial hair here
    # (dwarven braided sideburns are hair, and survive this).
    "f": "man, male, masculine jaw, stubble, beard, moustache, goatee, "
         "facial hair",
}


def species_tier(slug: str) -> str:
    """'human' | 'kindred' | 'creature' — how hard to push off a human face."""
    s = _norm(slug)
    if s in _HUMAN_SPECIES:
        return "human"
    if s in _CREATURE_SPECIES:
        return "creature"
    return "kindred"


def species_negative(base_negative: str, slug: str, sex: str,
                     small: bool = False,
                     look: Optional[Dict[str, str]] = None) -> str:
    """The negative prompt for one species/sex render.

    A look may carry its own ``negative``: saying "NO horns" in the positive
    does nothing (diffusion has no negation), so a species that keeps growing
    something it shouldn't — the firbolg's cattle horns — vetoes it here.
    """
    tier = species_tier(slug)
    parts = [base_negative, _NEG_EXTRA, _NEG_BY_SEX.get(sex, "")]
    if tier == "creature":
        parts.append(_NEG_CREATURE)
    elif tier == "kindred":
        parts.append(_NEG_KINDRED)
    if small:
        parts.append(_NEG_CHILD)
    parts.append((look or {}).get("negative", ""))
    return ", ".join(p for p in parts if p)

# Species art is only ever shown small in the CC menu (cards ~100px, the detail
# preview ~250px), so we store it far smaller than scene art — big space saving
# across the whole set with no visible loss on the cards.
_STORE_WIDTH = 512
_WEBP_QUALITY = 80
# The CC card shows these at aspect-ratio 3/4 with object-fit: cover, so a
# SQUARE render loses ~25% off the sides — and the composition was framed for a
# square the player never sees. Rendering at the card's own ratio puts every
# pixel on screen and makes the face bigger at the same file size. 896x1152 is
# an SDXL-native bucket close to 3:4 (0.78), so quality doesn't suffer.
_GEN_W, _GEN_H = 896, 1152

# Canon-accurate looks for the common SRD/PHB species. Each entry: shared traits
# plus a male/female cue, and optionally a ``negative`` the species must never
# grow (see species_negative). These are generic fantasy-species descriptions (own
# words), NOT any book's text.
SPECIES_LOOKS: Dict[str, Dict[str, str]] = {
    "human": {
        "shared": "an ordinary human of the realms, weathered adventurer's face, "
                  "varied realistic features, practical leather-and-cloth garb",
        "male": "a rugged man, short-cropped hair, light stubble",
        "female": "a determined woman, hair tied back for travel"},
    "elf": {
        "shared": "a tall slender elf, ageless angular face, high cheekbones, "
                  "long pointed ears, large almond eyes, smooth fair skin, "
                  "long straight hair, elegant elven attire",
        "male": "a graceful elven man, fine sharp jaw",
        "female": "a graceful elven woman, serene delicate features"},
    "half-elf": {
        "shared": "a half-elf, subtly pointed ears, a blend of human warmth and "
                  "elven grace, faintly angular features, expressive eyes",
        "male": "a charming half-elven man, light stubble",
        "female": "a striking half-elven woman, flowing hair"},
    "dwarf": {
        "shared": "a short stocky dwarf, broad powerful build, thick neck, ruddy "
                  "weathered skin, heavy brow, deep-set eyes, braided hair with "
                  "rings, stern proud expression, rugged armor",
        "male": "a dwarven man with a long thick braided beard",
        # Left at "strong features" the women came back as human travellers —
        # the dwarven build has to be named as loudly as the beard is.
        "female": "a dwarven WOMAN, clearly female and clean-shaven, but built "
                  "like a dwarf: a broad heavy-boned face, wide jaw, a large "
                  "blunt nose, ruddy weathered cheeks, thick neck and massive "
                  "shoulders, long elaborately braided hair with rings and "
                  "braided sideburns — short and stocky, not a slim human"},
    "halfling": {
        "negative": "child, teenager, boy, girl, chibi, cartoon, caricature, "
                    "doll, elf ears, pointed ears",
        "shared": "a halfling, a MIDDLE-AGED adult of a small people: a settled "
                  "grown-up face with deep laugh lines around the eyes and "
                  "mouth, weathered ruddy cheeks, a rounded jaw, thick curly "
                  "hair with grey coming in at the temples, small round ears "
                  "(not pointed), shrewd kindly eyes, simple rustic homespun, "
                  "painted seriously — a small ADULT of forty, not a child",
        "male": "a halfling man of middle years, curly greying hair, stubble",
        "female": "a halfling woman of middle years, greying curls, laugh lines"},
    "gnome": {
        "negative": "garden gnome, lawn ornament, figurine, toy, chibi, cartoon, "
                    "caricature, doll, plastic, child",
        "shared": "a gnome, a small elderly-featured adult with a lived-in "
                  "weathered face, crow's feet and fine wrinkles, a long "
                  "prominent nose, shrewd bright eyes under bushy brows, wiry "
                  "unruly hair going grey, neatly pointed ears, a tinker's "
                  "worn leather and brass trinkets, painted seriously",
        "male": "an old gnome man, wild grey hair and a pointed beard",
        "female": "an old gnome woman, wiry voluminous grey-streaked hair"},
    "half-orc": {
        "shared": "a powerful half-orc, greenish-gray skin, broad heavy jaw with "
                  "prominent lower tusks jutting up, sloped heavy brow, pointed "
                  "ears, coarse dark hair, battle scars, fierce proud gaze",
        "male": "a burly half-orc man, thick neck, top-knot or shaved head",
        "female": "a strong half-orc woman, high cheekbones, small tusks"},
    "orc": {
        "shared": "a full orc, massive and heavily muscled, deep gray-green skin, "
                  "a broad brutal jaw with large jutting tusks, a low heavy brow, "
                  "pointed ears, a flat wide nose, coarse black hair, war paint "
                  "and bone ornaments, a fierce commanding presence",
        "male": "a huge orc man, jutting tusks, shaved or mohawked head",
        "female": "a powerful orc woman, strong jaw, prominent tusks, braided "
                  "hair, the SAME deep grey-green skin over her whole face, "
                  "neck and shoulders",
        "negative": "two-tone skin, mismatched skin colour, war paint, face "
                    "paint, mask, pale jaw, human skin on the neck, horns, "
                    "antlers, demon"},
    "high-elf": {
        "shared": "a high elf, tall and refined, pale luminous skin, sharp regal "
                  "features, long pointed ears, cool jewel-toned eyes, immaculate "
                  "long hair, arcane scholar's circlet and fine silks",
        "male": "a poised high-elven man, aristocratic bearing",
        "female": "an elegant high-elven woman, serene and stately"},
    "wood-elf": {
        "shared": "a wood elf, lithe and wild, sun-touched coppery or tawny skin, "
                  "green and hazel eyes, long pointed ears, tousled earth-toned "
                  "hair with leaves and beads, weathered forest ranger's leathers",
        "male": "a rugged wood-elf man, feral grace, light face paint",
        "female": "a keen wood-elf woman, windswept hair, watchful eyes"},
    "forest-gnome": {
        "negative": "garden gnome, lawn ornament, figurine, toy, chibi, cartoon, "
                    "caricature, doll, plastic, child",
        "shared": "a forest gnome, a small elderly-featured adult with a "
                  "lived-in weathered face, crow's feet and fine wrinkles, a "
                  "long prominent nose, shrewd bright eyes under bushy brows, "
                  "neatly pointed ears — warm nut-brown sun-touched skin, wiry "
                  "moss-toned hair going grey with twigs and small flowers "
                  "caught in it, woodland clothing, painted seriously",
        "male": "an old forest-gnome man, leafy pointed grey beard",
        "female": "an old forest-gnome woman, flower-woven wiry grey hair"},
    "rock-gnome": {
        "negative": "garden gnome, lawn ornament, figurine, toy, chibi, cartoon, "
                    "caricature, doll, plastic, child",
        "shared": "a rock gnome tinkerer, a small elderly-featured adult with a "
                  "lived-in weathered face, crow's feet and fine wrinkles, a "
                  "long prominent nose, shrewd bright eyes under bushy brows, "
                  "neatly pointed ears — soot-smudged cheeks, brass goggles "
                  "pushed up on the brow, wiry grey hair, an inventor's leather "
                  "apron of tools, painted seriously",
        "male": "an old rock-gnome man, singed pointed grey beard, goggles",
        "female": "an old rock-gnome woman, wiry grey hair, goggles"},
    "tiefling": {
        "shared": "a tiefling: humanlike but clearly fiend-touched, prominent "
                  "curling horns rising from the brow, solid glowing eyes with no "
                  "visible sclera, small sharp fangs, a long pointed tail, richly "
                  "colored skin (deep red, violet, or dusky blue), dark hair",
        "male": "a tiefling man, swept-back horns, intense stare",
        "female": "a tiefling woman, elegant curling horns"},
    "dragonborn": {
        "shared": "a dragonborn: a proud draconic humanoid, a full reptilian "
                  "dragon head with a blunt snout and no external ears, sleek "
                  "colored scales (bronze, crimson, or steel-blue), a short frill "
                  "or small horns, reptilian slit-pupil eyes, no hair, muscular "
                  "scaled neck, ornate warrior's armor",
        "male": "a broad dragonborn warrior, heavier jaw and brow horns",
        "female": "a sleek dragonborn, finer features, subtle crest"},
    # Subtlety is what made these two render as plain handsome humans: a
    # "faint" glow and a "gray-toned" skin lose every argument with a warm
    # portrait style. The marks are stated as unmissable facts instead.
    "aasimar": {
        "shared": "an aasimar, a celestial-blooded being, skin visibly lit from "
                  "within with a soft golden glow, eyes shining solid luminous "
                  "silver-white with no visible pupil, delicate glowing filigree "
                  "markings tracing the cheekbones and brow, a faint ring of "
                  "light hanging behind the head, hair with a metallic sheen, "
                  "unmistakably not mortal",
        "male": "a radiant aasimar man, noble calm features, glowing sigils",
        "female": "a radiant aasimar woman, luminous and graceful, glowing sigils"},
    "goliath": {
        "shared": "a goliath, enormous and towering, skin a cool slate BLUE-GREY "
                  "(or a dull brick red), never tanned human flesh tones, "
                  "mottled with darker patches and studded with raised bony "
                  "lithoderm growths across the brow, jaw and shoulders, bold "
                  "dark tribal tattoos sweeping across the scalp and face, a "
                  "bald head, a heavy jutting stony brow ridge, small deep-set "
                  "eyes, mountain-giant heritage, colossal muscle",
        "negative": "tan skin, human skin tone, concrete grey, plain grey",
        "male": "a massive goliath man, jutting jaw, stony ridges",
        "female": "a towering goliath woman, angular stone-marked features"},
}

_ALIASES = {"half elf": "half-elf", "halfelf": "half-elf",
            "half orc": "half-orc", "halforc": "half-orc",
            "variant human": "human", "custom lineage": "human"}

# ---- lineage art -----------------------------------------------------------
# Only lineages that actually LOOK different from their base species get their
# own portrait; mechanical-only lineages (Goliath giant ancestries, Tiefling
# fiendish legacies) are omitted on purpose — they read as their base species,
# so the CC UI falls back to the base portrait and we store no near-duplicate
# art. Lineage files are namespaced "<race>-<lineage>-<sex>.webp".
_DRAGON_SCALES = {
    "black": "glossy jet-black scales", "blue": "deep cobalt-blue scales",
    "brass": "warm brass-yellow scales", "bronze": "burnished bronze scales",
    "copper": "ruddy copper-red scales", "gold": "gleaming golden scales, regal",
    "green": "mottled forest-green scales", "red": "fierce crimson-red scales, ember-lit",
    "silver": "bright silver scales, frost-touched", "white": "pale icy-white scales, frostbitten",
}
_SHIFTER_ASPECTS = {
    "beasthide": "bear-like beasthide shifter, heavy brow, thick shaggy mane, "
                 "broad rugged features, small blunt claws",
    "longtooth": "wolfish longtooth shifter, prominent jutting fangs, lean "
                 "predatory face, pointed ears, feral yellow eyes",
    "swiftstride": "cat-like swiftstride shifter, sleek fine fur, slit-pupil eyes, "
                   "high graceful cheekbones, alert pointed ears",
    "wildhunt": "stag-like wildhunt shifter, calm watchful eyes, faint antler nubs, "
                "earthy mottled fur, serene wild features",
}


def _dragon_look(color: str, scales: str) -> Dict[str, str]:
    base = SPECIES_LOOKS["dragonborn"]
    return {"shared": (f"a {color}-scaled dragonborn, proud draconic humanoid, full "
                       f"reptilian dragon head with a blunt snout and no ears, {scales}, "
                       "reptilian slit-pupil eyes, small horns or frill, no hair, "
                       "muscular scaled neck, ornate warrior's armor"),
            "male": base["male"], "female": base["female"]}


# Keyed by the DB lineage slug. Elf/gnome sub-looks reuse the curated species
# descriptors; drow, dragonborn colours and shifter aspects are defined here.
LINEAGE_LOOKS: Dict[str, Dict[str, str]] = {
    "high-elf": SPECIES_LOOKS["high-elf"],
    "wood-elf": SPECIES_LOOKS["wood-elf"],
    "forest-gnome": SPECIES_LOOKS["forest-gnome"],
    "rock-gnome": SPECIES_LOOKS["rock-gnome"],
    "drow": {
        "negative": "pale skin, white skin, fair skin, light skin, human skin "
                    "tone, tanned",
        "shared": "a drow (dark elf) whose skin is deep dusky VIOLET-PURPLE "
                  "shading to blue-black — richly coloured, never pale — stark "
                  "white or moonlight-silver hair against it, long pointed ears, "
                  "sharp angular features, pale lavender or red eyes adapted to "
                  "darkness, elegant dark attire",
        "male": "a drow man, cold refined features, dark violet skin",
        "female": "a drow woman, imperious elegant features, dark violet skin"},
    **{c: _dragon_look(c, s) for c, s in _DRAGON_SCALES.items()},
    **{slug: {"shared": desc, "male": f"a male {slug} shifter",
              "female": f"a female {slug} shifter"}
       for slug, desc in _SHIFTER_ASPECTS.items()},
}


def small_race_slugs() -> set:
    """Race slugs whose size is Small — their females get the cuter treatment."""
    try:
        from sqlmodel import Session, select
        from rules.query import RulesLibrary
        from rules.models import Race
        lib = RulesLibrary()
        with Session(lib.engine) as s:
            return {r.index_slug for r in s.exec(select(Race)).all()
                    if _norm(getattr(r, "size", "")) == "small"}
    except Exception:
        # Fallback to the known SRD small folk if the DB isn't reachable.
        return {"halfling", "gnome", "goblin", "kobold"}


def lineages_from_db() -> List[Tuple[str, str, Dict[str, str]]]:
    """(race_slug, lineage_slug, look) for every DB lineage we have curated art
    for. Lineages without a look are skipped — the UI falls back to base art."""
    try:
        from sqlmodel import Session, select
        from rules.query import RulesLibrary
        from rules.models import Race
        lib = RulesLibrary()
        out: List[Tuple[str, str, Dict[str, str]]] = []
        with Session(lib.engine) as s:
            for r in s.exec(select(Race)).all():
                for lin in (getattr(r, "lineages", None) or []):
                    slug = _norm(lin.get("slug") or "")
                    look = LINEAGE_LOOKS.get(slug)
                    if slug and look:
                        out.append((r.index_slug, slug, look))
        return out
    except Exception as e:
        print(f"[species] lineage DB unavailable ({e}); skipping lineages.")
        return []

# Owned-book species descriptors live in a LOCAL, gitignored override file so the
# public repo carries only SRD-safe descriptors (same policy as owned_books/*.json).
# Shape: {"<slug>": {"shared": "...", "male": "...", "female": "..."}}.
_LOOK_OVERRIDE_FILE = (Path(__file__).resolve().parent.parent
                       / "owned_books" / "species_looks.json")


def _load_look_overrides() -> Dict[str, Dict[str, str]]:
    try:
        if _LOOK_OVERRIDE_FILE.is_file():
            import json
            with open(_LOOK_OVERRIDE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            return {_norm(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception as e:
        print(f"[species] look-override file error: {e}")
    return {}


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _fallback_look(name: str, size: str, creature_type: str,
                   traits: Optional[list]) -> Dict[str, str]:
    """A generic, on-theme descriptor for a species we don't have a curated look
    for (e.g. an owned-book species) — built from its own mechanical fields."""
    ct = (creature_type or "humanoid").lower()
    sz = (size or "medium").lower()
    kin = "person" if ct == "humanoid" else ct
    base = (f"a {sz} {ct} of the {name} people, a distinctive fantasy {kin} with "
            f"striking non-human features, detailed and believable")
    return {"shared": base,
            "male": f"a {name} male", "female": f"a {name} female"}


def species_from_db() -> List[Tuple[str, Dict[str, str]]]:
    """Every playable species in the live rules DB → (slug, look-dict).

    Curated look when we have one, else a name-based fallback so owned-book
    species are covered too. Returns the curated set if the DB isn't reachable."""
    overrides = _load_look_overrides()
    try:
        from sqlmodel import Session, select
        from rules.query import RulesLibrary
        from rules.models import Race
        lib = RulesLibrary()
        rows = []
        with Session(lib.engine) as s:
            races = s.exec(select(Race)).all()
        for r in races:
            slug = _ALIASES.get(_norm(r.name), r.index_slug)
            look = (overrides.get(_norm(r.index_slug)) or overrides.get(_norm(r.name))
                    or SPECIES_LOOKS.get(slug) or SPECIES_LOOKS.get(_norm(r.name))
                    or _fallback_look(r.name, r.size,
                                      getattr(r, "creature_type", "Humanoid"),
                                      getattr(r, "traits", None)))
            rows.append((r.index_slug, look))
        if rows:
            return rows
    except Exception as e:
        print(f"[species] DB unavailable ({e}); using the built-in curated set.")
    merged = {**SPECIES_LOOKS, **overrides}
    return [(slug, look) for slug, look in merged.items()]


def build_positive(look: Dict[str, str], sex: str, style_prompt: str,
                   cute: bool = False, skip_grit: bool = False,
                   slug: str = "") -> str:
    """The positive prompt for one species/sex portrait.

    The species clause is CLIP-weighted and its name repeated at the tail: it
    has to outweigh the portrait-style language that follows it, or the render
    drifts back to a good-looking human (see the tier notes above).
    """
    sexed = look.get("male" if sex == "m" else "female", "")
    shared = look.get("shared", "")
    tier = species_tier(slug)
    anchor = _norm(slug).replace("-", " ")
    parts = [f"({shared}:1.35)" if shared and tier != "human" else shared,
             sexed,
             "a male" if sex == "m" else "a female",
             _FRAMING,
             _STYLE_HUMANLIKE if tier == "human"
             else _STYLE_CREATURE if tier == "creature" else _STYLE_KINDRED,
             style_prompt]
    if not skip_grit:   # a style reference (IP-Adapter) defines the mood instead
        parts.append(_GRIT_FEM if (sex == "f" and cute) else _GRIT)
    if tier == "creature" and anchor:
        parts.append(f"({anchor}:1.2)")   # last word on what this is
    return ", ".join(p for p in parts if p)


_REF_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def _kin_reference(slug: str, sex: str, race_slug: Optional[str] = None,
                   cross_sex: bool = False) -> Optional[Path]:
    """The already-rendered portrait a new one should take after, if any.

    IP-Adapter has been enabled and installed all along with nothing to feed
    it, because identity references meant sourcing art per species. But the set
    is its own best reference:

      * a LINEAGE takes after its base species — the forest gnome and the rock
        gnome should read as the same people as the gnome, which is exactly the
        note that came back from review. This is the useful link and it is on
        by default.
      * optionally (``cross_sex``), the FEMALE of a species takes after the
        male. This is OFF by default because it MEASURED BADLY: at weight 0.45
        it cloned rather than guided, and halfling, reborn, shifter, kalashtar,
        firbolg and tabaxi women came back as their own menfolk. Sex is a
        weaker signal in the prompt than a reference face is, so the reference
        simply wins. Two portraits of one species need to look like the same
        PEOPLE, which the prompt already handles — not the same PERSON.

    Returns None when the parent art doesn't exist yet, so a cold run still
    works — it just renders the base first and the lineages take after it.
    """
    if race_slug and race_slug != slug:
        base = _OUT_DIR / f"{race_slug}-{sex}.webp"       # lineage → its species
        if base.is_file():
            return base
    if cross_sex and sex == "f":
        male = _OUT_DIR / f"{race_slug or slug}-m.webp"   # female → the male
        if male.is_file():
            return male
    return None


def _find_reference(ref_dir: Optional[Path], slug: str,
                    sex: Optional[str] = None) -> Optional[Path]:
    """A real reference image for this species/sex, if the operator supplied one.
    Checks ``<slug>-<sex>.png`` first (sex-specific), then ``<slug>.png`` (both) —
    used to condition the render via IP-Adapter."""
    if not ref_dir:
        return None
    stems = ([f"{slug}-{sex}"] if sex else []) + [slug]
    for stem in stems:
        for ext in _REF_EXTS:
            p = ref_dir / f"{stem}{ext}"
            if p.is_file():
                return p
    return None


def generate_species(slugs: Optional[List[str]] = None, sexes: Optional[List[str]] = None,
                     *, force: bool = False, dry_run: bool = False,
                     ref_dir: Optional[Path] = None, ipadapter: bool = False,
                     ip_weight: Optional[float] = None,
                     lineages: bool = False, base: bool = True,
                     style_ref: Optional[Path] = None,
                     style_preset: str = "STANDARD (medium strength)",
                     kin: bool = False, kin_weight: float = 0.45,
                     kin_cross_sex: bool = False) -> int:
    cfg = get_config().imagery
    want = ({_ALIASES.get(_norm(s), _norm(s)) for s in slugs} if slugs else None)

    catalog = species_from_db()
    if want is not None:
        catalog = [(sl, lk) for sl, lk in catalog if _norm(sl) in want]
    lin_catalog = lineages_from_db() if lineages else []
    if want is not None:
        lin_catalog = [(r, l, lk) for (r, l, lk) in lin_catalog
                       if _norm(r) in want or _norm(l) in want]
    sexes = sexes or ["m", "f"]

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    style = _SPECIES_STYLE   # portrait-specific look (not the global scene style)
    base_negative = cfg.negative_prompt
    small = small_race_slugs()   # their females render cuter
    # A style reference (IP-Adapter style-transfer) defines the whole set's look
    # from one image; drop the grit descriptors so the reference's mood leads.
    use_style_ref = style_ref is not None
    skip_grit = use_style_ref

    state: Dict[str, object] = {"client": None, "style_ref_name": None}
    kin_cache: Dict[str, Optional[str]] = {}
    made = 0
    ref_cache: Dict[str, Optional[str]] = {}   # ref path -> uploaded ComfyUI filename

    def ensure_client():
        if state["client"] is None:
            c = client_from_config(cfg)
            if ipadapter or use_style_ref or kin:
                c.use_ipadapter = True
                if use_style_ref:
                    c.ipadapter_preset = style_preset
                # Kin references guide, they don't clone — a lineage that comes
                # back as a copy of its base species is as wrong as one that
                # looks unrelated. Lower weight than an identity reference.
                c.ipadapter_weight = float(
                    ip_weight if ip_weight is not None
                    else (kin_weight if kin else c.ipadapter_weight))
            if not c.is_available():
                return None
            state["client"] = c
        return state["client"]

    def kin_ref_files(slug: str, sex: str, race_slug: Optional[str] = None):
        """Upload the kin portrait for this render, once per file."""
        if not kin or dry_run:
            return None
        path = _kin_reference(slug, sex, race_slug, kin_cross_sex)
        if path is None:
            return None
        key = str(path)
        if key not in kin_cache:
            c = ensure_client()
            if c is None:
                return None
            try:
                kin_cache[key] = c.upload_image(path.read_bytes(),
                                                f"kin-{path.stem}.webp")
            except Exception as e:
                print(f"  (kin ref upload failed for {path.name}: {e})")
                kin_cache[key] = None
        name = kin_cache[key]
        return [name] if name else None

    def style_ref_files():
        """Uploaded filename of the global style reference, once, or None."""
        if not use_style_ref:
            return None
        c = ensure_client()
        if c is None:
            return None
        if state["style_ref_name"] is None:
            state["style_ref_name"] = c.upload_image(
                style_ref.read_bytes(), f"style-ref-{style_ref.stem}{style_ref.suffix}")
        n = state["style_ref_name"]
        return [n] if n else None

    def render(out: Path, positive: str, negative: str, tag: str,
               ref_files=None) -> bool:
        """Render one portrait. Returns False only on a fatal backend outage
        (stops the batch); a per-image failure is logged and skipped."""
        nonlocal made
        if dry_run:
            print(f"\n=== {tag} ===\n+ {positive}\n- {negative}")
            return True
        if out.exists() and not force:
            print(f"· {tag}: exists, skipping (use --force to regenerate)")
            return True
        client = ensure_client()
        if client is None:
            print(f"\n⚠ ComfyUI is not reachable at {cfg.base_url}. "
                  "Start ComfyUI (API mode) and retry.")
            return False
        try:
            print(f"→ rendering {tag}{' [ref]' if ref_files else ''} …", flush=True)
            raw = client.generate(positive, negative, width=_GEN_W,
                                  height=_GEN_H, steps=cfg.steps,
                                  reference_filenames=ref_files)
            enc = encode_webp(raw, store_width=_STORE_WIDTH, thumb_width=256,
                              quality=_WEBP_QUALITY)
            out.write_bytes(enc.data)
            made += 1
            print(f"  ✓ wrote {out.relative_to(_OUT_DIR.parents[3])} "
                  f"({len(enc.data) // 1024} KB)")
        except ImageServiceUnavailable as e:
            print(f"  ✗ service offline: {e}")
            return False
        except Exception as e:
            print(f"  ✗ {tag} failed: {e}")
        return True

    if base:
        for slug, look in catalog:
            for sex in sexes:
                out = _OUT_DIR / f"{slug}-{sex}.webp"
                if not dry_run and out.exists() and not force:
                    print(f"· {slug}-{sex}: exists, skipping (use --force to regenerate)")
                    continue
                # Reference conditioning: a global style ref (applied to all)
                # takes precedence over an optional per-species identity ref.
                ref_files = None
                if use_style_ref:
                    ref_files = None if dry_run else style_ref_files()
                elif kin:
                    ref_files = kin_ref_files(slug, sex)
                else:
                    ref_path = _find_reference(ref_dir, slug, sex)
                    if ref_path is not None and not dry_run:
                        if ensure_client() is None:
                            print(f"\n⚠ ComfyUI is not reachable at {cfg.base_url}.")
                            return made
                        key = str(ref_path)
                        if key not in ref_cache:
                            try:
                                ref_cache[key] = state["client"].upload_image(  # type: ignore[attr-defined]
                                    ref_path.read_bytes(),
                                    f"species-ref-{ref_path.stem}{ref_path.suffix}")
                            except Exception as e:
                                print(f"  (ref upload failed for {ref_path.name}: {e})")
                                ref_cache[key] = None
                        if ref_cache[key]:
                            ref_files = [ref_cache[key]]
                cute = sex == "f" and _norm(slug) in small
                if not render(out,
                              build_positive(look, sex, style, cute, skip_grit, slug),
                              species_negative(base_negative, slug, sex,
                                               _norm(slug) in small, look),
                              f"{slug}-{sex}", ref_files):
                    return made

    # Lineage portraits, namespaced "<race>-<lineage>-<sex>.webp".
    for race_slug, lin_slug, look in lin_catalog:
        for sex in sexes:
            cute = sex == "f" and _norm(race_slug) in small
            ref_files = (style_ref_files() if (use_style_ref and not dry_run)
                         else kin_ref_files(lin_slug, sex, race_slug))
            # A lineage inherits its parent species' tier (a red dragonborn
            # is as non-human as a dragonborn).
            if not render(_OUT_DIR / f"{race_slug}-{lin_slug}-{sex}.webp",
                          build_positive(look, sex, style, cute, skip_grit, race_slug),
                          species_negative(base_negative, race_slug, sex,
                                           _norm(race_slug) in small, look),
                          f"{race_slug}-{lin_slug}-{sex}", ref_files):
                return made

    return made


def expected_files() -> set:
    """Every filename the CC menu can ask for, from the live race list.

    Mirrors `speciesPortraitFor` in the client: `<race>-<sex>.webp` for each
    species plus `<race>-<lineage>-<sex>.webp` for each of its lineages. Any
    file on disk outside this set is dead weight (a species that left the DB —
    half-elf and half-orc, folded away by the 2024 rules, were the first).
    """
    want: set = set()
    try:
        from sqlmodel import Session, select
        from rules.query import RulesLibrary
        from rules.models import Race
        lib = RulesLibrary()
        with Session(lib.engine) as s:
            races = s.exec(select(Race)).all()
    except Exception as e:
        print(f"[species] race list unavailable ({e}); cannot audit.")
        return want
    for r in races:
        lins = [_norm(l.get("slug") or "") for l in (getattr(r, "lineages", None) or [])
                if isinstance(l, dict)]
        for sex in ("m", "f"):
            want.add(f"{r.index_slug}-{sex}.webp")
            for lin in lins:
                if lin:
                    want.add(f"{r.index_slug}-{lin}-{sex}.webp")
    return want


def audit(prune: bool = False) -> int:
    """Report (and optionally delete) species art the menu can never ask for."""
    want = expected_files()
    if not want:
        return 1
    have = {p.name for p in _OUT_DIR.glob("*.webp")}
    orphans = sorted(have - want)
    # Lineages we deliberately don't draw (mechanical-only ones fall back to the
    # base species) are "missing" by design, so they're reported, never fixed.
    missing = sorted(want - have)
    print(f"{len(have)} file(s) on disk · {len(want)} the menu can ask for\n")
    print(f"ORPHANED — never requested ({len(orphans)}):")
    for n in orphans:
        print(f"   {n}")
        if prune:
            (_OUT_DIR / n).unlink()
    if prune and orphans:
        print(f"   → deleted {len(orphans)} file(s)")
    print(f"\nNO ART — falls back to the base species, or hides ({len(missing)}):")
    for n in missing:
        print(f"   {n}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Generate species portraits for the CC menu.")
    ap.add_argument("--species", help="comma-separated slugs (default: all DB species)")
    ap.add_argument("--sex", choices=["m", "f", "both"], default="both")
    ap.add_argument("--force", action="store_true", help="regenerate even if a file exists")
    ap.add_argument("--dry-run", action="store_true", help="print prompts, generate nothing")
    ap.add_argument("--list", action="store_true", help="list the species that would be covered")
    ap.add_argument("--audit", action="store_true",
                    help="report art files the CC menu can never ask for (and which "
                         "species have none), then exit")
    ap.add_argument("--prune", action="store_true",
                    help="with --audit: delete the orphaned files")
    ap.add_argument("--ref-dir", help="folder of reference images (<slug>.png/jpg) to "
                    "condition each species on via IP-Adapter — 'use real art references'. "
                    "Requires use_ipadapter enabled + the ComfyUI_IPAdapter_plus nodes.")
    ap.add_argument("--ipadapter", action="store_true",
                    help="force IP-Adapter on for this run (use with --ref-dir)")
    ap.add_argument("--ip-weight", type=float, default=None,
                    help="IP-Adapter identity strength 0..1 (default from config, ~0.65)")
    ap.add_argument("--lineages", action="store_true",
                    help="also render per-lineage portraits for the visually-distinct "
                    "lineages (elf high/wood/drow, gnome forest/rock, dragonborn scale "
                    "colours, shifter aspects) as <race>-<lineage>-<sex>.webp")
    ap.add_argument("--skip-base", action="store_true",
                    help="skip the base-species pass (use with --lineages for lineages only)")
    ap.add_argument("--kin", action="store_true",
                    help="hold a family together with IP-Adapter, using the set's OWN art: "
                         "a lineage takes after its base species, a female after the male. "
                         "Render the base/male first (a missing parent is simply skipped).")
    ap.add_argument("--kin-weight", type=float, default=0.45,
                    help="how hard a kin reference pulls (default 0.45 — guide, not clone)")
    ap.add_argument("--kin-cross-sex", action="store_true",
                    help="also make each female take after her species' male. OFF by "
                         "default: at any useful weight the reference face beats the "
                         "prompt's sex cue and the women come back as the men.")
    ap.add_argument("--style-ref", help="one image whose ART STYLE every portrait should "
                    "match (IP-Adapter style-transfer). Grit descriptors are dropped so the "
                    "reference's look leads. Pair with --ip-weight (~0.8-1.0).")
    a = ap.parse_args(argv)

    if a.audit:
        return audit(prune=a.prune)

    if a.list:
        overrides = _load_look_overrides()
        for slug, look in species_from_db():
            src = ("curated" if _norm(slug) in {_norm(k) for k in SPECIES_LOOKS}
                   else "override" if _norm(slug) in overrides else "fallback")
            print(f"{slug:18s} [{src}] {look.get('shared', '')[:58]}…")
        return 0

    slugs = [s.strip() for s in a.species.split(",")] if a.species else None
    sexes = ["m", "f"] if a.sex == "both" else [a.sex]
    ref_dir = Path(a.ref_dir).expanduser() if a.ref_dir else None
    if ref_dir and not ref_dir.is_dir():
        print(f"⚠ --ref-dir {ref_dir} is not a folder; ignoring.")
        ref_dir = None
    style_ref = Path(a.style_ref).expanduser() if a.style_ref else None
    if style_ref and not style_ref.is_file():
        print(f"⚠ --style-ref {style_ref} is not a file; ignoring.")
        style_ref = None
    n = generate_species(slugs, sexes, force=a.force, dry_run=a.dry_run, ref_dir=ref_dir,
                         ipadapter=a.ipadapter or bool(ref_dir), ip_weight=a.ip_weight,
                         lineages=a.lineages, base=not a.skip_base, style_ref=style_ref,
                         kin=a.kin, kin_weight=a.kin_weight,
                         kin_cross_sex=a.kin_cross_sex)
    if not a.dry_run:
        print(f"\nDone — {n} portrait(s) generated into {_OUT_DIR}.")
        print("Review them, then `git add -f` the SRD/PHB ones you want in the repo "
              "(owned-book species art stays local).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
