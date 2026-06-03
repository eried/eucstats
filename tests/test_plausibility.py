from datetime import datetime, timezone, timedelta

from ingest.parser import Sample
from ingest.summary import summarize
from ingest.plausibility import check

BASE = datetime(2026, 6, 1, tzinfo=timezone.utc)


def mk(sec, **kw):
    return Sample(t=BASE + timedelta(seconds=sec), **kw)


def clean_samples():
    return [mk(i * 100, odo=100.0 + i * 0.4, lat=69.0 + i * 1e-4, lon=18.0 + i * 1e-4,
               speed=20.0, g=0.5) for i in range(5)]


def test_clean_trip_validated():
    s = clean_samples()
    status, reasons = check(s, summarize(s), is_mock=False)
    assert status == "validated"
    assert reasons == []


def test_impossible_speed_flagged():
    s = clean_samples()
    s[2].speed = 400.0
    status, reasons = check(s, summarize(s))
    assert status == "flagged"
    assert "impossible_speed" in reasons


def test_mock_location_flagged():
    s = clean_samples()
    status, reasons = check(s, summarize(s), is_mock=True)
    assert "mock_location" in reasons and status == "flagged"


def test_teleport_flagged_when_many_jumps():
    s = clean_samples()
    s[2] = mk(101, lat=80.0, lon=18.0, speed=20.0, odo=100.8)  # huge jump in 1s (2 jumps)
    # one isolated spike is tolerated by default; flag when jumps exceed the cap
    assert "teleport" not in check(s, summarize(s))[1]
    assert "teleport" in check(s, summarize(s), teleport_max_jumps=1)[1]
