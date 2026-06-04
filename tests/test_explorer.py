"""Admin explorer pages render, and ban/unban work end-to-end through the UI."""
import json
from datetime import datetime

import pyotp
from fastapi.testclient import TestClient

import config
import models
from main import app
from repository.riders import RiderRepo
from repository.trips import TripRepo
from services import settings, stats


def _seed(db):
    RiderRepo(db).upsert("ex1", "google_play", "Explorer Ed", "NO")
    db.add(models.Wheel(wheel_id="w1", rider_store_id="ex1", brand="Begode", model="Master"))
    TripRepo(db).insert_trip(trip_uuid="tr1", rider_store_id="ex1", distance_km=12.0,
                             start_utc=datetime(2026, 6, 1), country="NO",
                             start_lat=69.6, start_lon=18.9, max_speed=45.0,
                             validation_status="validated",
                             meta_json={"max_freespin": 88.0})
    db.commit()


def _auth(client):
    if config.ADMIN_STATE_FILE.exists():
        config.ADMIN_STATE_FILE.unlink()
    client.get("/admin")
    secret = json.loads(config.ADMIN_STATE_FILE.read_text())["totp_secret"]
    client.post("/admin/verify-totp", data={"code": pyotp.TOTP(secret).now()})


def test_explorer_pages_render(db):
    _seed(db)
    with TestClient(app) as client:
        _auth(client)
        r = client.get("/admin/explorer")
        assert r.status_code == 200 and "Explorer Ed" in r.text
        assert client.get("/admin/explorer?q=ed").status_code == 200
        rd = client.get("/admin/explorer/rider/ex1")
        assert rd.status_code == 200
        assert "Begode" in rd.text and "Ban rider" in rd.text and "Master" in rd.text
        assert client.get("/admin/explorer/trips").status_code == 200
        td = client.get("/admin/explorer/trip/tr1")
        assert td.status_code == 200
        assert "Metrics" in td.text and "freespin spike" in td.text   # meta_json freespin surfaced
        assert client.get("/admin/explorer/rider/nope").status_code == 404
        assert client.get("/admin/explorer/trip/nope").status_code == 404


def test_metrics_tree_shows_descriptions(db):
    with TestClient(app) as client:
        _auth(client)
        r = client.get("/admin/metrics")
        assert r.status_code == 200
        assert "Mile Muncher" in r.text and "Most distance ever ridden" in r.text


def test_ban_and_unban_through_ui(db):
    _seed(db)
    with TestClient(app) as client:
        _auth(client)
        r = client.post("/admin/rider/ex1/ban", data={"reason": "GPS spoofing"},
                        follow_redirects=False)
        assert r.status_code == 303
        db.expire_all()
        assert settings.is_banned(db, "ex1") is True
        assert stats.rider_card(db, "ex1")["ban_reason"] == "GPS spoofing"
        page = client.get("/admin/explorer/rider/ex1")
        assert "Account suspended" in page.text

        r = client.post("/admin/rider/ex1/unban", follow_redirects=False)
        assert r.status_code == 303
        db.expire_all()
        assert settings.is_banned(db, "ex1") is False
