"""Build a demo world: crews over the simulated fleet, then territory.

The scenario suite leaves behind a drawer of crews named after its own assertions, which is
fine for testing and useless for looking at. This wipes crew state (and only crew state — not
a single trip or rider is deleted) and builds something that reads like a real map: one or two
crews per city, members who actually ride there, and two cities deliberately contested so the
takeover-by-distance rule has somewhere to show itself.

    python scripts/seed_demo.py --riders 14 --seed 7

Run it after scripts/sim_fleet.py with the same --riders and --seed, so the crews it builds
match the fleet that exists.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import SessionLocal                                        # noqa: E402
from models import Clan, ClanCell, ClanMember, PairToken, Trip, WebSession, utcnow  # noqa: E402
from services import crews, settings, territory                         # noqa: E402
from sim_fleet import fleet                                             # noqa: E402

# A crew per city, named the way riders actually name things. Where a city has two, its riders
# are split between them and the map shows a contested border — which is the whole point of
# "easy takeover by distance" and impossible to see with one crew per place.
CREWS = {
    "Oslo":       [("Nordlys Collective", "Oslo, mostly after dark.", "open"),
                   ("Holmenkollen Climb", "Up is the only direction.", "approval")],
    "Tromso":     [("Polar Night Riders", "Three months without a sunrise.", "open")],
    "Copenhagen": [("Cykelslangen", "Bridges, bike lanes, bakeries.", "open"),
                   ("Harbour Loop", "Flat out along the water.", "invite")],
    "Berlin":     [("Ringbahn Runners", "One ring to ride them all.", "approval")],
    "Paris":      [("Peripherique", "Round and round and round.", "open")],
    "Barcelona":  [("Diagonal Drift", "Grid city, diagonal habits.", "open")],
    "New York":   [("Five Borough Crew", "Bridges are free real estate.", "approval")],
    "Austin":     [("Hill Country Hum", "Humid and hilly.", "open")],
    "Singapore":  [("Equator Express", "The tiles are enormous down here.", "open")],
    "Santiago":   [("Cordillera Sur", "The Andes are right there.", "open")],
    "Sydney":     [("Harbour Bridge Bombers", "Downhill both ways, somehow.", "open")],
}


def wipe_crew_state(db) -> dict:
    """Remove every crew, membership, held tile and session. Keeps all riders and trips.

    Deliberately not a database reset: the fleet took a while to upload and the trips are the
    expensive part. Only `Trip.clan_id` is cleared, which is the one piece of crew state that
    lives on a trip.
    """
    counts = {"crews": db.query(Clan).count(), "members": db.query(ClanMember).count(),
              "cells": db.query(ClanCell).count(),
              "stamped": db.query(Trip).filter(Trip.clan_id.isnot(None)).count()}
    db.query(ClanCell).delete()
    db.query(ClanMember).delete()
    db.query(Clan).delete()
    db.query(PairToken).delete()
    db.query(WebSession).delete()
    db.query(Trip).filter(Trip.clan_id.isnot(None)).update({Trip.clan_id: None},
                                                           synchronize_session=False)
    db.commit()
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--riders", type=int, default=14)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--keep", action="store_true", help="add to existing crews, do not wipe")
    ap.add_argument("--extra", action="append", default=[], metavar="SEED:RIDERS:CITY",
                    help="fold in another fleet, e.g. 11:6:Oslo. Use it for the cities this "
                         "file marks as contested: two crews with one rider each never meet, "
                         "and a border nobody shares is the one thing the takeover rule "
                         "cannot demonstrate. Upload the fleet first with sim_fleet.py "
                         "--city Oslo --riders 6 --seed 11.")
    a = ap.parse_args()

    db = SessionLocal()
    cfg = settings.get_crews(db)
    if not cfg["enabled"]:
        settings.set_crews(db, True, cfg["zoom"], cfg["window_days"], cfg["seed"],
                           cfg["cooldown_days"], cfg["max_members"], cfg["opacity"], True)
        print("crews switched on")
        cfg = settings.get_crews(db)

    if not a.keep:
        print("wiping crew state:", wipe_crew_state(db))

    riders = fleet(a.seed, a.riders)
    for spec in a.extra:
        try:
            sd, n, city = spec.split(":", 2)
            riders += fleet(int(sd), int(n), city)
        except ValueError:
            print(f"  ignoring --extra {spec!r}: expected SEED:RIDERS:CITY")
    by_city: dict[str, list] = {}
    for r in riders:
        by_city.setdefault(r["city"], []).append(r)
    # Interleave so a city's crews are split across fleets rather than one crew per fleet,
    # which would just move the problem: two groups riding two separate parts of town.
    for city in by_city:
        by_city[city].sort(key=lambda r: r["store_id"].rsplit("-", 1)[1])

    made = 0
    for city, members in by_city.items():
        specs = CREWS.get(city) or [(f"{city} Crew", f"Riding out of {city}.", "open")]
        # split the city's riders across its crews, so a contested city really is contested
        groups: list[list] = [[] for _ in specs]
        for i, m in enumerate(members):
            groups[i % len(specs)].append(m)
        for spec, group in zip(specs, groups):
            if not group:
                continue
            name, desc, policy = spec
            leader = group[0]
            ident = crews.suggest_identity(db)
            try:
                clan = crews.create(db, leader["store_id"], name=name, description=desc,
                                    colour=ident["colour"], pattern=ident["pattern"],
                                    join_policy=policy)
            except crews.CrewError as e:
                print(f"  skip {name}: {e.code} {e.detail}")
                continue
            made += 1
            for m in group[1:]:
                try:
                    mm = crews.join(db, m["store_id"], clan.clan_id,
                                    invite_code=clan.invite_code)
                    mm.status = "active"            # the demo world is already settled
                    mm.last_seen = utcnow()
                    db.commit()
                except crews.CrewError as e:
                    print(f"  skip member {m['name']}: {e.code}")
            # every rider's rides inside the window now belong to this crew
            ids = [m["store_id"] for m in group]
            n = (db.query(Trip)
                 .filter(Trip.rider_store_id.in_(ids),
                         Trip.validation_status == "validated")
                 .update({Trip.clan_id: clan.clan_id}, synchronize_session=False))
            db.commit()
            print(f"  {name:<26} {clan.colour} {clan.pattern:<8} "
                  f"{len(group)} riders, {n} rides")

    rep = territory.rebuild(db, window_days=cfg["window_days"], zoom=cfg["zoom"],
                           seed=cfg["seed"])
    print(f"\n{made} crews · territory: {rep}")
    print("\ntop crews by ground held:")
    for r in territory.ranking(db, limit=12):
        print(f"  {r['km2']:>8.1f} km2  {r['tiles']:>4} tiles  {r['colour']} "
              f"{r['pattern']:<8} {r['name']}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
