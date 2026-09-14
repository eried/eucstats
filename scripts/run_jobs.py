#!/usr/bin/env python
"""Periodic eucstats jobs, run by cron on the server:
    /opt/eucstats/.venv/bin/python /opt/eucstats/scripts/run_jobs.py

Generates the current-week champion snapshot and runs retention as a backstop
to the in-app loop. Idempotent and safe to run frequently."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, init_db
from services import datasets
from services.aggregator import reconcile_unaggregated
from services.retention import run_retention
from services.snapshots import generate_weekly


def main():
    init_db()
    db = SessionLocal()
    try:
        payload = generate_weekly(db)
        champ = payload.get("champion")
        print(f"[snapshot] {payload['week']}: champion={champ['name'] if champ else None}")
        print(f"[retention] evicted {run_retention(db)} raw uploads")
        print(f"[reconcile] aggregated {reconcile_unaggregated(db)} previously-missed trips")
    finally:
        db.close()
    try:
        # Seven dailies, not fourteen. Every snapshot is a full copy of the database, and the
        # database is mostly raw uploads - so each extra day of backups costs as much as the
        # rides themselves. Keeping fourteen of them is what filled the disk, and the disk
        # filling is what made the retention job delete raw uploads to win space back: the
        # backups were being paid for with the data they exist to protect.
        print(f"[backup] daily dataset snapshot -> {datasets.auto_backup(keep=7)}")
    except Exception as e:  # never let a backup failure break the rest of the cron
        print(f"[backup] failed: {e}")


if __name__ == "__main__":
    main()
