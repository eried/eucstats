"""Sandbox magic store_ids return deterministic responses when enabled (Android QA)."""
import gzip
import json

from fastapi.testclient import TestClient

import models
from main import app
from services import settings


_CSV = (b"Date,Speed,Voltage,Temperature,Battery level,Altitude,Latitude,Longitude,Total mileage,GPS speed,Current,PWM,G-Force,G-Force X,G-Force Y\n"
        b"01.06.2026 20:24:31.204,5,132,26,100,68,69.65,18.95,1000.0,5,1,10,0.5,0,0\n")


def _upload(client, store):
    files = {"trip": ("t.gz", gzip.compress(_CSV), "application/gzip")}
    return client.post("/api/v1/trips", data={"meta": json.dumps({"store_id": store, "trip_uuid": "x"})}, files=files)


def test_sandbox_off_magic_ids_are_inert(db, monkeypatch, tmp_path):
    monkeypatch.setattr(__import__("config"), "SITE_STATE_FILE", tmp_path / "site.json")
    assert settings.sandbox_enabled() is False
    with TestClient(app) as client:
        # with sandbox OFF, sandbox-banned is just an unregistered rider -> 400, not 403
        r = _upload(client, "sandbox-banned")
        assert r.status_code == 400 and "rider_not_registered" in r.text


def test_sandbox_on_returns_each_case(db, monkeypatch, tmp_path):
    monkeypatch.setattr(__import__("config"), "SITE_STATE_FILE", tmp_path / "site.json")
    settings.set_sandbox(True)
    with TestClient(app) as client:
        assert _upload(client, "sandbox-banned").status_code == 403
        assert "rider_banned" in _upload(client, "sandbox-banned").text
        assert _upload(client, "sandbox-429").status_code == 429
        assert _upload(client, "sandbox-413").status_code == 413
        assert _upload(client, "sandbox-422").status_code == 422
        ok = _upload(client, "sandbox-ok")
        assert ok.status_code == 201 and ok.json()["validation_status"] == "validated"
        fl = _upload(client, "sandbox-flagged")
        assert fl.json()["validation_status"] == "flagged" and fl.json()["verdict"] == "under_review"


def test_sandbox_register_paths(db, monkeypatch, tmp_path):
    monkeypatch.setattr(__import__("config"), "SITE_STATE_FILE", tmp_path / "site.json")
    settings.set_sandbox(True)
    with TestClient(app) as client:
        bad = client.post("/api/v1/riders", json={"store_id": "sandbox-400", "display_name": "X"})
        assert bad.status_code == 400
        ok = client.post("/api/v1/riders", json={"store_id": "sandbox-ok", "display_name": "X"})
        assert ok.status_code == 200 and ok.json().get("sandbox") is True
        # a magic register does NOT persist a real rider
        db.expire_all()
        assert db.get(models.Rider, "sandbox-ok") is None
