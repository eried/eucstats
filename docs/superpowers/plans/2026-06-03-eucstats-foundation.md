# eucstats Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the eucstats data foundation — a FastAPI service that registers riders, ingests attested trip uploads, validates them, stores them, and incrementally maintains precomputed stats — deployed at `https://eucstats.ried.no`.

**Architecture:** FastAPI app (`main:app`, gunicorn+uvicorn, port 8004, mirrors finn-home-finder). SQLAlchemy over SQLite (WAL) behind a repository layer. Ingestion pipeline: attestation → header-driven CSV parse → normalize → plausibility → persist summary (permanent) + raw blob (temporary) → incremental aggregation into materialized tables. Admin auth via TOTP (mirrored from finn). All public reads come from materialized tables.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, Pillow (avatar), reverse_geocoder (offline country lookup), pyotp + qrcode (admin TOTP), pytest + httpx TestClient, gunicorn + uvicorn (already on server).

**Design source:** `docs/superpowers/specs/2026-06-03-eucstats-foundation-design.md`
**Client contract:** `docs/integration/eucplanet-upload-contract.md`

---

## File Structure

```
config.py                 # Settings: paths, retention thresholds, attestation mode, grid sizes
database.py               # Engine, SessionLocal, get_db, WAL pragma, init_db
models.py                 # SQLAlchemy models (all tables in §5 of the spec)
main.py                   # FastAPI app, SessionMiddleware, router mounting, lifespan
repository/
  riders.py               # RiderRepo: CRUD + monthly-limit checks
  trips.py                # TripRepo: upsert by trip_uuid, status, raw-upload mgmt
  aggregates.py           # AggregateRepo: rider_stats/country_stats/daily/map_cells/records
ingest/
  schema.py               # Pydantic envelope models + known schema_version registry
  parser.py               # header-driven CSV -> list[Sample]; 2 date formats; optional cols
  summary.py              # Sample[] -> TripSummary (distance, speed, gforce, energy, duration)
  geo.py                  # country_of(lat,lon); cell_id(lat,lon,zoom) 10km grid
  downsample.py           # Sample[] -> <=500 pts, preserving min/max extremes
  plausibility.py         # checks -> (status, reasons[])
  attestation.py          # AttestationVerifier protocol; StubVerifier; PlayIntegrityVerifier
services/
  identity.py             # register/get/update(monthly)/delete/export; avatar pipeline
  ingest.py               # orchestrates the upload pipeline
  aggregator.py           # apply a validated trip to materialized tables
  retention.py            # hybrid eviction of raw_uploads
web/
  api.py                  # /api/v1 routes (riders, trips)
  admin.py                # TOTP auth (mirror finn) + minimal admin status
  templates/ static/      # (admin/site templates added in later plans)
tests/                    # pytest suite mirroring the tree
requirements.txt
scripts/
  send_samples.py         # local: POST sample CSVs to a running server (e2e test driver)
```

---

## Conventions

- TDD throughout: write the failing test, see it fail, implement minimally, see it pass, commit.
- Commit after each task with `feat:`/`test:`/`chore:` prefix and the Claude co-author trailer; push to `origin main` after each task (user preference: commit to GitHub regularly).
- All times stored UTC. All distances km, speeds km/h.
- `store_id` is an opaque stable string; the app decides its source.

---

## Task 1: Project scaffold, config, deployable skeleton

**Files:** Create `requirements.txt`, `config.py`, `database.py`, `main.py`, `tests/test_health.py`.

