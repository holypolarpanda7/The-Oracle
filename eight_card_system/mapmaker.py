"""
Player cartography — maps are in-game ARTIFACTS, not a UI freebie.

There is no world-map screen. A map exists only if someone drafted it with
Cartographer's Tools (SRD: 15 gp, DC 15 Wisdom check, proficiency bonus if
tool-proficient, advantage with a relevant skill) or bought it from a
map-maker in a settlement. And crucially: a FAILED draft still produces a
map — a confidently wrong one. Distances stretch, bearings rotate, a place
goes missing. The player holding it doesn't know which kind they own.

A finished map is TWO LAYERS, and the split is the same one ``vtt/art.py``
makes for battlemaps:

* the **terrain wash** is painted by the diffusion model (the ``worldmap``
  image kind, running the wowmap LoRA) from a survey of what biomes actually
  lie in which direction — country, drawn, at miles per inch;
* the **ink** is drawn by this module from the world's real spherical
  coordinates — every dot, name, route, compass and scale bar.

The model never places a landmark and never writes a word (see
``_WORLDMAP_NEGATIVE``): the picture is a texture, the coordinates are the
truth. Offline, the wash is simply absent and the ink lands on bare parchment,
exactly as it did before there was a GPU in the loop.

The survey samples ONLY the places on the map — the ones the drafter actually
knows — so an ignorant cartographer gets vague country as well as a sparse
sheet, and the knowledge gate holds in the art as well as in the data.
Distortion is deterministic per (drafter, day), and is applied BEFORE the
survey, so a bad map is wrong in a self-consistent way: its forests sit where
its badly-placed forest is drawn.
"""
from __future__ import annotations

import hashlib
import io
import math
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image, ImageDraw

from . import geo

# Local drafts cover what the drafter can reasonably survey from here.
DRAFT_RADIUS_MI = 25.0
# A bought regional map is the map-maker's compiled knowledge: wider, and it
# marks rumored (unexplored) sites — the tie-in to world generation.
PURCHASE_RADIUS_MI = 60.0
MAP_PRICE_GP = 25          # regional map from a cartographer
TOOLS_ITEM = "Cartographer's Tools"

_PARCHMENT = (233, 219, 182)
_INK = (62, 44, 28)
_FAINT = (139, 117, 86)
_RUMOR = (146, 116, 84)

_SCALE_R = {"region": 0, "settlement": 7, "town": 7, "city": 9, "village": 5,
            "district": 3, "building": 3, "poi": 4, "wilds": 4, "dungeon": 5}


def _font(size: int):
    """A legible label face, falling back to the bitmap default.

    Pillow's unsized default is ~11 px, which is illegible over a painted
    terrain wash at 768 px. ``load_default(size=...)`` has been available since
    Pillow 10.1; older builds ignore the argument and we take what we get.
    """
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


_LABEL_FONT = _font(15)
_TITLE_FONT = _font(20)


def _project(center: tuple[float, float], coords: tuple[float, float]) -> tuple[float, float]:
    """Equirectangular projection to local miles (x east, y north)."""
    lat0, lon0 = map(math.radians, center)
    lat, lon = map(math.radians, coords)
    dlon = lon - lon0
    if dlon > math.pi:
        dlon -= 2 * math.pi
    elif dlon < -math.pi:
        dlon += 2 * math.pi
    x = dlon * math.cos(lat0) * geo.WORLD_RADIUS_MI
    y = (lat - lat0) * geo.WORLD_RADIUS_MI
    return x, y


# The nine sectors of a drawn sheet, as a traveller would name them.
_SECTORS = [
    ("northwest", -1, 1), ("north", 0, 1), ("northeast", 1, 1),
    ("west", -1, 0), ("centre", 0, 0), ("east", 1, 0),
    ("southwest", -1, -1), ("south", 0, -1), ("southeast", 1, -1),
]


