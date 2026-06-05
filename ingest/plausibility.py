"""Server-side telemetry plausibility checks. Returns (status, reasons)."""
from __future__ import annotations

from .parser import Sample
from .summary import TripSummary, _haversine_km


def check(samples: list[Sample], summary: TripSummary, is_mock: bool = False,
          max_kmh: float = 120.0, max_g: float = 12.0,
          teleport_kmh: float = 150.0, teleport_max_jumps: int = 8,
          dist_tolerance: float = 0.4, unverified_dist_km: float = 3.0,
          disabled=frozenset()):
    reasons: list[str] = []

    def add(key):
        if key not in disabled:        # admin can switch individual rules off
            reasons.append(key)

    if is_mock:
        add("mock_location")

    # a meaningful distance with NO GPS at all is trivially faked via the odometer
    # — flag for review so it can't silently top distance boards
    has_gps = any(s.lat is not None and s.lon is not None for s in samples)
    if not has_gps and (summary.distance_km or 0) > unverified_dist_km:
        add("unverified_distance")

    if any(s.speed is not None and s.speed > max_kmh for s in samples):
        add("impossible_speed")

    if summary.max_gforce is not None and summary.max_gforce > max_g:
        add("impossible_gforce")

    # teleport: count consecutive GPS pairs implying > teleport_kmh. A few are
    # normal GPS noise; only flag when there are many (systematic teleporting).
    prev = None
    teleport_jumps = 0
    for s in samples:
        if s.lat is not None and s.lon is not None:
            if prev is not None:
                d = _haversine_km(prev[0], prev[1], s.lat, s.lon)
                dt = (s.t - prev[2]).total_seconds()
                if dt > 0 and (d / (dt / 3600.0)) > teleport_kmh:
                    teleport_jumps += 1
            prev = (s.lat, s.lon, s.t)
    if teleport_jumps > teleport_max_jumps:
        add("teleport")

    # odometer vs GPS distance disagreement (only when both are meaningful)
    if summary.gps_distance_km > 0.5 and summary.distance_km > 0.5:
        diff = abs(summary.distance_km - summary.gps_distance_km) / max(
            summary.distance_km, summary.gps_distance_km)
        if diff > dist_tolerance:
            add("distance_mismatch")

    status = "validated" if not reasons else "flagged"
    return status, reasons
