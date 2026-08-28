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

from .summary import _haversine_km

DEFAULTS = {
    "free_spin_kmh": 12.0,    # wheel speed before a GPS standstill counts as a candidate.
                              # The strongest lever on false positives: at 5 km/h, walking a
                              # wheel off a lift gets reported as a free spin.
    "gps_stopped_kmh": 2.0,   # GPS speed treated as stationary
    "accel_impossible": 6.0,  # m/s^2 a loaded wheel cannot produce (a real EUC peaks near 3)
    "motor_active_a": 1.0,    # amps that count as the motor doing work (watts track at x100)
    "max_accel": 20.0,        # km/h per s a loaded wheel can gain; beyond it the speed is fiction
    "freespin_margin": 5.0,   # km/h the wheel must beat the believable track by to be a free spin
    "track_moving_kmh": 3.0,  # ground speed FROM THE TRACK that contradicts a reported stop
    "min_event_len": 3,       # shortest run of samples that can be a lift, and
    "min_event_s": 2.0,       # the seconds it must cover
    "min_fall_len": 3,        # a fall must persist: one sample is a sensor blip, and
    "min_fall_s": 2.0,        # it must cover real time, whatever the logging rate
    "max_gap_s": 5.0,         # a longer gap makes every rate meaningless
}
_W_PER_A = 100.0              # the watts cutoff tracks the amp cutoff
_CHANNEL_JITTER = 0.05        # amps of variation that prove the current channel is reporting
_SURROUND = 8                 # samples either side for "did the motor ever work"
_CONTEXT = 10                 # samples either side for "was it travelling"
_MOVING_KMH = 3.0             # median GPS speed that counts as travelling
_RUNUP = 4                    # samples of run-up that decide whether a spin began from rest


def _span(ev) -> float:
    """Seconds the event covers. A fall holds the wheel unloaded for as long as the rider is
    off it; a single sample near a threshold is noise, and on this library every one of them
    was (a rolling stop, a braking transient). Length in samples alone is not enough - it says
    nothing at 10 Hz."""
    return (ev[-1].t - ev[0].t).total_seconds() if len(ev) > 1 else 0.0


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def _track_kmh(samples, half: int = 2, max_span_s: float = 8.0) -> list:
    """Ground speed read off the TRACK - where the rider actually was - rather than off the
    reported GPS speed.

    The two disagree, and when they do the track is right. On trip a6e9e5c6 the speedometer
    sat at 0.5 km/h for four seconds while the latitude climbed steadily: eleven metres of
    real ground covered during the reported standstill. A speed field can stall or round to
    zero; a position that keeps advancing cannot. Taken over a few samples either side, so one
    bad fix moves it a little instead of inventing a stop.
    """
    out = [None] * len(samples)
    fixed = [i for i, s in enumerate(samples) if s.lat is not None and s.lon is not None]
    if len(fixed) < 3:
        return out
    for k, i in enumerate(fixed):
        a = fixed[max(0, k - half)]
        b = fixed[min(len(fixed) - 1, k + half)]
        dt = (samples[b].t - samples[a].t).total_seconds()
        if not (0 < dt <= max_span_s):
            continue                              # a gap makes the average meaningless
        d = _haversine_km(samples[a].lat, samples[a].lon, samples[b].lat, samples[b].lon)
        out[i] = d / (dt / 3600.0)
    return out


def _believable(samples, c) -> list:
    """The fastest each sample COULD honestly be going, walking the ride forwards and letting
    speed rise only as fast as a loaded wheel can rise.

    This is the free-spin test, and it needs no current channel - which is the point, because
    122 of the 789 trips in the library carry no current at all and could never be judged on
    motor load. Lift a wheel off the ground and the balance algorithm spins it to 40 or 50
    km/h in a second or two; nothing with a rider aboard does that. Ground speed is the lower
    of wheel and GPS, so a wheel turning above a stationary ground runs away from this track
    on its own.
    """
    out = [None] * len(samples)
    plausible = prev_t = None
    for i, s in enumerate(samples):
        v = s.speed if s.gps_speed is None else min(s.speed or 0.0, s.gps_speed)
        if s.speed is None:
            continue
        if plausible is None or prev_t is None:
            plausible = v
        elif v <= plausible:
            plausible = v
        else:
            dt = min(max((s.t - prev_t).total_seconds(), 0.0), c["max_gap_s"])
            plausible = min(v, plausible + c["max_accel"] * dt)
        prev_t = s.t
        out[i] = plausible
    return out


