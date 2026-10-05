"""Web Mercator tiles — the grid territory is drawn on.

The heatmap uses degree cells (`ingest.geo.cell_id`), which are fine for a blurred glow but
wrong for territory: a degree cell is a rectangle that stretches taller the further from the
equator you go, so a crew emblem dropped into one would be squashed in Oslo and square in
Nairobi. Mercator tiles are square on screen at every latitude and zoom, which is what makes
an emblem placeable at all.

The two grids coexist. Nothing here touches the heatmap.

Tile ground size is `40075 km * cos(latitude) / 2^zoom`, so northern riders get finer tiles
for free: at zoom 14 that is about 1.2 km in Oslo against 2.4 km at the equator.

Zoom 14 rather than 13 because of what a 2x2 block costs. At 13 the seed a crew must plant is
a 5 km square, which is an afternoon set aside for it; at 14 it is 2.4 km across, which is an
ordinary ride, and territory reads as neighbourhoods instead of thirds of a city.
"""
from __future__ import annotations

import math

DEFAULT_ZOOM = 14          # ~1.2 km at Oslo, ~2.4 km at the equator
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
    """Ground area of a tile. A tile in Oslo covers a quarter of what the same tile covers at
    the equator.

    This used to claim "the crew ranking is in km²", which it is not and has not been for some
    time: `territory.ranking` sorts on `terr_best_tiles` and uses km² only to break a tie. So
    the one place the hazard was written down was also telling a reader the opposite of what
    the code does, and the warning it ends on — that ranking by tile COUNT quietly rewards
    riding far from the equator — describes the ranking as shipped. A reviewer measured it:
    Equator Express holds 83.7 km² and ranks 13th, below a crew holding 24.7.

    That is a deliberate trade rather than an oversight — "biggest patch in one piece,
    counted in squares" is the number the board, the targets card, the tap popup and a crew's
    public page all agree on, and `crew.board.sub` now says out loud that a square covers more
    ground nearer the equator. This function is what every km² figure beside those counts is
    built from."""
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


def _xy_float(lat: float, lon: float, zoom: int) -> tuple[float, float] | None:
    """Fractional tile coordinates — the same maths as tile_of without the floor.

    The traversal below needs to know *where inside* a tile a point sits, not just which tile
    it is in, so it can tell which edge the line leaves through first.
    """
    if lat is None or lon is None or not (-180.0 <= lon <= 180.0):
        return None
    lat = max(-85.05112878, min(85.05112878, float(lat)))
    n = 1 << zoom
    x = (float(lon) + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def _between(a, b, zoom: int) -> list[str]:
    """Every tile the straight line from a to b crosses, in order, excluding a's own tile.

    A proper grid traversal, one edge at a time. The previous version interpolated the line at
    N evenly spaced points and mapped each to a tile, which on any diagonal advanced x and y
    together: the result was a staircase of tiles touching only at their CORNERS, and the
    tiles the line genuinely passed through were never emitted at all. Territory grows through
    edges, so a corner-only chain is not a chain — a 31 km diagonal ride came out as six
    disconnected tiles and claimed nothing. Only a ride running exactly along an axis worked,
    which is why the original test (a due-east route) passed.

    This steps whichever edge the line reaches next, so consecutive tiles always share an
    edge, and nothing the line touches is skipped.
    """
    pa, pb = _xy_float(*a, zoom), _xy_float(*b, zoom)
    if pa is None or pb is None:
        return []
    x0f, y0f = pa
    x1f, y1f = pb
    x, y = int(x0f), int(y0f)
    ex, ey = int(x1f), int(y1f)
    if (x, y) == (ex, ey):
        return []
    if abs(ex - x) + abs(ey - y) > 512:      # a jump that big is a GPS teleport, not a ride
        return []

    dx, dy = x1f - x0f, y1f - y0f
    step_x = 1 if dx > 0 else (-1 if dx < 0 else 0)
    step_y = 1 if dy > 0 else (-1 if dy < 0 else 0)
    # distance along the line (in units of t, 0..1) to the next vertical / horizontal edge
    inf = float("inf")
    t_max_x = ((x + (1 if step_x > 0 else 0)) - x0f) / dx if step_x else inf
    t_max_y = ((y + (1 if step_y > 0 else 0)) - y0f) / dy if step_y else inf
    t_delta_x = abs(1.0 / dx) if step_x else inf
    t_delta_y = abs(1.0 / dy) if step_y else inf

    n = 1 << zoom
    out: list[str] = []
    for _ in range(1024):                    # hard stop; the length guard above already bounds it
        if t_max_x < t_max_y:
            x += step_x
            t_max_x += t_delta_x
        else:
            y += step_y
            t_max_y += t_delta_y
        if not (0 <= y < n):
            break
        if (x % n, y) == (ex, ey):
            break                            # the endpoint's own tile is added by the caller
        out.append(f"{zoom}/{x % n}/{y}")
        if t_max_x > 1.0 and t_max_y > 1.0:
            break
    return out
