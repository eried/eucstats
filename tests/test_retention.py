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


def test_zero_days_keeps_everything(db):
    """Zero means never, and it has to.

    `cutoff = now - retention_days` made zero the most destructive value in the settings form
    rather than the obvious way to switch the rule off: every raw upload in the database is
    older than `now`, so one save of "0" through the admin page would have deleted the lot --
    in a single pass, with no confirmation, on the only data here that cannot be recomputed
    from anything else. Erwin asked to keep raw uploads indefinitely, which is exactly the
    wish somebody expresses by typing that number.
    """
    _trip_with_raw(db, "keep-me", datetime(2020, 1, 1))      # as old as the fixture allows
    n = retention.run_retention(db, now=datetime(2026, 6, 3),
                                retention_days=0, disk_floor_gb=0)
    assert db.get(models.RawUpload, "keep-me") is not None, (
        "retention_days=0 deleted a six-year-old raw upload; zero is being read as 'keep "
        "nothing' instead of 'keep everything'")
    assert n == 0


def test_zero_days_still_honours_the_disk_floor(db, monkeypatch):
    """Keeping everything is a wish about data; the floor is a fact about the disk.

    Switching the age rule off must not switch off the thing that makes switching it off safe
    -- otherwise "keep forever" fills the box and takes the site down with it, and the archive
    it was protecting goes anyway.
    """
    _trip_with_raw(db, "squeeze", datetime(2026, 6, 1))
    monkeypatch.setattr(retention.shutil, "disk_usage",
                        lambda p: type("U", (), {"free": 1 * (1024 ** 3)}))
    n = retention.run_retention(db, now=datetime(2026, 6, 3),
                                retention_days=0, disk_floor_gb=10)
    assert n >= 1, "the disk is below the floor and nothing was evicted"
    assert db.get(models.RawUpload, "squeeze") is None
