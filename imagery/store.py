"""The imagery store: generate, cache, reuse, and evict scene pictures.

Bucketing rule (per the design): images are keyed by **subject x context**, so
``(creature, wolf, desert)`` and ``(creature, wolf, jungle)`` are separate
buckets, as are ``(npc, jim, town)`` and ``(npc, jim, town-winter)``. Each bucket
holds up to ``max_per_bucket`` (default 3) pictures. Once a bucket is full,
future "similar situations" randomly draw one of the stored images instead of
generating a new one.

Invalidation:
  - ``invalidate_subject`` wipes *every* bucket for a subject — used when an NPC/
    creature's intrinsic appearance permanently changes (e.g. Jim loses a leg) or
    when a place/NPC is removed as the world evolves.
  - ``invalidate_context`` wipes a single environment bucket.
  - ``invalidate_stale`` keeps only rows matching a new descriptor hash.

Everything degrades gracefully when the diffusion backend is offline.
"""
from __future__ import annotations

import base64
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from sqlmodel import Session, select
from sqlalchemy.engine import Engine
from .models import (
    EntityImage,
    ImageKind,
    get_engine,
    normalize_kind,
    slugify,
    context_key,
)
from .compress import encode_webp, make_placeholder
from .comfy_client import ComfyClient, ImageServiceUnavailable, client_from_config
from .prompt_build import build_prompt


@dataclass
class ImageResult:
    kind: str
    ref_slug: str
    context_key: str
    caption: str
    image: bytes                       # WebP bytes ready to attach
    width: int = 0
    height: int = 0
    image_id: Optional[int] = None
    reused: bool = False               # drawn from an existing bucket entry
    generated: bool = False            # a fresh render happened
    stored: bool = False               # persisted to the DB
    temp: bool = False                 # throwaway, never stored
    offline: bool = False              # backend unavailable; placeholder image
    seed: Optional[int] = None
    meta: dict = field(default_factory=dict)

    @property
    def mime(self) -> str:
        return "image/webp"

    def b64(self) -> str:
        return base64.b64encode(self.image).decode("ascii")

    def payload(self) -> dict:
        """Compact dict for API/bot transport (base64 image + metadata)."""
        return {
            "b64": self.b64(),
            "mime": self.mime,
            "caption": self.caption,
            "kind": self.kind,
            "ref": self.ref_slug,
            "context": self.context_key,
            "image_id": self.image_id,
            "reused": self.reused,
            "generated": self.generated,
            "temp": self.temp,
            "offline": self.offline,
            "width": self.width,
            "height": self.height,
        }


