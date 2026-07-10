"""Offline demo for the imagery store.

Runs WITHOUT a GPU or ComfyUI: a fake diffusion client renders solid-color PIL
images so the bucketing (subject x context), reuse-when-full, permanent-change
invalidation, and eviction logic can be exercised end to end.
"""
from __future__ import annotations

import io
import random

from PIL import Image
from sqlmodel import create_engine, SQLModel

from imagery import ImageStore, ImageKind
import imagery.models  # noqa: F401  (register table)


class FakeClient:
    """Stand-in for ComfyClient that draws a colored square instead of calling a GPU."""

    def generate(self, positive, negative="", *, width=512, height=512, steps=20, seed=None):
        rnd = random.Random(seed)
        color = (rnd.randint(30, 220), rnd.randint(30, 220), rnd.randint(30, 220))
        img = Image.new("RGB", (min(width, 512), min(height, 512)), color)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def _line(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def main() -> None:
    from game_config import ImageryConfig

    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    cfg = ImageryConfig()          # defaults, but backend is our FakeClient
    store = ImageStore(engine=eng, config=cfg, client=FakeClient())

    _line("Bucketing: a wolf in two environments")
    for env in ("desert", "jungle", "desert", "desert", "desert"):
        r = store.ensure_image("creature", "dire wolf", look="lean, scarred",
                               context=env, ref_slug="dire-wolf")
        tag = "reused" if r.reused else "generated"
        print(f"  desert/jungle bucket -> context={r.context_key:<8} {tag}"
              f"  (id={r.image_id}, {len(r.image)} bytes)")

    print("  desert bucket now holds:",
          len(store.list_for("creature", "dire-wolf", context="desert")), "images (cap 3)")
    print("  jungle bucket now holds:",
          len(store.list_for("creature", "dire-wolf", context="jungle")), "images")

    _line("NPC Jim across contexts")
    for ctx in ("town", "town", "town", "desert outside town", "town in winter"):
        r = store.ensure_image("npc", "Jim the blacksmith", look="burly, soot-stained",
                               context=ctx, ref_slug="jim")
        print(f"  jim @ {r.context_key:<20} -> {'reused' if r.reused else 'generated'}")
    print("  total stored for jim:", len(store.list_for("npc", "jim")))

    _line("Permanent change: Jim loses a leg -> invalidate all his images")
    removed = store.invalidate_subject("npc", "jim")
    print(f"  removed {removed} images; jim now has",
          len(store.list_for("npc", "jim")), "images")
    r = store.ensure_image("npc", "Jim the blacksmith",
                           look="burly, soot-stained, missing his left leg, wooden peg",
                           context="town", ref_slug="jim")
    print("  regenerated fresh:", r.caption, "generated=", r.generated)

    _line("Throwaway (temp) image — never stored")
    t = store.generate_temp("creature", "phoenix", look="wreathed in golden fire")
    print(f"  temp phoenix: temp={t.temp} stored=(never)  image_id={t.image_id}")

    _line("Store stats")
    print(" ", store.stats())


if __name__ == "__main__":
    main()
