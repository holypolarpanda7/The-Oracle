"""Every vocabulary one part of this project hands to another, checked.

THE BUG CLASS. `d.get(key, d["fallback"])` and `x if x in VOCAB else default`
never complain about a word they have not got. Where the key comes from
somewhere else — another module's table, a roll, a derivation from latitude —
the two sides drift, and nothing anywhere fails. Found four of these in one
week, all identical in shape and every one silent:

* `TERRAIN.get(name, TERRAIN["grassland"])` costed a SEA CROSSING as a stroll
  over a meadow, along with farmland, river, coast, underdark and dungeon.
* `climate if climate in CLIMATES else "temperate"` made four of the world's
  seven latitude bands temperate: the subarctic never froze.
* `_RELICS.get(fam, _RELICS["common"])` handed a soldier, a merchant and a
  farmer the generic prize for a quest they had just won.
* `_KIND_FRAMING.get(kind, ...CREATURE)` framed a MESH REFERENCE — an
  instrument reading nobody ever looks at — as "dynamic pose, menacing
  presence", arguing with sixty words of careful framing after it.

None of them raised. Every one resolved to something plausible and wrong.

WHAT THIS DOES. Names the pairs explicitly and asks whether the consumer knows
every key the producer can make. Deliberately a REGISTER rather than a scan: a
scan would find the `.get` calls and could never tell which of them matter,
and the interesting part is knowing what produces the key. Adding a pair here
is how a new vocabulary joins the guard.

    uv run python scripts/vocab_audit.py

Exits non-zero on any gap, so it can be a gate.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
_gaps: list[str] = []


def covers(what: str, produced, known, *, note: str = "") -> None:
    """Every key `produced` must be one `known` has."""
    missing = sorted(set(produced) - set(known))
    ok = not missing
    print(f"  {GREEN}✓{OFF} {what}" if ok else f"  {RED}✗{OFF} {what}"
          + (f" {DIM}— missing: {missing}{OFF}" if missing else ""))
    if note and not ok:
        print(f"      {DIM}{note}{OFF}")
    if not ok:
        _gaps.append(f"{what}: {missing}")


print(f"\n{BOLD}where a place IS{OFF}")
from eight_card_system import cartographer as carto, geo
from eight_card_system import placelore as pl
from survival.travel import TERRAIN as TRAVEL_TERRAIN
from survival import weather as wx

BANDS = {geo.climate_for((float(lat), 0.0)) for lat in range(-90, 91, 5)}
covers("every latitude band the world makes has WEATHER", BANDS, wx.CLIMATES,
       note="four of seven silently came out temperate")
covers("...and a BIOME ROSTER to roll from", BANDS, carto._BIOMES_BY_CLIMATE)

ROLLED = {b for v in carto._BIOMES_BY_CLIMATE.values() for b in v}
covers("every biome the cartographer rolls has TERRAIN WORDS", ROLLED, pl._TERRAIN)
covers("...has RELIEF", ROLLED, pl.RELIEF)
covers("...and has a WEATHER BIAS", ROLLED, pl.WEATHER_BIAS)
covers("placelore and its own relief table agree", pl._TERRAIN, pl.RELIEF)
covers("...and its own weather table", pl._TERRAIN, pl.WEATHER_BIAS)
covers("every terrain's TRAVEL key is one survival.travel knows",
       {pl.travel_terrain(t) for t in pl.RELIEF}
       | {pl.travel_terrain(t, c) for t in pl.RELIEF for c in BANDS},
       TRAVEL_TERRAIN,
       note="a sea crossing was costed as a stroll over grassland")

print(f"\n{BOLD}what the world grows there{OFF}")
covers("every region archetype has a settlement CEILING",
       [n for n, _w, _b in carto._REGION_ARCHETYPES], carto._CEILING_WEIGHTS)

from eight_card_system import ventures as vt

FAMS = set(vt._ROLE_FAMILY) | {"common"}
covers("every venture family has a PROMOTION", FAMS, vt._PROMOTION)
covers("...and a RELIC", FAMS, vt._RELICS,
       note="martial, mercantile and rustic won the generic prize")

print(f"\n{BOLD}what happens when you run{OFF}")
import importlib.util as _il

_spec = _il.spec_from_file_location(
    "_dm_audit", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
_dm = None
try:                                # importing the backend is optional here
    import os as _os
    _os.environ.setdefault("ORACLE_IMAGERY_ENABLED", "0")
    _dm = _il.module_from_spec(_spec)
    _spec.loader.exec_module(_dm)
except Exception as _e:             # pragma: no cover - a bare checkout
    print(f"  {DIM}(backend not importable here: {_e}){OFF}")

if _dm is not None:
    covers("every terrain a place can be in has CHASE complications",
           set(_dm._CHASE_FOR_TERRAIN.values()), _dm._CHASE_COMPLICATIONS,
           note="a chase through a marsh got a fruit-seller's cart")
    covers("...and the world's whole terrain vocabulary is mapped",
           pl.RELIEF, _dm._CHASE_FOR_TERRAIN)

print(f"\n{BOLD}what the board draws{OFF}")
from vtt import setpieces as sp
from vtt import terrain as tr
from vtt.mapgen import ARCHETYPES, _SETPIECES
from vtt.render_image import _TILE_COLORS

covers("every tile code has a colour on the Discord board", tr.TILES, _TILE_COLORS)
covers("every archetype named in the landmark pools exists",
       _SETPIECES, ARCHETYPES)
covers("every landmark named in a pool is in the catalogue",
       {s for v in _SETPIECES.values() for s in v}, sp.CATALOGUE)
covers("every climate a landmark names is a real band",
       {c for p in sp.CATALOGUE.values() for c in (p.climates or ())},
       wx.CLIMATES,
       note="a mistyped band would silently exclude the piece everywhere")
from vtt import skins as sk

covers("every skin a board defaults to exists",
       {n for v in sk.ARCH_SKINS.values() for n in v.values()}
       | set(sk.DEFAULT_SKINS.values()),
       sk.SKINS)
covers("...and every substance a skin names has a swatch subject",
       {p.substance for p in sk.SKINS.values()}, sk.substances())
from vtt import art as vart

covers("every archetype has a LOOK to draw its surfaces in",
       ARCHETYPES, vart._ARCH_LOOK,
       note="a board with no entry is drawn in DUNGEON — `terraces`, stacked "
            "plateaus of dry rock, was lit and floored like a crypt")
covers("...and every look an archetype names is one the catalogue holds",
       vart._ARCH_LOOK.values(), vart.BOARD_LOOKS)

# The board's own AMBIENT light, which crosses a language boundary: mapgen
# rolls it, `VttEngine.light_map` reads it as the floor every square starts
# from, `state()` ships it, and the isometric renderer sets its key light off
# it (KEY_LIGHT in vttScene3d.ts). A word one side coerces and the other does
# not is a crypt drawn in daylight, which is exactly what shipped for as long
# as the browser ignored the column.
_KEYS = set(re.findall(r"^  (\w+):\s*\{ colour:",
                       (ROOT / "activity-ui/src/lib/vttScene3d.ts")
                       .read_text(encoding="utf-8"), re.M))
covers("every ambient light level the board can carry is one the renderer draws",
       {"bright", "dim", "dark"}, _KEYS,
       note="`light_map` coerces anything else to bright, and so must the "
            "browser, or the picture and the rules disagree about the room")

print(f"\n{BOLD}what the picture is of{OFF}")
from imagery import prompt_build as pb
from imagery.models import ImageKind

KINDS = {getattr(ImageKind, a) for a in dir(ImageKind)
         if not a.startswith("_") and isinstance(getattr(ImageKind, a), str)}
covers("every image kind has its own FRAMING", KINDS, pb._KIND_FRAMING,
       note="a mesh reference was framed as a menacing creature")

print()
if _gaps:
    print(f"{RED}{len(_gaps)} vocabulary gap(s):{OFF}")
    for g in _gaps:
        print(f"  - {g}")
    raise SystemExit(1)
print(f"{GREEN}every vocabulary the project hands across a seam is known "
      f"on the other side{OFF}")
