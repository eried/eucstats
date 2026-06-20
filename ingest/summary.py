"""Compute a canonical per-trip summary from normalized samples."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .parser import Sample

EARTH_KM = 6371.0088

# All admin-tunable physics knobs, with their built-in fallback defaults. The
# ingest layer overrides these from settings; tests/direct callers get sane defaults.
CALIBRATION_DEFAULTS = {
    "max_accel": 20.0,            # km/h per s — believable accel; faster = freespin/spike
    "sustain_secs": 2.0,          # s — window for sustained power/current/g-force
    "freespin_margin": 5.0,       # km/h — raw speed must beat realistic by this to be a freespin
    "ascent_hysteresis_m": 3.0,   # m — ignore elevation wiggles under this
    "odo_max_step_km": 5.0,       # km — reject odometer jumps bigger than this
    "sag_window_s": 5.0,          # s — voltage-sag look-back window
    "accel_target_kmh": 40.0,     # km/h — launch metric target (0 -> target)
    "accel_min_s": 1.5,           # s — launches faster than this are sensor noise
    "accel_max_s": 20.0,          # s — only count a launch reaching target within this
    "sustain_accel_lo_s": 2.0,    # s — sustained-acceleration min window
    "sustain_accel_hi_s": 6.0,    # s — sustained-acceleration max window
    "range_min_battery_pct": 10.0,  # % — min battery drop to estimate full-charge range
}
MAX_ACCEL_KMH_S = CALIBRATION_DEFAULTS["max_accel"]   # kept for backward compatibility


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
    moving_s: float | None
    distance_km: float
    gps_distance_km: float
    max_speed: float | None
    max_freespin: float | None
    avg_speed: float | None
    max_gforce: float | None
    max_gforce_spike: float | None
    wh_per_km: float | None
    max_sustained_w: float | None
    max_sustained_a: float | None
    peak_voltage: float | None
    max_voltage_sag: float | None
    fastest_0_40_s: float | None
    sustained_accel: float | None
    ascent_m: float | None
    descent_m: float | None
    battery_used_pct: float | None
    est_range_km: float | None
    alt_range_m: float | None
    max_altitude_m: float | None
    min_altitude_m: float | None
    max_temp: float | None
    min_temp: float | None
    max_pwm: float | None
    min_battery_pct: float | None
    # --- newer (hidden) metrics: longer sustained windows, high-speed / directional g, shake ---
    g_sust_4s: float | None
    g_sust_6s: float | None
    pwm_sust_3s: float | None
    speed_sust_5s: float | None
    speed_sust_10s: float | None
    power_sust_6s: float | None
    current_sust_6s: float | None
    g_fast_20: float | None
    g_fast_30: float | None
    g_fast_40: float | None
    g_lateral: float | None
    g_brake: float | None
    shake_index: float | None
    accel_g: float | None       # longitudinal g from speed change: launch (accel) / brake
    brake_g: float | None
    t_0_60_s: float | None      # sprint times to 60 / 100 km/h (lower better)
    t_0_100_s: float | None
    accel_g_30: float | None    # roll-on accel g while already above 30 / 50 km/h
    accel_g_50: float | None
    brake_g_30: float | None    # braking g coming down from 30 / 50 km/h
    brake_g_50: float | None
    stop_30_s: float | None     # fastest stop from 30 / 50 km/h to a standstill (lower better)
    stop_50_s: float | None
    cutout_count: int           # detected cutout/overlean fall events (ride@speed -> freespin + impact)
    sample_count: int


def gps_distance_km(samples: list[Sample], teleport_kmh: float = 150.0) -> float:
    """Sum of GPS hops, but a hop whose implied speed exceeds teleport_kmh is a teleport
    (GPS glitch) and is NOT credited — so an accepted trip with teleports can't inflate distance."""
    total = 0.0
    prev = None
    for s in samples:
        if s.lat is not None and s.lon is not None:
            if prev is not None:
                d = _haversine_km(prev[1], prev[2], s.lat, s.lon)
                dt = (s.t - prev[0]).total_seconds() if (s.t and prev[0]) else None
                if not (dt and dt > 0 and (d / (dt / 3600.0)) > teleport_kmh):
                    total += d                # skip physically-impossible jumps
            prev = (s.t, s.lat, s.lon)
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


