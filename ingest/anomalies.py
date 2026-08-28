"""Free spins, lifts and falls: one physical event, read from its context.

A free spin, a pickup test and a cutout look identical in telemetry - the wheel turning
while nothing loads the motor. What separates them is what the wheel was doing either side:

    unloaded spin while travelling   -> FALL   (cutouts land here)
    unloaded spin from a standstill  -> LIFT   (pickup test, hand spin, bench check)

So the detector never hunts for a fall directly. It finds the unloaded spin, then reads the
context around it.

Current is the discriminating signal. A free-spinning wheel takes only bearing drag and
windage, under an amp; a wheel carrying a rider takes 5-30. Acceleration beyond what a loaded
wheel can produce is the fallback for logs with no current column, and current wins whenever
both exist - otherwise every hard launch on a fast wheel reads as a free spin.

Wheel-versus-GPS finds candidates and nothing more. In the reference library of 227 trips, of
495 raw candidates (wheel over 12 km/h while GPS read under 2) only 8% were genuinely
unloaded; the other 92% were riding through a GPS dropout of about two seconds. A detector
built on that comparison alone inherits that error rate, so it is never trusted here on its
own.

Spec: "EUC Cutout Detection"; reference implementation detectAnomalies() in eucviewer
static/js/analytics.js, validated on 227 trips.
"""
from __future__ import annotations

DEFAULTS = {
    "free_spin_kmh": 12.0,    # wheel speed before a GPS standstill counts as a candidate.
                              # The strongest lever on false positives: at 5 km/h, walking a
                              # wheel off a lift gets reported as a free spin.
    "gps_stopped_kmh": 2.0,   # GPS speed treated as stationary
    "accel_impossible": 6.0,  # m/s^2 a loaded wheel cannot produce (a real EUC peaks near 3)
    "motor_active_a": 1.0,    # amps that count as the motor doing work (watts track at x100)
    "min_event_len": 2,       # shortest run of samples that can be a lift
    "max_gap_s": 5.0,         # a longer gap makes every rate meaningless
}
_W_PER_A = 100.0              # the watts cutoff tracks the amp cutoff
_SURROUND = 8                 # samples either side for "did the motor ever work"
_CONTEXT = 10                 # samples either side for "was it travelling"
_MOVING_KMH = 3.0             # median GPS speed that counts as travelling


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def _drawing(s, amps: float) -> bool:
    """Is this sample's motor doing work? Absent data is NOT an idle motor, so callers must
    gate on has_motor before reading a False here as evidence of anything."""
    return ((s.current is not None and abs(s.current) >= amps)
            or (s.power is not None and abs(s.power) >= amps * _W_PER_A))


def _has_motor_data(samples) -> bool:
    """Probe once per trip, before anything else.

    Older exports stop at GPS speed and carry no current or power at all. Reading a missing
    column as zero makes every "was the motor working?" test answer no, and the detector then
    concludes the wheel was never loaded for the entire ride. In the reference library that
    turned 2,521 events into 2,521 logger glitches, reporting zero lifts and zero falls on a
    dataset that contained a real fall.
    """
    return any((s.current is not None and abs(s.current) > 0)
               or (s.power is not None and abs(s.power) > 0) for s in samples)


def _flag(samples, c, has_motor) -> list:
    """Pass 1: mark anything that looks wrong. Three independent detectors, any one suffices."""
    flags = [False] * len(samples)
    for i in range(1, len(samples)):
        a, b = samples[i - 1], samples[i]
        if a.speed is None or b.speed is None:
            continue
        dt = (b.t - a.t).total_seconds()
        if dt <= 0 or dt > c["max_gap_s"]:
            continue                              # a logging gap makes every rate a lie
        g_now, g_prev = b.gps_speed, a.gps_speed
        # (a) the wheel turns and the ground does not. A candidate only: riding through a GPS
        #     dropout looks exactly the same, which the load tests below are there to settle.
        has_gps = (g_now is not None and g_now > 0.3) or (g_prev is not None and g_prev > 0.3)
        if has_gps and b.speed > c["free_spin_kmh"] and (g_now or 0.0) < c["gps_stopped_kmh"]:
            flags[i] = True
            continue
        # (b) acceleration a loaded wheel cannot produce
        if ((b.speed - a.speed) / 3.6) / dt > c["accel_impossible"] and b.speed > 10:
            flags[i] = True
            continue
        # (c) phantom speed with nothing drawing current anywhere near it: a logger writing
        #     fiction, typically during shutdown. Gated on has_motor, or it fires on every
        #     fast sample of a log that simply has no current column.
        if has_motor and b.speed >= 25 and not _drawing(b, c["motor_active_a"]):
            near = samples[max(0, i - 2):i + 3]
            if not any(_drawing(x, c["motor_active_a"]) for x in near):
                flags[i] = True
    return flags


