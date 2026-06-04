"""Compute a canonical per-trip summary from normalized samples."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .parser import Sample

EARTH_KM = 6371.0088


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = rlat2 - rlat1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(a)))


@dataclass
class TripSummary:
    start_utc: datetime
    end_utc: datetime
    duration_s: float
    distance_km: float
    gps_distance_km: float
    max_speed: float | None
    avg_speed: float | None
    max_gforce: float | None
    wh_per_km: float | None
    max_sustained_w: float | None
    max_sustained_a: float | None
    peak_voltage: float | None
    fastest_0_40_s: float | None
    ascent_m: float | None
    battery_used_pct: float | None
    est_range_km: float | None
    sample_count: int


def gps_distance_km(samples: list[Sample]) -> float:
    total = 0.0
    prev = None
    for s in samples:
        if s.lat is not None and s.lon is not None:
            if prev is not None:
                total += _haversine_km(prev[0], prev[1], s.lat, s.lon)
            prev = (s.lat, s.lon)
    return total


def odometer_distance_km(samples: list[Sample], max_step_km: float = 5.0) -> tuple[float, bool]:
    """Robust trip distance from a (possibly lifetime) odometer: sum positive
    deltas, rejecting resets (negative deltas) and dropouts (a single step
    larger than max_step_km — e.g. a 0-reading that would otherwise span the
    whole lifetime odometer). Uses an absolute cap rather than implied speed
    because real odometers are quantized (0.1 km), which makes per-second
    increments look like impossible speeds. Returns (distance, has_odometer)."""
    dist = 0.0
    prev = None  # last odo value
    has_odo = False
    for s in samples:
        if s.odo is None:
            continue
        has_odo = True
        if prev is not None:
            d = s.odo - prev
            if 0 < d <= max_step_km:
                dist += d
        prev = s.odo
    return dist, has_odo


def _energy_wh(samples: list[Sample]) -> float | None:
    if not any(s.current is not None for s in samples):
        return None
    wh = 0.0
    prev = None  # (t, power_w)
    for s in samples:
        p = s.power if s.power is not None else (
            s.voltage * s.current if (s.voltage is not None and s.current is not None) else None)
        if prev is not None and prev[1] is not None:
            dt = (s.t - prev[0]).total_seconds()
            wh += prev[1] * dt / 3600.0
        prev = (s.t, p)
    return wh if wh > 0 else None


def _sustained_max(samples: list[Sample], fn, window_s: float = 2.0) -> float | None:
    """Max average of fn(sample) over any trailing window of ~window_s seconds."""
    pts = [(s.t, fn(s)) for s in samples if fn(s) is not None]
    if not pts:
        return None
    best = None
    left = 0
    ssum = 0.0
    for right in range(len(pts)):
        ssum += pts[right][1]
        while (pts[right][0] - pts[left][0]).total_seconds() > window_s:
            ssum -= pts[left][1]
            left += 1
        avg = ssum / (right - left + 1)
        if best is None or avg > best:
            best = avg
    return best


def _power(s: Sample):
    if s.power is not None:
        return s.power
    if s.voltage is not None and s.current is not None:
        return s.voltage * s.current
    return None


def _fastest_0_40(samples: list[Sample]) -> float | None:
    """Shortest time (s) to launch from a near-stop (<=2 km/h) up to 40 km/h —
    an EUC '0-60'-style acceleration metric. Lower is better."""
    best = None
    start = None
    for s in samples:
        sp = s.speed
        if sp is None:
            continue
        if sp <= 2.0:
            start = s.t
        elif start is not None and sp >= 40.0:
            dt = (s.t - start).total_seconds()
            # 1.5s floor rejects sensor noise; 20s ceiling rejects casual coasts
            # (only a genuine hard launch from a stop to 40 km/h counts)
            if 1.5 <= dt <= 20 and (best is None or dt < best):
                best = dt
            start = None
    return best


def _ascent_m(samples: list[Sample]) -> float | None:
    """Elevation gain from altitude samples, 3 m hysteresis to filter GPS noise."""
    alts = [s.alt for s in samples if s.alt is not None]
    if len(alts) < 2:
        return None
    gain = 0.0
    ref = alts[0]
    for a in alts[1:]:
        if a - ref > 3.0:
            gain += a - ref
            ref = a
        elif ref - a > 3.0:
            ref = a
    return round(gain, 1)


def _battery_used(samples: list[Sample]) -> float | None:
    """Total battery % consumed (sum of drops; ignores mid-ride charging)."""
    bs = [s.battery for s in samples if s.battery is not None]
    if len(bs) < 2:
        return None
    drop = 0.0
    prev = bs[0]
    for b in bs[1:]:
        if b < prev:
            drop += prev - b
        prev = b
    return round(drop, 1)


def summarize(samples: list[Sample], max_step_km: float = 5.0,
              gps_tolerance: float = 0.4) -> TripSummary:
    if not samples:
        raise ValueError("cannot summarize empty sample list")
    start, end = samples[0].t, samples[-1].t
    duration = (end - start).total_seconds()

    gps_km = gps_distance_km(samples)
    odo_km, has_odo = odometer_distance_km(samples, max_step_km)
    # Prefer the wheel odometer when it's meaningful and not severely under GPS
    # (a coarse/non-updating odometer should defer to GPS-measured movement).
    if has_odo and odo_km > 0.1 and (gps_km <= 0 or odo_km >= gps_km * (1 - gps_tolerance)):
        distance = odo_km
    else:
        distance = gps_km

    speeds = [s.speed for s in samples if s.speed is not None]
    max_speed = max(speeds) if speeds else None
    avg_speed = (sum(speeds) / len(speeds)) if speeds else None

    gs = [abs(s.g) for s in samples if s.g is not None]
    max_gforce = max(gs) if gs else None

    wh = _energy_wh(samples)
    wh_per_km = (wh / distance) if (wh is not None and distance > 0) else None

    volts = [s.voltage for s in samples if s.voltage is not None]
    peak_voltage = max(volts) if volts else None
    max_sustained_w = _sustained_max(samples, _power, 2.0)
    max_sustained_a = _sustained_max(samples, lambda s: s.current, 2.0)
    fastest_0_40_s = _fastest_0_40(samples)
    ascent_m = _ascent_m(samples)
    battery_used_pct = _battery_used(samples)
    est_range_km = (round(distance * 100.0 / battery_used_pct, 1)
                    if (battery_used_pct and battery_used_pct >= 10 and distance > 0) else None)

    return TripSummary(
        start_utc=start, end_utc=end, duration_s=duration,
        distance_km=distance, gps_distance_km=gps_km,
        max_speed=max_speed, avg_speed=avg_speed, max_gforce=max_gforce,
        wh_per_km=wh_per_km, max_sustained_w=max_sustained_w,
        max_sustained_a=max_sustained_a, peak_voltage=peak_voltage,
        fastest_0_40_s=fastest_0_40_s, ascent_m=ascent_m,
        battery_used_pct=battery_used_pct, est_range_km=est_range_km,
        sample_count=len(samples),
    )
