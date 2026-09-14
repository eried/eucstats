#!/usr/bin/env python3
"""Set how long raw uploads are kept, and show what it means in practice.

    cd /opt/eucstats && .venv/bin/python scripts/set_retention.py
    cd /opt/eucstats && .venv/bin/python scripts/set_retention.py --days 90 --apply

Raw uploads are what makes a metric fixable after the fact: without them a trip cannot be
reprocessed, and a rider disputing a record cannot be answered. They are also the bulk of the
database, and every daily snapshot copies the lot - so "keep everything forever" costs about
eight times what the rides themselves do.

The honest setting is therefore a period, not a principle: long enough to tune the metrics and
answer a dispute, not so long that the backups eat the disk and the retention job starts
deleting raw uploads to win the space back.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, help="how long to keep raw uploads")
    ap.add_argument("--floor-gb", type=float, help="free space the retention job protects")
    ap.add_argument("--apply", action="store_true", help="write it (default just reports)")
    args = ap.parse_args()

    import config
    from database import SessionLocal
    from models import RawUpload, Trip
    from services import settings

    db = SessionLocal()
    try:
        cur = settings.get_retention(db)
        raws = db.query(RawUpload).count()
        trips = db.query(Trip).count()
        total = sum(len(r.blob or b"") for r in db.query(RawUpload).all())
        free = shutil.disk_usage(str(config.DATA_DIR)).free / 1024 ** 3

        print(f"now: keep raw uploads {cur['days']} days, protect {cur['disk_floor_gb']} GB free")
        print(f"     {raws} raw upload(s) held for {trips} trip(s), "
              f"{total / 1024 ** 2:.1f} MB, {free:.1f} GB free on disk")
        if raws:
            print(f"     average {total / raws / 1024:.0f} KB per upload")
        print()

        if args.days is None and args.floor_gb is None:
            print("pass --days and/or --floor-gb to change it, then --apply to write.")
            return 0

        days = cur["days"] if args.days is None else args.days
        floor = cur["disk_floor_gb"] if args.floor_gb is None else args.floor_gb
        print(f"would set: keep {days} days, protect {floor} GB free")
        if raws:
            per_day = total / raws * 12          # a rough dozen rides a day, as now
            print(f"     at roughly a dozen rides a day that is "
                  f"{per_day * days / 1024 ** 3:.2f} GB of raw uploads at steady state,")
            print(f"     and about {per_day * days * 8 / 1024 ** 3:.2f} GB once the seven daily "
                  f"snapshots each carry a copy.")
        if not args.apply:
            print("\ndry run, nothing written. Re-run with --apply.")
            return 0
        settings.set_retention(db, days, floor, cur["interval_s"])
        print("\nwritten. It takes effect on the next retention run.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
