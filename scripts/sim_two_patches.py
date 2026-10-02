#!/usr/bin/env python3
"""Ride two separate pockets of a city, so the roadtrip row has something to point at.

The fleet generator picks loops of 4 to 34 km through a city centre, which at z14 sweeps
across most of that city in one ride, so every simulated crew comes out as a single blob.
That left `links` -- "3 to link up", the move the whole design leads with -- correct, tested
and invisible: no crew in the demo world has ever had two patches to join.

This rides tight loops around two points a few kilometres apart instead. Each pocket earns
its own 2x2, the gap between them stays inside the four-square reach, and the crew's own
panel starts offering the road between them.

    python scripts/sim_two_patches.py --city Barcelona --gap 6 --rides 14
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.sim_fleet import (CITIES, WHEELS, EARTH, ride_csv, register,  # noqa: E402
                               upload)

# Short enough that a loop stays inside a square or two. The generator's own floor is 4 km,
# which already spans a tile at these latitudes, so this is as tight as the shape allows.
LOOP_KM = (5.0, 8.0)

# Loops all centred on one point make a plus, not a block: the middle column clears the lead
# floor and the corners do not, so nothing seeds and the pocket is awarded nothing at all.
# Scattering the centres over a square the size of a couple of tiles fills the corners.
SPREAD_KM = 1.6


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--city", default="Barcelona")
    ap.add_argument("--offset", type=float, default=12.0,
                    help="km from the city centre, to keep clear of the crew already there")
    ap.add_argument("--gap", type=float, default=11.0, help="km between the two pockets")
    ap.add_argument("--rides", type=int, default=22, help="rides per pocket")
    ap.add_argument("--days", type=int, default=10,
                    help="how far back to spread them; inside the join backfill window")
    ap.add_argument("--seed", type=int, default=91)
    ap.add_argument("--prefix", default="SIM7")
    args = ap.parse_args()

    city = next((c for c in CITIES if c[0].lower() == args.city.lower()), None)
    if not city:
        print("unknown city. Known:", ", ".join(c[0] for c in CITIES))
        return 2
    name, lat0, lon0, flag = city
    rng = random.Random(args.seed)

    # due east of the centre, then the second pocket due east again
    dlon = 1.0 / (EARTH * max(0.05, math.cos(math.radians(lat0))))
    west = (lat0, lon0 + args.offset * dlon)
    east = (lat0, lon0 + (args.offset + args.gap) * dlon)

    riders = [
        {"store_id": f"sim-2p-{args.seed}-a", "name": f"{args.prefix}·Westside",
         "home": west, "wheel": WHEELS[0], "cruise": 31.0},
        {"store_id": f"sim-2p-{args.seed}-b", "name": f"{args.prefix}·Eastside",
         "home": east, "wheel": WHEELS[3], "cruise": 29.0},
    ]

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    total = 0.0
    for r in riders:
        register(args.base, r["store_id"], r["name"], flag)
        for i in range(args.rides):
            km = rng.uniform(*LOOP_KM)
            start = now - timedelta(days=rng.uniform(0.2, args.days),
                                    hours=rng.uniform(0, 6))
            jy = rng.uniform(-SPREAD_KM, SPREAD_KM) / EARTH
            jx = rng.uniform(-SPREAD_KM, SPREAD_KM) * dlon
            csv_text, info = ride_csv(rng, r["home"][0] + jy, r["home"][1] + jx,
                                      start, km, r["cruise"])
            end = start + timedelta(seconds=info["rows"])
            res = upload(args.base, r["store_id"], csv_text, start, end, r["wheel"], 60)
            if res.get("error"):
                print(f"  {r['name']} ride {i + 1}: {res['error']}")
            else:
                total += info["km"]
        print(f"{r['name']:<22} {args.rides} rides around "
              f"{r['home'][0]:.4f},{r['home'][1]:.4f}")

    print(f"\n{total:.1f} km in two pockets {args.gap:.0f} km apart, near {name}.")
    print("Put both riders in one crew, then rebuild the territory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
