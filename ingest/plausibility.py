"""Server-side telemetry plausibility checks. Returns (status, reasons)."""
from __future__ import annotations

from .parser import Sample
from .summary import TripSummary, _haversine_km, teleport_segments


def _turning_in_place(samples, summary, still_share: float) -> bool:
    """Was this a wheel pivoting rather than a wheel travelling?

    Two conditions, and both are needed. Most fixes show no movement between them -- which is
    what manoeuvring in one spot looks like to a receiver -- AND the odometer is the higher of
    the two figures, because a tyre that rolls through a turn the track records as a chord
    reads long, never short. A GPS figure above the odometer cannot be produced this way, and a
    ride whose samples were mostly moving is not turning in place whatever its average speed.

    Measured on the production set: the five trips this frees are 51-73% stationary at 3-5 kph;
    the ones it deliberately does not free sit at 0-15% stationary or have the GPS on top.
    """
    if (summary.distance_km or 0) <= (summary.gps_distance_km or 0):
        return False                       # the odometer is not the one claiming extra ground
    pts = [(s.lat, s.lon) for s in samples if s.lat is not None and s.lon is not None]
    if len(pts) < 10:
        return False                       # too few fixes to say anything about the shape
    still = 0
    for i in range(len(pts) - 1):
        if _haversine_km(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]) * 1000.0 < 0.5:
            still += 1
    return still / float(len(pts) - 1) > still_share


def check(samples: list[Sample], summary: TripSummary, is_mock: bool = False,
          max_kmh: float = 120.0, max_g: float = 12.0,
          teleport_kmh: float = 150.0, teleport_max_jumps: int = 8,
          teleport_gap_s: float = 20.0, teleport_min_kmh: float = 8.0,
          teleport_min_jump_m: float = 150.0, teleport_jump_rate: float = 0.01,
          dist_tolerance: float = 0.4, unverified_dist_km: float = 3.0,
          mismatch_min_km: float = 0.5, max_ascent_per_km: float = 300.0,
          turning_tolerance: float = 0.65, turning_still_share: float = 0.4,
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

    # Use the REALISTIC (acceleration-corroborated) top speed and the SUSTAINED
    # g-force — not raw samples. A crash or freespin spikes speed/g for a fraction
    # of a second; that's a warning (kept in meta_json), never a cheat flag.
    if summary.max_speed is not None and summary.max_speed > max_kmh:
        add("impossible_speed")

    if summary.max_gforce is not None and summary.max_gforce > max_g:
        add("impossible_gforce")

    # implausible climb: more than max_ascent_per_km metres gained per km ridden — GPS/baro
    # altitude noise or fabricated elevation. Only judged once ascent is significant.
    if (summary.ascent_m or 0) > 100 and (summary.distance_km or 0) > 0.5 \
            and summary.ascent_m / summary.distance_km > max_ascent_per_km:
        add("impossible_ascent")

    # teleport: count GPS jumps implying > teleport_kmh, but only genuine ones — riding (not indoor
    # drift), GPS sampling continuously (not a tunnel re-acquisition), and a real displacement (not
    # position noise). A few are normal GPS noise; only flag when there are many (systematic
    # teleporting).
    pts = [(s.t, s.lat, s.lon, s.speed) for s in samples if s.lat is not None and s.lon is not None]
    teleport_jumps = len(teleport_segments(pts, teleport_kmh, teleport_gap_s, teleport_min_kmh,
                                           teleport_min_jump_m))
    # Scale the allowance with the number of fixes. A flat count punishes distance: a two-hour ride
    # meets more noise than a ten-minute one purely by lasting longer. Spoofing corrupts a large
    # FRACTION of a track, so a rate catches it while leaving honest long rides alone.
    if teleport_jumps > max(teleport_max_jumps, int(len(pts) * teleport_jump_rate)):
        add("teleport")

    # odometer vs GPS distance disagreement (only when both are meaningful)
    if summary.gps_distance_km > mismatch_min_km and summary.distance_km > mismatch_min_km:
        diff = abs(summary.distance_km - summary.gps_distance_km) / max(
            summary.distance_km, summary.gps_distance_km)
        tol = dist_tolerance
        if _turning_in_place(samples, summary, turning_still_share):
            tol = max(dist_tolerance, turning_tolerance)
        if diff > tol:
            add("distance_mismatch")

    status = "validated" if not reasons else "flagged"
    return status, reasons
