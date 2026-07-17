"""Client for a self-hosted ComfyUI instance running in API mode.

ComfyUI exposes a small HTTP API:
  - ``POST /prompt``            queue a workflow graph, returns a ``prompt_id``
  - ``GET  /history/{id}``      poll for completion + output file references
  - ``GET  /view?filename=...`` download a produced image

We ship a built-in SDXL txt2img workflow and fill in the prompt/seed/size. A
custom workflow (exported from ComfyUI in *API* format) can be supplied via
``ImageryConfig.workflow_path`` for FLUX or other pipelines.

Everything degrades gracefully: if the server is unreachable, generation raises
``ImageServiceUnavailable`` and the store falls back to a placeholder, so the
rest of the game keeps running before the GPU box is set up.
"""
from __future__ import annotations

import copy
import json
import random
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import requests


class ImageServiceUnavailable(RuntimeError):
    """Raised when the diffusion backend can't be reached or fails a job."""


# Module-level (clients are constructed per call): whether the current
# free_memory offline streak has already been logged.
_FREE_MEMORY_ERR_LOGGED = False


# Built-in SDXL txt2img graph in ComfyUI *API* format. Node ids are strings.
_DEFAULT_WORKFLOW: dict[str, Any] = {
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "", "clip": ["4", 1]},
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "", "clip": ["4", 1]},
    },
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0,
            "steps": 25,
            "cfg": 7.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0],
        },
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "oracle", "images": ["8", 0]},
    },
}