def _at_rest(samples, lo, hi, c) -> bool:
    """Did the wheel stop and stay stopped? The tell Erwin named for a fall on a log with no
    current: the speed does something violent and then everything simply ends. A rider braking
    hard is still riding a second later; a rider on the ground is not."""
    win = [x.speed for x in samples[max(0, lo):hi] if x.speed is not None]
    return len(win) >= 3 and _median(win) < _MOVING_KMH


def _stopped(s, c, needs_fix: bool, track) -> bool:
    """Did the GROUND stop, as opposed to the receiver losing the satellites?

    A GPS speed of exactly 0.0 is what a log writes both when the rider is standing still and
    when the fix is gone, and the two are told apart by whether the sample still carries a
    position. On trip 04d09800 the zero readings had no latitude or longitude at all: the wheel
    was cruising at 15 km/h through a dropout, and reading that as a standstill made it a fall.

    An absent gps_speed is unknown, never zero - `(s.gps_speed or 0)` would call every log
    without a GPS channel permanently stopped.
    """
    if s.gps_speed is None or s.gps_speed >= c["gps_stopped_kmh"]:
        return False
    if needs_fix and (s.lat is None or s.lon is None):
        return False
    return track is None or track < c["track_moving_kmh"]


def _drawing(s, amps: float) -> bool:
    """Is this sample's motor doing work? Absent data is NOT an idle motor, so callers must
    gate on has_motor before reading a False here as evidence of anything."""
    return ((s.current is not None and abs(s.current) >= amps)
            or (s.power is not None and abs(s.power) >= amps * _W_PER_A))


def _channel_alive(samples, lo, hi) -> bool:
    """Is the current channel actually reporting around here, as opposed to stuck or dead?

    The question this replaces - "did the motor draw a real amp within eight samples?" - has
    the wrong answer for the very thing we most want to find. A wheel spun up on its stand is
    idle before and idle after, by definition, so every free spin classified itself as a
    logger glitch: nought detected out of five hundred trips with one spliced in.

    What separates a dead channel from a quiet one is VARIATION, not magnitude. A parked wheel
    still reports jitter as the controller idles; a BLE feed that has dropped reports the same
    number forever - on trip b95f1410 that number was -0.9 A for nineteen thousand samples,
    and on 2c4829c6 it was exactly 0.0 while the rider did 75 km/h.
    """
    vals = [abs(x.current) for x in samples[max(0, lo):hi] if x.current is not None]
    if len(vals) < 4:
        vals = [abs(x.power) / _W_PER_A for x in samples[max(0, lo):hi] if x.power is not None]
    return len(vals) >= 4 and (max(vals) - min(vals)) > _CHANNEL_JITTER


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


def _flag(samples, c, has_motor, needs_fix, track) -> list:
    """Pass 1: mark anything that looks wrong. Three independent detectors, any one suffices."""
    flags = [False] * len(samples)
    for i in range(1, len(samples)):
        a, b = samples[i - 1], samples[i]
        if a.speed is None or b.speed is None:
            continue
        dt = (b.t - a.t).total_seconds()
        if dt <= 0 or dt > c["max_gap_s"]:
            continue                              # a logging gap makes every rate a lie
        # (a) the wheel turns and the ground does not. A candidate only: riding through a GPS
        #     dropout looks exactly the same, which the load tests below are there to settle.
        if b.speed > c["free_spin_kmh"] and _stopped(b, c, needs_fix, track[i]):
            flags[i] = True
            continue
        # (b) a speed CHANGE a loaded wheel cannot produce, in either direction. Braking
        #     counts as well as accelerating: an EUC pulls up at roughly half a g, so 30 km/h
        #     to nothing in a second is not a rider stopping, it is a rider hitting something.
        #     Only the acceleration half was tested before, which left the whole crash-into-a
        #     -kerb shape invisible on any log without a current channel.
        if abs((b.speed - a.speed) / 3.6) / dt > c["accel_impossible"] and max(a.speed, b.speed) > 10:
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


