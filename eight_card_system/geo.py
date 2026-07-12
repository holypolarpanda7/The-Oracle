"""
Spherical world geography: lat/lon coordinates, great-circle distance and
bearings, travel time, and a latitude-driven climate model.

Every significant place carries ``attributes["coords"] = {"lat": deg,
"lon": deg}`` on a sphere of radius ``WORLD_RADIUS_MI``. Small child places
(buildings, rooms) inherit their parent's coords via PART_OF instead of
storing their own.

Why a sphere: travel wraps like a planet (keep walking east and you come home;
push far enough north and you cross the pole), so the world has a real, finite
size without ever hitting an artificial wall. And because climate is a pure
function of latitude, the ENTIRE world has climate bands while storing nothing:
anywhere the story wanders, climate is derivable. This is deliberately not a
hex map or terrain engine (those were dropped) — two floats per place and a
handful of standard spherical formulas.

The origin settlement sits at ORIGIN_LAT/ORIGIN_LON (45°N — temperate).
"""
from __future__ import annotations

import hashlib
import math
from typing import Optional


# A cozier-than-Earth planet: circumference ~12,600 mi, pole-to-pole ~6,300 mi.
# Big enough to hold continents, small enough that long journeys mean something.
WORLD_RADIUS_MI = 2000.0
WORLD_CIRCUMFERENCE_MI = 2.0 * math.pi * WORLD_RADIUS_MI

# Where the starting settlement sits: mid-northern latitudes, temperate.
ORIGIN_LAT = 45.0
ORIGIN_LON = 0.0

# 8-way compass, clockwise from north.
_COMPASS = ["north", "northeast", "east", "southeast",
            "south", "southwest", "west", "northwest"]
_COMPASS_BEARING = {name: i * 45.0 for i, name in enumerate(_COMPASS)}

# Overland walking pace used to express distances as travel time.
MILES_PER_DAY_ON_FOOT = 24.0

# How far a new place sits from its connection point when the extractor gives
# no explicit distance, by final scale word.
DEFAULT_SCALE_DISTANCE_MI = {
    "room": 0.0, "building": 0.2, "district": 0.5, "poi": 3.0,
    "dungeon": 6.0, "village": 8.0, "wilds": 10.0, "settlement": 15.0,
    "town": 20.0, "city": 40.0, "region": 60.0,
}

# Two settlements closer than this collapse into one in the fiction, so a new
# settlement inside this radius of an existing one is downgraded to a poi.
MIN_SETTLEMENT_SPACING_MI = 6.0

Coords = tuple[float, float]  # (lat_deg, lon_deg)


def _norm_lon(lon: float) -> float:
    """Wrap longitude into [-180, 180)."""
    return ((lon + 180.0) % 360.0) - 180.0


def coords_from_attrs(attributes: Optional[dict]) -> Optional[Coords]:
    """Read ``(lat, lon)`` out of an entity's attributes, or None."""
    c = (attributes or {}).get("coords")
    if not isinstance(c, dict):
        return None
    try:
        return float(c["lat"]), float(c["lon"])
    except (KeyError, TypeError, ValueError):
        return None


def coords_attr(lat: float, lon: float) -> dict:
    """The canonical attributes entry for a position."""
    return {"lat": round(float(lat), 4), "lon": round(_norm_lon(float(lon)), 4)}


def distance_mi(a: Coords, b: Coords) -> float:
    """Great-circle (haversine) distance in miles."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2.0 * WORLD_RADIUS_MI * math.asin(min(1.0, math.sqrt(h)))


def compass_between(a: Coords, b: Coords) -> str:
    """8-way compass direction of the initial great-circle bearing a -> b."""
    if distance_mi(a, b) < 0.05:
        return "here"
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = math.degrees(math.atan2(x, y)) % 360.0
    return _COMPASS[int((bearing + 22.5) // 45) % 8]


def offset_coords(origin: Coords, direction: str, miles: float) -> Coords:
    """Destination ``miles`` from origin walking a constant compass direction.

    Compass-walk (rhumb-like) semantics, matching traveler intuition: "keep
    going east" follows the parallel all the way around the world and back;
    "keep going north" crosses the pole and comes down the far side with
    longitude flipped. Diagonals apply the north-south leg, then the east-west
    leg at the destination latitude.
    """
    bearing = math.radians(_COMPASS_BEARING.get((direction or "").strip().lower(), 0.0))
    d = miles / WORLD_RADIUS_MI  # angular distance (radians)
    lat = math.radians(origin[0]) + d * math.cos(bearing)
    lon = math.radians(origin[1])

    # Pole crossings: reflect latitude, flip longitude 180° — like a planet.
    half_pi = math.pi / 2
    while lat > half_pi or lat < -half_pi:
        if lat > half_pi:
            lat = math.pi - lat
        else:
            lat = -math.pi - lat
        lon += math.pi

    # East-west leg along the destination parallel (degenerate at the poles).
    cos_lat = math.cos(lat)
    if abs(cos_lat) > 1e-9:
        lon += (d * math.sin(bearing)) / cos_lat

    return math.degrees(lat), _norm_lon(math.degrees(lon))


def from_origin(direction: str, miles: float) -> Coords:
    """Convenience: a point relative to the world origin settlement."""
    return offset_coords((ORIGIN_LAT, ORIGIN_LON), direction, miles)


def hashed_direction(name: str) -> str:
    """Deterministic pseudo-random compass direction for a name (stable across runs)."""
    h = hashlib.sha256((name or "").strip().lower().encode("utf-8")).digest()
    return _COMPASS[h[0] % 8]


def travel_time_str(miles: float) -> str:
    """Express a distance as walking time ("about an hour", "2 days on foot")."""
    if miles <= 0.3:
        return "moments away"
    hours = miles / (MILES_PER_DAY_ON_FOOT / 8.0)  # ~8 walking hours per day
    if hours < 0.75:
        return "under an hour on foot"
    if hours < 1.5:
        return "about an hour on foot"
    if hours <= 8:
        return f"about {round(hours)} hours on foot"
    days = miles / MILES_PER_DAY_ON_FOOT
    if days < 1.5:
        return "about a day on foot"
    return f"about {round(days)} days on foot"


def climate_for(coords: Optional[Coords]) -> str:
    """Climate band from latitude alone — the whole planet, stored nowhere.

    Symmetric about the equator like a real world. The origin (45°) is
    temperate by construction; the far east of the temperate belt dries toward
    steppe, echoing a continental interior.
    """
    if coords is None:
        return "temperate"
    lat, lon = coords
    a = abs(lat)
    if a >= 75:
        return "arctic"
    if a >= 60:
        return "subarctic"
    if a >= 50:
        return "cool temperate"
    if a >= 35:
        return "temperate"
    if a >= 23:
        # Continental interior east of the origin meridian runs dry.
        return "arid" if 60.0 <= _norm_lon(lon) <= 150.0 else "warm temperate"
    if a >= 10:
        return "subtropical"
    return "tropical"
