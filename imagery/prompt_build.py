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
    # The painted isometric board. Everything about its geometry — where the
    # walls are, how tall, what stands where — arrives through the depth
    # ControlNet, so the words are only here for MATERIAL and MOOD. Saying
    # anything about layout would invite the model to argue with the depth map.
    #
    # The one instruction that matters: no figures. Creatures are DOM tokens
    # drawn over this, and a painted adventurer is a second, wrong party
    # standing in the room forever.
    ImageKind.ISOBOARD: (
        "a single isometric model of this room ALONE, floating on empty black, "
        "hand-painted, viewed from a fixed high angle, consistent even "
        "lighting, rich material detail, "
        "nothing whatsoever around it, no surrounding room, no larger building, "
        "no floor or ground extending beyond it, plain black empty background, "
        "no people, no creatures, no figures, no text, no labels, no border, "
        "no user interface, nothing overlaid"
    ),
    # A material is a SAMPLE, not a picture. The two ways it goes wrong are a
    # picture of a thing and a picture of a place, and both come from the model
    # reading "stone floor" as a scene brief — so the framing insists on flat,
    # even, edge-to-edge coverage with nothing composed in it and, above all,
    # no border. (Rendered as a MAP this came back as whole battlemaps with
    # ornate frames; the kind exists to escape those LoRAs, and the framing has
    # to agree with it rather than leave the door open again.)
    ImageKind.MATERIAL: (
        "flat seamless tileable texture swatch of a single surface material, "
        "straight overhead orthographic view, uniform across the entire frame, "
        "even diffuse lighting, filling the frame completely edge to edge, "
        "no border, no frame, no ornament, no objects, no figures, "
        "no walls, no room, no scene, no composition, no focal point"
    ),
    # A world map is DRAWN COUNTRY, not photographed ground: miles per inch,
    # stylised relief, the conventions of a cartographer rather than a camera.
    # It also carries no writing — every label, dot, road, compass and scale bar
    # on the finished artifact is inked by mapmaker.py from real coordinates, so
    # anything the model writes here is a lie drawn over the truth.
    ImageKind.WORLDMAP: (
        "hand-drawn fantasy world map, overhead cartographic view, "
        "stylised painted relief, illustrated terrain on aged parchment, "
        "muted cartographer's palette, no labels, no writing, "
        "full-bleed edge to edge"
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

#: World-map negatives. Mostly about keeping the model's HANDWRITING off the
#: sheet: the ink layer draws every place name, route and compass from the
#: world's real coordinates, and a model-invented label underneath it is a
#: second, wrong map showing through.
_WORLDMAP_NEGATIVE = (
    "text, letters, words, labels, place names, writing, calligraphy, legend, "
    "key, scale bar, compass rose, grid lines, latitude lines, border, frame, "
    "torn edges, rolled scroll, hands, people, characters, creatures, "
    "isometric, perspective, horizon, watermark, signature, ui, "
    # The map LoRA is trained on game-atlas plates and will otherwise dress the
    # sheet in furniture: a carved title banner across the top, gilt corners, a
    # framing surround. Every one of those eats the edge of a full-bleed wash
    # and competes with the ink drawn over it.
    "ornate border, decorative frame, cartouche, title banner, scroll ends, "
    "gold filigree, corner ornament, inset panel, vignette"
)


@dataclass
class BuiltPrompt:
    positive: str
    negative: str
    descriptor: str        # the intrinsic-appearance text
    descriptor_hash: str   # stable hash of descriptor (kind+ref+descriptor)
    caption: str
    kind: str = ""         # normalized ImageKind — picks the render's LoRA stack


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
    elif kind == ImageKind.WORLDMAP:
        negative_prompt = ", ".join(p for p in (negative_prompt, _WORLDMAP_NEGATIVE) if p)
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
        kind=kind,
    )