class ImageStore:
    def __init__(
        self,
        engine: Optional[Engine] = None,
        *,
        config=None,
        client: Optional[ComfyClient] = None,
        world_day_fn: Optional[Callable[[], int]] = None,
        database_url: Optional[str] = None,
    ):
        self.engine = engine or get_engine(database_url)
        self._config = config
        self._client = client
        self.world_day_fn = world_day_fn
        from sqlmodel import SQLModel
        SQLModel.metadata.create_all(self.engine)

    # ----- config / client -----

    def _cfg(self):
        if self._config is not None:
            return self._config
        from game_config import get_config
        return get_config().imagery

    def _client_for(self, cfg) -> ComfyClient:
        if self._client is None:
            self._client = client_from_config(cfg)
        return self._client

    def _world_day(self) -> int:
        try:
            return int(self.world_day_fn()) if self.world_day_fn else 0
        except Exception:
            return 0

    # ----- queries -----

    def _bucket(self, session: Session, kind: str, ref: str, ckey: str) -> list[EntityImage]:
        stmt = select(EntityImage).where(
            EntityImage.kind == kind,
            EntityImage.ref_slug == ref,
            EntityImage.context_key == ckey,
        )
        return list(session.exec(stmt).all())

    def list_for(self, kind: str, ref: str, context: Optional[str] = None) -> list[dict]:
        """Metadata for a subject's stored images (no image bytes)."""
        kind = normalize_kind(kind)
        ref = slugify(ref)
        with Session(self.engine) as s:
            stmt = select(EntityImage).where(
                EntityImage.kind == kind, EntityImage.ref_slug == ref
            )
            if context is not None:
                stmt = stmt.where(EntityImage.context_key == context_key(context))
            rows = list(s.exec(stmt).all())
        return [
            {
                "image_id": r.id,
                "kind": r.kind,
                "ref": r.ref_slug,
                "context": r.context_key,
                "caption": r.caption,
                "width": r.width,
                "height": r.height,
                "byte_size": r.byte_size,
                "use_count": r.use_count,
                "world_day": r.world_day,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]

    def get_image_bytes(self, image_id: int, *, thumb: bool = False) -> Optional[bytes]:
        with Session(self.engine) as s:
            row = s.get(EntityImage, image_id)
            if row is None:
                return None
            return row.thumb if (thumb and row.thumb) else row.image

    def stats(self) -> dict:
        with Session(self.engine) as s:
            rows = list(s.exec(select(EntityImage)).all())
        buckets: dict[tuple, int] = {}
        total_bytes = 0
        for r in rows:
            buckets[(r.kind, r.ref_slug, r.context_key)] = buckets.get(
                (r.kind, r.ref_slug, r.context_key), 0) + 1
            total_bytes += r.byte_size or 0
        return {
            "images": len(rows),
            "buckets": len(buckets),
            "total_bytes": total_bytes,
        }

    # ----- generation -----

    def _render(self, cfg, prompt, ckey: str) -> tuple[Optional[bytes], Optional[int], bool]:
        """Return (raw_bytes, seed, offline). raw_bytes is None only if offline."""
        seed = random.randint(0, 2**31 - 1)
        try:
            client = self._client_for(cfg)
            raw = client.generate(
                prompt.positive,
                prompt.negative,
                width=cfg.gen_width,
                height=cfg.gen_height,
                steps=cfg.steps,
                seed=seed,
            )
            return raw, seed, False
        except ImageServiceUnavailable as e:
            print(f"[imagery] service offline: {e}")
            return None, seed, True
        except Exception as e:
            print(f"[imagery] generation error: {e}")
            return None, seed, True

    def ensure_image(
        self,
        kind: str,
        subject: str,
        *,
        look: str = "",
        context: str = "",
        ref_slug: Optional[str] = None,
        extra: str = "",
        force_new: bool = False,
    ) -> Optional[ImageResult]:
        """Return an image for (subject x context), reusing or generating as needed.

        Returns ``None`` when imagery is disabled. When the backend is offline a
        result with ``offline=True`` (placeholder bytes, not stored) is returned.
        """
        cfg = self._cfg()
        if not cfg.enabled:
            return None

        kind = normalize_kind(kind)
        ref = slugify(ref_slug or subject)
        ckey = context_key(context)

        prompt = build_prompt(
            kind, subject, look=look, context=context, ref_slug=ref,
            style_prompt=cfg.style_prompt, negative_prompt=cfg.negative_prompt,
            extra=extra,
        )

        with Session(self.engine) as s:
            existing = self._bucket(s, kind, ref, ckey)
            # Full bucket (or explicitly no new render) -> random draw.
            if existing and (force_new is False) and len(existing) >= cfg.max_per_bucket:
                chosen = random.choice(existing)
                chosen.use_count += 1
                chosen.last_used_at = datetime.utcnow()
                s.add(chosen)
                s.commit()
                s.refresh(chosen)
                return ImageResult(
                    kind=kind, ref_slug=ref, context_key=ckey,
                    caption=chosen.caption or prompt.caption, image=chosen.image,
                    width=chosen.width, height=chosen.height, image_id=chosen.id,
                    reused=True, stored=True, seed=chosen.seed,
                )

        # Otherwise render a fresh image (building bucket variety up to the cap).
        raw, seed, offline = self._render(cfg, prompt, ckey)
        if offline or raw is None:
            return ImageResult(
                kind=kind, ref_slug=ref, context_key=ckey, caption=prompt.caption,
                image=make_placeholder(), width=768, height=512,
                generated=False, offline=True, seed=seed,
            )

        enc = encode_webp(
            raw, store_width=cfg.store_width, thumb_width=cfg.thumb_width,
            quality=cfg.webp_quality,
        )
        row = EntityImage(
            kind=kind, ref_slug=ref, context_key=ckey, caption=prompt.caption,
            prompt=prompt.positive, descriptor_hash=prompt.descriptor_hash,
            image=enc.data, thumb=enc.thumb, width=enc.width, height=enc.height,
            byte_size=enc.byte_size, seed=seed, world_day=self._world_day(),
            use_count=1,
        )
        with Session(self.engine) as s:
            s.add(row)
            s.commit()
            s.refresh(row)
            self._enforce_global_cap(s, cfg)
            s.commit()
            image_id = row.id

        return ImageResult(
            kind=kind, ref_slug=ref, context_key=ckey, caption=prompt.caption,
            image=enc.data, width=enc.width, height=enc.height, image_id=image_id,
            generated=True, stored=True, seed=seed,
        )

    def generate_temp(
        self,
        kind: str,
        subject: str,
        *,
        look: str = "",
        context: str = "",
        extra: str = "",
    ) -> Optional[ImageResult]:
        """Render a throwaway image (never stored). ``None`` if temp disabled."""
        cfg = self._cfg()
        if not cfg.enabled or not cfg.allow_temp:
            return None
        kind = normalize_kind(kind)
        prompt = build_prompt(
            kind, subject, look=look, context=context,
            style_prompt=cfg.style_prompt, negative_prompt=cfg.negative_prompt,
            extra=extra,
        )
        raw, seed, offline = self._render(cfg, prompt, context_key(context))
        if offline or raw is None:
            return ImageResult(
                kind=kind, ref_slug=slugify(subject), context_key=context_key(context),
                caption=prompt.caption, image=make_placeholder(), width=768, height=512,
                temp=True, offline=True, seed=seed,
            )
        enc = encode_webp(
            raw, store_width=cfg.store_width, thumb_width=cfg.thumb_width,
            quality=cfg.webp_quality,
        )
        return ImageResult(
            kind=kind, ref_slug=slugify(subject), context_key=context_key(context),
            caption=prompt.caption, image=enc.data, width=enc.width, height=enc.height,
            generated=True, temp=True, seed=seed,
        )

    # ----- invalidation / eviction -----

    def invalidate_subject(self, kind: str, ref: str) -> int:
        """Delete every stored image for a subject (all contexts). Returns count."""
        kind = normalize_kind(kind)
        ref = slugify(ref)
        with Session(self.engine) as s:
            rows = list(s.exec(
                select(EntityImage).where(
                    EntityImage.kind == kind, EntityImage.ref_slug == ref)
            ).all())
            for r in rows:
                s.delete(r)
            s.commit()
        return len(rows)

    def invalidate_context(self, kind: str, ref: str, context: str) -> int:
        kind = normalize_kind(kind)
        ref = slugify(ref)
        ckey = context_key(context)
        with Session(self.engine) as s:
            rows = list(s.exec(
                select(EntityImage).where(
                    EntityImage.kind == kind, EntityImage.ref_slug == ref,
                    EntityImage.context_key == ckey)
            ).all())
            for r in rows:
                s.delete(r)
            s.commit()
        return len(rows)

    def invalidate_stale(self, kind: str, ref: str, keep_descriptor_hash: str) -> int:
        """Delete a subject's images whose descriptor no longer matches (kept the new look)."""
        kind = normalize_kind(kind)
        ref = slugify(ref)
        removed = 0
        with Session(self.engine) as s:
            rows = list(s.exec(
                select(EntityImage).where(
                    EntityImage.kind == kind, EntityImage.ref_slug == ref)
            ).all())
            for r in rows:
                if r.descriptor_hash != keep_descriptor_hash:
                    s.delete(r)
                    removed += 1
            s.commit()
        return removed

    def _enforce_global_cap(self, session: Session, cfg) -> int:
        """LRU-evict oldest, least-used rows beyond the global cap."""
        cap = cfg.max_total_images
        rows = list(session.exec(select(EntityImage)).all())
        if len(rows) <= cap:
            return 0
        # Least valuable first: low use_count, then oldest last_used.
        rows.sort(key=lambda r: (r.use_count, r.last_used_at))
        to_remove = rows[: len(rows) - cap]
        for r in to_remove:
            session.delete(r)
        return len(to_remove)
