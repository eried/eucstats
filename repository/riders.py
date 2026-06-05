"""Rider/profile persistence + once-per-calendar-month change rules."""
from datetime import datetime

from models import Rider, utcnow

_FIELD_COL = {"name": "display_name", "flag": "flag", "avatar": "avatar_png"}


class RiderRepo:
    def __init__(self, db):
        self.db = db

    def get(self, store_id) -> Rider | None:
        return self.db.get(Rider, store_id)

    def upsert(self, store_id, platform, display_name, flag=None,
               avatar_png=None, consent_public=True) -> Rider:
        r = self.get(store_id)
        now = utcnow()
        if r is None:
            r = Rider(
                store_id=store_id, platform=platform, display_name=display_name,
                flag=flag, avatar_png=avatar_png, consent_public=consent_public,
                last_name_change=now,
                last_flag_change=now if flag else None,
                last_avatar_change=now if avatar_png else None,
            )
            self.db.add(r)
        else:
            # Re-registration of an ACTIVE account: refresh platform/consent only.
            # Name/flag/avatar changes go through apply_change() (monthly limits).
            # Closed accounts (deleted_at set) are NOT revived here — the API rejects
            # re-registration of a deleted store_id before reaching this path.
            r.platform = platform
            r.consent_public = consent_public
        self.db.commit()
        return r

    @staticmethod
    def _same_month(a: datetime, b: datetime) -> bool:
        return a.year == b.year and a.month == b.month

    def can_change(self, rider: Rider, field: str, now: datetime | None = None) -> bool:
        now = now or utcnow()
        last = getattr(rider, f"last_{field}_change")
        return last is None or not self._same_month(last, now)

    def apply_change(self, rider: Rider, field: str, value, now: datetime | None = None) -> Rider:
        now = now or utcnow()
        setattr(rider, _FIELD_COL[field], value)
        setattr(rider, f"last_{field}_change", now)
        self.db.commit()
        return rider

    def soft_delete(self, store_id) -> Rider | None:
        """Rider closed their own account: mark it closed but KEEP their public
        presence (name, flag, avatar, stats) on the portal — only an admin purge
        removes data. The app may stop showing the account; the leaderboards don't."""
        r = self.get(store_id)
        if r:
            r.deleted_at = utcnow()
            self.db.commit()
        return r
