"""Compare whole LoRA MIXES across the four kinds a style decision touches.

The other three probes each sweep ONE LoRA's strength inside one job:
``style_lora_probe`` the house style, ``map_lora_probe`` the battlemap,
``worldmap_lora_probe`` the parchment wash. That is the right shape for
"how hard should this dial be turned". It is the wrong shape for the question
this one answers: **which whole stack do we ship**.

A house style is not chosen per kind. The same art direction has to survive an
ornate magic item, a species portrait, a top-down battlemap and a drafted
region map at once, and those four run DIFFERENT stacks by design
(``loras_by_kind`` — a map's job is the opposite of a portrait's). So a mix is
only comparable against another mix if all four are re-rendered together from
one seed. Each column here is a complete, shippable configuration; each row is
a kind rendered through its REAL path — the item and portrait prompts the game
builds, ``render_battlemap`` with its ControlNet floorplan, and the parchment
wash under a real terrain survey.

    ./.venv/Scripts/python.exe scripts/hades_style_probe.py
    ./.venv/Scripts/python.exe scripts/hades_style_probe.py --mix baseline,layer-mid
    ./.venv/Scripts/python.exe scripts/hades_style_probe.py --row portrait,item

MUST run under the WINDOWS interpreter — ComfyUI is a Windows process and WSL
cannot reach it (see CLAUDE.md -> Environment).

The printed diff column is the acceptance gate BEFORE any aesthetic call: mean
absolute pixel difference against that row's `baseline` cell. **0.00 means the
mix changed nothing** — a mismatched LoRA is a silent no-op that still renders
happily and still writes different bytes, and this project has shipped one
before. Only the pixels answer.

What to look for, once the diffs prove it fired:
  * item / portrait — does the Hades read (hard rim light, saturated key,
    graphic ink edges) arrive WITHOUT eating the descriptors? A goliath that
    comes back as a tattooed human is the failure four rounds of species-prompt
    surgery already fought.
  * battlemap — still dead-flat overhead, no isometric drift, no figures, no
    drawn grid. Hades level art is three-quarter perspective with characters in
    frame; that is exactly the drift to watch for here.
  * region map — drawn country, not photographed ground, and NO WRITING. Every
    label is inked afterwards from real coordinates.
"""
from __future__ import annotations

import argparse
import io
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

from imagery.prompt_build import BuiltPrompt, build_prompt          # noqa: E402
from imagery.species_portraits import (                             # noqa: E402
    _GEN_W, _GEN_H, build_positive, species_from_db, species_negative,
)

OUT_ROOT = Path(__file__).resolve().parent.parent / "style-probe"


def L(name: str, strength: float, trigger: str = "") -> dict:
    """One LoRA entry. `trigger` is the caption tag baked into its training.

    Carried per-entry rather than in the house style string because it belongs
    to the FILE: swap the LoRA and the tag has to go with it (the same reason
    `imagery.comfy_client._apply_lora_triggers` reads it off the stack).
    """
    e = {"name": name, "model": strength, "clip": strength}
    if trigger:
        e["trigger"] = trigger
    return e


# The installed files, with the trigger word read off each one's own
# ss_tag_frequency metadata — never guessed.
def PAINTERLY(s): return L("DD_Painterly_Clean.safetensors", s, "d&d painterly")
def DARKFAN(s):   return L("DarkFanXLGrain.safetensors", s, "dark fantasy art style")
def BATTLE(s):    return L("SDXL-Battlemaps.safetensors", s, "battlemap")
# sxz-wowmap was trained caption-free (empty ss_tag_frequency), so it has NO
# trigger and strength is the only dial; adding one would push a meaningless
# token into the prompt.
def WOWMAP(s):    return L("sxz-wowmap-civit-sdxl.safetensors", s)
# Hades_Art_Style's 210 captions are whole SENTENCES ("character shaded with
# neon colors"), not a repeated tag — there is no trigger word to fire, so it
# behaves like wowmap: strength is the dial.
def HADES(s):     return L("Hades_Art_Style.safetensors", s)
# HadesLevel DOES have one: `hadeslevel` on 99 of its 594 images. It is also
# rank 4 against Hades_Art_Style's 32, so it grips far more softly and wants a
# higher number to say the same thing.
def HLEVEL(s):    return L("HadesLevel.safetensors", s, "hadeslevel")


