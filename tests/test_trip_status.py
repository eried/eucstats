"""A trip's verdict must be re-checkable, or a held trip is yellow forever.

The app only ever saw the verdict returned at upload. There was no way to ask again, so
when an admin approved a flagged trip the phone never found out: the rider kept a yellow
"under review" cloud permanently, on a ride that had been accepted and was already counting
on the leaderboards. Retrying didn't help either — re-uploading the same trip_uuid hits the
dedupe and returns the same status.
"""
from fastapi.testclient import TestClient

import models
from main import app


def _trip(db, uuid, status, reasons=None, store="r1"):
    if db.get(models.Rider, store) is None:
        db.add(models.Rider(store_id=store, display_name="Rider One", platform="google_play"))
    db.add(models.Trip(trip_uuid=uuid, rider_store_id=store, distance_km=27.5,
                       validation_status=status, flag_reasons=reasons,
                       start_lat=55.31, start_lon=11.96, country="DK"))
    db.commit()


def test_unknown_trip_is_404(db):
    with TestClient(app) as client:
        assert client.get("/api/v1/trips/does-not-exist").status_code == 404


def test_accepted_trip(db):
    _trip(db, "t-ok", "validated")
    with TestClient(app) as client:
        j = client.get("/api/v1/trips/t-ok").json()
    assert j["validation_status"] == "validated"
    assert j["verdict"] == "accepted"
    assert j["distance_km"] == 27.5


def test_held_trip_reports_why(db):
    _trip(db, "t-held", "flagged", ["teleport"])
    with TestClient(app) as client:
        j = client.get("/api/v1/trips/t-held").json()
    assert j["verdict"] == "under_review"
    assert j["reasons"] == ["teleport"]


def test_rejected_trip(db):
    _trip(db, "t-no", "rejected", ["mock_location"])
    with TestClient(app) as client:
        j = client.get("/api/v1/trips/t-no").json()
    assert j["verdict"] == "rejected"


def test_verdict_changes_once_an_admin_approves(db):
    """The whole point: the app can learn the yellow cloud cleared."""
    _trip(db, "t-pending", "flagged", ["teleport"])
    with TestClient(app) as client:
        assert client.get("/api/v1/trips/t-pending").json()["verdict"] == "under_review"
        t = db.get(models.Trip, "t-pending")          # what the admin approve button does
        t.validation_status, t.flag_reasons = "validated", None
        db.commit()
        j = client.get("/api/v1/trips/t-pending").json()
    assert j["verdict"] == "accepted"
    assert j["reasons"] == []


def test_status_does_not_leak_rider_or_location(db):
    """A trip_uuid is unguessable, but this must still not become a lookup for who rode where."""
    _trip(db, "t-priv", "validated")
    with TestClient(app) as client:
        j = client.get("/api/v1/trips/t-priv").json()
    for leaked in ("rider_store_id", "store_id", "start_lat", "start_lon", "country"):
        assert leaked not in j, f"{leaked} exposed"
