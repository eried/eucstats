# eucstats — Foundation Design (Ingestion + Anti-fraud + Identity + Storage/Retention)

- **Date:** 2026-06-03
- **Status:** Draft for review
- **Author:** Erwin Ried (with Claude)
- **Scope of this spec:** The data foundation only — how trips arrive, how we trust them, who they belong to, and where they live. Explicitly **out of scope** (each gets its own later spec): the stats/leaderboard engine, the public map+leaderboard website, and the admin UI.

---

## 1. Context & goals

eucstats hosts public stats for unicycle (EUC) riders of the **eucplanet** app (Android/Google Play today; iOS later). The user controls eucplanet, so the upload payload and client behaviour are ours to define. A separate static viewer, **eucviewer** (`github.com/eried/eucviewer`), already parses EUC trip logs client-side and exposes `window.loadFileFromBase64`.

The full product is **six subsystems**:

| # | Subsystem | Depends on |
|---|---|---|
| 1 | Ingestion API + anti-fraud | — |
| 2 | Identity / registration | — |
| 3 | Storage + retention | 1,2 |
| 4 | Stats / leaderboard engine | 3 |
| 5 | Public site (map + leaderboards) | 4 |
| 6 | Admin view | 2 |

This document specifies the **foundation = 1 + 2 + 3**. The service runs as the FastAPI app already provisioned on the droplet at `127.0.0.1:8004` (behind nginx at `eucstats.ried.no`, `User=root`, mirroring `finn-home-finder`).

### Success criteria
- eucplanet can register a rider and upload trips (including bulk historical backfill) over a stable, versioned API.
- Every upload is authenticity-checked (attestation + telemetry plausibility) before it can influence public stats.
- The data layer is fast for public reads (sub-ms) because standings and map data are **precomputed**, and is portable (single SQLite file behind a repository seam).
- Full-resolution trip data is retained only temporarily; compact summaries and aggregates are permanent.

---

## 2. Data reality (grounding from real samples)

202 real trips were pulled from the phone (`/sdcard/download/euc/trips/`). Findings that drive the design:

- **6 distinct CSV header layouts** across two source families:
  - **DarknessBot** export (168 files): `Date, Speed, Voltage, PWM, Current, Power, Battery level, Total mileage, Temperature, Pitch, Roll, Latitude, Longitude, Altitude`. Pitch/Roll observed **empty**.
  - **eucplanet native** (varies): adds `GPS speed`, sometimes `Ext GPS speed`, and (newest, ~8% of files) `Current, PWM, G-Force, G-Force X, G-Force Y`.
- **2 date formats, neither carrying a timezone:** ISO `2025-05-30T20:17:22.000000` (DarknessBot) and `01.06.2026 20:24:31.204` (eucplanet).
- **`Total mileage` semantics differ:** DarknessBot resets to 0 per trip; eucplanet is a *lifetime* odometer. `max−min` yields trip distance for both; lifetime totals must **sum per-trip distances**, never read the odometer directly.
- **G-Force is sparse today** (only newest eucplanet schema). G-force leaderboards are best-effort until eucplanet standardises on the full schema.

### Parser requirements
- **Header-driven** (map by column *name*, not position).
- Every metric **optional** except timestamp + position.
- Handle both date formats; normalize to **UTC** using the timezone supplied in the envelope.
- Sampling ~1 Hz; files up to ~500 KB.

---

## 3. What eucplanet must send (the integration contract)

None of the rider/trip *metadata* is in the CSV — it must be supplied in an **upload envelope** alongside the raw trip file:

| Field | Why |
|---|---|
| `store_id` + `platform` (`google_play` \| `apple`) | Stable rider identity |
| Attestation token (Play Integrity / App Attest) | Proves the app is genuine |
| `trip_uuid` | Idempotent dedupe of (re)uploads |
| `tz_offset` (or IANA tz) | **Required** for daily/weekly/streak stats — not in the CSV |
| `wheel_id` (model + serial/MAC) | Per-wheel stats, dedupe, "real device" signal |
| `app_version` + `schema_version` | Server knows which columns to expect |
| `is_mock_location` flag | GPS-spoofing backstop (see §6) |

Going forward, eucplanet should standardise on the **full schema including G-Force** and metric units (§7).

---

## 4. Architecture & components

