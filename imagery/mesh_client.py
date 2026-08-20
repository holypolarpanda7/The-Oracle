"""Client for TRELLIS.2 image->3D, running as custom nodes inside ComfyUI.

The board can draw anything a RULE can describe — a wall, a cliff, a hull, a
crate — because all of those are (tile code, skin, x, z) put through a shape
table. It stops at a colossal seated guardian with a human face, and that is a
capability limit rather than an efficiency one: no hash produces one. Until now
the answer was a catalogue of CC0 meshes somebody had already modelled, which
covers a fixed list and nothing else, so a landmark the DM INVENTED fell back to
a stamped 2x2 box with a name on it.

This is the other door. A picture of the thing is something the project already
knows how to make — it renders one for every item in the catalogue and every
piece of wreckage on a board — and TRELLIS.2 turns a picture into geometry. What
comes back is a mesh like any other: it is fitted by ``setpieces.mesh_fit`` on
the server, drawn by the isometric board, rasterized into the depth map, and
carries NO mechanical content whatsoever. The tiles the piece stamps remain its
entire rules meaning, exactly as for a downloaded mesh.

Two things are deliberate:

* **OBJ, not GLB.** Three programs read these meshes — the browser's
  ``OBJLoader``, ``setpieces._obj_bounds`` (which is ``v`` lines and nothing
  else), and ``isocam``'s depth rasterizer (``v`` and ``f``) — and all three
  already speak OBJ. A GLB would need three new readers to buy nothing.
* **The file is read off DISK, not fetched from /view.** ComfyUI's view route
  serves images; a mesh export node returns a PATH. ComfyUI runs on the same
  machine, so the honest way to collect the result is to open it.

Degrades like everything else in :mod:`imagery`: unreachable server or a failed
job raises :class:`MeshServiceUnavailable`, and the caller falls back to the
geometry the board has always had.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import requests


class MeshServiceUnavailable(RuntimeError):
    """Raised when the 3D backend can't be reached or fails a job."""


def output_roots() -> list[Path]:
    """Every place this process might find what ComfyUI wrote.

    A list rather than a setting because the two sides of this rig disagree
    about the same directory: ComfyUI is a Windows process writing to
    ``D:/ComfyUI/output``, and the caller may be either the Windows
    interpreter (which sees exactly that) or ``uv run`` under WSL (which sees
    ``/mnt/d/...``). Guessing wrong reports "the 3D backend is broken" about a
    mesh that generated perfectly, so both spellings are simply tried.
    """
    out: list[Path] = []
    for cand in (os.getenv("COMFYUI_OUTPUT"),
                 (os.getenv("COMFYUI_HOME") or "") + "/output"
                 if os.getenv("COMFYUI_HOME") else None,
                 "/mnt/d/ComfyUI/output", "D:/ComfyUI/output"):
        if not cand:
            continue
        p = Path(cand)
        if p not in out:
            out.append(p)
    return out

#: Geometry only, at the resolution a 12 GB card can hold beside SDXL. The
#: 1024/1536 cascades want the whole 16 GB checkpoint resident.
DEFAULT_RESOLUTION = os.getenv("ORACLE_TRELLIS_RES", "512")

#: A landmark is looked at from a fixed overhead camera at board scale, so a
#: half-million-face mesh buys nothing a browser can see and costs the whole
#: frame. Decimated on the GPU side, where it is nearly free.
DEFAULT_FACES = int(os.getenv("ORACLE_TRELLIS_FACES", "40000"))


