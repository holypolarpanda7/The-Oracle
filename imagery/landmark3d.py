"""A landmark the DM INVENTED, given a shape.

The board draws everything else from (tile code, skin, x, z) put through a shape
table, because a wall, a cliff, a crate and a hull are all things a rule can
describe. A catalogue of CC0 meshes covers the rest — a colossal seated
guardian, a ruined arch — but a catalogue is a fixed list, and the DM's own
gilded sow is not on it. That case has been standing on the board as a 2x2
stamped box with a name on it: mechanically perfect and visually nothing.

The missing step was never the geometry. It was that a PICTURE of the thing is
something this project already knows how to make — it renders one for every
item in the catalogue and every piece of wreckage on every board — and nothing
turned a picture into a shape. TRELLIS.2 does, and what comes back is a mesh
like any other: fitted by :func:`vtt.setpieces.mesh_fit` on the server, drawn by
the isometric board, rasterized into the depth map the painter is conditioned
on, and carrying NO mechanical content at all. The tiles the piece stamps are
still its entire rules meaning. That is the same bargain a downloaded mesh
makes, and it is what keeps this from being the model authoring the board.

Three rules it keeps, all of them the project's already:

* **It degrades to what the board has always drawn.** No GPU, no ComfyUI, no
  TRELLIS nodes, a failed render, a mesh that comes back empty — every one of
  those leaves the stamped box exactly where it was. A landmark is never
  missing, only plainer.
* **It is a BACKGROUND job.** A mesh is minutes on this rig; a fight does not
  wait for one.
* **The file is written atomically.** ``_obj_bounds`` measures whatever is on
  disk, so a half-written OBJ would be measured, cached, and stand the landmark
  at a confidently wrong size.
"""
from __future__ import annotations

import json
import os
import re
import struct
import threading
import time
from pathlib import Path
from typing import Optional

from .mesh_client import MeshServiceUnavailable, TrellisClient

#: How big a reference picture to render. TRELLIS.2 is conditioned by DINOv3 at
#: 512-ish; the extra pixels buy detail the mesher then throws away, and SDXL
#: draws a sharper 768 than it draws a 512 (the wreckage-sprite lesson).
REFERENCE_PX = 768

#: What the picture has to BE for a mesher to read it. The view comes first for
#: the reason the sprite prompts do it: the model's prior for any object is its
#: own idea of how such a thing is photographed, and a trailing modifier loses
#: to it. Three-quarter rather than the board's overhead — a mesher needs to see
#: front and side, and the board's own camera never looks at the reference.
#: The framing lives with every other kind's, in `imagery/prompt_build.py`
#: under `ImageKind.MESHREF`. It was here as well, appended to the phrase,
#: while the kind had no entry of its own and fell back to CREATURE — so the
#: prompt opened with "dynamic pose, menacing presence" and then argued with
#: itself for another sixty words. One kind, one framing, one place.

#: The whole art direction, replacing the house style for this kind. Flat,
#: even, documentary — the opposite of everything the game's own look asks
#: for, and correct here for the same reason a reference photograph is not a
#: painting.
_MESH_STYLE = (
    "high resolution reference photograph, neutral colour, flat even "
    "illumination, no artistic styling, no stylisation, documentary product "
    "photography, full object visible"
)

#: Everything that ruins a matte or a mesh. A cropped subject loses a limb in
#: the geometry, a dramatic key light bakes a shadow into the shape, and a
#: second object anywhere in frame comes back fused to the first.
_MESH_NEGATIVE = (
    "cropped, cut off, out of frame, close-up, extreme close-up, multiple "
    "objects, group, collection, pair, background scenery, room, landscape, "
    "horizon, sky, floor pattern, tiles, text, watermark, signature, frame, "
    "border, dramatic shadows, hard cast shadow, silhouette, dark background, "
    "busy background, motion blur, depth of field, bokeh, reflections, "
    "transparent, glass, smoke, fog, particles, "
    # The failure this actually produced: a flat emblem, faithfully meshed as
    # a flat emblem. A mesher can only build what the picture shows it, so
    # anything that reads as a 2D device has to be refused in the negative.
    "flat, 2d, emblem, crest, heraldry, coat of arms, shield, badge, logo, "
    "icon, sticker, decal, sign, plaque, medallion, relief carving, "
    "bas-relief, engraving, illustration, vector art, flat design, "
    "front view, orthographic view, side view, top view"
)

