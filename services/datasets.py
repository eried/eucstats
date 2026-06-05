"""Dataset & snapshot manager.

A "dataset" is one portable SQLite file. The live working DB is
``config.DB_PATH``; named snapshots live in ``data/datasets/<slug>.sqlite`` and
are tracked in ``data/datasets/manifest.json``. Saving uses the SQLite online
backup API (consistent even while the app is writing). Switching atomically
replaces the active file, removes the stale WAL, and restarts the service.

This module has no FastAPI/web dependency so it is unit-testable in isolation;
the web layer injects the restart callable.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

import config

REQUIRED_TABLES = {"riders", "trips"}


class DatasetError(Exception):
    """Raised for any invalid dataset operation (surfaced to the admin UI)."""


# --- paths (resolved from config at call time so tests can repoint DATA_DIR) ---

def _datasets_dir() -> Path:
    return Path(config.DATA_DIR) / "datasets"


def _active() -> Path:
    return Path(config.DB_PATH)


def _manifest_path() -> Path:
    return _datasets_dir() / "manifest.json"


def _ensure_dirs() -> None:
    _datasets_dir().mkdir(parents=True, exist_ok=True)


# --- manifest (atomic json) ---

def _load() -> dict:
    p = _manifest_path()
    if p.exists():
        try:
            m = json.loads(p.read_text())
            m.setdefault("active", None)
            m.setdefault("datasets", [])
            return m
        except Exception:
            pass
    return {"active": None, "datasets": []}


def _save(m: dict) -> None:
    _ensure_dirs()
    p = _manifest_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2))
    os.replace(tmp, p)


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "dataset"


def _unique_slug(name: str) -> str:
    base = _slugify(name)
    existing = {d["slug"] for d in _load()["datasets"]}
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


def _get_entry(slug: str) -> Optional[dict]:
    return next((d for d in _load()["datasets"] if d["slug"] == slug), None)


# --- sqlite helpers ---

def _online_backup(src_path: Path, dst_path: Path) -> None:
    """Consistent copy of a (possibly live) SQLite DB via the backup API."""
    src = sqlite3.connect(str(src_path))
    try:
        dst = sqlite3.connect(str(dst_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _file_stats(path: Path) -> dict:
    out = {"size": 0, "riders": None, "trips": None, "is_test": True}
    try:
        out["size"] = os.path.getsize(path)
    except OSError:
        pass
    try:
        con = sqlite3.connect(str(path))
        try:
            def scalar(q):
                try:
                    return con.execute(q).fetchone()[0]
                except Exception:
                    return None
            out["riders"] = scalar("SELECT COUNT(*) FROM riders")
            out["trips"] = scalar("SELECT COUNT(*) FROM trips")
            v = scalar("SELECT value FROM app_meta WHERE key='is_test'")
            if v is not None:
                out["is_test"] = str(v) not in ("0", "false", "False", "")
        finally:
            con.close()
    except Exception:
        pass
    return out


def _validate_sqlite(path: Path) -> tuple[bool, str]:
    try:
        con = sqlite3.connect(str(path))
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
    except Exception:
        return False, "not a readable SQLite database"
    if ok != "ok":
        return False, f"integrity check failed: {ok}"
    missing = REQUIRED_TABLES - tables
    if missing:
        return False, "missing expected tables: " + ", ".join(sorted(missing))
    return True, ""


def _disk_floor_ok() -> None:
    try:
        free_gb = shutil.disk_usage(str(_datasets_dir().parent)).free / 1e9
    except Exception:
        return
    if free_gb < config.DISK_FLOOR_GB:
        raise DatasetError(
            f"refusing: only {free_gb:.1f} GB free (floor {config.DISK_FLOOR_GB} GB)")


def _record(slug: str, name: str, note: str, origin: str) -> None:
    st = _file_stats(_datasets_dir() / f"{slug}.sqlite")
    m = _load()
    m["datasets"] = [d for d in m["datasets"] if d["slug"] != slug]
    m["datasets"].append({
        "slug": slug, "name": name, "created": _now(),
        "is_test": st["is_test"], "note": note, "origin": origin,
        "size": st["size"], "riders": st["riders"], "trips": st["trips"],
    })
    _save(m)


# --- public API ---

def list_datasets() -> dict:
    m = _load()
    ds = sorted(m["datasets"], key=lambda x: x.get("created", ""), reverse=True)
    return {"active": m.get("active"), "datasets": ds}


def save_current(name: str, note: str = "", origin: str = "manual") -> str:
    """Snapshot the live DB into a new named slot. Returns its slug."""
    _ensure_dirs()
    active = _active()
    if not active.exists():
        raise DatasetError("no active database to save")
    _disk_floor_ok()
    slug = _unique_slug(name)
    dst = _datasets_dir() / f"{slug}.sqlite"
    tmp = dst.with_suffix(".sqlite.tmp")
    _online_backup(active, tmp)
    os.replace(tmp, dst)
    _record(slug, name, note, origin)
    return slug


def create_empty(name: str, is_test: bool = False, note: str = "") -> str:
    """Create a fresh, schema-only dataset (zero rows)."""
    from sqlalchemy import create_engine
    from database import Base
    import models  # noqa: F401  (registers tables on Base.metadata)

    _ensure_dirs()
    slug = _unique_slug(name)
    path = _datasets_dir() / f"{slug}.sqlite"
    eng = create_engine(f"sqlite:///{path}")
    try:
        Base.metadata.create_all(eng)
    finally:
        eng.dispose()
    con = sqlite3.connect(str(path))
    try:
        con.execute("INSERT OR REPLACE INTO app_meta(key,value) VALUES('is_test',?)",
                    ("1" if is_test else "0",))
        con.commit()
    finally:
        con.close()
    _record(slug, name, note, origin="empty")
    return slug


def import_file(tmp_path: str, name: str, note: str = "") -> str:
    """Validate an uploaded .sqlite and store it as a new dataset slot."""
    _ensure_dirs()
    ok, why = _validate_sqlite(Path(tmp_path))
    if not ok:
        raise DatasetError(f"rejected import: {why}")
    _disk_floor_ok()
    slug = _unique_slug(name)
    dst = _datasets_dir() / f"{slug}.sqlite"
    shutil.copyfile(tmp_path, dst)
    _record(slug, name, note, origin="imported")
    return slug


def export_path(slug: str) -> Path:
    p = _datasets_dir() / f"{slug}.sqlite"
    if not _get_entry(slug) or not p.exists():
        raise DatasetError(f"unknown dataset: {slug}")
    return p


def rename(slug: str, new_name: str) -> None:
    m = _load()
    entry = next((d for d in m["datasets"] if d["slug"] == slug), None)
    if not entry:
        raise DatasetError(f"unknown dataset: {slug}")
    entry["name"] = new_name
    _save(m)


def delete(slug: str) -> None:
    p = _datasets_dir() / f"{slug}.sqlite"
    if p.exists():
        p.unlink()
    m = _load()
    m["datasets"] = [d for d in m["datasets"] if d["slug"] != slug]
    if m.get("active") == slug:
        m["active"] = None
    _save(m)


def switch_to(slug: str, reload_app: Optional[Callable[[], None]] = None) -> str:
    """Make <slug> the active dataset: auto-backup current, atomically replace
    the active file, drop the stale WAL, then ask the app to reconnect.

    ``reload_app`` (in production: ``engine.dispose``) closes the pooled
    connections so the next request opens the freshly-swapped file — no service
    restart needed. Tests pass nothing (the file swap is what they assert)."""
    entry = _get_entry(slug)
    if not entry:
        raise DatasetError(f"unknown dataset: {slug}")
    snap = _datasets_dir() / f"{slug}.sqlite"
    if not snap.exists():
        raise DatasetError("snapshot file is missing on disk")
    active = _active()
    # 1) safety backup of whatever is live right now
    if active.exists():
        save_current(_timestamped("pre-switch"),
                     note=f"auto backup before switching to {entry['name']}",
                     origin="pre-switch")
    # 2) stage the incoming file, then atomically swap it in
    incoming = Path(str(active) + ".incoming")
    shutil.copyfile(snap, incoming)
    os.replace(incoming, active)
    # 3) remove the previous DB's WAL/SHM so SQLite can't replay it onto the new file
    for ext in ("-wal", "-shm"):
        stale = Path(str(active) + ext)
        if stale.exists():
            stale.unlink()
    # 4) bring an older snapshot's schema up to date (adds any missing columns)
    try:
        from database import ensure_schema
        ensure_schema(str(active))
    except Exception:
        pass
    # 5) record + make the running app reconnect to the new file
    m = _load()
    m["active"] = slug
    _save(m)
    if reload_app:
        reload_app()
    return slug


def auto_backup(keep: int = 14, today: Optional[date] = None) -> Optional[str]:
    """Daily rotated backup. Idempotent per day; prunes old auto-* beyond keep."""
    d = today or date.today()
    name = f"auto-{d.isoformat()}"
    slug = _slugify(name)
    created = None
    if not _get_entry(slug):
        created = save_current(name, note="scheduled backup", origin="auto")
    if keep > 0:
        autos = sorted((x for x in _load()["datasets"] if x.get("origin") == "auto"),
                       key=lambda x: x.get("created", ""))
        for old in autos[:-keep]:
            delete(old["slug"])
    return created or slug


def _timestamped(prefix: str) -> str:
    return f"{prefix}-{datetime.utcnow().strftime('%Y-%m-%d-%H%M%S')}"
