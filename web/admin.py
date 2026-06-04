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
from models import Rider, Trip
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


_NAV = [("/admin", "Overview"), ("/admin/datasets", "Datasets"),
        ("/admin/pipeline", "Pipeline"), ("/admin/metrics", "Metrics"),
        ("/admin/settings", "Settings")]

_IC = {
    "check": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>',
    "x": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    "db": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>',
    "pulse": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l3 8 4-16 3 8h4"/></svg>',
    "sliders": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7h16M4 17h16" stroke-linecap="round"/><circle cx="9" cy="7" r="2.3"/><circle cx="15" cy="17" r="2.3"/></svg>',
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
.acts{display:flex;flex-direction:column;gap:5px}
.acts form{display:flex;gap:5px;align-items:center;margin:0}
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
    cur_test = settings.is_test_dataset(db)
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
        f"<tr><td><code>{html.escape(r.store_id)}</code></td><td>{html.escape(r.display_name or '')}</td>"
        f"<td>{html.escape(r.flag or '')}</td><td>{html.escape(r.platform or '')}</td>"
        f"<td>{'<span class=\"badge rejected\">deleted</span>' if r.deleted_at else '<span class=\"badge validated\">active</span>'}</td></tr>"
        for r in riders) or "<tr><td colspan=5 class=mut>no riders</td></tr>"

    trips = db.query(Trip).order_by(desc(Trip.created_at)).limit(30).all()
    thtml = "".join(
        f"<tr><td><code>{t.trip_uuid[:8]}</code></td><td>{html.escape(t.rider_store_id or '')}</td>"
        f"<td>{round(t.distance_km or 0,1)}</td><td>{html.escape(t.country or '')}</td>"
        f"<td><span class='badge {t.validation_status or 'pending'}'>{html.escape(t.validation_status or 'pending')}</span></td></tr>"
        for t in trips) or "<tr><td colspan=5 class=mut>no trips</td></tr>"

    inner = f"""
    <h1>Overview</h1>
    <p class=sub>Active dataset: {_badge(cur_test)} &nbsp;{'— seeded test data (TEST DATA banner is showing)' if cur_test else '— serving live data'}</p>
    <div class=card><div class=kpi>{kpis}</div></div>
    <div class=card>
      <h2>Quick actions</h2>
      <div class=qa>
        <a class=btn href="/admin/datasets">{_IC['db']} Datasets &amp; backups</a>
        <a class=btn href="/admin/pipeline">{_IC['pulse']} Ingest pipeline</a>
        <a class=btn href="/admin/metrics">{_IC['sliders']} Metrics &amp; sections</a>
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
    cur_test = settings.is_test_dataset(db)
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
            f'<td>{nm}{" • active" if is_active else ""}</td><td>{_badge(d["is_test"])}</td>'
            f'<td>{riders}</td><td>{trips}</td><td>{_fmt_size(d["size"])}</td>'
            f'<td>{d.get("created","")}</td><td>{html.escape(d.get("origin",""))}</td>'
            f'<td class=acts>'
            f'<a class=btn href="/admin/datasets/export/{d["slug"]}">download</a>'
            f'<form method=post action="/admin/datasets/switch"><input type=hidden name=slug value="{d["slug"]}">'
            f'<input name=confirm placeholder="type name"><button>switch</button></form>'
            f'<form method=post action="/admin/datasets/rename"><input type=hidden name=slug value="{d["slug"]}">'
            f'<input name=new_name placeholder="new name"><button>rename</button></form>'
            f'<form method=post action="/admin/datasets/delete"><input type=hidden name=slug value="{d["slug"]}">'
            f'<input name=confirm placeholder="type name"><button class=danger>delete</button></form>'
            f'</td></tr>')
    if not rows:
        rows = '<tr><td colspan=8 class=mut>no saved datasets yet</td></tr>'

    inner = f"""
    {banner}
    <h1>Datasets &amp; backups</h1>
    <p class=sub>Save, swap, import and back up the whole database as portable snapshots.</p>
    <div class=card>
      <h2>Active dataset {_badge(cur_test)}</h2>
      <p>{c['riders']} riders · {c['trips']} trips · {c['validated']} validated · {c['flagged']} flagged</p>
      <form method=post action="/admin/datasets/save" class=inline>
        <input name=name placeholder="snapshot name" required><input name=note placeholder="note (optional)">
        <button>Save current as snapshot</button>
      </form>
      <form method=post action="/admin/datasets/flag" class=inline>
        <button name=test value="0">Mark current LIVE</button>
        <button name=test value="1">Mark current TEST</button>
      </form>
    </div>
    <div class=card>
      <h2>Create / import</h2>
      <form method=post action="/admin/datasets/new" class=inline>
        <input name=name placeholder="new dataset name" required>
        <label><input type=checkbox name=is_test value=1> test</label>
        <button>Create empty (keep current active)</button>
      </form>
      <form method=post action="/admin/datasets/import" enctype="multipart/form-data" class=inline>
        <input type=file name=file accept=".sqlite,.db" required><input name=name placeholder="name for import">
        <button>Import .sqlite</button>
      </form>
      <form method=post action="/admin/datasets/golive" class=inline
            onsubmit="return confirm('Create a fresh EMPTY live dataset and switch to it now? The current dataset is backed up first, then the site goes live (TEST DATA banner off).')">
        <input name=name value="live" placeholder="live dataset name">
        <button class=go>🚀 Go live (new empty live &amp; switch)</button>
      </form>
    </div>
    <h2>Saved datasets</h2>
    <table><tr><th>name</th><th>type</th><th>riders</th><th>trips</th><th>size</th><th>created (UTC)</th><th>origin</th><th>actions</th></tr>{rows}</table>
    <p class=mut>Switching or deleting requires typing the dataset's exact name. A switch auto-backs-up the
    current dataset, then reconnects instantly — no restart, no downtime.</p>
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
def datasets_new(request: Request, name: str = Form(...), is_test: str = Form("")):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    try:
        datasets.create_empty(name, is_test=bool(is_test))
        return _redir(msg=f"created empty dataset “{name}”")
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


