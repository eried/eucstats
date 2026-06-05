"""TOTP-protected admin (mirrors finn-home-finder/web/admin.py).

State (TOTP secret, enrolled flag, session secret) persists in
data/admin.json so sessions survive restarts. Full moderation UI is built
in the admin-UI plan; this provides auth + a status/flagged-queue view.
"""
from __future__ import annotations

import base64
import html
import json
import os
import tempfile
from datetime import datetime, timedelta
from io import BytesIO
from urllib.parse import quote

import pyotp
import qrcode
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

import config
from database import get_db
from models import RawUpload, Rider, RiderStat, Trip, Wheel
from services import datasets, settings
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


_NAV = [("/admin", "Overview"), ("/admin/explorer", "Explorer"),
        ("/admin/datasets", "Datasets"), ("/admin/pipeline", "Pipeline"),
        ("/admin/metrics", "Metrics"), ("/admin/system", "System"),
        ("/admin/settings", "Settings")]

_IC = {
    "check": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>',
    "x": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    "db": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>',
    "pulse": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l3 8 4-16 3 8h4"/></svg>',
    "sliders": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7h16M4 17h16" stroke-linecap="round"/><circle cx="9" cy="7" r="2.3"/><circle cx="15" cy="17" r="2.3"/></svg>',
    "search": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/></svg>',
    "ban": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/></svg>',
    "back": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg>',
}

_ADMIN_CSS = """<link rel=preconnect href="https://fonts.googleapis.com"><link rel=preconnect href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@400;500;600;700&family=Orbitron:wght@600;700;800&display=swap" rel=stylesheet><style>
*{box-sizing:border-box}
body{margin:0;font-family:'Chakra Petch',system-ui,sans-serif;background:radial-gradient(1100px 560px at 75% -12%,#16223f,transparent),#0a0f1e;color:#e9eefb;min-height:100vh}
a{color:#2ea8ff;text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1040px;margin:0 auto;padding:22px 18px 70px}
header.bar{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:16px;padding:12px 18px;background:rgba(10,15,30,.85);backdrop-filter:blur(12px);border-bottom:1px solid #26345e}
.brand{font-family:Orbitron,sans-serif;font-weight:800;letter-spacing:.5px;color:#ffd24a;font-size:14px;white-space:nowrap}
.brand b{color:#e9eefb}
nav.tabs2{display:flex;gap:3px;flex:1;flex-wrap:wrap}
nav.tabs2 a{padding:7px 12px;border-radius:9px;color:#8ea0c8;font-weight:600;font-size:13px}
nav.tabs2 a.on{background:rgba(46,168,255,.16);color:#2ea8ff}
nav.tabs2 a:hover{background:rgba(255,255,255,.05);text-decoration:none}
h1{font-family:Orbitron,sans-serif;font-weight:700;letter-spacing:.4px;font-size:22px;margin:6px 0 4px}
.sub{color:#8ea0c8;margin:0 0 18px;font-size:13.5px}
h2{font-family:Orbitron,sans-serif;font-weight:600;font-size:13.5px;letter-spacing:.4px;margin:2px 0 8px;color:#cfdcff}
.card{background:linear-gradient(160deg,#121a30,#0e1528);border:1px solid #26345e;border-radius:12px;padding:16px 18px;margin:0 0 16px;box-shadow:0 10px 30px rgba(0,0,0,.35)}
.hint,.mut{color:#8ea0c8;font-size:12.5px}
.hint{margin:.1rem 0 12px}
label{font-size:13px}
input,button,select{font:inherit}
input[type=text],input:not([type]),input[type=number]{background:#0b1124;border:1px solid #26345e;color:#e9eefb;padding:9px 11px;border-radius:9px;outline:none}
input::placeholder{color:#5d6f95}
input:focus{border-color:#2ea8ff}
input[type=file]{color:#8ea0c8;font-size:12px}
button,a.btn{display:inline-flex;align-items:center;gap:7px;padding:9px 14px;border-radius:9px;border:1px solid #2ea8ff;background:#2ea8ff;color:#04122a;font-weight:700;cursor:pointer;font-size:13px;text-decoration:none}
button:hover,a.btn:hover{filter:brightness(1.08);text-decoration:none}
button svg,a.btn svg{width:15px;height:15px}
a.btn{background:transparent;color:#cfe4ff;border-color:#26345e}
button.danger{background:transparent;color:#ff8585;border-color:rgba(255,107,107,.5)}
button.danger:hover{background:rgba(255,107,107,.12);filter:none}
button.go{background:linear-gradient(120deg,#13a05a,#0c7a44);border-color:#0c7a44;color:#eafff3}
button.ghost{background:transparent;color:#cfe4ff;border-color:#26345e}
.inline{display:inline-flex;gap:7px;flex-wrap:wrap;align-items:center;margin:6px 12px 6px 0}
.kpi{display:flex;gap:14px;flex-wrap:wrap}
.kpi .box{background:#0b1124;border:1px solid #26345e;border-radius:11px;padding:12px 16px;min-width:118px}
.kpi .n{font-family:Orbitron,sans-serif;font-size:25px}
.kpi .l{color:#8ea0c8;font-size:11px;text-transform:uppercase;letter-spacing:.6px;margin-top:2px}
table{border-collapse:collapse;width:100%;font-size:13px}
th{text-align:left;color:#8ea0c8;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid #26345e;padding:8px}
td{border-bottom:1px solid rgba(38,52,94,.5);padding:9px 8px;vertical-align:middle}
tr.active{background:rgba(46,168,255,.08)}
.b,.chip,.badge{display:inline-block;font-size:11px;font-weight:700;padding:2px 9px;border-radius:20px}
.chip{margin:0 6px 6px 0}
.b.test,.badge.test,.chip.rejected,.badge.rejected{background:rgba(255,107,107,.16);color:#ff9d9d}
.b.live,.badge.live,.chip.validated,.badge.validated{background:rgba(57,217,138,.16);color:#7ff0b6}
.chip.flagged,.badge.flagged{background:rgba(255,206,90,.16);color:#ffd98a}
.chip.pending,.badge.pending{background:rgba(142,160,200,.16);color:#aab8da}
.flash{padding:11px 14px;border-radius:10px;margin:0 0 16px;font-size:13.5px;border:1px solid}
.flash.ok{background:rgba(57,217,138,.1);border-color:rgba(57,217,138,.4);color:#9ff0c4}
.flash.err{background:rgba(255,107,107,.1);border-color:rgba(255,107,107,.4);color:#ffb0b0}
.bar{display:flex;align-items:center;gap:10px;margin:3px 0;font-size:12px}
.bar .d{width:46px;color:#8ea0c8}
.bar .track{flex:1;background:#0b1124;border-radius:6px;height:15px;overflow:hidden;border:1px solid #26345e}
.bar .fill{height:100%;background:linear-gradient(90deg,#2ea8ff,#7fd0ff);min-width:2px}
.bar .n{width:36px;text-align:right}
.toggle{display:flex;align-items:center;gap:9px;padding:8px 10px;border:1px solid #26345e;border-radius:9px;background:#0b1124;cursor:pointer;font-size:13px}
.toggle input{width:16px;height:16px;accent-color:#2ea8ff}
.grid2{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:7px}
.mtree2{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:12px;align-items:start;margin:0 0 16px}
.mnode{border:1px solid #26345e;border-radius:11px;background:#0b1124;overflow:hidden}
.mhead{display:flex;align-items:flex-start;gap:11px;padding:11px 13px;background:linear-gradient(160deg,#16203c,#0e1528);cursor:pointer}
.mhead input{width:17px;height:17px;accent-color:#2ea8ff;margin-top:2px;flex:none}
.mhead .tw{flex:1;min-width:0}
.mhead .t{font-weight:600;font-size:14px;display:block}
.mhead .d{color:#8ea0c8;font-size:12px;display:block;margin-top:3px}
.mhead .cnt{color:#8ea0c8;font-size:11px;white-space:nowrap;padding-top:2px}
.kids{padding:7px 11px 11px;display:flex;flex-direction:column;gap:5px;transition:opacity .15s}
.kids.dim{opacity:.4}
.krow{display:flex;align-items:flex-start;gap:10px;padding:7px 10px;border:1px solid #1d2945;border-radius:8px;background:#0d142a;cursor:pointer}
.krow:hover{border-color:#2ea8ff}
.krow input{width:15px;height:15px;accent-color:#2ea8ff;margin-top:2px;flex:none}
.krow .kl{font-size:13px;color:#e7ecfb;display:block}
.krow .kd{font-size:11.5px;color:#8ea0c8;display:block;margin-top:2px}
.krow.off .kl{text-decoration:line-through;color:#8ea0c8}
.mnone{color:#8ea0c8;font-size:12px;padding:0 2px 4px}
.searchbar{display:flex;gap:8px;margin:0 0 16px;flex-wrap:wrap;align-items:center}
.searchbar input,.searchbar select{flex:1;min-width:160px}
.dl{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:8px}
.dl .f{background:#0b1124;border:1px solid #1d2945;border-radius:9px;padding:8px 11px}
.dl .f .k{color:#8ea0c8;font-size:10.5px;text-transform:uppercase;letter-spacing:.5px}
.dl .f .v{font-size:14px;margin-top:3px;word-break:break-word}
.dl .f.hi{border-color:rgba(255,206,90,.5);background:rgba(255,206,90,.07)}.dl .f.hi .v{color:#ffd98a}
tr.clk{cursor:pointer}tr.clk:hover{background:rgba(46,168,255,.08)}
.bk{display:inline-flex;align-items:center;gap:5px;color:#8ea0c8;font-size:12.5px;margin-bottom:10px}
.banbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0}
.banbar input{flex:1;min-width:160px}
pre.j{background:#0b1124;border:1px solid #1d2945;border-radius:9px;padding:11px 13px;overflow:auto;font-size:12px;color:#cfe4ff;margin:0;max-height:280px}
.acts{display:flex;flex-direction:column;gap:5px}
.acts form{display:flex;gap:5px;align-items:center;justify-content:flex-end;margin:0}
.acts form button{min-width:74px;justify-content:center}
.acts input{width:118px;padding:6px 8px}
.mini,.acts button,.acts a.btn{padding:6px 10px;font-size:12px}
.center{max-width:430px;margin:60px auto;padding:0 18px;text-align:center}
.center .card{padding:26px}
.qr{width:208px;height:208px;border-radius:12px;background:#fff;padding:10px;margin:8px auto;display:block}
code{background:#0b1124;border:1px solid #26345e;padding:3px 8px;border-radius:6px;color:#ffd24a;font-size:12px;word-break:break-all}
.codein{font-size:20px;letter-spacing:6px;text-align:center;width:180px}
.qa{display:flex;gap:8px;flex-wrap:wrap;margin-top:4px}
</style>"""


