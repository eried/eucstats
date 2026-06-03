import gzip
import json

from fastapi.testclient import TestClient

from main import app

# Small but realistic eucplanet-schema trip near Tromsø (moves ~0.2 km).
CSV = (
    "Date,Speed,Voltage,Temperature,Battery level,Altitude,Latitude,Longitude,Total mileage,GPS speed,Current,PWM,G-Force,G-Force X,G-Force Y\n"
    "01.06.2026 20:24:31.204,5,132,26,100,68,69.6500,18.9500,1000.0,5,1,10,0.5,0,0\n"
    "01.06.2026 20:24:41.204,20,132,26,100,68,69.6510,18.9510,1000.1,20,5,40,1.0,0,0\n"
    "01.06.2026 20:24:51.204,25,132,26,100,68,69.6520,18.9520,1000.2,25,6,45,1.2,0,0\n"
).encode()

META = {
    "store_id": "u_test", "platform": "google_play", "trip_uuid": "trip-1",
    "source_app": "eucplanet", "schema_version": "eucplanet-v3-gforce",
    "tz": "Europe/Oslo", "tz_offset_min": 120, "tz_known": True,
    "is_mock_location": False, "wheel": {"serial": "AABBCC", "model": "Master"},
}


def _upload(client, meta):
    files = {"trip": ("trip.csv.gz", gzip.compress(CSV), "application/gzip")}
    return client.post("/api/v1/trips", data={"meta": json.dumps(meta)}, files=files)


def test_register_upload_dedupe_and_profile(db):
    with TestClient(app) as client:
        r = client.post("/api/v1/riders",
                        json={"store_id": "u_test", "display_name": "Tester", "flag": "NO"})
        assert r.status_code == 200, r.text
        assert r.json()["display_name"] == "Tester"

        r = _upload(client, META)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["validation_status"] == "validated"
        assert body["country"] == "NO"
        assert 0.1 < body["distance_km"] < 1.0

        # duplicate trip_uuid -> 200, deduped
        r2 = _upload(client, META)
        assert r2.status_code == 200 and r2.json()["duplicate"] is True

        rp = client.get("/api/v1/riders/u_test")
        assert rp.status_code == 200 and rp.json()["flag"] == "NO"


def test_upload_unregistered_rider_400(db):
    with TestClient(app) as client:
        r = _upload(client, {**META, "store_id": "ghost", "trip_uuid": "t-x"})
        assert r.status_code == 400


def test_upload_bad_meta_400(db):
    with TestClient(app) as client:
        files = {"trip": ("t.gz", gzip.compress(CSV), "application/gzip")}
        r = client.post("/api/v1/trips", data={"meta": "not-json"}, files=files)
        assert r.status_code == 400
