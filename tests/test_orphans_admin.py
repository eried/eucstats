"""The /admin/wheels orphan card: shows wheel-less trips and attaches them back."""
import json
from datetime import datetime

import pyotp
from fastapi.testclient import TestClient

import config
import models
from main import app
from repository.riders import RiderRepo
from repository.trips import TripRepo


def _auth(client):
    if config.ADMIN_STATE_FILE.exists():
        config.ADMIN_STATE_FILE.unlink()
    client.get("/admin")
    state = json.loads(config.ADMIN_STATE_FILE.read_text())
    # These tests are about other parts of the console, not about how it is unlocked. The
    # admin sign-in wants two factors now (a code AND a pairing confirmed from a phone), and
    # driving a pairing handshake in every one of them would test the same six lines over and
    # over. This is the documented single-factor escape hatch, used here for the same reason
    # it exists: so the console is reachable without a second device. The two-factor rule
    # itself is covered by tests/test_admin_two_factor.py.
    state["admin_require_pairing"] = False
    config.ADMIN_STATE_FILE.write_text(json.dumps(state))
    client.post("/admin/verify-totp", data={"code": pyotp.TOTP(state["totp_secret"]).now()})


def _seed(db):
    RiderRepo(db).upsert("solo", "google_play", "Solo Sam", "NO")
    RiderRepo(db).upsert("duo", "google_play", "Duo Dee", "NO")
    db.add(models.Wheel(wheel_id="AA:01", rider_store_id="solo", brand="InMotion", model="InMotion V14"))
    db.add(models.Wheel(wheel_id="BB:01", rider_store_id="duo", brand="LeaperKim", model="NOSFET Apex"))
    db.add(models.Wheel(wheel_id="BB:02", rider_store_id="duo", brand="IPS", model=None))
    db.commit()
    TripRepo(db).insert_trip(trip_uuid="s-orphan", rider_store_id="solo", distance_km=7.0,
                             start_utc=datetime(2026, 6, 1), validation_status="validated",
                             app_version="0.13.1", meta_json={"wheel_unresolved": {}})
    TripRepo(db).insert_trip(trip_uuid="d-orphan", rider_store_id="duo", distance_km=9.0,
                             start_utc=datetime(2026, 6, 2), validation_status="validated",
                             meta_json={"wheel_unresolved": {"brand": "LeaperKim"}})
    db.commit()


def test_orphan_card_lists_and_explains(db):
    _seed(db)
    with TestClient(app) as client:
        _auth(client)
        r = client.get("/admin/wheels")
        assert r.status_code == 200
        assert "Unattached trips" in r.text
        assert "Solo Sam" in r.text and "Duo Dee" in r.text
        assert "auto-attachable" in r.text           # solo has one wheel
        assert "needs a choice" in r.text            # duo has two
        # the diagnostic distinguishes the two failure modes
        assert "phone-only ride" in r.text
        assert "partial, no serial/MAC" in r.text
        # the app-version column: spot which build a lost-identity trip came from
        assert "<th>version" in r.text and "0.13.1" in r.text


def test_orphan_auto_attach_route(db):
    _seed(db)
    with TestClient(app) as client:
        _auth(client)
        r = client.post("/admin/wheels/orphan-auto", follow_redirects=False)
        assert r.status_code == 303
        db.expire_all()
        assert db.get(models.Trip, "s-orphan").wheel_id == "AA:01"
        assert db.get(models.Trip, "d-orphan").wheel_id is None   # ambiguous, untouched


def test_orphan_manual_attach_route(db):
    _seed(db)
    with TestClient(app) as client:
        _auth(client)
        r = client.post("/admin/wheels/orphan-attach",
                        data={"trip_uuid": "d-orphan", "wheel_id": "BB:01"}, follow_redirects=False)
        assert r.status_code == 303
        db.expire_all()
        assert db.get(models.Trip, "d-orphan").wheel_id == "BB:01"


def test_orphan_routes_require_auth(db):
    _seed(db)
    with TestClient(app) as client:
        r = client.post("/admin/wheels/orphan-auto", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin"
        db.expire_all()
        assert db.get(models.Trip, "s-orphan").wheel_id is None     # nothing happened


def test_overview_flags_orphans(db):
    _seed(db)
    with TestClient(app) as client:
        _auth(client)
        r = client.get("/admin")
        assert "no wheel" in r.text and "16.0 km" in r.text          # 7 + 9
