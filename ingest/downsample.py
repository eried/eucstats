"""Downsample a trip track to <= max_points, preserving global extremes,
then (en|de)code it as gzip-JSON for storage / eucviewer replay."""
from __future__ import annotations

import gzip
import json
import math

from .parser import Sample


def downsample(samples: list[Sample], max_points: int = 500) -> list[Sample]:
    """Keep <= max_points coordinate-bearing samples. Always retains the global
    max-speed and max-|G| points so records/extremes survive downsampling."""
    pts = [s for s in samples if s.lat is not None and s.lon is not None]
    if not pts:
        return []
    n = len(pts)

    def argmax(key):
        best, bi = None, None
        for i, s in enumerate(pts):
            v = key(s)
            if v is not None and (best is None or v > best):
                best, bi = v, i
        return bi

    keep = set()
    if n <= max_points:
        keep = set(range(n))
    else:
        step = math.ceil(n / max_points)
        keep.update(range(0, n, step))
        keep.add(n - 1)
    si = argmax(lambda s: s.speed)
    gi = argmax(lambda s: abs(s.g) if s.g is not None else None)
    if si is not None:
        keep.add(si)
    if gi is not None:
        keep.add(gi)
    return [pts[i] for i in sorted(keep)]


def encode_track(samples: list[Sample]) -> bytes:
    """Compact gzip-JSON: [[iso_t, lat, lon, speed, g], ...]."""
    arr = [[s.t.isoformat(), s.lat, s.lon, s.speed, s.g] for s in samples]
    return gzip.compress(json.dumps(arr, separators=(",", ":")).encode())


def decode_track(blob: bytes) -> list:
    return json.loads(gzip.decompress(blob).decode())
