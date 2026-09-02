#!/usr/bin/env python3
"""Merge wheels that are one physical wheel stored under two identities.

    cd /opt/eucstats && .venv/bin/python scripts/merge_split_wheels.py
    cd /opt/eucstats && .venv/bin/python scripts/merge_split_wheels.py --apply

Wheel identity was `serial or ble_mac`, so a wheel first seen by its MAC became a SECOND
wheel the day the app started reporting a serial for it. The rider's trips then split across
two wheels on every board that groups by wheel.

Two rows are the same wheel when they belong to the same rider and share a BLE name - that
name carries the wheel's own serial suffix (P6-600259A6 against serial A14219B0600259A6), so
it is not a coincidence of naming. Anything less certain than that is left alone and printed.

The surviving row is the one with more trips; the other's trips are reassigned to it, and
every id either row answered to is remembered against the survivor so a later upload using
the retired id still finds it. A dataset snapshot is taken first.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write it (default is a dry run)")
    args = ap.parse_args()

    from database import SessionLocal
    from models import Rider, Trip, Wheel
    from services.aggregator import rebuild_all

    db = SessionLocal()
    try:
        names = {r.store_id: r.display_name for r in db.query(Rider).all()}
        counts: dict = {}
        for (wid,) in db.query(Trip.wheel_id).filter(Trip.wheel_id.isnot(None)).all():
            counts[wid] = counts.get(wid, 0) + 1

        groups: dict = {}
        for w in db.query(Wheel).all():
            if w.ble_name:
                groups.setdefault((w.rider_store_id, w.ble_name), []).append(w)

        merges = [(k, v) for k, v in groups.items() if len(v) > 1]
        if not merges:
            print("no split wheels found")
            return 0

        plan = []
        for (store, ble), ws in merges:
            ws.sort(key=lambda w: -counts.get(w.wheel_id, 0))
            keep, drop = ws[0], ws[1:]
            print(f"{names.get(store, '?')}  {ble}  ({keep.brand} / {keep.model})")
            print(f"   keep {keep.wheel_id}  {counts.get(keep.wheel_id, 0)} trips")
            for d in drop:
                print(f"   fold {d.wheel_id}  {counts.get(d.wheel_id, 0)} trips")
            plan.append((keep, drop))

        moving = sum(counts.get(d.wheel_id, 0) for _, ds in plan for d in ds)
        print(f"\n{len(plan)} wheel(s) to merge, {moving} trip(s) reassigned")
        if not args.apply:
            print("dry run, nothing written. Re-run with --apply.")
            return 0

        try:                                      # the same safety net every bulk edit uses
            from services import datasets
            datasets.save_current(datasets._timestamped("pre-wheelmerge"),
                                  note="before merging split wheel identities", origin="pre-edit")
            print("dataset snapshot saved")
        except Exception as e:
            print(f"snapshot failed ({e}) - refusing to merge unprotected", file=sys.stderr)
            return 1

        for keep, drop in plan:
            alts = set()
            for w in [keep] + drop:
                alts.add(w.wheel_id)
                try:
                    alts |= set(json.loads(w.alt_keys) or [])
                except (TypeError, ValueError):
                    pass
            for d in drop:
                db.query(Trip).filter(Trip.wheel_id == d.wheel_id).update(
                    {"wheel_id": keep.wheel_id}, synchronize_session=False)
                db.delete(d)
            keep.alt_keys = json.dumps(sorted(alts))
            if not keep.firmware:                 # keep whatever either row knew
                keep.firmware = next((d.firmware for d in drop if d.firmware), None)
        db.commit()
        print(f"merged {len(plan)} wheel(s)")
        rebuild_all(db)
        print("aggregates rebuilt")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
