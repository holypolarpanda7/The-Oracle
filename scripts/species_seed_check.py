"""Render ONE species, both sexes, across several seeds, at the live config.

A species look is judged from a single render per sex, which is exactly how a
descriptor rewrite gets called fixed when it only got lucky. The goliath rewrite
oscillated across five passes -- pink human, gargoyle, pink human -- and every
swing looked decisive on one seed. Three seeds per sex made the pattern legible.

    ./.venv/Scripts/python.exe scripts/species_seed_check.py goliath

MUST run under the WINDOWS interpreter -- ComfyUI is a Windows process and WSL
cannot reach it (see CLAUDE.md -> Environment). Renders bypass the image DB.
"""
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from imagery import ImageStore
from imagery.prompt_build import BuiltPrompt
from imagery.species_portraits import (
    _GEN_W, _GEN_H, SPECIES_LOOKS, build_positive, species_negative,
)

SLUG = sys.argv[1] if len(sys.argv) > 1 else "goliath"
SEEDS = [int(x) for x in sys.argv[2:]] or [20260731, 7, 991234]
OUT = ROOT / "style-probe" / f"seed-check-{SLUG}"
OUT.mkdir(parents=True, exist_ok=True)

store = ImageStore()
cfg = store._cfg()
print("stack:", [(l["name"], l["model"]) for l in store._loras_for(cfg, "pc")])

look = SPECIES_LOOKS[SLUG]
cells = []
for sex in ("m", "f"):
    pos = build_positive(look, sex, cfg.style_prompt, slug=SLUG)
    neg = species_negative(cfg.negative_prompt, SLUG, sex, look=look)
    for seed in SEEDS:
        p = BuiltPrompt(positive=pos, negative=neg, descriptor="",
                        descriptor_hash="", caption="", kind="pc")
        raw, _s, offline = store._render(cfg, p, SLUG, seed=seed,
                                         width=_GEN_W, height=_GEN_H)
        if offline or not raw:
            print(f"{sex} {seed}: OFFLINE")
            continue
        f = OUT / f"{SLUG}-{sex}-{seed}.png"
        f.write_bytes(raw)
        cells.append(f)
        print(f"{sex} {seed}: ok")

from PIL import Image
ims = [Image.open(c).convert("RGB") for c in cells]
for im in ims:
    im.thumbnail((420, 420), Image.LANCZOS)
cols = len(SEEDS)
rows = (len(ims) + cols - 1) // cols
w, h = max(i.width for i in ims), max(i.height for i in ims)
sheet = Image.new("RGB", (cols * w, rows * h), (18, 20, 30))
for i, im in enumerate(ims):
    sheet.paste(im, ((i % cols) * w, (i // cols) * h))
sheet.save(OUT / "_sheet.png")
print("sheet:", OUT / "_sheet.png")