def _admin_shell(inner: str, active: str = "", chrome: bool = True) -> str:
    head = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>eucstats admin</title>" + _ADMIN_CSS + "</head><body>")
    if not chrome:
        return head + '<div class=center>' + inner + '</div></body></html>'
    tabs = "".join('<a href="%s"%s>%s</a>' % (h, " class=on" if h == active else "", lbl)
                   for h, lbl in _NAV)
    header = ('<header class=bar><span class=brand>EUC<b>STATS</b> · admin</span>'
              '<nav class=tabs2>' + tabs + '</nav>'
              '<form method=post action="/admin/logout" style="margin:0">'
              '<button class="ghost mini">Log out</button></form></header>')
    return head + header + '<div class=wrap>' + inner + '</div></body></html>'


def _enroll_html(qr: str, secret: str) -> str:
    inner = f"""
    <div class=card>
      <div class=brand style="font-size:19px;margin-bottom:8px">EUC<b>STATS</b></div>
      <h1 style="margin:0 0 4px">Set up admin access</h1>
      <p class=hint>One-time enrollment. Scan this code with an authenticator app
      (Google Authenticator, Authy, 1Password…), then enter the 6-digit code it shows.</p>
      <img class=qr src="data:image/png;base64,{qr}" alt="authenticator QR code"/>
      <p class=hint>Can't scan? Add this key by hand:<br><code>{secret}</code></p>
      <form method="post" action="/admin/verify-totp">
        <input class=codein name="code" placeholder="000000" inputmode="numeric" autocomplete="one-time-code" autofocus/>
        <div style="margin-top:14px"><button>{_IC['check']} Verify &amp; enroll</button></div>
      </form>
    </div>"""
    return _admin_shell(inner, chrome=False)


def _login_html(error: str = "") -> str:
    err = f'<div class="flash err">{html.escape(error)}</div>' if error else ""
    inner = f"""
    <div class=card>
      <div class=brand style="font-size:19px;margin-bottom:8px">EUC<b>STATS</b></div>
      <h1 style="margin:0 0 4px">Admin sign in</h1>
      <p class=hint>Enter the 6-digit code from your authenticator app.</p>
      {err}
      <form method="post" action="/admin/verify-totp">
        <input class=codein name="code" placeholder="000000" inputmode="numeric" autocomplete="one-time-code" autofocus/>
        <div style="margin-top:14px"><button>Log in</button></div>
      </form>
    </div>"""
    return _admin_shell(inner, chrome=False)


def _dash_html(db: Session) -> str:
    c = _counts(db)
    cur_test = settings.is_test_mode()
    kpis = "".join(f'<div class=box><div class=n>{c[k]}</div><div class=l>{k}</div></div>'
                   for k in ("riders", "trips", "validated", "flagged"))

    flagged = db.query(Trip).filter(Trip.validation_status == "flagged").order_by(desc(Trip.created_at)).limit(50).all()
    fhtml = "".join(
        f"<tr><td><code>{t.trip_uuid[:8]}</code></td><td>{html.escape(t.rider_store_id or '')}</td>"
        f"<td>{round(t.distance_km or 0,1)} km</td>"
        f"<td>{html.escape(', '.join(t.flag_reasons or []))}</td><td>"
        f"<form method=post action=/admin/trip/{t.trip_uuid}/approve style='display:inline-flex;margin-right:6px'><button class=mini>{_IC['check']} approve</button></form>"
        f"<form method=post action=/admin/trip/{t.trip_uuid}/reject style='display:inline-flex'><button class='mini danger'>{_IC['x']} reject</button></form>"
        f"</td></tr>" for t in flagged) or "<tr><td colspan=5 class=mut>nothing flagged — queue is clear</td></tr>"

    riders = db.query(Rider).order_by(desc(Rider.created_at)).limit(200).all()
    rhtml = "".join(
        f"<tr class=clk onclick=\"location='/admin/explorer/rider/{html.escape(r.store_id)}'\">"
        f"<td><code>{html.escape(r.store_id)}</code></td><td>{html.escape(r.display_name or '')}</td>"
        f"<td>{html.escape(r.flag or '')}</td><td>{html.escape(r.platform or '')}</td>"
        f"<td>{_rider_badges(db, r)}</td></tr>"
        for r in riders) or "<tr><td colspan=5 class=mut>no riders</td></tr>"

    trips = db.query(Trip).order_by(desc(Trip.created_at)).limit(30).all()
    thtml = "".join(
        f"<tr class=clk onclick=\"location='/admin/explorer/trip/{html.escape(t.trip_uuid)}'\">"
        f"<td><code>{t.trip_uuid[:8]}</code></td><td>{html.escape(t.rider_store_id or '')}</td>"
        f"<td>{round(t.distance_km or 0,1)}</td><td>{html.escape(t.country or '')}</td>"
        f"<td><span class='badge {t.validation_status or 'pending'}'>{html.escape(t.validation_status or 'pending')}</span></td></tr>"
        for t in trips) or "<tr><td colspan=5 class=mut>no trips</td></tr>"

    inner = f"""
    <h1>Overview</h1>
    <p class=sub>Site mode: {_badge(cur_test)} &nbsp;{'— the TEST DATA banner is showing publicly' if cur_test else '— serving as live data'} &nbsp;·&nbsp; <a href="/admin/settings">change in Settings</a></p>
    <div class=card><div class=kpi>{kpis}</div></div>
    <div class=card>
      <h2>Quick actions</h2>
      <div class=qa>
        <a class=btn href="/admin/explorer">{_IC['search']} Explore riders &amp; trips</a>
        <a class=btn href="/admin/datasets">{_IC['db']} Datasets &amp; backups</a>
        <a class=btn href="/admin/pipeline">{_IC['pulse']} Ingest pipeline</a>
        <a class=btn href="/admin/metrics">{_IC['sliders']} Metrics &amp; sections</a>
        <a class=btn href="/admin/system">{_IC['pulse']} System &amp; retention</a>
      </div>
    </div>
    <div class=card>
      <h2>Flagged trips — review queue</h2>
      <p class=hint>Trips held back by plausibility checks. Approve to count them toward leaderboards, or reject to drop them.</p>
      <table><tr><th>id</th><th>rider</th><th>distance</th><th>reasons</th><th>action</th></tr>{fhtml}</table>
    </div>
    <div class=card>
      <h2>Riders <span class=mut>· newest 200</span></h2>
      <table><tr><th>store id</th><th>name</th><th>flag</th><th>platform</th><th>status</th></tr>{rhtml}</table>
    </div>
    <div class=card>
      <h2>Recent trips <span class=mut>· newest 30</span></h2>
      <table><tr><th>id</th><th>rider</th><th>km</th><th>country</th><th>status</th></tr>{thtml}</table>
    </div>"""
    return _admin_shell(inner, active="/admin")


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
    if t and t.validation_status in ("flagged", "validated"):
        was_counted = t.validation_status == "validated"
        t.validation_status = "rejected"
        t.flag_reasons = None
        db.commit()
        if was_counted:   # it had already been aggregated -> recompute to remove its stats
            from services.aggregator import rebuild_all
            rebuild_all(db)
    return RedirectResponse("/admin", status_code=303)


