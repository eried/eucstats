from fastapi.testclient import TestClient

from main import app


def test_health():
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_root():
    with TestClient(app) as client:
        r = client.get("/")
    assert r.status_code == 200
    assert r.json()["service"] == "eucstats"