@admin_router.post("/datasets/flag")
def datasets_flag(request: Request, db: Session = Depends(get_db), test: str = Form("0")):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    settings.set_test(db, test == "1")
    return _redir(msg="marked current dataset " + ("TEST" if test == "1" else "LIVE"))


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


@admin_router.post("/datasets/golive")
def datasets_golive(request: Request, name: str = Form("live")):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    try:
        slug = datasets.create_empty(name or "live", is_test=False, note="go-live empty dataset")
        datasets.switch_to(slug, reload_app=_reload_app)
    except datasets.DatasetError as e:
        return _redir(err=str(e))
    return _redir(msg="🚀 now live with a fresh empty dataset — TEST DATA banner is off")


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

    banner = f'<div class="flash ok">{html.escape(msg)}</div>' if msg else ""
    inner = f"""
    {banner}
    <h1>Ingest pipeline</h1>
    <p class=sub>How uploads are flowing in and how validation is treating them.</p>
    <div class=card>
      <h2>Status — {total} trips total</h2>
      <p>{chips}</p>
      <p class=mut>Attestation: <b>{html.escape(config.ATTESTATION_MODE)}</b>
      ({'accepting all uploads, no verification' if config.ATTESTATION_MODE == 'stub' else 'requires an attestation token — presence only, not yet cryptographically verified'})
      · package <b>{html.escape(config.ANDROID_PACKAGE)}</b></p>
      <p class=mut>Ingest allowlist: <b>{', '.join(config.INGEST_ALLOW) if config.INGEST_ALLOW else 'off — all registered riders accepted'}</b></p>
      <form method=post action="/admin/rebuild" style="margin-top:10px"
            onsubmit="return confirm('Recompute all leaderboards &amp; records from validated trips?')">
        <button class="ghost mini">↻ Rebuild stats</button></form>
    </div>
    <div class=card>
      <h2>Ingest — last 14 days</h2>
      {bars}
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


# --- metric / section show-hide toggles ---

def _metrics_html(db: Session, msg: str = "") -> str:
    h = settings.get_hidden(db)

    def toggles(items, field, hidden):
        return "".join(
            f'<label class=toggle><input type=checkbox name={field} value="{k}"'
            f'{" checked" if k not in hidden else ""}> {html.escape(label)}</label>'
            for k, label in items)

    banner = f'<div class="flash ok">{html.escape(msg)}</div>' if msg else ""
    inner = f"""
    {banner}
    <h1>Metrics &amp; sections</h1>
    <p class=mut>Ticked = shown on the public site. Untick to hide. Changes are live immediately.</p>
    <form method=post action="/admin/metrics/save">
      <div class=card><h2>Dock sections</h2><div class=grid2>{toggles(settings.METRIC_SECTIONS, "show_section", h["sections"])}</div></div>
      <div class=card><h2>Rider leaderboards</h2><div class=grid2>{toggles(settings.METRIC_BOARDS, "show_board", h["boards"])}</div></div>
      <div class=card><h2>App &amp; devices panels</h2><div class=grid2>{toggles(settings.METRIC_APP, "show_app", h["app"])}</div></div>
      <button>{_IC['check']} Save visibility</button>
    </form>
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
                 show_app: list[str] = Form([])):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    hidden_sections = [k for k, _ in settings.METRIC_SECTIONS if k not in show_section]
    hidden_boards = [k for k, _ in settings.METRIC_BOARDS if k not in show_board]
    hidden_app = [k for k, _ in settings.METRIC_APP if k not in show_app]
    settings.set_hidden(db, hidden_boards, hidden_sections, hidden_app)
    return RedirectResponse("/admin/metrics?msg=" + quote("visibility saved — live now"),
                            status_code=303)


# --- page behaviour settings ---

def _settings_html(db: Session, msg: str = "") -> str:
    b = settings.get_behaviour(db)
    banner = f'<div class="flash ok">{html.escape(msg)}</div>' if msg else ""
    sel = "background:#0b1124;border:1px solid #26345e;color:#e9eefb;padding:8px 10px;border-radius:9px"
    styles = "".join(
        f'<option value="{s}"{" selected" if b["map_style"] == s else ""}>{s}</option>'
        for s in settings.MAP_STYLES)
    ck = lambda v: " checked" if v else ""
    inner = f"""
    {banner}
    <h1>Page behaviour</h1>
    <p class=sub>How the public site behaves. Changes apply on the next page load.</p>
    <form method=post action="/admin/settings/save">
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
        &nbsp;&nbsp;<label>intensity <input type=number name=glitch_intensity min=1 max=5 value="{b['glitch_intensity']}" style="width:60px"> <span class=hint>(1 subtle – 5 heavy)</span></label>
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
                  glitch_intensity: int = Form(2)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    settings.set_behaviour(db, poll_secs, bool(intro_enabled), intro_src, map_style,
                           bool(glitch_enabled), glitch_secs, glitch_intensity)
    return RedirectResponse("/admin/settings?msg=" + quote("behaviour saved — live on next page load"),
                            status_code=303)
