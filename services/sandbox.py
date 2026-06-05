"""Sandbox test responses (Stripe-test-card style) for the Android team.

When the admin enables sandbox mode, reserved `sandbox-*` store_ids short-circuit
/riders and /trips to a deterministic response so the app can exercise EVERY error /
success path against the real endpoints. Disabled by default; `sandbox-` never
collides with real riders (those are UUIDs), so leaving these store_ids in client
test code is harmless when sandbox is off.

Each case: (store_id, http_status, payload, human description).
  - status 201  -> success; `payload` is the validation_status (verdict derived)
  - other       -> error;   `payload` is the `detail` string returned
"""
from __future__ import annotations

CASES = [
    ("sandbox-ok",           201, "validated", "success — trip accepted (verdict accepted)"),
    ("sandbox-flagged",      201, "flagged",   "success but held for review (verdict under_review)"),
    ("sandbox-rejected",     201, "rejected",  "success but rejected (verdict rejected)"),
    ("sandbox-400",          400, "sandbox_bad_request",        "400 bad request"),
    ("sandbox-401",          401, "attestation_failed:sandbox", "401 attestation rejected"),
    ("sandbox-banned",       403, "rider_banned",               "403 account suspended"),
    ("sandbox-allowlist",    403, "rider_not_allowlisted",      "403 not on the allowlist"),
    ("sandbox-unregistered", 400, "rider_not_registered",       "400 rider not registered"),
    ("sandbox-413",          413, "payload_too_large",          "413 file too large"),
    ("sandbox-422",          422, "parse_failed:sandbox",       "422 corrupt / unparseable data"),
    ("sandbox-429",          429, "rate_limited:sandbox",       "429 rate limited — retry later"),
]
_BY_ID = {c[0]: c for c in CASES}

VERDICT = {"validated": "accepted", "flagged": "under_review", "rejected": "rejected"}


def case(store_id: str | None):
    """Return the (store_id, status, payload, desc) tuple for a magic id, else None."""
    return _BY_ID.get((store_id or "").strip())