def _moving(samples, lo, hi, has_motor, c, track=None):
    """Tri-state: True, False, or None when it cannot be known.

    Returning None rather than False is what stops a trip with neither GPS nor current from
    defaulting into a verdict. Medians, not means: one spike must not decide whether somebody
    was riding.
    """
    lo, hi = max(0, lo), max(0, hi)
    win = samples[lo:hi]
    if track is not None:                         # the track outranks the speedometer
        ground = [v for v in track[lo:hi] if v is not None]
        if len(ground) >= 3:
            return _median(ground) >= _MOVING_KMH
    gps = [x.gps_speed for x in win if x.gps_speed is not None]
    if len(gps) >= 3:
        return _median(gps) >= _MOVING_KMH
    if has_motor:
        amps = [abs(x.current) for x in win if x.current is not None]
        if amps:
            return _median(amps) >= c["motor_active_a"]
    return None


def detect(samples, cal: dict | None = None, trace: list | None = None) -> dict:
    """Count falls, lifts, spikes and glitches across one trip's samples.

    Pass `trace` a list to have every event's evidence appended to it. A detector that reports
    nothing is either right or broken, and the only way to tell them apart is to see which
    test each near miss failed - so the survey tool reads this rather than re-deriving the
    decision and grading its own homework."""
    c = {**DEFAULTS, **(cal or {})}
    out = {"fall": 0, "spin": 0, "spike": 0, "glitch": 0}
    if len(samples) < 3:
        return out
    has_motor = _has_motor_data(samples)
    needs_fix = any(s.lat is not None and s.lon is not None for s in samples)
    track = _track_kmh(samples)
    believable = _believable(samples, c)
    flags = _flag(samples, c, has_motor, needs_fix, track)

    for start, end in _events(flags):
        ev = samples[start:end + 1]
        speeds = [x.speed for x in ev if x.speed is not None]
        if not speeds:
            continue
        peak = max(speeds)

        # Is the current channel telling us anything here? True by definition when the log
        # carries no motor data at all, so an absent column can never manufacture a "glitch".
        # The window spans the event as well as its surroundings: what makes a free spin
        # readable is precisely that the current CHANGES when the wheel starts turning.
        surrounding_worked = (not has_motor) or _channel_alive(
            samples, start - _SURROUND, end + 1 + _SURROUND)

        # The MEDIAN load over the event, not the share of idle-looking samples. Regen braking
        # sweeps the current from -16 A up through zero to +7 A, so single samples sit near
        # nothing on the way past; counting those samples made hard braking look like a wheel
        # with no rider on it (trips 36ef1885, f539c9cf). A median asks the question that
        # actually matters - was the motor working THROUGHOUT - and one crossing cannot move it.
        amps = [abs(x.current) for x in ev if x.current is not None]
        watts = [abs(x.power) / _W_PER_A for x in ev if x.power is not None]
        load = _median(amps if amps else watts)
        idle = 1.0 if load is None else (1.0 if load < c["motor_active_a"] else 0.0)
        peak_accel = 0.0                          # the most violent speed change either way
        for k in range(start + 1, end + 1):
            a, b = samples[k - 1], samples[k]
            dt = (b.t - a.t).total_seconds()
            if a.speed is not None and b.speed is not None and 0 < dt <= c["max_gap_s"]:
                peak_accel = max(peak_accel, abs((b.speed - a.speed) / 3.6) / dt)

        # Was the wheel doing something no loaded wheel does?
        #
        # SPEED is the base test and always applies, so a log with no current channel can
        # still be judged - 122 of the 789 trips in the library have none. Where there IS
        # current it CORROBORATES rather than substitutes: both have to agree. That is
        # strictly stronger than either alone, and it is the pairing that matters, because a
        # wheel whose speed ran away while the motor pulled 9 A had a rider on it, and a wheel
        # drawing nothing whose speed never did anything impossible was only coasting.
        runaway = max((s.speed or 0.0) - (believable[k] or 0.0)
                      for k, s in enumerate(samples[start:end + 1], start))
        impossible_speed = peak >= c["free_spin_kmh"] and (
            runaway > c["freespin_margin"] or peak_accel >= c["accel_impossible"])
        current_agrees = (not has_motor) or idle >= 0.7
        unloaded = impossible_speed and current_agrees
        before = _moving(samples, start - _CONTEXT, start, has_motor, c, track)
        after = _moving(samples, end + 1, end + 1 + _CONTEXT, has_motor, c, track)
        # A lift starts from rest, and the wheel's own speed says so without needing GPS at
        # all. Splicing crashes into real rides showed why this matters: where the context
        # could not establish that the rider had been travelling, a 28 km/h fall was being
        # filed as a free spin. The two boards mean opposite things, so putting a crash on the
        # harmless one is worse than reporting nothing.
        # "From rest" means the wheel was stopped WHEN THIS BEGAN, so it reads the immediate
        # run-up rather than a ten-sample median: a rider who stops, steps off and picks the
        # wheel up has a window straddling both, and the median of riding-then-stopped lands
        # halfway between and answers neither question.
        prior = [x.speed for x in samples[max(0, start - _RUNUP):start] if x.speed is not None]
        from_rest = not prior or (_median(prior) < _MOVING_KMH)

        # The asymmetry is deliberate: a fall must be positively confirmed by a stop, while a
        # lift may end in unknown territory, which is common at the end of a log.
        # A fall ends the ride. That is the whole difference between the two boards: a wheel
        # spun up on a stand is put back down and life goes on, while a rider on the ground
        # stays there. Erwin's tell for a log with no current - the speed does something
        # violent and then everything simply stops.
        ended = _at_rest(samples, end + 1, end + 1 + _CONTEXT, c)
        if trace is not None:
            trace.append({"start": start, "end": end, "n": len(ev), "span": _span(ev),
                          "peak": peak, "load": load, "unloaded": unloaded,
                          "impossible_speed": impossible_speed, "current_agrees": current_agrees,
                          "before": before, "after": after, "worked": surrounding_worked,
                          "from_rest": from_rest, "ended": ended, "runaway": runaway,
                          "at_end": end >= len(samples) - _CONTEXT})
        long_enough = len(ev) >= c["min_fall_len"] and _span(ev) >= c["min_fall_s"]
        if not surrounding_worked:
            out["glitch"] += 1
        elif unloaded and before is True and (after is False or ended) and long_enough:
            out["fall"] += 1
        elif unloaded and from_rest and len(ev) >= c["min_event_len"]                 and _span(ev) >= c["min_event_s"]:
            # No condition on what happens afterwards. The commonest free spin there is goes
            # stop, pick the wheel up, spin it, set it down, ride on - and requiring the rider
            # NOT to be moving after it ruled out exactly that. Falls are tested first, so a
            # crash cannot fall through to here.
            # Free spins are judged from rest by the WHEEL's own speed, not by GPS context.
            # Riding tight circles in a car park moves the rider nowhere, so the ground reads
            # stopped quite honestly - what kept trip 97a15220 off this board is that its
            # wheel had been turning at 12 km/h all along, and a wheel somebody just picked up
            # had not.
            out["spin"] += 1
        else:
            out["spike"] += 1
    return out
