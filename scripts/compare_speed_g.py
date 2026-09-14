#!/usr/bin/env python3
"""Compare ways of deriving longitudinal g, without changing anything.

    cd /opt/eucstats && .venv/bin/python scripts/compare_speed_g.py

Read-only. It writes nothing, deploys nothing and touches no stored value — it replays the
raw uploads that still exist and reports what each method WOULD produce, side by side.

Today accel_g and brake_g come from how fast the CORROBORATED speed changes, and that is the
lower of wheel and GPS. Where GPS lags the wheel and then catches up - on the trip that
started this, on 49% of all samples above 10 km/h - the minimum jumps twenty km/h in a second
and the jump is reported as acceleration the wheel never did. The tell is in the stored data:
the largest values sit at exactly 0.80, pinned against MAX_LON_G, which is the shape of a
measurement being clamped rather than measured.

The alternative keeps GPS for what it is good at - proving the rider is genuinely out and
moving - and takes the speed CHANGE from the wheel, which is measured directly, at a high
rate, with no lag.

  current  min(wheel, gps), change taken from that
  present  change taken from the wheel, on samples where GPS is reporting at all
  moving   change taken from the wheel, on samples where GPS also says the rider is moving
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MOVE_KMH = 3.0


def main() -> int:
    import config
    from database import SessionLocal
    from ingest.parser import parse_csv
    from ingest.summary import _best_speed_g, _corrob_speed_pts
    from models import RawUpload, Rider, Trip
    from services.ingest import _gunzip_capped, _is_gzip

    db = SessionLocal()
    try:
        names = {r.store_id: r.display_name for r in db.query(Rider).all()}
        cap = int(config.MAX_DECOMPRESSED_MB * 1024 * 1024)
        raws = db.query(RawUpload).all()
        if not raws:
            print("no raw uploads left to replay")
            return 0

        print(f"{'trip':<10}{'rider':<13}{'gps lag':>8}   "
              f"{'accel_g: current  present  moving':<36}{'brake_g: current  present  moving'}")
        for ru in raws:
            t = db.get(Trip, ru.trip_uuid)
            if t is None:
                continue
            data = _gunzip_capped(ru.blob, cap) if _is_gzip(ru.blob) else ru.blob
            s = parse_csv(data.decode("utf-8", "replace"), 0)
            if len(s) < 10:
                continue

            both = [(x.speed, x.gps_speed) for x in s
                    if x.speed is not None and x.gps_speed is not None and x.speed > 10]
            lag = (sum(1 for w, g in both if g < w * 0.8) / len(both) * 100) if both else 0.0

            cur_a, cur_b = _best_speed_g(_corrob_speed_pts(s), 1.0)
            present = [(x.t, x.speed) for x in s
                       if x.speed is not None and x.gps_speed is not None]
            pre_a, pre_b = _best_speed_g(present, 1.0)
            moving = [(x.t, x.speed) for x in s
                      if x.speed is not None and x.gps_speed is not None
                      and x.gps_speed >= MOVE_KMH]
            mov_a, mov_b = _best_speed_g(moving, 1.0)

            f = lambda v: "  --  " if v is None else f"{v:6.3f}"
            print(f"{t.trip_uuid[:8]:<10}{(names.get(t.rider_store_id) or '?'):<13}{lag:>7.0f}%   "
                  f"{f(cur_a)}  {f(pre_a)}  {f(mov_a)}            "
                  f"{f(cur_b)}  {f(pre_b)}  {f(mov_b)}")

        print()
        print("stored values for the same trips, for reference:")
        for ru in raws:
            t = db.get(Trip, ru.trip_uuid)
            if t:
                print(f"   {t.trip_uuid[:8]}  accel_g={t.accel_g}  brake_g={t.brake_g}")
        print()
        print("Nothing was written. This is a comparison only.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