- **Ingest API** — `POST /api/v1/trips`: accepts envelope + raw trip file(s) (gzip).
- **Attestation verifier** — validates Play Integrity (later App Attest) tokens against the platform's keys.
- **Trip processor** — header-driven parse → normalize → plausibility checks → compute canonical per-trip summary + downsampled (~500-pt) track.
- **Identity/profile service** — register, query, update (name/flag/avatar with monthly limits), delete/export.
- **Repository layer** — the *only* module that touches SQLite. Defines interfaces (`RiderRepo`, `TripRepo`, `AggregateRepo`, …) so a future swap to Postgres/ClickHouse touches no business logic.
- **Aggregator** — incrementally updates materialized tables when a trip is validated.
- **Retention job** — periodic; applies the hybrid eviction policy.
- **Admin auth module** — TOTP, mirrored from finn (consumed by the later admin UI; pattern fixed here).

All public reads are served from **precomputed/materialized tables** — never by scanning raw trips. This is what makes SQLite fast enough; the principle holds regardless of engine.

---

## 5. Data model (SQLite, WAL mode)

### Source-of-truth tables
- **`riders`**: `store_id` (PK), `platform`, `display_name`, `flag`, `avatar_64` (PNG blob, 64×64), `last_name_change`, `last_flag_change`, `last_avatar_change`, `created_at`, `deleted_at` (soft-delete for GDPR), provenance.
- **`wheels`**: `wheel_id` (PK), `rider_store_id` (FK), `model`, first/last seen.
- **`trips`**: `trip_uuid` (PK), `rider_store_id` (FK), `wheel_id` (FK), `start_utc`, `end_utc`, `tz_offset`, `distance_km`, `duration_s`, `max_speed`, `avg_speed`, `max_gforce` (nullable), energy fields (nullable), `country` (ISO), `start_cell`/`end_cell`, `validation_status` (`validated`\|`flagged`\|`rejected`), `flag_reasons`, `schema_version`, `is_mock_location`, `created_at`.
- **`trip_tracks`**: `trip_uuid` (FK), downsampled normalized path as a compressed blob (kept for map/replay + eucviewer hand-off). *Permanent but compact.*
- **`raw_uploads`**: `trip_uuid` (FK), full-resolution upload blob, `bytes`, `received_at`. **Temporary** — retention-managed (§8).

### Materialized tables (precomputed; public reads hit only these)
- **`rider_stats`** — per-rider totals (lifetime km, trip count, best speed, max G, streak state…).
- **`country_stats`** — per-country total km, rider count, avg km/rider.
- **`daily_distance`** — per-rider per-day buckets (for "highest daily distance", weekly champion).
- **`map_cells`** — 10×10 km grid cells × 2–3 zoom levels: rider count, total km, last activity. *No raw GPS exposed.*
- **`records`** — current record-holders (mileage king, top speed, max G-force, longest single trip…).
- **`leaderboard_snapshots`** — closed-period snapshots (weekly champion at week-close, streak leaders).

### State file
- **`data/admin.json`** — TOTP secret, `enrolled`, persisted session secret (see §9).

---

## 6. Ingest data flow & anti-fraud

```
upload (envelope + raw file)
  → verify attestation (genuine app/device)
  → resolve rider by store_id (must be registered)
  → dedupe by trip_uuid (idempotent; re-uploads return the existing result)
  → header-driven parse + normalize raw rows → UTC
  → plausibility checks:
        • impossible speed / acceleration
        • GPS teleports (distance/time between fixes)
        • absurd G-force
        • odometer Δ vs GPS-integrated distance must roughly agree
        • is_mock_location flag honored
  → status = validated | flagged | rejected
  → persist summary + downsampled track (permanent) + raw blob (temporary)
  → if validated: aggregator updates materialized tables incrementally
```

**Trust model (chosen): attestation + plausibility.**
- **Attestation** (Play Integrity → later App Attest) proves the upload came from the genuine, unmodified app on a real device/account. Blocks impersonation and random API traffic.
- **Plausibility** catches *self-cheating* (a real user fabricating/editing their own trips) that attestation cannot.
- **Mock-GPS backstop:** attestation does **not** prove GPS authenticity. eucplanet supplies `is_mock_location`; the server additionally relies on odometer-vs-GPS agreement and teleport checks. Flagged trips are quarantined from leaderboards pending admin review.

**"Validated"** = passed all automated checks. **"Flagged"** = at least one check tripped → excluded from public stats until an admin reviews (admin UI is a later spec; the `validation_status` field and review queue exist from day one).

---

## 7. Identity / profile API