#: Slug shape a route may serve from disk. Landmark slugs are built by
#: :func:`vtt.setpieces.named_feature` out of a hex digest, but this is the
#: guard a file-serving route needs and it belongs beside the writer.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")

#: Only one mesh at a time, whoever asks. TRELLIS.2 wants ~2.6 GB per model on
#: a card that is also holding SDXL and — on this rig — a local LLM, so two at
#: once is how the box runs out of memory rather than how it goes faster. The
#: in-flight set on top of it means two boards wanting the same landmark queue
#: behind one render instead of racing to write the same file.
_LOCK = threading.Lock()
_INFLIGHT: set[str] = set()
_INFLIGHT_LOCK = threading.Lock()


def root() -> Path:
    from vtt import setpieces as sp
    return sp.generated_root()


#: What the mesher is asked for now, and what a new landmark is stored as.
#:
#: GLB, because it is the only format in this pipeline that can carry a
#: TEXTURE. The geometry-only workflow that came before wrote OBJ on the
#: reasoning that a mesh's readers wanted positions and nothing else — and the
#: cost of that was every generated landmark being drawn in ONE FLAT AVERAGED
#: COLOUR, a carefully modelled thing standing on a PBR floor looking like
#: painted cardboard.
MESH_EXT = "glb"

#: Read in this order. An installation that generated landmarks under the old
#: workflow still has them, and they are still perfectly good geometry — the
#: new format is preferred where both exist, and the old one is never thrown
#: away for being old.
MESH_EXTS = ("glb", "obj")


def _existing(slug: str) -> Optional[Path]:
    """The mesh already on disk for this landmark, whatever format it is in."""
    d = root()
    for ext in MESH_EXTS:
        p = d / f"{slug}.{ext}"
        try:
            if p.is_file() and p.stat().st_size > 0:
                return p
        except OSError:
            continue
    return None


def has_mesh(slug: str) -> bool:
    """Is there already a generated mesh for this landmark?"""
    return _existing(slug) is not None


def mesh_file(slug: str) -> Optional[Path]:
    """The generated mesh, guarded for a route to serve.

    The slug check is not decoration: this path is reachable from a URL, and
    the one thing a URL must never be able to say is ``../``.
    """
    if not SLUG_RE.match(slug or ""):
        return None
    p = _existing(slug)
    if p is None:
        return None
    try:
        p = p.resolve()
        if p.parent != root().resolve() or not p.is_file():
            return None
    except OSError:
        return None
    return p


def enabled() -> bool:
    """Is landmark meshing turned on at all?

    Off by default. It is minutes of GPU per invented landmark on a box that
    is already time-sharing a card between SDXL and a local model, so it is an
    operator's decision rather than something a table discovers by lagging.
    """
    return os.getenv("ORACLE_LANDMARK_MESH", "0").strip().lower() in (
        "1", "true", "yes", "on")


# --------------------------------------------------------------------------
# Making one
# --------------------------------------------------------------------------

def reference_prompt(phrase: str) -> str:
    """What the picture is OF — the DM's own words, tidied.

    How it is framed is `ImageKind.MESHREF`'s business and is stated once,
    beside every other kind's framing. See the note above.
    """
    return " ".join((phrase or "").strip().split())


def _forget_reference(slug: str, *, store=None) -> None:
    """Delete the cached reference for this slug, so the next one is drawn."""
    try:
        if store is None:
            from . import ImageStore
            store = ImageStore()
        from sqlmodel import Session, select
        from .models import EntityImage
        with Session(store.engine) as sess:
            for row in sess.exec(select(EntityImage)).all():
                if row.ref_slug == f"landmark3d-{slug}":
                    sess.delete(row)
            sess.commit()
    except Exception as e:                                # pragma: no cover
        print(f"[landmark3d] could not drop the reference for {slug}: {e}")


