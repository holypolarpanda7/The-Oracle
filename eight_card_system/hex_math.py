"""
Hex math utilities for the Eight Card System.

Uses axial coordinates (q, r) with flat-top hexagons.
Reference: https://www.redblobgames.com/grids/hexagons/
"""

from __future__ import annotations

import math
from dataclasses import dataclass


# Flat-top hex direction vectors in axial coordinates
# Order: E, NE, NW, W, SW, SE
DIRECTIONS = {
    "E":  (1, 0),
    "NE": (1, -1),
    "NW": (0, -1),
    "W":  (-1, 0),
    "SW": (-1, 1),
    "SE": (0, 1),
}

DIRECTION_LIST = ["E", "NE", "NW", "W", "SW", "SE"]


@dataclass(frozen=True, slots=True)
class Hex:
    """An axial hex coordinate."""
    q: int
    r: int

    @property
    def s(self) -> int:
        """Cube coordinate s (derived: q + r + s = 0)."""
        return -self.q - self.r

    def neighbor(self, direction: str) -> Hex:
        """Return the adjacent hex in the given direction."""
        dq, dr = DIRECTIONS[direction]
        return Hex(self.q + dq, self.r + dr)

    def neighbors(self) -> list[Hex]:
        """Return all 6 neighbors."""
        return [self.neighbor(d) for d in DIRECTION_LIST]

    def distance(self, other: Hex) -> int:
        """Hex distance (number of steps)."""
        return max(abs(self.q - other.q), abs(self.r - other.r), abs(self.s - other.s))

    def ring(self, radius: int) -> list[Hex]:
        """Return all hexes at exactly `radius` distance from this hex."""
        if radius == 0:
            return [self]
        results = []
        # Start at the SW direction, radius steps out
        h = Hex(self.q + DIRECTIONS["SW"][0] * radius,
                self.r + DIRECTIONS["SW"][1] * radius)
        for direction in DIRECTION_LIST:
            for _ in range(radius):
                results.append(h)
                h = h.neighbor(direction)
        return results

    def spiral(self, radius: int) -> list[Hex]:
        """Return all hexes within `radius` distance (inclusive), spiral order."""
        results = [self]
        for r in range(1, radius + 1):
            results.extend(self.ring(r))
        return results

    def disk(self, radius: int) -> list[Hex]:
        """Return all hexes within `radius` distance (inclusive), sorted."""
        results = []
        for q in range(-radius, radius + 1):
            r_min = max(-radius, -q - radius)
            r_max = min(radius, -q + radius)
            for r in range(r_min, r_max + 1):
                results.append(Hex(self.q + q, self.r + r))
        return results


def hex_to_pixel(h: Hex, size: float) -> tuple[float, float]:
    """Convert axial hex coordinate to pixel center (flat-top)."""
    x = size * (3 / 2 * h.q)
    y = size * (math.sqrt(3) / 2 * h.q + math.sqrt(3) * h.r)
    return x, y


def pixel_to_hex(x: float, y: float, size: float) -> Hex:
    """Convert pixel coordinate to nearest axial hex (flat-top)."""
    q = (2 / 3 * x) / size
    r = (-1 / 3 * x + math.sqrt(3) / 3 * y) / size
    return _axial_round(q, r)


def hex_corners(center: tuple[float, float], size: float) -> list[tuple[float, float]]:
    """Return the 6 corner pixel positions of a flat-top hex."""
    corners = []
    for i in range(6):
        angle_deg = 60 * i
        angle_rad = math.radians(angle_deg)
        corners.append((
            center[0] + size * math.cos(angle_rad),
            center[1] + size * math.sin(angle_rad),
        ))
    return corners


def _axial_round(q: float, r: float) -> Hex:
    """Round fractional axial coordinates to nearest hex."""
    s = -q - r
    rq = round(q)
    rr = round(r)
    rs = round(s)
    q_diff = abs(rq - q)
    r_diff = abs(rr - r)
    s_diff = abs(rs - s)
    if q_diff > r_diff and q_diff > s_diff:
        rq = -rr - rs
    elif r_diff > s_diff:
        rr = -rq - rs
    return Hex(int(rq), int(rr))
