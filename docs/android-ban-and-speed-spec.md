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

## 4. Sandbox test responses (QA) — magic store_ids

The admin can flip on **Sandbox** mode (Settings → "Sandbox test responses"). While it's
on, set a rider's `store_id` to one of these reserved values to make `POST /api/v1/riders`
and `POST /api/v1/trips` return that exact response — Stripe-test-card style. They never
collide with real riders (real ones are UUIDs), and the full live list is printed on that
Settings page so QA always has it.

| store_id | response |
|---|---|
| `sandbox-ok` | 201 — `validation_status: validated` (verdict `accepted`) |
| `sandbox-flagged` | 201 — `flagged` (verdict `under_review`) |
| `sandbox-rejected` | 201 — `rejected` |
| `sandbox-400` | 400 `sandbox_bad_request` |
| `sandbox-401` | 401 `attestation_failed:sandbox` |
| `sandbox-banned` | 403 `rider_banned` |
| `sandbox-allowlist` | 403 `rider_not_allowlisted` |
| `sandbox-unregistered` | 400 `rider_not_registered` |
| `sandbox-413` | 413 `payload_too_large` |
| `sandbox-422` | 422 `parse_failed:sandbox` |
| `sandbox-429` | 429 `rate_limited:sandbox` |

Notes: these short-circuit BEFORE attestation/rate-limit/parse, so you can fire them with
any dummy payload. A `sandbox-*` registration returns a synthetic profile (`"sandbox": true`)
and does NOT create a real rider. When sandbox is OFF, these store_ids behave like any normal
(unregistered) id, so leaving them in your test suite is safe.

## 5. Display-name rules — validate before submit, handle 422 / 409

`POST /riders` (new accounts) and `PATCH /riders/{store_id}` (name edits) now enforce
display-name rules. Validate client-side too, for instant feedback.

| Rule | Detail |
|---|---|
| Length | **3–20 characters** (counted in characters, so an emoji = 1). |
| Cleaning | We trim ends, collapse runs of whitespace to one space, and strip control characters. The **cleaned** name is what gets stored — show the user the cleaned result. |
| Uniqueness | Names are **unique, case- and space-insensitive**: `"John Doe"`, `"johndoe"`, `"JOHN DOE"` all collide. |

Responses:
- `422` body `display_name must be at least 3 characters` / `… at most 20 characters` — too short/long. Show inline.
- `409` body `display_name_taken` — name already used by someone else. Ask for another.

Important nuances:
- **Re-registration keeps the existing name.** Calling `POST /riders` again with the same
  `store_id` ignores the `display_name` field entirely (no rename, no 422/409). To rename,
  use `PATCH` — which is still limited to **once per calendar month** (returns `429` with a
  message + the date it can change again).
- `PATCH` name edits are checked in this order: format (`422`) → uniqueness (`409`) →
  monthly limit (`429`). So a too-short or taken name is rejected even if the rider is
  inside their monthly cooldown.
