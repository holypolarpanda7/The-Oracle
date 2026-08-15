"""Telling the painter WHAT each square is, not just where it stands.

The board's depth map says how far away everything is and how tall it stands,
and nothing else. That is why a taproom's timber posts came back as candles: a
two-foot shaft with something on top is a post, a candle or a bollard, and depth
cannot choose. A segmentation control image can — every square painted a flat
colour naming an ADE20K class, on a union ControlNet told it is being handed
`segment`.

Three things here can go wrong silently, which is the whole reason for a test:

1. **a wrong class colour** is a different class or none at all, and the picture
   would not tell you — it would just draw the wrong thing;
2. **a shaded class colour is not that class**, so the seg map must be drawn
   FLAT while every other colour picture on this board is lit;
3. **a union ControlNet left untold** which condition it has falls back to
   "auto" and returns mush that reads as a weak render, not a misconfiguration.

All of it is offline: no GPU, no model, no ComfyUI.

    uv run python scripts/segmap_smoke.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

from vtt import art, mapgen, segmap                            # noqa: E402
from vtt import skins as sk                                     # noqa: E402

OK, BAD, OFF, DIM = "\033[32m", "\033[31m", "\033[0m", "\033[2m"
_fails = 0


def check(cond: bool, what: str, detail: str = "") -> None:
    global _fails
    print(f"  {OK}OK{OFF}  {what}" if cond else f"  {BAD}FAIL{OFF}  {what}")
    if detail:
        print(f"      {DIM}{detail}{OFF}")
    if not cond:
        _fails += 1


print("\n\033[1m1. the palette is ADE20K's, not ours\033[0m")
ref_path = ROOT / "vtt" / "ade20k_palette.json"
if ref_path.exists():
    ref = json.load(open(ref_path, encoding="utf-8"))
    bad = {k: v for k, v in segmap.ADE20K.items()
           if tuple(ref.get(k, ())) != tuple(v)}
    check(not bad, "every class colour matches mmsegmentation's own table",
          f"{bad}" if bad else "extracted from source, never recalled")
else:
    print(f"  {DIM}(reference palette absent — skipped)")
check("road" not in segmap.ADE20K,
      "`road` is deliberately absent",
      "it shares (140,140,140) with `skyscraper` — the only duplicate in all "
      "150 classes, so a street painted `road` is genuinely ambiguous")
check(len(set(segmap.ADE20K.values())) == len(segmap.ADE20K),
      "and no two classes we use share a colour")

print("\n\033[1m2. every skin has an answer\033[0m")
missing = sorted(n for n in sk.SKINS if n not in segmap.SEG_BY_SKIN)
check(not missing, "the whole skin catalogue maps to a class",
      f"unmapped: {missing}" if missing else
      "a new skin without one falls back to its tile code, which is coarser")
unknown = sorted({c for c in segmap.SEG_BY_SKIN.values()
                  if c not in segmap.ADE20K}
                 | {c for c in segmap.SEG_BY_CODE.values()
                    if c not in segmap.ADE20K})
check(not unknown, "and every class named is one we hold a colour for",
      f"{unknown}")
check(segmap.seg_class("#", "setpiece:great-statue") is None,
      "a landmark's own mesh is left UNPAINTED",
      "no tile class describes somebody else's model of a specific thing")

print("\n\033[1m3. the taproom, which is why this exists\033[0m")
gen = mapgen.generate_map("tavern", width=24, height=18, seed=7)
codes = sk.skins_for(gen.archetype, style=gen.style)
squares = dict(gen.skins or {})
rows = gen.grid.to_rows()
seen = Counter(
    segmap.seg_class(rows[y][x], sk.skin_at(rows[y][x], x, y,
                                            codes=codes, squares=squares))
    for y in range(len(rows)) for x in range(len(rows[y])))
check(seen.get("column", 0) > 0, "the posts are COLUMN, not furniture",
      f"{seen.get('column', 0)} squares — the candles fix")
check(seen.get("bar", 0) > 0, "the counter is BAR, not a row of tables",
      f"{seen.get('bar', 0)} squares")
check(seen.get("fireplace", 0) > 0, "and the hearth is FIREPLACE",
      f"{seen.get('fireplace', 0)} square(s)")
check(seen.get("table", 0) > 0 and seen.get("floor", 0) > 0,
      "with tables and floor still themselves",
      f"table {seen.get('table', 0)}, floor {seen.get('floor', 0)}")

print("\n\033[1m4. drawn FLAT\033[0m")
kw = art.conditioning_kwargs(
    gen, skin_of=lambda c, x, z: sk.skin_at(c, x, z, codes=codes,
                                            squares=squares))
png = segmap.seg_image(**kw)
check(bool(png), "the seg map rasterizes")
from io import BytesIO                                          # noqa: E402
from PIL import Image                                           # noqa: E402
im = Image.open(BytesIO(png)).convert("RGB")
allowed = set(segmap.ADE20K.values()) | {(0, 0, 0)}
found = {c for _n, c in im.getcolors(maxcolors=1 << 20)}
stray = sorted(found - allowed)
check(not stray,
      "and EVERY pixel is an exact class colour or black",
      f"{len(found)} distinct colours, {len(stray)} stray: {stray[:4]}"
      if stray else
      f"{len(found)} distinct colours — no shading, because a lit class "
      f"colour is a different class")

depth = __import__("vtt.isocam", fromlist=["x"]).depth_image(**kw)
check(Image.open(BytesIO(depth)).size == im.size,
      "pixel-aligned with the depth map — same rasterizer, same camera",
      f"{im.size}")

print("\n\033[1m5. the graph the client builds\033[0m")
from imagery.comfy_client import ComfyClient                    # noqa: E402
c = ComfyClient(base_url="http://127.0.0.1:8188",
                controlnet="union.safetensors", controlnet_strength=0.55)
c.controlnet_union_type = "depth"
c._control_image_name = "depth.png"
c._extra_controls = [{"name": "union.safetensors", "image": "seg.png",
                      "union_type": "segment", "strength": 0.4}]
g = c._build_graph("a taproom", "blurry", 1024, 768, 1234, 30)
ks = next(n for n, v in g.items() if v["class_type"] == "KSampler")
types = {g[n]["inputs"]["type"] for n in g
         if g[n]["class_type"] == "SetUnionControlNetType"}
applies = [n for n in g if g[n]["class_type"] == "ControlNetApplyAdvanced"]
check(types == {"depth", "segment"},
      "both nets are TOLD which condition they hold", f"{sorted(types)}")
check(len(applies) == 2, "two links in the chain", f"{sorted(applies)}")
second = max(applies, key=lambda n: int(n))
first = min(applies, key=lambda n: int(n))
check(g[second]["inputs"]["positive"] == [first, 0]
      and g[second]["inputs"]["negative"] == [first, 1],
      "chained, and the NEGATIVE is carried through both",
      "the simpler ControlNetApply drops it silently")
check(g[ks]["inputs"]["positive"] == [second, 0],
      "and the sampler reads the end of the chain")

c2 = ComfyClient(base_url="x", controlnet="depth-only.safetensors")
c2._control_image_name = "depth.png"
g2 = c2._build_graph("x", "y", 512, 512, 1, 20)
check(not any(v["class_type"] == "SetUnionControlNetType" for v in g2.values())
      and sum(1 for v in g2.values()
              if v["class_type"] == "ControlNetApplyAdvanced") == 1,
      "a single-purpose net still builds exactly the graph it always did")
g3 = ComfyClient(base_url="x")._build_graph("x", "y", 512, 512, 1, 20)
check(not any(v["class_type"].startswith(("ControlNet", "SetUnion"))
              for v in g3.values()),
      "and an unconditioned render is untouched")

print()
if _fails:
    print(f"{BAD}{_fails} FAILED{OFF}")
    sys.exit(1)
print(f"{OK}the board can say what a square IS, and the painter is told{OFF}")
