"""Hybrid eviction of full-resolution raw uploads (summaries/tracks are kept).

Evict a validated trip's raw blob when it is older than RETENTION_DAYS, or
whenever free disk falls below DISK_FLOOR_GB (oldest-validated-first)."""
from __future__ import annotations

import shutil

import config
from models import utcnow
from repository.trips import TripRepo


def free_gb(path: str) -> float:
    return shutil.disk_usage(path).free / (1024 ** 3)


def run_retention(db, now=None, retention_days=None, disk_floor_gb=None,
                  data_dir=None) -> int:
    now = now or utcnow()
    if retention_days is None or disk_floor_gb is None:   # admin overrides (app_meta) win over env/config
        import services.settings as settings
        r = settings.get_retention(db)
        retention_days = r["days"] if retention_days is None else retention_days
        disk_floor_gb = r["disk_floor_gb"] if disk_floor_gb is None else disk_floor_gb
    data_dir = data_dir or str(config.DATA_DIR)

    tr = TripRepo(db)
    evicted = 0

    # 1) age-based
    for ru in tr.evictable_by_age(now, retention_days):
        db.delete(ru)
        evicted += 1
    db.commit()

    # 2) disk-pressure: evict oldest validated raw until above the floor
    if free_gb(data_dir) < disk_floor_gb:
        for ru in tr.oldest_raw(limit=10000):
            db.delete(ru)
            db.commit()
            evicted += 1
            if free_gb(data_dir) >= disk_floor_gb:
                break

    return evicted
