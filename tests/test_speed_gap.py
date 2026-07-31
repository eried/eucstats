"""A logging gap must not dissolve the acceleration limiter.

`_speeds` rejects spikes by capping how fast the believable speed may RISE
(max_accel km/h per second). The allowance was `max_accel * dt` with dt unbounded,
so a pause in the log handed a spike a free pass: across a 60 s gap the cap allowed
a 1200 km/h rise in a single step. One garbage sample after a pause then became the
trip's "realistic" top speed — flagging an honest ride as impossible_speed and
putting a phantom number on the top-speed board.
"""
from datetime import datetime, timedelta, timezone

from ingest.parser import Sample
from ingest.plausibility import check
from ingest.summary import summarize

BASE = datetime(2026, 6, 1, tzinfo=timezone.utc)


def mk(sec, **kw):
    return Sample(t=BASE + timedelta(seconds=sec), **kw)


def ride_then_gap_then(spike, gap_s=60, n=30, cruise=40.0):
    """A steady ride, a logging gap, then one sample reading `spike` km/h."""
    s = [mk(i, speed=cruise, gps_speed=cruise, lat=55.0 + i * 1e-5, lon=12.0,
            odo=100.0 + i * 0.011) for i in range(n)]
    s.append(mk(n + gap_s, speed=spike, gps_speed=spike, lat=55.0 + n * 1e-5, lon=12.0,
                odo=100.0 + n * 0.011))
    return s


def test_spike_after_a_gap_is_not_realistic_speed():
    sm = summarize(ride_then_gap_then(400.0))
    assert sm.max_speed < 120.0, "a garbage sample after a pause became the top speed"


def test_spike_after_a_gap_is_recorded_as_freespin():
    """Rejected from speed, but still surfaced in its own category — not silently dropped."""
    assert summarize(ride_then_gap_then(400.0)).max_freespin == 400.0


def test_honest_ride_with_a_pause_is_not_flagged_too_fast():
    s = ride_then_gap_then(400.0)
    assert "impossible_speed" not in check(s, summarize(s))[1]


def test_longer_gaps_do_not_buy_more_allowance():
    """The whole bug: allowance grew without bound with the length of the pause."""
    peaks = [summarize(ride_then_gap_then(400.0, gap_s=g)).max_speed
             for g in (5, 20, 60, 300)]
    assert max(peaks) < 120.0
    assert max(peaks) - min(peaks) < 1e-6, "gap length still changes the verdict"


def test_resuming_after_a_gap_at_a_believable_speed_still_counts():
    """Don't over-correct: a real ride that resumes faster than it paused must be kept."""
    s = [mk(i, speed=20.0, gps_speed=20.0, odo=100.0 + i * 0.005) for i in range(10)]
    s += [mk(40 + i, speed=70.0, gps_speed=70.0, odo=100.05 + i * 0.019) for i in range(10)]
    assert summarize(s).max_speed >= 70.0


def test_a_genuinely_fast_run_is_still_kept():
    """Reached through believable acceleration, so it is a real top speed."""
    s = [mk(i, speed=min(5.0 * i, 110.0), gps_speed=min(5.0 * i, 110.0),
            odo=100.0 + i * 0.02) for i in range(40)]
    assert summarize(s).max_speed >= 109.0


def test_fabricated_sustained_speed_is_still_flagged():
    """The check must keep catching what it is for."""
    s = [mk(i, speed=min(15.0 * i, 300.0), gps_speed=min(15.0 * i, 300.0),
            lat=55.0 + i * 3e-3, lon=12.0, odo=100.0 + i * 0.08) for i in range(60)]
    assert "impossible_speed" in check(s, summarize(s))[1]