def _fastest_0_40(samples: list[Sample], target_kmh: float = 40.0,
                  min_s: float = 1.5, max_s: float = 20.0,
                  start_kmh: float = 2.0, max_gap_s: float = 5.0, dip_frac: float = 0.8) -> float | None:
    """Shortest time (s) for a GENUINE launch from a near-stop up to target_kmh. A launch only
    counts when it is a real, continuous run — not just "the wheel was over target_kmh":
      * starts from a near-stop (corroborated speed <= start_kmh),
      * GPS-corroborated every sample (gps_speed present) — wheel-only/freespin can't qualify,
      * continuous in time (no gap > max_gap_s — a paused/resumed log isn't one launch),
      * monotonic-ish climb (a dip below dip_frac x the run's peak ends the attempt — a coast or
        stop-and-go isn't a launch).
    The crossing time is interpolated between the bracketing samples (so 0->40 and 0->60 differ).
    Lower is better."""
    best = None
    start = None          # time of the near-stop the current launch began at
    runmax = 0.0          # peak corroborated speed since start (for dip detection)
    prev = None           # (time, corroborated speed) of the previous valid, GPS-corroborated sample
    for s in samples:
        sp = _corrob_speed(s)
        if sp is None or s.gps_speed is None:      # need GPS to corroborate a real launch
            start, prev, runmax = None, None, 0.0
            continue
        if prev is not None and (s.t - prev[0]).total_seconds() > max_gap_s:
            start, runmax = None, 0.0              # discontinuous log -> not one clean launch
        if sp <= start_kmh:
            start, runmax = s.t, sp                # (re)arm at a near-stop
        elif start is not None:
            if sp < runmax * dip_frac:             # speed dropped back -> coast/restart, not a launch
                start, runmax = None, 0.0
            else:
                runmax = max(runmax, sp)
                if sp >= target_kmh:
                    cross = s.t
                    if prev is not None and prev[1] < target_kmh and sp > prev[1]:
                        cross = prev[0] + (s.t - prev[0]) * ((target_kmh - prev[1]) / (sp - prev[1]))
                    dt = (cross - start).total_seconds()
                    if min_s <= dt <= max_s and (best is None or dt < best):
                        best = dt
                    start, runmax = None, 0.0
        prev = (s.t, sp)
    return best


def _max_voltage_sag(samples: list[Sample], window_s: float = 5.0) -> float | None:
    """Biggest voltage dip under load: for each sample, how far voltage dropped
    below its recent peak (within a trailing window). Using a short rolling window
    measures transient sag during a hard pull, not the slow drain over the ride."""
    pts = [(s.t, s.voltage) for s in samples if s.voltage is not None]
    if len(pts) < 2:
        return None
    best = 0.0
    left = 0
    for right in range(len(pts)):
        while (pts[right][0] - pts[left][0]).total_seconds() > window_s:
            left += 1
        base = max(v for _, v in pts[left:right + 1])
        sag = base - pts[right][1]
        if sag > best:
            best = sag
    return round(best, 2) if best > 0 else None


def _max_sustained_accel(samples: list[Sample], lo: float = 2.0, hi: float = 6.0) -> float | None:
    """Highest acceleration (km/h per second) held for at least `lo` seconds — a
    real, sustained pull rather than a one-sample sensor jump. Capped at `hi`s so a
    long gradual climb to speed doesn't dilute a strong launch."""
    pts = [(s.t, s.speed) for s in samples if s.speed is not None]
    best = 0.0
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            dt = (pts[j][0] - pts[i][0]).total_seconds()
            if dt < lo:
                continue
            if dt > hi:
                break
            a = (pts[j][1] - pts[i][1]) / dt
            if a * _KMH_S_TO_G > MAX_LON_G:    # unphysical -> speed glitch, not real acceleration
                continue
            if a > best:
                best = a
    return round(best, 2) if best > 0 else None


def _g_fast(threshold_kmh: float):
    """fn for _sustained_max: |g| but only while the corroborated speed is at/above
    `threshold_kmh` — "how hard you load the wheel while actually moving fast", not a
    parking-lot stunt. Returns None for samples slower than the threshold (skipped)."""
    def fn(s: Sample):
        if s.g is None:
            return None
        v = _corrob_speed(s)
        return abs(s.g) if (v is not None and v >= threshold_kmh) else None
    return fn


