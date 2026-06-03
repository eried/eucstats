import json

import pyotp
from fastapi.testclient import TestClient

import config
from main import app


def test_totp_enroll_login_and_status(db):
    if config.ADMIN_STATE_FILE.exists():
        config.ADMIN_STATE_FILE.unlink()
    with TestClient(app) as client:
        # not enrolled -> enroll page, secret created
        r = client.get("/admin")
        assert r.status_code == 200
        secret = json.loads(config.ADMIN_STATE_FILE.read_text())["totp_secret"]

        # verify with a valid code -> authenticated session
        code = pyotp.TOTP(secret).now()
        r = client.post("/admin/verify-totp", data={"code": code})
        assert r.status_code in (200, 303)

        s = client.get("/admin/api/status")
        assert s.status_code == 200
        assert "riders" in s.json() and "flagged" in s.json()


def test_status_requires_auth(db):
    with TestClient(app) as client:
        assert client.get("/admin/api/status").status_code == 401
