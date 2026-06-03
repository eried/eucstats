# eucplanet Android — Integration & Submission Instructions

> **Status (2026-06-03):** the eucstats backend is **built, deployed, and live** at
> `https://eucstats.ried.no`, and every endpoint below was **tested end-to-end with
> real trip CSVs** (the examples are actual responses). This document is the
> validated to-do list for the eucplanet Android app. The detailed field reference
> lives in `eucplanet-upload-contract.md`; this file is the actionable summary +
> the real examples + go-live steps.

---

## 1. What the app must do

1. **Register / maintain the rider profile** (display name, flag, optional 64×64 avatar).
2. **After each saved trip, upload it** (and offer a one-time backfill of historical trips).
3. **Attach a Play Integrity token** to registration + uploads (see §6). *The server
   currently runs in `stub` mode and accepts uploads without a real token, so you can
   integrate and test now; enforcement is flipped on later (§6).*

Base URL: **`https://eucstats.ried.no/api/v1`** (HTTPS only). Units are metric.

---

## 2. Register a rider — `POST /riders`  ✅ tested

Request:
```
POST /api/v1/riders
Content-Type: application/json
{"store_id":"<stable id>","platform":"google_play","display_name":"Erwin","flag":"NO"}
```
Real response (`200`):
```json
{"store_id":"u_demo_1","platform":"google_play","display_name":"Erwin","flag":"NO",
 "has_avatar":false,"consent_public":true,
 "can_change_name_after":"2026-07-01","can_change_flag_after":"2026-07-01",
 "can_change_avatar_after":"2026-07-01"}
```
- Optional `avatar_png_base64` (any square-ish image; server re-encodes to 64×64 PNG, strips EXIF).
- Optional `consent_public` (default true).

**Read the profile** with `GET /riders/{store_id}` and use `can_change_*` to gate the edit
UI. **Edit** with `PATCH /riders/{store_id}` (`display_name`/`flag`/`avatar_png_base64`);
each is limited to **once per calendar month** (`429` if too soon). `DELETE /riders/{id}`
and `GET /riders/{id}/export` cover GDPR.

---

## 3. Upload a trip — `POST /trips` (multipart)  ✅ tested

Two parts: `meta` (JSON string field) + `trip` (the **gzipped raw CSV** file). Send the CSV
exactly as captured — the server parser is header-driven and handles every schema/date
variant; do not reformat.

`meta` example:
```json
{"store_id":"u_demo_1","platform":"google_play",
 "trip_uuid":"3cf0b8cb-3ea6-579a-bade-70c4b290d4bf",
 "source_app":"eucplanet","schema_version":"eucplanet-v3-gforce",
 "tz":"Europe/Oslo","tz_offset_min":120,"tz_known":true,
 "is_mock_location":false,
 "wheel":{"serial":"F4E02AB02A75","model":"Master"},
 "attestation":{"type":"play_integrity","token":"<token>","request_hash":"<sha256(meta)>"}}
```
Real response (`201`, a genuine 18.6 km Tromsø ride):
```json
{"trip_uuid":"3cf0b8cb-...","validation_status":"validated","reasons":[],
 "duplicate":false,"distance_km":18.59,"country":"NO"}
```
- **Idempotent:** re-POST with the same `trip_uuid` returns `200 {"duplicate":true}` — safe to retry.
- `validation_status` is `validated` or `flagged` (with `reasons`, e.g. `mock_location`,
  `impossible_speed`, `teleport`); flagged trips are held out of leaderboards until an admin approves.
- Error codes: `400` bad meta / unregistered rider, `401` attestation (enforce mode),
  `413` too large (>8 MB), `422` unparseable, `429` rate-limited. Queue uploads and
  retry with backoff on `5xx`/network; treat `409`/`duplicate` as success.