def _max_shake(samples: list[Sample], window_s: float = 2.0) -> float | None:
    """Experimental wobble index: the largest standard deviation of lateral g (gx) over
    any short trailing window. A speed-wobble / shimmy swings side to side fast (high
    variance), whereas a steady hard corner holds a near-constant lateral g (low
    variance) — so std-dev isolates the shake from a clean carve. O(n) sliding window."""
    pts = [(s.t, s.gx) for s in samples if s.gx is not None]
    if len(pts) < 3:
        return None
    best = 0.0
    left = 0
    ssum = ssq = 0.0
    for right in range(len(pts)):
        ssum += pts[right][1]
        ssq += pts[right][1] ** 2
        while (pts[right][0] - pts[left][0]).total_seconds() > window_s:
            ssum -= pts[left][1]
            ssq -= pts[left][1] ** 2
            left += 1
        n = right - left + 1
        if n >= 3:
            sd = max(0.0, ssq / n - (ssum / n) ** 2) ** 0.5
            if sd > best:
                best = sd
    return round(best, 3) if best > 0 else None


_KMH_S_TO_G = (1000.0 / 3600.0) / 9.80665   # km/h-per-second -> g (1 km/h/s ≈ 0.0283 g)
# Physical ceiling for speed-derived longitudinal g. An EUC can't brake/accelerate harder than
# tyre grip + rider balance allow (~0.5-0.7 g hard); anything above this is a speed glitch or a
# GPS dropout across the window (e.g. 70->2 km/h in ~1 s = ~1.9 g), so we drop that window.
MAX_LON_G = 1.2


def _speed_g(samples: list[Sample], window_s: float = 1.0) -> tuple[float | None, float | None]:
    """Longitudinal g from how hard wheel speed changes: the strongest sustained push
    (acceleration) and the strongest sustained slow-down (braking), each as a g-force.
    Speed-derived (corroborated wheel/GPS speed) so it works on every wheel without
    trusting a noisy IMU axis. Returns (accel_g, brake_g). A ~1s window keeps it a real
    hold rather than a one-sample spike. O(n) sliding window."""
    pts = [(s.t, _corrob_speed(s)) for s in samples]
    pts = [(t, v) for t, v in pts if v is not None]
    if len(pts) < 2:
        return None, None
    best_acc = best_brk = 0.0
    left = 0
    for right in range(1, len(pts)):
        while right - left > 1 and (pts[right][0] - pts[left][0]).total_seconds() > window_s:
            left += 1
        dt = (pts[right][0] - pts[left][0]).total_seconds()
        if dt <= 0:
            continue
        g = abs((pts[right][1] - pts[left][1]) / dt) * _KMH_S_TO_G
        if g > MAX_LON_G:                      # unphysical -> speed glitch / GPS dropout, not a real g
            continue
        if pts[right][1] >= pts[left][1]:
            best_acc = max(best_acc, g)
        else:
            best_brk = max(best_brk, g)
    return (round(best_acc, 3) or None), (round(best_brk, 3) or None)


def _speed_g_band(samples: list[Sample], band: float, window_s: float = 1.0) -> tuple[float | None, float | None]:
    """Same speed-derived longitudinal g as _speed_g, but only counts windows that START at or
    above `band` km/h: roll-on acceleration (pushing hard while already fast) and braking from
    real speed. Cheat-resistant — you must genuinely be going `band`+ km/h. Returns (accel_g, brake_g)."""
    pts = [(s.t, _corrob_speed(s)) for s in samples]
    pts = [(t, v) for t, v in pts if v is not None]
    if len(pts) < 2:
        return None, None
    best_acc = best_brk = 0.0
    left = 0
    for right in range(1, len(pts)):
        while right - left > 1 and (pts[right][0] - pts[left][0]).total_seconds() > window_s:
            left += 1
        dt = (pts[right][0] - pts[left][0]).total_seconds()
        if dt <= 0 or pts[left][1] < band:
            continue
        g = abs((pts[right][1] - pts[left][1]) / dt) * _KMH_S_TO_G
        if g > MAX_LON_G:                      # unphysical -> speed glitch / GPS dropout, not a real g
            continue
        if pts[right][1] >= pts[left][1]:
            best_acc = max(best_acc, g)
        else:
            best_brk = max(best_brk, g)
    return (round(best_acc, 3) or None), (round(best_brk, 3) or None)


