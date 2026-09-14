#!/usr/bin/env python3
"""Find which board a suspicious figure came from, and which trip produced it.

    cd /opt/eucstats && .venv/bin/python scripts/find_record.py 1.88
    cd /opt/eucstats && .venv/bin/python scripts/find_record.py --rider wheel

A rider disputing their own record is the most useful bug report this site gets, but the
number alone does not say which board it is on: several are measured in seconds. This searches
every per-trip timing and acceleration column for a match, and prints the trip behind each so
the telemetry can be read.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

COLS = ["fastest_0_40_s", "t_0_60_s", "t_0_100_s", "stop_30_s", "stop_50_s",
        "sustained_accel", "accel_g", "brake_g", "max_gforce",
        "accel_g_30", "accel_g_50", "brake_g_30", "brake_g_50"]


def _board(db, col: str) -> int:
    """Rank a timing column, with the acceleration it implies printed next to it.

    A time on its own tells a rider nothing about whether to believe it. The g does: an EUC
    puts its power down through one contact patch, and a rider can only lean so far before
    the wheel simply goes out from under them, so anything much past a third of a g deserves
    an explanation.
    """
    from models import Rider, Trip

    names = {r.store_id: r.display_name for r in db.query(Rider).all()}
    rows = [t for t in db.query(Trip).filter(Trip.validation_status == "validated").all()
            if getattr(t, col, None) is not None]
    rows.sort(key=lambda t: getattr(t, col))
    target = 40.0 if "0_40" in col else 60.0 if "0_60" in col else 100.0 if "0_100" in col else None
    print(f"{col}, lowest first ({len(rows)} trips)")
    for t in rows[:15]:
        v = getattr(t, col)
        g = (target / 3.6 / v / 9.80665) if (target and v > 0) else None
        print(f"   {v:7.2f}s  {('%.2f g' % g) if g else '      '}  "
              f"{(names.get(t.rider_store_id) or '?'):<14} {str(t.start_utc)[:10]}  "
              f"accel_g={t.accel_g}  max={t.max_speed}  {t.trip_uuid[:8]}")
    return 0


def _sprint(db, prefix: str) -> int:
    """Find the launch the 0-40 board actually timed, and show what the wheel and the ground
    were each doing through it. The metric takes the LOWER of wheel and GPS speed and needs
    GPS on every sample, so whatever produced the figure has to be visible in both columns."""
    import config
    from ingest.parser import parse_csv
    from ingest.summary import _corrob_speed
    from models import RawUpload, Rider, Trip
    from services import settings
    from services.ingest import _gunzip_capped, _is_gzip

    t = next((x for x in db.query(Trip).all() if x.trip_uuid.startswith(prefix)), None)
    if t is None:
        print(f"no trip starting {prefix}")
        return 1
    rider = db.get(Rider, t.rider_store_id)
    ru = db.get(RawUpload, t.trip_uuid)
    if ru is None:
        print("raw upload already evicted; cannot replay")
        return 1
    cap = int(config.MAX_DECOMPRESSED_MB * 1024 * 1024)
    data = _gunzip_capped(ru.blob, cap) if _is_gzip(ru.blob) else ru.blob
    s = parse_csv(data.decode("utf-8", "replace"), 0)
    cal = settings.get_calibration(db)
    target = cal["accel_target_kmh"]

    print(f"{t.trip_uuid[:8]}  {rider.display_name if rider else '?'}  {str(t.start_utc)[:16]}"
          f"  {(t.distance_km or 0):.1f} km  reported 0-{target:.0f} in {t.fastest_0_40_s}s")
    print()

    # walk it the way the metric does, and keep the fastest qualifying run
    best = None
    start_i = None
    runmax = 0.0
    prev = None
    for i, x in enumerate(s):
        sp = _corrob_speed(x)
        if sp is None or x.gps_speed is None:
            start_i, prev, runmax = None, None, 0.0
            continue
        if prev is not None and (x.t - prev[0]).total_seconds() > cal.get("max_gap_s", 5.0):
            start_i, runmax = None, 0.0
        if sp <= 2.0:
            start_i, runmax = i, sp
        elif start_i is not None:
            if sp < runmax * 0.8:
                start_i, runmax = None, 0.0
            else:
                runmax = max(runmax, sp)
                if sp >= target:
                    dt = (x.t - s[start_i].t).total_seconds()
                    if best is None or dt < best[0]:
                        best = (dt, start_i, i)
                    start_i, runmax = None, 0.0
        prev = (x.t, sp)

    if best is None:
        print("no qualifying launch found on replay")
        return 0
    dt, a, b = best
    print(f"fastest qualifying launch: samples {a}-{b}, {dt:.2f}s")
    print()
    fmt = lambda v, n=1: "-" if v is None else f"{v:.{n}f}"
    for k in range(max(0, a - 6), min(len(s), b + 7)):
        x = s[k]
        mark = ">>" if a <= k <= b else "  "
        print(f"   {mark} {str(x.t)[11:19]} wheel={fmt(x.speed):>6} gps={fmt(x.gps_speed):>6} "
              f"used={fmt(_corrob_speed(x)):>6} A={fmt(x.current):>7} pwm={fmt(x.pwm):>5} "
              f"g={fmt(x.g, 2):>5} lat={fmt(x.lat, 5):>10} lon={fmt(x.lon, 5):>10}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("value", nargs="?", type=float, help="the figure the rider is disputing")
    ap.add_argument("--rider", help="match part of a rider's display name instead")
    ap.add_argument("--tol", type=float, default=0.02, help="how close counts as a match")
    ap.add_argument("--sprint", help="replay the 0-40 launch of one trip and print its telemetry")
    ap.add_argument("--board", help="rank one timing column, with the implied g beside it")
    args = ap.parse_args()

    from database import SessionLocal
    from models import Rider, Trip

    db = SessionLocal()
    try:
        if args.board:
            return _board(db, args.board)
        if args.sprint:
            return _sprint(db, args.sprint)
        riders = {r.store_id: r.display_name for r in db.query(Rider).all()}
        if args.rider:
            hits = [(sid, n) for sid, n in riders.items()
                    if args.rider.lower() in (n or "").lower()]
            print(f"riders matching {args.rider!r}: {len(hits)}")
            for sid, n in hits:
                trips = db.query(Trip).filter(Trip.rider_store_id == sid).all()
                print(f"   {n}  ({sid})  {len(trips)} trip(s)")
                best = {}
                for c in COLS:
                    vals = [(getattr(t, c), t) for t in trips if getattr(t, c) is not None]
                    if not vals:
                        continue
                    lo = min(vals, key=lambda x: x[0])
                    hi = max(vals, key=lambda x: x[0])
                    best[c] = (lo, hi)
                for c, (lo, hi) in best.items():
                    print(f"      {c:<18} min {lo[0]:8.3f} ({lo[1].trip_uuid[:8]})"
                          f"   max {hi[0]:8.3f} ({hi[1].trip_uuid[:8]})")
            return 0

        if args.value is None:
            ap.error("give a value, or --rider")
        print(f"searching every timing column for {args.value} (+-{args.tol})\n")
        for c in COLS:
            rows = [t for t in db.query(Trip).all()
                    if getattr(t, c) is not None and abs(getattr(t, c) - args.value) <= args.tol]
            for t in rows:
                print(f"{c:<18} {getattr(t, c):8.3f}  {t.trip_uuid[:8]} "
                      f"{(riders.get(t.rider_store_id) or '?'):<14} {str(t.start_utc)[:16]} "
                      f"{(t.distance_km or 0):6.1f} km  max {t.max_speed or 0:.1f} km/h "
                      f"[{t.validation_status}]")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
