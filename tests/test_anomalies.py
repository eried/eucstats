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
    assert r["spin"] == 0


def test_a_free_spin_is_from_rest_and_back_to_rest():
    """A real pickup test happens mid-ride: ride, stop, lift and spin it, set it down, ride
    on. The riding either side is what proves the motor was alive; a log where nothing ever
    draws current is a dead board, and the spec calls that a glitch rather than a lift."""
    log = (ride(10)                                                     # rode here
           + [s(10 + i, 0.0, 0.0, 0.5) for i in range(6)]               # stopped
           + [s(16 + i, 40.0, 0.0, 0.2) for i in range(6)]              # lifted and spun
           + [s(22 + i, 0.0, 0.0, 0.5) for i in range(6)]               # set down
           + ride(10, start=28))                                        # rode off
    r = detect(log)
    assert r["spin"] == 1, r
    assert r["fall"] == 0


def test_riding_through_a_gps_dropout_is_not_a_fall():
    """92% of raw candidates are this: the ground signal vanishes, the motor keeps pulling."""
    log = ride(14) + [s(14 + i, 30.0, 0.0, 9.0) for i in range(4)] + ride(14, start=18)
    r = detect(log)
    assert r["fall"] == 0, "a GPS dropout was read as a fall"
    assert r["spin"] == 0


def test_a_hard_launch_with_current_is_not_a_free_spin():
    """Violent acceleration but heavy amps: a racer, not an unloaded wheel."""
    log = ([s(i, 0.0, 0.0, 0.2) for i in range(10)]
           + [s(10 + i, 12.0 * (i + 1), 12.0 * (i + 1), 28.0) for i in range(5)]
           + ride(12, wheel=60.0, gps=60.0, start=15))
    r = detect(log)
    assert r["fall"] == 0 and r["spin"] == 0, r


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
    assert r["spin"] == 1, "the acceleration proxy should still find the lift"


def test_walking_a_wheel_is_not_a_lift():
    """The 12 km/h floor: below it, wheeling it around the garage counts as a lift."""
    log = ([s(i, 0.0, 0.0, 0.1) for i in range(10)]
           + [s(10 + i, 7.0, 0.0, 0.3) for i in range(8)]
           + [s(18 + i, 0.0, 0.0, 0.1) for i in range(10)])
    assert detect(log)["spin"] == 0


def test_a_logging_gap_cannot_manufacture_an_event():
    """No dt guard and a gap becomes infinite acceleration."""
    log = ride(10) + [s(400, 60.0, 0.0, 0.2)] + ride(10, start=401)
    r = detect(log)
    assert r["fall"] == 0 and r["spin"] == 0, r


def test_thresholds_are_tunable():
    """The free-spin floor is the strongest lever on false positives: at 5 km/h a wheel being
    walked along produces the same wheel-turning-ground-still signature as a real spin."""
    log = ([s(i, 8.0, 8.0, 6.0) for i in range(8)]                      # walking it along
           + [s(8 + i, 8.0, 0.0, 0.3) for i in range(8)]                # ground signal drops
           + [s(16 + i, 0.0, 0.0, 0.2) for i in range(8)])
    assert sum(detect(log).values()) == 0, "8 km/h is below the 12 km/h floor"
    assert sum(detect(log, {"free_spin_kmh": 5.0}).values()) >= 1, "lowering it should catch it"


def test_an_empty_or_tiny_log_is_safe():
    assert detect([]) == {"fall": 0, "spin": 0, "spike": 0, "glitch": 0}
    assert detect([s(0, 10.0)])["fall"] == 0


# --- Regression: every one of these is a real trip the first version called a fall. ---
# The detector shipped 14 falls across the 789-trip library and all 14 were wrong. Each of
# these reproduces one, reduced to the samples that mattered.

def sp(i, wheel, gps=None, current=None, lat=None, lon=None, pwm=None):
    return Sample(t=BASE + timedelta(seconds=i), speed=wheel, gps_speed=gps,
                  current=current, lat=lat, lon=lon, pwm=pwm)


