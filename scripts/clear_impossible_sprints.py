#!/usr/bin/env python3
"""Clear sprint times that no wheel could have produced.

    cd /opt/eucstats && .venv/bin/python scripts/clear_impossible_sprints.py
    cd /opt/eucstats && .venv/bin/python scripts/clear_impossible_sprints.py --apply

The sprint boards were guarded by a minimum TIME per target, which is not the same guard at
different targets: 1.5 s to 40 km/h permits 0.76 g and 1.0 s to 60 km/h permits 1.69 g. Both
boards filled from the top with figures no wheel produces, and a rider disputing his own
record is what surfaced it.

ingest/summary.py now tests what a time IMPLIES instead, so new uploads are judged properly.
This clears what is already stored. It cannot recompute those trips - their raw uploads have
been evicted - but it does not need to: the implied acceleration follows from the stored time
and the target alone, which is precisely the test being applied.

Only the offending column is cleared, and only for a trip that fails the test. Everything else
about the trip is untouched, and a trip whose raw upload survives will recompute the column
correctly on the next reprocess.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# (column, the speed in km/h it is a time to)
SPRINTS = [("fastest_0_40_s", 40.0), ("t_0_60_s", 60.0), ("t_0_100_s", 100.0)]


def implied_g(target_kmh: float, seconds: float) -> float:
    return (target_kmh / 3.6) / seconds / 9.80665


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write it (default is a dry run)")
    ap.add_argument("--max-g", type=float, default=None,
                    help="override the cap (defaults to the admin calibration)")
    args = ap.parse_args()

    from database import SessionLocal
    from models import Rider, Trip
    from services import settings
    from services.aggregator import rebuild_all

    db = SessionLocal()
    try:
        cap = args.max_g if args.max_g is not None else settings.get_calibration(db)["accel_max_g"]
        names = {r.store_id: r.display_name for r in db.query(Rider).all()}
        doomed = []
        for t in db.query(Trip).all():
            for col, target in SPRINTS:
                v = getattr(t, col, None)
                if v is None or v <= 0:
                    continue
                g = implied_g(target, v)
                if g > cap:
                    doomed.append((t, col, v, g))

        print(f"cap {cap:.2f} g — {len(doomed)} stored sprint time(s) exceed it\n")
        for t, col, v, g in sorted(doomed, key=lambda x: -x[3]):
            print(f"   {g:5.2f} g  {col:<16} {v:7.2f}s  "
                  f"{(names.get(t.rider_store_id) or '?'):<14} {str(t.start_utc)[:10]}  "
                  f"{t.trip_uuid[:8]}  [{t.validation_status}]")

        if not doomed:
            return 0
        if not args.apply:
            print("\ndry run, nothing written. Re-run with --apply.")
            return 0

        try:                                  # the safety net every bulk edit here uses
            from services import datasets
            datasets.save_current(datasets._timestamped("pre-sprintclear"),
                                  note="before clearing impossible sprint times", origin="pre-edit")
            print("\ndataset snapshot saved")
        except Exception as e:
            print(f"\nsnapshot failed ({e}) — refusing to edit unprotected", file=sys.stderr)
            return 1

        for t, col, _v, _g in doomed:
            setattr(t, col, None)
        db.commit()
        print(f"cleared {len(doomed)} sprint time(s)")
        rebuild_all(db)
        print("aggregates rebuilt")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
