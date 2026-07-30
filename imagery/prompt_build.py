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
    ImageKind.ITEM: (
        "single object study, the whole object shown complete and in one piece, "
        "centered on a plain neutral background, even museum lighting"
    ),
    ImageKind.PC: "heroic character portrait, head and shoulders, detailed face, single figure, adventurer",
    # Deliberately NOT "dynamic action, mid-motion": a scene is whatever was
    # described, and a player who asks to see a quiet throne room should not get
    # a fight. The framing says "draw this moment", the description says which.
    ImageKind.SCENE: (
        "cinematic illustration of this exact moment, composed around what is "
        "described above and nothing else, single coherent scene"
    ),
    # A battlemap is furniture for the rules, not a picture: dead-flat overhead,
    # no perspective, no figures, so the grid the engine enforces lines up with
    # what the players see.
    ImageKind.MAP: (
        "top-down orthographic battlemap, straight overhead bird's-eye view, "
        "flat lay, no perspective, no horizon, tabletop RPG battle map, "
        "even diffuse lighting, full-bleed edge to edge"
    ),
}

#: Coherence clauses appended for kinds that depict a person. Diffusion models
#: happily give a red-haired dwarf a black beard, or shift a skin tone between
#: two renders of the same character; saying the constraint out loud (and
#: negating the failure below) holds a face together across a set of looks.
_FIGURE_COHERENCE = (
    "consistent colouring, facial hair exactly the same colour as the hair on "
    "the head, eyebrows matching the hair, one uniform skin tone over face neck "
    "and hands"
)

#: ...and the matching negatives. These name the actual failure modes rather
#: than generic quality tags, which the configured negative prompt covers.
_FIGURE_NEGATIVE = (
    "mismatched beard colour, beard a different colour from the hair, two-tone "
    "hair, dyed streaks, patchy skin tone, discoloured face, blotchy skin, "
    "colour shift between face and body"
)

#: Extra negatives that only make sense for a battlemap — anything that would
#: fight the grid, the tokens, or the flat overhead framing.
_MAP_NEGATIVE = (
    "isometric, perspective, side view, horizon, vanishing point, "
    "people, characters, figures, creatures, miniatures, tokens, "
    "grid lines, hex grid, text, labels, legend, compass rose, border, frame, "
    "vignette, watermark, signature, ui, drop shadow"
)


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

    # A scene is a one-off request for a SPECIFIC moment, usually because a
    # player asked to see it — so what was described has to outweigh the house
    # style trailing it. CLIP emphasis puts the thumb on that side of the scale.
    lead = f"({descriptor}:1.3)" if kind == ImageKind.SCENE and descriptor \
        else descriptor
    pieces = [lead, framing]
    if kind in (ImageKind.PC, ImageKind.NPC):
        pieces.append(_FIGURE_COHERENCE)
    if context:
        pieces.append(f"in {context}")
    if extra:
        pieces.append(extra)
    if style_prompt:
        pieces.append(style_prompt)
    positive = ", ".join(p for p in pieces if p)

    if kind == ImageKind.MAP:
        negative_prompt = ", ".join(p for p in (negative_prompt, _MAP_NEGATIVE) if p)
    elif kind in (ImageKind.PC, ImageKind.NPC):
        negative_prompt = ", ".join(p for p in (negative_prompt, _FIGURE_NEGATIVE) if p)

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