#: Each entry is a COMPLETE configuration: what `loras` would be, plus what the
#: two map kinds override it with. Nothing is implied or inherited — a column
#: is what would go into game_settings.json verbatim.
MIXES: dict[str, dict] = {
    # What ships today, and the only column the diffs are measured against.
    "baseline": {
        "house": [PAINTERLY(0.45), DARKFAN(0.35)],
        "map": [BATTLE(0.5)],
        "worldmap": [WOWMAP(0.5)],
    },
    # Layered ON TOP of the live stack, the way DarkFanXLGrain was added: the
    # house LoRA still carries the look, Hades only tints it. Three rungs,
    # because the honest answer to "how much" is usually a number, not a yes.
    "layer-lo": {
        "house": [PAINTERLY(0.45), DARKFAN(0.35), HADES(0.25)],
        "map": [BATTLE(0.5), HLEVEL(0.3)],
        "worldmap": [WOWMAP(0.5), HLEVEL(0.3)],
    },
    "layer-mid": {
        "house": [PAINTERLY(0.45), DARKFAN(0.35), HADES(0.45)],
        "map": [BATTLE(0.5), HLEVEL(0.5)],
        "worldmap": [WOWMAP(0.5), HLEVEL(0.5)],
    },
    "layer-hi": {
        "house": [PAINTERLY(0.45), DARKFAN(0.35), HADES(0.7)],
        "map": [BATTLE(0.5), HLEVEL(0.75)],
        "worldmap": [WOWMAP(0.5), HLEVEL(0.75)],
    },
    # Hades LEADS and the grain LoRA is dropped. DarkFanXLGrain exists to add
    # grit to a clean painterly base; Hades brings its own colour and contrast,
    # and two style LoRAs fighting over the same job is how a stack turns muddy.
    "hades-fwd": {
        "house": [PAINTERLY(0.35), HADES(0.6)],
        "map": [BATTLE(0.4), HLEVEL(0.6)],
        "worldmap": [WOWMAP(0.4), HLEVEL(0.6)],
    },
    # The other way to put Hades on the maps: the GENERAL art LoRA rather than
    # the level one. Level art is three-quarter perspective with characters in
    # frame, which is the wrong thing to teach a top-down board — this column
    # asks whether the pure style LoRA carries the look with less of that risk.
    "hades-art-maps": {
        "house": [PAINTERLY(0.45), DARKFAN(0.35), HADES(0.45)],
        "map": [BATTLE(0.5), HADES(0.35)],
        "worldmap": [WOWMAP(0.5), HADES(0.35)],
    },
    # The far end, kept in on purpose: nobody can judge how far is too far
    # without seeing past it. Expect the descriptors to start losing.
    "hades-pure": {
        "house": [HADES(0.85)],
        "map": [HLEVEL(0.8), BATTLE(0.25)],
        "worldmap": [HLEVEL(0.8)],
    },

    # ----- map-side columns (run with --row battlemap,regionmap) -----
    #
    # The first pass answered the house-style question and left the map one
    # open: at the strengths above, SDXL-Battlemaps and sxz-wowmap simply
    # out-shout the Hades LoRAs, and the maps moved least of all four rows.
    # That is not "Hades does nothing to a map", it is "nobody turned it up".
    # These columns turn it up, and they trade AGAINST the format LoRA rather
    # than stacking on top of it, because the two are competing for the same
    # job. The format LoRA is what holds dead-flat overhead — the ONE property
    # a board cannot lose — so each rung gives up a little of it deliberately
    # and the sheet shows what that costs. Their `house` is layer-mid's, so a
    # stray house row lands somewhere sensible rather than in a stack nobody
    # proposed.
    "map-art-50": {
        "house": [PAINTERLY(0.45), DARKFAN(0.35), HADES(0.45)],
        "map": [BATTLE(0.5), HADES(0.5)],
        "worldmap": [WOWMAP(0.5), HADES(0.5)],
    },
    "map-art-70": {
        "house": [PAINTERLY(0.45), DARKFAN(0.35), HADES(0.45)],
        "map": [BATTLE(0.45), HADES(0.7)],
        "worldmap": [WOWMAP(0.45), HADES(0.7)],
    },
    # HadesLevel is rank 4 against Hades_Art_Style's 32 — it grips far more
    # softly, so 0.9 here is not the same ask as 0.9 there.
    "map-lvl-hi": {
        "house": [PAINTERLY(0.45), DARKFAN(0.35), HADES(0.45)],
        "map": [BATTLE(0.4), HLEVEL(0.9)],
        "worldmap": [WOWMAP(0.4), HLEVEL(0.9)],
    },
    # Both Hades LoRAs at once: the style one for the palette and ink, the
    # level one for the subject matter it was actually trained on.
    "map-both": {
        "house": [PAINTERLY(0.45), DARKFAN(0.35), HADES(0.45)],
        "map": [BATTLE(0.45), HADES(0.45), HLEVEL(0.5)],
        "worldmap": [WOWMAP(0.45), HADES(0.45), HLEVEL(0.5)],
    },
}

