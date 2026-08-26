"""Incognito riders on speed boards.

A public top-speed entry can be evidence against the rider who set it. Where a jurisdiction
has a speed the rider is meant to stay under, an entry above it is published without an
identity: no name, no flag, no avatar, no rider id, and no coordinates.

The anonymity is server-side on purpose. Blurring or masking in the page leaves the real
values in the DOM and in the API response, one devtools panel away from anyone who wants
them, so nothing that identifies the rider is put in the response at all.

What replaces it is derived from a secret salt held only here, so it is stable (the same
rider keeps the same alias and mark everywhere) but cannot be run backwards into a rider id.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

_SALT_KEY = "speed_privacy_salt"


def _salt(db) -> bytes:
    """Per-install secret behind every alias. Generated once, never leaves the server."""
    from services.settings import get_meta, set_meta
    v = get_meta(db, _SALT_KEY, None)
    if not v:
        v = secrets.token_hex(32)
        set_meta(db, _SALT_KEY, v)
    return v.encode()


def token(db, store_id: str) -> str:
    """Stable, non-reversible handle for one rider."""
    return hmac.new(_salt(db), (store_id or "").encode(), hashlib.sha256).hexdigest()


def alias(tok: str, kind: str = "Rider") -> str:
    """Display name for an anonymous entry, e.g. "Rider #4F2A" or "Country #B7C1"."""
    return kind + " #" + tok[:4].upper()


def is_incognito(db, country: str | None, speed_kmh: float | None) -> bool:
    """Should a speed entry from `country` at `speed_kmh` be published without an identity?

    A country with no configured limit never triggers this: the site would otherwise be
    guessing at a law it does not know, and guessing wrong in public. Riders anywhere can
    still be made incognito by hand.
    """
    from services.settings import speed_privacy_limit
    if speed_kmh is None:
        return False
    limit = speed_privacy_limit(db, country)
    return limit is not None and speed_kmh > limit


def brief(db, base: dict, country: str | None, speed_kmh: float | None,
          reveal: bool = False) -> dict:
    """A `_rider_brief` dict, stripped of identity when the entry qualifies.

    Coordinates go too. A rider's last start point is their street; leaving it beside an
    anonymous entry would identify them more precisely than the name ever did.

    `reveal` is for a signed-in admin only. It keeps the real identity in the payload and
    just marks the entry as incognito, so the admin panel's eye toggle can switch between
    what they see and what a visitor sees. For everyone else the identity is never sent,
    which is the whole point: a client-side mask leaves the real values one devtools panel
    away from anybody.
    """
    if not is_incognito(db, country, speed_kmh):
        return base
    tok = token(db, base.get("store_id") or "")
    marks = {"anon": True, "mark": tok[:16], "alias": alias(tok)}
    if reveal:
        return {**base, **marks}
    return {"store_id": None, "name": alias(tok), "flag": None, "has_avatar": False,
            "lat": None, "lon": None, **marks}
