"""Admin hard-delete (purge) vs a rider's own account close (keeps portal presence)."""
import json

import pyotp
from fastapi.testclient import TestClient

import config
import models
from main import app
from services import stats
from services.aggregator import Aggregator
from services.identity import IdentityService, purge_rider


def _rider_with_trip(db, sid="s", name="Sheep"):
    db.add(models.Rider(store_id=sid, display_name=name, platform="google_play", flag="NO"))
    db.add(models.Wheel(wheel_id="w-" + sid, rider_store_id=sid, brand="Begode", model="Master"))
    db.commit()
    db.add(models.Trip(trip_uuid="tr-" + sid, rider_store_id=sid, validation_status="validated",
                       distance_km=10.0, max_speed=30.0))
    db.commit()                                  # trip must exist before its track/raw rows (FK)
    db.add(models.TripTrack(trip_uuid="tr-" + sid, points=b"x"))
    db.add(models.RawUpload(trip_uuid="tr-" + sid, blob=b"x", bytes=1))
    db.commit()
    Aggregator(db).apply(db.get(models.Trip, "tr-" + sid))


def test_self_delete_keeps_rider_on_portal(db):
    _rider_with_trip(db, "s", "Sheep")
    assert any(e["store_id"] == "s" for e in stats.mileage_leaderboard(db))
    IdentityService(db).delete("s")                 # rider closes their own account
    db.expire_all()
    board = stats.mileage_leaderboard(db)
    assert any(e["store_id"] == "s" for e in board)   # still on the leaderboard
    r = db.get(models.Rider, "s")
    assert r.display_name == "Sheep"                  # name NOT anonymized
    assert r.deleted_at is not None                   # but marked closed
    # the public stats card still loads (portal presence preserved)
    assert stats.rider_card(db, "s") is not None


def test_purge_removes_everything(db):
    _rider_with_trip(db, "s", "Sheep")
    _rider_with_trip(db, "keep", "Keeper")            # a second rider must survive

    assert purge_rider(db, "s") is True
    db.expire_all()
    assert db.get(models.Rider, "s") is None
    assert db.query(models.Trip).filter_by(rider_store_id="s").count() == 0
    assert db.get(models.TripTrack, "tr-s") is None
    assert db.get(models.RawUpload, "tr-s") is None
    assert db.get(models.Wheel, "w-s") is None
    assert db.get(models.RiderStat, "s") is None
    assert "s" not in [e["store_id"] for e in stats.mileage_leaderboard(db)]

    # the other rider is untouched
    assert db.get(models.Rider, "keep") is not None
    assert "keep" in [e["store_id"] for e in stats.mileage_leaderboard(db)]


def test_purge_missing_rider_returns_false(db):
    assert purge_rider(db, "ghost") is False


def _auth(client):
    if config.ADMIN_STATE_FILE.exists():
        config.ADMIN_STATE_FILE.unlink()
    client.get("/admin")
    secret = json.loads(config.ADMIN_STATE_FILE.read_text())["totp_secret"]
    client.post("/admin/verify-totp", data={"code": pyotp.TOTP(secret).now()})


def test_admin_delete_requires_matching_name(db):
    _rider_with_trip(db, "s", "Sheep")
    with TestClient(app) as client:
        _auth(client)
        # wrong confirmation -> not deleted
        client.post("/admin/rider/s/delete", data={"confirm": "wrong"}, follow_redirects=False)
        db.expire_all()
        assert db.get(models.Rider, "s") is not None
        # correct name -> deleted
        client.post("/admin/rider/s/delete", data={"confirm": "Sheep"}, follow_redirects=False)
        db.expire_all()
        assert db.get(models.Rider, "s") is None
