"""Render a TRAINING SET for a house-style LoRA, with captions, at full size.

Pony renders painted where the house style inks, and no combination of
downloaded LoRAs closed that (21 stacks tried — see style-probe/
pony_house_match*.py). The remaining move is to train the house style itself
and apply it to Pony. This builds the dataset for that.

Why not just use the art already on disk: species portraits are RENDERED at
896x1152 and STORED at 512x658 (_STORE_WIDTH), and the originals are discarded.
Items are 768 and sprites 512. SDXL trains in 1024 buckets, so training on
those teaches the LoRA downscaled softness — you would bake in blur and lose
exactly the crisp ink the whole exercise is chasing. Rendering fresh at full
size is cheap and fully under our control.

THE CAPTION RULE, which decides whether the LoRA works at all: caption the
CONTENT, never the STYLE. Anything named in a caption is attributed to those
words; anything left unnamed and constant across the set is attributed to the
TRIGGER. So the captions here describe species, sex, framing and lighting, and
say nothing about ink, cel shading or palette — those are the residual we want
`oraclehouse` to own. A caption that helpfully mentions "bold ink outlines"
would hand the style to that phrase and leave the trigger holding nothing.

Balanced male/female by construction, because this project has already shipped
a sex-linked style split once (scripts/species_style_gap.py) and a training set
that inherits it would make it permanent.

    ./.venv/Scripts/python.exe scripts/lora_dataset.py --variants 10
    ./.venv/Scripts/python.exe scripts/lora_dataset.py --plan     # count only

Resumable: an image whose file already exists is skipped, so a crash or a
freeze costs only what it had not reached. MUST run under the WINDOWS
interpreter — ComfyUI is a Windows process (see CLAUDE.md -> Environment).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imagery.prompt_build import BuiltPrompt                       # noqa: E402
from imagery.species_portraits import (                            # noqa: E402
    _GEN_W, _GEN_H, _STYLE_CREATURE, _STYLE_HUMANLIKE, _STYLE_KINDRED,
    _STYLE_WEIGHT, species_from_db, species_negative, species_tier,
)

OUT = Path(__file__).resolve().parent.parent / "lora-train" / "oraclehouse"
TRIGGER = "oraclehouse"

#: Framing and lighting are what VARY, so the LoRA learns a style rather than
#: one composition. Each entry is (caption fragment, prompt fragment) — the two
#: differ because the prompt may carry craft language the caption must not.
VIEWS = [
    ("head and shoulders portrait, three-quarter view",
     "head and shoulders character portrait, three-quarter view facing the viewer"),
    ("head and shoulders portrait, facing forward",
     "head and shoulders character portrait, facing the viewer directly"),
    ("bust portrait, turned slightly away",
     "bust portrait, the head turned slightly away from the viewer"),
    ("close portrait of the face",
     "a close character portrait, the face filling the frame"),
    ("chest-up portrait, shoulders squared",
     "chest-up character portrait, shoulders squared to the viewer"),
]

LIGHTS = [
    ("warm rim light, blurred outdoor background",
     "warm gentle rim light, softly blurred natural outdoor background"),
    ("cold moonlight, night background",
     "cold blue moonlight from one side, a dark night background"),
    ("firelight from below, dark background",
     "warm firelight from below, a dark background"),
    ("overcast daylight, pale background",
     "flat overcast daylight, a pale neutral background"),
    ("low sun behind, bright edge light",
     "a low sun directly behind the figure, bright edge light, haze"),
]

EXPRESSIONS = [
    ("a calm expression", "a calm curious expression"),
    ("a hard stare", "a hard level stare"),
    ("a slight smile", "the faintest smile"),
    ("a weary look", "a weary, worn look"),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variants", type=int, default=10,
                    help="images per species+sex (default 10 -> ~520 total)")
    ap.add_argument("--species", help="comma-separated slugs (default: all)")
    ap.add_argument("--seed", type=int, default=770000,
                    help="base seed; each cell offsets from it deterministically")
    ap.add_argument("--plan", action="store_true",
                    help="print what would be rendered and stop")
    a = ap.parse_args(argv)

    looks = dict(species_from_db())
    slugs = ([s.strip() for s in a.species.split(",")] if a.species
             else sorted(looks))
    unknown = [s for s in slugs if s not in looks]
    if unknown:
        print(f"unknown species: {unknown}")
        return 2

    jobs = []
    for slug in slugs:
        for sex in ("m", "f"):                      # balanced by construction
            for i in range(a.variants):
                jobs.append((slug, sex, i))

    print(f"{len(slugs)} species x 2 sexes x {a.variants} = {len(jobs)} images")
    print(f"-> {OUT}")
    if a.plan:
        est = len(jobs) * 12 / 60
        print(f"~{est:.0f} min of GPU at ~12s each")
        for slug, sex, i in jobs[:4]:
            pos, cap = _build(looks, slug, sex, i)
            print(f"\n--- {slug}-{sex}-{i}\nCAPTION: {cap}\nPROMPT : {pos[:150]}...")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    from imagery import ImageStore
    store = ImageStore()
    cfg = store._cfg()
    store._config = cfg

    done = skipped = failed = 0
    for n, (slug, sex, i) in enumerate(jobs, 1):
        stem = f"{slug}-{sex}-{i:02d}"
        img = OUT / f"{stem}.png"
        if img.is_file():                            # resumable
            skipped += 1
            continue
        pos, cap = _build(looks, slug, sex, i)
        bp = BuiltPrompt(positive=pos,
                         negative=species_negative(cfg.negative_prompt, slug, sex,
                                                   look=looks[slug]),
                         descriptor="", descriptor_hash="", caption="", kind="pc")
        raw, _s, offline = store._render(cfg, bp, "train", seed=a.seed + n,
                                         width=_GEN_W, height=_GEN_H)
        if offline or not raw:
            failed += 1
            print(f"[{n}/{len(jobs)}] {stem}  FAILED/OFFLINE")
            continue
        img.write_bytes(raw)
        (OUT / f"{stem}.txt").write_text(cap, encoding="utf-8")
        done += 1
        if done % 10 == 0 or n == len(jobs):
            print(f"[{n}/{len(jobs)}] {stem}  ok   "
                  f"(done {done}, skipped {skipped}, failed {failed})", flush=True)

    print(f"\ndone {done}, skipped {skipped}, failed {failed}")
    print(f"dataset: {len(list(OUT.glob('*.png')))} images with captions in {OUT}")
    return 0


def _build(looks, slug: str, sex: str, i: int) -> tuple[str, str]:
    """(positive prompt, caption) for one training image.

    The prompt reproduces the LIVE house look — same species clause weight,
    same tier style line, same weighted art direction — because the LoRA is
    meant to learn what the game actually ships, not an approximation of it.
    """
    look = looks[slug]
    tier = species_tier(slug)
    # Coprime strides, so 10 variants reach ALL five views AND all five
    # lightings instead of pairing them. `i // len(VIEWS)` looked reasonable and
    # gave only two of the five lightings at the default variant count — which
    # would teach the style under one light and call it the style.
    view_c, view_p = VIEWS[i % len(VIEWS)]
    light_c, light_p = LIGHTS[(i * 2) % len(LIGHTS)]
    expr_c, expr_p = EXPRESSIONS[i % len(EXPRESSIONS)]
    sexed = look.get("male" if sex == "m" else "female", "")

    from game_config import get_config
    style = get_config().imagery.style_prompt

    positive = ", ".join(p for p in [
        f"({look.get('shared','')}:1.35)" if tier != "human" else look.get("shared", ""),
        sexed,
        "a male" if sex == "m" else "a female",
        view_p, expr_p, light_p, "single figure, no text",
        _STYLE_HUMANLIKE if tier == "human"
        else _STYLE_CREATURE if tier == "creature" else _STYLE_KINDRED,
        f"({style}:{_STYLE_WEIGHT})",
    ] if p)

    # CONTENT ONLY. No ink, no cel shading, no palette — see the module docstring.
    name = slug.replace("-", " ")
    article = "an" if name[:1] in "aeiou" else "a"
    caption = ", ".join([
        TRIGGER,
        f"{article} {name} {'man' if sex == 'm' else 'woman'}",
        view_c, expr_c, light_c, "single figure",
    ])
    return positive, caption


if __name__ == "__main__":
    raise SystemExit(main())