# --- data explorer (riders & trips) -------------------------------------
# Read-only views over data we already store (plus ban controls). No new data
# is collected — this just surfaces what's in the active dataset for moderation.

def _fmt_dt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "—"


def _num(v, dec=1):
    return "—" if v is None else (round(v, dec) if dec else int(v))


def _field(k: str, v, hi: bool = False) -> str:
    return f'<div class="f{" hi" if hi else ""}"><div class=k>{html.escape(k)}</div><div class=v>{v}</div></div>'


def _rider_badges(db: Session, r: Rider) -> str:
    parts = ['<span class="badge rejected">deleted</span>' if r.deleted_at
             else '<span class="badge validated">active</span>']
    if settings.is_banned(db, r.store_id):
        parts.append('<span class="badge rejected">banned</span>')
    return " ".join(parts)


def _rider_row(db: Session, r: Rider, rs) -> str:
    km = _num(rs.total_km if rs else 0, 1)
    n = int((rs.trip_count if rs else 0) or 0)
    return f"""<tr class=clk onclick="location='/admin/explorer/rider/{html.escape(r.store_id)}'">
      <td><code>{html.escape((r.store_id or '')[:12])}…</code></td>
      <td>{html.escape(r.display_name or '')}</td><td>{html.escape(r.flag or '')}</td>
      <td>{n}</td><td>{km} km</td><td>{_rider_badges(db, r)}</td></tr>"""


def _trip_row(t: Trip) -> str:
    fr = t.max_freespin
    frs = f' <span class=chip flagged title="freespin">⟳{fr}</span>' if fr else ""
    return f"""<tr class=clk onclick="location='/admin/explorer/trip/{html.escape(t.trip_uuid)}'">
      <td><code>{html.escape(t.trip_uuid[:8])}</code></td><td>{_fmt_dt(t.start_utc)}</td>
      <td>{_num(t.distance_km)}</td><td>{_num(t.max_speed)}{frs}</td>
      <td>{html.escape(t.country or '')}</td>
      <td><span class="badge {t.validation_status or 'pending'}">{html.escape(t.validation_status or 'pending')}</span></td></tr>"""


def _explorer_html(db: Session, q: str = "") -> str:
    q = (q or "").strip()
    query = db.query(Rider, RiderStat).outerjoin(RiderStat, RiderStat.store_id == Rider.store_id)
    if q:
        like = f"%{q}%"
        query = query.filter(Rider.store_id.ilike(like) | Rider.display_name.ilike(like))
    rows = query.order_by(desc(Rider.created_at)).limit(100).all()
    body = "".join(_rider_row(db, r, rs) for r, rs in rows) or \
        f"<tr><td colspan=6 class=mut>no riders{' match' if q else ''}</td></tr>"
    bn = settings.banned(db)
    banned_note = (f'<p class=hint>{len(bn)} rider(s) currently banned — excluded from public stats.</p>'
                   if bn else "")
    inner = f"""
    <h1>Explorer</h1>
    <p class=sub>Search and inspect riders and trips. Click any row for full detail.</p>
    <div class=card>
      <h2>Find a rider</h2>
      <form class=searchbar method=get action="/admin/explorer">
        <input type=text name=q value="{html.escape(q)}" placeholder="store id or display name…" autofocus>
        <button>{_IC['search']} Search</button>
        <a class=btn href="/admin/explorer/trips">{_IC['pulse']} Trip explorer →</a>
      </form>
      {banned_note}
      <table><tr><th>store id</th><th>name</th><th>flag</th><th>trips</th><th>distance</th><th>status</th></tr>{body}</table>
      <p class=hint>Showing up to 100{' matching' if q else ' newest'} riders.</p>
    </div>"""
    return _admin_shell(inner, active="/admin/explorer")


def _rider_detail_html(db: Session, store_id: str, msg: str = "") -> str | None:
    r = db.get(Rider, store_id)
    if r is None:
        return None
    rs = db.get(RiderStat, store_id)
    banned, reason = settings.is_banned(db, store_id), settings.ban_reason(db, store_id)
    wheels = db.query(Wheel).filter(Wheel.rider_store_id == store_id).order_by(desc(Wheel.last_seen)).all()
    trips = db.query(Trip).filter(Trip.rider_store_id == store_id).order_by(desc(Trip.start_utc)).limit(200).all()
    tcount = db.query(func.count(Trip.trip_uuid)).filter(Trip.rider_store_id == store_id).scalar()

    if banned:
        ban_card = f"""<div class=card style="border-color:rgba(255,107,107,.4)">
          <h2 style="color:#ff9d9d">Account suspended</h2>
          <p class=hint>Reason shown to the rider in-app: <b>{html.escape(reason or '')}</b></p>
          <form method=post action="/admin/rider/{html.escape(store_id)}/unban">
            <button class=go>{_IC['check']} Lift the ban</button></form></div>"""
    else:
        ban_card = f"""<div class=card>
          <h2>Moderation</h2>
          <p class=hint>Banning refuses new uploads (403) and removes the rider from all public stats. Reversible.</p>
          <form class=banbar method=post action="/admin/rider/{html.escape(store_id)}/ban">
            <input type=text name=reason placeholder="reason (shown to the rider in-app)…">
            <button class=danger>{_IC['ban']} Ban rider</button></form></div>"""

    prof = "".join([
        _field("platform", html.escape(r.platform or "—")),
        _field("created", _fmt_dt(r.created_at)),
        _field("public consent", "yes" if r.consent_public else "no"),
        _field("last name change", _fmt_dt(r.last_name_change)),
        _field("last flag change", _fmt_dt(r.last_flag_change)),
        _field("deleted", _fmt_dt(r.deleted_at) if r.deleted_at else "no"),
    ])
    if rs:
        statf = "".join([
            _field("total distance", f"{_num(rs.total_km)} km"),
            _field("validated trips", int(rs.trip_count or 0)),
            _field("best speed", f"{_num(rs.best_speed)} km/h"),
            _field("longest trip", f"{_num(rs.longest_trip_km)} km"),
            _field("total ascent", f"{_num(rs.total_ascent_m, 0)} m"),
            _field("hours", _num((rs.total_duration_s or 0) / 3600.0)),
            _field("current streak", f"{int(rs.current_streak or 0)} d"),
            _field("longest streak", f"{int(rs.longest_streak or 0)} d"),
            _field("best range", f"{_num(rs.best_range_km)} km"),
            _field("best Wh/km", _num(rs.best_wh_per_km)),
            _field("peak voltage", f"{_num(rs.peak_voltage)} V"),
            _field("last ride", rs.last_ride_date.isoformat() if rs.last_ride_date else "—"),
        ])
        stat_card = f'<div class=card><h2>Stats <span class=mut>· materialized, validated only</span></h2><div class=dl>{statf}</div></div>'
    else:
        stat_card = '<div class=card><h2>Stats</h2><p class=mut>No validated trips yet.</p></div>'

    whtml = "".join(
        f"<tr><td>{html.escape(w.brand or '')}</td><td>{html.escape(w.model or '')}</td>"
        f"<td>{html.escape(w.ble_name or '')}</td><td>{html.escape(w.firmware or '')}</td>"
        f"<td>{_fmt_dt(w.first_seen)}</td><td>{_fmt_dt(w.last_seen)}</td></tr>"
        for w in wheels) or "<tr><td colspan=6 class=mut>no wheels recorded</td></tr>"
    thtml = "".join(_trip_row(t) for t in trips) or "<tr><td colspan=6 class=mut>no trips</td></tr>"

    flash = f'<div class="flash ok">{html.escape(msg)}</div>' if msg else ""
    av = '<img src="/admin/explorer/rider/%s/avatar" style="width:46px;height:46px;border-radius:50%%;vertical-align:middle;margin-right:10px;border:1px solid #26345e">' % quote(store_id) if r.avatar_png else ""
    inner = f"""
    <a class=bk href="/admin/explorer">{_IC['back']} all riders</a>
    {flash}
    <h1>{av}{html.escape(r.display_name or '(no name)')} {html.escape(r.flag or '')}</h1>
    <p class=sub><code>{html.escape(store_id)}</code> &nbsp; {_rider_badges(db, r)}
       &nbsp;·&nbsp; <a href="/api/riders/{quote(store_id)}/card" target=_blank>card JSON →</a></p>
    {ban_card}
    {stat_card}
    <div class=card><h2>Profile</h2><div class=dl>{prof}</div></div>
    <div class=card><h2>Wheels <span class=mut>· {len(wheels)}</span></h2>
      <table><tr><th>brand</th><th>model</th><th>BLE name</th><th>firmware</th><th>first seen</th><th>last seen</th></tr>{whtml}</table></div>
    <div class=card><h2>Trips <span class=mut>· {tcount} total, newest {len(trips)}</span></h2>
      <table><tr><th>id</th><th>start (UTC)</th><th>km</th><th>max km/h</th><th>country</th><th>status</th></tr>{thtml}</table></div>"""
    return _admin_shell(inner, active="/admin/explorer")