def test_rolling_to_a_stop_is_not_a_fall():
    """Erwin, 2026-06-09. Wheel 15 -> 0 over six seconds, GPS correctly reads 0, and a wheel
    doing 13 km/h on the flat draws under an amp. Stopping is not a cutout."""
    amps = [0.9, 1.5, 0.2, 0.5, 0.6, 0.8]        # a real amp or two while still rolling
    log = ([sp(i, 15.0 - i * 0.2, 12.3, amps[i], 69.6738, 18.9219) for i in range(6)]
           + [sp(6 + i, 12.6 - i * 2.1, 0.0, 0.5, 69.6738, 18.9219) for i in range(6)]
           + [sp(12 + i, 0.0, 0.0, 0.4, 69.6738, 18.9219) for i in range(8)])
    assert detect(log)["fall"] == 0


def test_braking_through_zero_current_is_not_an_unloaded_wheel():
    """chainik, 2026-06-17. Regen braking swings the current from -16 A to +7 A, so single
    samples sit near zero on the way past. The wheel is very much loaded."""
    amps = [-8.3, -0.4, -1.0, -16.1, -7.1, -5.8, 0.5, -3.5, -3.2, 7.4, 3.4, 7.4, -13.5]
    log = [sp(i, 16.0, 0.0 if i >= 6 else 8.0, a, 50.4641, 30.4280)
           for i, a in enumerate(amps)]
    assert detect(log)["fall"] == 0


def test_a_gps_dropout_with_no_position_fix_is_not_a_stop():
    """Hardfault, 2026-08-11. GPS speed reads 0.0 but the log carries no latitude or
    longitude for those samples - the receiver lost the fix, the rider did not stop."""
    fixes = [13.1, 14.3, 12.7, 13.5, 14.5, 10.9, 0.0, 0.0, 11.4, 11.4,
             0.0, 12.9, 0.0, 0.0, 11.0, 0.0, 0.0, 12.0, 0.0, 0.0]
    log = [sp(i, 15.0 + (i % 3) * 0.5, g, 0.1 if g == 0.0 else -1.3,
              None if g == 0.0 else 49.8384, None if g == 0.0 else 24.0079)
           for i, g in enumerate(fixes)]
    assert detect(log)["fall"] == 0


def test_a_frozen_telemetry_feed_is_a_glitch_not_a_fall():
    """MackNificent, 2026-08-16. Wheel 12.2, current -0.9 and PWM 5.0 repeat byte for byte
    for minutes: the BLE feed is stuck. A channel that never changes proves nothing, so it
    must never be read as 'the motor was idle'."""
    log = [sp(i, 12.2, 13.0 if i < 6 else 0.0, -0.9, 41.8174, -72.6897, 5.0)
           for i in range(20)]
    r = detect(log)
    assert r["fall"] == 0 and r["spin"] == 0


def test_a_real_fall_survives_all_of_those_guards():
    """The guards above must not cost us the thing we are looking for: riding, then the
    wheel spinning free with the ground still and no current, then a stop."""
    log = ([sp(i, 30.0, 29.0, 9.0, 55.0 + i * 1e-4, 11.0) for i in range(12)]
           + [sp(12 + i, 42.0, 0.0, 0.2, 55.0012, 11.0) for i in range(6)]
           + [sp(18 + i, 0.0, 0.0, 0.1, 55.0012, 11.0) for i in range(12)])
    assert detect(log)["fall"] == 1