@dataclass
class TerrainSurvey:
    """What country this sheet covers, and which way each kind of it lies."""
    sectors: dict[str, str] = field(default_factory=dict)   # sector -> biome
    shares: dict[str, float] = field(default_factory=dict)  # biome -> 0..1
    climate: str = "temperate"
    signature: str = ""

    @property
    def dominant(self) -> str:
        return max(self.shares, key=self.shares.get) if self.shares else ""

    def prompt_look(self) -> str:
        """Directional description of the country, for the wash render.

        Grouped by biome rather than listed per sector, so the model is told
        "forest across the north and northeast" once instead of being handed
        the same clause three times — repetition in a CLIP prompt reads as
        emphasis, and emphasising a sector doesn't make it appear there.
        """
        from .placelore import terrain_words

        by_biome: dict[str, list[str]] = {}
        for sector, biome in self.sectors.items():
            by_biome.setdefault(biome, []).append(sector)
        # Biggest share first: the sheet's main country leads the prompt.
        order = sorted(by_biome, key=lambda b: -self.shares.get(b, 0.0))
        parts = []
        for biome in order:
            where = by_biome[biome]
            words = terrain_words(biome, "map")
            if "centre" in where and len(where) == 1:
                parts.append(f"{words} at the centre")
            elif len(where) >= 6:
                parts.append(f"{words} across the whole sheet")
            else:
                named = [w for w in where if w != "centre"]
                parts.append(f"{words} to the {' and '.join(named[:3])}"
                             if named else words)
        return "; ".join(parts)


def survey_terrain(pts: list[dict], reach_mi: float, center: tuple[float, float],
                   *, default_biome: str = "") -> TerrainSurvey:
    """Which biome lies in each sector of the sheet, from the mapped places.

    ``pts`` are the already-projected (and, on a flawed map, already-distorted)
    places in local miles. Each sector takes the biome of the nearest place to
    its middle; sectors with nothing within reach fall back to the climate
    band, which is defined everywhere on the globe.
    """
    climate = geo.climate_for(center)
    fallback = default_biome or _CLIMATE_FALLBACK.get(climate, "hills")
    known = [p for p in pts if p.get("biome")]

    sectors: dict[str, str] = {}
    third = max(reach_mi, 1.0) * 2.0 / 3.0
    for name, sx, sy in _SECTORS:
        mx, my = sx * third, sy * third
        best, best_d = fallback, float("inf")
        for p in known:
            d = math.hypot(p["x"] - mx, p["y"] - my)
            if d < best_d:
                best, best_d = str(p["biome"]), d
        sectors[name] = best

    counts = Counter(sectors.values())
    total = float(sum(counts.values())) or 1.0
    shares = {b: n / total for b, n in counts.items()}
    sig = hashlib.sha256(
        "|".join(f"{k}={v}" for k, v in sorted(sectors.items())).encode("utf-8")
    ).hexdigest()[:16]
    return TerrainSurvey(sectors=sectors, shares=shares, climate=climate,
                         signature=sig)


# Climate -> the country to assume where the drafter knows nothing. Mirrors
# placelore._CLIMATE_BIOME; kept local so a map can be drawn with no graph.
_CLIMATE_FALLBACK = {
    "arctic": "mountains", "subarctic": "forest", "cool temperate": "forest",
    "temperate": "farmland", "warm temperate": "hills", "arid": "desert",
    "desert": "desert", "subtropical": "forest", "tropical": "forest",
}

#: Art direction for the terrain wash. Replaces the house style outright — the
#: standing look is rim-lit jewel-toned key art, which is exactly wrong for a
#: chart. Same override the mundane-item renders use, for the same reason.
_MAP_STYLE = (
    "antique hand-drawn cartography, painted parchment map, soft muted inks, "
    "stylised terrain relief, aged paper texture, flat overhead view"
)


def render_terrain_wash(survey: TerrainSurvey, *, size: int, seed: str,
                        store=None, area: str = "") -> Optional[bytes]:
    """Paint the country under the ink. None whenever art isn't available.

    Never raises and never blocks the artifact: a missing GPU, a disabled
    imagery config or a failed render all mean "no wash", and the ink lands on
    plain parchment. Cached by the survey signature, so two cartographers who
    walked the same country share one render.
    """
    if store is None:
        try:
            from imagery import ImageStore
            store = ImageStore()
        except Exception as e:
            print(f"[mapmaker] imagery unavailable: {e}")
            return None
    try:
        res = store.ensure_image(
            "worldmap", area or "unnamed country",
            look=survey.prompt_look(),
            context=survey.climate,
            ref_slug=f"survey-{survey.signature}",
            style_prompt=_MAP_STYLE,
            width=size, height=size, store_width=size,
            # Seeded from the survey, not the drafter: the same country drawn
            # twice is the same country.
            seed=int(survey.signature[:8], 16) & 0x7FFFFFFF,
            max_per_bucket=1,
        )
    except Exception as e:
        print(f"[mapmaker] terrain wash failed: {e}")
        return None
    if res is None or res.offline or not res.image:
        return None
    return res.image


