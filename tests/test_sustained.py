"""A "sustained" value must be backed by evidence that it lasted.

`_sustained_max` slides a time window over the points a metric produced and takes the best
average. It never required the window to actually COVER that time, so a lone point was its
own window and its instantaneous value was reported as sustained.

That is not a corner case. The movement gate drops parked/slow samples, so the surviving
series is full of holes: on a real 1 Hz ride a single pothole spike sat alone after a 9.1 s
hole and became a 3.15 g "2-second sustained" record on an otherwise smooth ride whose g
readings had a median of 0.08.

Fourteen metrics are built on this helper (g-force, sustained watts/amps, PWM, speed holds,
directional g), so a lone sample could mint a record on any of them.
"""
from datetime import datetime, timedelta, timezone

from ingest.parser import Sample
from ingest.summary import _sustained_max, summarize

BASE = datetime(2026, 6, 1, tzinfo=timezone.utc)


def mk(sec, **kw):
    return Sample(t=BASE + timedelta(seconds=sec), **kw)


def g_of(s):
    return abs(s.g) if s.g is not None else None


def test_a_lone_sample_is_not_sustained():
    """The reported bug, reduced: one spike, isolated by gaps, is not a 2 second hold."""
    s = [mk(0, g=0.1), mk(1, g=0.1), mk(20, g=3.2), mk(40, g=0.1), mk(41, g=0.1)]
    assert (_sustained_max(s, g_of, 2.0) or 0) < 1.0


def test_a_window_may_not_bridge_a_gap():
    """Two points 30 s apart are not evidence of a 2 second hold, however close in value."""
    s = [mk(0, g=3.0), mk(30, g=3.0)]
    assert (_sustained_max(s, g_of, 2.0) or 0) < 3.0


def test_a_genuine_hold_is_reported():
    s = [mk(i, g=1.4) for i in range(10)]
    assert _sustained_max(s, g_of, 2.0) == 1.4


def test_a_spike_inside_a_real_window_is_averaged_down():
    """One bad reading among good ones must dilute, not dominate."""
    s = [mk(i, g=0.1) for i in range(10)]
    s[5] = mk(5, g=3.0)
    assert (_sustained_max(s, g_of, 2.0) or 0) < 1.5


def test_sparse_but_continuous_data_still_counts():
    """A 1 Hz log is normal; a 2 s window there is 3 points and must not be discarded."""
    s = [mk(i, g=1.2) for i in range(10)]
    assert _sustained_max(s, g_of, 2.0) == 1.2


def test_smooth_ride_with_one_pothole():
    """End to end, in the shape of the real trip: 1 Hz, smooth, one isolated spike."""
    s = []
    for i in range(600):
        g = 3.153 if i == 300 else 0.08
        # movement gate keeps these: real riding speed throughout
        s.append(mk(i, g=g, speed=22.0, gps_speed=21.0, odo=100.0 + i * 0.006))
    sm = summarize(s)
    assert sm.max_gforce < 1.5, f"a pothole still became sustained g: {sm.max_gforce}"
    assert sm.max_gforce_spike == 3.153, "the spike itself must still be recorded"
