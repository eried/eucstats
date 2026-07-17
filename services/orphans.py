"""Trips that arrived with no wheel identity.

The app snapshots the wheel's brand/model/serial/MAC when a ride ends. If the wheel
already disconnected by then (rider powered it off), that snapshot can come back empty,
and the upload carries no `serial`/`ble_mac` for `wheel_id` to be built from. Such a trip
still counts for the rider, but `by_wheel`/`by_brand` inner-join `wheels`, so it is
invisible on every wheel and brand board — the model looks smaller than it really is.

These helpers surface those trips and attach them back to a wheel. Attaching is safe to
do at any time: the group boards read `trips JOIN wheels` live, so no rebuild is needed.
"""
from sqlalchemy import func, or_

from models import Rider, Trip, Wheel

_NO_WHEEL = or_(Trip.wheel_id.is_(None), Trip.wheel_id == "")


def orphan_count(db) -> int:
    return db.query(func.count(Trip.trip_uuid)).filter(_NO_WHEEL).scalar() or 0


def orphan_km(db) -> float:
    return round(db.query(func.coalesce(func.sum(Trip.distance_km), 0.0)).filter(_NO_WHEEL).scalar() or 0.0, 1)


def orphan_trips(db, limit: int = 500) -> list:
    """Every wheel-less trip, newest first, with the rider it belongs to."""
    rows = (db.query(Trip, Rider.display_name)
            .outerjoin(Rider, Rider.store_id == Trip.rider_store_id)
            .filter(_NO_WHEEL).order_by(Trip.start_utc.desc()).limit(limit).all())
    return [{"trip_uuid": t.trip_uuid, "store_id": t.rider_store_id, "rider": name,
             "km": round(t.distance_km or 0, 2), "start_utc": t.start_utc,
             "status": t.validation_status, "app_version": t.app_version,
             "why": _why(t)} for t, name in rows]


def _why(trip) -> str:
    """What the upload actually carried, from the diagnostic the ingest stores.

    'no wheel data' means every field was blank — a phone-only ride, or the wheel had
    already disconnected. Anything else means identity arrived but without a serial or
    MAC to key on, which is a bug worth chasing.
    """
    meta = trip.meta_json or {}
    if "wheel_unresolved" not in meta:
        return "unknown (uploaded before diagnostics)"
    w = meta.get("wheel_unresolved") or {}
    if not w:
        return "no wheel data (phone-only ride, or wheel already disconnected)"
    seen = ", ".join(f"{k}={v}" for k, v in sorted(w.items()))
    return f"partial, no serial/MAC: {seen}"


def _wheels_of(db, store_id) -> list:
    return db.query(Wheel).filter(Wheel.rider_store_id == store_id).all()


def orphan_summary(db) -> list:
    """Orphans grouped by rider, with that rider's wheels as attach candidates.

    `auto` marks riders we can attach without guessing — they own exactly one wheel, so
    there is only one answer. Riders with several wheels need a human to pick.
    """
    rows = (db.query(Trip.rider_store_id, Rider.display_name,
                     func.count(Trip.trip_uuid), func.coalesce(func.sum(Trip.distance_km), 0.0))
            .outerjoin(Rider, Rider.store_id == Trip.rider_store_id)
            .filter(_NO_WHEEL).group_by(Trip.rider_store_id, Rider.display_name)
            .order_by(func.sum(Trip.distance_km).desc()).all())
    out = []
    for store_id, name, n, km in rows:
        ws = _wheels_of(db, store_id)
        out.append({
            "store_id": store_id, "rider": name, "trips": n, "km": round(km or 0, 1),
            "wheels": [{"wheel_id": w.wheel_id, "brand": w.brand, "model": w.model} for w in ws],
            "auto": len(ws) == 1,
        })
    return out


def attach_auto(db) -> list:
    """Attach every orphan whose rider owns exactly one wheel — the only unambiguous case.

    Returns the (trip_uuid, wheel_id) pairs applied, so the caller can audit them.
    """
    sole = {}
    for r in orphan_summary(db):
        if r["auto"]:
            sole[r["store_id"]] = r["wheels"][0]["wheel_id"]
    if not sole:
        return []
    done = []
    for t in db.query(Trip).filter(_NO_WHEEL, Trip.rider_store_id.in_(sole)).all():
        t.wheel_id = sole[t.rider_store_id]
        done.append((t.trip_uuid, t.wheel_id))
    db.commit()
    return done


def attach_one(db, trip_uuid: str, wheel_id: str) -> bool:
    """Attach a single orphan to one of its own rider's wheels.

    Refuses when the trip already has a wheel, or when the wheel belongs to someone
    else — a cross-rider attach would invent data rather than recover it.
    """
    t = db.get(Trip, trip_uuid)
    if t is None or t.wheel_id:
        return False
    w = db.get(Wheel, wheel_id)
    if w is None or w.rider_store_id != t.rider_store_id:
        return False
    t.wheel_id = wheel_id
    db.commit()
    return True
