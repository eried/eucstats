#!/usr/bin/env python3
"""Add a wheel brand/model correction rule and apply it to the wheels already stored.

    cd /opt/eucstats && .venv/bin/python scripts/fix_wheel_brand.py
    cd /opt/eucstats && .venv/bin/python scripts/fix_wheel_brand.py --apply

Brand and model are not measured. The app reports them when a ride ends, guessing from the
Bluetooth connection, and where it guesses wrong the BLE name still says what it really
connected to, because that string comes from the wheel itself.

MackNificent's wheel was stored as "KingSong / Panther", which is not a product anybody
sells. Its BLE name is GotWay_010860 and its firmware GW2026201 - a Begode, the same
signature as JonahOnEUC's GotWay_000572 that the app labelled Begode correctly. The owner
confirmed it: "I only have a Begode Panther and a LeaperKim Oryx." So the model was right all
along and the brand was not.

The rule is stored, so future uploads from that wheel are corrected on the way in, and
apply_name_fixes rewrites the wheel already in the database. A dataset snapshot is taken
first, exactly as the admin button does, because rewriting stored names is destructive.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RULES = [
    {"m_brand": "KingSong", "m_model": "Panther", "set_brand": "Begode", "set_model": "Panther"},
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write it (default is a dry run)")
    args = ap.parse_args()

    from database import SessionLocal
    from services import settings
    from services.aggregator import rebuild_all

    db = SessionLocal()
    try:
        existing = settings.get_name_rules(db)
        merged = existing + [r for r in RULES if r not in existing]
        changes = settings.name_fix_changes(db, merged)
        print(f"existing rules: {len(existing)}, adding {len(merged) - len(existing)}")
        print(f"wheels this rewrites: {len(changes)}")
        for c in changes:
            print(f"   {c['brand']} / {c['model']}  ->  {c['new_brand']} / {c['new_model']}")
        if not args.apply:
            print("\ndry run, nothing written. Re-run with --apply.")
            return 0

        try:                                      # the same safety net the admin button uses
            from services import datasets
            datasets.save_current(datasets._timestamped("pre-namefix"),
                                  note="before Begode Panther brand fix", origin="pre-edit")
            print("\ndataset snapshot saved")
        except Exception as e:
            print(f"\nsnapshot failed ({e}) - refusing to rewrite names unprotected", file=sys.stderr)
            return 1

        settings.set_name_rules(db, merged)
        done = settings.apply_name_fixes(db)
        print(f"renamed {len(done)} wheel(s)")
        rebuild_all(db)                           # brand boards and group standings
        print("aggregates rebuilt")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
