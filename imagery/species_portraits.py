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
_FRAMING = ("head and shoulders character portrait bust, facing the viewer, "
            "neutral expression, plain dark stone background, even studio lighting, "
            "fantasy character concept art, single figure, no text")
_NEG_EXTRA = ("full body, multiple people, crowd, nudity, nsfw, modern clothing, "
              "photograph, low detail")

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
            look = SPECIES_LOOKS.get(slug) or SPECIES_LOOKS.get(_norm(r.name)) \
                or _fallback_look(r.name, r.size, getattr(r, "creature_type", "Humanoid"),
                                  getattr(r, "traits", None))
            rows.append((r.index_slug, look))
        if rows:
            return rows
    except Exception as e:
        print(f"[species] DB unavailable ({e}); using the built-in curated set.")
    return [(slug, look) for slug, look in SPECIES_LOOKS.items()]


def build_positive(look: Dict[str, str], sex: str, style_prompt: str) -> str:
    sexed = look.get("male" if sex == "m" else "female", "")
    parts = [look.get("shared", ""), sexed, _FRAMING, style_prompt]
    return ", ".join(p for p in parts if p)


_REF_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def _find_reference(ref_dir: Optional[Path], slug: str) -> Optional[Path]:
    """A real reference image for this species, if the operator supplied one
    (``<ref_dir>/<slug>.png`` etc.) — used to condition the render via IP-Adapter."""
    if not ref_dir:
        return None
    for ext in _REF_EXTS:
        p = ref_dir / f"{slug}{ext}"
        if p.is_file():
            return p
    return None


def generate_species(slugs: Optional[List[str]] = None, sexes: Optional[List[str]] = None,
                     *, force: bool = False, dry_run: bool = False,
                     ref_dir: Optional[Path] = None) -> int:
    cfg = get_config().imagery
    catalog = species_from_db()
    if slugs:
        want = {_ALIASES.get(_norm(s), _norm(s)) for s in slugs}
        catalog = [(sl, lk) for sl, lk in catalog if _norm(sl) in want]
    sexes = sexes or ["m", "f"]

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    style = cfg.style_prompt
    negative = f"{cfg.negative_prompt}, {_NEG_EXTRA}"

    client = None
    made = 0
    ref_cache: Dict[str, Optional[str]] = {}   # slug -> uploaded ComfyUI filename
    for slug, look in catalog:
        ref_path = _find_reference(ref_dir, slug)
        for sex in sexes:
            out = _OUT_DIR / f"{slug}-{sex}.webp"
            positive = build_positive(look, sex, style)
            tag = f"{slug}-{sex}"
            if dry_run:
                ref_note = f"  [ref: {ref_path.name}]" if ref_path else ""
                print(f"\n=== {tag}{ref_note} ===\n{positive}")
                continue
            if out.exists() and not force:
                print(f"· {tag}: exists, skipping (use --force to regenerate)")
                continue
            if client is None:
                client = client_from_config(cfg)
                if not client.is_available():
                    print("\n⚠ ComfyUI is not reachable at "
                          f"{cfg.base_url}. Start ComfyUI (API mode) and retry.")
                    return made
            # Upload the operator's reference once per species (IP-Adapter conditioning).
            ref_files = None
            if ref_path is not None:
                if slug not in ref_cache:
                    try:
                        ref_cache[slug] = client.upload_image(
                            ref_path.read_bytes(), f"species-ref-{slug}{ref_path.suffix}")
                    except Exception as e:
                        print(f"  (ref upload failed for {slug}: {e})")
                        ref_cache[slug] = None
                if ref_cache[slug]:
                    ref_files = [ref_cache[slug]]
            try:
                print(f"→ rendering {tag}{' [ref]' if ref_files else ''} …", flush=True)
                raw = client.generate(positive, negative, width=cfg.gen_width,
                                      height=cfg.gen_height, steps=cfg.steps,
                                      reference_filenames=ref_files)
                enc = encode_webp(raw, store_width=768, thumb_width=256,
                                  quality=cfg.webp_quality)
                out.write_bytes(enc.data)
                made += 1
                print(f"  ✓ wrote {out.relative_to(_OUT_DIR.parents[3])}")
            except ImageServiceUnavailable as e:
                print(f"  ✗ service offline: {e}")
                return made
            except Exception as e:
                print(f"  ✗ {tag} failed: {e}")
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
    a = ap.parse_args(argv)

    if a.list:
        for slug, look in species_from_db():
            curated = "curated" if slug in SPECIES_LOOKS else "fallback"
            print(f"{slug:16s} [{curated}] {look.get('shared', '')[:60]}…")
        return 0

    slugs = [s.strip() for s in a.species.split(",")] if a.species else None
    sexes = ["m", "f"] if a.sex == "both" else [a.sex]
    ref_dir = Path(a.ref_dir).expanduser() if a.ref_dir else None
    if ref_dir and not ref_dir.is_dir():
        print(f"⚠ --ref-dir {ref_dir} is not a folder; ignoring.")
        ref_dir = None
    n = generate_species(slugs, sexes, force=a.force, dry_run=a.dry_run, ref_dir=ref_dir)
    if not a.dry_run:
        print(f"\nDone — {n} portrait(s) generated into {_OUT_DIR}.")
        print("Review them, then `git add -f` the SRD/PHB ones you want in the repo "
              "(owned-book species art stays local).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
