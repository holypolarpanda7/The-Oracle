"""A landmark the DM INVENTED can have a shape, and never has to.

The board draws a wall, a cliff, a crate and a hull from (tile code, skin, x, z)
put through a shape table, because those are things a RULE can describe. A
catalogue of modelled meshes covers the named exceptions — a ruined arch, a
colossal seated guardian — and a catalogue is a fixed list. The DM's own gilded
sow is not on it, and has been standing on the board as a 2x2 stamped box:
mechanically exact and visually nothing.

What this covers is the whole path around that, and — deliberately — it covers
it with NO GPU, NO ComfyUI and no model of any kind. The mesh is a few triangles
written here in the test, because every question worth asking is about what the
code does with a file:

  * the DM's sentence still produces the same piece, and stamps the same tiles;
  * with no mesh anywhere, the board reports what it always reported, and the
    landmark is drawn from its tiles — the degrade path IS the old behaviour;
  * a generated mesh arriving mid-session is picked up (the fit is cached, and
    a remembered ``None`` would keep the board flat until a restart);
  * the fit is measured off the DECLARED HEIGHT, so a mesh in arbitrary units
    stands the right size;
  * a generated mesh is served over the BACKEND's route and a collected one
    over vite's, because vite has never heard of a file made five minutes ago;
  * a catalogue piece can never be displaced by something this machine invented
    under the same slug;
  * a URL can never say ``../``;
  * and the mesh changes NO rule: the tiles a piece stamps are its entire
    mechanical content before and after.

    uv run python scripts/landmark_mesh_smoke.py
"""
from __future__ import annotations

import json
import os
import shutil
import struct
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


from imagery import landmark3d as L          # noqa: E402
from imagery.mesh_client import TrellisClient, _first_path  # noqa: E402
from vtt import setpieces as sp              # noqa: E402
from vtt.mapgen import generate_map          # noqa: E402


# A cube 4 units on a side, sitting on y=0. Arbitrary units on purpose: the
# whole point of the fit is that a mesh's own scale means nothing and the
# piece's declared height means everything.
def cube_obj(size: float = 4.0) -> str:
    s = size
    vs = [(0, 0, 0), (s, 0, 0), (s, 0, s), (0, 0, s),
          (0, s, 0), (s, s, 0), (s, s, s), (0, s, s)]
    fs = [(1, 2, 3, 4), (5, 8, 7, 6), (1, 5, 6, 2),
          (2, 6, 7, 3), (3, 7, 8, 4), (4, 8, 5, 1)]
    return ("# a test landmark\n"
            + "".join(f"v {x} {y} {z}\n" for x, y, z in vs)
            + "".join("f " + " ".join(str(i) for i in f) + "\n" for f in fs))


# ---------------------------------------------------------------------------
# The DM's sentence
# ---------------------------------------------------------------------------
print(f"\n{BOLD}1. what the DM described{OFF}")

PHRASE = "a gilded sow"
piece = sp.named_feature(PHRASE)
check("a phrase the catalogue never heard of becomes a landmark",
      piece is not None, PHRASE)
assert piece is not None
check("...and the same phrase is the same landmark tomorrow",
      sp.named_feature(PHRASE).slug == piece.slug, piece.slug)
check("...rebuilt by slug + name in a process that never saw it",
      (sp.piece(piece.slug, PHRASE) or sp.SetPiece("", "", None, (), 0)).slug
      == piece.slug)
check("a description of the ROOM is still not a thing in it",
      sp.named_feature("a smoky taproom") is None)

TILES_BEFORE = piece.tiles
CODE_BEFORE = piece.stamped_code

# The real path: a DM opens a board and names the landmark outright.
from vtt.scene import _landmarks_from                       # noqa: E402
slugs = _landmarks_from(PHRASE, invent=True)
check("the board's own resolver invents it from a DM landmark=",
      piece.slug in slugs, str(slugs))
check("...and the same words read as PLACE text invent nothing",
      piece.slug not in _landmarks_from(PHRASE, invent=False))
