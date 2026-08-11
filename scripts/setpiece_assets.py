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
    resolve at all. Reports how many squares the mesh NEEDS once it is scaled to
    the height the entry declares, against how many the entry declares.

    **Height is authoritative and the footprint gives way**, which is a reversal
    worth writing down because the first version had it the other way round. It
    fitted the mesh's width to the declared footprint and reported the height
    that fell out, and against real packs that made every tall thing a dwarf:
    a 60-ft jungle giant came out at 21 ft and a 40-ft gate tower at 3 ft. The
    height is the fiction — the DM says a colossus stands here — and it is what
    the depth map's frame is sized from, so shrinking it to suit a footprint
    silently rewrites the scene. The footprint is a floor-level RULES statement
    about which squares are stamped, and there is no reason it cannot be wider;
    a landmark that needs nine squares gets nine, and the board grows to hold
    it (see ``vtt.triggers.board_size_for``). Scaling stays UNIFORM either way:
    stretching one axis to satisfy both numbers distorts the model, which reads
    as a bug on anything organic.

``--collect``
    Copy each resolved mesh (and its material/texture, if any) out of the
    gitignored workspace into ``activity-ui/public/assets/setpieces/``, which
    IS committed. Only what is used gets copied — a whole pack is large and not
    ours to hand on in bulk.

``--attribution``
    Regenerate ATTRIBUTION.md from the register in code, so the file beside the
    binaries and the code that names their licences cannot drift.

``--fetch``
    Download and extract the packs that CAN be fetched. This RESOLVES rather
    than guesses, which is the distinction that makes it safe: it reads the
    pack's own registered page and takes the ``.zip`` that page links to, the
    same discipline :attr:`Source.match` uses to find a mesh inside a pack. No
    URL is constructed or predicted, so a pack that re-releases under a new
    hash is still found and a pack that stops publishing a zip fails loudly
    instead of fetching something else.

    Not every source can be fetched, and the ones that cannot are REPORTED with
    their page rather than worked around. Quaternius puts its packs behind a
    JavaScript-rendered Google Drive folder with no server-side listing; there
    is nothing on that page to resolve, so those stay a manual download. That
    is a statement about the host, not a limitation to route around with a
    headless browser — see ``--audit``, which tells you exactly where to unzip.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
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

