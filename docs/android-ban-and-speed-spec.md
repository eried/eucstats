# eucstats → Android app: ban handling + speed/freespin (brief)

Base URL: `https://eucstats.ried.no/api/v1`

## 1. Banned (suspended) accounts

A rider can be suspended server-side (admin action: fraud, GPS spoofing, abuse).
Bans are reversible and account-level — they are **not** a per-trip verdict.

### On profile load — show a suspension notice

Both profile endpoints now include two fields:

`GET /riders/{store_id}` (and `GET /riders/{store_id}/card`) →
```json
{
  "store_id": "…",
  "display_name": "…",
  "banned": false,
  "ban_reason": null
}
```
When suspended:
```json
{ "banned": true, "ban_reason": "Violation of fair-use / anti-fraud policy" }
```

**App behaviour:** when `banned == true`, show a non-dismissable notice on the
profile screen using `ban_reason` (e.g. *“Your account is suspended: <reason>.
Contact support if you think this is a mistake.”*), and disable trip upload.
`ban_reason` is always a non-empty human-readable string when `banned` is true,
and `null` otherwise.

### On trip upload — uploads are refused

`POST /trips` from a banned rider returns:
```
HTTP 403
{ "detail": "rider_banned" }
```
**App behaviour:** treat `403 rider_banned` as terminal — do **not** retry/queue;
mark the trip as not-submittable and surface the same suspension notice. (Other
upload errors are unchanged: `401 attestation_failed:*`, `403 rider_not_allowlisted`,
`400 rider_not_registered`, `409`/200 duplicate, `413 payload_too_large`.)

> Note: the existing per-trip `validation_status` / `verdict`
> (`validated`→`accepted`, `flagged`→`under_review`, `rejected`) is unchanged.
> A ban is separate and outranks it (the upload never reaches verdict stage).

## 2. Top speed vs. freespin (FYI — no app change required)

The server now derives the **realistic top speed** from a believable
acceleration ramp (~20 km/h/s; EUCs reach 100 km/h in well over 5 s) and
cross-checks wheel speed against GPS speed per sample. An instantaneous spike
with no ramp — a sensor glitch or a **freespin** (wheel lifted and spun) — is
excluded from `max_speed` and recorded separately as a “freespin” value.

Nothing to change in the upload contract: keep sending **both wheel speed and
GPS speed per sample** (the cross-check needs both). Sending `0,0` for a missing
GPS fix is fine and expected. The more honest per-sample GPS speed you send, the
better the server can separate real speed from freespin.

## 3. Rate limiting — handle HTTP 429 (retry, don't drop)

To stop floods/abuse the server caps request frequency. A normal rider never hits
these (defaults: **60 uploads/hour per rider**, 200/hour per IP, 20 new accounts/hour
per IP). When a cap is hit the response is **HTTP 429** with a body telling you which:

| Endpoint | Body when limited | What it means |
|---|---|---|
| `POST /trips` | `{ "detail": "rate_limited:trip_per_rider" }` | this rider uploaded too many trips this hour |
| `POST /trips` | `{ "detail": "rate_limited:trip_per_ip" }` | too many uploads from this network/IP |
| `POST /riders` | `{ "detail": "rate_limited:rider_create" }` | too many **new** accounts from this IP (re-registering an existing `store_id` is never limited) |

**How to handle — 429 is temporary, not a failure:**
- **Trip upload (`429`):** do **NOT** discard the trip. Keep it in the local upload
  queue and **retry later with backoff** (the window is a rolling hour, so a slot
  frees up over time). Suggested: retry after a few minutes, then back off
  progressively; also just retry the queue on next app launch / next ride. Keep it
  **silent** to the rider (optionally a subtle “waiting to sync” indicator) — their
  ride will upload, they don't need to see an internal cap.
- **Registration (`429`):** rare for a real user. Don't block them permanently — show
  a gentle “couldn't finish setup, retrying…” and retry after a short delay.

**Decision table for the app (all upload outcomes):**
| Status | Meaning | App action |
|---|---|---|
| `201` / `200` | accepted / duplicate | done (200 = already had it) |
| `429` | rate limited | **queue + retry with backoff** (silent) |
| `403 rider_banned` | account suspended | terminal — stop, show suspension notice |
| `401 attestation_failed:*` | attestation rejected | terminal (or refresh token, then retry once) |
| `403 rider_not_allowlisted` | not on allowlist (test period) | terminal for now |
| `400 rider_not_registered` / `missing_*` | bad request | register first / fix payload; don't blind-retry |
| `413 payload_too_large` | file too big | don't retry as-is |
| `422 *` | parse/checksum/no-samples | don't retry as-is (data problem) |

Rule of thumb: **only 429 (and a one-shot 401 token refresh) should auto-retry.**
Everything else is terminal for that upload. We don't send a `Retry-After` header today
— use your own backoff; tell us if you'd prefer we add one.
