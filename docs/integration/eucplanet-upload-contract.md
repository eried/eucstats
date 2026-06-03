# eucplanet ↔ eucstats — Integration Brief

> **How to use this document:** This is a self-contained brief/prompt for the agent (or developer) building the **eucplanet Android app**. It defines the exact contract eucplanet must implement to register riders and upload trips to the **eucstats** backend. Implement the client side against the API described here. Items marked **(confirm)** are assumptions the eucstats backend will finalize; flag them if infeasible on the app side.
>
> Backend design reference: `docs/superpowers/specs/2026-06-03-eucstats-foundation-design.md`.

---

## 0. Goal

eucstats hosts **public** EUC-rider stats (leaderboards, a world activity map, podiums with name/flag/avatar). eucplanet is the data source: it registers the rider's identity and, after every trip, uploads a trip report. Uploads must be **trustworthy** (genuine app + plausible telemetry) and **idempotent** (safe to retry / backfill).

**Two responsibilities for the app:**
1. **Identity** — register/maintain the rider's public profile (display name, flag, 64×64 avatar).
2. **Trip upload** — after a trip is saved, build a report and POST it; also offer to backfill historical trips.

---

## 1. Conventions

- **Base URL:** `https://eucstats.ried.no/api/v1` (HTTPS only).
- **Format:** JSON request/response, UTF-8. Trip files uploaded via `multipart/form-data`.
- **Units are metric and canonical:** km, km/h, V, A, W, °C, g. Do **not** convert to imperial before upload (the website handles display conversion).
- **Timestamps:** ISO-8601 UTC (`2026-06-01T20:24:31.204Z`) in the envelope. The raw CSV keeps its original local time; the envelope supplies the timezone (see §4).
- **Auth model:** there are no passwords. Every state-changing call carries a **Play Integrity attestation** (§2) that proves the request comes from the genuine, unmodified eucplanet app.

---

## 2. Attestation (anti-fraud) — REQUIRED

Use the **Google Play Integrity API** (Standard requests) on registration, profile edits, and every trip upload.

- For each request, compute a **`request_hash`** = SHA-256 of the canonical JSON envelope bytes (for trip uploads, hash the `meta` object exactly as sent). Pass it as the Standard request's `requestHash` so the token is bound to *this* request (prevents replay/swapping).
- Send the resulting **integrity token** in the envelope:
  ```json
  "attestation": { "type": "play_integrity", "token": "<integrity token>", "request_hash": "<hex sha256>" }
  ```
- The backend decodes the token with Google, checking app integrity, device integrity, and that `requestHash` matches the received payload. Tokens are short-lived — generate a fresh one per request; on `401` re-mint and retry once.
- **iOS (future):** the same envelope field will carry `{ "type": "app_attest", ... }`. Build the attestation as a small, swappable module.

**GPS spoofing backstop:** Play Integrity proves the *app* is genuine, not that GPS is real. The app MUST also report whether any location sample came from a mock provider — set `is_mock_location: true` if Android's `Location.isFromMockProvider()` (or `isMock` on API 31+) was true for any fix in the trip.

---

## 3. Identity / profile endpoints

### `POST /riders` — register or upsert the rider
```json
{
  "store_id": "<stable Google Play account identifier the app uses>",   // REQUIRED, stable per user (confirm source)
  "platform": "google_play",
  "display_name": "RiderName",
  "flag": "NO",                         // ISO-3166-1 alpha-2 country
  "avatar_png_base64": "<optional>",    // any square-ish image; server re-encodes to 64x64 PNG, strips EXIF
  "consent_public": true,               // user opted in to public stats (see §8)
  "attestation": { ... }                // §2
}
```
Returns the stored profile + `can_change_after` dates (see below).

### `GET /riders/{store_id}` — fetch current profile (drives the app UI)
Returns:
```json
{
  "store_id": "...", "display_name": "...", "flag": "NO",
  "avatar_url": "https://.../avatars/...png",
  "can_change_name_after":  "2026-07-01",
  "can_change_flag_after":  "2026-07-01",
  "can_change_avatar_after":"2026-07-01"
}
```
Use the `can_change_*` dates to enable/disable the edit controls and show friendly messaging ("You can change your name again on Jul 1").

### `PATCH /riders/{store_id}` — change name / flag / avatar
Each of name, flag, avatar is editable **once per calendar month**, enforced server-side. Pre-check with `GET` and gate the UI; still handle a `409`/`429` if the user races the limit.

### `DELETE /riders/{store_id}` — delete account + data (GDPR)
### `GET /riders/{store_id}/export` — self data export (GDPR)
Expose both in the app's settings/privacy screen.

---

## 4. Trip upload — `POST /trips`

`multipart/form-data` with two parts:
- **`meta`** — the JSON envelope (below).
- **`trip`** — the **raw trip CSV, gzipped** (`Content-Type: application/gzip`). Send the file *as captured* — do NOT reformat or strip columns; the backend has a header-driven parser. Files are ~2 KB–500 KB raw.