### Required `meta` fields the app must produce (not in the CSV)
| Field | Notes |
|---|---|
| `store_id`, `platform` | stable rider identity (§7) |
| `trip_uuid` | **new trips:** random UUIDv4 saved with the trip. **backfill:** UUIDv5 of `sha1(source_app|wheel_serial|start_utc|first_odometer)` so re-runs dedupe |
| `tz` / `tz_offset_min` / `tz_known` | **the CSV has no timezone** — supply it (offset in minutes; `tz_known:false` for old imports) |
| `is_mock_location` | true if any fix had `Location.isFromMockProvider()` — closes the GPS-spoof gap |
| `wheel` | `{brand,model,serial,ble_mac,ble_name,firmware}` (all optional) |
| `source_app`, `schema_version`, `app_version` | provenance |

**Standardize new eucplanet exports on the full schema including `G-Force,G-Force X,G-Force Y`** —
G-force leaderboards depend on it (DarknessBot CSVs have none).

---

## 4. Backfill historical trips
Offer a "sync past rides" action that uploads the existing files in
`/sdcard/download/euc/trips/` with their original timestamps (set `tz_known:false` if the
tz is unknown). Idempotent via the deterministic `trip_uuid` above, so it's safe to re-run.

---

## 5. What riders see (already live)
- Public site `https://eucstats.ried.no/`: world map (clustered to ~10 km), the **Mileage
  Kings podium** (name/flag/avatar), and leaderboards — mileage, top speed, biggest day,
  longest streak, max G-force — plus per-country stats and record-holders.
- Verified live, e.g. `GET /api/v1/stats/summary` → `{"riders":2,"trips":3,"total_km":34.4,"countries":1}`.

---

## 6. Play Integrity (anti-fraud) — setup & go-live

The app's package is **`com.eried.eucplanet`** (Google Play account `7771489537038540127`).

**App side:**
1. Add the Play Integrity API (Standard requests). For each register/upload, compute
   `request_hash = SHA-256(meta-json-bytes)` and pass it as the request's `requestHash`.
2. Put the returned token in `meta.attestation.token` (+ the `request_hash`).
3. iOS later: same field with `{"type":"app_attest",...}`.

**Server side (to enable enforcement):**
1. In Play Console → **App integrity**, link a Google Cloud project and enable the
   Play Integrity API; create a service account for **Google-managed decoding**
   (`playintegrity.googleapis.com/.../decodeIntegrityToken`).
2. Implement `PlayIntegrityVerifier` (already stubbed in `ingest/attestation.py`) to
   decode + verify: `requestPackageName == com.eried.eucplanet`, `requestHash` matches,
   `appRecognitionVerdict == PLAY_RECOGNIZED`, device verdict OK.
3. Flip the service to enforce: set `EUCSTATS_ATTESTATION_MODE=enforce` and restart.
   Until then it runs `stub` (accepts all) so you can integrate without blocking.

---

## 7. `store_id` choice (decide once — both sides depend on it)
Use a **stable, privacy-respecting per-user identifier**. Recommended order:
1. **Play Games Services player ID** if the app uses Play Games (stable, no PII), or
2. a **server-issued rider id**: on first launch the app calls `POST /riders` with a
   locally-generated UUID it persists; that UUID becomes `store_id`. Simplest, no extra SDK.

Avoid device IDs (reset on reinstall) and raw Google account emails (PII). The server
treats `store_id` as an opaque string, so the app owns this decision — just keep it stable
across reinstalls (back it up via Play account if possible).

---

## 8. Privacy / consent
Show a one-time opt-in: **name, flag, avatar, aggregate stats, and clustered (~10 km)
locations become public; raw GPS tracks are never published.** Only upload for consenting
users; expose the delete/export endpoints in settings.

---

## 9. Submission checklist
- [ ] Decide `store_id` source (§7) and register riders on first launch.
- [ ] Build the `meta` envelope per §3 (tz, mock-location flag, trip_uuid, wheel).
- [ ] Upload on trip-save (gzipped CSV) + a backfill action; queue + retry.
- [ ] Profile edit screen driven by `can_change_*`; delete/export in settings.
- [ ] Integrate Play Integrity (Standard + requestHash); coordinate the server enforce flip.
- [ ] Consent opt-in before any upload.
- [ ] QA against the live `stub`-mode API, then submit the Play release.