def render_reference(phrase: str, slug: str, *, store=None) -> Optional[bytes]:
    """Draw the thing once, cut it out, and hand back PNG bytes with alpha.

    Cut out rather than rendered on a plain background and left at that:
    TRELLIS reads the MASK, and a mid-grey backdrop is not a mask. The matte is
    checked the way :func:`vtt.art.cutout` checks it — a matte that kept
    everything or nothing is worse than no matte, and here it is worse still,
    because the mesher would happily turn the whole rectangle into a slab.
    """
    if store is None:
        try:
            from . import ImageStore
            store = ImageStore()
        except Exception as e:
            print(f"[landmark3d] imagery unavailable: {e}")
            return None
    from .models import ImageKind
    try:
        res = store.ensure_image(
            # The MESHREF kind, never "map". A kind carries LoRAs, and rendered
            # as a MAP this came back a flat heraldic emblem — SDXL-Battlemaps
            # and HadesLevel@0.9 doing precisely what they are for, and no
            # wording survives a LoRA at that strength. TRELLIS then turned a
            # 2D emblem into a 2D emblem in relief: 1.0 x 0.02 x 0.95, correct
            # work on the wrong input. Measured, not reasoned about.
            ImageKind.MESHREF, reference_prompt(phrase),
            context="landmark-reference",
            ref_slug=f"landmark3d-{slug}", negative_extra=_MESH_NEGATIVE,
            # No house style either. The rim-lit, jewel-toned, painterly art
            # direction is right for everything a PLAYER looks at and wrong for
            # every one of these: nobody ever sees this picture, it is an
            # instrument reading, and dramatic light bakes a shadow into the
            # geometry.
            style_prompt=_MESH_STYLE,
            width=REFERENCE_PX, height=REFERENCE_PX, store_width=REFERENCE_PX,
            max_per_bucket=1)
    except Exception as e:
        print(f"[landmark3d] reference render failed for {slug}: {e}")
        return None
    if res is None or res.offline or not res.image:
        return None
    return _matte(res.image)


def _matte(image_bytes: bytes) -> Optional[bytes]:
    """rembg, checked. Returns RGBA PNG bytes, or None when there is nothing
    a mesher could use."""
    try:
        import io
        from PIL import Image
    except Exception as e:                            # pragma: no cover
        print(f"[landmark3d] Pillow unavailable: {e}")
        return None
    try:
        from rembg import new_session, remove
    except Exception as e:
        print(f"[landmark3d] rembg unavailable ({e}); a mesher needs a mask")
        return None
    try:
        cut = Image.open(io.BytesIO(
            remove(image_bytes, session=new_session("u2netp")))).convert("RGBA")
    except Exception as e:
        print(f"[landmark3d] matting failed: {e}")
        return None
    a = cut.getchannel("A")
    w, h = cut.size
    kept = sum(a.point(lambda v: 255 if v > 32 else 0).convert("L").getdata())
    frac = kept / (255.0 * w * h) if w and h else 0.0
    # Same window as the sprite matte, and the same reasoning: a matte that
    # kept the whole frame is a slab and one that kept nothing is a landmark
    # that silently isn't there. Both are worse than not trying.
    if not (0.05 <= frac <= 0.95):
        print(f"[landmark3d] matte kept {frac:.0%} — not a usable subject")
        return None
    buf = io.BytesIO()
    cut.save(buf, format="PNG")
    return buf.getvalue()


def generate(slug: str, phrase: str, *, store=None, seed: int = 0,
             client: Optional[TrellisClient] = None,
             base_url: Optional[str] = None,
             refresh: bool = False,
             texture_px: int = 1024) -> Optional[Path]:
    """Render, mesh and store one invented landmark. Returns the file or None.

    Never raises. Every failure here is a landmark that keeps the shape the
    board has always given it, and stopping play for one would be a far worse
    trade than a plain box.

    ``refresh`` DROPS THE STORED REFERENCE FIRST, and without it a reworded
    subject cannot reach a pixel. The reference is cached on the SLUG —
    `ensure_image(ref_slug=..., max_per_bucket=1)` hands back whatever is in
    that bucket whatever the prompt now says — so re-meshing a kind re-meshed
    the same old picture. `vtt/furniture.py` carries a note diagnosing "a stack
    of two crates" and rewording the subject to a single one; the reference row
    that fix was aimed at is dated 2026-08-21 and still reads "a stack of two
    wooden packing crates". The wording had been changed three times and drawn
    zero times, and the mesh was refused for a width nobody could argue with
    because it was measuring the picture it had always had.
    """
    if refresh:
        _forget_reference(slug, store=store)
    if not SLUG_RE.match(slug or ""):
        print(f"[landmark3d] refusing an unusable slug: {slug!r}")
        return None
    got = _existing(slug)
    if got is not None:
        return got
    if client is None:
        url = base_url or _configured_url()
        client = TrellisClient(base_url=url)
    if not client.available():
        # Told apart out loud: no server is the ordinary offline case, and a
        # server without the nodes is the pixi shim having been wiped by a
        # reinstall — which looks like nothing at all from the outside.
        print("[landmark3d] no TRELLIS.2 nodes reachable; "
              "the landmark keeps its stamped shape")
        return None

    picture = render_reference(phrase, slug, store=store)
    if not picture:
        return None
    started = time.time()
    with _LOCK:                     # one card, one mesh at a time
        try:
            mesh = client.image_to_mesh(picture, seed=seed, fmt=MESH_EXT,
                                        name_hint=slug, textured=True,
                                        texture_px=texture_px)
        except MeshServiceUnavailable as e:
            print(f"[landmark3d] {slug}: {e}")
            return None
        except Exception as e:
            print(f"[landmark3d] {slug} failed unexpectedly: {e}")
            return None
    out = _write(slug, phrase, mesh, seed=seed, seconds=time.time() - started)
    if out is not None:
        # The fit is cached on the reasoning that meshes are immutable within a
        # run, which a mesh that arrives mid-session breaks.
        try:
            from vtt import setpieces as sp
            sp.forget_mesh(slug)
        except Exception:
            pass
    return out


