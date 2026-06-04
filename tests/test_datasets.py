"""Dataset & snapshot manager tests (operate on the conftest temp DATA_DIR)."""
import shutil
import sqlite3
from datetime import date

import pytest

from services import datasets


def _reset_active_schema():
    from database import Base, engine, init_db
    engine.dispose()
    init_db()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    engine.dispose()


def _seed_active_rider(store_id="s1", name="Alice"):
    from database import SessionLocal
    import models
    s = SessionLocal()
    try:
        s.add(models.Rider(store_id=store_id, display_name=name, platform="google_play"))
        s.commit()
    finally:
        s.close()
    from database import engine
    engine.dispose()  # release the handle so a later swap can replace the file (Windows)


@pytest.fixture
def clean():
    d = datasets._datasets_dir()
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    _reset_active_schema()
    yield
    from database import engine
    engine.dispose()


def test_save_lists_with_counts(clean):
    _seed_active_rider()
    slug = datasets.save_current("snap one", note="hi")
    listing = datasets.list_datasets()
    entry = next(d for d in listing["datasets"] if d["slug"] == slug)
    assert entry["riders"] == 1
    assert entry["origin"] == "manual"
    assert entry["note"] == "hi"


def test_create_empty_has_schema_and_zero_rows(clean):
    slug = datasets.create_empty("fresh", is_test=True)
    path = datasets._datasets_dir() / f"{slug}.sqlite"
    st = datasets._file_stats(path)
    assert st["riders"] == 0
    assert st["trips"] == 0
    assert st["is_test"] is True


def test_import_rejects_non_sqlite(clean, tmp_path):
    junk = tmp_path / "notadb.sqlite"
    junk.write_text("this is not a database")
    with pytest.raises(datasets.DatasetError):
        datasets.import_file(str(junk), "junk")


def test_import_rejects_missing_tables(clean, tmp_path):
    bad = tmp_path / "partial.sqlite"
    con = sqlite3.connect(str(bad))
    con.execute("CREATE TABLE riders(store_id TEXT)")  # no trips table
    con.commit()
    con.close()
    with pytest.raises(datasets.DatasetError):
        datasets.import_file(str(bad), "partial")


def test_import_accepts_valid(clean, tmp_path):
    good = tmp_path / "real.sqlite"
    con = sqlite3.connect(str(good))
    con.execute("CREATE TABLE riders(store_id TEXT)")
    con.execute("CREATE TABLE trips(trip_uuid TEXT)")
    con.commit()
    con.close()
    slug = datasets.import_file(str(good), "real data")
    assert datasets._get_entry(slug)["origin"] == "imported"


def test_switch_roundtrip_and_is_test_travels(clean):
    _seed_active_rider("s1", "Alice")
    with_alice = datasets.save_current("with-alice")          # is_test default True
    empty_live = datasets.create_empty("go-live", is_test=False)

    datasets.switch_to(empty_live)
    st = datasets._file_stats(datasets._active())
    assert st["riders"] == 0
    assert st["is_test"] is False                              # live flag travelled
    assert datasets.list_datasets()["active"] == empty_live

    from database import engine
    engine.dispose()
    datasets.switch_to(with_alice)
    st = datasets._file_stats(datasets._active())
    assert st["riders"] == 1
    assert st["is_test"] is True


def test_switch_makes_safety_backup(clean):
    _seed_active_rider()
    target = datasets.create_empty("target", is_test=False)
    datasets.switch_to(target)
    pre = [d for d in datasets.list_datasets()["datasets"] if d["origin"] == "pre-switch"]
    assert len(pre) == 1


def test_auto_backup_idempotent_and_rotates(clean):
    _seed_active_rider()
    datasets.auto_backup(keep=2, today=date(2026, 6, 1))
    datasets.auto_backup(keep=2, today=date(2026, 6, 1))   # same day -> no dupe
    datasets.auto_backup(keep=2, today=date(2026, 6, 2))
    datasets.auto_backup(keep=2, today=date(2026, 6, 3))   # should prune 06-01
    autos = [d["slug"] for d in datasets.list_datasets()["datasets"] if d["origin"] == "auto"]
    assert sorted(autos) == ["auto-2026-06-02", "auto-2026-06-03"]


def test_delete_removes_file_and_entry(clean):
    _seed_active_rider()
    slug = datasets.save_current("temp")
    path = datasets._datasets_dir() / f"{slug}.sqlite"
    assert path.exists()
    datasets.delete(slug)
    assert not path.exists()
    assert datasets._get_entry(slug) is None