def _compose_parchment(size: int, wash: Optional[bytes], seed: str) -> Image.Image:
    """The sheet the ink is drawn on: painted country, or bare parchment.

    The wash is veiled with a parchment tint before anything is drawn over it.
    Without that, a dark forest render swallows the ink — and an unreadable
    name is worse than no picture, because the map is a rules artifact first
    and a nice image second.
    """
    base = Image.new("RGB", (size, size), _PARCHMENT)
    if wash:
        try:
            art = Image.open(io.BytesIO(wash)).convert("RGB")
            if art.size != (size, size):
                art = art.resize((size, size), Image.LANCZOS)
            # 0.42 keeps the terrain clearly legible while guaranteeing every
            # label has parchment behind it.
            base = Image.blend(art, base, 0.42)
        except Exception as e:
            print(f"[mapmaker] wash composite failed: {e}")
            base = Image.new("RGB", (size, size), _PARCHMENT)

    d = ImageDraw.Draw(base)
    d.rectangle([6, 6, size - 7, size - 7], outline=_FAINT, width=2)
    # Aging blotches sell the artifact on BARE parchment. Over a painted wash
    # they read as pale blobs floating on the country, so they go much fainter
    # there — the paint already carries its own age.
    stain_rng = random.Random(f"stain:{seed}")
    stain = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stain)
    alpha = 32 if wash else 90
    for _ in range(7):
        cx, cy = stain_rng.uniform(0, size), stain_rng.uniform(0, size)
        r = stain_rng.uniform(12, 42)
        sd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(226, 210, 170, alpha))
    base = Image.alpha_composite(base.convert("RGBA"), stain).convert("RGB")
    return base


