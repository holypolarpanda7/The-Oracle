"""What a surface DOES to light, as opposed to what colour it is.

Every swatch on every board has been albedo and nothing else: one render of
"dressed limestone" tiled over each square, lit by a lamp and an ambient fill.
That is a picture of stone laid flat on a shape — every face returns exactly the
same light for its orientation, so mortar courses, grain and pitting are painted
ON the surface instead of being surface, and the geometry reads as coloured
cardboard however good the swatch is.

Two halves, and they come from different places, which is the thing this test
exists to hold:

* The RELIEF is already in the picture and is recovered from it. The classic
  way that goes wrong is that an albedo render contains the lighting it was
  made under, so luminance-as-height bakes somebody else's sun into the
  geometry. The high-pass is the fix and it is MEASURABLE — the check below
  fails outright with the naive method.
* The SHINE is not in the picture at all. Contrast tells you nothing about
  whether stone is wet, so roughness and metalness are declared per SUBSTANCE,
  which is exactly what a skin already is.

Offline: numpy and Pillow, a scratch copy of the database, no GPU and no LLM.

    uv run python scripts/surface_smoke.py
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
_fails: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {GREEN}✓{OFF} {label}" if ok else f"  {RED}✗{OFF} {label}"
          + (f" {DIM}— {detail}{OFF}" if detail else ""))
    if not ok:
        _fails.append(label)


import numpy as np                                   # noqa: E402
from PIL import Image                                # noqa: E402

from vtt import surface as S                         # noqa: E402


def swatch(*, gradient: float = 120.0, detail: float = 70.0,
           n: int = 256, seed: int = 3) -> bytes:
    """A tiling stone-ish swatch with a LIGHTING GRADIENT baked across it.

    The gradient is the whole point: it is what a real diffusion swatch has and
    what a naive luminance-to-height turns into a hillside.
    """
    rng = np.random.default_rng(seed)
    x, y = np.meshgrid(np.arange(n), np.arange(n))
    courses = ((x // 32 + y // 32) % 2) * detail          # mortar-ish blocks
    grain = rng.normal(0, detail * 0.12, (n, n))
    lit = (x / n) * gradient                              # somebody else's sun
    a = np.clip(90 + courses + grain + lit, 0, 255).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(a, "L").convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def as_array(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)), dtype=np.float32) / 255.0


# ---------------------------------------------------------------------------
print(f"\n{BOLD}1. the relief is recovered, the lighting is not{OFF}")

raw = swatch()
h = S.height_field(raw)
check("the height field is centred on zero, so scaling it is safe",
      abs(float(h.mean())) < 1e-5, f"mean {float(h.mean()):.2e}")

wide = S._wrap_blur(h, 21)
low, fine = float(np.abs(wide).mean()), float(np.abs(h).mean())
check("...and the baked lighting is gone: detail dwarfs the slow gradient",
      fine > 6 * low, f"detail {fine:.3f} vs low-frequency {low:.3f}")

# The naive version, for comparison — this is what it replaces, and it fails
# the same check by a wide margin.
grey = np.asarray(Image.open(io.BytesIO(raw)).convert("L"),
                  dtype=np.float32) / 255.0
naive = grey - grey.mean()
n_low = float(np.abs(S._wrap_blur(naive, 21)).mean())
n_fine = float(np.abs(naive).mean())
check("...where luminance alone keeps it, which is the mistake avoided",
      not (n_fine > 6 * n_low),
      f"detail {n_fine:.3f} vs low-frequency {n_low:.3f}")


print(f"\n{BOLD}2. a normal map three.js can actually use{OFF}")

nm = S.normal_map(raw)
check("one comes back at all", bool(nm), f"{len(nm or b'')} bytes")
a = as_array(nm) * 2.0 - 1.0
L = np.sqrt((a * a).sum(-1))
check("every vector is unit length",
      float(L.min()) > 0.99 and float(L.max()) < 1.01,
      f"{float(L.min()):.3f}..{float(L.max()):.3f}")
check("...and points OUT of the surface, which is what tangent space means",
      float(a[..., 2].min()) > 0.0, f"min z {float(a[..., 2].min()):.3f}")
check("a flat surface stays flat rather than inventing relief",
      float(np.abs(as_array(S.normal_map(_flat := (lambda: (
          lambda b: (Image.fromarray(np.full((256, 256, 3), 128, "uint8"), "RGB")
                     .save(b, "PNG"), b.getvalue())[1])(io.BytesIO()))())
      ) * 2 - 1)[..., :2].max()) < 0.02)

# Tiling is not a nicety: the swatch is drawn with RepeatWrapping, so a filter
# that clamps at the edge leaves a seam every five feet, in a grid, everywhere.
edge = float(np.abs(a[:, 0] - a[:, -1]).mean())
mid = float(np.abs(a[:, 128] - a[:, 129]).mean())
check("the derivation WRAPS, so a tiled floor has no seam",
      edge < mid * 3.0 + 1e-3, f"edge {edge:.4f} vs neighbouring {mid:.4f}")


print(f"\n{BOLD}3. shine is a fact about the SUBSTANCE{OFF}")

wet = as_array(S.roughness_map(raw, "water")).mean()
dry = as_array(S.roughness_map(raw, "limestone")).mean()
check("wet stone and dry stone are not the same surface",
      wet < dry - 0.3, f"water {wet:.2f} vs unknown-stone {dry:.2f}")

other = as_array(S.roughness_map(swatch(seed=99, detail=20), "water")).mean()
check("...and a different picture of the same stuff is the same surface",
      abs(other - wet) < 0.05, f"{other:.3f} vs {wet:.3f}")

check("an unclassified material is rough and not metal, which is most of them",
      S.properties_for("flagstone-worn") == S.DEFAULT_PROPERTIES,
      str(S.properties_for("flagstone-worn")))
check("a compound name finds its material — ship-TIMBER is timber",
      S.properties_for("ship-timber") == S.SURFACE_PROPERTIES["timber"])
check("...on word boundaries, so BRASS is not found inside embrasure",
      S.properties_for("embrasure") == S.DEFAULT_PROPERTIES)
check("metalness is a switch, never a dial",
      all(m in (0.0, 1.0) for _r, m in S.SURFACE_PROPERTIES.values()))
check("nothing is a mirror — everything on a battlefield is dirty",
      all(r >= 0.10 for r, _m in S.SURFACE_PROPERTIES.values()),
      f"shiniest {min(r for r, _ in S.SURFACE_PROPERTIES.values()):.2f}")
check("...and a roughness map never reaches 0 either",
      float(as_array(S.roughness_map(raw, "water")).min()) > 0.02)


print(f"\n{BOLD}4. what a URL may ask for, and what it gets{OFF}")

check("the channels are an allowlist", set(S.channels()) == {"normal", "rough"})
check("...and anything else is nothing", S.derived(1, "albedo", raw) is None)
check("a channel is memoised — the same swatch is asked for constantly",
      S.derived(1, "normal", raw) is S.derived(1, "normal", raw))
check("an unreadable swatch degrades rather than raising",
      S.normal_map(b"not a picture") is None
      and S.roughness_map(b"not a picture") is None)


print(f"\n{BOLD}5. the board ships them beside its swatches{OFF}")

db = Path(tempfile.gettempdir()) / "oracle_surface_smoke.db"
for suffix in ("", "-wal", "-shm"):
    Path(str(db) + suffix).unlink(missing_ok=True)
live = ROOT / "oracle-dm-backend" / "oracle.db"
if live.is_file():
    shutil.copy(live, db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"
# No GPU here: the swatches are looked UP, and a board with none of them is
# a board that degrades to flat tile colours — which is also worth asserting.
os.environ["ORACLE_IMAGERY_ENABLED"] = "0"

from imagery import ImageStore                       # noqa: E402
from vtt.scene import VttEngine                      # noqa: E402

eng = VttEngine(image_store=ImageStore())
# The project's rule: a board test opens with a DM's own sentence.
scene = eng.open_scene("surface:smoke", place_hint="a smoky taproom, lamplit",
                       name="The Gilded Sow")
mats = eng.materials_for(scene.id)
surf = eng.surfaces_for(scene.id)
check("every material has a surface, and no surface has no material",
      set(mats) == set(surf), f"{len(mats)} material(s), {len(surf)} surface(s)")
for key, rec in surf.items():
    ok = (rec["normal"].startswith("/imagery/surface/")
          and rec["normal"].endswith("/normal")
          and "/rough" in rec["rough_map"]
          and 0.0 <= rec["roughness"] <= 1.0
          and rec["metalness"] in (0.0, 1.0))
    if not ok:
        check(f"the record for {key!r} is well formed", False, str(rec))
        break
else:
    check("each names a derived normal, a derived roughness, and its numbers",
          bool(surf), f"{len(surf)} surface(s)")

st = eng.state(scene.id)
check("...and state() carries them to both renderers",
      "surfaces" in st and st["surfaces"] == surf)
check("a board with no swatches at all is simply flat-lit, not broken",
      isinstance(st.get("surfaces"), dict))

for suffix in ("", "-wal", "-shm"):
    Path(str(db) + suffix).unlink(missing_ok=True)

print()
if _fails:
    print(f"{RED}{len(_fails)} check(s) failed:{OFF} " + "; ".join(_fails))
    raise SystemExit(1)
print(f"{GREEN}the board's surfaces answer to light now{OFF}")