def _fastest_stop(samples: list[Sample], from_kmh: float, to_kmh: float = 2.0) -> float | None:
    """Shortest time to brake from `from_kmh`+ down to a near-standstill (<=`to_kmh`), using the
    corroborated speed. An emergency-stop metric — can't be faked (you must really be going fast,
    then really stop). Lower is better. Re-accelerating back above `from_kmh` resets the window."""
    best = None
    start = None
    for s in samples:
        sp = _corrob_speed(s)
        if sp is None:
            continue
        if sp >= from_kmh:
            start = s.t                       # latest moment at/above the entry speed
        elif sp <= to_kmh and start is not None:
            dt = (s.t - start).total_seconds()
            if dt > 0 and (best is None or dt < best):
                best = dt
            start = None
    return round(best, 2) if best is not None else None


def _moving_seconds(samples: list[Sample], min_kmh: float = 2.0, max_gap_s: float = 30.0) -> float | None:
    """Seconds actually rolling: add the time up to each sample only when the previous sample was
    moving (corroborated speed > min_kmh). Each gap is capped at max_gap_s so a long stop or a
    paused/resumed recording isn't counted as ride time. This is the real 'hours on the wheel',
    not the whole logging session."""
    total = 0.0
    prev_t = prev_v = None
    for s in samples:
        v = _corrob_speed(s)
        if prev_t is not None and prev_v is not None and prev_v > min_kmh:
            dt = (s.t - prev_t).total_seconds()
            if 0 < dt <= max_gap_s:
                total += dt
        prev_t, prev_v = s.t, v
    return round(total, 1) if total > 0 else None


def _cutout_count(samples: list[Sample], ride_kmh: float = 20.0, freespin_jump: float = 40.0,
                  g_fall: float = 2.5, window_s: float = 2.0) -> int:
    """Count cutout / overlean fall events. The classic signature, all in one short window:
    you were riding at speed (corroborated >= ride_kmh just before), then the wheel SUDDENLY
    free-spins (wheel speed leaps >= freespin_jump km/h in <=1.5s while GPS does NOT follow —
    the motor cut out so the wheel spins free), with a g-force impact spike (|g| >= g_fall)
    within window_s — the fall. Clustered so one crash counts once. Strict on purpose: a false
    positive wrongly blames a wheel model."""
    g_times = [s.t for s in samples if s.g is not None and abs(s.g) >= g_fall]
    if not g_times:
        return 0
    n = 0
    last = None
    prev = None                       # (t, wheel speed, corroborated speed) of the previous sample
    for s in samples:
        ws = s.speed
        if prev is not None and ws is not None and prev[1] is not None:
            dt = (s.t - prev[0]).total_seconds()
            gps_follows = s.gps_speed is not None and s.gps_speed >= ws - 15   # real accel, not a free-spin
            riding = prev[2] is not None and prev[2] >= ride_kmh               # at speed just before
            if 0 < dt <= 1.5 and (ws - prev[1]) >= freespin_jump and not gps_follows and riding \
                    and any(abs((gt - s.t).total_seconds()) <= window_s for gt in g_times):
                if last is None or (s.t - last).total_seconds() > 5:
                    n += 1
                    last = s.t
        prev = (s.t, ws, _corrob_speed(s))
    return n


def _ascent_m(samples: list[Sample], hysteresis_m: float = 3.0) -> float | None:
    """Elevation gain from altitude samples, with a hysteresis to filter GPS noise."""
    alts = [s.alt for s in samples if s.alt is not None]
    if len(alts) < 2:
        return None
    gain = 0.0
    ref = alts[0]
    for a in alts[1:]:
        if a - ref > hysteresis_m:
            gain += a - ref
            ref = a
        elif ref - a > hysteresis_m:
            ref = a
    return round(gain, 1)