gen = generate_map("tavern", width=24, height=18, seed=11, landmarks=slugs)
placed = [p for p in (gen.setpieces or [])
          if (p.get("slug") if isinstance(p, dict) else p.slug) == piece.slug]
check("...and it is standing on the board", bool(placed),
      f"{len(gen.setpieces or [])} set piece(s)")


# ---------------------------------------------------------------------------
# With no mesh: exactly what the board has always done
# ---------------------------------------------------------------------------
print(f"\n{BOLD}2. with no mesh at all — the degrade path{OFF}")

scratch = Path(tempfile.mkdtemp(prefix="oracle-mesh-smoke-"))
gen_root = scratch / "setpieces"
gen_root.mkdir(parents=True)
_real_root = sp.generated_root


def fake_root() -> Path:
    return gen_root


sp.generated_root = fake_root                 # type: ignore[assignment]
L.root = fake_root                            # type: ignore[assignment]
sp.forget_mesh()

inst = sp.Placed(slug=piece.slug, x=4, y=4, yaw=0).instance()
check("the piece reports no mesh", inst.get("mesh") is None, str(inst.get("mesh")))
check("...and no scale for a renderer to apply", "scale" not in inst)
check("...but it is still a landmark with a name and a footprint",
      inst.get("name") == PHRASE and inst.get("w") == piece.width,
      f"{inst.get('name')!r} {inst.get('w')}x{inst.get('d')}")
check("...standing on the tiles it always stamped",
      piece.tiles == TILES_BEFORE and piece.stamped_code == CODE_BEFORE,
      f"{piece.stamped_code!r} x{len(piece.tiles)}")


# ---------------------------------------------------------------------------
# A mesh arrives mid-session
# ---------------------------------------------------------------------------
print(f"\n{BOLD}3. a mesh arrives while the table is playing{OFF}")

out = L._write(piece.slug, PHRASE, cube_obj(4.0).encode(), seed=3, seconds=1.0)
check("the writer stores it", out is not None and out.is_file(), str(out))
check("...with its provenance beside it, so a stale one can be told apart",
      (gen_root / f"{piece.slug}.json").is_file())
check("a truncated mesh is refused rather than measured",
      L._write("feature-empty", "x", b"# nothing here\n") is None)

# The failure the first real run actually produced: asked for a gilded sow the
# reference came back a flat heraldic emblem, and the mesher faithfully built a
# flat heraldic emblem in relief — 1.00 x 0.02 x 0.95. Correct work on the
# wrong input, and nothing anywhere complained. A landmark is something a fight
# happens AROUND; a sheet on its edge is worse than the box it replaces.
sheet = "".join(f"v {x} {0.002 * z} {z}\n"
                for x in (0.0, 1.0) for z in range(8)).encode()
check("a mesh that came back a SHEET is refused, not stood up",
      L._write("feature-sheet00", "a gilded sow", sheet) is None)
check("...and the reason names the shape, not just 'failed'",
      "thinnest side" in (L._too_flat(sheet) or ""), str(L._too_flat(sheet)))
solid = "".join(f"v {x} {y} {z}\n" for x in (0.0, 1.0)
                for y in (0.0, 0.9) for z in (0.0, 1.0)).encode()
check("...while a thing with real volume passes", L._too_flat(solid) is None)


# ---------------------------------------------------------------------------
# The format a landmark actually arrives in
# ---------------------------------------------------------------------------
print(f"\n{BOLD}3a. a textured GLB, stood up without being taken apart{OFF}")


def tiny_glb(sx: float = 1.0, sy: float = 2.0, sz: float = 3.0) -> bytes:
    """A minimal but VALID binary glTF: one box, with UVs and a material.

    Hand-built because the point of this section is that our normalizer edits
    the container correctly, and a fixture that came out of the mesher would
    make the test depend on a GPU.
    """
    verts = [(x * sx, y * sy, z * sz)
             for x in (0.0, 1.0) for y in (0.0, 1.0) for z in (0.0, 1.0)]
    blob = b"".join(struct.pack("<fff", *v) for v in verts)
    blob += b"\x00" * (-len(blob) % 4)
    doc = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0},
                                    "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor":
                                                [1, 1, 1, 1]}}],
        "accessors": [{"bufferView": 0, "componentType": 5126,
                       "count": len(verts), "type": "VEC3",
                       "min": [0.0, 0.0, 0.0], "max": [sx, sy, sz]}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0,
                         "byteLength": len(blob)}],
        "buffers": [{"byteLength": len(blob)}],
    }
    body = json.dumps(doc, separators=(",", ":")).encode()
    body += b" " * (-len(body) % 4)
    total = 12 + 8 + len(body) + 8 + len(blob)
    return (b"glTF" + struct.pack("<II", 2, total)
            + struct.pack("<II", len(body), 0x4E4F534A) + body
            + struct.pack("<II", len(blob), 0x004E4942) + blob)


