"""
Player cartography — maps are in-game ARTIFACTS, not a UI freebie.

There is no world-map screen. A map exists only if someone drafted it with
Cartographer's Tools (SRD: 15 gp, DC 15 Wisdom check, proficiency bonus if
tool-proficient, advantage with a relevant skill) or bought it from a
map-maker in a settlement. And crucially: a FAILED draft still produces a
map — a confidently wrong one. Distances stretch, bearings rotate, a place
goes missing. The player holding it doesn't know which kind they own.

Renders parchment-style PNGs from the world's real spherical coordinates.
Distortion is deterministic per (drafter, day) so re-examining the same bad
map shows the same bad map.
"""
from __future__ import annotations

import io
import math
import random
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


def render_map(
    places: list[dict],
    center: tuple[float, float],
    *,
    title: str,
    flawed: bool = False,
    seed: str = "",
    subtitle: str = "",
    size: int = 768,
) -> bytes:
    """Draw a parchment map PNG of ``places`` around ``center``.

    Each place: {"name", "coords": (lat, lon), "scale": str, "rumored": bool}.
    ``flawed=True`` applies the failed-draft distortion: a global rotation,
    per-place jitter, and (when there's enough to lose) one dropped place —
    all deterministic for ``seed``.
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

    img = Image.new("RGB", (size, size), _PARCHMENT)
    d = ImageDraw.Draw(img)
    # Weathered edge + faint aging blotches for the artifact feel.
    d.rectangle([6, 6, size - 7, size - 7], outline=_FAINT, width=2)
    stain_rng = random.Random(f"stain:{seed}")
    for _ in range(7):
        cx, cy = stain_rng.uniform(0, size), stain_rng.uniform(0, size)
        r = stain_rng.uniform(12, 42)
        d.ellipse([cx - r, cy - r, cx + r, cy + r],
                  fill=(226, 210, 170))

    def to_px(x_mi: float, y_mi: float) -> tuple[float, float]:
        return size / 2 + x_mi * px_per_mi, size / 2 - y_mi * px_per_mi

    # Routes: faint lines from the center to each site (traveler's sketch).
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
                d.line([x1, y1, x2, y2], fill=_RUMOR, width=1)
        else:
            d.line([cx, cy, x, y], fill=_FAINT, width=1)

    for p in pts:
        x, y = to_px(p["x"], p["y"])
        r = _SCALE_R.get(str(p.get("scale", "poi")).lower(), 4)
        color = _RUMOR if p.get("rumored") else _INK
        if p.get("rumored"):
            d.ellipse([x - r, y - r, x + r, y + r], outline=color, width=2)
        else:
            d.ellipse([x - r, y - r, x + r, y + r], fill=color)
        label = p["name"] + (" (rumored)" if p.get("rumored") else "")
        d.text((x + r + 3, y - 6), label, fill=color)

    # "You are here" center mark (the drafting spot).
    d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], outline=_INK, width=2)

    # Compass rose (a flawed map's north is confidently wrong, but drawn
    # straight up all the same — that's the trap).
    nx, ny = size - 52, 58
    d.line([nx, ny + 18, nx, ny - 18], fill=_INK, width=2)
    d.polygon([(nx - 5, ny - 10), (nx + 5, ny - 10), (nx, ny - 22)], fill=_INK)
    d.text((nx - 4, ny + 22), "N", fill=_INK)

    # Scale bar (10 miles).
    bar = 10 * px_per_mi
    d.line([pad, size - 34, pad + bar, size - 34], fill=_INK, width=2)
    d.text((pad, size - 30), "10 miles", fill=_INK)

    d.text((pad, 22), title, fill=_INK)
    if subtitle:
        d.text((pad, 38), subtitle, fill=_FAINT)

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
            })
    return center, out
