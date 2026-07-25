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
            "single figure, no text")
# Species house style — matches the reference: a soft, warm, semi-realistic
# painterly illustration (NOT the bold graphic-novel scene style). Used in place
# of the global style_prompt for portraits so scenes/items keep their own look.
_SPECIES_STYLE = ("soft painterly digital painting, semi-realistic stylized "
                  "fantasy character portrait, warm naturalistic lighting, "
                  "appealing expressive face with large lively eyes, smooth "
                  "confident brushwork with fine detail, muted warm earthy "
                  "palette, gentle atmosphere, high-quality fantasy character art")
# Light grounding so faces stay characterful rather than airbrushed. Males and
# non-small females get a touch of natural realism; the SMALL folk females read
# cuter (a weathered look is wrong on the little peoples).
_GRIT = "grounded semi-realism, natural skin texture, characterful weathered look"
_GRIT_FEM = ("cute and pretty, endearing charming face, soft rounded youthful "
             "features, large expressive eyes, smooth complexion, adorable")
_NEG_EXTRA = ("full body, multiple people, crowd, nudity, nsfw, modern clothing, "
              "photograph, low detail, plastic skin, airbrushed, harsh, ugly, "
              "grimdark, horror")

# Species art is only ever shown small in the CC menu (cards ~100px, the detail
# preview ~250px), so we store it far smaller than scene art — big space saving
# across the whole set with no visible loss on the cards.
_STORE_WIDTH = 512
_WEBP_QUALITY = 80

# Canon-accurate looks for the common SRD/PHB species. Each entry: shared traits
# plus a male/female cue. These are generic fantasy-species descriptions (own
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
        "female": "a dwarven woman, strong features, elaborately braided hair "
                  "(no beard), often braided sideburns"},
    "halfling": {
        "shared": "a small halfling with an adult but soft round face, curly hair, "
                  "rosy cheeks, warm cheerful eyes, simple rustic clothing, "
                  "childlike stature but clearly a grown adult",
        "male": "a jovial halfling man, curly hair, maybe light stubble",
        "female": "a cheerful halfling woman, bouncy curls"},
    "gnome": {
        "shared": "a very small gnome, oversized head-to-body proportions, large "
                  "bright curious eyes, a big nose, wild unruly hair, animated "
                  "mischievous grin, tinker's clothes with brass trinkets",
        "male": "a gnome man, wild hair and a pointed beard",
        "female": "a gnome woman, wild voluminous hair"},
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
        "female": "a powerful orc woman, strong jaw, prominent tusks, braided hair"},
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
        "shared": "a forest gnome, tiny and quick, warm nut-brown skin, oversized "
                  "bright eyes, a button nose, wild mossy-toned hair with twigs "
                  "and flowers, woodland clothing, an impish knowing smile",
        "male": "a forest-gnome man, leafy pointed beard",
        "female": "a forest-gnome woman, flower-woven wild hair"},
    "rock-gnome": {
        "shared": "a rock gnome tinkerer, tiny with an oversized head, huge "
                  "curious eyes, a big nose, soot-smudged cheeks, brass goggles on "
                  "the brow, frizzy wild hair, an inventor's leather apron of tools",
        "male": "a rock-gnome man, singed pointed beard, goggles",
        "female": "a rock-gnome woman, frizzy voluminous hair, goggles"},
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
    "aasimar": {
        "shared": "an aasimar, a celestial-touched human of ethereal beauty, "
                  "luminous softly-glowing eyes, faintly radiant skin sometimes "
                  "flecked with metallic light, a suggestion of a halo, serene "
                  "otherworldly presence",
        "male": "a radiant aasimar man, noble calm features",
        "female": "a radiant aasimar woman, luminous and graceful"},
    "goliath": {
        "shared": "a goliath, enormous and towering, gray stone-toned skin marked "
                  "with darker mottled patches and lithoderm bony growths, "
                  "sweeping tribal tattoos, a bald or minimally-haired head, a "
                  "heavy stony brow, mountain-giant heritage, tremendous muscle",
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
        "shared": "a drow (dark elf), obsidian to dusky-charcoal skin, stark white "
                  "or silver hair, long pointed ears, sharp angular features, pale "
                  "lavender or red eyes adapted to darkness, elegant dark attire",
        "male": "a drow man, cold refined features",
        "female": "a drow woman, imperious elegant features"},
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
                   cute: bool = False, skip_grit: bool = False) -> str:
    sexed = look.get("male" if sex == "m" else "female", "")
    parts = [look.get("shared", ""), sexed, _FRAMING, style_prompt]
    if not skip_grit:   # a style reference (IP-Adapter) defines the mood instead
        parts.append(_GRIT_FEM if (sex == "f" and cute) else _GRIT)
    return ", ".join(p for p in parts if p)