- [ ] **Test:** `tests/test_health.py` — `TestClient(app).get("/health")` returns 200 `{"ok": True}`; `GET /` returns service JSON.
- [ ] **requirements.txt:** `fastapi`, `uvicorn[standard]`, `gunicorn`, `sqlalchemy`, `pydantic`, `python-multipart`, `pillow`, `reverse_geocoder`, `numpy` (rg dep), `pyotp`, `qrcode[pil]`, `itsdangerous` (sessions), `httpx` + `pytest` (dev).
- [ ] **config.py:** `BASE_DIR`, `DATA_DIR=BASE_DIR/"data"`, `DB_PATH=DATA_DIR/"eucstats.sqlite"`, `RETENTION_DAYS=30`, `DISK_FLOOR_GB=10`, `ATTESTATION_MODE` (`stub`|`enforce`, default `stub`), `GRID_ZOOMS=[0.1,0.5,2.0]` (degrees), `MAX_UPLOAD_MB=8`, `AVATAR_PX=64`. Read overrides from env.
- [ ] **database.py:** SQLAlchemy engine (`sqlite:///DB_PATH`, `check_same_thread=False`), `PRAGMA journal_mode=WAL` + `foreign_keys=ON` on connect, `SessionLocal`, `get_db()` dependency, `init_db()` (create_all).
- [ ] **main.py:** `app=FastAPI(title="eucstats")`, lifespan calls `init_db()`, `/health` + `/` (keep placeholder behavior), mount routers (added later).
- [ ] **Run:** `pytest tests/test_health.py -v` → PASS. **Commit + push.**

---

## Task 2: Models + repository layer

**Files:** Create `models.py`, `repository/riders.py`, `repository/trips.py`, `repository/aggregates.py`, `tests/test_models.py`.

- [ ] **Models** (SQLAlchemy) per spec §5:
  - `Rider(store_id PK, platform, display_name, flag, avatar_png(LargeBinary), last_name_change, last_flag_change, last_avatar_change, consent_public, created_at, deleted_at)`
  - `Wheel(wheel_id PK, rider_store_id FK, brand, model, ble_name, firmware, first_seen, last_seen)`
  - `Trip(trip_uuid PK, rider_store_id FK, wheel_id, start_utc, end_utc, tz, tz_known, distance_km, duration_s, max_speed, avg_speed, max_gforce, wh_per_km, country, start_cell, validation_status, flag_reasons(JSON), schema_version, source_app, is_mock_location, sample_count, created_at)`
  - `TripTrack(trip_uuid PK/FK, points(LargeBinary gzip-json))`
  - `RawUpload(trip_uuid PK/FK, blob(LargeBinary), bytes, received_at)`
  - Materialized: `RiderStat(store_id PK, total_km, trip_count, best_speed, best_gforce, longest_trip_km, current_streak, longest_streak, last_ride_date)`, `CountryStat(country PK, total_km, rider_count, avg_km_per_rider)`, `DailyDistance(store_id+date PK, km)`, `MapCell(zoom+cell PK, rider_count, total_km, last_activity)`, `Record(key PK, store_id, value, trip_uuid, achieved_at)`, `LeaderboardSnapshot(period_type+period_key+board PK, payload JSON, generated_at)`.
- [ ] **RiderRepo:** `get(store_id)`, `upsert(...)`, `can_change(field, now)->bool`, `apply_change(...)`, `soft_delete(store_id)`.
- [ ] **TripRepo:** `get(trip_uuid)`, `exists(trip_uuid)`, `insert_trip(...)`, `set_status(...)`, `save_track(...)`, `save_raw(...)`, `delete_raw(trip_uuid)`, `iter_evictable(now, retention_days)`, `oldest_validated_raw()`.
- [ ] **AggregateRepo:** getters/`upsert` for each materialized table; `add_daily(store_id,date,km)`, `bump_map_cell(...)`, `set_record_if_better(key, store_id, value, trip_uuid)`.
- [ ] **Tests:** create in-memory/temp-file DB, exercise upsert/get/monthly-limit/raw-eviction-iteration. **Commit + push.**

---

## Task 3: Header-driven CSV parser

**Files:** Create `ingest/parser.py`, `tests/test_parser.py`.