raw = tiny_glb()
check("the format is judged on the BYTES, not on what we asked for",
      L.mesh_format(raw) == "glb" and L.mesh_format(b"v 0 0 0\n") == "obj"
      and L.mesh_format(b"") is None)
stood = L._normalize(raw, "glb")
check("a glTF is stood up rather than rewritten", stood is not None)
# The whole argument for expressing the rotation instead of applying it: the
# BIN chunk is the texture, the uvs and the normals, and a normalizer that
# rewrote vertices would have to decode and re-encode all of it.
check("...and the BIN chunk is not touched at all — texture, uvs and normals "
      "are the payload",
      stood[-len(raw.split(b"BIN")[-1]):] == raw[-len(raw.split(b"BIN")[-1]):]
      if b"BIN" in raw else True)
doc_before = sp.glb_document(raw)
doc_after = sp.glb_document(stood)
check("...and it is still a document we can read", doc_after is not None)
lo0, hi0 = sp.glb_bounds_of(doc_before)
lo1, hi1 = sp.glb_bounds_of(doc_after)
# Z-up to Y-up is (x, y, z) -> (x, z, -y), the same mapping the OBJ path bakes
# into its vertices. A box maps to a box, so the corners are exact.
want_lo = (lo0[0], lo0[2], -hi0[1])
want_hi = (hi0[0], hi0[2], -lo0[1])
check("the mesh is Y-UP afterwards — the same rotation the OBJ path bakes in",
      all(abs(lo1[i] - want_lo[i]) < 1e-6 and abs(hi1[i] - want_hi[i]) < 1e-6
          for i in range(3)),
      f"{[round(v, 3) for v in lo1]} .. {[round(v, 3) for v in hi1]}")
check("...and its own materials survived, which is the entire point",
      bool((doc_after or {}).get("materials")))
check("a POSITION accessor's stated extent is what gets measured",
      sp.glb_bounds_of(sp.glb_document(tiny_glb(1.0, 5.0, 1.0)))[1][1] == 5.0)
check("a sheet in GLB is refused exactly as a sheet in OBJ is",
      L._too_flat(tiny_glb(1.0, 0.01, 1.0), "glb") is not None
      and L._too_flat(raw, "glb") is None)
check("something that is not a glTF at all is declined, not guessed at",
      L._normalize_glb(b"glTFnonsense") is None
      and L._normalize_glb(b"") is None)

# And the round trip through the writer: the extension, the URL and the fit all
# follow the format, or a textured landmark is stored somewhere nothing serves.
tex_piece = sp.named_feature("an iron-banded packing crate")
gout = L._write(tex_piece.slug, tex_piece.name, raw, seed=1, seconds=1.0)
check("the writer stores it as a GLB", gout is not None and gout.suffix == ".glb",
      str(gout))
check("...and finds it again whatever format it is in",
      L.has_mesh(tex_piece.slug) and L.mesh_file(tex_piece.slug) == gout)
sp.forget_mesh()
check("...and the board ships it over the backend's route, as a GLB",
      sp.Placed(slug=tex_piece.slug, x=4, y=4, yaw=0).instance().get("mesh")
      == f"/vtt/setpiece/{tex_piece.slug}.glb",
      str(sp.Placed(slug=tex_piece.slug, x=4, y=4, yaw=0).instance().get("mesh")))
