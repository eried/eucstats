"""Free spins, lifts and falls (ingest/anomalies.py).

All three are the same physical event in telemetry - the wheel turning with nothing loading
it - and what separates them is the context either side. So the detector hunts the unloaded
spin and then reads that context:

    unloaded spin while travelling   -> FALL   (a cutout lands here)
    unloaded spin from a standstill  -> LIFT   (pickup test, hand spin)

Current is the discriminator: free spinning takes under an amp, a rider takes 5-30. Only
when a log has no current at all does acceleration stand in for it, and current wins
whenever both exist - otherwise a hard launch on a fast wheel reads as a free spin.

Per the reference spec (eucviewer detectAnomalies, validated on 227 trips): of 495 raw
wheel-vs-GPS candidates only 8% were genuinely unloaded, the rest were riding through a
~2 s GPS dropout. That is why a wheel-versus-GPS rule is never trusted on its own here.
"""
from datetime import datetime, timedelta, timezone

from ingest.anomalies import detect
from ingest.parser import Sample

BASE = datetime(2026, 6, 1, tzinfo=timezone.utc)


def s(i, wheel, gps=None, current=None, power=None, hz=1.0):
    return Sample(t=BASE + timedelta(seconds=i / hz), speed=wheel, gps_speed=gps,
                  current=current, power=power)


def ride(n, wheel=25.0, gps=25.0, current=8.0, start=0):
    """Ordinary riding: wheel and ground agree, motor pulling real current."""
    return [s(start + i, wheel, gps, current) for i in range(n)]


def test_a_fall_is_travelling_then_an_unloaded_spin_then_stopped():
    """Cutouts land here: control lost while moving, wheel spins free, rider stops."""
    log = (ride(14)                                                     # riding
           + [s(14 + i, 45.0, 0.0, 0.2) for i in range(6)]              # free spin, no load
           + [s(20 + i, 0.0, 0.0, 0.1) for i in range(14)])             # stopped
    r = detect(log)
    assert r["fall"] == 1, r
    assert r["lift"] == 0


def test_a_lift_is_from_rest_and_back_to_rest():
    """A real pickup test happens mid-ride: ride, stop, lift and spin it, set it down, ride
    on. The riding either side is what proves the motor was alive; a log where nothing ever
    draws current is a dead board, and the spec calls that a glitch rather than a lift."""
    log = (ride(10)                                                     # rode here
           + [s(10 + i, 0.0, 0.0, 0.5) for i in range(6)]               # stopped
           + [s(16 + i, 40.0, 0.0, 0.2) for i in range(6)]              # lifted and spun
           + [s(22 + i, 0.0, 0.0, 0.5) for i in range(6)]               # set down
           + ride(10, start=28))                                        # rode off
    r = detect(log)
    assert r["lift"] == 1, r
    assert r["fall"] == 0


def test_riding_through_a_gps_dropout_is_not_a_fall():
    """92% of raw candidates are this: the ground signal vanishes, the motor keeps pulling."""
    log = ride(14) + [s(14 + i, 30.0, 0.0, 9.0) for i in range(4)] + ride(14, start=18)
    r = detect(log)
    assert r["fall"] == 0, "a GPS dropout was read as a fall"
    assert r["lift"] == 0


def test_a_hard_launch_with_current_is_not_a_free_spin():
    """Violent acceleration but heavy amps: a racer, not an unloaded wheel."""
    log = ([s(i, 0.0, 0.0, 0.2) for i in range(10)]
           + [s(10 + i, 12.0 * (i + 1), 12.0 * (i + 1), 28.0) for i in range(5)]
           + ride(12, wheel=60.0, gps=60.0, start=15))
    r = detect(log)
    assert r["fall"] == 0 and r["lift"] == 0, r


def test_a_log_with_no_motor_column_never_reports_a_dead_motor():
    """The bug that turned 2521 events into 2521 glitches: absent data is not evidence.

    With no current column the acceleration proxy carries the whole decision, so the spin-up
    has to ramp across samples for the event to be long enough to judge.
    """
    log = ([s(i, 0.0, 0.0) for i in range(12)]
           + [s(12, 25.0, 0.0), s(13, 50.0, 0.0)]                       # 6.9 m/s^2, twice
           + [s(14 + i, 50.0, 0.0) for i in range(4)]
           + [s(18 + i, 0.0, 0.0) for i in range(12)])
    r = detect(log)
    assert r["glitch"] == 0, "a missing column was read as an idle motor"
    assert r["lift"] == 1, "the acceleration proxy should still find the lift"


def test_walking_a_wheel_is_not_a_lift():
    """The 12 km/h floor: below it, wheeling it around the garage counts as a lift."""
    log = ([s(i, 0.0, 0.0, 0.1) for i in range(10)]
           + [s(10 + i, 7.0, 0.0, 0.3) for i in range(8)]
           + [s(18 + i, 0.0, 0.0, 0.1) for i in range(10)])
    assert detect(log)["lift"] == 0


def test_a_logging_gap_cannot_manufacture_an_event():
    """No dt guard and a gap becomes infinite acceleration."""
    log = ride(10) + [s(400, 60.0, 0.0, 0.2)] + ride(10, start=401)
    r = detect(log)
    assert r["fall"] == 0 and r["lift"] == 0, r


def test_thresholds_are_tunable():
    """The free-spin floor is the strongest lever on false positives: at 5 km/h a wheel being
    walked along produces the same wheel-turning-ground-still signature as a real spin."""
    log = ([s(i, 8.0, 8.0, 6.0) for i in range(8)]                      # walking it along
           + [s(8 + i, 8.0, 0.0, 0.3) for i in range(8)]                # ground signal drops
           + [s(16 + i, 0.0, 0.0, 0.2) for i in range(8)])
    assert sum(detect(log).values()) == 0, "8 km/h is below the 12 km/h floor"
    assert sum(detect(log, {"free_spin_kmh": 5.0}).values()) >= 1, "lowering it should catch it"


def test_an_empty_or_tiny_log_is_safe():
    assert detect([]) == {"fall": 0, "lift": 0, "spike": 0, "glitch": 0}
    assert detect([s(0, 10.0)])["fall"] == 0