Base path `/api/v1`. All rider-mutating calls require a valid attestation token for that `store_id`.

- `POST /riders` — register/upsert: `{store_id, platform, attestation, display_name, flag, avatar?}`.
- `GET /riders/{store_id}` — returns current `display_name`, `flag`, `avatar` URL, and `can_change_after` dates so **the app drives the edit UI**.
- `PATCH /riders/{store_id}` — change `display_name` / `flag` / `avatar`, each enforced to **once per calendar month** independently. Avatar is re-encoded server-side to a 64×64 PNG with **EXIF/GPS stripped**.
- `DELETE /riders/{store_id}` — soft-delete + purge PII, cascade trips/tracks, schedule aggregate recompute (GDPR).
- `GET /riders/{store_id}/export` — self data export (GDPR).
- `POST /trips` — trip ingest (§6); supports **bulk/historical backfill** (array or repeated calls; idempotent via `trip_uuid`; original timestamps preserved).

**Units:** canonical metric everywhere (km, km/h, V, A, W, °C, g). Imperial is a client-side display conversion only.

**Public exposure:** only `display_name`, `flag`, `avatar`, aggregate stats, and clustered (10 km) locations. **Never** `store_id`, raw GPS tracks, or home location. Full GPS + the home location seen in `eucplanet_settings.json` make raw tracks identifying — hence clustering and trimming trip endpoints near home.

---

## 8. Storage & retention

- **Engine:** SQLite in WAL mode, single file under `data/` (mirrors finn). All access via the repository layer.
- **Permanent:** `riders`, `wheels`, `trips` (summaries), `trip_tracks` (downsampled), all materialized tables.
- **Temporary:** `raw_uploads` (full-resolution blobs).
- **Hybrid eviction** of `raw_uploads`:
  - Evict when **(validated AND older than `RETENTION_DAYS`)**, **or**
  - whenever **free disk < `DISK_FLOOR_GB`** → evict oldest validated first until above the floor.
  - Defaults: `RETENTION_DAYS = 30`, `DISK_FLOOR_GB = 10` (both config).
- **Backups:** periodic copy of the SQLite file + avatar blobs from `data/` (cron or app job), matching finn's operational pattern.

---

## 9. Admin authentication (pattern fixed; UI is a later spec)

Mirror `finn-home-finder/web/admin.py` exactly:
- **TOTP** via `pyotp` + `qrcode`. First visit to `/admin` shows a QR + secret to enroll an authenticator app.
- Login = current 6-digit code → `pyotp.TOTP(secret).verify(code, valid_window=1)`.
- Session via Starlette `SessionMiddleware`, `secret_key` persisted in `data/admin.json` so sessions survive restarts: `app.add_middleware(SessionMiddleware, secret_key=_get_session_secret())`.
- Every admin route guarded by an `_is_authenticated(request)` check.

---

## 10. Non-functional

- **Scale target:** < 10k riders, dozens–hundreds of trips/day. SQLite + precompute is comfortably sufficient.
- **Performance:** public reads hit materialized tables only (sub-ms). Heavy work happens at ingest/aggregation time.
- **Versioned API** (`/api/v1`) for forward compatibility; `schema_version` carried per upload.
- **Rate limits + payload size caps** on ingest and avatar upload.

---

## 11. Assumptions & open questions

1. **store_id ↔ attestation binding:** attestation proves the app is genuine; `store_id` is supplied by eucplanet from the Play account. Play Integrity does not expose a cryptographically verifiable account ID, so we **trust that binding** rather than proving account ownership. (Accepted risk.)
2. **Map granularity:** ~10 km grid with 2–3 precomputed zoom levels. (User left this to us.)
3. **Country detection:** offline point-in-polygon against bundled country boundaries — no per-trip external API.
4. **Timezone policy for "day"/"week" boundaries** (per-rider local vs UTC) is deferred to the stats-engine spec; the foundation simply **stores `tz_offset`** so either policy is possible.

---

## 12. Out of scope (later specs)

- **Stats/leaderboard engine** — curating the "most fun" leaderboards from InMotion's set (mileage king, highest daily-distance avg, riders/country, total & avg km/country, weekly champion, longest streak) plus G-force records; defining day/week boundaries.
- **Public site** — world map (10 km clusters) + leaderboards + podiums (name/flag/avatar) + eucviewer trip replay.
- **Admin UI** — rider/trip moderation, the flagged-trip review queue, retention/config controls (auth pattern fixed in §9).