# An installation that generated landmarks under the old workflow still has
# them, and they are still perfectly good geometry.
(gen_root / f"{tex_piece.slug}.obj").write_text(cube_obj(4.0))
(gen_root / f"{tex_piece.slug}.glb").unlink()
sp.forget_mesh()
check("an OBJ from the old workflow is still found, served and fitted",
      sp.Placed(slug=tex_piece.slug, x=4, y=4, yaw=0).instance().get("mesh")
      == f"/vtt/setpiece/{tex_piece.slug}.obj")

# The fit is cached on the reasoning that meshes are immutable within a run —
# which a mesh that lands three minutes into a session breaks.
stale = sp.mesh_fit(piece.slug)
sp.forget_mesh()
fit = sp.mesh_fit(piece.slug)
check("dropping the cache is what lets the board see it",
      fit is not None, f"before={stale} after={fit}")
assert fit is not None

# A 4-unit cube standing 9 ft on 5-ft squares: 9/4/5 squares per unit. The cube
# is as wide as it is tall, so the footprint does not bind and the height wins.
want = (sp.FEATURE_HEIGHT_FT / 4.0) / 5.0
check("the fit comes off the DECLARED height, not the mesh's own units",
      abs(fit["scale"] - want) < 1e-9, f"{fit['scale']:.6f} vs {want:.6f}")
check("...and the pivot stands it on the floor, centred on its squares",
      abs(fit["pivot"][1]) < 1e-9
      and abs(fit["pivot"][0] - 2.0 * fit["scale"]) < 1e-9,
      str([round(v, 4) for v in fit["pivot"]]))

inst = sp.Placed(slug=piece.slug, x=4, y=4, yaw=0).instance()
check("the board now ships a mesh for it",
      inst.get("mesh") == f"/vtt/setpiece/{piece.slug}.obj", str(inst.get("mesh")))
check("...over the BACKEND's route, because vite serves only public/",
      str(inst.get("mesh")).startswith("/vtt/setpiece/"))
check("...and the scale it ships is the one measured here",
      inst.get("scale") == fit["scale"])
check("gaining a shape changed no rule",
      piece.tiles == TILES_BEFORE and piece.stamped_code == CODE_BEFORE)

# A landmark nobody authored has no stated fiction to protect: both its numbers
# are defaults, and the first real mesh — a sow on a broad plinth, 1.00 x 0.44 x
# 1.00 — scaled to nine feet tall spills five feet onto every square around it.
# The catalogue's rule is the opposite and must STAY the opposite, or every tall
# thing becomes a dwarf again.
wide = ("# a slab\n"
        + "".join(f"v {x} {y} {z}\n" for x in (0.0, 4.0)
                  for y in (0.0, 1.0) for z in (0.0, 4.0))
        + "f 1 2 3\nf 2 3 4\n")
(gen_root / f"{piece.slug}.obj").write_text(wide)
sp.forget_mesh()
wfit = sp.mesh_fit(piece.slug)
across = 4.0 * wfit["scale"]
check("an INVENTED landmark is not allowed to spill off its own squares",
      abs(across - piece.width) < 1e-6,
      f"{across * 5:.1f} ft across a {piece.width * 5} ft footprint")
tall_ft = 1.0 * wfit["scale"] * 5
check("...it gives up height instead, which is the cheaper of the two",
      tall_ft < sp.FEATURE_HEIGHT_FT, f"{tall_ft:.1f} ft tall")
if catalogued_early := next((s2 for s2 in sp.CATALOGUE
                             if sp.mesh_path(s2) is not None
                             and not sp.is_generated(sp.mesh_path(s2))), ""):
    cfit = sp.mesh_fit(catalogued_early)
    cb = sp._obj_bounds(sp.mesh_path(catalogued_early))
    up = sp.CATALOGUE[catalogued_early].up
    tall = (cb[1][1] - cb[0][1]) if up == "y" else (cb[1][2] - cb[0][2])
    check("...while a CATALOGUED height is a stated fact and still wins",
          abs(tall * cfit["scale"] * 5
              - sp.CATALOGUE[catalogued_early].height_ft) < 0.01,
          f"{tall * cfit['scale'] * 5:.1f} ft vs declared "
          f"{sp.CATALOGUE[catalogued_early].height_ft} ft")
