"""What the DM says, and what the board makes of it.

The chain from narration to battlemap has five decision points, and every one of
them is somewhere a scene can go wrong in a way that looks like a rendering
problem and isn't:

1. the DM's ``[[VTT: open | kind | place | name]]`` — the only part an LLM
   authors, and it authors FICTION, not layout;
2. :func:`vtt.mapgen.archetype_for` mapping that free text onto one of 21
   layout families (or falling through to the place's biome);
3. :func:`vtt.triggers.board_size_for` sizing the board from the fight and its
   scenery;
4. the generator, deterministic from (archetype, size, seed);
5. the landmark table, the skins, and finally the painted layer.

This probe runs real narrations through 1-4 and prints what each step decided,
because "the board came out wrong" is nearly always one specific step choosing
something defensible from input that did not say enough. Where the archetype is
wrong, no amount of prompt tuning on the RENDER will help — and that is the
distinction this exists to make visible.

    uv run python scripts/scene_probe.py               # the decision table
    uv run python scripts/scene_probe.py --sheet       # + a geometry contact sheet
    ./.venv/Scripts/python.exe scripts/scene_probe.py --paint   # + the real render

``--paint`` needs ComfyUI and therefore the WINDOWS interpreter; see CLAUDE.md.
Output lands in ``scene-probe/``, which is gitignored.
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "scene-probe"


#: (label, narration, the hook's place text, biome the world graph holds,
#: what the hook's ``landmark=`` says — "" for a DM who names none).
#:
#: Written as PROSE first and the hook second, on purpose. The hook is what the
#: model actually emits and the prose is what it was looking at, so a case where
#: the prose is vivid and the hook is thin is the interesting one — that is the
#: shape of most real failures. The landmark column is the newest of those: the
#: prose promises a ziggurat, and until there was a channel for it the board
#: had no way to hear.
SCENES: tuple[tuple[str, str, str, str, str], ...] = (
    ("taproom",
     "The Gilded Sow is packed to the rafters, all pipe smoke and spilled ale. "
     "The bravo shoves the table aside and draws steel.",
     "a smoky taproom", "interior", ""),

    ("jungle-temple",
     "The canopy breaks and there it is — a stepped ziggurat swallowed in vines, "
     "its lower terrace crawling with the things that have been following you.",
     "an overgrown temple in the jungle", "forest", "a stepped ziggurat"),

    ("crypt",
     "Sarcophagi in ranks, lids shouldered aside from the inside. Something dry "
     "is moving at the far end of the vault.",
     "a burial vault", "dungeon", ""),

    ("mountain-road",
     "The track narrows to a ledge with a hundred feet of nothing below it. "
     "Rocks come down first, then the goblins.",
     "a high mountain pass", "mountains", "fallen boulders"),

    ("market",
     "Market day. Awnings, crates, a fountain running green — and three of the "
     "duke's men closing in from the alley mouths.",
     "a crowded market square", "urban", "a fountain"),

    ("reef",
     "Twenty feet down, sunlight comes through in bars. The coral heads make a "
     "maze of it, and something long is circling.",
     "a coral reef", "sea", ""),

    ("skyship",
     "The deck pitches. Boarding lines thump into the rail and the raiders come "
     "over the side hand over hand.",
     "the deck of an airship under boarding", "sky", ""),

    # ---- the ones that SHOULD be hard -----------------------------------
    ("thin-hook",
     "A fight breaks out where you were standing.",
     "", "forest", ""),                   # nothing in the hook; biome must save it

    ("name-only",
     "You are ambushed in the Grey Tors.",
     "The Grey Tors", "mountains", ""),   # a NAME keys nothing; biome decides

    ("mixed-signal",
     "The old chapel has no roof left; birches have come up through the nave "
     "and the floor is more moss than flagstone.",
     "a ruined chapel grown over with birches", "forest",
     "a ruined wall"),                     # ruins vs forest
)


def _fmt(v) -> str:
    return "—" if v in (None, "", [], {}) else str(v)


def decisions() -> list[dict]:
    """Run every scene through the real decision chain."""
    from vtt.mapgen import archetype_for, archetype_for_place, generate_map, setpiece_area_for
    from vtt.scene import DEFAULT_SIZE, _landmarks_from
    from vtt.triggers import board_size_for

    rows: list[dict] = []
    for label, prose, hook, biome, asked in SCENES:
        # Step 2, both halves: what the HOOK alone keys, and what the real
        # resolver decides once the world graph's biome is allowed to speak.
        from_hook = archetype_for(hook, default="") or "(nothing)"
        arch = archetype_for_place(hint=hook, biome=biome, default="open")

        # Step 2b: what the DM's landmark= words become. Falls back to the place
        # text exactly as the backend does, so a DM who never learned the
        # parameter still gets the fountain they described.
        marks = _landmarks_from(asked) or _landmarks_from(hook)

        # Step 3. A representative party-and-foes roster, so sizing is real.
        roster = [(1, 30)] * 4 + [(1, 30)] * 5
        base = DEFAULT_SIZE.get("combat", (24, 18))
        w, h = board_size_for(base, archetype=arch, creatures=roster,
                              longest_range_ft=150, landmarks=marks)

        gen = generate_map(arch, width=w, height=h, seed=7, biome=biome,
                           landmarks=marks)
        rows.append({
            "label": label, "prose": prose, "hook": hook, "biome": biome,
            "asked": asked, "marks": marks,
            "from_hook": from_hook, "arch": arch, "size": f"{w}x{h}",
            "base": f"{base[0]}x{base[1]}",
            "scenery_sq": setpiece_area_for(arch, marks),
            "landmarks": [p["slug"] for p in gen.setpieces],
            "mode": gen.mode, "style": gen.style,
            "desc": gen.description,
            "gen": gen,
        })
    return rows


def report(rows: list[dict]) -> None:
    B, D, OFFC = "\033[1m", "\033[2m", "\033[0m"
    print(f"\n{B}What the DM said, and what the board decided{OFFC}\n")
    for r in rows:
        print(f"{B}{r['label']}{OFFC}")
        print(f"  {D}narration{OFFC}  {r['prose'][:96]}")
        asked = f" | landmark={r['asked']}" if r["asked"] else ""
        print(f"  {D}hook{OFFC}       [[VTT: open | combat | "
              f"{r['hook'] or '<none>'}{asked} ]]"
              f"   {D}biome={r['biome']}{OFFC}")
        keyed = r["from_hook"]
        via = "hook" if keyed not in ("(nothing)",) and keyed == r["arch"] else (
            "biome" if keyed == "(nothing)" else f"hook({keyed})")
        print(f"  {D}archetype{OFFC}  {r['arch']}   {D}via {via}{OFFC}")
        print(f"  {D}size{OFFC}       {r['size']}   {D}(kind default {r['base']}, "
              f"scenery wants {r['scenery_sq']} sq){OFFC}")
        # Asked vs stood. A landmark the DM named and the board could not fit is
        # the one failure invisible from the narration, so it is called out.
        stood = ", ".join(r["landmarks"])
        lost = [s for s in r["marks"] if s not in r["landmarks"]]
        if r["marks"]:
            stood += f"   {D}(asked: {', '.join(r['marks'])}"
            stood += f"; NOT PLACED: {', '.join(lost)})" if lost else ")"
        print(f"  {D}landmarks{OFFC}  {_fmt(stood)}")
        print(f"  {D}board says{OFFC} {r['desc'][:96]}")
        print()


# --------------------------------------------------------------------------
# Pictures
# --------------------------------------------------------------------------

def sheet(rows: list[dict]) -> None:
    """A geometry contact sheet — the shape the painter will be handed."""
    from PIL import Image, ImageDraw
    from vtt import isocam
    from vtt.terrain import tile_height_ft, tile
    from vtt import skins as S

    OUT.mkdir(parents=True, exist_ok=True)
    tiles = []
    for r in rows:
        gen = r["gen"]
        codes = S.skins_for(gen.archetype, style=gen.style)
        skin_of = (lambda c, x, z, _c=codes, _s=gen.skins:
                   S.skin_at(c, x, z, codes=_c, squares=_s))
        insts = []
        if gen.setpieces:
            from vtt import setpieces as sp
            insts = [sp.Placed(slug=p["slug"], x=p["x"], y=p["y"],
                               yaw=p["yaw"]).instance() for p in gen.setpieces]
        png = isocam.terrain_image(
            gen.grid.to_rows(),
            colour_of=lambda c, n="": _colour(c),
            height_ft=tile_height_ft, skin_of=skin_of,
            elevation=gen.elevation, setpieces=insts, px_per_square=18)
        im = Image.open(io.BytesIO(png)).convert("RGBA")
        im.thumbnail((430, 430))
        tiles.append((r, im))

    cols = 3
    cw, ch = 440, 400
    rowsn = (len(tiles) + cols - 1) // cols
    sh = Image.new("RGBA", (cols * cw, rowsn * ch), (16, 16, 18, 255))
    d = ImageDraw.Draw(sh)
    for i, (r, im) in enumerate(tiles):
        x, y = (i % cols) * cw, (i // cols) * ch
        sh.paste(im, (x + (cw - im.width) // 2, y + 6), im)
        d.text((x + 8, y + ch - 40), r["label"], fill=(240, 238, 230, 255))
        d.text((x + 8, y + ch - 26),
               f"{r['arch']}  {r['size']}  {', '.join(r['landmarks']) or 'no landmarks'}",
               fill=(150, 150, 160, 255))
    path = OUT / "scenes.png"
    sh.convert("RGB").save(path)
    print(f"wrote {path}")


_COLS = {
    "g": (96, 142, 78), "\"": (74, 116, 62), ",": (128, 132, 96),
    "#": (150, 146, 140), "O": (166, 160, 150), "T": (58, 102, 52),
    "R": (128, 120, 112), ".": (176, 168, 150), "b": (150, 132, 104),
    "~": (86, 132, 168), "W": (54, 96, 140), "s": (198, 184, 148),
    "^": (168, 196, 220), "x": (30, 30, 38), "u": (160, 152, 140),
    "+": (140, 110, 70), "/": (150, 122, 80), "%": (110, 120, 110),
    "w": (100, 140, 120), "i": (200, 220, 230), "m": (120, 110, 130),
    "l": (200, 90, 40), "f": (170, 160, 150), "n": (90, 90, 100),
    "o": (150, 150, 160), "p": (120, 120, 130), "A": (170, 160, 140),
    "=": (140, 140, 150), " ": (16, 16, 18),
}


def _colour(code: str):
    return _COLS.get(code, (140, 140, 140))


def paint(rows: list[dict], limit: int) -> None:
    """Render the real battlemap for the first ``limit`` scenes."""
    from vtt import art
    from imagery import ImageStore

    OUT.mkdir(parents=True, exist_ok=True)
    store = ImageStore()
    # The depth model is CONFIGURATION, not a constant — the same lookup
    # iso_gallery and vtt/scene.py make. Without it render_iso_board refuses
    # rather than painting a plausible room that is not this one.
    img = store._cfg()
    cn = getattr(img, "isoboard_controlnet", "") or ""
    strength = float(getattr(img, "isoboard_controlnet_strength", 0.55))
    if not cn:
        print("  no isoboard_controlnet configured — nothing to paint.")
        return
    for r in rows[:limit]:
        gen = r["gen"]
        print(f"  painting {r['label']} ({r['arch']} {r['size']}) …", flush=True)
        try:
            got = art.render_iso_board(gen, store=store, biome=r["biome"],
                                       lighting=gen.lighting, name=r["label"],
                                       controlnet=cn, controlnet_strength=strength)
        except Exception as exc:                       # noqa: BLE001
            print(f"    failed: {exc}")
            continue
        if got is None or got.image_id is None:
            print(f"    no image ({'offline' if got and got.offline else 'refused'})")
            continue
        blob = store.get_image_bytes(got.image_id)
        if blob:
            (OUT / f"paint-{r['label']}.png").write_bytes(blob)
            print(f"    -> scene-probe/paint-{r['label']}.png  (id {got.image_id})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sheet", action="store_true", help="write a geometry sheet")
    ap.add_argument("--paint", action="store_true", help="render real battlemaps")
    ap.add_argument("--limit", type=int, default=3, help="scenes to paint")
    ap.add_argument("--only", default="", help="comma-separated scene labels")
    a = ap.parse_args()

    rows = decisions()
    report(rows)
    if a.sheet:
        sheet(rows)
    if a.paint:
        want = [w.strip() for w in a.only.split(",") if w.strip()]
        paint([r for r in rows if r["label"] in want] if want else rows, a.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