- [ ] **Test (real headers from samples):** parse a DarknessBot snippet (`...T...` ISO date, `Pitch/Roll`, no GPS-speed) and an eucplanet snippet (`DD.MM.YYYY` date, `G-Force` cols); assert sample count, that lat/lon/speed/odometer parsed, that missing columns are `None`, and both date formats → aware UTC datetimes given a tz.
- [ ] **Implement:**
```python
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import csv, io

CANON = {  # header name (lower) -> canonical field
  "date":"t","speed":"speed","gps speed":"gps_speed","ext gps speed":"ext_gps_speed",
  "voltage":"voltage","current":"current","power":"power","pwm":"pwm",
  "battery level":"battery","total mileage":"odo","temperature":"temp",
  "altitude":"alt","latitude":"lat","longitude":"lon",
  "g-force":"g","g-force x":"gx","g-force y":"gy","pitch":"pitch","roll":"roll",
}

@dataclass
class Sample:
    t: datetime; lat: float|None; lon: float|None; speed: float|None
    gps_speed: float|None; alt: float|None; odo: float|None; voltage: float|None
    current: float|None; power: float|None; pwm: float|None; battery: float|None
    temp: float|None; g: float|None; gx: float|None; gy: float|None

def _parse_dt(s: str, tz_offset_min: int) -> datetime:
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%d.%m.%Y %H:%M:%S.%f", "%d.%m.%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            naive = datetime.strptime(s, fmt)
            return (naive - timedelta(minutes=tz_offset_min)).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unrecognized date: {s!r}")

def _f(v):  # tolerant float
    v = (v or "").strip()
    return float(v) if v not in ("","-") else None

def parse_csv(text: str, tz_offset_min: int = 0) -> list[Sample]:
    rdr = csv.reader(io.StringIO(text))
    header = [h.strip().lower() for h in next(rdr)]
    idx = {CANON[h]: i for i, h in enumerate(header) if h in CANON}
    out = []
    for row in rdr:
        if not row or len(row) < len(header): continue
        def g(k): 
            i = idx.get(k); return _f(row[i]) if i is not None else None
        ti = idx.get("t")
        if ti is None: continue
        out.append(Sample(t=_parse_dt(row[ti], tz_offset_min),
            lat=g("lat"),lon=g("lon"),speed=g("speed"),gps_speed=g("gps_speed"),
            alt=g("alt"),odo=g("odo"),voltage=g("voltage"),current=g("current"),
            power=g("power"),pwm=g("pwm"),battery=g("battery"),temp=g("temp"),
            g=g("g"),gx=g("gx"),gy=g("gy")))
    return out
```
- [ ] **Run** parser tests → PASS. **Commit + push.**

---

## Task 4: Trip summary computation

**Files:** Create `ingest/summary.py`, `tests/test_summary.py`.

- [ ] **Test:** given samples, `distance_km` = `max(odo)-min(odo)` when odometer present (fallback to GPS haversine integration); `duration_s` = last.t-first.t; `max_speed`/`avg_speed` from speed (ignore None); `max_gforce` = max(g) or None; `wh_per_km` from current*voltage integral / distance when available. Include a tiny fixture asserting exact numbers.
- [ ] **Implement** `TripSummary` dataclass + `summarize(samples)->TripSummary`, with haversine helper and a moving-average for avg_speed over time deltas. **Commit + push.**

---

## Task 5: Geo — country + map cell

**Files:** Create `ingest/geo.py`, `tests/test_geo.py`.

- [ ] **Test:** `country_of(69.667, 18.925)` == `"NO"` (Tromsø, from real sample coords); `cell_id(lat,lon,zoom)` is stable and quantizes (two nearby points share a cell at coarse zoom).
- [ ] **Implement:**
```python
import reverse_geocoder as rg
def country_of(lat, lon):
    if lat is None or lon is None: return None
    return rg.search((lat, lon), mode=1)[0].get("cc")
def cell_id(lat, lon, zoom_deg):
    import math
    return f"{zoom_deg}:{math.floor(lat/zoom_deg)}:{math.floor(lon/zoom_deg)}"
```
(`country_of` may be cached; rg loads its dataset once.) **Commit + push.**

---

## Task 6: Downsample track

**Files:** Create `ingest/downsample.py`, `tests/test_downsample.py`.

- [ ] **Test:** a 5000-sample input downsamples to ≤500 points but the point with global max speed and the point with max g are retained (extremes preserved).
- [ ] **Implement:** bucket into `ceil(n/target)` windows; from each window keep first point + the local max-speed and max-g points; always include global extremes. Serialize to gzip-JSON `[[t,lat,lon,speed,g],...]`. **Commit + push.**