def _descent_m(samples: list[Sample], hysteresis_m: float = 3.0) -> float | None:
    """Elevation LOST from altitude samples (the downhill total), hysteresis-filtered."""
    alts = [s.alt for s in samples if s.alt is not None]
    if len(alts) < 2:
        return None
    drop = 0.0
    ref = alts[0]
    for a in alts[1:]:
        if ref - a > hysteresis_m:
            drop += ref - a
            ref = a
        elif a - ref > hysteresis_m:
            ref = a
    return round(drop, 1)


def _alt_range(samples: list[Sample]) -> float | None:
    """Altitude swing (max - min) over the ride."""
    alts = [s.alt for s in samples if s.alt is not None]
    return round(max(alts) - min(alts), 1) if len(alts) >= 2 else None


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


def _corrob_speed(s: Sample) -> float | None:
    """Per-sample speed used for the realistic top speed: the LOWER of wheel and
    GPS speed when both exist (rejects GPS noise while walking AND sustained
    freespin, where GPS reads ~0). Falls back to wheel speed when there's no GPS."""
    if s.speed is None:
        return None
    return min(s.speed, s.gps_speed) if s.gps_speed is not None else s.speed


MOVE_KMH = 3.0   # a sample only counts as "real riding" above this corroborated speed


def _moving(s: Sample, min_kmh: float = MOVE_KMH) -> bool:
    """Anti-cheat gate: True only when the rider is genuinely moving (corroborated
    wheel+GPS speed >= min_kmh). A wheel spun on a stand reads GPS ~0, so the min is
    ~0 and the sample is excluded — performance/extreme metrics are only credited for
    real riding, not stationary stunts or parked logs."""
    v = _corrob_speed(s)
    return v is not None and v >= min_kmh


def _speeds(samples: list[Sample], wheel_speeds: list[float],
            max_accel: float = MAX_ACCEL_KMH_S,
            freespin_margin: float = 5.0) -> tuple[float | None, float | None]:
    """Return (realistic_max_speed, max_freespin).

    A real top speed must be *reached through believable acceleration*. We walk
    the samples in time order and clamp how fast the speed may rise (max_accel);
    decelerations are always allowed. The realistic top speed is the peak of this
    acceleration-limited track. The raw peak that the cap rejected (an instantaneous
    jump with no ramp — a freespin or sensor spike) is reported separately as
    max_freespin so it can be celebrated as its own category, not counted as speed."""
    plausible = None      # running believable speed
    realistic = None      # peak of the believable track
    prev_t = None
    for s in samples:
        v = _corrob_speed(s)
        if v is None:
            continue
        if plausible is None or prev_t is None:
            plausible = v
        elif v <= plausible:
            plausible = v                                      # slowing down: always believable
        else:
            dt = max((s.t - prev_t).total_seconds(), 0.0)
            plausible = min(v, plausible + max_accel * dt)     # speeding up: capped by accel
        prev_t = s.t
        realistic = plausible if realistic is None else max(realistic, plausible)

    raw_max = max(wheel_speeds) if wheel_speeds else None
    if realistic is None:
        return raw_max, None
    # The spike only counts as "freespin" when it's clearly beyond what real
    # acceleration could have produced (freespin_margin km/h over the believable peak).
    freespin = raw_max if (raw_max is not None and raw_max > realistic + freespin_margin) else None
    return realistic, freespin