# Samples 46-100 of trip a6e9e5c6, verbatim. Reduced stand-ins for these two kept passing
# for reasons the real trip did not share, so the fixtures are the telemetry itself.
STALLED_SPEEDO = [
    (0, 17.8, 20.1, 4.5, 69.65125, 18.95768), (1, 18.5, 17.7, 1.5, 69.65126, 18.95770),
    (2, 18.2, 17.4, 5.6, 69.65128, 18.95771), (3, 21.5, 18.1, 8.2, 69.65133, 18.95776),
    (4, 24.3, 10.7, 8.1, 69.65130, 18.95774), (5, 24.3, 10.8, 2.3, 69.65131, 18.95775),
    (6, 23.1, 12.0, 2.4, 69.65134, 18.95778), (7, 22.7, 19.0, 4.9, 69.65145, 18.95784),
    (8, 24.2, 21.0, 3.4, 69.65155, 18.95793), (9, 24.5, 24.0, -0.3, 69.65163, 18.95802),
    (10, 24.3, 25.3, 1.8, 69.65170, 18.95809), (11, 22.7, 24.3, -0.1, 69.65175, 18.95816),
    (12, 20.9, 24.8, 0.3, 69.65180, 18.95818), (13, 20.5, 24.7, 1.5, 69.65185, 18.95821),
    (14, 19.6, 23.2, 0.6, 69.65189, 18.95826), (15, 18.5, 24.9, -0.7, 69.65194, 18.95834),
    (16, 18.1, 26.2, -0.0, 69.65197, 18.95842), (17, 17.1, 22.1, 0.2, 69.65201, 18.95847),
    (19, 14.8, 17.1, -0.1, 69.65207, 18.95852), (20, 13.9, 15.1, 0.2, 69.65210, 18.95855),
    (21, 12.8, 14.1, 1.1, 69.65213, 18.95857), (22, 12.3, 12.1, 3.2, 69.65215, 18.95861),
    (23, 12.5, 12.3, 2.1, 69.65218, 18.95864), (23, 12.5, 12.4, 2.2, 69.65220, 18.95868),
    (25, 12.4, 7.2, 0.5, 69.65221, 18.95870), (26, 12.5, 0.6, 1.1, 69.65222, 18.95872),
    (27, 12.9, 0.5, 1.5, 69.65223, 18.95872), (28, 13.5, 0.5, 0.2, 69.65225, 18.95873),
    (29, 13.5, 0.3, -0.5, 69.65227, 18.95875), (29, 11.6, 0.7, -0.2, 69.65229, 18.95877),
    (31, 9.4, 0.8, 0.1, 69.65229, 18.95878), (32, 7.6, 1.0, -0.2, 69.65230, 18.95880),
    (33, 7.8, 0.9, 1.1, 69.65231, 18.95882), (34, 7.4, 0.7, 0.0, 69.65232, 18.95882),
    (35, 6.3, 0.9, 1.1, 69.65233, 18.95886), (36, 5.7, 0.9, 0.4, 69.65234, 18.95887),
    (37, 5.6, 2.6, -0.6, 69.65238, 18.95894), (38, 5.2, 2.8, 0.0, 69.65240, 18.95897),
    (39, 5.9, 2.0, -0.4, 69.65241, 18.95901), (40, 5.6, 2.0, 0.8, 69.65241, 18.95902),
    (41, 6.1, 2.4, 3.5, 69.65242, 18.95907), (42, 10.5, 3.8, 3.7, 69.65244, 18.95916),
    (43, 11.1, 3.4, 1.4, 69.65246, 18.95925), (44, 11.9, 3.4, 2.2, 69.65246, 18.95926),
    (45, 13.4, 2.6, 1.2, 69.65248, 18.95930), (46, 12.4, 3.3, 4.6, 69.65249, 18.95934),
    (47, 11.9, 4.8, 1.0, 69.65250, 18.95938), (48, 11.8, 6.9, -0.8, 69.65251, 18.95949),
    (49, 10.8, 9.5, -1.2, 69.65254, 18.95964), (50, 13.5, 10.2, 3.2, 69.65255, 18.95969),
    (51, 15.6, 10.0, 4.4, 69.65257, 18.95977), (52, 16.9, 22.3, 2.0, 69.65271, 18.95989),
    (53, 16.3, 19.3, 6.0, 69.65270, 18.96007), (54, 15.6, 11.9, 1.3, 69.65274, 18.96009),
    (55, 15.0, 15.5, -1.5, 69.65275, 18.96018),
]