---

## Task 7: Plausibility checks

**Files:** Create `ingest/plausibility.py`, `tests/test_plausibility.py`.

- [ ] **Test:** clean trip → `("validated", [])`; trip with a 400 km/h sample → flagged "impossible_speed"; GPS jump implying >150 km/h between fixes → "teleport"; `is_mock_location=True` → "mock_location"; odometer-Δ vs GPS distance off by >40% → "distance_mismatch".
- [ ] **Implement** `check(samples, summary, is_mock)->(status, reasons)` with configurable thresholds (`MAX_KMH=120`, `MAX_G=12`, `TELEPORT_KMH=150`, `DIST_TOLERANCE=0.4`). Any reason ⇒ `flagged`; empty ⇒ `validated`. **Commit + push.**

---

## Task 8: Attestation verifier

**Files:** Create `ingest/attestation.py`, `tests/test_attestation.py`.

- [ ] **Test:** `StubVerifier.verify(envelope)` returns ok in `stub` mode; in `enforce` mode with no/garbage token returns not-ok. `PlayIntegrityVerifier` interface present (raises `NotImplementedError` placeholder body documented for later wiring with Google keys + package name + requestHash check).
- [ ] **Implement** `class AttestationResult`, `Verifier` protocol, `StubVerifier`, `PlayIntegrityVerifier` (real verification stubbed pending console access). Factory `get_verifier(mode)`. **Commit + push.**

---

## Task 9: Identity / profile service

**Files:** Create `services/identity.py`, `tests/test_identity.py`. Modify `web/api.py`.

- [ ] **Test:** register creates rider; second name change within same month → rejected (`can_change_name_after` in future); avatar bytes re-encoded to 64×64 PNG with EXIF stripped (assert size 64×64 and no EXIF); delete soft-deletes + clears PII.
- [ ] **Implement** `IdentityService` using `RiderRepo`; avatar pipeline with Pillow (`Image.open` → `convert("RGBA")` → `thumbnail/resize((64,64))` → save PNG, new image so EXIF dropped). Monthly rule: allow if `last_*_change` is None or in a previous calendar month. **Commit + push.**

---

## Task 10: Ingest endpoint + orchestration

**Files:** Create `services/ingest.py`, `ingest/schema.py`. Modify `web/api.py`, `main.py` (mount `/api/v1`).

- [ ] **Test (TestClient, multipart):** POST `/api/v1/trips` with `meta` (JSON) + gzipped sample CSV → 201, `validation_status` correct; re-POST same `trip_uuid` → 200 duplicate, no double-insert; bad attestation in enforce mode → 401; oversize/garbage → 413/422.
- [ ] **Implement** `IngestService.handle(meta, raw_bytes)`: verify attestation → dedupe → gunzip+parse → summarize → geo → plausibility → persist (Trip + TripTrack downsampled + RawUpload) → if validated call `Aggregator.apply(trip)`. Endpoint wires multipart, size cap, error codes from contract §8. **Commit + push.**

---

## Task 11: Aggregator

**Files:** Create `services/aggregator.py`, `tests/test_aggregator.py`.

- [ ] **Test:** applying two validated trips for one rider updates `rider_stats.total_km` = sum, `daily_distance`, `country_stats` (rider counted once), `map_cells` for the start cell at each zoom, and sets `records` (mileage/top-speed/max-g) to the better value; re-applying same trip is idempotent (guard via trip already-aggregated flag).
- [ ] **Implement** `Aggregator.apply(trip)` updating all materialized tables via `AggregateRepo`; streak update from `daily_distance`. Mark trip `aggregated=True` to prevent double counting. **Commit + push.**

---

## Task 12: Retention job

**Files:** Create `services/retention.py`, `tests/test_retention.py`. Modify `main.py` (lifespan: background interval task).

- [ ] **Test:** with `RETENTION_DAYS=0`, a validated trip's `RawUpload` is evicted but `Trip`+`TripTrack` remain; simulate low disk (monkeypatch `shutil.disk_usage`) → evicts oldest validated raw first until above floor.
- [ ] **Implement** `run_retention(now)`; schedule via an asyncio task every N minutes in lifespan (guarded, non-blocking). **Commit + push.**