def render_map(
    places: list[dict],
    center: tuple[float, float],
    *,
    title: str,
    flawed: bool = False,
    seed: str = "",
    subtitle: str = "",
    size: int = 768,
    store=None,
    paint_terrain: bool = True,
    area: str = "",
) -> bytes:
    """Draw a parchment map PNG of ``places`` around ``center``.

    Each place: {"name", "coords": (lat, lon), "scale": str, "rumored": bool,
    "biome": str}. ``biome`` is optional and only feeds the painted terrain.
    ``flawed=True`` applies the failed-draft distortion: a global rotation,
    per-place jitter, and (when there's enough to lose) one dropped place —
    all deterministic for ``seed``.

    ``paint_terrain`` asks for the diffusion wash under the ink; it degrades
    silently to bare parchment whenever art isn't available, so no caller has
    to care whether there's a GPU up.
    """
    rng = random.Random(f"map:{seed}")
    pts = []
    for p in places:
        x, y = _project(center, p["coords"])
        pts.append({**p, "x": x, "y": y})

    if flawed and pts:
        theta = math.radians(rng.uniform(20, 55) * rng.choice((-1, 1)))
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        survivors = []
        drop_idx = rng.randrange(len(pts)) if len(pts) > 3 else -1
        for i, p in enumerate(pts):
            if i == drop_idx and abs(p["x"]) + abs(p["y"]) > 1.0:
                continue  # a whole landmark, quietly forgotten
            stretch = rng.uniform(0.65, 1.45)
            x, y = p["x"] * stretch, p["y"] * stretch
            p["x"], p["y"] = x * cos_t - y * sin_t, x * sin_t + y * cos_t
            survivors.append(p)
        pts = survivors

    reach = max([max(abs(p["x"]), abs(p["y"])) for p in pts] + [5.0])
    pad = 70
    half = size / 2 - pad
    px_per_mi = half / (reach * 1.15)

    # Survey AFTER the distortion, so a bad map's country agrees with its own
    # bad geography: the forest is painted where this sheet puts the forest.
    wash = None
    if paint_terrain:
        survey = survey_terrain(pts, reach, center)
        wash = render_terrain_wash(survey, size=size, seed=seed, store=store,
                                   area=area or title)
    img = _compose_parchment(size, wash, seed)
    d = ImageDraw.Draw(img)

    def to_px(x_mi: float, y_mi: float) -> tuple[float, float]:
        return size / 2 + x_mi * px_per_mi, size / 2 - y_mi * px_per_mi

    def ink_text(xy, text, fill, *, large: bool = False) -> None:
        """Label outlined in parchment, so names survive the painted terrain.

        Dark ink on a dark forest wash is unreadable, and an unreadable name
        makes the sheet useless as the rules artifact it primarily is.
        """
        d.text(xy, text, fill=fill, font=(_TITLE_FONT if large else _LABEL_FONT),
               stroke_width=2, stroke_fill=_PARCHMENT)

    # Routes: faint lines from the center to each site (traveler's sketch).
    # A hairline vanishes into painted terrain, so the ink thickens when there
    # is country underneath it.
    route_w = 2 if wash else 1
    cx, cy = to_px(0, 0)
    for p in pts:
        x, y = to_px(p["x"], p["y"])
        if p.get("rumored"):
            # dashed rumor-line
            steps = 14
            for s in range(0, steps, 2):
                x1 = cx + (x - cx) * s / steps
                y1 = cy + (y - cy) * s / steps
                x2 = cx + (x - cx) * (s + 1) / steps
                y2 = cy + (y - cy) * (s + 1) / steps
                d.line([x1, y1, x2, y2], fill=_RUMOR, width=route_w)
        else:
            d.line([cx, cy, x, y], fill=_INK if wash else _FAINT, width=route_w)

    for p in pts:
        x, y = to_px(p["x"], p["y"])
        r = _SCALE_R.get(str(p.get("scale", "poi")).lower(), 4)
        color = _RUMOR if p.get("rumored") else _INK
        if p.get("rumored"):
            d.ellipse([x - r, y - r, x + r, y + r], outline=color, width=2)
        else:
            d.ellipse([x - r, y - r, x + r, y + r], fill=color)
        label = p["name"] + (" (rumored)" if p.get("rumored") else "")
        ink_text((x + r + 3, y - 6), label, color)

    # "You are here" center mark (the drafting spot).
    d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], outline=_INK, width=2)

    # Compass rose (a flawed map's north is confidently wrong, but drawn
    # straight up all the same — that's the trap).
    nx, ny = size - 52, 58
    d.line([nx, ny + 18, nx, ny - 18], fill=_INK, width=2)
    d.polygon([(nx - 5, ny - 10), (nx + 5, ny - 10), (nx, ny - 22)], fill=_INK)
    ink_text((nx - 4, ny + 22), "N", _INK)

    # Scale bar (10 miles).
    bar = 10 * px_per_mi
    d.line([pad, size - 34, pad + bar, size - 34], fill=_INK, width=2)
    ink_text((pad, size - 30), "10 miles", _INK)

    ink_text((pad, 20), title, _INK, large=True)
    if subtitle:
        ink_text((pad, 38), subtitle, _FAINT)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def known_place_ids(graph, pc_ref) -> set[int]:
    """Every place this PC actually KNOWS: visited, or learned about.

    You can't draw what you've never seen or heard described. Knowledge =
    the PC's full ``located_in`` history (open AND closed — everywhere they
    have ever stood), the PART_OF ancestors of those places (standing in the
    tavern means knowing Millbrook), and every ``knows_about`` edge (told by
    an NPC, read from a bought map, learned through play).
    """
    from sqlmodel import Session, select
    from .models import Entity, Relation, RelationType

    known: set[int] = set()
    with Session(graph.engine) as s:
        pc = graph._resolve_entity(s, pc_ref)
        if pc is None:
            return known
        rels = s.exec(select(Relation).where(Relation.src_id == pc.id)).all()
        frontier: list[int] = []
        for r in rels:
            if r.rel_type == RelationType.LOCATED_IN:
                frontier.append(r.dst_id)          # any validity: ever stood there
            elif r.rel_type == RelationType.KNOWS_ABOUT:
                known.add(r.dst_id)
        # Visited places + their containing places (2 ancestor hops).
        for pid in frontier:
            current = pid
            for _ in range(3):
                if current in known:
                    break
                known.add(current)
                parent = s.exec(select(Relation).where(
                    Relation.src_id == current,
                    Relation.rel_type == RelationType.PART_OF,
                    Relation.valid_to == None,  # noqa: E711
                )).first()
                if parent is None:
                    break
                current = parent.dst_id
    return known