# Samples 2105-2160 of trip 97a15220, verbatim.
CIRCLING_A_CAR_PARK = [
    (0, 14.4, 1.3, 2.2, 69.65041, 18.95776), (1, 14.4, 1.4, 1.3, 69.65041, 18.95776),
    (2, 14.8, 1.1, 1.1, 69.65041, 18.95776), (3, 14.1, 0.7, -0.4, 69.65041, 18.95776),
    (4, 13.3, 2.1, 0.7, 69.65039, 18.95777), (5, 13.0, 0.6, 0.3, 69.65038, 18.95777),
    (6, 12.8, 2.3, 1.7, 69.65037, 18.95776), (7, 12.8, 0.4, 0.6, 69.65037, 18.95775),
    (8, 12.3, 0.7, 0.6, 69.65037, 18.95774), (9, 10.6, 1.1, 1.3, 69.65037, 18.95774),
    (10, 10.4, 2.0, 2.2, 69.65035, 18.95774), (11, 9.9, 0.9, 1.4, 69.65035, 18.95774),
    (12, 8.7, 0.8, -0.3, 69.65035, 18.95774), (13, 7.9, 0.8, 0.7, 69.65036, 18.95773),
    (14, 8.5, 1.0, 1.8, 69.65035, 18.95773), (15, 9.0, 0.7, 1.1, 69.65035, 18.95773),
    (16, 7.4, 1.5, 0.5, 69.65035, 18.95772), (17, 8.7, 0.5, 7.9, 69.65035, 18.95772),
    (18, 11.7, 0.7, 10.9, 69.65035, 18.95772), (19, 15.9, 1.8, 5.0, 69.65033, 18.95772),
    (20, 14.9, 0.6, 4.0, 69.65034, 18.95772), (21, 14.2, 0.6, 1.6, 69.65035, 18.95772),
    (22, 11.8, 0.5, -0.2, 69.65034, 18.95772), (23, 9.8, 0.6, 2.5, 69.65034, 18.95773),
    (24, 10.9, 0.5, 6.2, 69.65033, 18.95773), (25, 13.2, 0.5, 0.4, 69.65033, 18.95774),
    (26, 12.2, 0.3, -0.7, 69.65033, 18.95775), (27, 10.4, 0.7, 0.8, 69.65032, 18.95775),
    (28, 11.3, 0.4, 2.1, 69.65032, 18.95775), (29, 11.1, 0.6, -0.6, 69.65032, 18.95775),
    (30, 9.6, 0.6, 0.9, 69.65032, 18.95774), (31, 8.7, 0.4, -0.3, 69.65031, 18.95774),
    (32, 7.5, 0.4, 2.5, 69.65031, 18.95774), (33, 9.5, 0.4, 2.5, 69.65031, 18.95775),
    (34, 9.0, 0.4, -0.5, 69.65031, 18.95777), (35, 8.6, 0.1, -0.1, 69.65031, 18.95777),
    (36, 7.8, 0.3, -0.4, 69.65031, 18.95778), (37, 7.5, 0.3, -0.4, 69.65031, 18.95779),
    (38, 7.8, 0.4, -0.9, 69.65031, 18.95779), (39, 7.2, 0.3, -0.4, 69.65031, 18.95779),
    (40, 7.5, 0.5, 1.2, 69.65020, 18.95785), (41, 7.7, 0.5, 1.4, 69.65010, 18.95790),
    (42, 8.1, 0.6, 1.4, 69.65000, 18.95801), (43, 8.5, 0.5, 0.9, 69.64995, 18.95808),
    (44, 8.6, 0.4, 2.3, 69.64991, 18.95810), (45, 9.9, 0.6, 1.8, 69.64991, 18.95810),
    (46, 9.4, 0.4, -0.3, 69.64991, 18.95810), (47, 8.2, 0.2, 1.9, 69.64991, 18.95810),
    (48, 8.8, 0.6, 2.8, 69.64990, 18.95810), (49, 8.3, 0.5, 1.1, 69.64990, 18.95809),
    (50, 7.4, 0.5, 1.5, 69.64990, 18.95810), (51, 8.1, 0.4, 1.5, 69.64990, 18.95809),
    (52, 8.6, 0.4, 2.4, 69.64990, 18.95809), (53, 8.8, 0.4, 1.4, 69.64991, 18.95808),
    (54, 9.1, 0.3, 2.0, 69.64995, 18.95802), (55, 9.2, 0.4, 0.9, 69.64999, 18.95797),
]


