"""TOTP-protected admin (mirrors finn-home-finder/web/admin.py).

State (TOTP secret, enrolled flag, session secret) persists in
data/admin.json so sessions survive restarts. Full moderation UI is built
in the admin-UI plan; this provides auth + a status/flagged-queue view.
"""
from __future__ import annotations

import base64
import json
import os
from io import BytesIO

import pyotp
import qrcode
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

import config
from database import get_db
from models import Rider, Trip
from services.aggregator import Aggregator

admin_router = APIRouter(prefix="/admin", tags=["admin"])

STATE_FILE = config.ADMIN_STATE_FILE
TOTP_ISSUER = "eucstats"
TOTP_NAME = "admin"


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def _save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _get_session_secret() -> str:
    state = _load_state()
    if "session_secret" not in state:
        state["session_secret"] = os.urandom(32).hex()
        _save_state(state)
    return state["session_secret"]


def _is_enrolled() -> bool:
    return _load_state().get("enrolled", False)


def _get_or_create_secret() -> str:
    state = _load_state()
    if "totp_secret" not in state:
        state["totp_secret"] = pyotp.random_base32()
        state["enrolled"] = False
        _save_state(state)
    return state["totp_secret"]


def _verify_totp(code: str) -> bool:
    secret = _load_state().get("totp_secret")
    return bool(secret) and pyotp.TOTP(secret).verify(code, valid_window=1)


def _qr_b64(secret: str) -> str:
    uri = pyotp.TOTP(secret).provisioning_uri(name=TOTP_NAME, issuer_name=TOTP_ISSUER)
    buf = BytesIO()
    qrcode.make(uri).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _is_authenticated(request: Request) -> bool:
    return request.session.get("admin_auth", False)


def _counts(db: Session) -> dict:
    q = db.query(func.count(Trip.trip_uuid))
    return {
        "riders": db.query(func.count(Rider.store_id)).scalar(),
        "trips": q.scalar(),
        "validated": q.filter(Trip.validation_status == "validated").scalar(),
        "flagged": q.filter(Trip.validation_status == "flagged").scalar(),
    }


_PAGE = "<title>eucstats admin</title><style>body{{font-family:system-ui;max-width:640px;margin:3rem auto}}</style>{body}"


def _enroll_html(qr: str, secret: str) -> str:
    return _PAGE.format(body=f"""
    <h1>eucstats admin — enroll</h1>
    <p>Scan with an authenticator app, then enter the 6-digit code.</p>
    <img src="data:image/png;base64,{qr}" alt="totp qr"/>
    <p>Secret: <code>{secret}</code></p>
    <form method="post" action="/admin/verify-totp">
      <input name="code" placeholder="123456" autofocus/>
      <button>Verify</button>
    </form>""")


def _login_html(error: str = "") -> str:
    err = f'<p style="color:#c00">{error}</p>' if error else ""
    return _PAGE.format(body=f"""
    <h1>eucstats admin</h1>{err}
    <form method="post" action="/admin/verify-totp">
      <input name="code" placeholder="123456" autofocus/>
      <button>Log in</button>
    </form>""")


def _dash_html(db: Session) -> str:
    rows = "".join(f"<tr><td>{k}</td><td><b>{v}</b></td></tr>" for k, v in _counts(db).items())

    flagged = db.query(Trip).filter(Trip.validation_status == "flagged").order_by(desc(Trip.created_at)).limit(50).all()
    fhtml = "".join(
        f"<tr><td>{t.trip_uuid[:8]}</td><td>{t.rider_store_id}</td><td>{round(t.distance_km or 0,1)} km</td>"
        f"<td>{','.join(t.flag_reasons or [])}</td><td>"
        f"<form method=post action=/admin/trip/{t.trip_uuid}/approve style=display:inline><button>approve</button></form> "
        f"<form method=post action=/admin/trip/{t.trip_uuid}/reject style=display:inline><button>reject</button></form>"
        f"</td></tr>" for t in flagged) or "<tr><td colspan=5 style=color:#8a93b2>none</td></tr>"

    riders = db.query(Rider).order_by(desc(Rider.created_at)).limit(200).all()
    rhtml = "".join(
        f"<tr><td>{r.store_id}</td><td>{r.display_name}</td><td>{r.flag or ''}</td>"
        f"<td>{r.platform}</td><td>{'deleted' if r.deleted_at else 'active'}</td></tr>"
        for r in riders) or "<tr><td colspan=5 style=color:#8a93b2>none</td></tr>"

    trips = db.query(Trip).order_by(desc(Trip.created_at)).limit(30).all()
    thtml = "".join(
        f"<tr><td>{t.trip_uuid[:8]}</td><td>{t.rider_store_id}</td><td>{round(t.distance_km or 0,1)}</td>"
        f"<td>{t.country or ''}</td><td>{t.validation_status}</td></tr>"
        for t in trips) or "<tr><td colspan=5 style=color:#8a93b2>none</td></tr>"

    return _PAGE.format(body=f"""
    <form method="post" action="/admin/logout" style="float:right"><button>Log out</button></form>
    <h1>eucstats admin</h1>
    <table>{rows}</table>
    <h2>Flagged trips — review queue</h2>
    <table><tr><th>id</th><th>rider</th><th>dist</th><th>reasons</th><th>action</th></tr>{fhtml}</table>
    <h2>Riders</h2>
    <table><tr><th>store_id</th><th>name</th><th>flag</th><th>platform</th><th>status</th></tr>{rhtml}</table>
    <h2>Recent trips</h2>
    <table><tr><th>id</th><th>rider</th><th>km</th><th>country</th><th>status</th></tr>{thtml}</table>""")


@admin_router.get("", response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db)):
    if not _is_enrolled():
        secret = _get_or_create_secret()
        return HTMLResponse(_enroll_html(_qr_b64(secret), secret))
    if not _is_authenticated(request):
        return HTMLResponse(_login_html())
    return HTMLResponse(_dash_html(db))


@admin_router.post("/verify-totp")
def verify_totp(request: Request, code: str = Form(...)):
    if not _verify_totp(code):
        return HTMLResponse(_login_html("Invalid code. Try again."), status_code=401)
    if not _is_enrolled():
        state = _load_state()
        state["enrolled"] = True
        _save_state(state)
    request.session["admin_auth"] = True
    return RedirectResponse("/admin", status_code=303)


@admin_router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin", status_code=303)


@admin_router.get("/api/status")
def status(request: Request, db: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return _counts(db)


@admin_router.post("/trip/{trip_uuid}/approve")
def approve_trip(trip_uuid: str, request: Request, db: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    t = db.get(Trip, trip_uuid)
    if t and t.validation_status == "flagged":
        t.validation_status = "validated"
        t.flag_reasons = None
        db.commit()
        Aggregator(db).apply(t)   # now counts toward leaderboards
    return RedirectResponse("/admin", status_code=303)


@admin_router.post("/trip/{trip_uuid}/reject")
def reject_trip(trip_uuid: str, request: Request, db: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    t = db.get(Trip, trip_uuid)
    if t and t.validation_status == "flagged":
        t.validation_status = "rejected"
        db.commit()
    return RedirectResponse("/admin", status_code=303)
