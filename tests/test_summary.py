from datetime import datetime, timezone, timedelta

from ingest.parser import Sample
from ingest.summary import summarize

BASE = datetime(2026, 6, 1, tzinfo=timezone.utc)


def mk(sec, **kw):
    return Sample(t=BASE + timedelta(seconds=sec), **kw)


def test_summary_odometer_distance():
    # 100s apart so 0.5 km steps imply 18 km/h (plausible)
    samples = [
        mk(0, odo=100.0, speed=0.0, g=0.1, voltage=132, current=1),
        mk(100, odo=100.5, speed=20.0, g=0.5, voltage=132, current=5),
        mk(200, odo=101.0, speed=10.0, g=0.2, voltage=132, current=3),
    ]
    s = summarize(samples)
    assert round(s.distance_km, 3) == 1.0      # sum of plausible odometer steps
    assert s.duration_s == 200
    assert s.max_speed == 20.0
    assert s.avg_speed == 10.0                 # mean(0,20,10)
    assert s.max_gforce == 0.5
    assert s.wh_per_km is not None and s.wh_per_km > 0


def test_summary_rejects_odometer_dropout():
    # a lifetime odometer with a single 0-reading dropout must NOT yield ~1653 km
    samples = [
        mk(0, odo=1653.0, speed=10.0),
        mk(100, odo=1653.5, speed=10.0),   # +0.5 km, plausible
        mk(101, odo=0.0, speed=10.0),      # dropout (negative delta -> ignored)
        mk(102, odo=1653.5, speed=10.0),   # +1653.5 in 1s -> teleport -> ignored
        mk(200, odo=1654.0, speed=10.0),   # +0.5 km, plausible
    ]
    s = summarize(samples)
    assert round(s.distance_km, 3) == 1.0      # 0.5 + 0.5, dropout excluded


def test_summary_gps_fallback_when_no_odometer():
    samples = [
        mk(0, lat=69.6545046, lon=18.9190817, speed=0.0),
        mk(10, lat=69.6544136, lon=18.9192160, speed=19.4),
    ]
    s = summarize(samples)
    assert s.distance_km > 0          # falls back to GPS integration
    assert s.gps_distance_km > 0
    assert s.max_gforce is None       # no g column


def test_summary_empty_raises():
    import pytest
    with pytest.raises(ValueError):
        summarize([])
