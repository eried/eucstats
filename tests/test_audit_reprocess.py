"""Admin audit log (flat file) + re-process trips with current calibration."""
import gzip
import json

import pyotp
from fastapi.testclient import TestClient

import config
import models
from main import app
from services import audit, settings


# ---------- audit log ----------

def test_audit_log_writes_and_tails(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    audit.log("ban", "rider=x reason=fraud")
    audit.log("dataset_switch", "to=live")
    lines = audit.tail()
    assert len(lines) == 2
    assert "dataset_switch" in lines[0] and "to=live" in lines[0]   # newest first
    assert "ban" in lines[1]


def test_audit_tail_empty_when_no_file(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "nope")
    assert audit.tail() == []


def _auth(client):
    if config.ADMIN_STATE_FILE.exists():
        config.ADMIN_STATE_FILE.unlink()
    client.get("/admin")
    secret = json.loads(config.ADMIN_STATE_FILE.read_text())["totp_secret"]
    client.post("/admin/verify-totp", data={"code": pyotp.TOTP(secret).now()})


def test_ban_writes_audit_and_page_shows_it(db):
    db.add(models.Rider(store_id="z", display_name="Z", platform="google_play"))
    db.commit()
    with TestClient(app) as client:
        _auth(client)
        client.post("/admin/rider/z/ban", data={"reason": "spoofing"}, follow_redirects=False)
        page = client.get("/admin/system")   # audit log folded into System now
        assert page.status_code == 200
        assert "ban" in page.text and "rider=z" in page.text


# ---------- re-process with calibration ----------

_CSV = (b"Date,Speed,Voltage,Temperature,Battery level,Altitude,Latitude,Longitude,Total mileage,GPS speed,Current,PWM,G-Force,G-Force X,G-Force Y\n"
        b"01.06.2026 20:24:31.204,0,84,26,100,68,69.6500,18.9500,1000.0,0,1,0,0.3,0,0\n"
        b"01.06.2026 20:24:32.204,50,80,26,100,68,69.6501,18.9501,1000.0,50,8,40,0.3,0,0\n"
        b"01.06.2026 20:24:33.204,10,84,26,100,68,69.6502,18.9502,1000.1,10,1,10,0.3,0,0\n")


def test_reprocess_applies_new_calibration(db):
    from services.reprocess import reprocess_with_calibration, raw_available_count
    meta = {"store_id": "rp", "platform": "google_play", "source_app": "eucplanet",
            "schema_version": "eucplanet-v3-gforce", "tz": "Europe/Oslo"}
    with TestClient(app) as client:
        _auth(client)
        client.post("/api/v1/riders", json={"store_id": "rp", "display_name": "Ripley", "flag": "NO"})
        files = {"trip": ("t.gz", gzip.compress(_CSV), "application/gzip")}
        r = client.post("/api/v1/trips", data={"meta": json.dumps({**meta, "trip_uuid": "tp1"})}, files=files)
        assert r.status_code == 201

    db.expire_all()
    assert raw_available_count(db) == 1
    t = db.get(models.Trip, "tp1")
    # default cap 20 km/h/s: 0->50 in 1s is a freespin spike, realistic stays low
    assert t.max_freespin == 50.0 and t.max_speed <= 25

    # raise the cap so 0->50 in 1s is now "believable" -> realistic max ~50, no freespin
    settings.set_calibration(db, {"max_accel": 80})
    out = reprocess_with_calibration(db)
    assert out["reprocessed"] == 1 and out["failed"] == 0
    db.expire_all()
    t = db.get(models.Trip, "tp1")
    assert t.max_speed >= 49 and t.max_freespin is None     # recomputed with the new calibration


def test_reprocess_admin_route(db):
    with TestClient(app) as client:
        _auth(client)
        r = client.post("/admin/pipeline/reprocess", follow_redirects=False)
        assert r.status_code == 303


# 3 moving samples; a 26->0->26 dropout cliff in the middle. De-spiked temp = 26.
_CSV_TEMP = (b"Date,Speed,Voltage,Temperature,Battery level,Altitude,Latitude,Longitude,Total mileage,GPS speed,Current,PWM,G-Force,G-Force X,G-Force Y\n"
             b"01.06.2026 20:24:31.204,20,84,26,100,68,69.6500,18.9500,1000.0,20,1,0,0.3,0,0\n"
             b"01.06.2026 20:24:32.204,20,84,0,100,68,69.6501,18.9501,1000.1,20,1,0,0.3,0,0\n"
             b"01.06.2026 20:24:33.204,20,84,26,100,68,69.6502,18.9502,1000.2,20,1,0,0.3,0,0\n")


def test_reprocess_refreshes_temp_extremes(db):
    # Regression: reprocess must copy per-trip temp extremes back. They were omitted,
    # so a stale/garbage min_temp (e.g. a 0 dropout) survived a reprocess and kept
    # winning the "Coldest ride" board.
    from services.reprocess import reprocess_with_calibration
    meta = {"store_id": "rt", "platform": "google_play", "source_app": "eucplanet",
            "schema_version": "eucplanet-v3-gforce", "tz": "Europe/Oslo"}
    with TestClient(app) as client:
        _auth(client)
        client.post("/api/v1/riders", json={"store_id": "rt", "display_name": "Temp", "flag": "NO"})
        files = {"trip": ("t.gz", gzip.compress(_CSV_TEMP), "application/gzip")}
        r = client.post("/api/v1/trips", data={"meta": json.dumps({**meta, "trip_uuid": "tt1"})}, files=files)
        assert r.status_code == 201

    db.expire_all()
    t = db.get(models.Trip, "tt1")
    assert t.min_temp == 26.0 and t.max_temp == 26.0     # de-spiked at ingest already
    # simulate an older summarizer having stored garbage
    t.min_temp = -300.0
    t.max_temp = 999.0
    db.commit()

    out = reprocess_with_calibration(db)
    assert out["reprocessed"] == 1 and out["failed"] == 0
    db.expire_all()
    t = db.get(models.Trip, "tt1")
    assert t.min_temp == 26.0 and t.max_temp == 26.0     # refreshed, garbage gone