_REF_EXTS = (".png", ".jpg", ".jpeg", ".webp")


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
                     style_preset: str = "STANDARD (medium strength)") -> int:
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
    negative = f"{cfg.negative_prompt}, {_NEG_EXTRA}"
    small = small_race_slugs()   # their females render cuter
    # A style reference (IP-Adapter style-transfer) defines the whole set's look
    # from one image; drop the grit descriptors so the reference's mood leads.
    use_style_ref = style_ref is not None
    skip_grit = use_style_ref

    state: Dict[str, object] = {"client": None, "style_ref_name": None}
    made = 0
    ref_cache: Dict[str, Optional[str]] = {}   # ref path -> uploaded ComfyUI filename

    def ensure_client():
        if state["client"] is None:
            c = client_from_config(cfg)
            if ipadapter or use_style_ref:
                c.use_ipadapter = True
                if use_style_ref:
                    c.ipadapter_preset = style_preset
                if ip_weight is not None:
                    c.ipadapter_weight = float(ip_weight)
            if not c.is_available():
                return None
            state["client"] = c
        return state["client"]

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

    def render(out: Path, positive: str, tag: str, ref_files=None) -> bool:
        """Render one portrait. Returns False only on a fatal backend outage
        (stops the batch); a per-image failure is logged and skipped."""
        nonlocal made
        if dry_run:
            print(f"\n=== {tag} ===\n{positive}")
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
            raw = client.generate(positive, negative, width=cfg.gen_width,
                                  height=cfg.gen_height, steps=cfg.steps,
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
                if not render(out, build_positive(look, sex, style, cute, skip_grit),
                              f"{slug}-{sex}", ref_files):
                    return made

    # Lineage portraits, namespaced "<race>-<lineage>-<sex>.webp".
    for race_slug, lin_slug, look in lin_catalog:
        for sex in sexes:
            cute = sex == "f" and _norm(race_slug) in small
            ref_files = style_ref_files() if (use_style_ref and not dry_run) else None
            if not render(_OUT_DIR / f"{race_slug}-{lin_slug}-{sex}.webp",
                          build_positive(look, sex, style, cute, skip_grit),
                          f"{race_slug}-{lin_slug}-{sex}", ref_files):
                return made

    return made


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Generate species portraits for the CC menu.")
    ap.add_argument("--species", help="comma-separated slugs (default: all DB species)")
    ap.add_argument("--sex", choices=["m", "f", "both"], default="both")
    ap.add_argument("--force", action="store_true", help="regenerate even if a file exists")
    ap.add_argument("--dry-run", action="store_true", help="print prompts, generate nothing")
    ap.add_argument("--list", action="store_true", help="list the species that would be covered")
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
    ap.add_argument("--style-ref", help="one image whose ART STYLE every portrait should "
                    "match (IP-Adapter style-transfer). Grit descriptors are dropped so the "
                    "reference's look leads. Pair with --ip-weight (~0.8-1.0).")
    a = ap.parse_args(argv)

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
                         lineages=a.lineages, base=not a.skip_base, style_ref=style_ref)
    if not a.dry_run:
        print(f"\nDone — {n} portrait(s) generated into {_OUT_DIR}.")
        print("Review them, then `git add -f` the SRD/PHB ones you want in the repo "
              "(owned-book species art stays local).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
