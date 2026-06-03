from datetime import datetime, timezone, timedelta

from ingest.parser import Sample
from ingest.downsample import downsample, encode_track, decode_track

BASE = datetime(2026, 6, 1, tzinfo=timezone.utc)


def mk(i, lat, lon, speed=None, g=None):
    return Sample(t=BASE + timedelta(seconds=i), lat=lat, lon=lon, speed=speed, g=g)


def test_downsample_preserves_global_extremes():
    samples = [mk(i, 69.0 + i * 1e-5, 18.0 + i * 1e-5, speed=float(i % 30), g=float(i % 5))
               for i in range(5000)]
    samples[1234].speed = 999.0   # unique global max speed
    samples[4321].g = 9.99        # unique global max g
    out = downsample(samples, max_points=500)
    assert len(out) <= 503
    assert any(s.speed == 999.0 for s in out)
    assert any(s.g == 9.99 for s in out)


def test_small_track_passthrough():
    samples = [mk(i, 69.0, 18.0, speed=1.0) for i in range(10)]
    assert len(downsample(samples, max_points=500)) == 10


def test_encode_decode_roundtrip():
    samples = [mk(0, 69.0, 18.0, speed=1.0, g=0.1), mk(1, 69.1, 18.1, speed=2.0, g=0.2)]
    arr = decode_track(encode_track(samples))
    assert len(arr) == 2
    assert arr[0][1] == 69.0 and arr[1][3] == 2.0


def test_no_coords_returns_empty():
    samples = [Sample(t=BASE, speed=1.0)]
    assert downsample(samples) == []