---

## Task 13: Admin auth (TOTP, mirror finn)

**Files:** Create `web/admin.py`, `tests/test_admin.py`. Modify `main.py` (SessionMiddleware + mount).

- [ ] **Test:** unauthenticated `/admin` shows enroll/login; `POST /admin/verify-totp` with a code computed from the stored secret authenticates (session set); admin API returns 401 when not authed.
- [ ] **Implement** mirrored from `D:\GitHub\finn-home-finder\web\admin.py`: `_load/_save_state` to `data/admin.json`, `_get_session_secret`, TOTP enroll/verify, `_is_authenticated`. `main.py`: `app.add_middleware(SessionMiddleware, secret_key=_get_session_secret())`. Minimal admin status page (rider/trip counts, flagged queue count). **Commit + push.**

---

## Task 14: API polish — versioning, rate limit, error handlers

**Files:** Modify `web/api.py`, `main.py`. Create `tests/test_api_contract.py`.

- [ ] **Test:** error envelope shapes match contract §8 codes; basic per-IP rate limit returns 429 with `Retry-After`; `/api/v1` prefix correct.
- [ ] **Implement** exception handlers, a lightweight in-memory rate limiter, request size guard. **Commit + push.**

---

## Task 15: Deploy to droplet

**Files:** Create `scripts/deploy.py` (paramiko: rsync code to `/opt/eucstats`, `pip install -r requirements.txt`, `systemctl restart eucstats`, smoke-check).

- [ ] Sync repo (excluding `.venv`, `data`, `samples`, `.git`) to `/opt/eucstats`.
- [ ] `/opt/eucstats/.venv/bin/pip install -r requirements.txt`.
- [ ] `systemctl restart eucstats`; `journalctl -u eucstats -n 30`; `curl -sf https://eucstats.ried.no/health`.
- [ ] **Verify** HTTPS health 200. **Commit + push.**

---

## Task 16: End-to-end test with REAL trips + Playwright

**Files:** Create `scripts/send_samples.py`.

- [ ] `send_samples.py`: for each CSV in `samples/` (and optionally a larger ADB pull), build a `meta` envelope (synthetic `store_id`, `trip_uuid`=uuid5 of file, tz from filename/default, `attestation` stub), gzip the CSV, POST to `https://eucstats.ried.no/api/v1/trips`; register 2–3 synthetic riders first.
- [ ] Run it; assert all uploads 200/201 and statuses; query a debug/stats endpoint to confirm `rider_stats`, `country_stats` (NO), `map_cells`, `records` populated.
- [ ] **Playwright:** open `https://eucstats.ried.no/admin`, enroll/login with a test TOTP, confirm counts; screenshot. (Public site is a later plan; once built, Playwright drives the map + leaderboards.)
- [ ] **Commit + push.** Foundation milestone complete.

---

## Subsequent plans (written when reached — each its own working milestone)
- **`...-stats-engine.md`** — leaderboard queries/APIs over the materialized tables (mileage king, daily-distance avg, riders/country, total & avg km/country, weekly champion at week-close, longest streak, G-force records), week/day boundary policy.
- **`...-public-site.md`** — world map (10 km clusters from `map_cells`, 2–3 zooms), leaderboard pages, podiums (name/flag/avatar), eucviewer trip replay; Playwright-tested.
- **`...-admin-ui.md`** — flagged-trip review queue, rider moderation, retention/config controls (auth already built in Task 13).

## Self-review notes
- Spec coverage: ingestion(§6)→T3,4,6,7,8,10; identity(§7)→T9; storage/repo(§5,§8)→T2,12; admin(§9)→T13; non-functional(§10)→T14. Map cells/records/stats tables created in T2, populated in T11; full leaderboard *queries* deferred to the stats-engine plan (reads of the same tables).
- `store_id` opaque; attestation pluggable (stub default) — unblocks build without Play Console.
- Types consistent across tasks (`Sample`, `TripSummary`, repo method names).