class ComfyClient:
    """Minimal, synchronous ComfyUI API client."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8188",
        *,
        checkpoint: str = "sd_xl_base_1.0.safetensors",
        checkpoint_mature: Optional[str] = None,
        workflow_path: Optional[str] = None,
        steps: int = 25,
        cfg_scale: float = 7.0,
        sampler: str = "euler",
        scheduler: str = "normal",
        timeout_seconds: int = 180,
        use_ipadapter: bool = False,
        ipadapter_weight: float = 0.65,
        ipadapter_preset: str = "STANDARD (medium strength)",
    ):
        self.base_url = base_url.rstrip("/")
        self.checkpoint = checkpoint
        self.checkpoint_mature = checkpoint_mature
        self.steps = steps
        self.cfg_scale = cfg_scale
        self.sampler = sampler
        self.scheduler = scheduler
        self.timeout_seconds = timeout_seconds
        self.use_ipadapter = use_ipadapter
        self.ipadapter_weight = ipadapter_weight
        self.ipadapter_preset = ipadapter_preset
        self.client_id = uuid.uuid4().hex
        self._template = self._load_workflow(workflow_path)

    # ----- workflow -----

    def _load_workflow(self, workflow_path: Optional[str]) -> dict:
        if workflow_path:
            p = Path(workflow_path)
            if p.is_file():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception as e:
                    print(f"[imagery] Failed to read workflow {p}: {e}; using default")
        return copy.deepcopy(_DEFAULT_WORKFLOW)

    def upload_image(self, image_bytes: bytes, name: str) -> Optional[str]:
        """Upload reference image bytes to ComfyUI's input store.

        Returns the server-side filename to reference in LoadImage nodes, or
        None on failure (callers degrade to reference-free generation).
        """
        try:
            resp = requests.post(
                f"{self.base_url}/upload/image",
                files={"image": (name, image_bytes, "image/webp")},
                data={"overwrite": "true"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("name") or name
        except Exception as e:
            print(f"[imagery] reference upload failed ({name}): {e}")
            return None

    def _inject_references(self, g: dict, ref_filenames: list[str]) -> None:
        """Wire reference images into the graph so the render RESEMBLES them.

        Two mechanisms, in priority order:
        1. Custom workflows: any LoadImage node titled ``oracle_ref_N`` gets
           the Nth reference filename — full operator control.
        2. Built-in/other workflows with ``use_ipadapter`` on: an IPAdapter
           chain (ComfyUI_IPAdapter_plus custom nodes + an ip-adapter SDXL
           model must be installed) is spliced between the checkpoint and the
           KSampler. If the nodes aren't installed, ComfyUI rejects the graph
           and generation falls back upstream — never a hard failure here.
        """
        # (1) Title-convention slots in custom workflows.
        slots = sorted(
            (nid for nid, node in g.items()
             if node.get("class_type") == "LoadImage"
             and str((node.get("_meta") or {}).get("title", "")).startswith("oracle_ref")),
            key=lambda nid: str(g[nid].get("_meta", {}).get("title")),
        )
        if slots:
            for nid, fname in zip(slots, ref_filenames):
                g[nid].setdefault("inputs", {})["image"] = fname
            return

        if not self.use_ipadapter:
            return

        # (2) IPAdapter chain injection into the default-style graph.
        sampler_id = next((nid for nid, n in g.items()
                           if n.get("class_type") == "KSampler"), None)
        if sampler_id is None:
            return
        model_src = g[sampler_id]["inputs"].get("model")
        if not model_src:
            return
        g["90"] = {"class_type": "IPAdapterUnifiedLoader",
                   "inputs": {"model": model_src, "preset": self.ipadapter_preset}}
        prev_model = ["90", 0]
        for i, fname in enumerate(ref_filenames[:3]):
            load_id, ada_id = f"91{i}", f"92{i}"
            g[load_id] = {"class_type": "LoadImage", "inputs": {"image": fname}}
            g[ada_id] = {"class_type": "IPAdapter", "inputs": {
                "model": prev_model, "ipadapter": ["90", 1],
                "image": [load_id, 0], "weight": self.ipadapter_weight,
                "start_at": 0.0, "end_at": 1.0, "weight_type": "standard",
            }}
            prev_model = [ada_id, 0]
        g[sampler_id]["inputs"]["model"] = prev_model

    def _build_graph(
        self, positive: str, negative: str, width: int, height: int, seed: int,
        steps: int, checkpoint: Optional[str] = None,
    ) -> dict:
        ckpt = checkpoint or self.checkpoint
        g = copy.deepcopy(self._template)
        # Best-effort fill of the well-known node ids from the default graph. If
        # a custom workflow uses different ids this still works when it follows
        # the same class_type layout; otherwise the operator should pre-fill it.
        for node in g.values():
            ct = node.get("class_type")
            ins = node.setdefault("inputs", {})
            if ct == "CheckpointLoaderSimple":
                ins["ckpt_name"] = ckpt
            elif ct == "EmptyLatentImage":
                ins["width"], ins["height"] = width, height
            elif ct == "KSampler":
                ins["seed"] = seed
                ins["steps"] = steps
                ins["cfg"] = self.cfg_scale
                ins["sampler_name"] = self.sampler
                ins["scheduler"] = self.scheduler
        # Positive/negative encoders: in the default graph node 6 = positive
        # (wired to KSampler.positive) and node 7 = negative.
        if "6" in g and g["6"].get("class_type") == "CLIPTextEncode":
            g["6"]["inputs"]["text"] = positive
        if "7" in g and g["7"].get("class_type") == "CLIPTextEncode":
            g["7"]["inputs"]["text"] = negative
        return g

    # ----- HTTP -----

    def is_available(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/system_stats", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def free_memory(self, *, unload_models: bool = True) -> bool:
        """Ask ComfyUI to release GPU memory (unload the diffusion model).

        Used for single-GPU time-sharing so a self-hosted LLM can reclaim VRAM
        between image renders. Best-effort: returns False if ComfyUI is offline
        or the request fails, and never raises. The failure is logged once per
        offline streak (this runs before every chat turn, so per-call logging
        floods the console when ComfyUI simply isn't running).
        """
        global _FREE_MEMORY_ERR_LOGGED
        try:
            r = requests.post(
                f"{self.base_url}/free",
                json={"unload_models": unload_models, "free_memory": True},
                timeout=10,
            )
            _FREE_MEMORY_ERR_LOGGED = False
            return r.status_code == 200
        except Exception as e:
            if not _FREE_MEMORY_ERR_LOGGED:
                print(f"[imagery] free_memory failed (ComfyUI offline? "
                      f"further failures muted until it recovers): {e}")
                _FREE_MEMORY_ERR_LOGGED = True
            return False

    def generate(
        self,
        positive: str,
        negative: str = "",
        *,
        width: int = 1024,
        height: int = 1024,
        steps: Optional[int] = None,
        seed: Optional[int] = None,
        reference_filenames: Optional[list[str]] = None,
        mature: bool = False,
    ) -> bytes:
        """Queue a job and return the produced image bytes (PNG).

        ``reference_filenames`` (already uploaded via ``upload_image``) make
        the render resemble those images — see ``_inject_references``.
        ``mature`` routes the render to the NSFW-capable checkpoint when one is
        configured; otherwise it falls back to the default (safe) checkpoint.
        Raises ``ImageServiceUnavailable`` on any connection/generation failure.
        """
        seed = random.randint(0, 2**31 - 1) if seed is None else seed
        ckpt = self.checkpoint_mature if (mature and self.checkpoint_mature) else None
        graph = self._build_graph(positive, negative, width, height, seed,
                                   steps or self.steps, checkpoint=ckpt)
        if reference_filenames:
            self._inject_references(graph, list(reference_filenames))
        try:
            resp = requests.post(
                f"{self.base_url}/prompt",
                json={"prompt": graph, "client_id": self.client_id},
                timeout=15,
            )
            resp.raise_for_status()
            prompt_id = resp.json().get("prompt_id")
            if not prompt_id:
                raise ImageServiceUnavailable("ComfyUI did not return a prompt_id")
        except ImageServiceUnavailable:
            raise
        except Exception as e:
            raise ImageServiceUnavailable(f"Could not queue job: {e}") from e

        image_ref = self._poll_history(prompt_id)
        return self._download(image_ref)

    def _poll_history(self, prompt_id: str) -> dict:
        deadline = time.time() + self.timeout_seconds
        consecutive_failures = 0
        while time.time() < deadline:
            try:
                r = requests.get(f"{self.base_url}/history/{prompt_id}", timeout=10)
                r.raise_for_status()
                hist = r.json().get(prompt_id)
                consecutive_failures = 0
            except Exception as e:
                # A single dropped poll shouldn't abandon a render that is still
                # running; only give up after several failures in a row.
                consecutive_failures += 1
                if consecutive_failures >= 5:
                    raise ImageServiceUnavailable(f"History poll failed: {e}") from e
                time.sleep(2.0)
                continue
            if hist:
                status = hist.get("status") or {}
                if status.get("status_str") == "error":
                    raise ImageServiceUnavailable(
                        f"ComfyUI job {prompt_id} failed: "
                        f"{json.dumps(status.get('messages', []))[:300]}"
                    )
                if hist.get("outputs"):
                    for out in hist["outputs"].values():
                        images = out.get("images") or []
                        if images:
                            return images[0]
                    raise ImageServiceUnavailable("Job finished with no image output")
            time.sleep(1.0)
        raise ImageServiceUnavailable("Timed out waiting for image generation")

    def _download(self, image_ref: dict) -> bytes:
        params = {
            "filename": image_ref.get("filename", ""),
            "subfolder": image_ref.get("subfolder", ""),
            "type": image_ref.get("type", "output"),
        }
        try:
            r = requests.get(f"{self.base_url}/view", params=params,
                             timeout=self.timeout_seconds)
            r.raise_for_status()
            return r.content
        except Exception as e:
            raise ImageServiceUnavailable(f"Image download failed: {e}") from e


def client_from_config(cfg) -> ComfyClient:
    """Build a ComfyClient from an ``ImageryConfig``."""
    return ComfyClient(
        base_url=cfg.base_url,
        checkpoint=cfg.checkpoint,
        checkpoint_mature=getattr(cfg, "checkpoint_mature", None),
        workflow_path=cfg.workflow_path,
        steps=cfg.steps,
        cfg_scale=cfg.cfg_scale,
        sampler=cfg.sampler,
        scheduler=cfg.scheduler,
        timeout_seconds=cfg.timeout_seconds,
        use_ipadapter=getattr(cfg, "use_ipadapter", False),
        ipadapter_weight=getattr(cfg, "ipadapter_weight", 0.65),
        ipadapter_preset=getattr(cfg, "ipadapter_preset", "STANDARD (medium strength)"),
    )