def mesh_footprint(path: Path, piece: sp.SetPiece,
                   square_ft: float) -> tuple[int, int] | None:
    """How many squares this mesh covers once scaled to its declared height.

    The one place the set-piece scale is computed, and it is DERIVED rather
    than authored: a pack's units are arbitrary and a per-pack magic multiplier
    is a number nobody can check. Both renderers have to arrive at this same
    scale from the same two facts — the mesh's own bounding box and the height
    the catalogue declares — or the painting is conditioned on a landmark a
    different size from the one the player is looking at.

    Returns ``None`` for a format this script cannot measure; only OBJ is read
    here, which is also the format the depth rasterizer will parse server-side.
    """
    if path.suffix.lower() != ".obj":
        return None
    bounds = obj_bounds(path)
    if bounds is None:
        return None
    (x0, y0, z0), (x1, y1, z1) = bounds
    tall = (y1 - y0) if piece.up == "y" else (z1 - z0)
    if tall <= 0:
        return None
    scale = piece.height_ft / tall
    wide = (x1 - x0) * scale
    deep = ((z1 - z0) if piece.up == "y" else (y1 - y0)) * scale
    ceil = lambda ft: max(1, int(-(-ft // square_ft)))          # noqa: E731
    return ceil(wide), ceil(deep)


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


def _norm(name: str) -> str:
    """A model name with its word separators taken out, for matching.

    Kits are re-released with their naming convention changed and nothing else:
    Kenney's nature kit is ``tree_palmDetailedTall`` and the castle and pirate
    kits it ships beside are now ``tower-square-top``. Matching on the literal
    string makes a catalogue entry correct against one release of a pack and
    silently wrong against the next — which is the failure this whole
    fragment-matching scheme exists to avoid, so the separator has to go the
    same way the case does.
    """
    return re.sub(r"[-_\s]+", "", name).lower()


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
        f = _norm(frag)
        hits = [p for p in files if f in _norm(p.stem)]
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
    unresolved_fit: list[tuple[sp.SetPiece, int, int]] = []
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
        need = mesh_footprint(path, piece, square_ft)
        if need is not None:
            nw, nd = need
            line += f"   needs {nw}x{nd} at {piece.height_ft:g} ft"
            if nw > piece.width or nd > piece.depth:
                unresolved_fit.append((piece, nw, nd))
                line += f"   <- declares {piece.width}x{piece.depth}; WIDEN IT"
            elif nw < piece.width or nd < piece.depth:
                # Not an error. A footprint may be larger than the mesh on
                # purpose — the jungle giant's canopy squares are there to keep
                # the scatter out from under it, and cost nothing but room.
                line += f"   (declares {piece.width}x{piece.depth}, room to spare)"
        print(line)

    print()
    if missing_packs:
        print("Unzip these into the workspace, one directory per pack slug:")
        for pack in missing_packs:
            print(f"  {WORKSPACE / pack.slug}   <- {pack.url}")
        print()
    if unresolved_fit:
        print("These declare a footprint smaller than the mesh needs at the "
              "height they claim. Widen the footprint (the board grows to "
              "hold it) or lower the height — never scale one axis alone:")
        for piece, nw, nd in unresolved_fit:
            print(f"  {piece.slug:<16} declares {piece.width}x{piece.depth}, "
                  f"needs {nw}x{nd} at {piece.height_ft:g} ft")
        print()
    meshed = sum(1 for p in sp.CATALOGUE.values() if p.source)
    print(f"{meshed - unresolved}/{meshed} set pieces needing a mesh resolve; "
          f"{len(sp.CATALOGUE) - meshed} need none.")
    return 0


def cmd_fetch(force: bool) -> int:
    """Download and unzip every pack whose page actually links to an archive."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    manual: list[tuple[sp.Pack, str]] = []
    got = 0
    for pack in sp.packs_in_use():
        dest = WORKSPACE / pack.slug
        if dest.is_dir() and any(dest.rglob("*")) and not force:
            print(f"  have    {pack.slug}")
            continue
        try:
            page = _get(pack.url)
        except urllib.error.URLError as exc:
            manual.append((pack, f"page unreachable: {exc}"))
            continue
        url = _zip_link(page, pack.url)
        if url is None:
            manual.append((pack, "the page links no archive — see the docstring"))
            continue
        print(f"  fetch   {pack.slug:<22} {url}")
        try:
            blob = _get(url)
        except urllib.error.URLError as exc:
            manual.append((pack, f"download failed: {exc}"))
            continue
        tmp = WORKSPACE / f"{pack.slug}.zip"
        tmp.write_bytes(blob)
        try:
            with zipfile.ZipFile(tmp) as z:
                _safe_extract(z, dest)
        except zipfile.BadZipFile:
            manual.append((pack, "downloaded file is not a zip"))
            tmp.unlink(missing_ok=True)
            continue
        tmp.unlink(missing_ok=True)
        n = len(candidates(dest))
        got += 1
        note = "" if n else "   <- extracted but no mesh files; check FORMATS"
        print(f"  ok      {pack.slug:<22} {n} mesh files{note}")

    if manual:
        print("\nThese have to be downloaded by hand — unzip each into the "
              "directory shown:")
        for pack, why in manual:
            print(f"  {pack.name}  ({why})")
            print(f"      from {pack.url}")
            print(f"      into {WORKSPACE / pack.slug}")
    print(f"\n{got} pack(s) fetched; {len(manual)} need a manual download.")
    return 0


def cmd_adopt(where: str, force: bool) -> int:
    """Take in a pack that was downloaded by hand, matching it to its slug.

    The counterpart to ``--fetch`` for the sources it cannot reach. Quaternius
    hands you ``Medieval Village MegaKit[Standard].zip`` in a browser, and the
    step after that — work out which registered pack it is, unzip it under the
    right slug — is the step that gets done by hand once and misremembered
    later. Matching is by the pack's NAME with the separators taken out, the
    same normalisation the mesh resolver uses, so a download keeps whatever the
    host chose to call it.
    """
    src = Path(where).expanduser()
    zips = ([src] if src.is_file()
            else sorted(p for p in src.glob("*.zip")) if src.is_dir() else [])
    if not zips:
        print(f"no .zip found at {src}")
        return 1
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    took = 0
    for z in zips:
        stem = _norm(z.stem)
        hit = next((p for p in sp.PACKS.values() if _norm(p.name) in stem), None)
        if hit is None:
            print(f"  skip  {z.name}  (matches no registered pack)")
            continue
        dest = WORKSPACE / hit.slug
        if dest.is_dir() and any(dest.rglob("*")) and not force:
            print(f"  have  {hit.slug}")
            continue
        try:
            with zipfile.ZipFile(z) as zf:
                _safe_extract(zf, dest)
        except zipfile.BadZipFile:
            print(f"  bad   {z.name} is not a zip")
            continue
        took += 1
        print(f"  took  {hit.slug:<22} <- {z.name}  "
              f"({len(candidates(dest))} mesh files)")
    print(f"\n{took} pack(s) adopted into {WORKSPACE}")
    return 0


def _get(url: str, timeout: int = 180) -> bytes:
    # Kenney's CDN serves a 403 to the stdlib's default agent.
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return fh.read()


def _zip_link(page: bytes, base: str) -> str | None:
    """The archive this page offers, or None if it offers none.

    Deliberately takes the FIRST ``.zip`` on the page rather than scoring
    candidates: a pack page offers its own pack, and a page that offers several
    archives is a page this script has misunderstood and should not guess at.
    """
    text = page.decode("utf-8", errors="ignore")
    m = re.search(r'https?://[^\s"\'<>]+\.zip', text)
    if m:
        return m.group(0)
    m = re.search(r'''["']((?:/|\.\.?/)[^\s"'<>]+\.zip)["']''', text)
    if m:
        return urllib.parse.urljoin(base, m.group(1))
    return None


def _safe_extract(z: zipfile.ZipFile, dest: Path) -> None:
    """Extract, refusing any member that would land outside ``dest``.

    A zip is an untrusted archive off the internet, and ``extractall`` will
    happily honour ``../`` in a member name. The packs here are reputable; the
    check costs four lines and does not depend on them staying so.
    """
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    for member in z.namelist():
        target = (root / member).resolve()
        if not str(target).startswith(str(root)):
            raise ValueError(f"zip member escapes the workspace: {member!r}")
    z.extractall(root)


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
    ap.add_argument("--fetch", action="store_true",
                    help="download and unzip the packs whose page links one")
    ap.add_argument("--adopt", metavar="PATH",
                    help="take in a hand-downloaded zip (or a folder of them)")
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
    if args.fetch:
        return cmd_fetch(args.force)
    if args.adopt:
        return cmd_adopt(args.adopt, args.force)
    if args.collect:
        return cmd_collect(args.force)
    return cmd_audit(args.square_ft)


if __name__ == "__main__":
    raise SystemExit(main())
