"""Rider identity/profile service: registration, monthly-limited edits,
avatar processing (64x64 PNG, EXIF stripped), delete/export."""
from __future__ import annotations

from datetime import date
from io import BytesIO

from PIL import Image

import config
from models import Rider, utcnow
from repository.riders import RiderRepo


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