(gen_root / f"{piece.slug}.obj").write_text(cube_obj(4.0))
sp.forget_mesh()


# ---------------------------------------------------------------------------
# A collected mesh is never displaced
# ---------------------------------------------------------------------------
print(f"\n{BOLD}3b. the file is put into the form the board already assumes{OFF}")

# TRELLIS.2 hands back a Z-UP mesh — measured, on the first real one: the sow's
# plinth was a flat slab at minimum z and the animal stood along +z. The board
# is Y-up, `SetPiece.up` is read by mesh_fit and by NO renderer, and nothing
# anywhere rotates anything — so an unrotated file reaches the board lying on
# its side, correctly scaled. Fixed once, at write time, rather than by
# teaching three readers a second convention.
zup = ("mtllib material.mtl\nusemtl material_0\n"
       "vt 0.0 0.0\nvt 1.0 0.0\nvt 1.0 1.0\n"
       "v 0 0 0\nv 2 0 0\nv 2 3 0\nv 0 0 9\n"
       "f 1/1 2/2 3/3\n").encode()
norm = L._normalize_obj(zup)
text = norm.decode()
vs = [tuple(float(t) for t in ln.split()[1:])
      for ln in text.splitlines() if ln.startswith("v ")]
check("a Z-up mesh is stood up: (x, y, z) -> (x, z, -y)",
      vs[3] == (0.0, 9.0, 0.0) and vs[2] == (2.0, 0.0, -3.0), str(vs))
check("...and the rotation is PROPER, so face winding survives it",
      [ln for ln in text.splitlines() if ln.startswith("f ")] == ["f 1 2 3"])
check("only geometry is kept — an OBJ carries no texture to lose",
      "vt " not in text and "mtllib" not in text and "usemtl" not in text)
check("...which is also why the mtllib goes: it names a file no route serves",
      "material.mtl" not in text)


print(f"\n{BOLD}4. a modeller's answer outranks a generated one{OFF}")

catalogued = next((s for s in sp.CATALOGUE
                   if sp.mesh_path(s) is not None
                   and not sp.is_generated(sp.mesh_path(s))), "")
if catalogued:
    (gen_root / f"{catalogued}.obj").write_text(cube_obj(1.0))
    sp.forget_mesh()
    got = sp.mesh_path(catalogued)
    check("a generated file cannot take a catalogued slug",
          got is not None and not sp.is_generated(got), str(got))
    served = sp.Placed(slug=catalogued, x=1, y=1, yaw=0).instance().get("mesh")
    check("...and it is still served as a static asset",
          str(served).startswith("/assets/setpieces/"), str(served))
else:
    check("a collected mesh to compare against", False, "no pack unzipped")


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------
print(f"\n{BOLD}5. what a URL may ask for{OFF}")

check("the landmark's own mesh is servable",
      L.mesh_file(piece.slug) is not None)
for bad in ("../../etc/passwd", "..", "a/b", "feature-x/../../x",
            "", "FEATURE-Upper", "x" * 200):
    if L.mesh_file(bad) is not None:
        check(f"a route refuses {bad!r}", False)
        break
else:
    check("a route refuses traversal, absolutes and anything odd", True)
check("a slug with no file is a miss, not an error",
      L.mesh_file("feature-0000000000") is None)


# ---------------------------------------------------------------------------
# The mesher's own contract, checked without a mesher
# ---------------------------------------------------------------------------
print(f"\n{BOLD}6. the graph we would send{OFF}")

g = TrellisClient(resolution="512", face_count=40000).build_graph(
    "sow.png", seed=7, fmt="obj")
check("it is API format — node ids to class_type + inputs",
      all(isinstance(k, str) and "class_type" in v and "inputs" in v
          for k, v in g.items()), f"{len(g)} nodes")
bad = [f"{nid}.{k}" for nid, node in g.items()
       for k, v in node["inputs"].items()
       if isinstance(v, list) and (str(v[0]) not in g)]
check("...and every wire lands on a node that exists", not bad, str(bad))
check("it asks for an OBJ, which both of our geometry readers speak",
      any(n["inputs"].get("file_format") == "obj" for n in g.values()))
