"""Where the water lies, and how far it is down to the bottom.

Water was drawn as a coloured floor at the same height as the bank beside it,
and — because ``~`` and ``W`` were on :data:`vtt.terrain.SOFT_GROUND` — that
floor was then averaged with its neighbours and given the ordinary ground
ripple. So a swamp's pools tilted: a pool with a hummock on one side ran
visibly UPHILL into it, which is the one thing water never does.

Two separate faults, and both are fixed here rather than in the renderers.

**A liquid surface is LEVEL.** That is what being a liquid means, and it is why
the codes came off ``SOFT_GROUND``. The exception is a board fought UNDER the
water, where there is no surface in view at all and the seabed is ordinary
ground that should roll — which the ``seabed-*`` skins say for themselves,
because the skin answers the softness question first.

**And water lies in a DEPRESSION.** Level is not enough on its own: a pool
flush with the ground around it is paint on a floor. :func:`sink` cuts the BED
below its own shore, as a basin rather than a trench — the depth grows with the
distance from the nearest bank, so the shallows are at the edge where anyone
would expect them.

The depth is real elevation, in whole feet, and every rule reads it: wading out
of a pool is the foot-per-foot climb the SRD charges. It is capped under a
LEDGE on purpose, so stepping into water is never reported as a fall.

:func:`surfaces` is the other half — the level sheet drawn back on top, so the
depression reads as full of water rather than as a hole. It is traced ONCE, on
the server, and shipped in ``state()``: a pool's surface is a property of the
whole POOL and no square can see one, which is the same argument that put
:mod:`vtt.hull` on this side of the wire.
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

from .terrain import WATER_CODES, WATERLINE_DROP_FT

#: How deep each kind of water gets, in feet, and how fast it deepens per
#: square from the bank.
#:
#: Shallow water is ankle-to-knee — enough to see a bank and cost a couple of
#: feet of climbing to leave, and no more. Deep water is swimmers-only by the
#: rules already, so the only question its depth answers is what the picture
#: shows; six feet is over a Medium creature's head, which is what the tile
#: means. Both stay under ``LEDGE_FT``, so walking into water is never a fall.
DEPTH_FT: dict[str, tuple[int, int]] = {"~": (2, 1), "W": (6, 2)}


def _code(rows: Sequence[str], x: int, z: int) -> Optional[str]:
    if 0 <= z < len(rows) and 0 <= x < len(rows[z]):
        return rows[z][x]
    return None


def pools(rows: Sequence[str]) -> list[list[tuple[int, int]]]:
    """Every connected body of water on this grid, four-connected.

    A body, not a square: the whole point of both halves below is that a pool
    has ONE surface and one shore, and neither is a fact any single square of
    it holds.
    """
    seen: set[tuple[int, int]] = set()
    out: list[list[tuple[int, int]]] = []
    for z in range(len(rows)):
        for x in range(len(rows[z])):
            if rows[z][x] not in WATER_CODES or (x, z) in seen:
                continue
            body: list[tuple[int, int]] = []
            stack = [(x, z)]
            seen.add((x, z))
            while stack:
                ax, az = stack.pop()
                body.append((ax, az))
                for bx, bz in ((ax - 1, az), (ax + 1, az),
                               (ax, az - 1), (ax, az + 1)):
                    if (bx, bz) in seen:
                        continue
                    if _code(rows, bx, bz) in WATER_CODES:
                        seen.add((bx, bz))
                        stack.append((bx, bz))
            out.append(body)
    return out


def _shore(rows: Sequence[str], body: Iterable[tuple[int, int]],
           elevation: dict) -> tuple[list[tuple[int, int]], Optional[int]]:
    """The dry squares this body touches, and the LOWEST of them.

    Lowest rather than mean, because that is the one a full pool would spill
    over — and the complaint this module exists to answer is water standing
    higher than the ground next to it.
    """
    edge: list[tuple[int, int]] = []
    for ax, az in body:
        for bx, bz in ((ax - 1, az), (ax + 1, az), (ax, az - 1), (ax, az + 1)):
            c = _code(rows, bx, bz)
            if c is not None and c not in WATER_CODES:
                edge.append((bx, bz))
    if not edge:
        return [], None
    return edge, min(int(elevation.get(f"{x},{z}", 0) or 0) for x, z in edge)


def _from_shore(rows: Sequence[str],
                body: Iterable[tuple[int, int]]) -> dict[tuple[int, int], int]:
    """How many squares each square of a body is from open bank."""
    body = set(body)
    dist: dict[tuple[int, int], int] = {}
    frontier = [sq for sq in body
                if any(_code(rows, sq[0] + dx, sq[1] + dz) not in WATER_CODES
                       for dx, dz in ((-1, 0), (1, 0), (0, -1), (0, 1)))]
    for sq in frontier:
        dist[sq] = 1
    step = 1
    while frontier:
        step += 1
        nxt = []
        for ax, az in frontier:
            for bx, bz in ((ax - 1, az), (ax + 1, az),
                           (ax, az - 1), (ax, az + 1)):
                if (bx, bz) in body and (bx, bz) not in dist:
                    dist[(bx, bz)] = step
                    nxt.append((bx, bz))
        frontier = nxt
    # A body with no bank at all (a board that is all water) never entered the
    # frontier; it is not a pool in anything and is left alone.
    return dist


def sink(rows: Sequence[str], elevation: dict, *, mode: str = "walk") -> int:
    """Cut every pool's BED below its own shore. Returns squares changed.

    Only ever LOWERS a square, which is what makes it safe to run over a
    generator that already dug its own channel — a sewer's sludge run is
    already below the walkways beside it, and the answer there is to leave it
    exactly where the generator put it.

    Skipped entirely off dry land: a board whose MEDIUM is the water has no
    surface in view, and its ``~`` is the sand shelf a swimmer is looking
    down at rather than a pool anyone is standing beside.
    """
    if (mode or "walk") != "walk":
        return 0
    changed = 0
    for body in pools(rows):
        _, base = _shore(rows, body, elevation)
        if base is None:
            continue
        dist = _from_shore(rows, body)
        for (x, z), d in dist.items():
            cap, per = DEPTH_FT.get(rows[z][x], (2, 1))
            target = base - min(cap, d * per)
            key = f"{x},{z}"
            if target < int(elevation.get(key, 0) or 0):
                elevation[key] = target
                changed += 1
    return changed


def surfaces(rows: Sequence[str], elevation: Optional[dict] = None, *,
             mode: str = "walk") -> dict[str, float]:
    """The level sheet over each pool: ``{"x,y": feet}``, sparse.

    Only where there is something to draw — a square whose bed was never cut
    is flush with its bank, and a sheet laid on it would z-fight the floor for
    no gain. That is also what keeps a swim board out of this: nothing there
    was sunk, so nothing there gets a surface.
    """
    if (mode or "walk") != "walk":
        return {}
    elev = elevation or {}
    out: dict[str, float] = {}
    for body in pools(rows):
        _, base = _shore(rows, body, elev)
        if base is None:
            continue
        top = base - WATERLINE_DROP_FT
        for x, z in body:
            key = f"{x},{z}"
            if top > float(elev.get(key, 0) or 0) + 1e-6:
                out[key] = top
    return out