def replay(rows):
    return [Sample(t=BASE + timedelta(seconds=dt), speed=w, gps_speed=g,
                   current=a, lat=lat, lon=lon) for dt, w, g, a, lat, lon in rows]


def test_a_stalled_gps_speedometer_with_a_moving_track_is_not_a_fall():
    """Erwin, 2026-08-19. GPS speed sticks near zero for four seconds while latitude keeps
    climbing - the rider covered eleven metres during the reported standstill. The track is
    the honest channel: a speedometer can stall, a position that advances cannot."""
    assert detect(replay(STALLED_SPEEDO))["fall"] == 0


def test_a_two_second_current_dip_while_circling_on_the_spot_is_not_a_free_spin():
    """Erwin, 2026-06-28. Riding tight circles in a car park: the wheel does 8-16 km/h while
    the rider goes nowhere, so the surroundings honestly read as stopped. What says a rider
    is aboard is the load - 6.2 A one sample, 0.8 A two later - and the wheel is never
    unloaded for longer than a control cycle."""
    assert detect(replay(CIRCLING_A_CAR_PARK))["spin"] == 0


def test_a_wheel_actually_picked_up_is_still_a_free_spin():
    """The guard must not cost us the real thing: stationary, then spinning free with no load
    for a good few seconds, then set back down."""
    log = ([sp(i, 22.0, 21.0, 7.0, 55.0 + i * 5e-5, 11.0) for i in range(10)]   # rode there
           + [sp(10 + i, 0.0, 0.0, 0.1, 55.0005, 11.0) for i in range(8)]        # stopped
           + [sp(18 + i, 20.0, 0.0, 0.2, 55.0005, 11.0) for i in range(8)]       # picked up
           + [sp(26 + i, 0.0, 0.0, 0.1, 55.0005, 11.0) for i in range(6)]        # set down
           + [sp(32 + i, 22.0, 21.0, 7.0, 55.0005 + i * 5e-5, 11.0) for i in range(10)])
    assert detect(log)["spin"] == 1


def test_the_stop_does_not_bleed_backwards_into_the_before_window():
    """Ground speed is smoothed over a couple of samples either side, which means the samples
    just before a fall already carry some of the standstill that follows. Read the context
    right up against the event and a real fall reports 'was not travelling before' and is
    dismissed - it cost 14% of spliced falls. So the context windows stand clear of it."""
    log = ([sp(i, 32.0, 31.0, 9.0, 55.0 + i * 8e-5, 11.0) for i in range(30)]   # riding
           + [sp(30 + i, 44.0, 0.0, 0.2, 55.00232, 11.0) for i in range(6)]     # wheel runs free
           + [sp(36 + i, 0.0, 0.0, 0.1, 55.00232, 11.0) for i in range(20)])    # rider is down
    assert detect(log)["fall"] == 1