def _trips_html(db: Session, status: str = "", country: str = "", store: str = "", q: str = "") -> str:
    query = db.query(Trip)
    if status:
        query = query.filter(Trip.validation_status == status)
    if country:
        query = query.filter(Trip.country == country.strip().upper())
    if store:
        query = query.filter(Trip.rider_store_id == store.strip())
    if q:
        query = query.filter(Trip.trip_uuid.ilike(f"{q.strip()}%"))
    trips = query.order_by(desc(Trip.start_utc)).limit(200).all()
    opts = "".join(f'<option value="{s}"{" selected" if status == s else ""}>{s or "any status"}</option>'
                   for s in ("", "validated", "flagged", "rejected"))
    body = "".join(_trip_row(t) for t in trips) or "<tr><td colspan=6 class=mut>no trips match</td></tr>"
    inner = f"""
    <a class=bk href="/admin/explorer">{_IC['back']} explorer</a>
    <h1>Trip explorer</h1>
    <p class=sub>Filter and inspect individual trip submissions.</p>
    <div class=card>
      <form class=searchbar method=get action="/admin/explorer/trips">
        <select name=status>{opts}</select>
        <input type=text name=country value="{html.escape(country)}" placeholder="country (e.g. NO)">
        <input type=text name=store value="{html.escape(store)}" placeholder="rider store id">
        <input type=text name=q value="{html.escape(q)}" placeholder="trip id prefix">
        <button>{_IC['search']} Filter</button>
      </form>
      <table><tr><th>id</th><th>start (UTC)</th><th>km</th><th>max km/h</th><th>country</th><th>status</th></tr>{body}</table>
      <p class=hint>Showing up to 200 trips, newest first. ⟳ marks a freespin spike.</p>
    </div>"""
    return _admin_shell(inner, active="/admin/explorer")


_TRIP_MAP_TMPL = """
    <link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css"/>
    <script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
    <div class=card><h2>Route <span class=mut>· admin view · exact GPS path</span></h2>
      <div id=tmap style="height:360px;border-radius:10px;overflow:hidden;border:1px solid #26345e"></div>
      <p class=hint>Green = start, red = end. Full downsampled GPS path (admin only — public maps obfuscate rider locations).</p></div>
    <script>
    (function(){
      if(!window.maplibregl){return;}
      var m=new maplibregl.Map({container:"tmap",style:"https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",center:[__LON__,__LAT__],zoom:12,attributionControl:false});
      m.addControl(new maplibregl.NavigationControl({showCompass:false}));
      m.on("load",function(){
        fetch("/admin/explorer/trip/__UUID__/track.geojson").then(function(r){return r.json();}).then(function(g){
          if(!g.features||!g.features.length){return;}
          m.addSource("trk",{type:"geojson",data:g});
          m.addLayer({id:"trk-glow",type:"line",source:"trk",filter:["==",["get","role"],"path"],layout:{"line-cap":"round","line-join":"round"},paint:{"line-color":"#2ea8ff","line-width":9,"line-blur":6,"line-opacity":0.3}});
          m.addLayer({id:"trk-line",type:"line",source:"trk",filter:["==",["get","role"],"path"],layout:{"line-cap":"round","line-join":"round"},paint:{"line-color":"#7fd0ff","line-width":3}});
          m.addLayer({id:"trk-start",type:"circle",source:"trk",filter:["==",["get","role"],"start"],paint:{"circle-radius":6,"circle-color":"#39d98a","circle-stroke-color":"#eaffff","circle-stroke-width":2}});
          m.addLayer({id:"trk-end",type:"circle",source:"trk",filter:["==",["get","role"],"end"],paint:{"circle-radius":6,"circle-color":"#ff8585","circle-stroke-color":"#ffecec","circle-stroke-width":2}});
          var b=new maplibregl.LngLatBounds();
          g.features.forEach(function(f){
            if(f.geometry.type==="LineString"){f.geometry.coordinates.forEach(function(c){b.extend(c);});}
            else if(f.geometry.type==="Point"){b.extend(f.geometry.coordinates);}
          });
          if(!b.isEmpty()){m.fitBounds(b,{padding:40,maxZoom:15,duration:0});}
        }).catch(function(){});
      });
    })();
    </script>"""


def _trip_map_card(trip_uuid: str, lat, lon) -> str:
    if lat is None or lon is None:
        return '<div class=card><h2>Route</h2><p class=mut>No GPS location recorded for this trip.</p></div>'
    return (_TRIP_MAP_TMPL.replace("__LON__", repr(float(lon))).replace("__LAT__", repr(float(lat)))
            .replace("__UUID__", quote(trip_uuid)))


def _trip_detail_html(db: Session, trip_uuid: str) -> str | None:
    t = db.get(Trip, trip_uuid)
    if t is None:
        return None
    raw = db.get(RawUpload, trip_uuid)
    mj = t.meta_json if isinstance(t.meta_json, dict) else {}
    fr = t.max_freespin
    gspike = mj.get("max_gforce_spike")
    fields = "".join([
        _field("rider", f'<a href="/admin/explorer/rider/{quote(t.rider_store_id or "")}">{html.escape(t.rider_store_id or "—")}</a>'),
        _field("start (UTC)", _fmt_dt(t.start_utc)), _field("end (UTC)", _fmt_dt(t.end_utc)),
        _field("duration", f"{_num((t.duration_s or 0) / 60.0)} min"),
        _field("distance", f"{_num(t.distance_km)} km"),
        _field("max speed (realistic)", f"{_num(t.max_speed)} km/h"),
    ] + ([_field("⚠ freespin spike", f"{fr} km/h", hi=True)] if fr else []) + [
        _field("avg speed", f"{_num(t.avg_speed)} km/h"),
        _field("sustained accel", f"{_num(t.sustained_accel, 2)} km/h/s" if t.sustained_accel else "—"),
        _field("g-force (2s sustained)", _num(t.max_gforce, 2)),
    ] + ([_field("⚠ g-force spike", _num(gspike, 2), hi=True)] if gspike else []) + [
        _field("voltage sag", f"{_num(t.max_voltage_sag, 2)} V" if t.max_voltage_sag else "—"),
        _field("sustained W", _num(t.max_sustained_w, 0)),
        _field("sustained A", _num(t.max_sustained_a, 0)),
        _field("peak voltage", f"{_num(t.peak_voltage)} V"),
        _field("0→40 km/h", f"{_num(t.fastest_0_40_s)} s" if t.fastest_0_40_s else "—"),
        _field("ascent", f"{_num(t.ascent_m, 0)} m"),
        _field("alt range", f"{_num(t.alt_range_m, 0)} m"),
        _field("battery used", f"{_num(t.battery_used_pct)} %"),
        _field("est range", f"{_num(t.est_range_km)} km"),
        _field("country", html.escape(t.country or "—")),
        _field("start cell", html.escape(t.start_cell or "—")),
        _field("start coords", f"{_num(t.start_lat, 3)}, {_num(t.start_lon, 3)}" if t.start_lat is not None else "—"),
        _field("samples", t.sample_count if t.sample_count is not None else "—"),
        _field("app version", html.escape(t.app_version or "—")),
        _field("OS", html.escape(t.os_name or "—")),
        _field("device", html.escape(((t.device_brand or "") + " " + (t.device_model or "")).strip() or "—")),
        _field("schema", html.escape(t.schema_version or "—")),
        _field("source app", html.escape(t.source_app or "—")),
        _field("mock location", "⚠ yes" if t.is_mock_location else "no", hi=bool(t.is_mock_location)),
        _field("aggregated", "yes" if t.aggregated else "no"),
        _field("raw upload", _fmt_size(raw.bytes) if raw else "—"),
        _field("created", _fmt_dt(t.created_at)),
    ])
    reasons = ", ".join(t.flag_reasons or []) if t.flag_reasons else ""
    reasons_card = (f'<div class=card style="border-color:rgba(255,206,90,.4)"><h2 style="color:#ffd98a">Flag reasons</h2>'
                    f'<p class=hint>{html.escape(reasons)}</p></div>') if reasons else ""
    actions = ""
    if t.validation_status in ("flagged", "validated"):
        actions += (f'<form method=post action="/admin/trip/{quote(trip_uuid)}/reject" style="display:inline-flex;margin-right:6px">'
                    f'<button class="mini danger">{_IC["x"]} reject</button></form>')
    if t.validation_status == "flagged":
        actions = (f'<form method=post action="/admin/trip/{quote(trip_uuid)}/approve" style="display:inline-flex;margin-right:6px">'
                   f'<button class=mini>{_IC["check"]} approve</button></form>') + actions
    actions_card = f'<div class=card><h2>Actions</h2>{actions}</div>' if actions else ""
    try:
        mj_pretty = html.escape(json.dumps(mj, indent=2, ensure_ascii=False)) if mj else "{}"
    except Exception:
        mj_pretty = "{}"
    route_card = _trip_map_card(trip_uuid, t.start_lat, t.start_lon)
    inner = f"""
    <a class=bk href="/admin/explorer/trips">{_IC['back']} trip explorer</a>
    <h1>Trip <code style="font-size:16px">{html.escape(trip_uuid[:8])}</code>
      <span class="badge {t.validation_status or 'pending'}">{html.escape(t.validation_status or 'pending')}</span></h1>
    <p class=sub><code>{html.escape(trip_uuid)}</code></p>
    {reasons_card}
    {actions_card}
    {route_card}
    <div class=card><h2>Metrics</h2><div class=dl>{fields}</div></div>
    <div class=card><h2>meta_json <span class=mut>· device / gps extras</span></h2><pre class=j>{mj_pretty}</pre></div>"""
    return _admin_shell(inner, active="/admin/explorer")


