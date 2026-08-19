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
    "accel_gap_max_s": 3.0,       # s — cap the accel allowance across a logging gap
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
    "eff_min_km": 1.0,            # km — shorter than this and Wh/km is noise, not efficiency
    "eff_min_wh_km": 5.0,         # Wh/km — below this the power channel is broken, not thrifty
    "eff_max_wh_km": 300.0,       # Wh/km — above this it's a unit/scale error, not a hungry wheel
    "range_min_km": 1.0,          # km — too short a ride to extrapolate a full-charge range from
    "range_max_km": 400.0,        # km — beyond any real EUC, so the drain reading was wrong
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
    temp_rise_rate: float | None   # fastest sustained board heat-up (deg/s) while riding
    temp_drop_rate: float | None   # fastest sustained board cool-down (deg/s) while riding
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


def teleport_segments(pts, teleport_kmh: float = 150.0, max_gap_s: float = 20.0,
                      min_ride_kmh: float = 8.0, min_jump_m: float = 150.0):
    """GPS hops that look like a GENUINE teleport, excluding the three benign cases:
      * indoor / standing GPS drift — the wheel wasn't really riding (speed < min_ride_kmh),
      * tunnel / signal loss — the jump just spans a GPS outage (gap > max_gap_s),
      * ordinary position noise — the hop didn't actually move the rider (< min_jump_m).
    A hop counts only when the rider was riding, GPS was sampling continuously, the implied
    speed exceeds teleport_kmh AND the rider really was displaced.

    That last test matters because implied speed alone is meaningless at short sample
    intervals: at 1 Hz a 42 m hop already reads as >150 km/h, and phone GPS wanders further
    than that near buildings and trees (a single bad fix produces two hops, out and back).
    A real teleport moves you hundreds of metres; noise doesn't, however fast it looks.

    `pts` is [(time, lat, lon, wheel_speed)]; returns the jump segments as
    [[lon,lat],[lon,lat]] (GeoJSON order)."""
    segs = []
    prev = None
    for (t, lat, lon, ws) in pts:
        if lat is None or lon is None:
            continue
        if prev is not None and t and prev[0]:
            dt = (t - prev[0]).total_seconds()
            d = _haversine_km(prev[1], prev[2], lat, lon)
            wheel = max(prev[3] or 0.0, ws or 0.0)          # wheel speed around the hop
            if 0 < dt <= max_gap_s and wheel >= min_ride_kmh and d * 1000.0 >= min_jump_m \
                    and (d / (dt / 3600.0)) > teleport_kmh:
                segs.append([[prev[2], prev[1]], [lon, lat]])
        prev = (t, lat, lon, ws)
    return segs


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


# Power is held across a sample interval to integrate energy. Generous enough that any real
# log rate (1 Hz to a coarse one-per-minute) integrates normally, tight enough that a genuine
# multi-minute pause can't hold the last wattage and invent energy never drawn.
ENERGY_MAX_GAP_S = 120.0


def _energy_wh(samples: list[Sample], max_gap_s: float = ENERGY_MAX_GAP_S) -> float | None:
    """Energy DRAWN from the pack over the ride, in Wh.

    Regenerative braking (negative power) is clamped to zero rather than subtracted. Summing
    signed power made consumption cancel out: a regen-heavy descent drove the integral toward
    zero, so the harder you braked the more "efficient" you looked — which is what filled the
    efficiency board with impossible sub-1 Wh/km figures. Gross draw is the honest measure of
    what the ride cost, and it can't be gamed by braking.

    A sample's power is also only integrated across a real interval: a logging gap must not
    hold the last known wattage for minutes and invent energy that was never drawn."""
    if not any(s.current is not None for s in samples):
        return None
    wh = 0.0
    prev = None  # (t, power_w)
    for s in samples:
        p = s.power if s.power is not None else (
            s.voltage * s.current if (s.voltage is not None and s.current is not None) else None)
        if prev is not None and prev[1] is not None:
            dt = (s.t - prev[0]).total_seconds()
            if 0 < dt <= max_gap_s:
                wh += max(prev[1], 0.0) * dt / 3600.0     # regen never pays energy back
        prev = (s.t, p)
    return wh if wh > 0 else None