#: A plain style line, standing in for what `_item_art_prompt` swaps in for
#: mundane gear — kept here as the note it is: the ORNATE row is the one that
#: shows a house style, so that is the row this probe renders.
_ORNATE_ITEM = ("an ornate longsword, dawn-forged steel with a fullered blade, "
                "gold filigree along the ricasso, a sunstone set in the pommel")

ROWS = ("item", "portrait", "battlemap", "regionmap")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="hades",
                    help="names the output folder under style-probe/")
    ap.add_argument("--mix", help="comma-separated mix names (default: all)")
    ap.add_argument("--row", help=f"comma-separated rows (default: all of {list(ROWS)})")
    ap.add_argument("--seed", type=int, default=20260805,
                    help="the SAME seed in every cell, so a column differs "
                         "only by its LoRA stack")
    ap.add_argument("--species", default="goliath",
                    help="portrait row's species — goliath is the descriptor "
                         "stress case (slate blue-grey skin is what a strong "
                         "style LoRA eats first)")
    ap.add_argument("--sheet-only", action="store_true",
                    help="rebuild the sheets from the PNGs already in --tag, "
                         "rendering nothing. For changing how the sheet is "
                         "LABELLED without paying for the pixels again.")
    a = ap.parse_args(argv)

    mixes = _pick(a.mix, list(MIXES), "mix")
    rows = _pick(a.row, list(ROWS), "row")
    if mixes is None or rows is None:
        return 2
    if "baseline" not in mixes:
        print("note: 'baseline' is not in --mix, so the diff column has "
              "nothing to measure against.\n")

    out = OUT_ROOT / a.tag
    out.mkdir(parents=True, exist_ok=True)

    if a.sheet_only:
        grid = {(r, m): out / f"{r}__{m}.png"
                for r in rows for m in mixes if (out / f"{r}__{m}.png").is_file()}
        if not grid:
            print(f"no renders found in {out} - run without --sheet-only first.")
            return 1
        rows = [r for r in rows if any((r, m) in grid for m in mixes)]
        mixes = [m for m in mixes if any((r, m) in grid for r in rows)]
        _finish(out, grid, rows, mixes)
        return 0

    from imagery import ImageStore
    store = ImageStore()
    cfg = store._cfg()
    store._config = cfg
    looks = dict(species_from_db())

    print(f"seed {a.seed} | {len(rows)} row(s) x {len(mixes)} mix(es) "
          f"= {len(rows) * len(mixes)} renders -> {out}\n")

    grid: dict[tuple[str, str], Path] = {}
    # Mix on the OUTSIDE: a column shares its three stacks across all four
    # rows, so looping this way reloads the LoRAs a few times instead of once
    # per cell. On a 16 GB rig that is the difference worth having.
    for mix in mixes:
        stacks = MIXES[mix]
        print(f"--- {mix}")
        for name, stack in stacks.items():
            print(f"      {name:<9} " +
                  ", ".join(f"{l['name'].split('.')[0]}@{l['model']}" for l in stack))
        for row in rows:
            print(f"  {row:<11}", end="", flush=True)
            try:
                raw = _render_row(store, cfg, stacks, row, a, looks)
            except Exception as e:
                print(f"  FAILED: {e}")
                continue
            if not raw:
                print("  OFFLINE / no bytes")
                continue
            p = out / f"{row}__{mix}.png"
            p.write_bytes(raw)
            grid[(row, mix)] = p
            print(f"  ok ({len(raw)//1024} KB)")
        print()

    if not grid:
        print("nothing rendered - is ComfyUI up, and are you on the Windows "
              "interpreter? (see CLAUDE.md -> Environment)")
        return 1

    _finish(out, grid, rows, mixes)
    return 0


def _finish(out: Path, grid, rows, mixes) -> None:
    """The diff table and the sheets — everything after the pixels exist."""
    _report_diffs(grid, rows, mixes)
    _contact_sheet(out, grid, rows, mixes)
    for row in rows:
        _contact_sheet(out, grid, [row], mixes, name=f"_sheet_{row}.png", cell=460)
    print(f"\nSheet: {out / '_sheet.png'}  (+ one per row, larger)")


def _pick(arg: str | None, known: list[str], what: str) -> list[str] | None:
    if not arg:
        return known
    want = [s.strip() for s in arg.split(",") if s.strip()]
    unknown = [w for w in want if w not in known]
    if unknown:
        print(f"unknown {what}(s): {unknown}\nknown: {known}")
        return None
    return want


