"""Rider identity/profile service: registration, monthly-limited edits,
avatar processing (64x64 PNG, EXIF stripped), delete/export."""
from __future__ import annotations

import re
from datetime import date
from io import BytesIO

from PIL import Image
from sqlalchemy import func

import config
from models import Rider, utcnow
from repository.riders import RiderRepo

NAME_MIN = 3
NAME_MAX = 20
_CTRL = re.compile(r"[\x00-\x1f\x7f]")        # control chars (newlines/tabs/etc.)
_WS = re.compile(r"\s+")


class InvalidName(ValueError):
    """Display name fails the length/format rules (message is user-facing)."""


def clean_display_name(raw) -> str:
    """Normalise a display name and enforce length 3..20 characters. Trims, collapses
    internal whitespace to single spaces, and strips control characters. Length is
    counted in characters (so an emoji counts as one). Raises InvalidName."""
    if raw is None:
        raise InvalidName("Please enter a display name")
    name = _WS.sub(" ", _CTRL.sub("", str(raw))).strip()
    n = len(name)
    if n < NAME_MIN:
        raise InvalidName(f"Display name must be at least {NAME_MIN} characters")
    if n > NAME_MAX:
        raise InvalidName(f"Display name must be {NAME_MAX} characters or fewer")
    return name


def _name_key(name) -> str:
    """Uniqueness comparison key: lower-cased with all whitespace removed."""
    return _WS.sub("", str(name)).lower()


def name_taken(db, cleaned: str, exclude_store_id=None) -> bool:
    """True if another rider already uses this display name (case- and
    space-insensitive). Stored names are already cleaned, so a SQL lower+strip-spaces
    compare matches _name_key()."""
    key = _name_key(cleaned)
    q = db.query(Rider.store_id).filter(
        func.replace(func.lower(Rider.display_name), " ", "") == key)
    if exclude_store_id is not None:
        q = q.filter(Rider.store_id != exclude_store_id)
    return db.query(q.exists()).scalar()


def purge_rider(db, store_id) -> bool:
    """Admin hard-delete: permanently remove a rider and ALL their data (trips,
    tracks, raw uploads, wheels, and every materialized row), then rebuild stats.
    Irreversible — unlike a rider's own account close, which keeps portal presence.
    Returns True if the rider existed."""
    from models import (DailyDistance, MapCellRider, RawUpload, Record, Rider,
                        RiderStat, TripTrack, Trip, Wheel)
    from services.aggregator import rebuild_all
    if db.get(Rider, store_id) is None:
        return False
    trip_ids = [t for (t,) in db.query(Trip.trip_uuid)
                .filter(Trip.rider_store_id == store_id).all()]
    if trip_ids:
        db.query(TripTrack).filter(TripTrack.trip_uuid.in_(trip_ids)).delete(synchronize_session=False)
        db.query(RawUpload).filter(RawUpload.trip_uuid.in_(trip_ids)).delete(synchronize_session=False)
    db.query(Trip).filter(Trip.rider_store_id == store_id).delete(synchronize_session=False)
    db.query(Wheel).filter(Wheel.rider_store_id == store_id).delete(synchronize_session=False)
    db.query(RiderStat).filter(RiderStat.store_id == store_id).delete(synchronize_session=False)
    db.query(DailyDistance).filter(DailyDistance.store_id == store_id).delete(synchronize_session=False)
    db.query(MapCellRider).filter(MapCellRider.store_id == store_id).delete(synchronize_session=False)
    db.query(Record).filter(Record.store_id == store_id).delete(synchronize_session=False)
    db.query(Rider).filter(Rider.store_id == store_id).delete(synchronize_session=False)
    db.commit()
    rebuild_all(db)                     # recompute records / map / country cleanly
    return True


class ChangeNotAllowed(Exception):
    def __init__(self, field: str, allowed_after: str | None):
        super().__init__(f"{field} can be changed again after {allowed_after}")
        self.field = field
        self.allowed_after = allowed_after


def process_avatar(raw: bytes, px: int | None = None) -> bytes:
    """Re-encode any image to a px*px PNG. Re-encoding drops EXIF/GPS metadata."""
    px = px or config.AVATAR_PX
    im = Image.open(BytesIO(raw)).convert("RGBA").resize((px, px))
    out = BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def _next_month_start(d: date) -> date:
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def _allowed_after(last) -> str | None:
    return _next_month_start(last).isoformat() if last else None


class IdentityService:
    def __init__(self, db):
        self.db = db
        self.repo = RiderRepo(db)

    def register(self, store_id, platform, display_name, flag=None,
                 avatar_bytes=None, consent_public=True) -> Rider:
        avatar_png = process_avatar(avatar_bytes) if avatar_bytes else None
        return self.repo.upsert(store_id, platform, display_name, flag,
                                avatar_png, consent_public)

    def get_profile(self, store_id) -> dict | None:
        import services.settings as settings
        r = self.repo.get(store_id)
        if r is None or r.deleted_at:
            return None
        return {
            "store_id": r.store_id,
            "platform": r.platform,
            "display_name": r.display_name,
            "flag": r.flag,
            "has_avatar": r.avatar_png is not None,
            "consent_public": r.consent_public,
            "banned": settings.is_banned(self.db, store_id),       # show a suspension notice on load
            "ban_reason": settings.ban_reason(self.db, store_id),   # human-readable; null when not banned
            "can_change_name_after": _allowed_after(r.last_name_change),
            "can_change_flag_after": _allowed_after(r.last_flag_change),
            "can_change_avatar_after": _allowed_after(r.last_avatar_change),
        }

    def update(self, store_id, field, value, now=None) -> Rider:
        now = now or utcnow()
        r = self.repo.get(store_id)
        if r is None or r.deleted_at:
            raise KeyError("rider not found")
        if not self.repo.can_change(r, field, now):
            last = getattr(r, f"last_{field}_change")
            raise ChangeNotAllowed(field, _allowed_after(last))
        if field == "avatar":
            value = process_avatar(value)
        return self.repo.apply_change(r, field, value, now)

    def delete(self, store_id):
        return self.repo.soft_delete(store_id)

    def export(self, store_id) -> dict | None:
        # Extended with the rider's trips in the public-site/stats plan.
        return self.get_profile(store_id)