def _wh_per_km(wh: float | None, distance_km: float, c: dict) -> float | None:
    """Wh/km, or None when the ride can't support the claim. Reporting nothing keeps a broken
    reading off the board entirely; clamping would invent a plausible-looking number instead.
    Real EUCs sit around 15-80 Wh/km, so anything outside a generous band is a broken power
    channel or a unit/scale mismatch, not a remarkable wheel."""
    if wh is None or distance_km < c["eff_min_km"] or distance_km <= 0:
        return None
    v = wh / distance_km
    return v if c["eff_min_wh_km"] <= v <= c["eff_max_wh_km"] else None


def _sustained_max(samples: list[Sample], fn, window_s: float = 2.0) -> float | None:
    """Max average of fn(sample) over any trailing window of ~window_s seconds.

    A window only counts as evidence that a value was HELD when it actually covers the
    period: at least two points, spanning at least half of window_s, with no internal hole
    bigger than window_s. Without those checks a lone point was its own window and its
    instantaneous value was reported as sustained.

    That mattered because the series is not continuous. Callers filter out samples that fail
    the movement gate, so the surviving points are full of holes, and a single spike landing
    after one of them stood alone. On a real 1 Hz ride whose g readings had a median of 0.08,
    one pothole sample sat 9.1 s clear of its neighbours and became a 3.15 g "2-second
    sustained" record. Every sustained metric is built on this helper, so a single sample
    could mint a record on any of them.

    Returns None when no window qualifies: no claim is better than a false one."""
    pts = [(s.t, fn(s)) for s in samples if fn(s) is not None]
    if len(pts) < 2:
        return None
    min_span = window_s * 0.5      # the window has to cover most of the period it claims
    best = None
    left = 0
    ssum = 0.0
    for right in range(len(pts)):
        if right > 0 and (pts[right][0] - pts[right - 1][0]).total_seconds() > window_s:
            left, ssum = right, 0.0            # a hole breaks continuity: start a fresh window
        ssum += pts[right][1]
        while (pts[right][0] - pts[left][0]).total_seconds() > window_s:
            ssum -= pts[left][1]
            left += 1
        span = (pts[right][0] - pts[left][0]).total_seconds()
        if right - left + 1 < 2 or span < min_span:
            continue                           # one point, or too little of the period covered
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
# tyre grip + rider balance allow — ~0.5-0.7 g is already a hard stop/launch. Anything meaningfully
# above that is a speed glitch or a GPS dropout across the window (e.g. 70->2 km/h in ~1 s = ~1.9 g,
# or a noisy GPS-only speed channel wobbling ±35 km/h/s), so we drop that window. Set just above the
# real ceiling so a genuine emergency stop still counts but phantom ~1 g records can't form.
MAX_LON_G = 0.8

# Board/battery temperature is the EUC's internal sensor, not ambient air. EUC firmware emits
# 0.0 as a "no reading" placeholder (and occasionally wild garbage like -304 C, below absolute
# zero), in bursts that arrive as an impossible cliff -- e.g. 73,73,0,0,...,0,41. A flat band
# isn't enough (a genuine 0 should still count), so per-trip min/max temp is taken over a
# slew-limited series: board temperature has thermal inertia, it drifts, it can't fall 73 C in
# one sample. Without this the "Coldest ride" board fills with sensor dropouts, not cold rides.
TEMP_MIN_C, TEMP_MAX_C = -40.0, 150.0   # absolute plausibility band (kills sub-absolute-zero etc.)
TEMP_MAX_SLEW_C_S = 5.0                 # max believable board-temp change per second
TEMP_STEP_CAP_C = 20.0                  # ceiling on one accepted step across a sample gap