def test_a_fall_is_never_filed_as_a_free_spin():
    """The two boards mean opposite things, so confusing them is worse than missing the event:
    a crash would be published as somebody messing about. What tells them apart without
    needing GPS at all is the wheel's own speed - a lift starts from rest, a fall does not."""
    log = ([sp(i, 30.0, 29.0, 8.0, 55.0 + i * 7e-5, 11.0) for i in range(20)]
           + [sp(20 + i, 42.0, 0.0, 0.2, 55.00133, 11.0) for i in range(6)]
           + [sp(26 + i, 0.0, 0.0, 0.1, 55.00133, 11.0) for i in range(16)])
    assert detect(log)["spin"] == 0


def test_coarse_gps_that_repeats_a_fix_is_not_a_standstill():
    """Plenty of phones write the same position for several samples and then jump. Read the
    track over two samples and a rider doing 40 km/h looks parked half the time, which threw
    away one spliced fall in seven - and filed it under free spins."""
    rows = []
    for i in range(40):
        lat = 55.0 + (i // 5) * 5e-4              # position updates once every five samples
        rows.append(sp(i, 40.0, 39.0, 9.0, lat, 11.0))
    rows += [sp(40 + i, 55.0, 0.0, 0.2, 55.0035, 11.0) for i in range(6)]
    rows += [sp(46 + i, 0.0, 0.0, 0.1, 55.0035, 11.0) for i in range(16)]
    assert detect(rows)["fall"] == 1


# --- Free spins, defined by SPEED alone. ---------------------------------------------------
# A free spin is a wheel accelerating to a speed no loaded wheel could have reached: lift one
# off the ground and the balance algorithm spins it up in a second or two. That is a speed
# signature, so it needs no current channel - which matters, because 122 of the 789 trips in
# the library have no current channel at all and could never be judged on motor load.

def spin_only(log):
    return detect(log)["spin"]


def test_a_wheel_spun_up_off_the_ground_is_a_free_spin():
    """0 to 45 km/h in two seconds, GPS flat on the floor. No wheel does that with a rider."""
    log = ([sp(i, 0.0, 0.0, None, 55.0, 11.0) for i in range(10)]
           + [sp(10 + i, 22.0 * (i + 1), 0.0, None, 55.0, 11.0) for i in range(2)]
           + [sp(12 + i, 45.0, 0.0, None, 55.0, 11.0) for i in range(8)]
           + [sp(20 + i, 0.0, 0.0, None, 55.0, 11.0) for i in range(8)])
    assert spin_only(log) == 1


def test_a_hard_but_real_launch_is_not_a_free_spin():
    """0 to 40 km/h in five seconds with the ground agreeing the whole way. That is just a
    quick rider on a quick wheel, and it must never land on a board about wheels in the air."""
    log = [sp(i, 8.0 * i, 8.0 * i, 12.0, 55.0 + i * 2e-4, 11.0) for i in range(6)]
    log += [sp(6 + i, 40.0, 40.0, 9.0, 55.0012 + i * 3e-4, 11.0) for i in range(14)]
    assert spin_only(log) == 0


def test_a_gps_dropout_mid_ride_is_not_a_free_spin():
    """The ground speed reading vanishing does not mean the ground stopped. This is the
    commonest event in the library by far - 178 of them - so getting it wrong would bury the
    board in noise."""
    log = []
    for i in range(30):
        lost = 12 <= i <= 18
        log.append(sp(i, 38.0, 0.0 if lost else 37.0, 10.0,
                      None if lost else 55.0 + i * 3e-4, None if lost else 11.0))
    assert spin_only(log) == 0


def test_a_falls_runaway_spin_is_not_also_counted_as_a_free_spin():
    """A cutout spins the wheel up too, but there is nothing harmless about it. It belongs on
    the fall board and only there."""
    log = ([sp(i, 30.0, 29.0, 8.0, 55.0 + i * 7e-5, 11.0) for i in range(20)]
           + [sp(20 + i, 60.0, 0.0, 0.2, 55.00133, 11.0) for i in range(6)]
           + [sp(26 + i, 0.0, 0.0, 0.1, 55.00133, 11.0) for i in range(16)])
    r = detect(log)
    assert (r["fall"], r["spin"]) == (1, 0)