def _render_row(store, cfg, stacks: dict, row: str, a, looks) -> bytes | None:
    """One cell, through the kind's REAL render path.

    Every row forces BOTH `loras` and `loras_by_kind` from this column's
    stacks, so no kind can quietly fall back to the configured one and land in
    the sheet mislabelled.
    """
    cfg.loras = list(stacks["house"])
    cfg.loras_by_kind = {"map": list(stacks["map"]),
                         "worldmap": list(stacks["worldmap"])}

    if row == "battlemap":
        # The real path: a generated layout, its floorplan control image and
        # the ControlNet, because a battlemap's whole problem is that the
        # picture must depict THIS room. force_new bypasses the art cache.
        from vtt.mapgen import generate_map
        from vtt.art import render_battlemap
        gen = generate_map("dungeon-room", width=20, height=20,
                           seed=a.seed, lighting="dim")
        # `render_battlemap` does NOT read the ControlNet out of config — the
        # live caller (`vtt/scene.py`) passes it, so a probe that leaves it off
        # is judging a picture of SOME room, which is the one thing the
        # conditioning exists to prevent.
        art = render_battlemap(gen, store=store, biome="underground",
                               lighting="dim", force_new=True,
                               controlnet=(getattr(cfg, "map_controlnet", "") or None),
                               controlnet_strength=float(
                                   getattr(cfg, "map_controlnet_strength", 0.8)))
        if art.offline or not art.image_id:
            return None
        return store.get_image_bytes(art.image_id)

    if row == "regionmap":
        # The terrain wash only — the dots, names, routes, compass and scale
        # bar are inked over it afterwards from real coordinates, so what a
        # LoRA is judged on is exactly this layer.
        from eight_card_system.mapmaker import _MAP_STYLE, TerrainSurvey
        sectors = {"northwest": "farmland", "north": "farmland",
                   "northeast": "forest", "west": "hills", "centre": "river",
                   "east": "forest", "southwest": "hills", "south": "river",
                   "southeast": "coast"}
        from collections import Counter
        counts = Counter(sectors.values())
        total = float(sum(counts.values())) or 1.0
        survey = TerrainSurvey(sectors=sectors,
                               shares={b: n / total for b, n in counts.items()},
                               climate="temperate", signature="probe")
        p = build_prompt("worldmap", "the lands of the Greenfields march",
                         look=survey.prompt_look(), context=survey.climate,
                         style_prompt=_MAP_STYLE,
                         negative_prompt=cfg.negative_prompt)
        raw, _s, offline = store._render(cfg, p, "probe", seed=a.seed,
                                         width=768, height=768)
        return None if offline else raw

    if row == "portrait":
        # The real 166-word species prompt, not a toy one: the descriptor
        # overrun only shows at that length, so a short prompt would pass a
        # LoRA that ruins every portrait in the game.
        look = looks.get(a.species) or {}
        p = BuiltPrompt(
            positive=build_positive(look, "m", cfg.style_prompt, slug=a.species),
            negative=species_negative(cfg.negative_prompt, a.species, "m", look=look),
            descriptor="", descriptor_hash="", caption="", kind="pc")
        raw, _s, offline = store._render(cfg, p, "probe", seed=a.seed,
                                         width=_GEN_W, height=_GEN_H)
        return None if offline else raw

    p = build_prompt("item", _ORNATE_ITEM,
                     look="polished steel, warm gold, a lit sunstone",
                     context="displayed on dark cloth",
                     style_prompt=cfg.style_prompt,
                     negative_prompt=cfg.negative_prompt)
    raw, _s, offline = store._render(cfg, p, "probe", seed=a.seed,
                                     width=cfg.gen_width, height=cfg.gen_height)
    return None if offline else raw


