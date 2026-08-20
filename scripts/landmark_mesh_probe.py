"""Make one invented landmark real, end to end, on the GPU.

    ./.venv/Scripts/python.exe scripts/landmark_mesh_probe.py "a gilded sow on a stone plinth"

The Windows interpreter, because this talks to ComfyUI (see CLAUDE.md). Prints
what each stage cost and what it produced, and leaves the mesh in
``generated/setpieces/`` where the board will find it. Everything it exercises
is the real path — the same slug the DM's phrase produces, the same reference
render, the same matte, the same fit the two renderers are handed.
"""
from __future__ import annotations
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from imagery import landmark3d as L
from imagery.mesh_client import TrellisClient
from vtt import setpieces as sp

phrase = " ".join(sys.argv[1:]) or "a gilded sow on a stone plinth"
piece = sp.named_feature(phrase)
if piece is None:
    raise SystemExit(f"{phrase!r} does not read as a thing standing in a room")
print(f"phrase : {phrase!r}")
print(f"slug   : {piece.slug}   ({piece.width}x{piece.depth} squares, "
      f"{piece.height_ft} ft, stamps {piece.stamped_code!r})")

client = TrellisClient()
print(f"comfy  : {client.base_url}  nodes={client.available()}")
if not client.available():
    raise SystemExit("no TRELLIS.2 nodes — is ComfyUI up with the packs registered?")

t0 = time.time()
pic = L.render_reference(phrase, piece.slug)
print(f"picture: {'None' if not pic else f'{len(pic)/1024:.0f} KB'} "
      f"in {time.time()-t0:.0f}s")
if not pic:
    raise SystemExit("no usable reference picture")
Path("generated").mkdir(exist_ok=True)
Path(f"generated/{piece.slug}-ref.png").write_bytes(pic)

t1 = time.time()
mesh = client.image_to_mesh(pic, seed=7, fmt="obj", name_hint=piece.slug)
print(f"mesh   : {len(mesh)/1024:.0f} KB in {time.time()-t1:.0f}s")

out = L._write(piece.slug, phrase, mesh, seed=7, seconds=time.time()-t1)
print(f"stored : {out}")
sp.forget_mesh()
fit = sp.mesh_fit(piece.slug)
print(f"fit    : {fit}")
placed = sp.Placed(slug=piece.slug, x=4, y=4, yaw=0).instance()
print(f"state  : mesh={placed.get('mesh')} scale={placed.get('scale')}")
verts = mesh.count(b"\nv ")
faces = mesh.count(b"\nf ")
print(f"geometry: {verts} verts / {faces} faces")
print(f"total  : {time.time()-t0:.0f}s")
