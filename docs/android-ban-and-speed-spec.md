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