check("...and exports, or the job produces nothing to collect",
      any(n["class_type"].startswith("Trellis2Export") for n in g.values()))

# The graph a landmark ACTUALLY gets. The geometry-only one above is still the
# contract for anything that wants bare shape; this is the one `generate` sends,
# and the difference between them is the whole reason a generated landmark
# stopped being drawn in one flat averaged colour.
tg = TrellisClient(resolution="512", face_count=40000).build_textured_graph(
    "sow.png", seed=7)
check("the textured graph is API format too",
      all(isinstance(k, str) and "class_type" in v and "inputs" in v
          for k, v in tg.items()), f"{len(tg)} nodes")
bad = [f"{nid}.{k}" for nid, node in tg.items()
       for k, v in node["inputs"].items()
       if isinstance(v, list) and (str(v[0]) not in tg)]
check("...and every wire lands on a node that exists", not bad, str(bad))
check("it PAINTS — a shape pass alone is what left every landmark flat",
      any(n["class_type"] == "Trellis2ShapeToTexturedMesh" for n in tg.values()))
check("...onto an atlas, because a texture with no uvs addresses nothing",
      any(n["class_type"] == "Trellis2UVUnwrap" for n in tg.values()))
check("...baked by the rasterizer, which is what joins the two",
      any(n["class_type"] == "Trellis2RasterizePBR" for n in tg.values()))
check("it exports GLB, the only format here that can carry a texture at all",
      any(n["inputs"].get("file_format") == "glb" for n in tg.values()))
# The export must read the BAKED mesh. Wired to the decimated one instead it
# would produce a perfectly good untextured mesh and report success — which is
# exactly the shape of failure this whole pipeline keeps being bitten by.
exp = [n for n in tg.values() if n["class_type"].startswith("Trellis2Export")]
rast = [nid for nid, n in tg.items() if n["class_type"] == "Trellis2RasterizePBR"]
check("...and the export reads what was BAKED, not what was decimated",
      len(exp) == 1 and str(exp[0]["inputs"]["trimesh"][0]) in rast,
      str(exp[0]["inputs"]["trimesh"]) if exp else "no export")
check("the mask is the alpha INVERTED, which is what LoadImage emits",
      g["2"]["class_type"] == "InvertMask" and g["2"]["inputs"]["mask"] == ["1", 1])
check("the seed reaches the shape model",
      g["5"]["inputs"]["seed"] == 7)
check("the mesh is decimated on the GPU, not shipped whole to a browser",
      g["6"]["inputs"]["target_face_count"] == 40000)

hist = {"7": {"result": ["D:\\ComfyUI\\output\\oracle_mesh\\sow_00001_.obj"]},
        "9": {"images": [{"filename": "x.png"}]}}
check("a written file is found wherever ComfyUI files it",
      (_first_path(hist) or "").endswith(".obj"), str(_first_path(hist)))
check("...and a history with no mesh in it reports none",
      _first_path({"9": {"images": [{"filename": "x.png"}]}}) is None)


# ---------------------------------------------------------------------------
# Off by default
# ---------------------------------------------------------------------------
print(f"\n{BOLD}7. nobody pays for this by accident{OFF}")

os.environ.pop("ORACLE_LANDMARK_MESH", None)
check("meshing is off unless an operator turns it on", not L.enabled())
os.environ["ORACLE_LANDMARK_MESH"] = "1"
check("...and on when they do", L.enabled())
check("a request for a mesh that already exists starts nothing",
      L.request(piece.slug, PHRASE) is False)
os.environ.pop("ORACLE_LANDMARK_MESH", None)
check("...and with it off, nothing is ever started",
      L.request("feature-1111111111", "a brass owl") is False)

sp.generated_root = _real_root                # type: ignore[assignment]
shutil.rmtree(scratch, ignore_errors=True)

print()
if _fails:
    print(f"{RED}{len(_fails)} check(s) failed:{OFF} " + "; ".join(_fails))
    raise SystemExit(1)
print(f"{GREEN}an invented landmark can have a shape, and plays fine without one{OFF}")
