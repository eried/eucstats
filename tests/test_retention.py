from datetime import datetime

from repository.riders import RiderRepo
from repository.trips import TripRepo
from services import retention
import models


def _trip_with_raw(db, uuid, received_at):
    if RiderRepo(db).get("rr") is None:
        RiderRepo(db).upsert("rr", "google_play", "R", "NO")
    tr = TripRepo(db)
    if not tr.exists(uuid):
        tr.insert_trip(trip_uuid=uuid, rider_store_id="rr",
                       validation_status="validated", start_utc=datetime(2026, 6, 1))
    tr.save_raw(uuid, b"x" * 1000)
    ru = db.get(models.RawUpload, uuid)
    ru.received_at = received_at
    db.commit()


def test_age_eviction_keeps_summary(db):
    _trip_with_raw(db, "t1", datetime(2020, 1, 1))
    n = retention.run_retention(db, now=datetime(2026, 6, 3), retention_days=30, disk_floor_gb=0)
    assert n >= 1
    assert db.get(models.RawUpload, "t1") is None     # raw evicted
    assert db.get(models.Trip, "t1") is not None       # summary kept


def test_disk_pressure_eviction(db, monkeypatch):
    _trip_with_raw(db, "t2", datetime(2026, 6, 1))     # recent (not age-evictable)
    monkeypatch.setattr(retention.shutil, "disk_usage",
                        lambda p: type("U", (), {"free": 1 * (1024 ** 3)}))
    n = retention.run_retention(db, now=datetime(2026, 6, 3),
                                retention_days=3650, disk_floor_gb=10)
    assert n >= 1
    assert db.get(models.RawUpload, "t2") is None
