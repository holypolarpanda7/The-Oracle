"""Sweep the SWATCH STYLE clause across the substances it has to survive.

The board's whole palette comes from ~123 swatches, and the one line that
decides how they look is :data:`vtt.art._MATERIAL_STYLE`. Changing it is a
prompt edit; SEEING the change was, until this, either a full 123-swatch batch
or ``--redraw``, which deletes the stored swatch before drawing the new one —
fine once you have decided, useless while you are deciding, because a worse
result has already replaced the better one.

So this renders into its own ``probe-*`` slugs and touches the catalogue not at
all. Each COLUMN is a complete candidate configuration — the style clause AND the
LoRA stack, since a swatch's colour is decided by both. Each ROW is a substance
chosen because it fails differently:

  * a pale masonry, which is where an over-saturated clause invents a hue —
    the failure the granite prompt took three attempts to stop making;
  * a WOOD, because two woods described as one wood come back the same colour;
  * open GROUND, the thing most of an outdoor board is made of;
  * FOLIAGE, the one that reads as a lawn when it should read as a canopy;
  * a METAL, the only substance with a specular story to tell.

MUST run under the WINDOWS interpreter — ComfyUI is a Windows process and WSL
cannot reach it (see CLAUDE.md -> Environment):

    DATABASE_URL="sqlite:///D:/Projects/The Oracle/oracle-dm-backend/oracle.db" \\
      WSLENV=DATABASE_URL ./.venv/Scripts/python.exe scripts/material_style_probe.py

The printed columns are the acceptance gate BEFORE any aesthetic call:

  chroma  mean saturation, 0-100. A swatch is the paint the whole board is
          tinted with, so this is the number the north star is actually about.
  var     how much HUE varies across the frame. High is not a virtue: a
          material is one substance, and a swatch that wanders in hue is a
          picture of a place (see MATERIAL_NEGATIVE).
  detail  high-frequency energy against low. `--surface` in the prerenderer
          uses the same measure — a swatch is a SURFACE, and a composition
          scores low here because its structure is big.
  dist    mean absolute pixel difference against that row's first column.
          0.00 means the clause changed NOTHING.
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: Substances that fail in different directions. (code, skin, label).
ROWS: list[tuple[str, str, str]] = [
    ("#", "", "masonry"),
    (".", "taproom-floor", "wood"),
    (".", "", "ground"),
    ("T", "", "foliage"),
    ("O", "field-stone", "stone"),
    ("#", "plating", "metal"),
]

#: The clause every LoRA column shares, so those columns differ in ONE thing.
UNMUTED = (
    "seen from directly overhead looking straight down, flat-on surface, "
    "one single material filling the entire frame edge to edge, "
    "hand-painted texture, rich painterly surface detail, "
    "richly saturated colour, even flat lighting"
)

#: A COLUMN is a whole candidate configuration: the style clause AND the LoRA
#: stack, because a swatch's colour is decided by both and arguing about one
#: while the other is fixed is how you spend a day on a prompt that was never
#: the problem. ``loras`` of None means the house stack this installation
#: ships; an empty list means none at all.
COLUMNS: dict[str, dict] = {
    # What ships today. Column 0, so every other column is measured against it.
    "current": {"style": (
        "seen from directly overhead looking straight down, flat-on surface, "
        "one single material filling the entire frame edge to edge, "
        "hand-painted texture, rich painterly surface detail, honest materials, "
        "muted natural palette, even flat lighting")},
    # The minimal edit: the two anti-chroma words out, nothing else touched.
    # The granite lesson — keep the sentence that is already producing a
    # SURFACE and change only what you must.
    "unmuted": {"style": (
        "seen from directly overhead looking straight down, flat-on surface, "
        "one single material filling the entire frame edge to edge, "
        "hand-painted texture, rich painterly surface detail, "
        "richly saturated colour, even flat lighting")},
    # The house direction said out loud, minus everything about LIGHT — a
    # swatch may not carry a light source, so "dramatic rim lighting" is
    # exactly the half that cannot come across.
    "jewel": {"style": (
        "seen from directly overhead looking straight down, flat-on surface, "
        "one single material filling the entire frame edge to edge, "
        "hand-painted texture, visible brushwork, rich painterly surface "
        "detail, deep saturated jewel tones, even flat lighting")},
    # The same clause with NO house style in the weights. This is the control,
    # and it exists because the first sweep came back teal in every column and
    # got MORE teal the harder the prompt asked for colour — which is what a
    # style LoRA overriding a named hue looks like from the outside. If this
    # column is the only one that obeys "warm sandy grey", the argument was
    # never about wording.
    "nostyle": {"style": (
        "seen from directly overhead looking straight down, flat-on surface, "
        "one single material filling the entire frame edge to edge, "
        "hand-painted texture, rich painterly surface detail, "
        "richly saturated colour, even flat lighting"),
        "loras": []},
    # WHICH of the three carries the cast — and the answer, measured against
    # the whole rendered catalogue afterwards, was NONE OF THEM for five of the
    # six rows. `nostyle` reads as a vindication on a contact sheet and is not
    # one: masonry at zero LoRAs is 71% teal and stone is 98%, both WORSE than
    # the shipped stack, because their subject prompts named a blue or named
    # nothing at all. Only FOLIAGE answers to the stack (`no-hades` takes it to
    # 4% teal and a real green), and even that is a single noisy sample — the
    # same substance across its thirteen catalogue looks measures 7%.
    #
    # Kept as a record of a hypothesis that a probe can support and a
    # measurement can refute. The cast was in the prompts; see the palette rule
    # in docs/design/vtt-board-appearance.md and `--palette` in
    # scripts/material_prerender.py, which is what settles this question now.
    "no-hades": {"style": UNMUTED, "loras": "drop:Hades_Art_Style"},
    "no-grain": {"style": UNMUTED, "loras": "drop:DarkFanXLGrain"},
    "no-paint": {"style": UNMUTED, "loras": "drop:DD_Painterly_Clean"},
    "hades-lo": {"style": UNMUTED, "loras": "scale:Hades_Art_Style:0.3"},
    # Half the house style. If the cast is the LoRA, this is where the named
    # colour starts coming back without losing the hand it is drawn with.
    "half": {"style": (
        "seen from directly overhead looking straight down, flat-on surface, "
        "one single material filling the entire frame edge to edge, "
        "hand-painted texture, rich painterly surface detail, "
        "richly saturated colour, even flat lighting"),
        "loras": "half"},
}


def measure(png: bytes) -> tuple[float, float, float]:
    from PIL import Image
    import numpy as np
    im = Image.open(io.BytesIO(png)).convert("RGB")
    a = np.asarray(im, dtype=float) / 255.0
    mx, mn = a.max(2), a.min(2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    hsv = np.asarray(im.convert("HSV"), dtype=float)[:, :, 0]
    # Hue is circular; the spread is taken on the unit circle or 359 and 1
    # read as opposite ends of the palette.
    ang = hsv / 255.0 * 2 * np.pi
    r = np.hypot(np.cos(ang).mean(), np.sin(ang).mean())
    small = np.asarray(im.convert("L").resize((16, 16), Image.BOX), dtype=float)
    full = np.asarray(im.convert("L"), dtype=float)
    blur = np.asarray(im.convert("L").resize((16, 16), Image.BOX)
                        .resize(im.size, Image.BILINEAR), dtype=float)
    detail = float(np.abs(full - blur).mean()) / max(float(small.std()), 1.0)
    return float(sat.mean() * 100), float((1 - r) * 100), detail


HOUSE: list[dict] = []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--styles", default="",
                    help="comma-separated column names (default: all)")
    ap.add_argument("--rows", default="", help="comma-separated row labels")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--px", type=int, default=512)
    ap.add_argument("--out", default="style-probe/review/material-style.png")
    a = ap.parse_args(argv)

    from imagery import ImageStore
    from imagery.models import ImageKind
    from vtt import art

    want_s = [s.strip() for s in a.styles.split(",") if s.strip()] or list(COLUMNS)
    want_r = [s.strip() for s in a.rows.split(",") if s.strip()]
    rows = [r for r in ROWS if not want_r or r[2] in want_r]
    store = ImageStore()
    # The stack as shipped, captured before any column overrides it.
    global HOUSE
    HOUSE = [dict(l) for l in (getattr(store._cfg(), "loras", None) or [])]
    print("house stack: "
          + (", ".join(f"{l['name'].split('.')[0]}@{l['model']}" for l in HOUSE)
             or "(none)"))

    from PIL import Image
    cells: dict[tuple[str, str], bytes] = {}
    print(f"{'row':10} {'style':9} {'chroma':>7} {'var':>6} {'detail':>7} {'dist':>6}")
    for code, skin, label in rows:
        subject = art.material_subject(code, skin)
        if not subject:
            print(f"{label:10} — no subject for {code!r}/{skin!r}")
            continue
        base = None
        for name in want_s:
            col = COLUMNS[name]
            # The stack is forced per column, for the MATERIAL kind and for the
            # house default, so nothing quietly falls back to the other.
            cfg = store._cfg()
            want = col.get("loras")
            if want is None:
                stack = list(HOUSE)
            elif isinstance(want, str) and want.startswith("drop:"):
                stack = [l for l in HOUSE if not l["name"].startswith(want[5:])]
            elif isinstance(want, str) and want.startswith("scale:"):
                _, which, dose = want.split(":")
                stack = [dict(l, model=float(dose), clip=float(dose))
                         if l["name"].startswith(which) else dict(l)
                         for l in HOUSE]
            elif want == "half":
                stack = [dict(l, model=l["model"] * 0.5,
                              clip=l.get("clip", l["model"]) * 0.5)
                         for l in HOUSE]
            else:
                stack = list(want)
            cfg.loras = stack
            cfg.loras_by_kind = dict(getattr(cfg, "loras_by_kind", None) or {},
                                     material=stack)
            store._config = cfg
            res = store.ensure_image(
                ImageKind.MATERIAL, subject, look="",
                context=art.material_look(code, skin) or "dungeon",
                # Its OWN slug. The catalogue is not touched, so a clause that
                # turns out worse costs nothing but the GPU time.
                ref_slug=f"probe-mstyle-{label}-{name}",
                extra=col["style"],
                negative_extra=art.MATERIAL_NEGATIVE,
                width=a.px, height=a.px, store_width=a.px,
                seed=a.seed, max_per_bucket=1)
            if res is None or res.offline or not res.image_id:
                print(f"{label:10} {name:9} — no render (is ComfyUI up?)")
                continue
            png = store.get_image_bytes(res.image_id)
            if not png:
                continue
            cells[(label, name)] = png
            chroma, var, detail = measure(png)
            import numpy as np
            cur = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), float)
            if base is None:
                base, dist = cur, 0.0
            else:
                dist = float(np.abs(cur - base).mean())
            print(f"{label:10} {name:9} {chroma:7.1f} {var:6.1f} {detail:7.2f} "
                  f"{dist:6.2f}")

    if not cells:
        print("nothing rendered")
        return 1
    cw = 256
    sheet = Image.new("RGB", (cw * len(want_s), cw * len(rows)), (18, 18, 24))
    for r, (_c, _s, label) in enumerate(rows):
        for c, name in enumerate(want_s):
            png = cells.get((label, name))
            if not png:
                continue
            im = Image.open(io.BytesIO(png)).convert("RGB").resize((cw, cw))
            sheet.paste(im, (c * cw, r * cw))
    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    print(f"\ncolumns: {' | '.join(want_s)}")
    print(f"rows:    {' | '.join(r[2] for r in rows)}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
