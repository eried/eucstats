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
        state = json.loads(config.ADMIN_STATE_FILE.read_text())
        secret = state["totp_secret"]

        # a valid code alone is NOT enough any more: the console wants a pairing from the
        # admin's phone as well, so this lands on the second-factor step
        code = pyotp.TOTP(secret).now()
        r = client.post("/admin/verify-totp", data={"code": code})
        assert r.status_code in (200, 303)
        assert client.get("/admin/api/status").status_code == 401

        # with the phone factor switched off in admin.json — the documented path for an
        # operator who has lost the bound device — the code alone gets in again
        state["admin_require_pairing"] = False
        config.ADMIN_STATE_FILE.write_text(json.dumps(state))
        client.post("/admin/verify-totp", data={"code": pyotp.TOTP(secret).now()})

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
        state = json.loads(config.ADMIN_STATE_FILE.read_text())
        # single factor on purpose: the two-factor rule has its own test
        state["admin_require_pairing"] = False
        config.ADMIN_STATE_FILE.write_text(json.dumps(state))
        secret = state["totp_secret"]
        client.post("/admin/verify-totp", data={"code": pyotp.TOTP(secret).now()})
        r = client.post("/admin/trip/fl1/approve")
        assert r.status_code in (200, 303)
    db.expire_all()
    assert db.get(models.Trip, "fl1").validation_status == "validated"
    assert db.get(models.RiderStat, "fr").total_km == 7.0


def _signed_in(client, db):
    """TOTP alone, with the phone factor switched off -- the documented recovery path, and the
    only way a test can reach the console without a second device."""
    import config as _config
    if _config.ADMIN_STATE_FILE.exists():
        _config.ADMIN_STATE_FILE.unlink()
    client.get("/admin")
    state = json.loads(_config.ADMIN_STATE_FILE.read_text())
    state["admin_require_pairing"] = False
    _config.ADMIN_STATE_FILE.write_text(json.dumps(state))
    client.post("/admin/verify-totp", data={"code": pyotp.TOTP(state["totp_secret"]).now()})


def _flagged(db, n):
    """n trips held back by the plausibility checks, each by its own rider."""
    import models
    out = []
    for i in range(n):
        sid = "bulk-%d" % i
        if db.get(models.Rider, sid) is None:
            db.add(models.Rider(store_id=sid, display_name=sid, platform="google_play",
                                flag="NO"))
        t = models.Trip(trip_uuid="bulk-trip-%d" % i, rider_store_id=sid, distance_km=4.0,
                        validation_status="flagged", flag_reasons=["too fast"],
                        start_utc=models.utcnow(), end_utc=models.utcnow())
        db.add(t)
        out.append(t.trip_uuid)
    db.commit()
    return out


def test_approve_all_clears_the_queue_and_counts_them(db):
    """Fifty rows with two buttons each is a hundred presses to answer one question."""
    import models
    uuids = _flagged(db, 4)
    with TestClient(app) as client:
        _signed_in(client, db)
        r = client.post("/admin/trips/flagged/approve-all", follow_redirects=False)
        assert r.status_code == 303, r.text
    db.expire_all()
    for u in uuids:
        assert db.get(models.Trip, u).validation_status == "validated"
        assert db.get(models.Trip, u).flag_reasons in (None, [])
    assert db.query(models.Trip).filter(models.Trip.validation_status == "flagged").count() == 0


def test_reject_all_clears_the_queue(db):
    import models
    uuids = _flagged(db, 3)
    with TestClient(app) as client:
        _signed_in(client, db)
        r = client.post("/admin/trips/flagged/reject-all", follow_redirects=False)
        assert r.status_code == 303, r.text
    db.expire_all()
    for u in uuids:
        assert db.get(models.Trip, u).validation_status == "rejected"


def test_the_bulk_buttons_need_the_console(db):
    """Both are one press that moves every flagged trip, so neither may answer to a stranger."""
    import models
    _flagged(db, 2)
    with TestClient(app) as client:
        for path in ("/admin/trips/flagged/approve-all", "/admin/trips/flagged/reject-all"):
            r = client.post(path, follow_redirects=False)
            assert r.status_code == 303 and r.headers["location"] == "/admin", path
    db.expire_all()
    assert db.query(models.Trip).filter(models.Trip.validation_status == "flagged").count() == 2


def test_an_empty_queue_offers_no_bulk_buttons(db):
    """A press into the dark: "approve all" with nothing to approve."""
    from web.admin import _bulk_flagged
    assert _bulk_flagged(0) == ""
    h = _bulk_flagged(9)
    assert "approve all 9" in h and "reject all 9" in h
    assert "confirm(" in h, "a bulk action with no confirmation"
