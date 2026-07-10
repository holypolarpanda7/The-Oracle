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
        workflow_path: Optional[str] = None,
        steps: int = 25,
        cfg_scale: float = 7.0,
        sampler: str = "euler",
        scheduler: str = "normal",
        timeout_seconds: int = 180,
    ):
        self.base_url = base_url.rstrip("/")
        self.checkpoint = checkpoint
        self.steps = steps
        self.cfg_scale = cfg_scale
        self.sampler = sampler
        self.scheduler = scheduler
        self.timeout_seconds = timeout_seconds
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

    def _build_graph(
        self, positive: str, negative: str, width: int, height: int, seed: int, steps: int
    ) -> dict:
        g = copy.deepcopy(self._template)
        # Best-effort fill of the well-known node ids from the default graph. If
        # a custom workflow uses different ids this still works when it follows
        # the same class_type layout; otherwise the operator should pre-fill it.
        for node in g.values():
            ct = node.get("class_type")
            ins = node.setdefault("inputs", {})
            if ct == "CheckpointLoaderSimple":
                ins["ckpt_name"] = self.checkpoint
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

    def generate(
        self,
        positive: str,
        negative: str = "",
        *,
        width: int = 1024,
        height: int = 1024,
        steps: Optional[int] = None,
        seed: Optional[int] = None,
    ) -> bytes:
        """Queue a job and return the produced image bytes (PNG).

        Raises ``ImageServiceUnavailable`` on any connection/generation failure.
        """
        seed = random.randint(0, 2**31 - 1) if seed is None else seed
        graph = self._build_graph(positive, negative, width, height, seed,
                                   steps or self.steps)
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
        while time.time() < deadline:
            try:
                r = requests.get(f"{self.base_url}/history/{prompt_id}", timeout=10)
                r.raise_for_status()
                hist = r.json().get(prompt_id)
            except Exception as e:
                raise ImageServiceUnavailable(f"History poll failed: {e}") from e
            if hist and hist.get("outputs"):
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
        workflow_path=cfg.workflow_path,
        steps=cfg.steps,
        cfg_scale=cfg.cfg_scale,
        sampler=cfg.sampler,
        scheduler=cfg.scheduler,
        timeout_seconds=cfg.timeout_seconds,
    )
