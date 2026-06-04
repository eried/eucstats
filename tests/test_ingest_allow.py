"""Ingest allowlist gate (EUCSTATS_INGEST_ALLOW)."""
import pytest

import config
from services.ingest import IngestError, IngestService


def test_allowlist_blocks_other_store(db, monkeypatch):
    monkeypatch.setattr(config, "INGEST_ALLOW", ["my_store"])
    with pytest.raises(IngestError) as e:
        IngestService(db).handle({"store_id": "someone_else", "trip_uuid": "t1"}, b"x")
    assert e.value.code == 403
    assert "allowlist" in e.value.detail


def test_allowlist_lets_listed_store_through(db, monkeypatch):
    monkeypatch.setattr(config, "INGEST_ALLOW", ["my_store"])
    # passes the allowlist, then fails later because the rider isn't registered
    with pytest.raises(IngestError) as e:
        IngestService(db).handle({"store_id": "my_store", "trip_uuid": "t1"}, b"x")
    assert e.value.code == 400  # rider_not_registered — i.e. it got past the allowlist


def test_allowlist_off_accepts_any(db, monkeypatch):
    monkeypatch.setattr(config, "INGEST_ALLOW", [])
    with pytest.raises(IngestError) as e:
        IngestService(db).handle({"store_id": "anyone", "trip_uuid": "t1"}, b"x")
    assert e.value.code == 400  # not 403 — allowlist disabled