class TrellisClient:
    """Minimal, synchronous image->mesh client over the ComfyUI API."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8188",
        *,
        resolution: str = DEFAULT_RESOLUTION,
        face_count: int = DEFAULT_FACES,
        steps: int = 12,
        timeout_seconds: int = 3600,
        output_root: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.resolution = resolution
        self.face_count = int(face_count)
        self.steps = int(steps)
        # An hour, and that is not padding. The FIRST call downloads the model
        # set — 16 GB of TRELLIS.2 plus ~1.2 GB of DINOv3 — and on this rig
        # that measured at about 14 MB/s, so twenty minutes before a single
        # sample is drawn. A shorter timeout reports "the 3D backend is broken"
        # about a download that is working perfectly, and worse, leaves the job
        # running so the next attempt queues a SECOND one behind it. Warm, a
        # geometry-only 512 run is a fraction of this.
        self.timeout_seconds = int(timeout_seconds)
        self.output_roots = ([Path(output_root)] if output_root
                             else output_roots())
        self.client_id = str(uuid.uuid4())

    # -- availability ------------------------------------------------------
    def available(self) -> bool:
        """Is there a ComfyUI up with the TRELLIS.2 nodes registered?

        Both halves matter and they fail differently: no server at all is the
        ordinary offline case, while a server whose nodes did not register is
        the ``sitecustomize`` shim having been wiped — which looks like nothing
        at all from the outside and is worth telling apart.
        """
        try:
            r = requests.get(f"{self.base_url}/object_info", timeout=10)
            r.raise_for_status()
            return "Trellis2ImageToShape" in r.json()
        except Exception:
            return False

    # -- the graph ---------------------------------------------------------
    def build_graph(self, image_name: str, *, seed: int = 0,
                    fmt: str = "obj", prefix: str = "oracle_mesh") -> dict[str, Any]:
        """The API-format prompt, written out rather than converted.

        The shipped workflows are UI-graph format (nodes, links, widget values
        by position) and the API wants named inputs, so a converter would have
        to reconstruct the names from ``/object_info`` and then guess which
        widget entries are real inputs and which are editor furniture — the
        seed's ``control_after_generate`` being the classic one. The pipeline
        is six nodes. Writing it is smaller than converting it, and every
        number in it is then a number somebody chose.

        The mask is the alpha channel INVERTED, which is ComfyUI's convention:
        ``LoadImage`` emits a mask that is 1 where the image is transparent, so
        the subject is what is left after inverting. Feed it a cut-out.
        """
        return {
            "1": {"class_type": "LoadImage",
                  "inputs": {"image": image_name}},
            "2": {"class_type": "InvertMask",
                  "inputs": {"mask": ["1", 1]}},
            "3": {"class_type": "LoadTrellis2Models",
                  "inputs": {"resolution": self.resolution,
                             "precision": "auto", "attn_backend": "auto"}},
            "4": {"class_type": "Trellis2GetConditioning",
                  "inputs": {"model_config": ["3", 0], "image": ["1", 0],
                             "mask": ["2", 0], "background_color": "black"}},
            "5": {"class_type": "Trellis2ImageToShape",
                  "inputs": {"model_config": ["3", 0], "conditioning": ["4", 0],
                             "seed": int(seed) & 0x7FFFFFFF,
                             "ss_guidance_strength": 6.5,
                             "ss_guidance_rescale": 0.05,
                             "ss_sampling_steps": self.steps,
                             "shape_guidance_strength": 6.5,
                             "shape_guidance_rescale": 0.05,
                             "shape_sampling_steps": self.steps,
                             "max_tokens": 49152}},
            "6": {"class_type": "Trellis2ProcessMesh",
                  "inputs": {"trimesh": ["5", 0], "remesh": "on",
                             "remesh.remesh_band": 1.0,
                             "remesh.remove_inner_faces": True,
                             "target_face_count": self.face_count,
                             "floater_threshold": 0.001,
                             "weld_vertices": True, "weld_digits": 4,
                             "chart_cone_angle": 90.0,
                             "chart_refine_iterations": 1,
                             "chart_global_iterations": 1,
                             "chart_smooth_strength": 1}},
            "7": {"class_type": "Trellis2ExportTrimesh",
                  "inputs": {"trimesh": ["6", 0], "filename_prefix": prefix,
                             "file_format": fmt}},
        }

    # -- the call ----------------------------------------------------------
    def image_to_mesh(self, png_bytes: bytes, *, seed: int = 0,
                      fmt: str = "obj", name_hint: str = "landmark") -> bytes:
        """Turn one cut-out picture into mesh bytes. Raises, never returns None."""
        up = self._upload(png_bytes, f"{name_hint}-{seed}.png")
        graph = self.build_graph(up, seed=seed, fmt=fmt,
                                 prefix=f"oracle_mesh/{name_hint}")
        try:
            resp = requests.post(f"{self.base_url}/prompt",
                                 json={"prompt": graph, "client_id": self.client_id},
                                 timeout=20)
            if resp.status_code >= 400:
                raise MeshServiceUnavailable(
                    f"ComfyUI rejected the mesh graph: {resp.text[:400]}")
            prompt_id = resp.json().get("prompt_id")
        except MeshServiceUnavailable:
            raise
        except Exception as e:
            raise MeshServiceUnavailable(f"Could not queue mesh job: {e}") from e
        if not prompt_id:
            raise MeshServiceUnavailable("ComfyUI did not return a prompt_id")
        started = time.time()
        reported = self._poll(prompt_id)
        if reported:
            return self._read(reported)
        # The export node finished and published NOTHING. See _locate.
        found = self._locate(name_hint, since=started - 60)
        if found is None:
            raise MeshServiceUnavailable(
                f"The mesh job finished and no file for {name_hint!r} appeared "
                f"under {[str(r) for r in self.output_roots]}")
        return found.read_bytes()

    def _upload(self, image_bytes: bytes, name: str) -> str:
        try:
            r = requests.post(f"{self.base_url}/upload/image",
                              files={"image": (name, image_bytes, "image/png")},
                              data={"overwrite": "true"}, timeout=60)
            r.raise_for_status()
            return r.json().get("name") or name
        except Exception as e:
            raise MeshServiceUnavailable(f"Could not upload the picture: {e}") from e

    def _poll(self, prompt_id: str) -> Optional[str]:
        """Wait for the job. Returns the reported path, or None if it reported
        none — which is what this export node does. See ``_locate``."""
        deadline = time.time() + self.timeout_seconds
        misses = 0
        while time.time() < deadline:
            try:
                r = requests.get(f"{self.base_url}/history/{prompt_id}", timeout=15)
                r.raise_for_status()
                hist = r.json().get(prompt_id)
                misses = 0
            except Exception as e:
                misses += 1
                if misses >= 5:
                    raise MeshServiceUnavailable(f"History poll failed: {e}") from e
                time.sleep(2.0)
                continue
            if hist:
                status = hist.get("status") or {}
                if status.get("status_str") == "error":
                    raise MeshServiceUnavailable(
                        f"ComfyUI mesh job failed: "
                        f"{json.dumps(status.get('messages', []))[:400]}")
                found = _first_path(hist.get("outputs") or {})
                if found:
                    return found
                # `execution_success` with an EMPTY outputs dict is what this
                # export node actually does — measured on a real run: the file
                # was written, the job reported success, and `outputs` was {}.
                # So finishing is the signal, and where the file went is the
                # caller's problem to solve by looking. Returning None rather
                # than raising keeps that decision out of here.
                if status.get("status_str") in ("success", "error") \
                        or status.get("completed"):
                    return None
            time.sleep(2.0)
        raise MeshServiceUnavailable("Timed out waiting for the mesh")

    def _locate(self, name_hint: str, *, since: float = 0.0) -> Optional[Path]:
        """Find the file the export node wrote but never told anyone about.

        Not a guess: ``filename_prefix`` is ours (``oracle_mesh/<slug>``), so
        the file is under a directory we named, beginning with a slug we chose,
        and the only unknown is the timestamp the node appends. Newest wins,
        and anything older than this job is ignored — a stale mesh from a
        previous attempt at the same landmark would otherwise be collected as
        if it were this one's, which is a wrong SHAPE rather than an error.
        """
        best: Optional[Path] = None
        best_at = since
        for root in self.output_roots:
            d = root / "oracle_mesh"
            try:
                cands = list(d.glob(f"{name_hint}*.obj")) + \
                    list(d.glob(f"{name_hint}*.glb")) + \
                    list(d.glob(f"{name_hint}*.ply"))
            except OSError:
                continue
            for c in cands:
                try:
                    at = c.stat().st_mtime
                except OSError:
                    continue
                if at >= best_at:
                    best, best_at = c, at
        return best

    def _read(self, reported: str) -> bytes:
        """Open what the export node wrote.

        The path it reports is the one ComfyUI sees, which on a split rig is a
        Windows path this process cannot open — so the FILE NAME is what is
        trusted and the configured output roots are where it is looked for.
        """
        name = reported.replace("\\", "/").rsplit("/", 1)[-1]
        cands: list[Path] = [Path(reported)]
        for root in self.output_roots:
            cands.append(root / name)
        for cand in cands:
            try:
                if cand.is_file():
                    return cand.read_bytes()
            except OSError:
                continue
        for root in self.output_roots:
            try:
                hits = sorted(root.rglob(name))
            except OSError:
                continue
            if hits:
                return hits[0].read_bytes()
        raise MeshServiceUnavailable(
            f"The mesh was written to {reported!r} and this process cannot see "
            f"it (looked under {[str(r) for r in self.output_roots]}). "
            "Set COMFYUI_OUTPUT.")


def _first_path(outputs: dict) -> Optional[str]:
    """The first thing in a history payload that looks like a written file.

    Export nodes are output nodes returning a STRING, and how ComfyUI files
    that under ``outputs`` is not something to hard-code against — it has moved
    between versions. Anything string-shaped ending in a mesh extension is the
    answer, whatever key it arrived under.
    """
    exts = (".obj", ".glb", ".ply", ".stl", ".gltf")
    def walk(v):
        if isinstance(v, str):
            if v.lower().endswith(exts):
                yield v
        elif isinstance(v, dict):
            for x in v.values():
                yield from walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                yield from walk(x)
    for hit in walk(outputs):
        return hit
    return None
