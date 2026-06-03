import json

import pyotp
from fastapi.testclient import TestClient

import config
from main import app


def test_totp_enroll_login_and_status(db):
    if config.ADMIN_STATE_FILE.exists():
        config.ADMIN_STATE_FILE.unlink()
    with TestClient(app) as client:
        # not enrolled -> enroll page, secret created
        r = client.get("/admin")
        assert r.status_code == 200
        secret = json.loads(config.ADMIN_STATE_FILE.read_text())["totp_secret"]

        # verify with a valid code -> authenticated session
        code = pyotp.TOTP(secret).now()
        r = client.post("/admin/verify-totp", data={"code": code})
        assert r.status_code in (200, 303)

        s = client.get("/admin/api/status")
        assert s.status_code == 200
        assert "riders" in s.json() and "flagged" in s.json()


def test_status_requires_auth(db):
    with TestClient(app) as client:
        assert client.get("/admin/api/status").status_code == 401


def test_approve_flagged_trip(db):
    from datetime import datetime
    from repository.riders import RiderRepo
    from repository.trips import TripRepo
    import models

    if config.ADMIN_STATE_FILE.exists():
        config.ADMIN_STATE_FILE.unlink()
    RiderRepo(db).upsert("fr", "google_play", "Flag", "NO")
    TripRepo(db).insert_trip(trip_uuid="fl1", rider_store_id="fr", distance_km=7.0,
                             start_utc=datetime(2026, 6, 1), country="NO",
                             start_lat=69.6, start_lon=18.9, max_speed=20.0,
                             validation_status="flagged")
    with TestClient(app) as client:
        client.get("/admin")
        secret = json.loads(config.ADMIN_STATE_FILE.read_text())["totp_secret"]
        client.post("/admin/verify-totp", data={"code": pyotp.TOTP(secret).now()})
        r = client.post("/admin/trip/fl1/approve")
        assert r.status_code in (200, 303)
    db.expire_all()
    assert db.get(models.Trip, "fl1").validation_status == "validated"
    assert db.get(models.RiderStat, "fr").total_km == 7.0
