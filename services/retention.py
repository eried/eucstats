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


def _sweep_spent_notices(db, now) -> int:
    """Membership rows whose only job was to carry one notice, long since unreadable.

    `*_seen` means a rider has been told their request was declined, their crew folded or
    they were removed. The notice is bounded to a week, so after that the row cannot be read
    again by anything -- it is a tombstone, one per rider per crew per event, kept for ever.

    The WAITING statuses go the same way at the same age, and have to. While a notice was
    retired by the act of reading it, almost every row reached `*_seen` within a page load
    and this sweep saw nearly all of them. Reading is a peek now -- it had to become one, or
    the notice never reached the reader at all; see `crews.last_answer` -- so a rider who is
    turned down and never comes back leaves a row that stays `declined` for ever. Its own
    seven-day window has already made it unreadable, which is the whole of the argument
    above: at thirty days an unacknowledged notice is exactly as dead as an acknowledged one,
    and keeping it is keeping a tombstone with a different name on it.

    `left_at IS NOT NULL` is what makes this safe: an active membership never has one.
    """
    from datetime import timedelta
    from models import ClanMember
    cutoff = now - timedelta(days=30)
    n = (db.query(ClanMember)
         .filter(ClanMember.status.in_(("declined_seen", "disbanded_seen", "removed_seen",
                                        "declined", "disbanded", "removed")),
                 ClanMember.left_at.isnot(None), ClanMember.left_at < cutoff)
         .delete(synchronize_session=False))
    if n:
        db.commit()
    return n


def run_retention(db, now=None, retention_days=None, disk_floor_gb=None,
                  data_dir=None) -> int:
    now = now or utcnow()
    if retention_days is None or disk_floor_gb is None:   # admin overrides (app_meta) win over env/config
        import services.settings as settings
        r = settings.get_retention(db)
        retention_days = r["days"] if retention_days is None else retention_days
        disk_floor_gb = r["disk_floor_gb"] if disk_floor_gb is None else disk_floor_gb
    data_dir = data_dir or str(config.DATA_DIR)

    _sweep_spent_notices(db, now)

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
