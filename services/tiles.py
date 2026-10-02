"""Web Mercator tiles — the grid territory is drawn on.

The heatmap uses degree cells (`ingest.geo.cell_id`), which are fine for a blurred glow but
wrong for territory: a degree cell is a rectangle that stretches taller the further from the
equator you go, so a crew emblem dropped into one would be squashed in Oslo and square in
Nairobi. Mercator tiles are square on screen at every latitude and zoom, which is what makes
an emblem placeable at all.

The two grids coexist. Nothing here touches the heatmap.

Tile ground size is `40075 km * cos(latitude) / 2^zoom`, so northern riders get finer tiles
for free — at zoom 13 that is about 2.4 km in Oslo against 4.9 km at the equator.
"""
from __future__ import annotations

import math

DEFAULT_ZOOM = 13          # ~2.4 km at Oslo, ~4.9 km at the equator
EARTH_C_KM = 40075.016686


def tile_of(lat: float, lon: float, zoom: int = DEFAULT_ZOOM) -> str | None:
    """"z/x/y" for a coordinate, or None if it is not a usable position.

    Latitude is clamped to the Mercator limit rather than rejected: a fix at 86° is a bad fix,
    not a reason to drop the whole trip.
    """
    if lat is None or lon is None:
        return None
    if not (-180.0 <= lon <= 180.0):
        return None
    lat = max(-85.05112878, min(85.05112878, float(lat)))
    n = 1 << zoom
    x = int((float(lon) + 180.0) / 360.0 * n)
    r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(r)) / math.pi) / 2.0 * n)
    x = max(0, min(n - 1, x))
    y = max(0, min(n - 1, y))
    return f"{zoom}/{x}/{y}"


def parse(tile: str) -> tuple[int, int, int] | None:
    try:
        z, x, y = tile.split("/")
        return int(z), int(x), int(y)
    except (AttributeError, ValueError):
        return None


def bounds(tile: str) -> tuple[float, float, float, float] | None:
    """(west, south, east, north) in degrees — the polygon the client would draw."""
    p = parse(tile)
    if p is None:
        return None
    z, x, y = p
    n = 1 << z

    def lat_at(yy):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))

    return (x / n * 360.0 - 180.0, lat_at(y + 1), (x + 1) / n * 360.0 - 180.0, lat_at(y))


def area_km2(tile: str) -> float:
    """Ground area of a tile. Needed because the crew ranking is in km², and a tile in Oslo
    covers a quarter of what the same tile covers at the equator — ranking by tile COUNT would
    quietly reward riding far from the equator."""
    b = bounds(tile)
    if b is None:
        return 0.0
    west, south, east, north = b
    mid = math.radians((south + north) / 2.0)
    w = (east - west) / 360.0 * EARTH_C_KM * math.cos(mid)   # shrinks toward the poles
    h = (north - south) * 111.32                             # a degree of latitude is constant
    return abs(w * h)


def neighbours(tile: str) -> list[str]:
    """The four edge-adjacent tiles. Territory grows through edges, not corners — two blocks
    touching only at a point are not one region to anybody looking at the map."""
    p = parse(tile)
    if p is None:
        return []
    z, x, y = p
    n = 1 << z
    out = []
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = x + dx, y + dy
        if 0 <= ny < n:
            out.append(f"{z}/{nx % n}/{ny}")      # wrap east-west, never past the poles
    return out


def tiles_along(points, zoom: int = DEFAULT_ZOOM) -> list[str]:
    """Every tile a route passes through, in order, without duplicates in a row.

    Consecutive GPS fixes can be hundreds of metres apart, so simply mapping each fix to a
    tile leaves holes in a fast ride. Where two fixes land in tiles that are not adjacent, the
    gap is walked in a straight line and the tiles underneath are filled in.
    """
    out: list[str] = []
    prev_pt = None
    for lat, lon in points:
        t = tile_of(lat, lon, zoom)
        if t is None:
            continue
        if prev_pt is not None:
            for step in _between(prev_pt, (lat, lon), zoom):
                if not out or out[-1] != step:
                    out.append(step)
        if not out or out[-1] != t:
            out.append(t)
        prev_pt = (lat, lon)
    return out


def _between(a, b, zoom: int) -> list[str]:
    """Tiles under the straight line from a to b, excluding the endpoints' own tiles."""
    ta, tb = tile_of(*a, zoom), tile_of(*b, zoom)
    if ta is None or tb is None or ta == tb:
        return []
    pa, pb = parse(ta), parse(tb)
    steps = max(abs(pb[1] - pa[1]), abs(pb[2] - pa[2]))
    if steps <= 1:
        return []
    if steps > 256:          # a jump that big is a GPS teleport, not a ride
        return []
    out = []
    for i in range(1, steps):
        f = i / steps
        lat = a[0] + (b[0] - a[0]) * f
        lon = a[1] + (b[1] - a[1]) * f
        t = tile_of(lat, lon, zoom)
        if t is not None and (not out or out[-1] != t):
            out.append(t)
    return out
