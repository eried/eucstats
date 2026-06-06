import gzip
import json

from fastapi.testclient import TestClient

import config
from main import app
from services import health


def test_health():
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_root_serves_site():
    with TestClient(app) as client:
        r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "eucstats" in r.text.lower()


# --- ops log (data/health.log): ingest lines + periodic heartbeat ---

_CSV = (b"Date,Speed,Voltage,Temperature,Battery level,Altitude,Latitude,Longitude,Total mileage,GPS speed,Current,PWM,G-Force,G-Force X,G-Force Y\n"
        b"01.06.2026 20:24:31.204,5,132,26,100,68,69.6500,18.9500,1000.0,5,1,10,0.5,0,0\n"
        b"01.06.2026 20:24:41.204,20,132,26,100,68,69.6510,18.9510,1000.1,20,5,40,1.0,0,0\n")


def test_log_ingest_and_tail(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    health.log_ingest("riderXYZ123", "tripABC987", "validated", dist=1.2, ms=42, size=500)
    assert any("ingest" in l and "validated" in l and "rider=riderXYZ" in l and "trip=tripABC9" in l
               for l in health.tail(5))


def test_upload_writes_ingest_line(db, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with TestClient(app) as c:
        c.post("/api/v1/riders", json={"store_id": "hlog", "display_name": "Health Tester", "flag": "NO"})
        files = {"trip": ("t.gz", gzip.compress(_CSV), "application/gzip")}
        r = c.post("/api/v1/trips",
                   data={"meta": json.dumps({"store_id": "hlog", "trip_uuid": "hl-1", "tz": "Europe/Oslo"})},
                   files=files)
        assert r.status_code in (200, 201), r.text
        assert any("trip=hl-1" in l and "ingest" in l for l in health.tail(20))


def test_heartbeat_runs(db, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    health.heartbeat(db)                        # must never raise
    assert any("health" in l and "riders=" in l for l in health.tail(5))
