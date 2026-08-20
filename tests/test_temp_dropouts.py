"""Board temperature: a majority of dropouts must not evict the real readings.

The de-spiker anchors on the median reading and keeps what sits within a thermally
plausible step of it. That is robust "while dropouts are a minority", as its own comment
says. On real logs they are not: 79-81% of the moving samples on some rides are exactly
0.0, the firmware's "no reading" placeholder. The median is then 0.0, so the filter keeps
all the zeros and rejects every genuine 50-58 C reading, and the ride is published as
min 0.0 / max 0.0 - a 67 km ride with the board apparently at freezing point the whole way.

A genuine cold ride must still register, so 0 cannot simply be banned (that was tried and
deliberately reverted). The distinction is that a real freezing ride drifts around zero,
while a dead channel sits at exactly 0.0 while the board is plainly hot.
"""
from datetime import datetime, timedelta, timezone

from ingest.parser import Sample
from ingest.summary import _valid_temp_points, summarize

BASE = datetime(2026, 6, 1, tzinfo=timezone.utc)


def mk(sec, **kw):
    return Sample(t=BASE + timedelta(seconds=sec), **kw)


def ride(temps, **kw):
    """A moving ride carrying the given temperature series."""
    return [mk(i, temp=v, speed=22.0, gps_speed=21.0, odo=100.0 + i * 0.006, **kw)
            for i, v in enumerate(temps)]


def test_dropouts_in_the_majority_do_not_evict_real_readings():
    # 80% zeros, 20% real: the shape seen on the live logs.
    temps = [0.0 if i % 5 else 52.0 + (i % 7) * 0.5 for i in range(400)]
    kept = [v for _, v in _valid_temp_points(ride(temps))]
    assert kept, "everything was rejected"
    assert min(kept) > 40.0, f"zeros survived as real readings: min {min(kept)}"


def test_the_published_extremes_come_from_the_real_readings():
    temps = [0.0 if i % 5 else 52.0 + (i % 7) * 0.5 for i in range(400)]
    sm = summarize(ride(temps))
    assert sm.min_temp > 40.0, f"min_temp still a dropout: {sm.min_temp}"
    assert 50.0 < sm.max_temp < 60.0


def test_a_genuinely_cold_ride_still_registers():
    """Riding at freezing drifts around zero; that is a reading, not a dropout."""
    temps = [(-2.0, -1.0, 0.0, 0.5, 1.0, 0.0, -0.5)[i % 7] for i in range(200)]
    sm = summarize(ride(temps))
    assert sm.min_temp is not None and sm.min_temp <= 0.0
    assert sm.max_temp <= 2.0


def test_an_all_zero_channel_reports_nothing():
    """Never a real reading: the wheel has no temperature sensor wired, it is not freezing.

    Publishing 0 C would put a wheel that never reported a temperature at the top of the
    coldest-ride board. A genuinely freezing ride has sensor resolution and drift, so it is
    never exactly 0.0 for every single sample.
    """
    assert _valid_temp_points(ride([0.0] * 100)) == []
    sm = summarize(ride([0.0] * 100))
    assert sm.min_temp is None and sm.max_temp is None


def test_a_normal_warm_ride_is_unaffected():
    temps = [40.0 + i * 0.02 for i in range(300)]
    sm = summarize(ride(temps))
    assert 39.0 < sm.min_temp < 41.0
    assert 45.0 < sm.max_temp < 47.0