def summarize(samples: list[Sample], gps_tolerance: float = 0.4,
              cal: dict | None = None, teleport_kmh: float = 150.0) -> TripSummary:
    if not samples:
        raise ValueError("cannot summarize empty sample list")
    c = {**CALIBRATION_DEFAULTS, **(cal or {})}     # admin overrides on top of defaults
    start, end = samples[0].t, samples[-1].t
    duration = (end - start).total_seconds()

    gps_km = gps_distance_km(samples, teleport_kmh)
    odo_km, has_odo = odometer_distance_km(samples, c["odo_max_step_km"])
    gps_present = sum(1 for s in samples if s.lat is not None and s.lon is not None) >= 2
    # ANTI-CHEAT: never credit the wheel odometer when GPS is present and shows the rider
    # barely moved — that's a stationary wheel-spin faking distance (GPS ~0, odo climbing).
    # GPS noise inflates distance, never deflates it to ~0, so near-zero GPS = truly stationary.
    if gps_present and gps_km < 0.2 and odo_km > gps_km:
        distance = gps_km
    # Otherwise prefer the odometer when meaningful: no GPS at all (legit no-signal ride), or
    # odo isn't severely under GPS (a coarse/non-updating odometer defers to GPS movement).
    elif has_odo and odo_km > 0.1 and (not gps_present or odo_km >= gps_km * (1 - gps_tolerance)):
        distance = odo_km
    else:
        distance = gps_km

    speeds = [s.speed for s in samples if s.speed is not None]
    # avg speed over MOVING samples only (>2 km/h) so stops don't drag it down
    moving = [v for v in (_corrob_speed(s) for s in samples) if v is not None and v > 2.0]
    avg_speed = (sum(moving) / len(moving)) if moving else None
    moving_s = _moving_seconds(samples)        # real ride time (rolling >2 km/h), not the whole log
    max_speed, max_freespin = _speeds(samples, speeds, c["max_accel"], c["freespin_margin"])

    # --- ANTI-CHEAT GATE: performance & extreme metrics are only credited while the rider
    # is genuinely MOVING (corroborated wheel+GPS speed >= MOVE_KMH). A wheel spun on a stand
    # reads GPS ~0, so the min is ~0 and those samples drop out — no free scores from stationary
    # stunts or parked logs. Distance, ride-time and realistic top speed keep their own logic.
    mov = [s for s in samples if _moving(s)]
    g_moving = lambda s: abs(s.g) if (s.g is not None and _moving(s)) else None

    # G-force board = SUSTAINED g over real riding. The instantaneous peak is kept raw (any g)
    # for the crash/plausibility warning, never as the leaderboard metric.
    gs = [abs(s.g) for s in samples if s.g is not None]
    max_gforce_spike = max(gs) if gs else None
    max_gforce = _sustained_max(samples, g_moving, c["sustain_secs"])

    wh = _energy_wh(samples)
    wh_per_km = (wh / distance) if (wh is not None and distance > 0) else None

    # voltage is NOT movement-gated: peak voltage is just battery charge, and sag is measured
    # against the RESTING baseline (rest -> hard pull), which gating away would destroy.
    volts = [s.voltage for s in samples if s.voltage is not None]
    peak_voltage = max(volts) if volts else None
    max_voltage_sag = _max_voltage_sag(samples, c["sag_window_s"])
    max_sustained_w = _sustained_max(samples, lambda s: _power(s) if _moving(s) else None, c["sustain_secs"])
    max_sustained_a = _sustained_max(samples, lambda s: s.current if _moving(s) else None, c["sustain_secs"])
    fastest_0_40_s = _fastest_0_40(samples, c["accel_target_kmh"], c["accel_min_s"], c["accel_max_s"])
    sustained_accel = _max_sustained_accel(mov, c["sustain_accel_lo_s"], c["sustain_accel_hi_s"])
    ascent_m = _ascent_m(mov, c["ascent_hysteresis_m"])
    descent_m = _descent_m(mov, c["ascent_hysteresis_m"])
    alt_range_m = _alt_range(mov)
    battery_used_pct = _battery_used(mov)
    est_range_km = (round(distance * 100.0 / battery_used_pct, 1)
                    if (battery_used_pct and battery_used_pct >= c["range_min_battery_pct"] and distance > 0) else None)

    # absolute per-trip extremes (feed gated min/max boards) — also gated to real riding
    alts = [s.alt for s in mov if s.alt is not None]
    temps = [s.temp for s in mov if s.temp is not None]
    pwms = [s.pwm for s in mov if s.pwm is not None]
    batts = [s.battery for s in mov if s.battery is not None]
    max_altitude_m = round(max(alts), 1) if alts else None
    min_altitude_m = round(min(alts), 1) if alts else None
    max_temp = round(max(temps), 1) if temps else None
    min_temp = round(min(temps), 1) if temps else None
    max_pwm = round(max(pwms), 1) if pwms else None
    min_battery_pct = round(min(batts), 1) if batts else None

    # --- newer (hidden) metrics, fed to gated leaderboards ---------------------
    # Group A: longer sustained windows (the 2s spikes were getting too easy to game),
    # all gated to real riding via g_moving / _moving.
    g_sust_4s = _sustained_max(samples, g_moving, 4.0)
    g_sust_6s = _sustained_max(samples, g_moving, 6.0)
    pwm_sust_3s = _sustained_max(samples, lambda s: s.pwm if _moving(s) else None, 3.0)
    speed_sust_5s = _sustained_max(samples, _corrob_speed, 5.0)
    speed_sust_10s = _sustained_max(samples, _corrob_speed, 10.0)
    power_sust_6s = _sustained_max(samples, lambda s: _power(s) if _moving(s) else None, 6.0)
    current_sust_6s = _sustained_max(samples, lambda s: s.current if _moving(s) else None, 6.0)
    # Group B: g-force while genuinely fast (sustained over the calibration window).
    sw = c["sustain_secs"]
    g_fast_20 = _sustained_max(samples, _g_fast(20.0), sw)
    g_fast_30 = _sustained_max(samples, _g_fast(30.0), sw)
    g_fast_40 = _sustained_max(samples, _g_fast(40.0), sw)
    # Group C: directional g — sideways (cornering) and fore-aft (braking/launch), gated to riding.
    g_lateral = _sustained_max(samples, lambda s: abs(s.gx) if (s.gx is not None and _moving(s)) else None, sw)
    g_brake = _sustained_max(samples, lambda s: abs(s.gy) if (s.gy is not None and _moving(s)) else None, sw)
    # Group D: experimental wobble/shake index (lateral-g oscillation) — only while moving.
    shake_index = _max_shake(mov, sw)
    # Speed-derived longitudinal g: how hard you launch / brake (every wheel reports speed).
    accel_g, brake_g = _speed_g(samples, 1.0)
    # Cheat-proof sprints + banded roll-on/braking g + emergency-stop times (all from real speed).
    t_0_60_s = _fastest_0_40(samples, 60.0, 1.0, 40.0)
    t_0_100_s = _fastest_0_40(samples, 100.0, 1.0, 60.0)
    accel_g_30, brake_g_30 = _speed_g_band(samples, 30.0, 1.0)
    accel_g_50, brake_g_50 = _speed_g_band(samples, 50.0, 1.0)
    stop_30_s = _fastest_stop(samples, 30.0)
    stop_50_s = _fastest_stop(samples, 50.0)
    cutout_count = _cutout_count(samples)        # overlean/cutout fall events (per-wheel-model safety signal)

    return TripSummary(
        start_utc=start, end_utc=end, duration_s=duration, moving_s=moving_s,
        distance_km=distance, gps_distance_km=gps_km,
        max_speed=max_speed, max_freespin=max_freespin, avg_speed=avg_speed,
        max_gforce=max_gforce, max_gforce_spike=max_gforce_spike,
        wh_per_km=wh_per_km, max_sustained_w=max_sustained_w,
        max_sustained_a=max_sustained_a, peak_voltage=peak_voltage,
        max_voltage_sag=max_voltage_sag, sustained_accel=sustained_accel,
        fastest_0_40_s=fastest_0_40_s, ascent_m=ascent_m, descent_m=descent_m,
        battery_used_pct=battery_used_pct, est_range_km=est_range_km,
        alt_range_m=alt_range_m, max_altitude_m=max_altitude_m, min_altitude_m=min_altitude_m,
        max_temp=max_temp, min_temp=min_temp, max_pwm=max_pwm, min_battery_pct=min_battery_pct,
        g_sust_4s=g_sust_4s, g_sust_6s=g_sust_6s, pwm_sust_3s=pwm_sust_3s,
        speed_sust_5s=speed_sust_5s, speed_sust_10s=speed_sust_10s,
        power_sust_6s=power_sust_6s, current_sust_6s=current_sust_6s,
        g_fast_20=g_fast_20, g_fast_30=g_fast_30, g_fast_40=g_fast_40,
        g_lateral=g_lateral, g_brake=g_brake, shake_index=shake_index,
        accel_g=accel_g, brake_g=brake_g,
        t_0_60_s=t_0_60_s, t_0_100_s=t_0_100_s,
        accel_g_30=accel_g_30, accel_g_50=accel_g_50,
        brake_g_30=brake_g_30, brake_g_50=brake_g_50,
        stop_30_s=stop_30_s, stop_50_s=stop_50_s, cutout_count=cutout_count,
        sample_count=len(samples),
    )
