# eucstats Dataset & Snapshot Manager — Design

**Goal:** Admin-only management of the SQLite database as named, portable "save-slots" — save the current dataset, create an empty one, import a real one, switch/restore between them safely, with scheduled rotated backups and a clean TEST→live cut-over.

**Approved decisions (2026-06-04):**
- Single active DB; real pre-launch uploads are accepted into the active dataset; a clean swap happens at launch (no dual-DB routing).
- Swap mechanism: **active-file + atomic copy + service restart**. Snapshots are plain portable `.sqlite` files.
- Defaults: daily auto-backup keep-14; full-file snapshots; restart-based switch; type-the-name to confirm destructive ops.

## Core model
- Active working DB: `config.DB_PATH` (`data/eucstats.sqlite`) — unchanged; receives all live writes.
- Snapshots: `data/datasets/<slug>.sqlite`. Manifest `data/datasets/manifest.json` records `{active, datasets:[{slug,name,created,is_test,note,origin,size,riders,trips}]}`.
- **`is_test` lives inside each dataset** (`app_meta` key/value table), so it travels on every swap. The active DB's `is_test` drives the `TEST DATA` watermark. Default when absent = `true` (failsafe: show the banner).

## Components
- `models.Meta` (`app_meta`: key TEXT pk, value TEXT) — auto-created by `create_all`; reusable for future metric toggles.
- `services/settings.py` — `get_meta/set_meta/is_test_dataset/set_test` over a session.
- `services/datasets.py` — file/manifest engine, no FastAPI deps:
  - `save_current(name,note,origin)` via SQLite **online backup API** (consistent on a live DB).
  - `create_empty(name,is_test)` via `Base.metadata.create_all` on a temp engine.
  - `switch_to(slug, restart)` — auto-backup current → copy snapshot to `eucstats.sqlite.incoming` → `os.replace` → delete stale `-wal/-shm` → `restart()`.
  - `import_sqlite`, `export_path`, `delete`, `rename`, `auto_backup(keep,today)`.
- `web/admin.py` — `/admin/datasets` page + POST actions (save/new/import/switch/delete/rename/flag) + export download + a "reconnecting" page that polls `/health`. All behind TOTP; destructive actions require typing the dataset name.
- `web/public.py` — `__TESTWM__` placeholder; `home()` injects the watermark only when active `is_test`.
- `scripts/run_jobs.py` — adds `auto_backup(keep=14)` (idempotent per day).

## Switch safety
Online-backup the current dataset first. The snapshot copied in is a clean single file (no WAL). After `os.replace` of the main file, stale `eucstats.sqlite-wal/-shm` MUST be deleted before restart or SQLite would replay the old WAL onto the new file (corruption). Restart is a detached `sleep 1; systemctl restart eucstats` so the HTTP response flushes first; the service runs as root so it can restart itself.

## Error handling
- Every destructive op auto-backs-up first; atomic file + manifest writes (temp + `os.replace`); imports validated (`integrity_check` + required tables `riders`,`trips`) before acceptance; disk-floor check; failed restart surfaces "restart manually" (file already swapped, data safe).

## Testing
Unit tests on a temp `DATA_DIR` (restart injected as no-op): save→list→switch→restore round-trip; empty has schema + 0 rows; import rejects non-sqlite / missing tables; rotation keeps N; `is_test` travels with the file.

## Out of scope (separate sub-projects)
Ingest/pipeline monitor (B), metric show/hide toggles (C, will reuse `app_meta`), Microsoft Clarity (D, shipped).
