# Build prompt — eucplanet → eucstats upload integration

> **How to use:** paste everything below the line into the coding agent working on the
> **eucplanet Android app** repository. It is self-contained (the eucstats backend is
> already built, deployed and live — this is the client side only). Full reference:
> `eucplanet-upload-contract.md` / `eucplanet-android-instructions.md` in the eucstats repo.

---

You are implementing the **eucstats integration** in the eucplanet Android app. eucstats is a
public EUC-rider stats site (leaderboards, world heatmap, podiums). eucplanet is the data
source: it registers the rider's public profile and, after every saved trip, uploads the trip
so it appears on the site. The backend is **live and tested** at `https://eucstats.ried.no`.

## Mission
1. **Rider identity** — on first launch register the rider; expose an edit screen for
   name / flag / 64×64 avatar.
2. **Trip upload** — after a trip is saved (and the user consented), upload the raw trip CSV;
   also offer a one-time "sync past rides" backfill of files in `/sdcard/download/euc/trips/`.
3. **Anti-fraud** — attach a Play Integrity token to every state-changing request.
4. **Consent + GDPR** — one-time opt-in; expose delete/export.

## API (base `https://eucstats.ried.no/api/v1`, HTTPS, metric units, JSON)

### Register / upsert — `POST /riders`
```json
{"store_id":"<stable per-user id>","platform":"google_play","display_name":"Erwin",
 "flag":"NO","avatar_png_base64":"<optional>","consent_public":true,
 "attestation":{"type":"play_integrity","token":"<token>","request_hash":"<sha256(body)>"}}
```
`GET /riders/{id}` returns the profile + `can_change_{name,flag,avatar}_after` dates → use them
to gate the edit UI. `PATCH /riders/{id}` edits a field (once per calendar month → `429`).
`DELETE /riders/{id}` and `GET /riders/{id}/export` cover GDPR.

### Upload a trip — `POST /trips` (`multipart/form-data`)
- part `trip` = the **raw CSV, gzipped** (`application/gzip`) — send **as captured**, do not reformat.
- part `meta` = JSON string:
```json
{"store_id":"...","platform":"google_play","trip_uuid":"<uuid>",
 "source_app":"eucplanet","schema_version":"eucplanet-v3-gforce",
 "tz":"Europe/Oslo","tz_offset_min":120,"tz_known":true,"is_mock_location":false,
 "wheel":{"brand":"Begode","model":"Master","serial":"F4E02AB02A75","ble_mac":"F4:E0:2A:B0:2A:75","firmware":"1.07"},
 "attestation":{"type":"play_integrity","token":"<token>","request_hash":"<sha256(meta)>"}}
```
Response `201 {"trip_uuid","validation_status":"validated"|"flagged","distance_km","country"}`.
The server parses the CSV (header-driven, both date formats) and computes distance/speed/etc.

**The app must supply (not in the CSV):** `tz`/`tz_offset_min`/`tz_known` (CSV has no timezone),
`is_mock_location` (`Location.isFromMockProvider()` — the GPS-spoof backstop), `wheel.*`, and
`trip_uuid` — random **UUIDv4** for new trips; deterministic **UUIDv5** of
`sha1(source_app|wheel_serial|start_utc|first_odometer)` for backfill so re-runs dedupe.

**Idempotent:** same `trip_uuid` → `200 {"duplicate":true}`. Treat as success.

## Behaviour rules
- Queue uploads; never block the UI. Retry network/`5xx` with exponential backoff; honour
  `429 Retry-After`; `409`/duplicate = success; `400`/`413`/`422` = log, don't infinite-retry.
- **Standardize new exports on the full schema incl. `G-Force,G-Force X,G-Force Y`** (the
  g-force / sustained-power / 0→40 boards depend on these columns).
- `store_id`: use a stable, privacy-respecting id (Play Games player id, or a UUID the app
  generates on first launch and persists/backs-up). Never device IDs or raw account emails.
- Consent copy: "name, flag, avatar, aggregate stats and clustered (~10 km) locations become
  public; raw GPS tracks are never published." Only upload for consenting users.

## Play Integrity
Package is `com.eried.eucplanet`. Use Standard requests; bind each token with
`requestHash = SHA-256(meta-bytes)`. The server runs in **`stub` mode today (accepts any token)**,
so build and ship now; enforcement is flipped server-side later (`EUCSTATS_ATTESTATION_MODE=enforce`).
Make attestation a small swappable module (iOS later uses `{"type":"app_attest",...}`).

## Acceptance criteria
- [ ] First launch registers a rider (consented) and the profile shows on `https://eucstats.ried.no/`.
- [ ] Saving a trip uploads it; it appears in the rider's stats within ~a minute.
- [ ] Re-uploading the same trip does not double-count (idempotent).
- [ ] Backfill uploads historical files once each, idempotently.
- [ ] Edit screen respects `can_change_*`; delete/export wired in settings.
- [ ] All requests carry a Play Integrity token + `request_hash`.
- [ ] QA against the live stub API; coordinate the enforce flip with the eucstats owner before/after release.
