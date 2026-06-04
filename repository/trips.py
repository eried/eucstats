"""Trip / track / raw-upload persistence + retention helpers."""
from datetime import datetime, timedelta

from models import Trip, TripTrack, RawUpload


class TripRepo:
    def __init__(self, db):
        self.db = db

    def get(self, trip_uuid) -> Trip | None:
        return self.db.get(Trip, trip_uuid)

    def exists(self, trip_uuid) -> bool:
        return self.db.get(Trip, trip_uuid) is not None

    def insert_trip(self, **kw) -> Trip:
        t = Trip(**kw)
        self.db.add(t)
        self.db.commit()
        return t

    def set_status(self, trip_uuid, status, reasons=None) -> Trip:
        t = self.get(trip_uuid)
        t.validation_status = status
        t.flag_reasons = reasons
        self.db.commit()
        return t

    def save_track(self, trip_uuid, points: bytes):
        self.db.merge(TripTrack(trip_uuid=trip_uuid, points=points))
        self.db.commit()

    def save_raw(self, trip_uuid, blob: bytes):
        self.db.merge(RawUpload(trip_uuid=trip_uuid, blob=blob, bytes=len(blob)))
        self.db.commit()

    def delete_raw(self, trip_uuid):
        ru = self.db.get(RawUpload, trip_uuid)
        if ru:
            self.db.delete(ru)
            self.db.commit()

    def evictable_by_age(self, now: datetime, retention_days: int):
        cutoff = now - timedelta(days=retention_days)
        # evict ANY raw older than the cutoff (incl. flagged/rejected — summaries
        # and tracks are kept regardless, so review data survives within the window)
        return self.db.query(RawUpload).filter(RawUpload.received_at < cutoff).all()

    def oldest_raw(self, limit: int = 50):
        return (self.db.query(RawUpload)
                .order_by(RawUpload.received_at.asc()).limit(limit).all())
