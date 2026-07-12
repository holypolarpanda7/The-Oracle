"""Compose diffusion prompts from a subject, its look, and the scene context.

Keeps the generated prompt grounded: the subject's intrinsic appearance drives a
stable ``descriptor`` (used for permanent-change invalidation), while the
environment/context and operator style are layered on top.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

from .models import ImageKind, normalize_kind


# Light framing per subject kind so the composition suits what's depicted.
_KIND_FRAMING = {
    ImageKind.PLACE: "wide establishing shot of a location, environment scenery, no characters in focus",
    ImageKind.NPC: "character portrait, upper body, expressive face, single figure",
    ImageKind.CREATURE: "full-body creature illustration, dynamic pose, menacing presence",
    ImageKind.ITEM: "single object study on a neutral background, museum lighting",
    ImageKind.PC: "heroic character portrait, head and shoulders, detailed face, single figure, adventurer",
    ImageKind.SCENE: "dynamic action scene, mid-motion, cinematic wide composition, dramatic moment",
}


@dataclass
class BuiltPrompt:
    positive: str
    negative: str
    descriptor: str        # the intrinsic-appearance text
    descriptor_hash: str   # stable hash of descriptor (kind+ref+descriptor)
    caption: str


def _hash(*parts: str) -> str:
    h = hashlib.sha256("\u0001".join(p.strip().lower() for p in parts).encode("utf-8"))
    return h.hexdigest()[:16]


def build_prompt(
    kind: str,
    subject: str,
    *,
    look: str = "",
    context: str = "",
    ref_slug: str = "",
    style_prompt: str = "",
    negative_prompt: str = "",
    extra: str = "",
) -> BuiltPrompt:
    """Assemble the positive/negative prompt and the intrinsic descriptor.

    - ``subject``: what it is ("dire wolf", "Jim the blacksmith", "Greenfields").
    - ``look``: the intrinsic appearance ("lean, sand-colored, one ear torn").
    - ``context``: environment/situation ("desert at dusk", "town in winter").
    - ``extra``: any extra scene detail to include but NOT count toward identity.
    """
    kind = normalize_kind(kind)
    subject = (subject or "").strip()
    look = (look or "").strip()
    context = (context or "").strip()

    framing = _KIND_FRAMING.get(kind, _KIND_FRAMING[ImageKind.CREATURE])

    # Intrinsic descriptor = subject + its look. Context is deliberately excluded
    # so a permanent look-change invalidates every context bucket at once.
    descriptor = subject if not look else f"{subject}, {look}"
    descriptor_hash = _hash(kind, ref_slug or subject, descriptor)

    pieces = [descriptor, framing]
    if context:
        pieces.append(f"in {context}")
    if extra:
        pieces.append(extra)
    if style_prompt:
        pieces.append(style_prompt)
    positive = ", ".join(p for p in pieces if p)

    caption_bits = [subject]
    if context:
        caption_bits.append(f"({context})")
    caption = " ".join(caption_bits).strip() or subject

    return BuiltPrompt(
        positive=positive,
        negative=negative_prompt or "",
        descriptor=descriptor,
        descriptor_hash=descriptor_hash,
        caption=caption,
    )
