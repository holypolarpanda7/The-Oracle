"""Resolve, collect and account for the board's set-piece meshes.

The catalogue in :mod:`vtt.setpieces` authors the RULES half of every landmark
exactly — footprint, tiles, height. It cannot author the file half, because a
pack's model names are the pack author's business and change between releases.
So an entry carries candidate name fragments and this script resolves them
against the packs on disk, which is both more honest than hard-coding paths
somebody guessed and more robust when a pack is updated.

Three things it does, in the ``--audit`` / ``--render`` idiom the item-art and
debris pre-renderers already use:

``--audit``
    What is on disk, what each catalogue entry resolved to, and what did not
    resolve at all. Reports the mesh's own bounding box against the height the
    entry declares, because a model scaled to fit its footprint and coming out
    twice as tall as the rules say is a landmark that lies about cover.

``--collect``
    Copy each resolved mesh (and its material/texture, if any) out of the
    gitignored workspace into ``activity-ui/public/assets/setpieces/``, which
    IS committed. Only what is used gets copied — a whole pack is large and not
    ours to hand on in bulk.

``--attribution``
    Regenerate ATTRIBUTION.md from the register in code, so the file beside the
    binaries and the code that names their licences cannot drift.

Getting the packs is a manual step and stays one: every source here puts its
download behind a page rather than a stable URL, and a scraper that guesses is
a scraper that silently fetches the wrong thing. Run ``--audit`` for the list
of what to fetch and where to unzip it.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vtt import setpieces as sp                                   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / sp.PACK_WORKSPACE
MESHES = ROOT / sp.MESH_ROOT


# --------------------------------------------------------------------------
# A very small OBJ reader
#
# Only the bounding box is wanted, so this is deliberately not a mesh loader:
# ``v`` lines and nothing else. Worth the forty lines to avoid a dependency —
# the depth rasterizer will need to read these same files server-side, and OBJ
# being this cheap in Python is why the catalogue prefers it.
# --------------------------------------------------------------------------

def obj_bounds(path: Path) -> tuple[tuple[float, float, float],
                                    tuple[float, float, float]] | None:
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    seen = False
    try:
        with path.open("r", errors="ignore") as fh:
            for line in fh:
                if not line.startswith("v "):
                    continue
                parts = line.split()
                if len(parts) < 4:
                    continue
                try:
                    xyz = [float(parts[1]), float(parts[2]), float(parts[3])]
                except ValueError:
                    continue
                seen = True
                for i in range(3):
                    lo[i] = min(lo[i], xyz[i])
                    hi[i] = max(hi[i], xyz[i])
    except OSError:
        return None
    return (tuple(lo), tuple(hi)) if seen else None    # type: ignore[return-value]


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

def pack_dir(slug: str) -> Path | None:
    """Where a pack was unzipped. Tolerant of how the archive named itself."""
    direct = WORKSPACE / slug
    if direct.is_dir():
        return direct
    if not WORKSPACE.is_dir():
        return None
    want = slug.replace("-", "").replace("_", "")
    for child in sorted(WORKSPACE.iterdir()):
        if not child.is_dir():
            continue
        if child.name.replace("-", "").replace("_", "").lower().startswith(want[:8]):
            return child
    return None


def candidates(root: Path) -> list[Path]:
    out: list[Path] = []
    for ext in sp.FORMATS:
        out.extend(sorted(root.rglob(f"*.{ext}")))
    return out


def resolve(piece: sp.SetPiece) -> tuple[Path | None, str]:
    """Best file for this entry, and why — exact fragment order wins.

    Fragments are tried best-first and the FIRST that matches anything decides,
    rather than scoring every file against every fragment. A later fragment is
    a fallback, not a rival: ``pillar_broken`` before ``pillar`` means "the
    broken one if the pack has one, any pillar if not", and a scoring scheme
    would happily prefer a well-named whole pillar to a badly-named broken one.
    """
    if piece.source is None:
        return None, "no mesh — drawn from the tiles it stamps"
    root = pack_dir(piece.source.pack)
    if root is None:
        return None, "pack not unzipped"
    files = candidates(root)
    if not files:
        return None, "no mesh files under the pack directory"
    for frag in piece.source.match:
        f = frag.lower()
        hits = [p for p in files if f in p.stem.lower()]
        if not hits:
            continue
        # Prefer the format the catalogue prefers, then the shortest name —
        # "rock" over "rock_largeA_variant3" when the fragment was "rock".
        hits.sort(key=lambda p: (sp.FORMATS.index(p.suffix[1:].lower())
                                 if p.suffix[1:].lower() in sp.FORMATS else 9,
                                 len(p.stem)))
        return hits[0], f"matched {frag!r}"
    return None, f"no file matching any of {list(piece.source.match)}"


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_list() -> int:
    print(f"{len(sp.PACKS)} packs registered, "
          f"{len(sp.CATALOGUE)} set pieces, "
          f"{len(sp.packs_in_use())} packs actually drawn on\n")
    by_pack: dict[str, list[sp.SetPiece]] = {}
    for piece in sp.CATALOGUE.values():
        by_pack.setdefault(piece.source.pack if piece.source else "(no mesh)",
                           []).append(piece)
    for slug in sorted(by_pack):
        pack = sp.PACKS.get(slug)
        if pack is None:
            print("Drawn from tiles alone — no mesh, no third-party anything")
        else:
            print(f"{pack.name}  [{pack.license}]  {pack.author}  {pack.url}")
        for piece in sorted(by_pack[slug], key=lambda p: p.slug):
            flag = ("  (STAND-IN)"
                    if piece.source and piece.source.stand_in else "")
            print(f"    {piece.slug:<16} {piece.width}x{piece.depth} squares, "
                  f"{piece.height_ft:g} ft{flag}")
            print(f"        tiles: {' '.join(piece.tiles)}")
            if piece.source:
                print(f"        looks for: {', '.join(piece.source.match)}")
        print()
    unused = sorted(set(sp.PACKS)
                    - {p.source.pack for p in sp.CATALOGUE.values() if p.source})
    if unused:
        print("Registered but not yet drawn on: " + ", ".join(unused))
    return 0


def cmd_audit(square_ft: float) -> int:
    print(f"workspace: {WORKSPACE}")
    print(f"meshes:    {MESHES}\n")
    missing_packs: list[sp.Pack] = []
    for pack in sp.packs_in_use():
        root = pack_dir(pack.slug)
        if root is None:
            missing_packs.append(pack)
            print(f"  MISSING  {pack.slug:<22} {pack.name}")
        else:
            n = len(candidates(root))
            note = "" if n else "  (unzipped but no meshes found)"
            print(f"  ok       {pack.slug:<22} {n} mesh files{note}")
    print()

    unresolved = 0
    for piece in sorted(sp.CATALOGUE.values(), key=lambda p: p.slug):
        path, why = resolve(piece)
        committed = MESHES / f"{piece.slug}.obj"
        mark = "committed" if committed.exists() else "         "
        if path is None:
            if piece.source is None:
                print(f"  n/a {mark} {piece.slug:<16} {why}")
                continue
            unresolved += 1
            extra = ("  <- nearest match only; nothing open IS this thing"
                     if piece.source.stand_in else "")
            print(f"  --  {mark} {piece.slug:<16} {why}{extra}")
            continue
        line = f"  ok  {mark} {piece.slug:<16} {path.relative_to(WORKSPACE)}"
        bounds = obj_bounds(path) if path.suffix.lower() == ".obj" else None
        if bounds:
            (x0, y0, z0), (x1, y1, z1) = bounds
            span = max(x1 - x0, z1 - z0) or 1.0
            # Scale is DERIVED from the declared footprint, never authored: a
            # pack's units are arbitrary, and a magic per-pack multiplier is a
            # number nobody can check. Fit the model to the squares it is said
            # to cover, then see what its height became.
            scale = (piece.width * square_ft) / span
            got = (y1 - y0) * scale
            off = abs(got - piece.height_ft) / max(piece.height_ft, 1.0)
            flag = "" if off <= 0.35 else f"   <- {off * 100:.0f}% off"
            line += f"   fits to {got:.0f} ft vs {piece.height_ft:g} declared{flag}"
        print(line)

    print()
    if missing_packs:
        print("Unzip these into the workspace, one directory per pack slug:")
        for pack in missing_packs:
            print(f"  {WORKSPACE / pack.slug}   <- {pack.url}")
        print()
    meshed = sum(1 for p in sp.CATALOGUE.values() if p.source)
    print(f"{meshed - unresolved}/{meshed} set pieces needing a mesh resolve; "
          f"{len(sp.CATALOGUE) - meshed} need none.")
    return 0


def cmd_collect(force: bool) -> int:
    MESHES.mkdir(parents=True, exist_ok=True)
    took = 0
    for piece in sorted(sp.CATALOGUE.values(), key=lambda p: p.slug):
        path, why = resolve(piece)
        if path is None:
            print(f"  skip  {piece.slug:<16} {why}")
            continue
        dest = MESHES / f"{piece.slug}{path.suffix.lower()}"
        if dest.exists() and not force:
            print(f"  have  {piece.slug}")
            continue
        shutil.copy2(path, dest)
        took += 1
        # An OBJ's materials live beside it and are useless without it. They
        # are copied for completeness, not because the board reads them: once
        # the painted layer is present the geometry draws no colour at all.
        for mtl in path.parent.glob(f"{path.stem}.mtl"):
            shutil.copy2(mtl, MESHES / f"{piece.slug}.mtl")
        print(f"  took  {piece.slug:<16} <- {path.name}")
    print(f"\n{took} copied into {MESHES}")
    return 0


def cmd_attribution() -> int:
    MESHES.mkdir(parents=True, exist_ok=True)
    out = MESHES / "ATTRIBUTION.md"
    out.write_text(sp.attribution_markdown(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true",
                    help="the catalogue and the packs it draws on")
    ap.add_argument("--audit", action="store_true",
                    help="what is on disk and what each entry resolves to")
    ap.add_argument("--collect", action="store_true",
                    help="copy resolved meshes into the committed asset dir")
    ap.add_argument("--attribution", action="store_true",
                    help="regenerate ATTRIBUTION.md from the register in code")
    ap.add_argument("--force", action="store_true",
                    help="with --collect, overwrite meshes already copied")
    ap.add_argument("--square-ft", type=float, default=5.0,
                    help="feet per square, for the height check (default 5)")
    args = ap.parse_args()

    if args.list:
        return cmd_list()
    if args.attribution:
        return cmd_attribution()
    if args.collect:
        return cmd_collect(args.force)
    return cmd_audit(args.square_ft)


if __name__ == "__main__":
    raise SystemExit(main())
