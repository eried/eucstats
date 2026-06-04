"""eucplanet integrity brief: 0,0 = no-fix, app-facing verdict, overlapping-trip,
file_sha256 integrity, app/os version stats."""
import hashlib
from datetime import datetime

import pytest

import config
import models
from ingest.parser import parse_csv
from services import stats
from services.ingest import IngestError, IngestService, _verdict


def test_zero_zero_is_no_fix():
    text = ("Date,Latitude,Longitude,Speed\n"
            "2026-06-04T10:00:00,0,0,12\n"
            "2026-06-04T10:00:01,59.9,10.7,13\n")
    s = parse_csv(text)
    assert s[0].lat is None and s[0].lon is None      # 0,0 treated as missing
    assert s[1].lat == 59.9 and s[1].lon == 10.7


def test_verdict_mapping():
    assert _verdict("validated") == "accepted"
    assert _verdict("flagged") == "under_review"
    assert _verdict("rejected") == "rejected"


def _rider(db, sid="a"):
    db.add(models.Rider(store_id=sid, display_name=sid, platform="google_play"))
    db.commit()


def _gps_csv(hh):
    # short GPS ride; odometer delta ~ GPS distance so it validates cleanly
    return (f"Date,Latitude,Longitude,Speed,Total mileage\n"
            f"2026-06-04T{hh}:00:00,59.90,10.70,12,1000.0\n"
            f"2026-06-04T{hh}:00:30,59.91,10.71,14,1001.2\n").encode()


def test_overlapping_trip_flagged(db, monkeypatch):
    monkeypatch.setattr(config, "INGEST_ALLOW", [])
    _rider(db, "a")
    r1 = IngestService(db).handle({"store_id": "a", "trip_uuid": "t1"}, _gps_csv("10"))
    assert "verdict" in r1
    # a second trip for the same rider in the same time window is impossible
    r2 = IngestService(db).handle({"store_id": "a", "trip_uuid": "t2"}, _gps_csv("10"))
    assert "overlapping_trip" in (r2.get("reasons") or [])
    assert r2["verdict"] == "under_review"


def test_non_overlapping_trip_ok(db, monkeypatch):
    monkeypatch.setattr(config, "INGEST_ALLOW", [])
    _rider(db, "a")
    IngestService(db).handle({"store_id": "a", "trip_uuid": "t1"}, _gps_csv("10"))
    r2 = IngestService(db).handle({"store_id": "a", "trip_uuid": "t2"}, _gps_csv("14"))  # later window
    assert "overlapping_trip" not in (r2.get("reasons") or [])


def test_checksum_mismatch_rejected(db, monkeypatch):
    monkeypatch.setattr(config, "INGEST_ALLOW", [])
    _rider(db, "a")
    csv = b"Date,Speed\n2026-06-04T10:00:00,12\n2026-06-04T10:00:01,13\n"
    with pytest.raises(IngestError) as e:
        IngestService(db).handle({"store_id": "a", "trip_uuid": "c1", "file_sha256": "deadbeef"}, csv)
    assert e.value.code == 422


def test_checksum_match_ok(db, monkeypatch):
    monkeypatch.setattr(config, "INGEST_ALLOW", [])
    _rider(db, "a")
    csv = b"Date,Speed\n2026-06-04T10:00:00,12\n2026-06-04T10:00:01,13\n"
    res = IngestService(db).handle(
        {"store_id": "a", "trip_uuid": "c2", "file_sha256": hashlib.sha256(csv).hexdigest()}, csv)
    assert res["trip_uuid"] == "c2"


def test_version_stats_app_and_os(db):
    db.add(models.Rider(store_id="a", display_name="A", platform="google_play"))
    db.add(models.Trip(trip_uuid="t1", rider_store_id="a", validation_status="validated",
                       app_version="1.2.3", meta_json={"os_version": "Android 14"},
                       start_utc=datetime(2026, 6, 4, 10, 0)))
    db.commit()
    vs = stats.version_stats(db)
    assert vs["appvers"][0]["version"] == "1.2.3"
    assert vs["osvers"][0]["version"] == "Android 14"
    assert vs["adopters"][0]["ver"] == "1.2.3"
