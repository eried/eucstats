"""Rate limiting: the sliding-window limiter + the API 429s for uploads and signups."""
import gzip
import json

from fastapi.testclient import TestClient

from main import app
from services import ratelimit, settings


def test_sliding_window_allows_then_blocks():
    ratelimit.clear()
    assert [ratelimit.hit("k", 3) for _ in range(5)] == [True, True, True, False, False]
    # a different key is independent
    assert ratelimit.hit("other", 3) is True
    # limit <= 0 disables
    assert all(ratelimit.hit("z", 0) for _ in range(10))


def test_rate_limits_roundtrip(db):
    r = settings.get_rate_limits(db)
    assert r["rider_create_per_ip"] == 20 and r["trip_per_rider"] == 60 and r["trip_per_ip"] == 200
    settings.set_rate_limits(db, {"rider_create_per_ip": "2", "trip_per_rider": "3"})
    r = settings.get_rate_limits(db)
    assert r["rider_create_per_ip"] == 2 and r["trip_per_rider"] == 3


def test_rider_create_rate_limited_per_ip(db):
    settings.set_rate_limits(db, {"rider_create_per_ip": "2"})
    with TestClient(app) as client:
        ratelimit.clear()
        ok1 = client.post("/api/v1/riders", json={"store_id": "a", "display_name": "Alpha"})
        ok2 = client.post("/api/v1/riders", json={"store_id": "b", "display_name": "Bravo"})
        blocked = client.post("/api/v1/riders", json={"store_id": "c", "display_name": "Charlie"})
        assert ok1.status_code == 200 and ok2.status_code == 200
        assert blocked.status_code == 429 and "rider_create" in blocked.text
        # re-registering an EXISTING rider is idempotent and never rate-limited
        again = client.post("/api/v1/riders", json={"store_id": "a", "display_name": "A2"})
        assert again.status_code == 200


_CSV = (b"Date,Speed,Voltage,Temperature,Battery level,Altitude,Latitude,Longitude,Total mileage,GPS speed,Current,PWM,G-Force,G-Force X,G-Force Y\n"
        b"01.06.2026 20:24:31.204,5,132,26,100,68,69.6500,18.9500,1000.0,5,1,10,0.5,0,0\n"
        b"01.06.2026 20:24:41.204,20,132,26,100,68,69.6510,18.9510,1000.1,20,5,40,1.0,0,0\n")


def test_trip_upload_rate_limited_per_rider(db):
    settings.set_rate_limits(db, {"trip_per_rider": "2", "trip_per_ip": "0"})  # ip limit off
    with TestClient(app) as client:
        ratelimit.clear()
        client.post("/api/v1/riders", json={"store_id": "u", "display_name": "Uniglide", "flag": "NO"})
        meta = {"store_id": "u", "platform": "google_play", "source_app": "eucplanet",
                "schema_version": "eucplanet-v3-gforce", "tz": "Europe/Oslo"}
        codes = []
        for i in range(3):
            files = {"trip": ("t.gz", gzip.compress(_CSV), "application/gzip")}
            r = client.post("/api/v1/trips", data={"meta": json.dumps({**meta, "trip_uuid": f"t{i}"})}, files=files)
            codes.append(r.status_code)
        assert codes[2] == 429        # third upload in the window is blocked
        assert codes[0] in (201, 200) and codes[1] in (201, 200)