@admin_router.get("/explorer", response_class=HTMLResponse)
def explorer_page(request: Request, db: Session = Depends(get_db), q: str = ""):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    return HTMLResponse(_explorer_html(db, q))


@admin_router.get("/explorer/trips", response_class=HTMLResponse)
def explorer_trips(request: Request, db: Session = Depends(get_db),
                   status: str = "", country: str = "", store: str = "", q: str = ""):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    return HTMLResponse(_trips_html(db, status, country, store, q))


@admin_router.get("/explorer/rider/{store_id}", response_class=HTMLResponse)
def explorer_rider(store_id: str, request: Request, db: Session = Depends(get_db), msg: str = ""):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    page = _rider_detail_html(db, store_id, msg)
    if page is None:
        return HTMLResponse(_admin_shell('<a class=bk href="/admin/explorer">← back</a><div class=card><h1>Rider not found</h1></div>',
                                         active="/admin/explorer"), status_code=404)
    return HTMLResponse(page)


@admin_router.get("/explorer/rider/{store_id}/avatar")
def explorer_avatar(store_id: str, request: Request, db: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    r = db.get(Rider, store_id)
    if not r or not r.avatar_png:
        return JSONResponse({"error": "no avatar"}, status_code=404)
    return HTMLResponse(content=r.avatar_png, media_type="image/png")


@admin_router.get("/explorer/trip/{trip_uuid}", response_class=HTMLResponse)
def explorer_trip(trip_uuid: str, request: Request, db: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    page = _trip_detail_html(db, trip_uuid)
    if page is None:
        return HTMLResponse(_admin_shell('<a class=bk href="/admin/explorer/trips">← back</a><div class=card><h1>Trip not found</h1></div>',
                                         active="/admin/explorer"), status_code=404)
    return HTMLResponse(page)


@admin_router.get("/explorer/trip/{trip_uuid}/track.geojson")
def explorer_trip_track(trip_uuid: str, request: Request, db: Session = Depends(get_db)):
    """The trip's downsampled GPS path as GeoJSON (admin only — exact coords)."""
    if not _is_authenticated(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from ingest.downsample import decode_track
    from models import TripTrack
    t = db.get(Trip, trip_uuid)
    coords = []
    tr = db.get(TripTrack, trip_uuid)
    if tr and tr.points:
        try:
            for row in decode_track(tr.points):     # [iso_t, lat, lon, speed, g]
                lat, lon = row[1], row[2]
                if lat is not None and lon is not None:
                    coords.append([lon, lat])
        except Exception:
            coords = []
    feats = []
    if len(coords) >= 2:
        feats.append({"type": "Feature", "properties": {"role": "path"},
                      "geometry": {"type": "LineString", "coordinates": coords}})
        feats.append({"type": "Feature", "properties": {"role": "start"},
                      "geometry": {"type": "Point", "coordinates": coords[0]}})
        feats.append({"type": "Feature", "properties": {"role": "end"},
                      "geometry": {"type": "Point", "coordinates": coords[-1]}})
    elif t and t.start_lat is not None and t.start_lon is not None:
        feats.append({"type": "Feature", "properties": {"role": "start"},
                      "geometry": {"type": "Point", "coordinates": [t.start_lon, t.start_lat]}})
    return JSONResponse({"type": "FeatureCollection", "features": feats})


@admin_router.post("/rider/{store_id}/ban")
def ban_rider(store_id: str, request: Request, db: Session = Depends(get_db), reason: str = Form("")):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    from services.aggregator import rebuild_all
    settings.ban(db, store_id, reason)
    rebuild_all(db)   # drop the banned rider from all materialized public stats
    return RedirectResponse("/admin/explorer/rider/" + quote(store_id) + "?msg=" +
                            quote("rider banned — removed from public stats"), status_code=303)


@admin_router.post("/rider/{store_id}/unban")
def unban_rider(store_id: str, request: Request, db: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    from services.aggregator import rebuild_all
    settings.unban(db, store_id)
    rebuild_all(db)   # restore their trips to public stats
    return RedirectResponse("/admin/explorer/rider/" + quote(store_id) + "?msg=" +
                            quote("ban lifted — rider restored to public stats"), status_code=303)


# --- dataset & snapshot manager ---

def _ds_page(inner: str, active: str = "") -> str:
    return _admin_shell(inner, active=active)


def _fmt_size(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _badge(is_test: bool) -> str:
    return '<span class="b test">TEST</span>' if is_test else '<span class="b live">LIVE</span>'


def _reload_app() -> None:
    """Drop pooled DB connections so the next request opens the freshly-swapped
    active file. Avoids a service restart entirely (instant, no downtime)."""
    from database import engine
    engine.dispose()


def _datasets_html(db: Session, msg: str = "", err: str = "") -> str:
    c = _counts(db)
    listing = datasets.list_datasets()
    active = listing["active"]
    banner = ""
    if msg:
        banner += f'<div class="flash ok">{html.escape(msg)}</div>'
    if err:
        banner += f'<div class="flash err">{html.escape(err)}</div>'

    rows = ""
    for d in listing["datasets"]:
        nm = html.escape(d["name"])
        is_active = d["slug"] == active
        riders = d["riders"] if d["riders"] is not None else "?"
        trips = d["trips"] if d["trips"] is not None else "?"
        rows += (
            f'<tr class="{"active" if is_active else ""}">'
            f'<td>{nm}{" • active" if is_active else ""}</td>'
            f'<td>{riders}</td><td>{trips}</td><td>{_fmt_size(d["size"])}</td>'
            f'<td>{d.get("created","")}</td><td>{html.escape(d.get("origin",""))}</td>'
            f'<td class=acts>'
            f'<form method=get action="/admin/datasets/export/{d["slug"]}"><button class=ghost>download</button></form>'
            f'<form method=post action="/admin/datasets/switch"><input type=hidden name=slug value="{d["slug"]}">'
            f'<input name=confirm placeholder="type name"><button>switch</button></form>'
            f'<form method=post action="/admin/datasets/rename"><input type=hidden name=slug value="{d["slug"]}">'
            f'<input name=new_name placeholder="new name"><button>rename</button></form>'
            f'<form method=post action="/admin/datasets/delete"><input type=hidden name=slug value="{d["slug"]}">'
            f'<input name=confirm placeholder="type name"><button class=danger>delete</button></form>'
            f'</td></tr>')
    if not rows:
        rows = '<tr><td colspan=7 class=mut>no saved datasets yet</td></tr>'

    inner = f"""
    {banner}
    <h1>Datasets &amp; backups</h1>
    <p class=sub>Save, swap, import and back up the whole database as portable snapshots.
    Test mode is a <a href="/admin/settings">site setting</a> now — datasets are just data.</p>
    <div class=card>
      <h2>Create / import</h2>
      <form method=post action="/admin/datasets/new" class=inline
            onsubmit="return confirm('Create a fresh empty dataset and switch the site to it now? The current dataset is backed up first (unless it is empty).')">
        <input name=name placeholder="new dataset name" required>
        <button class=go>Create empty &amp; activate</button>
      </form>
      <p class=hint style="margin:6px 0 10px">Creates a brand-new empty dataset and switches the site to it immediately.</p>
      <form method=post action="/admin/datasets/import" enctype="multipart/form-data" class=inline>
        <input type=file name=file accept=".sqlite,.db" required><input name=name placeholder="name for import">
        <button>Import .sqlite</button>
      </form>
    </div>
    <h2>Saved datasets</h2>
    <table><tr><th>name</th><th>riders</th><th>trips</th><th>size</th><th>created (UTC)</th><th>origin</th><th>actions</th></tr>{rows}</table>
    <p class=mut>Switching or deleting requires typing the dataset's exact name. A switch auto-backs-up the
    current dataset (unless it's empty), then reconnects instantly — no restart, no downtime.</p>
    <div class=card>
      <h2>Active dataset</h2>
      <p>{c['riders']} riders · {c['trips']} trips · {c['validated']} validated · {c['flagged']} flagged</p>
      <form method=post action="/admin/datasets/save" class=inline>
        <input name=name placeholder="snapshot name" required><input name=note placeholder="note (optional)">
        <button>Save current as snapshot</button>
      </form>
    </div>
    """
    return _ds_page(inner, "/admin/datasets")


def _redir(msg: str = "", err: str = ""):
    q = ("?msg=" + quote(msg)) if msg else ("?err=" + quote(err)) if err else ""
    return RedirectResponse("/admin/datasets" + q, status_code=303)


@admin_router.get("/datasets", response_class=HTMLResponse)
def datasets_page(request: Request, db: Session = Depends(get_db), msg: str = "", err: str = ""):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    return HTMLResponse(_datasets_html(db, msg, err))


@admin_router.post("/datasets/save")
def datasets_save(request: Request, name: str = Form(...), note: str = Form("")):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    try:
        datasets.save_current(name, note=note)
        return _redir(msg=f"saved snapshot “{name}”")
    except datasets.DatasetError as e:
        return _redir(err=str(e))


@admin_router.post("/datasets/new")
def datasets_new(request: Request, name: str = Form(...)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    try:
        slug = datasets.create_empty(name)
        datasets.switch_to(slug, reload_app=_reload_app)        # create AND activate
        return _redir(msg=f"created and switched to empty dataset “{name}”")
    except datasets.DatasetError as e:
        return _redir(err=str(e))


@admin_router.post("/datasets/import")
async def datasets_import(request: Request, file: UploadFile = File(...), name: str = Form("")):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite")
    try:
        tmp.write(await file.read())
        tmp.close()
        datasets.import_file(tmp.name, name or file.filename or "imported")
        return _redir(msg="imported dataset")
    except datasets.DatasetError as e:
        return _redir(err=str(e))
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


@admin_router.post("/datasets/rename")
def datasets_rename(request: Request, slug: str = Form(...), new_name: str = Form(...)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    try:
        datasets.rename(slug, new_name)
        return _redir(msg="renamed")
    except datasets.DatasetError as e:
        return _redir(err=str(e))


@admin_router.post("/datasets/delete")
def datasets_delete(request: Request, slug: str = Form(...), confirm: str = Form("")):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    entry = datasets._get_entry(slug)
    if not entry:
        return _redir(err="unknown dataset")
    if (confirm or "").strip() != entry["name"]:
        return _redir(err="confirmation name did not match — nothing deleted")
    datasets.delete(slug)
    return _redir(msg=f"deleted “{entry['name']}”")


@admin_router.post("/datasets/switch")
def datasets_switch(request: Request, slug: str = Form(...), confirm: str = Form("")):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    entry = datasets._get_entry(slug)
    if not entry:
        return _redir(err="unknown dataset")
    if (confirm or "").strip() != entry["name"]:
        return _redir(err="confirmation name did not match — no switch")
    try:
        datasets.switch_to(slug, reload_app=_reload_app)
    except datasets.DatasetError as e:
        return _redir(err=str(e))
    return _redir(msg=f"now serving “{entry['name']}” (a safety backup of the previous dataset was saved)")


@admin_router.get("/datasets/export/{slug}")
def datasets_export(slug: str, request: Request):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    try:
        p = datasets.export_path(slug)
    except datasets.DatasetError:
        return _redir(err="unknown dataset")
    return FileResponse(str(p), filename=f"{slug}.sqlite", media_type="application/octet-stream")


# --- ingest / pipeline monitor (read-only) ---

def _pipeline_html(db: Session, msg: str = "") -> str:
    total = db.query(func.count(Trip.trip_uuid)).scalar() or 0
    allow = settings.ingest_allow(db)
    by_status = dict(db.query(Trip.validation_status, func.count(Trip.trip_uuid))
                     .group_by(Trip.validation_status).all())
    chips = "".join(
        f'<span class="chip {html.escape(str(s or "pending"))}">{html.escape(str(s or "pending"))}: {n}</span>'
        for s, n in sorted(by_status.items(), key=lambda kv: -kv[1])) or '<span class=mut>no trips yet</span>'

    since = datetime.utcnow() - timedelta(days=13)
    daily = dict(db.query(func.date(Trip.created_at), func.count(Trip.trip_uuid))
                 .filter(Trip.created_at >= since).group_by(func.date(Trip.created_at)).all())
    today = datetime.utcnow().date()
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    counts = [int(daily.get(d.isoformat(), 0)) for d in days]
    peak = max(counts + [1])
    bars = "".join(
        f'<div class=bar><span class=d>{d.isoformat()[5:]}</span>'
        f'<div class=track><div class=fill style="width:{int(100 * c / peak)}%"></div></div>'
        f'<span class=n>{c}</span></div>'
        for d, c in zip(days, counts))

    recent = db.query(Trip).order_by(desc(Trip.created_at)).limit(60).all()
    rrows = "".join(
        f'<tr><td>{(t.created_at or "").__str__()[:16]}</td><td>{html.escape(t.rider_store_id or "")}</td>'
        f'<td>{round(t.distance_km or 0, 1)}</td><td>{html.escape(t.country or "")}</td>'
        f'<td><span class="chip {html.escape(t.validation_status or "pending")}">{html.escape(t.validation_status or "pending")}</span></td>'
        f'<td>{html.escape(", ".join(t.flag_reasons or []))}</td></tr>'
        for t in recent) or '<tr><td colspan=6 class=mut>no trips yet</td></tr>'

    offenders = (db.query(Trip.rider_store_id, func.count(Trip.trip_uuid).label("n"))
                 .filter(Trip.validation_status.in_(["flagged", "rejected"]))
                 .group_by(Trip.rider_store_id)
                 .order_by(func.count(Trip.trip_uuid).desc()).limit(15).all())
    ohtml = "".join(
        f'<tr><td><code>{html.escape(sid or "")}</code></td>'
        f'<td>{html.escape((db.get(Rider, sid).display_name if db.get(Rider, sid) else "") or "")}</td>'
        f'<td>{n}</td></tr>'
        for sid, n in offenders) or '<tr><td colspan=3 class=mut>none</td></tr>'

    # --- anti-fraud rule toggles + tunable thresholds ---
    disabled = settings.pipeline_disabled(db)
    rule_rows = "".join(
        f'<label class="krow{"" if k not in disabled else " off"}">'
        f'<input type=checkbox name=rule value="{k}"{" checked" if k not in disabled else ""}>'
        f'<span class=tw><span class=kl>{html.escape(lbl)}</span>'
        f'<span class=kd>{html.escape(desc)}</span></span></label>'
        for k, lbl, desc in settings.PIPELINE_RULES)
    thr = settings.get_thresholds(db)
    thr_inputs = "".join(
        f'<label style="display:inline-flex;flex-direction:column;gap:3px;margin:0 14px 10px 0;font-size:12px;color:#8ea0c8">'
        f'{html.escape(lbl)}<input type=number name="thr_{key}" value="{thr[key]}" '
        f'step="{"1" if kind == "int" else "any"}" min="{lo}" max="{hi}" style="width:140px"></label>'
        for key, lbl, _mk, _ca, kind, lo, hi in settings.PIPELINE_THRESHOLDS)
    rules_card = f"""
    <div class=card>
      <h2>Anti-fraud rules <span class=mut>· what we check on every upload</span></h2>
      <p class=hint>Ticked = the rule is active and will flag a matching trip for review. Untick to disable a
      check entirely. Applies to new uploads (use “Rebuild stats” after to re-evaluate nothing — rules run at ingest).</p>
      <form method=post action="/admin/pipeline/rules">
        <div class=kids style="padding:0">{rule_rows}</div>
        <h2 style="margin-top:16px">Thresholds</h2>
        <p class=hint>Tune the limits each rule uses. Blank-safe; values clamp to sane ranges.</p>
        <div style="display:flex;flex-wrap:wrap">{thr_inputs}</div>
        <button>{_IC['check']} Save rules &amp; thresholds</button>
      </form>
    </div>"""

    banner = f'<div class="flash ok">{html.escape(msg)}</div>' if msg else ""
    inner = f"""
    {banner}
    <h1>Ingest pipeline</h1>
    <p class=sub>How uploads are flowing in and how validation is treating them.</p>
    {rules_card}
    <div class=card>
      <h2>Status — {total} trips total</h2>
      <p>{chips}</p>
      <p class=mut>Attestation: <b>{html.escape(config.ATTESTATION_MODE)}</b>
      ({'accepting all uploads, no verification' if config.ATTESTATION_MODE == 'stub' else 'requires an attestation token — presence only, not yet cryptographically verified'})
      · package <b>{html.escape(config.ANDROID_PACKAGE)}</b></p>
      <form method=post action="/admin/allowlist" style="margin-top:12px">
        <label class=toggle style="display:inline-flex"><input type=checkbox name=enabled value=1{' checked' if allow['enabled'] else ''}> Restrict uploads to an allowlist</label>
        <div style="margin-top:8px"><input name=ids value="{html.escape(', '.join(allow['ids']))}" placeholder="store_id, store_id, …" style="width:min(520px,100%)"> <button class="ghost mini">Save allowlist</button></div>
        <p class=hint style="margin-top:6px">Off = any registered rider can upload (use during the open test period). On = only the listed store_ids (others get 403).</p>
      </form>
      <form method=post action="/admin/rebuild" style="margin-top:6px"
            onsubmit="return confirm('Recompute all leaderboards &amp; records from validated trips?')">
        <button class="ghost mini">↻ Rebuild stats</button></form>
    </div>
    <div class=card>
      <h2>Ingest — last 14 days</h2>
      {bars}
    </div>
    <div class=card>
      <h2>Repeat offenders <span class=mut>· riders by flagged/rejected trips</span></h2>
      <table><tr><th>store id</th><th>name</th><th>flagged/rejected</th></tr>{ohtml}</table>
    </div>
    <h2>Recent uploads (newest 60)</h2>
    <table><tr><th>created (UTC)</th><th>rider</th><th>km</th><th>country</th><th>status</th><th>flag/reject reasons</th></tr>{rrows}</table>
    """
    return _ds_page(inner, "/admin/pipeline")


@admin_router.get("/pipeline", response_class=HTMLResponse)
def pipeline_page(request: Request, db: Session = Depends(get_db), msg: str = ""):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    return HTMLResponse(_pipeline_html(db, msg))


@admin_router.post("/rebuild")
def admin_rebuild(request: Request, db: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    from services.aggregator import rebuild_all
    n = rebuild_all(db)
    return RedirectResponse("/admin/pipeline?msg=" + quote(f"rebuilt stats from {n} validated trips"),
                            status_code=303)


@admin_router.post("/pipeline/rules")
async def pipeline_rules_save(request: Request, db: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    form = await request.form()
    settings.set_pipeline_enabled(db, form.getlist("rule"))
    settings.set_thresholds(db, {key: form.get("thr_" + key) for key, *_ in settings.PIPELINE_THRESHOLDS})
    off = len(settings.pipeline_disabled(db))
    note = f"rules saved — {len(settings.PIPELINE_RULES) - off}/{len(settings.PIPELINE_RULES)} active"
    return RedirectResponse("/admin/pipeline?msg=" + quote(note), status_code=303)


@admin_router.post("/allowlist")
def allowlist_save(request: Request, db: Session = Depends(get_db),
                   enabled: str = Form(""), ids: str = Form("")):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    id_list = [s.strip() for s in ids.replace("\n", ",").replace(";", ",").split(",") if s.strip()]
    settings.set_ingest_allow(db, bool(enabled), id_list)
    state = f"on ({len(id_list)} id{'s' if len(id_list) != 1 else ''})" if enabled else "off — open to all"
    return RedirectResponse("/admin/pipeline?msg=" + quote("allowlist " + state), status_code=303)


# --- metric / section show-hide toggles ---

_METRICS_JS = """
    <script>
    (function(){
      function offc(cb){var r=cb.closest('.krow');if(r){r.classList.toggle('off',!cb.checked);}}
      document.querySelectorAll('.krow input').forEach(function(cb){
        cb.addEventListener('change',function(){
          offc(cb);
          document.querySelectorAll('.krow input[data-k="'+cb.dataset.k+'"]').forEach(function(o){
            if(o!==cb){o.checked=cb.checked;offc(o);}     // group tabs are shared across Countries/Wheels/Brands
          });
        });
      });
      document.querySelectorAll('.mhead input[data-parent]').forEach(function(p){
        function upd(){var n=p.closest('.mnode'),k=n&&n.querySelector('.kids');if(k){k.classList.toggle('dim',!p.checked);}}
        p.addEventListener('change',upd);upd();
      });
    })();
    </script>"""


def _metrics_html(db: Session, msg: str = "") -> str:
    h = settings.get_hidden(db)
    secmeta = {k: (lbl, desc) for k, lbl, desc in settings.METRIC_SECTIONS}
    # section_key -> (child form field, items, hidden list)
    kids_of = {
        "riders": ("show_board", settings.METRIC_BOARDS, h["boards"]),
        "countries": ("show_group", settings.METRIC_GROUPS, h["groups"]),
        "wheels": ("show_group", settings.METRIC_GROUPS, h["groups"]),
        "brands": ("show_group", settings.METRIC_GROUPS, h["groups"]),
        "records": ("show_record", settings.METRIC_RECORDS, h["records"]),
        "tech": ("show_app", settings.METRIC_APP, h["app"]),
    }

    def krow(field, k, label, desc, hidden):
        on = k not in hidden
        return (f'<label class="krow{"" if on else " off"}">'
                f'<input type=checkbox name={field} value="{k}" data-k="{field}:{k}"{" checked" if on else ""}>'
                f'<span class=tw><span class=kl>{html.escape(label)}</span>'
                f'<span class=kd>{html.escape(desc)}</span></span></label>')

    def field_is_group(fi):
        return bool(fi) and fi[0] == "show_group"

    def node(sec_key):
        lbl, desc = secmeta[sec_key]
        son = sec_key not in h["sections"]
        fi = kids_of.get(sec_key)
        if fi:
            field, items, hidden = fi
            shown = sum(1 for k, *_ in items if k not in hidden)
            body = "".join(krow(field, k, l, d, hidden) for k, l, d in items)
            cnt = f'<span class=cnt>{shown}/{len(items)} on</span>'
        else:
            body = '<div class=mnone>No sub-metrics — the section toggle controls it.</div>'
            cnt = ""
        shared = ' · shared tabs' if field_is_group(fi) else ''
        return (f'<div class=mnode>'
                f'<label class=mhead><input type=checkbox name=show_section value="{sec_key}" data-parent="{sec_key}"{" checked" if son else ""}>'
                f'<span class=tw><span class=t>{html.escape(lbl)}</span>'
                f'<span class=d>{html.escape(desc)}{shared}</span></span>{cnt}</label>'
                f'<div class=kids>{body}</div></div>')

    tree = "".join(node(k) for k, *_ in settings.METRIC_SECTIONS)
    banner = f'<div class="flash ok">{html.escape(msg)}</div>' if msg else ""
    inner = f"""
    {banner}
    <h1>Metrics &amp; sections</h1>
    <p class=mut>Each dock section is a parent toggle; its metrics are nested underneath. Ticked = shown
    on the public site. Untick a parent to hide the whole section, or individual rows to hide single metrics.
    Countries / Wheels / Brands share the same group tabs (editing one updates all three). Changes are live on save.</p>
    <form method=post action="/admin/metrics/save">
      <div class=mtree2>{tree}</div>
      <button>{_IC['check']} Save visibility</button>
    </form>
    {_METRICS_JS}
    """
    return _ds_page(inner, "/admin/metrics")


@admin_router.get("/metrics", response_class=HTMLResponse)
def metrics_page(request: Request, db: Session = Depends(get_db), msg: str = ""):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    return HTMLResponse(_metrics_html(db, msg))


@admin_router.post("/metrics/save")
def metrics_save(request: Request, db: Session = Depends(get_db),
                 show_section: list[str] = Form([]), show_board: list[str] = Form([]),
                 show_app: list[str] = Form([]), show_group: list[str] = Form([]),
                 show_record: list[str] = Form([])):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    hidden_sections = [k for k, *_ in settings.METRIC_SECTIONS if k not in show_section]
    hidden_boards = [k for k, *_ in settings.METRIC_BOARDS if k not in show_board]
    hidden_app = [k for k, *_ in settings.METRIC_APP if k not in show_app]
    hidden_groups = [k for k, *_ in settings.METRIC_GROUPS if k not in show_group]
    hidden_records = [k for k, *_ in settings.METRIC_RECORDS if k not in show_record]
    settings.set_hidden(db, hidden_boards, hidden_sections, hidden_app, hidden_groups, hidden_records)
    return RedirectResponse("/admin/metrics?msg=" + quote("visibility saved — live now"),
                            status_code=303)


# --- page behaviour settings ---

def _settings_html(db: Session, msg: str = "") -> str:
    b = settings.get_behaviour(db)
    tm = settings.get_test_mode()
    banner = f'<div class="flash ok">{html.escape(msg)}</div>' if msg else ""
    sel = "background:#0b1124;border:1px solid #26345e;color:#e9eefb;padding:8px 10px;border-radius:9px"
    styles = "".join(
        f'<option value="{s}"{" selected" if b["map_style"] == s else ""}>{s}</option>'
        for s in settings.MAP_STYLES)
    intensity_opts = "".join(
        f'<option value="{i}"{" selected" if b["glitch_intensity"] == i else ""}>{lbl}</option>'
        for i, lbl in ((1, "1 — subtle"), (2, "2 — light"), (3, "3 — medium"),
                       (4, "4 — strong"), (5, "5 — heavy")))
    ck = lambda v: " checked" if v else ""
    inner = f"""
    {banner}
    <h1>Page behaviour</h1>
    <p class=sub>How the public site behaves. Changes apply on the next page load.</p>
    <form method=post action="/admin/settings/save">
      <div class=card>
        <h2>Test mode</h2>
        <p class=hint>Site-wide — independent of which dataset is active. When on, the public site shows a banner marking the data as not-yet-real.</p>
        <label class=toggle style="display:inline-flex"><input type=checkbox name=test_enabled value=1{ck(tm['enabled'])}> Show the TEST DATA banner</label>
        <p class=hint style="margin:12px 0 4px">Banner text:</p>
        <input type=text name=test_text value="{html.escape(tm['text'])}" placeholder="TEST DATA" style="width:min(300px,100%)">
      </div>
      <div class=card>
        <h2>Live refresh</h2>
        <p class=hint>How often the top champions + global stats auto-refresh. 0 = off.</p>
        <label>Refresh every <input type=number name=poll_secs min=0 max=3600 value="{b['poll_secs']}" style="width:90px"> seconds</label>
      </div>
      <div class=card>
        <h2>Intro video</h2>
        <label class=toggle style="display:inline-flex"><input type=checkbox name=intro_enabled value=1{ck(b['intro_enabled'])}> Show the cinematic intro</label>
        <p class=hint style="margin:12px 0 4px">Video path or URL:</p>
        <input type=text name=intro_src value="{html.escape(b['intro_src'])}" style="width:min(440px,100%)">
      </div>
      <div class=card>
        <h2>Look &amp; feel</h2>
        <p><label>Default map style <select name=map_style style="{sel}">{styles}</select></label>
        <span class=hint>(a visitor's own choice still wins)</span></p>
        <label class=toggle style="display:inline-flex;margin-top:8px"><input type=checkbox name=glitch_enabled value=1{ck(b['glitch_enabled'])}> RGB glitch effects</label>
        <p class=hint style="margin:12px 0 4px">Glitch tuning (when enabled):</p>
        <label>roughly every <input type=number name=glitch_secs min=1 max=60 value="{b['glitch_secs']}" style="width:72px"> s</label>
        &nbsp;&nbsp;<label>intensity <select name=glitch_intensity style="{sel}">{intensity_opts}</select></label>
      </div>
      <button>{_IC['check']} Save behaviour</button>
    </form>
    """
    return _ds_page(inner, "/admin/settings")


@admin_router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db), msg: str = ""):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    return HTMLResponse(_settings_html(db, msg))


@admin_router.post("/settings/save")
def settings_save(request: Request, db: Session = Depends(get_db),
                  poll_secs: int = Form(30), intro_enabled: str = Form(""),
                  intro_src: str = Form("/static/intro.mp4"), map_style: str = Form("dark"),
                  glitch_enabled: str = Form(""), glitch_secs: int = Form(4),
                  glitch_intensity: int = Form(2),
                  test_enabled: str = Form(""), test_text: str = Form("TEST DATA")):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    settings.set_test_mode(bool(test_enabled), test_text)
    settings.set_behaviour(db, poll_secs, bool(intro_enabled), intro_src, map_style,
                           bool(glitch_enabled), glitch_secs, glitch_intensity)
    return RedirectResponse("/admin/settings?msg=" + quote("settings saved — live on next page load"),
                            status_code=303)


# --- system: server resources + data retention ---

def _gb(n) -> str:
    try:
        return f"{n / 1e9:.1f} GB"
    except (TypeError, ValueError):
        return "?"


def _resbar(label: str, pct, sub: str) -> str:
    p = 0 if pct is None else pct
    col = "#39d98a" if p < 70 else "#ffd24a" if p < 90 else "#ff8585"
    return (f'<div class=bar><span class=d style="width:54px">{html.escape(label)}</span>'
            f'<div class=track><div class=fill style="width:{min(100, p)}%;background:{col}"></div></div>'
            f'<span class=n>{p}%</span></div>'
            f'<p class=hint style="margin:1px 0 12px 0">{html.escape(sub)}</p>')


def _disk_card(disk, app) -> str:
    """Disk bar with our eucstats footprint highlighted inside the used portion."""
    if not disk:
        return '<p class=mut>disk stats unavailable</p>'
    total, used, used_pct = disk["total"], disk["used"], disk["pct"]
    app_b = (app or {}).get("bytes") or 0
    app_pct = round(app_b / total * 100, 2) if total else 0
    bar = (f'<div class=bar><span class=d style="width:54px">Disk</span>'
           f'<div class=track style="position:relative">'
           f'<div class=fill style="width:{min(100, used_pct)}%;background:#3a6ea5"></div>'
           f'<div class=fill style="position:absolute;left:0;top:0;height:100%;width:{min(100, app_pct)}%;background:#ffd24a"></div>'
           f'</div><span class=n>{used_pct}%</span></div>'
           f'<p class=hint style="margin:1px 0 10px 0">'
           f'<span style="color:#ffd24a">●</span> eucstats {_gb(app_b)} &nbsp;·&nbsp; '
           f'<span style="color:#3a6ea5">●</span> other used {_gb(used - app_b)} &nbsp;·&nbsp; '
           f'{_gb(disk["free"])} free of {_gb(total)}</p>')
    # per-folder breakdown of our footprint
    bd = (app or {}).get("breakdown") or []
    if bd and app_b:
        top = bd[:8]
        rows = "".join(
            f'<div class=bar><span class=d style="width:120px;overflow:hidden;text-overflow:ellipsis">{html.escape(name)}</span>'
            f'<div class=track><div class=fill style="width:{int(100 * b / app_b)}%"></div></div>'
            f'<span class=n style="width:64px">{_gb(b)}</span></div>'
            for name, b in top)
        bar += (f'<details style="margin-top:4px"><summary class=hint style="cursor:pointer">'
                f'eucstats breakdown ({html.escape((app or {}).get("path", ""))})</summary>'
                f'<div style="margin-top:8px">{rows}</div></details>')
    return bar


def _resources_html() -> str:
    from services.sysinfo import system_stats
    s = system_stats()
    disk, mem, cpu = s["disk"], s["mem"], s["cpu"]
    res = _disk_card(disk, s.get("app"))
    res += (_resbar("RAM", mem["pct"], f'{_gb(mem["used"])} used of {_gb(mem["total"])} · {_gb(mem["avail"])} available')
            if mem else '<p class=mut>memory stats unavailable (Linux only)</p>')
    if cpu.get("load"):
        load = " / ".join(str(x) for x in cpu["load"]) + " (1·5·15 min)"
        res += _resbar("CPU", cpu.get("pct"), f'{cpu["count"]} core(s) · load {load}')
    else:
        res += _resbar("CPU", None, f'{cpu["count"]} core(s) · load average unavailable on this OS (Linux only)')
    return res


def _system_html(db: Session, msg: str = "") -> str:
    r = settings.get_retention(db)
    banner = f'<div class="flash ok">{html.escape(msg)}</div>' if msg else ""
    inner = f"""
    {banner}
    <h1>System</h1>
    <p class=sub>Live server resources and data-retention controls.</p>
    <div class=card>
      <h2>Server resources <span class=mut id=resage>· live · auto-refreshing</span></h2>
      <div id=resbox>{_resources_html()}</div>
    </div>
    <script>
    (function(){{
      var box=document.getElementById('resbox');
      function tick(){{
        fetch('/admin/system/resources',{{headers:{{'x-frag':'1'}}}})
          .then(function(r){{return r.ok?r.text():Promise.reject();}})
          .then(function(h){{
            var open=box.querySelector('details')&&box.querySelector('details').open;
            box.innerHTML=h;
            var d=box.querySelector('details'); if(d&&open)d.open=true;   // keep breakdown expanded
          }}).catch(function(){{}});
      }}
      setInterval(tick,4000);
    }})();
    </script>
    <div class=card>
      <h2>Data retention</h2>
      <p class=hint>Only the original uploaded files (raw blobs) are ever evicted — trip summaries, GPS tracks,
      leaderboards and the map are kept permanently.</p>
      <form method=post action="/admin/system/save">
        <p><label>Keep raw uploads for
          <input type=number name=ret_days min=0 max=3650 value="{r['days']}" style="width:80px"> days</label></p>
        <p><label>Evict oldest raw when free disk drops below
          <input type=number step=0.1 name=ret_floor_gb min=0 value="{r['disk_floor_gb']}" style="width:90px"> GB</label>
          <span class=hint>(safety valve so the droplet never fills)</span></p>
        <p><label>Run the retention sweep every
          <input type=number name=ret_interval_s min=60 max=86400 value="{r['interval_s']}" style="width:100px"> seconds</label></p>
        <button>{_IC['check']} Save retention</button>
      </form>
    </div>"""
    return _ds_page(inner, "/admin/system")


@admin_router.get("/system", response_class=HTMLResponse)
def system_page(request: Request, db: Session = Depends(get_db), msg: str = ""):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    return HTMLResponse(_system_html(db, msg))


@admin_router.get("/system/resources", response_class=HTMLResponse)
def system_resources(request: Request):
    """Just the resource bars — polled by the System page for live autorefresh."""
    if not _is_authenticated(request):
        return HTMLResponse("", status_code=401)
    return HTMLResponse(_resources_html())


@admin_router.post("/system/save")
def system_save(request: Request, db: Session = Depends(get_db),
                ret_days: int = Form(30), ret_floor_gb: float = Form(10.0),
                ret_interval_s: int = Form(3600)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    settings.set_retention(db, ret_days, ret_floor_gb, ret_interval_s)
    return RedirectResponse("/admin/system?msg=" + quote("retention saved"), status_code=303)
