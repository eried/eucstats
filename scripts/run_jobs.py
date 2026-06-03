#!/usr/bin/env python
"""Periodic eucstats jobs, run by cron on the server:
    /opt/eucstats/.venv/bin/python /opt/eucstats/scripts/run_jobs.py

Generates the current-week champion snapshot and runs retention as a backstop
to the in-app loop. Idempotent and safe to run frequently."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, init_db
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
    finally:
        db.close()


if __name__ == "__main__":
    main()
