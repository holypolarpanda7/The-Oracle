"""Self-hosted scene imagery: generate, store, reuse, and evict pictures.

The DM narrates a new place, NPC, or creature and this package produces (or
reuses) a compact WebP illustration for it, bucketed by subject x context, via a
local diffusion backend (ComfyUI in API mode). See ``store.ImageStore``.
"""
from __future__ import annotations

from .models import (
    EntityImage,
    ImageKind,
    normalize_kind,
    slugify,
    context_key,
    get_engine,
)
from .comfy_client import ComfyClient, ImageServiceUnavailable, client_from_config
from .compress import encode_webp, make_placeholder, EncodedImage
from .prompt_build import build_prompt, BuiltPrompt
from .store import ImageStore, ImageResult

__all__ = [
    "EntityImage",
    "ImageKind",
    "normalize_kind",
    "slugify",
    "context_key",
    "get_engine",
    "ComfyClient",
    "ImageServiceUnavailable",
    "client_from_config",
    "encode_webp",
    "make_placeholder",
    "EncodedImage",
    "build_prompt",
    "BuiltPrompt",
    "ImageStore",
    "ImageResult",
]
