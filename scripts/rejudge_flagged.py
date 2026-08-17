#!/usr/bin/env python3
"""Re-judge flagged trips against the CURRENT rules, and approve the ones that now pass.

    cd /opt/eucstats && .venv/bin/python scripts/rejudge_flagged.py            # dry run
    cd /opt/eucstats && .venv/bin/python scripts/rejudge_flagged.py --apply

When a plausibility rule is fixed, trips it wrongly flagged stay flagged: reprocessing
deliberately leaves validation_status alone, so nothing re-examines them. Approving by hand
means a human deciding a trip is fine, which is exactly the judgement the rules exist to make.

This replays each flagged trip's stored raw upload through the current parser, summarizer and
plausibility check. A trip is approved only when the rules themselves now return "validated" -
the fixed code is the arbiter, not the operator. Anything that still trips a rule stays
flagged, and its reasons are printed.

Only trips whose raw upload is still inside the retention window can be re-judged; older ones
are reported and left alone. Approving matches the admin button exactly: status to validated,
reasons cleared, then aggregate so it counts on the leaderboards.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="approve the trips that now pass (default is a dry run)")
    ap.add_argument("--rider", help="limit to one rider's store_id")
    args = ap.parse_args()

    import config
    from database import SessionLocal
    from ingest.parser import parse_csv
    from ingest.plausibility import check
    from ingest.summary import summarize
    from models import RawUpload, Rider, Trip
    from services import settings
    from services.aggregator import Aggregator
    from services.ingest import _gunzip_capped, _is_gzip

    db = SessionLocal()
    try:
        cal = settings.get_calibration(db)
        thr = settings.get_thresholds(db)
        disabled = settings.pipeline_disabled(db)
        cap = int(config.MAX_DECOMPRESSED_MB * 1024 * 1024)

        q = db.query(Trip).filter(Trip.validation_status == "flagged")
        if args.rider:
            q = q.filter(Trip.rider_store_id == args.rider)
        flagged = q.order_by(Trip.start_utc).all()
        names = {r.store_id: r.display_name for r in db.query(Rider).all()}
        print(f"{len(flagged)} flagged trip(s)\n")

        would, kept, no_raw = [], [], []
        for t in flagged:
            who = (names.get(t.rider_store_id) or "?")[:14].ljust(14)
            head = f"  {t.trip_uuid[:8]} {who} {(t.distance_km or 0):7.1f} km"
            ru = db.get(RawUpload, t.trip_uuid)
            if ru is None:
                no_raw.append(t)
                print(f"{head}  raw evicted, cannot re-judge")
                continue
            try:
                data = _gunzip_capped(ru.blob, cap) if _is_gzip(ru.blob) else ru.blob
                samples = parse_csv(data.decode("utf-8", "replace"), 0)
                sm = summarize(samples, gps_tolerance=thr["dist_tolerance"], cal=cal,
                               teleport_kmh=thr["teleport_kmh"])
                status, reasons = check(
                    samples, sm, bool(t.is_mock_location),
                    max_kmh=thr["max_kmh"], max_g=thr["max_g"],
                    teleport_kmh=thr["teleport_kmh"], teleport_max_jumps=thr["teleport_max_jumps"],
                    teleport_gap_s=thr["teleport_gap_s"], teleport_min_kmh=thr["teleport_min_kmh"],
                    teleport_min_jump_m=thr["teleport_min_jump_m"],
                    teleport_jump_rate=thr["teleport_jump_rate"],
                    dist_tolerance=thr["dist_tolerance"],
                    unverified_dist_km=thr["unverified_dist_km"],
                    mismatch_min_km=thr["mismatch_min_km"], disabled=disabled,
                )
            except Exception as e:                     # a corrupt blob must not stop the run
                kept.append(t)
                print(f"{head}  re-judge failed ({type(e).__name__}), left flagged")
                continue

            if status == "validated":
                would.append(t)
                print(f"{head}  {t.flag_reasons} -> clears")
            else:
                kept.append(t)
                print(f"{head}  {t.flag_reasons} -> still {status}: {reasons}")

        print(f"\n{len(would)} would be approved, {len(kept)} stay flagged, "
              f"{len(no_raw)} unjudgeable")
        if not args.apply:
            print("\ndry run, nothing written. Re-run with --apply to approve.")
            return 0
        if not would:
            print("\nnothing to approve")
            return 0

        for t in would:                                # same steps as the admin approve button
            t.validation_status = "validated"
            t.flag_reasons = None
            db.commit()
            Aggregator(db).apply(t)                    # count it on the leaderboards
            print(f"approved {t.trip_uuid[:8]}  {(t.distance_km or 0):.1f} km")
        print(f"\napproved {len(would)} trip(s)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