def _valid_temp_points(samples: list[Sample]) -> list[tuple]:
    """Board temp (time, value) pairs with sensor dropouts removed, in time order. We
    anchor on the median reading (robust while dropouts are a minority) and walk outward
    in both directions, keeping a sample only when it sits within a thermally-plausible
    step of the last accepted one. A 30,0,0,32 dropout -- or a 976-sample run of 0s
    entered on a cliff -- is rejected; a genuine 0 that drifts in with neighbours survives."""
    pts = [(s.t, s.temp) for s in samples
           if s.temp is not None and TEMP_MIN_C <= s.temp <= TEMP_MAX_C]
    if len(pts) <= 1:
        return pts
    vals = [v for _, v in pts]
    anchor = sorted(vals)[len(vals) // 2]                       # median
    seed = min(range(len(pts)), key=lambda i: abs(pts[i][1] - anchor))

    def _ok(t_a, v_a, t_b, v_b) -> bool:
        dt = abs((t_a - t_b).total_seconds())
        return abs(v_a - v_b) <= min(TEMP_STEP_CAP_C, TEMP_MAX_SLEW_C_S * max(dt, 1.0))

    keep = [False] * len(pts)
    keep[seed] = True
    last_t, last_v = pts[seed]
    for i in range(seed + 1, len(pts)):                         # forward
        t, v = pts[i]
        if _ok(t, v, last_t, last_v):
            keep[i] = True; last_t, last_v = t, v
    last_t, last_v = pts[seed]
    for i in range(seed - 1, -1, -1):                           # backward
        t, v = pts[i]
        if _ok(t, v, last_t, last_v):
            keep[i] = True; last_t, last_v = t, v
    return [pts[i] for i in range(len(pts)) if keep[i]]         # time-ordered


def _valid_temp_series(samples: list[Sample]) -> list[float]:
    """De-spiked board temps as plain values (for per-trip min/max)."""
    return [v for _, v in _valid_temp_points(samples)]


# Temp incline/decline rate: how fast the board heats up / cools down while riding.
# Measured over the de-spiked series so a dropout can't fake a huge slope, and only
# within continuous data (a window can't bridge a > TEMP_RATE_GAP_S signal gap).
TEMP_RATE_LO_S, TEMP_RATE_HI_S, TEMP_RATE_GAP_S = 5.0, 60.0, 15.0


def _max_temp_rate(points: list[tuple], rising: bool,
                   lo: float = TEMP_RATE_LO_S, hi: float = TEMP_RATE_HI_S,
                   max_gap_s: float = TEMP_RATE_GAP_S) -> float | None:
    """Fastest sustained board-temp change in deg/s (rising or falling), held for at
    least `lo`s (a real trend, not one sample) and at most `hi`s (so a long slow drift
    doesn't dilute a sharp change). `points` is the de-spiked (time, temp) series."""
    best = 0.0
    n = len(points)
    for i in range(n):
        for k in range(i + 1, n):
            if (points[k][0] - points[k - 1][0]).total_seconds() > max_gap_s:
                break                                  # don't measure across a signal gap
            dt = (points[k][0] - points[i][0]).total_seconds()
            if dt < lo:
                continue
            if dt > hi:
                break
            rate = (points[k][1] - points[i][1]) / dt
            r = rate if rising else -rate
            if r > best:
                best = r
    return round(best, 3) if best > 0 else None


# A speed-derived accel/brake g only counts if the change PERSISTS: a real stop stays stopped,
# a real launch stays fast. A transient speed spike swings back within a second or two — e.g. a
# noisy GPS channel wobbling 56->21->41 km/h reads as a ~1 g "brake" that never happened (the
# motor did nothing). If the speed reverts by more than half its change inside _G_HOLD_S, the
# window was a spike, not a real g-event, and is dropped. This is what corroboration can't catch
# on wheels whose speed column is GPS-only (wheel_speed == gps_speed).
_G_HOLD_S = 3.0
_G_REVERT_FRAC = 0.5


def _sustained_speed_change(pts, right: int, delta: float, accel: bool) -> bool:
    """True unless the speed reached at index `right` is undone within _G_HOLD_S. A braked-to
    speed that bounces back UP (or a launched-to speed that falls back DOWN) past half its change
    was a transient spike, not a sustained g-event. No look-ahead data (end of ride / a gap) ->
    can't disprove -> accepted."""
    s_end, t_end = pts[right][1], pts[right][0]
    limit = s_end + _G_REVERT_FRAC * delta if not accel else s_end - _G_REVERT_FRAC * delta
    for k in range(right + 1, len(pts)):
        if (pts[k][0] - t_end).total_seconds() > _G_HOLD_S:
            break
        reverted = pts[k][1] > limit if not accel else pts[k][1] < limit
        if reverted:
            return False
    return True


def _best_speed_g(pts, window_s: float, band: float | None = None) -> tuple[float | None, float | None]:
    """(accel_g, brake_g): the strongest SUSTAINED speed-derived longitudinal g over ~window_s
    windows. `band` (optional) counts only windows that start at >= band km/h. Windows whose speed
    change reverts (see [_sustained_speed_change]) are dropped, so GPS-noise spikes can't mint fake
    records. O(n) sliding window; the persistence look-ahead is bounded by _G_HOLD_S."""
    if len(pts) < 2:
        return None, None
    best_acc = best_brk = 0.0
    left = 0
    for right in range(1, len(pts)):
        while right - left > 1 and (pts[right][0] - pts[left][0]).total_seconds() > window_s:
            left += 1
        dt = (pts[right][0] - pts[left][0]).total_seconds()
        if dt <= 0 or (band is not None and pts[left][1] < band):
            continue
        delta = pts[right][1] - pts[left][1]
        g = abs(delta / dt) * _KMH_S_TO_G
        if g > MAX_LON_G:                      # unphysical -> speed glitch / GPS dropout, not a real g
            continue
        accel = delta >= 0
        if not _sustained_speed_change(pts, right, abs(delta), accel):
            continue                           # transient spike that swings back -> not a real g
        if accel:
            best_acc = max(best_acc, g)
        else:
            best_brk = max(best_brk, g)
    return (round(best_acc, 3) or None), (round(best_brk, 3) or None)


def _corrob_speed_pts(samples: list[Sample]) -> list:
    return [(t, v) for t, v in ((s.t, _corrob_speed(s)) for s in samples) if v is not None]


def _speed_g(samples: list[Sample], window_s: float = 1.0) -> tuple[float | None, float | None]:
    """Longitudinal g from how hard wheel speed changes: the strongest sustained push
    (acceleration) and the strongest sustained slow-down (braking), each as a g-force.
    Speed-derived (corroborated wheel/GPS speed) so it works on every wheel without
    trusting a noisy IMU axis. Returns (accel_g, brake_g). A ~1s window plus a persistence
    check keeps it a real hold, not a one-sample spike."""
    return _best_speed_g(_corrob_speed_pts(samples), window_s)


def _speed_g_band(samples: list[Sample], band: float, window_s: float = 1.0) -> tuple[float | None, float | None]:
    """Same speed-derived longitudinal g as _speed_g, but only counts windows that START at or
    above `band` km/h: roll-on acceleration (pushing hard while already fast) and braking from
    real speed. Cheat-resistant — you must genuinely be going `band`+ km/h. Returns (accel_g, brake_g)."""
    return _best_speed_g(_corrob_speed_pts(samples), window_s, band=band)


def _fastest_stop(samples: list[Sample], from_kmh: float, to_kmh: float = 2.0) -> float | None:
    """Shortest time to brake from `from_kmh`+ down to a near-standstill (<=`to_kmh`), using the
    corroborated speed. An emergency-stop metric — can't be faked (you must really be going fast,
    then really stop). Lower is better. Re-accelerating back above `from_kmh` resets the window.
    A stop faster than tyre grip allows (implied deceleration > MAX_LON_G) is a speed glitch — a
    sensor/GPS dropout reading full speed -> 0 in one sample — not a real stop, so it's skipped."""
    max_decel = MAX_LON_G / _KMH_S_TO_G       # km/h shed per second a real EUC can manage
    best = None
    start_t = start_v = None
    for s in samples:
        sp = _corrob_speed(s)
        if sp is None:
            continue
        if sp >= from_kmh:
            start_t, start_v = s.t, sp        # latest moment at/above the entry speed
        elif sp <= to_kmh and start_t is not None:
            dt = (s.t - start_t).total_seconds()
            if dt > 0 and (start_v - sp) / dt <= max_decel:   # physical deceleration only
                if best is None or dt < best:
                    best = dt
            start_t = start_v = None
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


def _battery_used(samples: list[Sample], edge_n: int = 30) -> float | None:
    """Battery % the ride actually consumed: the level it started at minus the level it ended
    at, each taken as a MEDIAN over the first/last `edge_n` readings.

    Battery % is voltage-derived, so it sags under load and recovers when you ease off.
    Summing every downward tick counted that sag as consumption — a ride using 20% with 4%
    sag dips reported 974% drain — which then crushed est_range, since range divides by it.
    Medians at the two ends only move when the level genuinely moved, and are unbothered by
    a ride that happens to start or finish mid-sag.

    A ride that charges part-way nets out here rather than accumulating. The extrapolation
    would be meaningless on such a ride anyway, so netting it out keeps the estimate off the
    board instead of inventing a flattering one."""
    bs = [s.battery for s in samples if s.battery is not None]
    if len(bs) < 2:
        return None
    k = max(1, min(edge_n, len(bs) // 4))
    start = sorted(bs[:k])[k // 2]
    end = sorted(bs[-k:])[k // 2]
    drop = start - end
    return round(drop, 1) if drop > 0 else None


def _est_range(distance_km: float, battery_used_pct: float | None, c: dict) -> float | None:
    """Full-charge range extrapolated from this ride, or None when the ride can't support the
    claim. A short hop or a tiny drain multiplies straight into a confident-looking number
    that no wheel could achieve, so both the ride and the result must be plausible."""
    if (not battery_used_pct or battery_used_pct < c["range_min_battery_pct"]
            or distance_km < c["range_min_km"]):
        return None
    v = distance_km * 100.0 / battery_used_pct
    return round(v, 1) if v <= c["range_max_km"] else None


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
            freespin_margin: float = 5.0,
            gap_max_s: float = 3.0) -> tuple[float | None, float | None]:
    """Return (realistic_max_speed, max_freespin).

    A real top speed must be *reached through believable acceleration*. We walk
    the samples in time order and clamp how fast the speed may rise (max_accel);
    decelerations are always allowed. The realistic top speed is the peak of this
    acceleration-limited track. The raw peak that the cap rejected (an instantaneous
    jump with no ramp — a freespin or sensor spike) is reported separately as
    max_freespin so it can be celebrated as its own category, not counted as speed.

    The allowance across one step is capped at gap_max_s seconds' worth. Without that
    bound a pause in the log dissolved the limiter entirely — over a 60 s gap it
    permitted a 1200 km/h rise — so one garbage sample after a pause became the trip's
    top speed. We genuinely don't know what happened during a gap, so a bounded rise
    is the honest reading: enough to accept a ride that resumes faster than it paused,
    not enough to wave through a sensor spike."""
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
            dt = min(max((s.t - prev_t).total_seconds(), 0.0), gap_max_s)
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
    max_speed, max_freespin = _speeds(samples, speeds, c["max_accel"], c["freespin_margin"],
                                      c["accel_gap_max_s"])

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
    wh_per_km = _wh_per_km(wh, distance, c)

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
    est_range_km = _est_range(distance, battery_used_pct, c)

    # absolute per-trip extremes (feed gated min/max boards) — also gated to real riding
    alts = [s.alt for s in mov if s.alt is not None]
    temp_pts = _valid_temp_points(mov)
    temps = [v for _, v in temp_pts]
    pwms = [s.pwm for s in mov if s.pwm is not None]
    batts = [s.battery for s in mov if s.battery is not None]
    max_altitude_m = round(max(alts), 1) if alts else None
    min_altitude_m = round(min(alts), 1) if alts else None
    max_temp = round(max(temps), 1) if temps else None
    min_temp = round(min(temps), 1) if temps else None
    temp_rise_rate = _max_temp_rate(temp_pts, rising=True)
    temp_drop_rate = _max_temp_rate(temp_pts, rising=False)
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
        max_temp=max_temp, min_temp=min_temp,
        temp_rise_rate=temp_rise_rate, temp_drop_rate=temp_drop_rate,
        max_pwm=max_pwm, min_battery_pct=min_battery_pct,
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
