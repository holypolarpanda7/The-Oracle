"""Sweep a HOUSE-STYLE LoRA across every kind it touches, at several strengths.

``scripts/map_lora_probe.py`` answers the map question. This answers the other
one: the house style applies to ``pc`` ``npc`` ``creature`` ``place`` ``item``
``scene`` at once, so a style that flatters a crypt and turns the starter
village into a charnel house is a net loss. One good portrait proves nothing.

Each subject is rendered at every ``--weight`` from the SAME seed, so the
columns of the sheet differ only by the LoRA. The rows are picked to put the
two known failure modes on screen next to each other:

  * ``goliath`` / ``tiefling`` go through the REAL 166-word species prompt
    (``species_portraits.build_positive``). A style LoRA that eats "slate
    blue-grey skin" or "purple, horned" is the failure four rounds of prompt
    surgery already fought — see ``imagery/MODELS.md``.
  * a sunlit village square and a plain pair of boots are where a DARK-fantasy
    style overreaches. A ruined crypt is where it is supposed to pay off.
    Judge it on the bright rows; the grim ones will always look better.

Renders go through ``ImageStore._render`` — the real prompt build, the real
LoRA-stack selection, a fixed seed, and nothing written to the image DB.

    ./.venv/Scripts/python.exe scripts/style_lora_probe.py \
        --lora DD_Painterly_Clean.safetensors:0.45 \
        --sweep DarkFanXLGrain.safetensors --weight 0 --weight 0.2 --weight 0.35

MUST run under the WINDOWS interpreter — ComfyUI is a Windows process and WSL
cannot reach it (see CLAUDE.md -> Environment).

The printed ``diff`` column is the acceptance gate BEFORE any aesthetic call:
mean absolute pixel difference against that row's weight-0 render. **0.00 means
the LoRA did nothing** — a mismatched-architecture LoRA is a silent no-op, and
neither "it rendered" nor "the bytes changed" is evidence it fired.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The Windows console defaults to cp1252 and dies on the arrows/em-dashes in
# this module's help text (same guard as imagery/species_portraits.py).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imagery.prompt_build import build_prompt                      # noqa: E402
from imagery.species_portraits import (                            # noqa: E402
    _GEN_W, _GEN_H, _parse_lora, build_positive, species_from_db,
    species_negative,
)

OUT_ROOT = Path(__file__).resolve().parent.parent / "style-probe"

# A plain style line, standing in for what `_item_art_prompt` swaps in for
# mundane gear: the house style is ornate and jewel-toned, which is wrong for a
# pair of workman's boots, so the item row must be judged without it.
_MUNDANE_STYLE = ("plain functional gear, honest worn materials, muted natural "
                  "colours, neutral background, no gilding, no gemstones")

# (row-key, kind, subject, look, context, style-override)
# Bright rows first — they are the ones a dark style breaks.
SUBJECTS = [
    ("village-square", "place", "Greenfields village square",
     "thatched cottages, a well, market stalls, flower boxes",
     "a bright summer market morning, clear blue sky", None),
    ("boots", "item", "sturdy leather boots",
     "scuffed brown leather, hobnailed soles, plain buckles",
     "laid on a workbench", _MUNDANE_STYLE),
    ("tavern-scene", "scene", "a crowded tavern",
     "", "warm hearthlight, a bard playing, tankards raised, laughter", None),
    ("owlbear", "creature", "owlbear",
     "matted brown feathers over bear muscle, hooked beak, round yellow eyes",
     "a woodland clearing at midday", None),
    ("crypt", "place", "a ruined crypt",
     "cracked sarcophagi, collapsed pillars, creeping roots",
     "underground, guttering torchlight", None),
    # Species rows use the real portrait prompt, not build_prompt - see module doc.
    ("goliath-m", "pc", "@species:goliath:m", "", "", None),
    ("tiefling-f", "pc", "@species:tiefling:f", "", "", None),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="run",
                    help="names the output folder, e.g. 'darkfan'")
    ap.add_argument("--lora", action="append", default=[], metavar="NAME[:STRENGTH]",
                    help="the FIXED part of the stack, applied at every weight "
                         "(repeatable). Normally the installed house style.")
    ap.add_argument("--sweep", metavar="NAME",
                    help="the candidate LoRA whose strength is swept")
    ap.add_argument("--weight", action="append", type=float, default=[],
                    metavar="W", help="a strength for --sweep (repeatable). "
                                      "Include 0 for the baseline column. "
                                      "Default: 0, 0.2, 0.35, 0.5")
    ap.add_argument("--trigger", default="",
                    help="trigger word for --sweep, if it is not already in "
                         "species_portraits.LORA_TRIGGERS")
    ap.add_argument("--only", help="comma-separated row keys (default: all)")
    ap.add_argument("--seed", type=int, default=20260731,
                    help="the SAME seed across every cell and every run, so a "
                         "column differs only by the LoRA")
    a = ap.parse_args(argv)

    weights = a.weight or [0.0, 0.2, 0.35, 0.5]
    rows = SUBJECTS
    if a.only:
        want = {s.strip() for s in a.only.split(",")}
        unknown = want - {r[0] for r in SUBJECTS}
        if unknown:
            print(f"unknown row(s): {sorted(unknown)}\n"
                  f"known: {[r[0] for r in SUBJECTS]}")
            return 2
        rows = [r for r in SUBJECTS if r[0] in want]

    out = OUT_ROOT / a.tag
    out.mkdir(parents=True, exist_ok=True)

    from imagery import ImageStore
    store = ImageStore()
    cfg = store._cfg()
    base_stack = [_parse_lora(x) for x in a.lora]
    looks = dict(species_from_db())

    shown = ", ".join("{}@{}".format(l["name"], l["model"]) for l in base_stack)
    print(f"seed {a.seed} | base stack: {shown or 'none'}")
    print(f"sweeping {a.sweep or '(nothing)'} at {weights}\n")

    grid: dict[tuple[str, float], Path] = {}
    for key, kind, subject, look, context, style in rows:
        print(f"{key:<16}", end="", flush=True)
        for w in weights:
            stack = list(base_stack)
            if a.sweep and w > 0:
                entry = _parse_lora(f"{a.sweep}:{w}")
                if a.trigger:
                    entry["trigger"] = a.trigger
                stack.append(entry)
            # Force this run's stack for every kind, so no kind quietly falls
            # back to the configured one and lands in the sheet mislabelled.
            cfg.loras = stack
            cfg.loras_by_kind = {}
            store._config = cfg

            prompt, width, height = _prompt_for(cfg, kind, subject, look,
                                                context, style, looks)
            raw, _seed, offline = store._render(cfg, prompt, key, seed=a.seed,
                                                width=width, height=height)
            if offline or not raw:
                print("  OFFLINE", end="", flush=True)
                continue
            p = out / f"{key}__w{w:.2f}.png"
            p.write_bytes(raw)
            grid[(key, w)] = p
            print(f"  {w:.2f}:ok", end="", flush=True)
        print()

    if not grid:
        print("\nnothing rendered - is ComfyUI up, and are you on the Windows "
              "interpreter? (see CLAUDE.md -> Environment)")
        return 1

    _report_diffs(grid, [r[0] for r in rows], weights)
    _contact_sheet(out, grid, [r[0] for r in rows], weights)
    print(f"\nSheet: {out / '_sheet.png'}")
    return 0


def _prompt_for(cfg, kind, subject, look, context, style, looks):
    """A BuiltPrompt plus the render size for one row.

    ``@species:<slug>:<sex>`` routes through the portrait prompt builder rather
    than ``build_prompt``: the descriptor-overrun failure only shows at the real
    prompt's length, so a toy one would pass a LoRA that ruins the portraits.
    """
    if subject.startswith("@species:"):
        _, slug, sex = subject.split(":")
        look_d = looks.get(slug) or {}
        positive = build_positive(look_d, sex, cfg.style_prompt, slug=slug)
        negative = species_negative(cfg.negative_prompt, slug, sex, look=look_d)
        from imagery.prompt_build import BuiltPrompt
        return (BuiltPrompt(positive=positive, negative=negative, descriptor="",
                            descriptor_hash="", caption="", kind="pc"),
                _GEN_W, _GEN_H)
    p = build_prompt(kind, subject, look=look, context=context,
                     style_prompt=(style if style is not None else cfg.style_prompt),
                     negative_prompt=cfg.negative_prompt)
    return p, cfg.gen_width, cfg.gen_height


def _report_diffs(grid, keys, weights) -> None:
    """Mean absolute pixel difference from each row's weight-0 render.

    This is the gate that catches the silent no-op. `LoraLoader` skips keys it
    cannot match, so a wrong-architecture LoRA renders happily and even yields
    different BYTES (the encoder is not bit-stable) while changing nothing. Only
    the pixels answer. A working style LoRA moves this into double digits.
    """
    try:
        import numpy as np
        from PIL import Image
    except Exception as e:                                   # pragma: no cover
        print(f"\n(diffs skipped: {e})")
        return
    base_w = min(weights)
    print(f"\nmean |pixel| difference vs w={base_w:.2f}  (0.00 => the LoRA did nothing)")
    for key in keys:
        base = grid.get((key, base_w))
        if not base:
            continue
        a0 = np.asarray(Image.open(base).convert("RGB"), dtype=float)
        cells = []
        for w in weights:
            p = grid.get((key, w))
            if not p:
                cells.append(f"{w:.2f}: --   ")
                continue
            b = np.asarray(Image.open(p).convert("RGB"), dtype=float)
            d = float(np.abs(a0 - b).mean())
            pct = float((np.abs(a0 - b).max(axis=2) > 2).mean() * 100.0)
            cells.append(f"{w:.2f}: {d:5.2f} ({pct:4.1f}%)")
        print(f"  {key:<16} " + "  ".join(cells))


def _contact_sheet(out: Path, grid, keys, weights) -> None:
    """One grid: a row per subject, a column per strength, labelled."""
    from PIL import Image, ImageDraw
    cell, pad, hdr = 320, 4, 20
    sheet = Image.new("RGB", (len(weights) * (cell + pad),
                              hdr + len(keys) * (cell + pad + hdr)), (18, 20, 30))
    dr = ImageDraw.Draw(sheet)
    for c, w in enumerate(weights):
        dr.text((c * (cell + pad) + 4, 4), f"w = {w:.2f}", fill=(230, 200, 130))
    for r, key in enumerate(keys):
        y = hdr + r * (cell + pad + hdr)
        dr.text((4, y), key, fill=(200, 220, 250))
        for c, w in enumerate(weights):
            p = grid.get((key, w))
            if not p:
                continue
            im = Image.open(p).convert("RGB")
            # Letterbox rather than crop: the portrait rows are 3:4 and cropping
            # them to square is exactly the mistake the card sizing already fixed.
            im.thumbnail((cell, cell), Image.LANCZOS)
            sheet.paste(im, (c * (cell + pad) + (cell - im.width) // 2, y + hdr))
    sheet.save(out / "_sheet.png")


if __name__ == "__main__":
    raise SystemExit(main())