def _write(slug: str, phrase: str, mesh: bytes, *, seed: int = 0,
           seconds: float = 0.0) -> Optional[Path]:
    """Put the mesh on disk, whole or not at all, with its provenance beside it.

    Atomic because :func:`vtt.setpieces._obj_bounds` measures whatever is on
    disk and caches the answer: a half-written file measures, fits, and stands
    the landmark at a confidently wrong size, which is exactly the kind of
    silent wrongness the grid-is-truth rule exists to prevent.
    """
    kind = mesh_format(mesh)
    if kind is None:
        print(f"[landmark3d] {slug}: the mesher returned nothing readable")
        return None
    stood = _normalize(mesh, kind)
    if stood is None:
        print(f"[landmark3d] {slug}: the {kind.upper()} could not be stood "
              f"upright, and a landmark lying on its side is worse than a box")
        return None
    mesh = stood
    thin = _too_flat(mesh, kind)
    if thin is not None:
        # A mesher can only build what the picture SHOWS it, and the first real
        # run proved how that fails: asked for a gilded sow, the reference came
        # back a flat heraldic emblem, and TRELLIS faithfully produced a flat
        # heraldic emblem in relief — 1.0 x 0.02 x 0.95. Correct work on the
        # wrong input, and nothing in the pipeline complained. A landmark is
        # something a fight happens AROUND; a sheet standing on its edge is
        # worse than the stamped box it would replace, so it is refused here
        # rather than fitted, cached and drawn.
        print(f"[landmark3d] {slug}: the mesh is a sheet, not an object "
              f"({thin}) — the reference picture was probably flat")
        return None
    d = root()
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".{slug}.{kind}.part"
        tmp.write_bytes(mesh)
        final = d / f"{slug}.{kind}"
        os.replace(tmp, final)
        # An older run may have left a geometry-only mesh under the same slug.
        # Both would be found by `_existing`, and the newer one wins there, but
        # a stale file that nothing will ever open again is a trap for whoever
        # reads this directory next.
        for other in MESH_EXTS:
            if other != kind:
                try:
                    (d / f"{slug}.{other}").unlink()
                except OSError:
                    pass
        (d / f"{slug}.json").write_text(json.dumps({
            "slug": slug, "phrase": phrase, "seed": seed,
            "seconds": round(seconds, 1), "bytes": len(mesh),
            "made": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": "TRELLIS.2 (image->shape) from a rendered reference",
            "format": kind,
            "normalized": ("Z-up -> Y-up via a root node; materials, UVs and "
                           "normals kept" if kind == "glb"
                           else "Z-up -> Y-up, geometry only"),
        }, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"[landmark3d] could not store {slug}: {e}")
        return None
    print(f"[landmark3d] {slug}: {len(mesh)/1024:.0f} KB in {seconds:.0f}s "
          f"— {phrase!r}")
    return final


#: Which way is up in what the mesher hands back. **Z**, measured — the first
#: real mesh came out with its plinth as a flat slab at minimum z and the figure
#: standing along +z, while y was the front-to-back axis. The board is Y-up and
#: nothing downstream rotates anything: `SetPiece.up` exists, `mesh_fit` uses it
#: for the scale and the floor offset, and NO renderer reads it — so a Z-up file
#: reaches the board lying on its side, correctly scaled.
#:
#: Fixed HERE, once, at write time, rather than by teaching three readers a
#: second convention. Same argument as `vtt/hull.py`: a thing both languages
#: must agree about is settled by one of them doing it.
TRELLIS_UP = "z"


def mesh_format(mesh: bytes) -> Optional[str]:
    """Which of our formats these bytes are, judged on the bytes themselves.

    On the CONTENT rather than on what was asked for, because the export node
    is the thing that decides and it has surprised us before — this is the same
    node that reports ``outputs: {}`` for a file it definitely wrote.
    """
    if not mesh:
        return None
    if mesh[:4] == b"glTF":
        return "glb"
    if b"v " in mesh[:200_000]:
        return "obj"
    return None


#: Stand a Z-up mesh on its feet: a rotation of -90 degrees about X, which is
#: exactly the ``(x, y, z) -> (x, z, -y)`` the OBJ path bakes into its
#: vertices, written as the quaternion glTF wants.
_Y_UP_QUAT = [-0.7071067811865476, 0.0, 0.0, 0.7071067811865476]


def _normalize(mesh: bytes, kind: str) -> Optional[bytes]:
    """Put the mesh into the form the rest of the project assumes."""
    if kind == "glb":
        return _normalize_glb(mesh)
    return _normalize_obj(mesh)


def _normalize_glb(mesh: bytes) -> Optional[bytes]:
    """Stand a binary glTF up, and CHANGE NOTHING ELSE ABOUT IT.

    The OBJ path rewrites vertices and throws the rest away. Doing that here
    would defeat the entire reason for moving to this format: the texture, the
    UVs that address it and the normals that light it are the payload, and a
    normalizer that dropped them would hand back the flat-shaded mesh we came
    here to stop drawing.

    So the rotation is expressed rather than applied — a new node above each
    scene's roots, carrying the quaternion. Every reader that walks the node
    hierarchy honours it for free, which is both of ours: three.js's
    ``GLTFLoader`` in the browser, and ``setpieces.glb_bounds_of`` on the
    server. The BIN chunk is not touched at all, so no accessor is decoded, no
    ``min``/``max`` goes stale, and 4 MB of mesh and texture are copied once.

    Returns None if the container is not one we can read, which the writer
    treats as a refusal — a mesh we cannot stand up arrives on the board lying
    on its side, correctly scaled and silently wrong, and that is the shape of
    failure this project refuses rather than ships.
    """
    from vtt import setpieces as sp
    doc = sp.glb_document(mesh)
    span = sp.glb_span(mesh)
    if doc is None or span is None:
        return None
    off, length = span
    if off != 12 + 8:
        # The JSON chunk is the first one by spec. If it is not, anything ahead
        # of it would be dropped by the repack below, so decline instead.
        return None
    nodes = doc.get("nodes")
    scenes = doc.get("scenes")
    if not isinstance(nodes, list) or not isinstance(scenes, list) or not scenes:
        return None
    # One wrapper PER SCENE: a node may not be the child of two parents, so a
    # single shared wrapper would be invalid glTF the moment there were two.
    for scene in scenes:
        if not isinstance(scene, dict):
            return None
        roots = scene.get("nodes")
        if not isinstance(roots, list) or not roots:
            continue
        nodes.append({"name": "oracle-y-up",
                      "rotation": list(_Y_UP_QUAT),
                      "children": list(roots)})
        scene["nodes"] = [len(nodes) - 1]
    body = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    body += b" " * (-len(body) % 4)             # chunks are 4-byte aligned
    rest = mesh[off + length:]
    total = 12 + 8 + len(body) + len(rest)
    return (b"glTF" + struct.pack("<II", 2, total)
            + struct.pack("<II", len(body), sp.GLB_JSON_CHUNK) + body + rest)


def _normalize_obj(mesh: bytes) -> bytes:
    """Stand the mesh up, and strip it to what our three readers actually use.

    Two jobs, and they belong together because both are "put this file into the
    form the rest of the project already assumes".

    **Z-up to Y-up**: ``(x, y, z) -> (x, z, -y)``, a proper rotation (its
    determinant is +1), so face winding — and therefore every normal the
    renderer derives from it — is unchanged.

    **v and f only**, and this is the path for a mesh that has nothing else to
    lose. It was once the only path, on the reasoning that a mesh's readers
    wanted positions and nothing more — and the cost of that reasoning was
    every generated landmark being drawn in one flat averaged colour, which is
    why :func:`_normalize_glb` exists and why it strips nothing. Here the
    ``mtllib`` line is still worse than dead weight: it names a file beside the
    mesh in ComfyUI's output that this route does not serve, so a loader that
    did resolve materials would fetch a 404 on every landmark. Dropping the UVs
    means the face lines have to be rewritten to bare vertex indices, which is
    the whole of why this is a rewrite rather than a filter.
    """
    out: list[bytes] = [b"# normalized by imagery/landmark3d.py: Z-up -> Y-up, "
                        b"geometry only\n"]
    for line in mesh.splitlines():
        if line.startswith(b"v "):
            parts = line.split()
            if len(parts) >= 4:
                try:
                    x, y, z = (float(parts[1]), float(parts[2]), float(parts[3]))
                except ValueError:
                    continue
                out.append(b"v %.6f %.6f %.6f\n" % (x, z, -y))
        elif line.startswith(b"f "):
            idx = [tok.split(b"/")[0] for tok in line.split()[1:]]
            idx = [i for i in idx if i]
            if len(idx) >= 3:
                out.append(b"f " + b" ".join(idx) + b"\n")
    return b"".join(out)


#: How thin a landmark may be before it is a picture rather than a thing:
#: its smallest dimension against its largest. A stone arch is nowhere near
#: this; an emblem, a plaque or a bas-relief is well under it.
MIN_THICKNESS = 0.08


def _too_flat(mesh: bytes, kind: str = "obj") -> Optional[str]:
    """A description of the flatness, or None if the mesh has real volume."""
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    seen = 0
    if kind == "glb":
        from vtt import setpieces as sp
        doc = sp.glb_document(mesh)
        bounds = sp.glb_bounds_of(doc) if doc is not None else None
        if bounds is None:
            return "no measurable geometry"
        (lo, hi) = ([*bounds[0]], [*bounds[1]])
        # A glTF POSITION accessor states its own extent, so there is nothing
        # to count — the box either exists or it does not.
        seen = 8
    else:
        for line in mesh.splitlines():
            if not line.startswith(b"v "):
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                xyz = [float(parts[1]), float(parts[2]), float(parts[3])]
            except ValueError:
                continue
            seen += 1
            for i in range(3):
                lo[i] = min(lo[i], xyz[i])
                hi[i] = max(hi[i], xyz[i])
    if seen < 8:
        return f"only {seen} vertices"
    span = [hi[i] - lo[i] for i in range(3)]
    widest = max(span)
    if widest <= 0:
        return "no extent at all"
    ratio = min(span) / widest
    if ratio < MIN_THICKNESS:
        return (f"{span[0]:.2f} x {span[1]:.2f} x {span[2]:.2f}, "
                f"thinnest side {ratio:.1%} of the widest")
    return None


def _configured_url() -> str:
    try:
        from game_config import load_config
        return load_config().imagery.base_url
    except Exception:
        return "http://127.0.0.1:8188"


# --------------------------------------------------------------------------
# Asking for one from play
# --------------------------------------------------------------------------

def request(slug: str, phrase: str, *, seed: int = 0, store=None) -> bool:
    """Ask for a landmark's mesh in the background. True if one was started.

    The board has already been drawn and handed to the table by the time this
    is called; the mesh joins the NEXT frame that asks for state. That is the
    same contract the isometric painting keeps, and for the same reason — a
    fight must not wait on a GPU.
    """
    if not enabled() or has_mesh(slug) or not SLUG_RE.match(slug or ""):
        return False
    with _INFLIGHT_LOCK:
        if slug in _INFLIGHT:
            return False
        _INFLIGHT.add(slug)

    def run() -> None:
        try:
            generate(slug, phrase, seed=seed, store=store)
        finally:
            with _INFLIGHT_LOCK:
                _INFLIGHT.discard(slug)

    threading.Thread(target=run, name=f"landmark3d-{slug}", daemon=True).start()
    return True
