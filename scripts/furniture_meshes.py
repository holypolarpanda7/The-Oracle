"""Give the board's furniture MODELS, one per kind.

The shape tables draw a crate, a table, an altar and a column out of
prismatoids — chamfered, battered, turned — and that goes a long way for a few
numbers. What it cannot do is put a handle on a barrel, a moulding on an altar
or a grain in a plank, because none of those is a shape a rule can describe.

So a tile KIND may have a mesh, on exactly the economics the item catalogue and
the board sprites already use: **one crate model serves every crate on every
board in every session**. Nine kinds, not nine hundred squares — which is the
whole reason this is affordable where a per-square model would not be.

    ./.venv/Scripts/python.exe scripts/furniture_meshes.py --audit
    ./.venv/Scripts/python.exe scripts/furniture_meshes.py --render --only o,n

The WINDOWS interpreter, because it talks to ComfyUI (see CLAUDE.md). Renders
into ``generated/furniture/`` (gitignored); ``--collect`` moves the ones you
are happy with into ``activity-ui/public/assets/furniture/``, which IS
committed — a model of a crate carries no book text, no stat block and no
mechanics, so it is derived ART and follows the species portraits rather than
the rules data.

Decimated far harder than a landmark: a crate is five feet across at the far
end of an overhead camera, and forty thousand faces of it is bytes nobody can
see going down a socket to a Discord webview.

**Rendering and COMMITTING are two steps on purpose.** ``--render`` puts a
model where this installation will use it; ``--collect`` is the deliberate act
of putting one in the repo for everyone. The code can tell you a model is
ILLEGAL — wider than its own square at the height the rules quote, which
``--audit`` prints and ``furniture.fit`` refuses — and it cannot tell you
whether a model is any GOOD. A pedestal that is a perfectly correct altar is
the wrong thing in a crypt full of coffins, and no measurement catches that.
The server prices the cart; a person decides what to buy.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from imagery import landmark3d as L                  # noqa: E402
from imagery.mesh_client import TrellisClient        # noqa: E402
from vtt import furniture as F                       # noqa: E402
from vtt.terrain import cover_height_ft, tile        # noqa: E402

#: A crate is five feet across at the far end of an overhead camera.
FACES = 3000


def audit() -> int:
    print(f"{'code':<5} {'kind':<12} {'drawn':>7} {'spread':>7}  model")
    have = 0
    for code, subject in F.SUBJECTS.items():
        p = F.mesh_path(code)
        where = "—"
        sp = F.spread(code)
        if p is not None:
            where = ("committed" if p.parent == F.root() else "generated") \
                + f"  {p.stat().st_size // 1024} KB"
            if F.fit(code) is None:
                where += "  REFUSED: wider than its square"
            else:
                have += 1
        print(f"{code!r:<5} {tile(code).name:<12} "
              f"{F.quoted_height_ft(code):>5.0f} ft "
              f"{(f'{sp:.2f} sq' if sp else ''):>7}  {where}")
    print(f"\n{have} of {len(F.SUBJECTS)} kinds have a model. "
          f"The rest draw the shape tables, which is what every board did "
          f"before and is never an error.")
    return 0


def render(only: list[str], force: bool) -> int:
    client = TrellisClient(face_count=FACES)
    if not client.available():
        print("no TRELLIS.2 nodes reachable — is ComfyUI up with the packs "
              "registered? (see CLAUDE.md: check 'Registered N total nodes')")
        return 1
    out = F.generated_root()
    out.mkdir(parents=True, exist_ok=True)
    made = 0
    for code, subject in F.SUBJECTS.items():
        if only and code not in only:
            continue
        slug = F.slug_for(code)
        if not force and F.mesh_path(code) is not None:
            print(f"  {code!r} {slug}: already have one")
            continue
        print(f"  {code!r} {slug}: {subject}")
        # The landmark pipeline exactly — a rendered reference photograph with
        # no house style, matted, then meshed and normalized to Y-up geometry.
        # Nothing about furniture needs a second one.
        got = L.generate(f"furniture-{slug}", subject, seed=7, client=client)
        if got is None:
            print("      (no mesh — the reference or the mesher declined)")
            continue
        dest = out / f"{slug}.obj"
        shutil.move(str(got), dest)
        (got.with_suffix(".json")).replace(dest.with_suffix(".json"))
        made += 1
        print(f"      -> {dest.relative_to(ROOT)} "
              f"({dest.stat().st_size // 1024} KB)")
    F.forget()
    print(f"\n{made} model(s) rendered.")
    return 0


def collect(only: list[str]) -> int:
    """Move the ones you are happy with into the committed tree."""
    dest = F.root()
    dest.mkdir(parents=True, exist_ok=True)
    moved = 0
    for code in F.SUBJECTS:
        if only and code not in only:
            continue
        src = F.generated_root() / f"{F.slug_for(code)}.obj"
        if not src.exists():
            continue
        shutil.copy(src, dest / src.name)
        moved += 1
        print(f"  {code!r} -> {(dest / src.name).relative_to(ROOT)}")
    F.forget()
    print(f"\n{moved} model(s) collected. They are derived ART and are "
          f"committed; the descriptors that made them stay in code.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated tile codes")
    ap.add_argument("--force", action="store_true", help="re-render existing")
    a = ap.parse_args()
    only = [c.strip() for c in a.only.split(",") if c.strip()]
    if a.render:
        return render(only, a.force)
    if a.collect:
        return collect(only)
    return audit()


if __name__ == "__main__":
    raise SystemExit(main())