def _report_diffs(grid, rows, mixes) -> None:
    """Mean absolute pixel difference from each row's `baseline` cell.

    The gate that catches the silent no-op: `LoraLoader` skips keys it cannot
    match, so a wrong-architecture LoRA renders happily and even yields
    different BYTES (the encoder is not bit-stable) while changing nothing.
    Only the pixels answer.
    """
    try:
        import numpy as np
        from PIL import Image
    except Exception as e:                                   # pragma: no cover
        print(f"(diffs skipped: {e})")
        return
    base_mix = "baseline" if "baseline" in mixes else mixes[0]
    print(f"mean |pixel| diff vs '{base_mix}'   "
          f"(0.00 => that mix changed NOTHING; %% = share of pixels moved)")
    print("  " + " " * 12 + "".join(f"{m:>16}" for m in mixes))
    for row in rows:
        base = grid.get((row, base_mix))
        if not base:
            continue
        a0 = np.asarray(Image.open(base).convert("RGB"), dtype=float)
        cells = []
        for m in mixes:
            p = grid.get((row, m))
            if not p:
                cells.append(f"{'--':>16}")
                continue
            b = np.asarray(Image.open(p).convert("RGB"), dtype=float)
            if b.shape != a0.shape:
                cells.append(f"{'(size)':>16}")
                continue
            d = float(np.abs(a0 - b).mean())
            pct = float((np.abs(a0 - b).max(axis=2) > 2).mean() * 100.0)
            cells.append(f"{d:>9.2f}({pct:3.0f}%)")
        print(f"  {row:<12}" + "".join(cells))
    print()


def _font(size: int):
    from PIL import ImageFont
    for p in (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


#: Which of a mix's three stacks a given row actually renders under. A sheet
#: that only prints the mix NAME is unreadable for exactly this reason: the
#: name says "layer-mid", the cell says nothing about whether the house LoRAs
#: are still underneath, and the answer is different per row.
_ROW_STACK = {"item": "house", "portrait": "house",
              "battlemap": "map", "regionmap": "worldmap"}

#: Short names for the sheet. The value is (label, is_new) — the new LoRAs are
#: drawn in a different colour so "what got layered on" is readable at a glance
#: instead of being decoded from a filename.
_SHORT = {
    "DD_Painterly_Clean": ("painterly", False),
    "DarkFanXLGrain": ("darkfan", False),
    "SDXL-Battlemaps": ("battlemaps", False),
    "sxz-wowmap-civit-sdxl": ("wowmap", False),
    "Hades_Art_Style": ("HadesArt", True),
    "HadesLevel": ("HadesLevel", True),
}


def _stack_caption(mix: str, row: str) -> list[tuple[str, bool]]:
    """The stack this cell really ran, as [(label, is_new), ...]."""
    stack = MIXES[mix].get(_ROW_STACK.get(row, "house"), [])
    out = []
    for l in stack:
        stem = l["name"].rsplit(".", 1)[0]
        label, is_new = _SHORT.get(stem, (stem[:14], False))
        out.append((f"{label} {l['model']:g}", is_new))
    return out


def _contact_sheet(out: Path, grid, rows, mixes, name="_sheet.png",
                   cell: int = 340) -> None:
    """One grid: a row per kind, a column per MIX, every cell captioned.

    The caption is the whole point. A mix name is a label for a decision, not
    a description of one, and the first thing anyone asks of this sheet is
    "are the other LoRAs still in there" — which the name cannot answer,
    because each row runs a DIFFERENT one of the mix's three stacks. So each
    cell prints the stack it actually ran, new LoRAs picked out in gold.

    Letterboxed rather than cropped — the portrait row is 3:4 and cropping it
    square is exactly the mistake the CC card sizing already fixed.
    """
    from PIL import Image, ImageDraw
    pad, hdr, lab = 6, 30, 24
    f_hdr, f_lab, f_cap = _font(19), _font(17), _font(14)
    line = 17
    cap_h = line * max((len(_stack_caption(m, r)) for r in rows for m in mixes),
                       default=1) + 6
    row_h = cell + pad + lab + cap_h
    sheet = Image.new("RGB", (len(mixes) * (cell + pad) + pad,
                              hdr + len(rows) * row_h + pad), (18, 20, 30))
    dr = ImageDraw.Draw(sheet)
    for c, m in enumerate(mixes):
        dr.text((pad + c * (cell + pad) + 4, 6), m, fill=(230, 200, 130), font=f_hdr)
    for r, row in enumerate(rows):
        y = hdr + r * row_h
        dr.text((pad + 4, y), row, fill=(200, 220, 250), font=f_lab)
        for c, m in enumerate(mixes):
            x = pad + c * (cell + pad)
            p = grid.get((row, m))
            if p:
                im = Image.open(p).convert("RGB")
                im.thumbnail((cell, cell), Image.LANCZOS)
                sheet.paste(im, (x + (cell - im.width) // 2, y + lab))
            cy = y + lab + cell + 4
            for i, (text, is_new) in enumerate(_stack_caption(m, row)):
                dr.text((x + 4, cy + i * line), ("+ " if is_new else "  ") + text,
                        fill=((245, 205, 100) if is_new else (140, 150, 170)),
                        font=f_cap)
    sheet.save(out / name)


if __name__ == "__main__":
    raise SystemExit(main())
