"""Server-side telemetry plausibility checks. Returns (status, reasons)."""
from __future__ import annotations

from .parser import Sample
from .summary import TripSummary, _haversine_km


def check(samples: list[Sample], summary: TripSummary, is_mock: bool = False,
          max_kmh: float = 120.0, max_g: float = 12.0,
          teleport_kmh: float = 150.0, dist_tolerance: float = 0.4):
    reasons: list[str] = []

    if is_mock:
        reasons.append("mock_location")

    if any(s.speed is not None and s.speed > max_kmh for s in samples):
        reasons.append("impossible_speed")

    if summary.max_gforce is not None and summary.max_gforce > max_g:
        reasons.append("impossible_gforce")

    # teleport: any consecutive GPS pair implying > teleport_kmh
    prev = None
    teleport = False
    for s in samples:
        if s.lat is not None and s.lon is not None:
            if prev is not None:
                d = _haversine_km(prev[0], prev[1], s.lat, s.lon)
                dt = (s.t - prev[2]).total_seconds()
                if dt > 0 and (d / (dt / 3600.0)) > teleport_kmh:
                    teleport = True
            prev = (s.lat, s.lon, s.t)
    if teleport:
        reasons.append("teleport")

    # odometer vs GPS distance disagreement (only when both are meaningful)
    if summary.gps_distance_km > 0.5 and summary.distance_km > 0.5:
        diff = abs(summary.distance_km - summary.gps_distance_km) / max(
            summary.distance_km, summary.gps_distance_km)
        if diff > dist_tolerance:
            reasons.append("distance_mismatch")

    status = "validated" if not reasons else "flagged"
    return status, reasons
