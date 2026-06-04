# Testing eucplanet uploads against the live eucstats server (emulator)

> Hand this to the agent building the **eucplanet Android app**. It explains how to verify,
> from an emulator (or device), that registration + trip uploads actually reach the live
> **eucstats** backend. The backend is deployed at `https://eucstats.ried.no` and is already
> tested end‑to‑end. Implementation contract: `eucplanet-build-prompt.md`.

## Why this works on an emulator
- The server runs in **`stub` attestation mode** → it accepts any `attestation.token` value
  (use `"stub"`). This is what lets you test from an emulator at all, because the
  **Play Integrity API does not return a valid verdict on emulators** (they aren't
  Play‑recognized). Build the real Play Integrity call now, but it's a no‑op server‑side
  until the eucstats owner sets `EUCSTATS_ATTESTATION_MODE=enforce`.
- Base URL: **`https://eucstats.ried.no/api/v1`** (HTTPS, public). An emulator reaches it
  directly — no special networking.
- If you instead test against a **local** eucstats dev server (`127.0.0.1:8004`): from the
  emulator the host is `http://10.0.2.2:8004/api/v1`, and because that's cleartext you must add
  a `network_security_config.xml` exception for `10.0.2.2`. Testing against the live HTTPS
  server avoids this.

## Step 0 — prove the server is reachable & accepting (run before app work)
From your dev machine or `adb shell`. Use a **throwaway `store_id`** (e.g. `emu_test_1`).

Register:
```bash
curl -sS -X POST https://eucstats.ried.no/api/v1/riders \
  -H "Content-Type: application/json" \
  -d '{"store_id":"emu_test_1","platform":"google_play","display_name":"Emu Tester","flag":"NO"}'
# -> 200 {"store_id":"emu_test_1",...,"can_change_name_after":...}
```

Upload one trip (gzipped CSV + a `meta` JSON string, multipart):
```bash
printf 'Date,Speed,Voltage,Current,Power,Latitude,Longitude,G-Force\n2026-06-04T10:00:00.000000,0,84,2,168,59.910,10.750,1.0\n2026-06-04T10:00:03.000000,18,83,30,2490,59.911,10.751,1.1\n2026-06-04T10:00:06.000000,42,82,60,4920,59.912,10.752,1.4\n2026-06-04T10:00:09.000000,40,82,40,3280,59.913,10.753,1.1\n2026-06-04T10:00:12.000000,0,84,2,168,59.914,10.754,1.0\n' | gzip > trip.csv.gz

META='{"store_id":"emu_test_1","platform":"google_play","trip_uuid":"11111111-1111-4111-8111-111111111111","source_app":"eucplanet","schema_version":"eucplanet-v3-gforce","tz":"Europe/Oslo","tz_offset_min":120,"tz_known":true,"is_mock_location":false,"wheel":{"brand":"Begode","model":"Master","serial":"EMU0001"},"attestation":{"type":"play_integrity","token":"stub","request_hash":"x"}}'

curl -sS -X POST https://eucstats.ried.no/api/v1/trips \
  -F "meta=$META" \
  -F "trip=@trip.csv.gz;type=application/gzip"
# -> 201 {"trip_uuid":"...","validation_status":"validated","distance_km":...,"country":"NO"}
```
Re‑run the **same** command → `200 {"duplicate":true}` (proves idempotency).

## Step 1 — the same call from the app (OkHttp sketch)
```kotlin
val meta = """{"store_id":"$storeId","platform":"google_play","trip_uuid":"$uuid",
  "source_app":"eucplanet","schema_version":"eucplanet-v3-gforce",
  "tz":"$tz","tz_offset_min":$tzMin,"tz_known":true,"is_mock_location":$isMock,
  "wheel":{"brand":"$brand","model":"$model","serial":"$serial"},
  "attestation":{"type":"play_integrity","token":"$token","request_hash":"$reqHash"}}"""

val gz = ByteArrayOutputStream().also { GZIPOutputStream(it).use { z -> z.write(csvBytes) } }.toByteArray()

val body = MultipartBody.Builder().setType(MultipartBody.FORM)
  .addFormDataPart("meta", meta)                                   // a plain string part
  .addFormDataPart("trip", "trip.csv.gz",
      gz.toRequestBody("application/gzip".toMediaType()))          // gzipped CSV file part
  .build()

val req = Request.Builder().url("https://eucstats.ried.no/api/v1/trips").post(body).build()
```
- `token = "stub"` works for now. `reqHash = SHA-256(meta bytes)` (any value is accepted in stub mode, but compute it correctly so it's ready for enforce).
- The CSV is sent **gzipped** as the `trip` file part; `meta` is a **string form field**, NOT a JSON request body. (This is the #1 integration mistake.)

## Step 2 — confirm the submission landed (3 independent checks)
- **Response code:** `201` + `validation_status:"validated"` is success. `flagged` means it
  was accepted but held from boards (check `reasons`, e.g. `mock_location`, `impossible_speed`).
- **GET back:**
  ```bash
  curl -sS https://eucstats.ried.no/api/v1/riders/emu_test_1          # profile exists
  curl -sS https://eucstats.ried.no/api/v1/stats/summary              # trips/total_km went up
  curl -sS https://eucstats.ried.no/api/v1/leaderboards/mileage       # rider appears
  ```
- **Live site:** open `https://eucstats.ried.no/` → the test rider shows in Riders /
  the map. (Ask the eucstats owner to `journalctl -u eucstats -f` if you want to watch the
  `POST /api/v1/trips … 201` hit the server in real time — that requires droplet access.)

## Step 3 — backfill path
`adb push sample.csv /sdcard/Download/euc/trips/` then trigger the app's "sync past rides".
Each historical file should upload once; re‑syncing must not double‑count (deterministic
`trip_uuid` = UUIDv5 of `sha1(source_app|wheel_serial|start_utc|first_odometer)`).

## Step 4 — clean up test data
```bash
curl -sS -X DELETE https://eucstats.ried.no/api/v1/riders/emu_test_1
```

## Response codes to handle
`200/201` ok · `200 {"duplicate":true}` already have it (success) · `400` bad meta /
unregistered rider · `401` attestation (only in enforce mode) · `413` CSV > 8 MB ·
`422` unparseable CSV · `429` rate‑limited (back off). Retry network/`5xx` with backoff.

## Acceptance checklist for the test
- [ ] `curl` register + upload from the dev machine returns `200`/`201` (server reachable).
- [ ] App (emulator) registers a rider; it appears via `GET /riders/{id}` and on the site.
- [ ] App uploads a saved trip → `201 validated`; `stats/summary` counts increase.
- [ ] Re‑upload same `trip_uuid` → `duplicate:true` (no double count).
- [ ] Backfill uploads each file once, idempotently.
- [ ] Test rider deleted afterwards.
- [ ] `meta` sent as multipart string field + gzipped CSV as `trip` file (verified by `201`).
