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

# The fit is cached on the reasoning that meshes are immutable within a run —
# which a mesh that lands three minutes into a session breaks.
stale = sp.mesh_fit(piece.slug)
sp.forget_mesh()
fit = sp.mesh_fit(piece.slug)
check("dropping the cache is what lets the board see it",
      fit is not None, f"before={stale} after={fit}")
assert fit is not None

# A 4-unit cube standing 9 ft on 5-ft squares: 9/4/5 squares per unit.
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


# ---------------------------------------------------------------------------
# A collected mesh is never displaced
# ---------------------------------------------------------------------------
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
check("it asks for an OBJ, which all three of our readers speak",
      any(n["inputs"].get("file_format") == "obj" for n in g.values()))
check("...and exports, or the job produces nothing to collect",
      any(n["class_type"].startswith("Trellis2Export") for n in g.values()))
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
