"""Offline country lookup + map-cell quantization (no per-trip external calls)."""
from __future__ import annotations

import math

_rg = None


def _get_rg():
    global _rg
    if _rg is None:
        import reverse_geocoder as rg  # loads its dataset on first use
        _rg = rg
    return _rg


def country_of(lat, lon) -> str | None:
    """ISO-3166-1 alpha-2 country code for a coordinate, or None."""
    if lat is None or lon is None:
        return None
    try:
        res = _get_rg().search((float(lat), float(lon)), mode=1)
        return res[0].get("cc") if res else None
    except Exception:
        return None


def places_for(coords: list[tuple[float, float]]) -> list[str | None]:
    """Nearest settled place name for each coordinate, in order.

    One batched call: the dataset is loaded once and a list costs barely more than a single
    point. Used by the hourly territory rebuild to put a neighbourhood in a target row.
    """
    if not coords:
        return []
    try:
        res = _get_rg().search([(float(a), float(b)) for a, b in coords], mode=1)
    except Exception:
        return [None] * len(coords)
    return [(r.get("name") or None) for r in res]


def cell_id(lat, lon, zoom_deg: float) -> str | None:
    """Quantize a coordinate to a grid cell of `zoom_deg` degrees."""
    if lat is None or lon is None:
        return None
    return f"{zoom_deg}:{math.floor(lat / zoom_deg)}:{math.floor(lon / zoom_deg)}"


def cells_for(lat, lon, zooms: list[float]) -> dict[float, str]:
    """Cell id at each configured zoom level (skips Nones)."""
    out = {}
    for z in zooms:
        c = cell_id(lat, lon, z)
        if c is not None:
            out[z] = c
    return out