def gather_places_by_slug(graph, slugs) -> list[dict]:
    """Re-read specific places, for a sheet that already charts them.

    A map ACCUMULATES: it keeps every site it has ever recorded, wherever the
    cartographer happens to be standing when they next open it. Re-surveying
    by radius alone would quietly drop the far half of the sheet the moment
    its owner walked away — so a revision reads its own recorded slugs and
    only ever ADDS to them.

    Places that have since been archived are dropped (the site is gone, and a
    map of it would be a map of nothing); everything else keeps its dot, with
    whatever the world now knows about it.
    """
    from sqlmodel import Session, select
    from . import placelore
    from .models import Entity

    wanted = [s for s in (slugs or []) if s]
    if not wanted:
        return []
    out: list[dict] = []
    with Session(graph.engine) as s:
        rows = s.exec(select(Entity).where(Entity.slug.in_(wanted),  # type: ignore[attr-defined]
                                           Entity.type == "place")).all()
        for e in rows:
            c = geo.coords_from_attrs(e.attributes)
            if c is None or e.status == "archived":
                continue
            out.append({
                "name": e.name,
                "slug": e.slug,
                "coords": c,
                "scale": (e.attributes or {}).get("scale") or e.subtype or "poi",
                "rumored": e.status == "unexplored",
                "biome": placelore._biome_of(e),
            })
    return out


def merge_places(existing: list[dict], found: list[dict]) -> list[dict]:
    """Union two site lists by slug, letting the fresher reading win.

    ``found`` is what the world says now, so it replaces a stale entry — a
    rumored stub the party has since walked into stops being drawn as a rumor.
    """
    by_slug: dict[str, dict] = {}
    for p in list(existing) + list(found):
        key = str(p.get("slug") or p.get("name") or "")
        if key:
            by_slug[key] = p
    return list(by_slug.values())


def gather_mappable_places(
    graph, center_ref, *, radius_mi: float, include_rumored: bool = False,
    known_ids: Optional[set[int]] = None,
) -> tuple[Optional[tuple[float, float]], list[dict]]:
    """Places with coords within radius of an anchor entity's position.

    ``known_ids`` (a drafting PC's knowledge from ``known_place_ids``)
    restricts the map to places the drafter has visited or learned about —
    someone else exploring the region doesn't put it in YOUR head. A
    map-maker's purchased map passes no filter (their knowledge, not yours)
    and includes unexplored stubs as rumors when ``include_rumored``.
    """
    from sqlmodel import Session, select
    from . import placelore
    from .models import Entity

    with Session(graph.engine) as s:
        anchor = graph._resolve_entity(s, center_ref)
        if anchor is None:
            return None, []
        center = graph._coords_in_db(s, anchor)
        if center is None:
            return None, []
        out: list[dict] = []
        for e in s.exec(select(Entity).where(Entity.type == "place")).all():
            c = geo.coords_from_attrs(e.attributes)
            if c is None:
                continue
            dist = geo.distance_mi(center, c)
            if dist > radius_mi:
                continue
            unexplored = e.status == "unexplored"
            if e.status == "archived" or (unexplored and not include_rumored):
                continue
            if known_ids is not None and e.id not in known_ids:
                continue
            scale = str((e.attributes or {}).get("scale") or e.subtype or "").lower()
            if scale == "region":
                continue  # regions title maps; they aren't dots on them
            out.append({
                "name": e.name,
                "slug": e.slug,
                "coords": c,
                "scale": (e.attributes or {}).get("scale") or e.subtype or "poi",
                "rumored": unexplored,
                # The OUTDOOR terrain only — what the sheet paints is country,
                # so a tavern contributes the farmland it stands in, not its
                # floorboards (see placelore's terrain/surface split).
                "biome": placelore._biome_of(e),
            })
    return center, out