def _events(flags) -> list:
    """Pass 2: group flags into events, tolerating one clean sample so a single good reading
    in the middle does not split one event into two."""
    out, i, n = [], 0, len(flags)
    while i < n:
        if not flags[i]:
            i += 1
            continue
        start = end = i
        j = i + 1
        while j < n:
            if flags[j]:
                end = j
            elif not (j + 1 < n and flags[j + 1]):
                break                             # two clean samples in a row ends the event
            j += 1
        out.append((start, end))
        i = end + 1
    return out


def _moving(samples, lo, hi, has_motor, c):
    """Tri-state: True, False, or None when it cannot be known.

    Returning None rather than False is what stops a trip with neither GPS nor current from
    defaulting into a verdict. Medians, not means: one spike must not decide whether somebody
    was riding.
    """
    win = samples[max(0, lo):max(0, hi)]
    gps = [x.gps_speed for x in win if x.gps_speed is not None]
    if len(gps) >= 3:
        return _median(gps) >= _MOVING_KMH
    if has_motor:
        amps = [abs(x.current) for x in win if x.current is not None]
        if amps:
            return _median(amps) >= c["motor_active_a"]
    return None


def detect(samples, cal: dict | None = None) -> dict:
    """Count falls, lifts, spikes and glitches across one trip's samples."""
    c = {**DEFAULTS, **(cal or {})}
    out = {"fall": 0, "lift": 0, "spike": 0, "glitch": 0}
    if len(samples) < 3:
        return out
    has_motor = _has_motor_data(samples)
    flags = _flag(samples, c, has_motor)

    for start, end in _events(flags):
        ev = samples[start:end + 1]
        speeds = [x.speed for x in ev if x.speed is not None]
        if not speeds:
            continue
        peak = max(speeds)

        # Did the motor work either side of this? True by definition when the log has no
        # motor data, so an absent column can never manufacture a "glitch".
        if has_motor:
            around = (samples[max(0, start - _SURROUND):start]
                      + samples[end + 1:end + 1 + _SURROUND])
            surrounding_worked = any(_drawing(x, c["motor_active_a"]) for x in around)
        else:
            surrounding_worked = True

        idle = sum(1 for x in ev if not _drawing(x, c["motor_active_a"])) / len(ev)
        peak_accel = 0.0
        for k in range(start + 1, end + 1):
            a, b = samples[k - 1], samples[k]
            dt = (b.t - a.t).total_seconds()
            if a.speed is not None and b.speed is not None and 0 < dt <= c["max_gap_s"]:
                peak_accel = max(peak_accel, ((b.speed - a.speed) / 3.6) / dt)

        # Current decides when it exists; acceleration is only the proxy for logs without it.
        unloaded = peak >= c["free_spin_kmh"] and (
            idle >= 0.7 if has_motor else peak_accel >= c["accel_impossible"])
        before = _moving(samples, start - _CONTEXT, start, has_motor, c)
        after = _moving(samples, end + 1, end + 1 + _CONTEXT, has_motor, c)

        # The asymmetry is deliberate: a fall must be positively confirmed by a stop, while a
        # lift may end in unknown territory, which is common at the end of a log.
        if not surrounding_worked:
            out["glitch"] += 1
        elif unloaded and before is True and after is False:
            out["fall"] += 1
        elif unloaded and before is False and after is not True and len(ev) >= c["min_event_len"]:
            out["lift"] += 1
        else:
            out["spike"] += 1
    return out
