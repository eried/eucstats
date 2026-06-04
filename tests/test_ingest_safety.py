"""Ingest hardening: gzip-bomb cap, sample cap, NaN/Inf rejection."""
import gzip

import pytest

import config
import models
from ingest.parser import _f
from services.ingest import IngestError, IngestService


def _rider(db, sid="a"):
    db.add(models.Rider(store_id=sid, display_name="A", platform="google_play"))
    db.commit()


def test_gzip_bomb_rejected(db, monkeypatch):
    monkeypatch.setattr(config, "MAX_DECOMPRESSED_MB", 1)   # 1 MB cap
    monkeypatch.setattr(config, "INGEST_ALLOW", [])
    _rider(db, "a")
    bomb = gzip.compress(b"0" * (3 * 1024 * 1024))          # 3 MB -> exceeds cap
    with pytest.raises(IngestError) as e:
        IngestService(db).handle({"store_id": "a", "trip_uuid": "b1"}, bomb)
    assert e.value.code == 413


def test_tz_offset_clamped_not_500(db, monkeypatch):
    monkeypatch.setattr(config, "INGEST_ALLOW", [])
    _rider(db, "a")
    csv = b"Date,Speed\n2026-06-04T10:00:00,12\n2026-06-04T10:00:01,13\n"
    # absurd tz offset must not raise OverflowError/500 — clamped then parsed
    res = IngestService(db).handle({"store_id": "a", "trip_uuid": "tz1",
                                    "tz_offset_min": 10 ** 12}, csv)
    assert res["trip_uuid"] == "tz1"


def test_f_rejects_non_finite():
    assert _f("nan") is None
    assert _f("inf") is None
    assert _f("-inf") is None
    assert _f("12.5") == 12.5
