#!/usr/bin/env python3
"""Clean up re-uploaded archive trips.

    cd /opt/eucstats && .venv/bin/python scripts/prune_archive_trips.py --before 2026-01-01
    cd /opt/eucstats && .venv/bin/python scripts/prune_archive_trips.py --before 2026-01-01 --apply

A testing phone restoring its archive re-submitted rides the server already had. The app
minted a NEW trip_uuid each time, so the server's dedupe (which keys on trip_uuid) could not
see them, and they landed as fresh trips. Two things came in with them:

  * exact duplicates - same rider, same window, same distance, same sample count;
  * merged re-exports - one decimated trip (mostly 500 or 501 samples) spanning what were
    two or three separate rides, which is what tripped `overlapping_trip`.

Two independent actions, both dry by default:

  --before DATE   DELETE trips ridden before DATE, with their tracks and raw uploads.
                  Irreversible, which is why a dataset snapshot is taken first.
  --overlaps      REJECT the re-uploaded side of every remaining time-overlapping pair.
                  Reversible: the row stays, and rejected trips are excluded from every
                  board and from the aggregates.

Which side of a pair is the re-upload is decided by how long after the ride it arrived, with
the sample count breaking ties - the original recording is denser than a 500-point re-export.
The original is always kept.

Aggregates, records, the heatmap and country stats are rebuilt from what remains.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _lag_days(t):
    if not (t.created_at and t.start_utc):
        return 0
    return (t.created_at - t.start_utc).total_seconds() / 86400.0


def _overlapping_pairs(trips):
    """(keep, drop) for every pair of one rider's trips whose time windows overlap."""
    by = {}
    for t in trips:
        if t.start_utc and t.end_utc:
            by.setdefault(t.rider_store_id, []).append(t)
    pairs = []
    for group in by.values():
        group.sort(key=lambda t: t.start_utc)
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a.start_utc >= b.end_utc or a.end_utc <= b.start_utc:
                    continue
                # The re-upload arrived long after the ride; if that ties, it is the
                # sparser of the two, because the archive export decimates to ~500 points.
                ranked = sorted((a, b), key=lambda t: (-_lag_days(t), t.sample_count or 0))
                pairs.append((ranked[1], ranked[0]))     # (keep, drop)
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", help="delete trips RIDDEN before this date (YYYY-MM-DD)")
    ap.add_argument("--overlaps", action="store_true",
                    help="reject the re-uploaded side of each overlapping pair")
    ap.add_argument("--apply", action="store_true", help="write changes (default is a dry run)")
    args = ap.parse_args()
    if not args.before and not args.overlaps:
        ap.error("nothing to do: pass --before and/or --overlaps")

    from database import SessionLocal
    from models import RawUpload, Rider, Trip, TripTrack
    from services.aggregator import rebuild_all

    cutoff = datetime.strptime(args.before, "%Y-%m-%d") if args.before else None
    db = SessionLocal()
    try:
        names = {r.store_id: r.display_name for r in db.query(Rider).all()}
        who = lambda t: (names.get(t.rider_store_id) or "?")[:13].ljust(13)

        doomed = []
        if cutoff:
            doomed = [t for t in db.query(Trip).all() if t.start_utc and t.start_utc < cutoff]
            km = sum(t.distance_km or 0 for t in doomed)
            print(f"DELETE: {len(doomed)} trip(s) ridden before {args.before}, {km:.1f} km")
            for t in sorted(doomed, key=lambda x: x.start_utc)[:8]:
                print(f"   {t.trip_uuid[:8]} {who(t)} {str(t.start_utc)[:10]} {t.distance_km or 0:7.1f} km")
            if len(doomed) > 8:
                print(f"   ... and {len(doomed) - 8} more")

        gone = {t.trip_uuid for t in doomed}
        remaining = [t for t in db.query(Trip).all() if t.trip_uuid not in gone]
        pairs = _overlapping_pairs(remaining) if args.overlaps else []
        to_reject = {}
        for keep, drop in pairs:
            to_reject[drop.trip_uuid] = (keep, drop)
        if args.overlaps:
            print(f"\nREJECT: {len(to_reject)} re-upload(s) from {len(pairs)} overlapping pair(s)")
            for keep, drop in list(to_reject.values())[:8]:
                print(f"   keep {keep.trip_uuid[:8]} {str(keep.start_utc)[:16]} "
                      f"{keep.sample_count or 0:5d} samples, +{_lag_days(keep):.0f}d")
                print(f"   drop {drop.trip_uuid[:8]} {str(drop.start_utc)[:16]} "
                      f"{drop.sample_count or 0:5d} samples, +{_lag_days(drop):.0f}d  {who(drop)}")
            if len(to_reject) > 8:
                print(f"   ... and {len(to_reject) - 8} more")

        if not args.apply:
            print("\ndry run, nothing written. Re-run with --apply.")
            return 0

        try:                                  # the same safety net the admin Apply uses
            from services import datasets
            datasets.save_current(datasets._timestamped("pre-prune"),
                                  note="before archive trip prune", origin="pre-edit")
            print("\ndataset snapshot saved")
        except Exception as e:
            print(f"\nsnapshot failed ({e}) - aborting rather than deleting unprotected",
                  file=sys.stderr)
            return 1

        if doomed:
            ids = [t.trip_uuid for t in doomed]
            for chunk in (ids[i:i + 400] for i in range(0, len(ids), 400)):
                db.query(TripTrack).filter(TripTrack.trip_uuid.in_(chunk)).delete(synchronize_session=False)
                db.query(RawUpload).filter(RawUpload.trip_uuid.in_(chunk)).delete(synchronize_session=False)
                db.query(Trip).filter(Trip.trip_uuid.in_(chunk)).delete(synchronize_session=False)
            db.commit()
            print(f"deleted {len(doomed)} trip(s)")

        if to_reject:
            for uuid in to_reject:
                t = db.get(Trip, uuid)
                if t is not None and t.validation_status != "rejected":
                    t.validation_status = "rejected"
                    t.flag_reasons = ["archive_reupload"]
            db.commit()
            print(f"rejected {len(to_reject)} re-upload(s)")

        rebuild_all(db)                       # records, heatmap, country and rider stats
        print("aggregates rebuilt")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
