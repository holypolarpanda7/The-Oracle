"""The DM names a thing, and the board stands it there.

A tavern called The Gilded Sow ought to have a gilded sow in it. The catalogue
of landmarks is a fixed list of meshes, and it exists so a model cannot ask for
a model nobody shipped — but that guarantee is about MESHES, and it says nothing
about a thing the board can already draw: one block of worked stone with a name
on it.

So a `landmark=` phrase the catalogue does not know becomes a piece with no mesh
(the same `source=None` a stepped pyramid uses), stamping `A` — already the tile
for a worked object standing on a floor that screens four feet and can be
broken. The DM says WHAT; the code decides where it stands, how big it is, and
what it does to a fight.

This came out of an accident worth keeping: a board whose only prompt was its
own name came back with golden pigs standing in it. The model draws a described
thing readily; the board simply had no way to MEAN one.

    uv run python scripts/feature_smoke.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

from vtt import art, mapgen, regions                          # noqa: E402
from vtt import setpieces as sp                                # noqa: E402
from vtt import skins as sk                                    # noqa: E402
from vtt.scene import _landmarks_from                          # noqa: E402
from vtt.terrain import cover_height_ft, tile                   # noqa: E402

OK, BAD, OFF, DIM = "\033[32m", "\033[31m", "\033[0m", "\033[2m"
_fails = 0


def check(cond: bool, what: str, detail: str = "") -> None:
    global _fails
    print(f"  {OK}OK{OFF}  {what}" if cond else f"  {BAD}FAIL{OFF}  {what}")
    if detail:
        print(f"      {DIM}{detail}{OFF}")
    if not cond:
        _fails += 1


PHRASE = "a gilded sow, a life-size statue of a pig in gold leaf"

print("\n\033[1m1. what becomes a feature, and what does not\033[0m")
f = sp.named_feature(PHRASE)
check(f is not None and f.name == PHRASE, "a described thing becomes a piece")
check(f.source is None, "with NO mesh — it is drawn from the tiles it stamps",
      "the same `source=None` a stepped pyramid uses")
check(sp.named_feature("a smoky taproom") is None,
      "a description of the ROOM is refused",
      "`landmark=` is also fed loose place text; a room must not become a "
      "statue of itself")
check(sp.named_feature("the ruined chapel") is None,
      "…and so is any phrase naming a kind of place")
check(sp.named_feature(PHRASE).slug == f.slug,
      "the same phrase always resolves to the same piece")
check(sp.piece(f.slug) is f and sp.piece("great-statue") is not None,
      "one accessor finds both the invented and the catalogued")

print("\n\033[1m2. the catalogue still wins where it knows the words\033[0m")
check(_landmarks_from("a stepped ziggurat", invent=True) == ["step-pyramid"],
      "a catalogue phrase resolves to the catalogue, not to a feature")
check(_landmarks_from(PHRASE, invent=True) == [f.slug],
      "an unknown one becomes the feature")
place_text = _landmarks_from(PHRASE)
check(all(not s_.startswith("feature-") for s_ in place_text),
      "and never from the place text alone — that path invents nothing",
      f"{place_text} — invent=True is set for an explicit landmark= only, or "
      f"every board would grow a statue of its own description")

print("\n\033[1m3. it is a real thing on a real board\033[0m")
gen = mapgen.generate_map("tavern", width=24, height=18, seed=7,
                          landmarks=[f.slug])
placed = list(gen.setpieces or [])
check(len(placed) == 1, "it is placed", f"{placed}")
x0, y0 = int(placed[0]["x"]), int(placed[0]["y"])
codes = {gen.grid.get(x0 + dx, y0 + dy)
         for dx in range(sp.FEATURE_SQUARES) for dy in range(sp.FEATURE_SQUARES)}
check(codes == {"A"}, "and it STAMPS its squares — the grid carries it",
      f"{codes}")
check(cover_height_ft("A") > 0 and not tile("A").move_cost_ft,
      "so it is cover you can hide behind and cannot walk through",
      f"{cover_height_ft('A')} ft, impassable — the rules never hear the word "
      f"'sow'")
inst = art._setpiece_instances(gen)
check(inst and inst[0]["name"] == PHRASE,
      "the board knows its NAME, which is what labels it in the picture",
      "render_image drops one chip per landmark at its footprint's middle")

print("\n\033[1m4. and the painter is told\033[0m")
codes_ = sk.skins_for(gen.archetype, style=gen.style)
sq = dict(gen.skins or {})
kw = art.conditioning_kwargs(
    gen, skin_of=lambda c, x, z: sk.skin_at(c, x, z, codes=codes_, squares=sq))
regs = regions.setpiece_regions(gen, **kw)
check(len(regs) == 1 and PHRASE in regs[0]["words"],
      "it gets a REGION of its own squares carrying its own description")
check(len(regs[0]["mask"]) > 0, "with a real mask",
      f"{len(regs[0]['mask'])} bytes")
check(any(w for w in (sp.piece(str(p.get('slug') or '')).words
                      for p in placed)),
      "SetPiece.words is non-empty and now reaches the prompt",
      "it said it was 'joined into the iso prompt exactly as Skin.words is' "
      "for months, and nothing joined it")

print("\n\033[1m5. it outlives the process that invented it\033[0m")
rec = dict(placed[0])
check(rec.get("name") == PHRASE,
      "the placed record carries the NAME",
      "the ad-hoc register is in memory and a board outlives the process that "
      "drew it, so without this a reloaded board silently loses the landmark "
      "it was built around")
sp._ADHOC.clear()
check(sp.piece(rec["slug"]) is None, "…and the register really is empty now")
check(sp.piece(rec["slug"], rec["name"]) is not None,
      "the stored name rebuilds the same piece",
      "named_feature is deterministic — the phrase IS the identity")

print("\n\033[1m6. a board nobody asked anything of is untouched\033[0m")
plain = mapgen.generate_map("tavern", width=24, height=18, seed=7)
check(not (plain.setpieces or []), "no landmark, no feature",
      f"{plain.setpieces}")

print()
if _fails:
    print(f"{BAD}{_fails} FAILED{OFF}")
    sys.exit(1)
print(f"{OK}the tavern called The Gilded Sow can have a gilded sow in it{OFF}")
