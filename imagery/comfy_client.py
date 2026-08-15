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
        loras: Optional[list] = None,
        controlnet: Optional[str] = None,
        controlnet_strength: float = 0.8,
        init_denoise: float = 1.0,
        rescale_cfg: Optional[float] = None,
        pag_scale: Optional[float] = None,
        freeu: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.checkpoint = checkpoint
        self.checkpoint_mature = checkpoint_mature
        self._last_checkpoint: Optional[str] = None
        self.steps = steps
        self.cfg_scale = cfg_scale
        self.sampler = sampler
        self.scheduler = scheduler
        self.timeout_seconds = timeout_seconds
        self.use_ipadapter = use_ipadapter
        self.ipadapter_weight = ipadapter_weight
        self.ipadapter_preset = ipadapter_preset
        self.loras = list(loras or [])
        # ControlNet: the layout is handed to the model as a PICTURE, because a
        # prompt cannot say where a wall goes. Set per-render, not globally —
        # only a battlemap has a floorplan to obey.
        self.controlnet = controlnet
        self.controlnet_strength = float(controlnet_strength)
        #: Which condition a UNION ControlNet is being handed. One model
        #: answers to depth, segmentation, canny and tile, and it is told which
        #: rather than working it out — see `_apply_controlnet`. Empty for a
        #: single-purpose net, where the question does not arise.
        self.controlnet_union_type: str = ""
        #: Region prompts for one render, each ``{words, mask, strength}``
        #: with the mask already uploaded. Set beside `_control_image_name`
        #: and cleared with it.
        self._region_conds: list[dict] = []
        #: Further conditions for one render, each
        #: ``{name, image, union_type, strength}``. Set for the duration of a
        #: generate() call beside `_control_image_name`, and cleared with it.
        self._extra_controls: list[dict] = []
        # img2img: how much of the init image is thrown away. 1.0 is a plain
        # text-to-image render and the init is ignored entirely.
        self.init_denoise = float(init_denoise)
        self._init_image_name: Optional[str] = None
        # Set for the duration of one generate() call; the graph builder reads
        # it. Kept off the signature of every helper it would otherwise thread
        # through, and always cleared afterwards.
        self._control_image_name: Optional[str] = None
        self.rescale_cfg = rescale_cfg
        self.pag_scale = pag_scale
        self.freeu = freeu
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

    def _apply_model_layers(self, g: dict) -> None:
        """Splice LoRA + guidance patches between the checkpoint and the sampler.

        These act on the MODEL rather than on the words, which is why they grip
        where prompt wording slips. Order is deliberate:

            checkpoint → LoRA(s) → RescaleCFG → PAG → FreeU → [IP-Adapter] → KSampler

        LoRA comes first and is the only one that also patches CLIP, so the text
        encoders are rewired onto its CLIP output — a style LoRA that never
        reached the encoders would be half-applied. IP-Adapter is spliced later
        by ``_inject_references``, which reads whatever this leaves on the
        sampler, so the two compose without either knowing about the other.

        Every layer is optional and skipped when unconfigured, so the default
        graph is byte-identical to before.
        """
        sampler_id = next((nid for nid, n in g.items()
                           if n.get("class_type") == "KSampler"), None)
        ckpt_id = next((nid for nid, n in g.items()
                        if n.get("class_type") == "CheckpointLoaderSimple"), None)
        if sampler_id is None or ckpt_id is None:
            return
        model_src = g[sampler_id]["inputs"].get("model")
        if not model_src:
            return

        # --- LoRAs (model + clip) ---
        clip_src = [ckpt_id, 1]
        for i, lora in enumerate(self.loras or []):
            name = (lora or {}).get("name") if isinstance(lora, dict) else str(lora)
            if not name:
                continue
            nid = f"80{i}"
            g[nid] = {"class_type": "LoraLoader", "inputs": {
                "lora_name": name,
                "strength_model": float((lora or {}).get("model", 1.0)
                                        if isinstance(lora, dict) else 1.0),
                "strength_clip": float((lora or {}).get("clip", 1.0)
                                       if isinstance(lora, dict) else 1.0),
                "model": model_src, "clip": clip_src,
            }}
            model_src, clip_src = [nid, 0], [nid, 1]
        # Text encoders must read the LoRA's CLIP, not the checkpoint's.
        if clip_src != [ckpt_id, 1]:
            for node in g.values():
                if node.get("class_type") == "CLIPTextEncode":
                    node.setdefault("inputs", {})["clip"] = clip_src

        # --- guidance patches (model only) ---
        if self.rescale_cfg is not None:
            g["85"] = {"class_type": "RescaleCFG", "inputs": {
                "model": model_src, "multiplier": float(self.rescale_cfg)}}
            model_src = ["85", 0]
        if self.pag_scale is not None:
            g["86"] = {"class_type": "PerturbedAttentionGuidance", "inputs": {
                "model": model_src, "scale": float(self.pag_scale)}}
            model_src = ["86", 0]
        if self.freeu:
            g["87"] = {"class_type": "FreeU_V2", "inputs": {
                "model": model_src,
                "b1": 1.3, "b2": 1.4, "s1": 0.9, "s2": 0.2}}
            model_src = ["87", 0]

        g[sampler_id]["inputs"]["model"] = model_src

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
        self._apply_model_layers(g)
        self._apply_lora_triggers(g)
        self._apply_init_image(g)
        # Regions BEFORE the ControlNet chain: the chain reads whatever the
        # sampler currently points at, so it has to pick up the combined
        # conditioning rather than the bare encoder.
        self._apply_regions(g)
        self._apply_controlnet(g)
        return g

    def _apply_init_image(self, g: dict) -> None:
        """Start the sampler from a picture instead of from noise.

        A depth map says how far away everything is and nothing about what it is
        MADE of, which is fine for a walled room — the walls carry the meaning —
        and useless outdoors, where the board's character is flat: water, grass,
        road, ice all sit at height zero and are invisible to depth. Handed that,
        the model invents, and what it invents disagrees with the grid.

        An init image carries the part depth cannot: layout, terrain TYPE, scale
        and the board's own outline. The sampler then repaints rather than
        composes, which is exactly the division of labour we want — the grid
        stays the truth and the model supplies the paint.

            LoadImage -> VAEEncode -> KSampler.latent_image, denoise < 1

        Leaves the graph alone when there is no init image, so the ordinary
        text-to-image path is untouched.
        """
        if not self._init_image_name or self.init_denoise >= 1.0:
            return
        ks_id = next((nid for nid, n in g.items()
                      if n.get("class_type") == "KSampler"), None)
        if ks_id is None:
            return
        ckpt = next((nid for nid, n in g.items()
                     if n.get("class_type") == "CheckpointLoaderSimple"), None)
        if ckpt is None:
            return
        g["80"] = {"class_type": "LoadImage",
                   "inputs": {"image": self._init_image_name}}
        g["81"] = {"class_type": "VAEEncode",
                   "inputs": {"pixels": ["80", 0], "vae": [ckpt, 2]}}
        g[ks_id]["inputs"]["latent_image"] = ["81", 0]
        g[ks_id]["inputs"]["denoise"] = self.init_denoise

    def _apply_controlnet(self, g: dict) -> None:
        """Condition the render on one or more control images.

        Spliced between the text encoders and the sampler, one link per
        condition:

            CLIPTextEncode(+/-) → ControlNetApplyAdvanced ×N → KSampler

        ``ControlNetApplyAdvanced`` takes BOTH conditionings and returns both,
        which is what makes them CHAIN: the first link's outputs are the second
        link's inputs. (The simpler ControlNetApply only handles the positive
        and would quietly drop the negative.)

        **A union ControlNet must be TOLD what it is being fed.** One model
        answers to depth, segmentation, canny, tile and the rest, and it picks
        by an explicit type — not by looking at the image. Left unset it falls
        back to "auto", and the widely-reported result is mush that looks like
        a weak render rather than like a misconfiguration. So a condition
        carrying ``union_type`` gets a ``SetUnionControlNetType`` between the
        loader and the apply. The type strings are ComfyUI's own
        (``comfy/cldm/control_types.py``): ``depth`` and ``segment``, not
        ``seg``.
        """
        conds = self._controlnet_conditions()
        if not conds:
            return
        ks_id = next((nid for nid, n in g.items()
                      if n.get("class_type") == "KSampler"), None)
        if ks_id is None:
            return
        pos = g[ks_id]["inputs"].get("positive")
        neg = g[ks_id]["inputs"].get("negative")
        if not (pos and neg):
            return
        for i, c in enumerate(conds):
            base = 70 + i * 10
            loader, img, apply_ = str(base), str(base + 1), str(base + 2)
            g[loader] = {"class_type": "ControlNetLoader",
                         "inputs": {"control_net_name": c["name"]}}
            net = [loader, 0]
            if c.get("union_type"):
                typed = str(base + 3)
                g[typed] = {"class_type": "SetUnionControlNetType",
                            "inputs": {"control_net": net,
                                       "type": c["union_type"]}}
                net = [typed, 0]
            g[img] = {"class_type": "LoadImage",
                      "inputs": {"image": c["image"]}}
            g[apply_] = {"class_type": "ControlNetApplyAdvanced", "inputs": {
                "positive": pos, "negative": neg,
                "control_net": net, "image": [img, 0],
                "strength": float(c.get("strength", self.controlnet_strength)),
                "start_percent": float(c.get("start", 0.0)),
                "end_percent": float(c.get("end", 1.0)),
            }}
            pos, neg = [apply_, 0], [apply_, 1]
        g[ks_id]["inputs"]["positive"] = pos
        g[ks_id]["inputs"]["negative"] = neg

    def _apply_regions(self, g: dict) -> None:
        """Say a different thing about different parts of the picture.

            CLIPTextEncode(region words) → ConditioningSetMask → Combine → …

        Each region is ADDED to the shared positive rather than replacing it:
        the base prompt is still the scene, the style and the framing, and a
        region only says what its own squares are made of. Applied to the
        POSITIVE alone — a mask on the negative would mean "no blurriness
        here, and everywhere else it is fine".

        Spliced ahead of the ControlNet chain, so its links pick up the
        combined conditioning rather than the bare encoder. Order matters:
        `_apply_controlnet` reads whatever the sampler currently points at.
        """
        if not self._region_conds:
            return
        ks_id = next((nid for nid, n in g.items()
                      if n.get("class_type") == "KSampler"), None)
        clip = next((nid for nid, n in g.items()
                     if n.get("class_type") == "CheckpointLoaderSimple"), None)
        if ks_id is None or clip is None:
            return
        pos = g[ks_id]["inputs"].get("positive")
        if not pos:
            return
        for i, r in enumerate(self._region_conds):
            base = 200 + i * 10
            img, mask, enc, setm, comb = (str(base), str(base + 1),
                                          str(base + 2), str(base + 3),
                                          str(base + 4))
            g[img] = {"class_type": "LoadImage", "inputs": {"image": r["mask"]}}
            # The mask is a black-and-white PICTURE, so it arrives on the image
            # output and has to be converted; LoadImage's own MASK output is the
            # file's alpha channel, which a PNG of white squares does not have.
            g[mask] = {"class_type": "ImageToMask",
                       "inputs": {"image": [img, 0], "channel": "red"}}
            g[enc] = {"class_type": "CLIPTextEncode",
                      "inputs": {"text": r["words"], "clip": [clip, 1]}}
            g[setm] = {"class_type": "ConditioningSetMask", "inputs": {
                "conditioning": [enc, 0], "mask": [mask, 0],
                "strength": float(r.get("strength", 0.85)),
                "set_cond_area": "default",
            }}
            g[comb] = {"class_type": "ConditioningCombine",
                       "inputs": {"conditioning_1": pos,
                                  "conditioning_2": [setm, 0]}}
            pos = [comb, 0]
        g[ks_id]["inputs"]["positive"] = pos

    def _controlnet_conditions(self) -> list[dict]:
        """Every condition to apply this render, newest API and oldest together.

        The single-image fields (``controlnet`` + one ``control_image``) are how
        every caller but the isometric board still asks, and they keep working
        by becoming a one-item list. Nothing else in the client needs to know
        which form it was given.
        """
        out: list[dict] = []
        if self.controlnet and self._control_image_name:
            out.append({"name": self.controlnet,
                        "image": self._control_image_name,
                        "union_type": self.controlnet_union_type,
                        "strength": self.controlnet_strength})
        for extra in self._extra_controls:
            if extra.get("image"):
                out.append(extra)
        return out

    def _apply_lora_triggers(self, g: dict) -> None:
        """Append each active LoRA's trigger word to the positive prompt.

        Many style LoRAs are trained against a caption tag and only half-fire
        without it — DD_Painterly_Clean carries "d&d painterly" on all 138 of
        its training images. The tag lives with the LoRA in config rather than
        in the house style string, because it belongs to the file: swap the
        LoRA and the tag has to go with it.

        Appended rather than prepended, so it can't outrank the weighted
        subject clause the prompt builder puts first.
        """
        triggers = [str((l or {}).get("trigger", "")).strip()
                    for l in (self.loras or []) if isinstance(l, dict)]
        triggers = [t for t in triggers if t]
        if not triggers:
            return
        for nid in ("6",):
            node = g.get(nid)
            if node and node.get("class_type") == "CLIPTextEncode":
                cur = str(node["inputs"].get("text") or "")
                node["inputs"]["text"] = ", ".join([p for p in (cur, *triggers) if p])

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
        control_image: Optional[bytes] = None,
        controls: Optional[list[dict]] = None,
        regions: Optional[list[dict]] = None,
        init_image: Optional[bytes] = None,
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
        effective = ckpt or self.checkpoint
        # Some 2026 ComfyUI nightlies crash their prompt_worker thread when they
        # auto-evict one large checkpoint to make room for another (a None-deref
        # in model_management.free_memory). Pre-emptively unload whatever is loaded
        # whenever we're about to use a different checkpoint than we last loaded —
        # so that buggy auto-eviction path is never taken. ``None`` (unknown loaded
        # state: fresh client, or ComfyUI left something loaded from before) counts
        # as "different", so the first render of a process unloads first too. The
        # explicit /free is safe while the model ref is still valid.
        if effective != self._last_checkpoint:
            self.free_memory(unload_models=True)
        # A control image has to reach ComfyUI's input folder before the graph
        # that names it is queued. Uploaded per render and cleared afterwards,
        # so one conditioned battlemap can't leak its floorplan into the next
        # portrait that happens to use the same client.
        self._init_image_name = None
        if init_image is not None and self.init_denoise < 1.0:
            self._init_image_name = self.upload_image(
                init_image, f"init-{seed}.png")

        self._control_image_name = None
        if control_image and self.controlnet:
            self._control_image_name = self.upload_image(
                control_image, f"control-{seed}.png")
            if not self._control_image_name:
                print("[imagery] control image upload failed; "
                      "rendering unconditioned")
        # Further conditions, each with its own picture to upload. A condition
        # whose upload fails is DROPPED rather than failing the render: losing
        # the segmentation hint costs a worse picture, and losing the render
        # costs the turn.
        self._extra_controls = []
        for i, extra in enumerate(controls or []):
            blob = extra.get("image")
            if not blob or not extra.get("name"):
                continue
            up = self.upload_image(blob, f"control-{seed}-{i + 1}.png")
            if not up:
                print(f"[imagery] control image {i + 1} upload failed; skipped")
                continue
            self._extra_controls.append({**extra, "image": up})
        # Region masks, uploaded like any other conditioning picture. A region
        # whose mask will not upload is dropped: its words are lost, which is
        # the behaviour before regions existed, and losing the render is worse.
        self._region_conds = []
        for i, r in enumerate(regions or []):
            blob, words = r.get("mask"), (r.get("words") or "").strip()
            if not blob or not words:
                continue
            up = self.upload_image(blob, f"region-{seed}-{i}.png")
            if not up:
                print(f"[imagery] region mask {i} upload failed; skipped")
                continue
            self._region_conds.append({**r, "mask": up})
        try:
            graph = self._build_graph(positive, negative, width, height, seed,
                                      steps or self.steps, checkpoint=ckpt)
        finally:
            self._control_image_name = None
            self._extra_controls = []
            self._region_conds = []
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
            self._last_checkpoint = effective
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
        loras=getattr(cfg, "loras", None),
        rescale_cfg=getattr(cfg, "rescale_cfg", None),
        pag_scale=getattr(cfg, "pag_scale", None),
        freeu=getattr(cfg, "freeu", False),
    )
