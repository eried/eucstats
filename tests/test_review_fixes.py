"""Fixes from the pre-launch ingest review: ragged rows (M5), no-GPS anti-cheat
(H1), retention of non-validated raws (M7), reconcile (M2), rebuild (M3)."""
from datetime import datetime, timedelta

import config
import models
from ingest.parser import parse_csv
from services import stats
from services.aggregator import Aggregator, rebuild_all, reconcile_unaggregated
from services.ingest import IngestService
from services.retention import run_retention


def _rider(db, sid="a"):
    db.add(models.Rider(store_id=sid, display_name=sid, platform="google_play"))
    db.commit()


def test_ragged_rows_parsed():
    text = "Date,Speed,Voltage\n2026-06-04T10:00:00,12\n2026-06-04T10:00:01,13,80\n"
    s = parse_csv(text)
    assert len(s) == 2                       # short row no longer dropped
    assert s[0].speed == 12 and s[0].voltage is None
    assert s[1].voltage == 80


def test_unverified_distance_flagged(db, monkeypatch):
    monkeypatch.setattr(config, "INGEST_ALLOW", [])
    _rider(db, "a")
    csv = b"Date,Total mileage\n2026-06-04T10:00:00,1000.0\n2026-06-04T10:30:00,1005.0\n"
    res = IngestService(db).handle({"store_id": "a", "trip_uuid": "u1"}, csv)
    assert res["validation_status"] == "flagged"
    assert "unverified_distance" in (res.get("reasons") or [])


def test_retention_evicts_flagged_raw(db):
    _rider(db, "a")
    db.add(models.Trip(trip_uuid="t1", rider_store_id="a", validation_status="flagged"))
    db.commit()
    db.add(models.RawUpload(trip_uuid="t1", blob=b"x", bytes=1))
    db.commit()
    ru = db.get(models.RawUpload, "t1")
    ru.received_at = datetime.utcnow() - timedelta(days=999)
    db.commit()
    run_retention(db, retention_days=30)
    assert db.get(models.RawUpload, "t1") is None     # flagged raw now evicted


def test_reconcile_aggregates_missed_trip(db):
    _rider(db, "a")
    db.add(models.Trip(trip_uuid="t1", rider_store_id="a", validation_status="validated",
                       distance_km=10.0, aggregated=False))
    db.commit()
    assert reconcile_unaggregated(db) == 1
    assert stats.mileage_leaderboard(db)[0]["total_km"] == 10.0


def test_rebuild_drops_rejected_trip(db):
    _rider(db, "a")
    db.add(models.Trip(trip_uuid="t1", rider_store_id="a", validation_status="validated",
                       distance_km=10.0))
    db.commit()
    Aggregator(db).apply(db.get(models.Trip, "t1"))
    assert stats.mileage_leaderboard(db)[0]["total_km"] == 10.0
    t = db.get(models.Trip, "t1")
    t.validation_status = "rejected"
    db.commit()
    rebuild_all(db)
    assert stats.mileage_leaderboard(db) == []        # its stats are gone