### The `meta` envelope
| Field | Type | Req | Notes |
|---|---|:--:|---|
| `store_id` | string | ✅ | Same identity as registration |
| `platform` | enum | ✅ | `google_play` \| `apple` |
| `trip_uuid` | string (UUID) | ✅ | Stable & unique per trip — see §5 |
| `source_app` | enum | ✅ | `eucplanet` \| `darknessbot` \| `euc_world` \| `other` |
| `schema_version` | string | ✅ | Identifies the CSV column layout (see §6) |
| `start_utc` | ISO-8601 Z | ✅ | Trip start in UTC |
| `end_utc` | ISO-8601 Z | ✅ | Trip end in UTC |
| `tz` | string | ✅ | IANA zone (`Europe/Oslo`) or UTC-offset minutes |
| `tz_known` | bool | ✅ | `false` for legacy imports with unknown tz → backend excludes from tz-sensitive boards |
| `wheel` | object | ✅ | See §7 |
| `is_mock_location` | bool | ✅ | §2 |
| `app_version` | string | ✅ | eucplanet version |
| `os_version` | string | ➖ | Android version |
| `sample_count` | int | ✅ | Rows in the CSV (post-header) |
| `file_sha256` | string | ✅ | SHA-256 of the **uncompressed** CSV bytes (integrity) |
| `distance_km_client` | number | ➖ | Client-computed trip distance, for cross-check (optional) |
| `attestation` | object | ✅ | §2 |

### Example
```json
{
  "store_id": "g_8f3a...", "platform": "google_play",
  "trip_uuid": "0f1c2d34-...-9abc",
  "source_app": "eucplanet", "schema_version": "eucplanet-v3-gforce",
  "start_utc": "2026-06-01T18:24:31Z", "end_utc": "2026-06-01T19:10:02Z",
  "tz": "Europe/Oslo", "tz_known": true,
  "wheel": { "brand": "Begode", "model": "Master", "serial": "F4E02AB02A75",
             "ble_mac": "F4:E0:2A:B0:2A:75", "ble_name": "GW...", "firmware": "1.07" },
  "is_mock_location": false,
  "app_version": "3.4.1", "os_version": "Android 16",
  "sample_count": 2731, "file_sha256": "ab12...",
  "attestation": { "type": "play_integrity", "token": "ey...", "request_hash": "9f86..." }
}
```

---

## 5. `trip_uuid` generation (idempotency key)

The backend dedupes on `trip_uuid` — re-uploading the same id returns the existing result, never a duplicate.

- **New trips:** generate a random **UUIDv4** when the trip is first saved and persist it with the trip locally.
- **Legacy / imported trips** (e.g., the existing files in `/sdcard/download/euc/trips/`): derive a deterministic **UUIDv5** from `sha1(source_app + "|" + wheel.serial + "|" + start_utc + "|" + first_odometer_value)` so re-running a backfill never double-counts.

---

## 6. The trip CSV & `schema_version`

Send the CSV unmodified; the backend maps columns by **name**. Tag the layout so the server knows what to expect. Known layouts observed in real data (use these or extend):

| `schema_version` | Header (column names) |
|---|---|
| `darknessbot-v1` | `Date,Speed,Voltage,PWM,Current,Power,Battery level,Total mileage,Temperature,Pitch,Roll,Latitude,Longitude,Altitude` |
| `eucplanet-v1` | `Date,Speed,Voltage,Temperature,Battery level,Altitude,Latitude,Longitude,Total mileage,GPS speed` |
| `eucplanet-v2-extgps` | `… ,GPS speed,Ext GPS speed` |
| `eucplanet-v3-gforce` | `… ,GPS speed,Current,PWM,G-Force,G-Force X,G-Force Y` |

**Going forward, standardize new eucplanet exports on the full schema including `G-Force, G-Force X, G-Force Y`** (G-force leaderboards depend on it). Two date formats exist in the wild (`DD.MM.YYYY HH:MM:SS.fff` and ISO `YYYY-MM-DDThh:mm:ss.ffffff`); the backend handles both — just don't rewrite them.

---

## 7. Wheel & device info ("everything about the wheel")

Include all that are available; `null` where unknown:
`brand`, `model`, `serial` (or unique wheel id), `ble_mac`, `ble_name`, `firmware`. The MAC also appears in DarknessBot filenames (e.g. `F4E02AB02A75_…`). These drive per-wheel stats, dedupe, and the "real device" signal.

---

## 8. Behavior rules

- **Upload trigger:** when a trip is saved (and `consent_public` is true). Queue uploads; do not block the UI.
- **Consent:** show a one-time opt-in explaining that **name, flag, avatar, aggregate stats, and *clustered* (≈10 km) locations become public**, while **raw GPS tracks are never published**. Provide an off switch. Only upload for consenting users.
- **Backfill:** offer a "sync past rides" action that uploads historical trips with their original timestamps (set `tz_known:false` if the tz of an old import is unknown). Idempotent via §5.
- **Retry/queue:** persist a pending-upload queue. Retry on network errors and `5xx` with exponential backoff; honor `429 Retry-After`; treat `409 duplicate` as success and mark the trip uploaded.

### Response & error codes
| Code | Meaning | App action |
|---|---|---|
| `200/201` | Stored. Body: `{trip_uuid, validation_status: "validated"\|"flagged", reasons?}` | Mark uploaded; if `flagged`, optionally surface "under review" |
| `202` | Accepted, processing | Mark uploaded |
| `400` | Malformed envelope | Fix payload; log (don't infinite-retry) |
| `401` | Attestation invalid/expired | Re-mint integrity token, retry once |
| `409` | Duplicate `trip_uuid` | Treat as success |
| `413` | File too large | Don't retry; report |
| `422` | CSV unparseable | Don't retry; report with `file_sha256` |
| `429` | Rate-limited | Back off per `Retry-After` |
| `5xx` | Server error | Exponential backoff retry |

---

## 9. Open items to confirm with the eucstats backend
1. **`store_id` source** — which stable Google Play identifier the app will use (signed-in Google account id vs Play Billing obfuscated account id). Must be stable across reinstalls.
2. Max upload size / rate limits (for `413`/`429` tuning).
3. Whether batch upload (array of trips in one request) is wanted for faster backfill, or one-trip-per-request is fine.
