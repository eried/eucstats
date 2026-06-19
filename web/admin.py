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
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

import config
from database import get_db
from models import RawUpload, Rider, RiderStat, Trip, Wheel, utcnow
from services import audit, datasets, sandbox, settings
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


_NAV = [("/admin", "Overview"), ("/admin/explorer", "Riders & Trips"),
        ("/admin/wheels", "Wheels"), ("/admin/ingest", "Ingest"),
        ("/admin/audit", "Metric audit"),
        ("/admin/appearance", "Public site"), ("/admin/datasets", "Data & backups"),
        ("/admin/telegram", "Telegram"), ("/admin/system", "System")]

_IC = {
    "check": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>',
    "x": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    "db": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>',
    "pulse": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l3 8 4-16 3 8h4"/></svg>',
    "sliders": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7h16M4 17h16" stroke-linecap="round"/><circle cx="9" cy="7" r="2.3"/><circle cx="15" cy="17" r="2.3"/></svg>',
    "search": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/></svg>',
    "ban": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/></svg>',
    "back": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg>',
    "grip": '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/></svg>',
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
button:disabled,input:disabled{opacity:.38;cursor:not-allowed}
button:disabled:hover{filter:none;background:transparent}
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
.scrollbox{height:210px;overflow:auto;border:1px solid #1d2945;border-radius:9px}
.scrollbox table th{position:sticky;top:0;background:#10182e;z-index:1}
.pager{display:flex;gap:8px;align-items:center;margin:12px 0 0;font-size:13px}
.pager a,.pager span.cur{padding:6px 11px;border:1px solid #26345e;border-radius:8px;color:#cfe4ff}
.pager a:hover{border-color:#2ea8ff;text-decoration:none}
.pager .cur{background:rgba(46,168,255,.16);color:#2ea8ff;border-color:#2ea8ff}
.pager .off{opacity:.4;pointer-events:none}
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
.krow{display:flex;align-items:flex-start;gap:9px;padding:7px 10px;border:1px solid #1d2945;border-radius:8px;background:#0d142a}
.krow:hover{border-color:#2ea8ff}
.krow.dragging{opacity:.45;border-color:#2ea8ff}
.krow .grip{color:#4d5d85;cursor:grab;flex:none;display:flex;align-items:center;margin-top:1px}
.krow .grip:active{cursor:grabbing}.krow .grip svg{width:15px;height:15px}
.krow .ktoggle{display:flex;align-items:flex-start;gap:10px;flex:1;min-width:0;cursor:pointer;margin:0}
.krow input{width:15px;height:15px;accent-color:#2ea8ff;margin-top:2px;flex:none}
.krow .kl{font-size:13px;color:#e7ecfb;display:block}
.krow .kd{font-size:11.5px;color:#8ea0c8;display:block;margin-top:2px}
.krow.off .kl{text-decoration:line-through;color:#8ea0c8}
.mnone{color:#8ea0c8;font-size:12px;padding:0 2px 4px}
.rblock{border:1px solid #1d2945;border-radius:9px;background:#0d142a;padding:9px 12px;margin-bottom:7px}
.rblock.off{opacity:.55}
.rhead{display:flex;align-items:flex-start;gap:11px;cursor:pointer}
.rhead input{width:16px;height:16px;accent-color:#2ea8ff;margin-top:2px;flex:none}
.rparams{display:flex;flex-wrap:wrap;gap:14px;margin:9px 0 1px 27px}
.rparams .nopar{color:#5d6f95;font-size:11.5px}
.thr{display:flex;flex-direction:column;gap:3px;font-size:11.5px;color:#8ea0c8}
.thr input{width:100%;max-width:160px}
.thr .calc{display:block;font-size:10.5px;color:#7fd0ff;margin-top:3px;line-height:1.35;min-height:12px}
.calgrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px 18px;margin:12px 0 2px}
.calgrid .thr{background:#0b1124;border:1px solid #1d2945;border-radius:8px;padding:9px 11px}
@media(max-width:720px){.calgrid{grid-template-columns:repeat(2,minmax(0,1fr))}}
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
/* phones: stack the header so the nav becomes a tidy scrollable strip under the
   brand, instead of 8 tabs wrapping into a tall column beside it */
@media(max-width:640px){
  header.bar{flex-wrap:wrap;gap:8px 10px;padding:10px 12px}
  .brand{flex:1 1 auto;font-size:13px}
  header.bar>form{flex:0 0 auto}
  nav.tabs2{order:3;flex-basis:100%;flex-wrap:nowrap;overflow-x:auto;gap:5px;padding-bottom:4px;-webkit-overflow-scrolling:touch;scrollbar-width:thin;scrollbar-color:rgba(130,170,255,.45) transparent}
  nav.tabs2::-webkit-scrollbar{height:6px}
  nav.tabs2::-webkit-scrollbar-track{background:transparent}
  nav.tabs2::-webkit-scrollbar-thumb{background:rgba(130,170,255,.3);border-radius:4px}
  nav.tabs2::-webkit-scrollbar-thumb:hover{background:rgba(130,170,255,.5)}
  nav.tabs2 a{flex:0 0 auto;white-space:nowrap}
}
</style>"""


def _admin_shell(inner: str, active: str = "", chrome: bool = True) -> str:
    head = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<link rel=icon href=\"data:image/svg+xml,"
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'>"
            "<rect width='16' height='16' rx='3' fill='%230a0f1e'/>"
            "<text x='8' y='12' font-size='11' text-anchor='middle'>%E2%9A%99%EF%B8%8F</text></svg>\">"
            "<title>EUCSTATS · admin</title>" + _ADMIN_CSS + "</head><body>")
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
    _fr = {r.store_id: r for r in db.query(Rider).filter(
        Rider.store_id.in_({t.rider_store_id for t in flagged if t.rider_store_id})).all()} if flagged else {}

    def _frider(sid):                      # rider cell: name + flag (linked), store_id underneath
        r, sid_e = _fr.get(sid), html.escape(sid or "")
        if not r:
            return f"<code>{sid_e}</code>"
        fl = (html.escape(r.flag) + " ") if r.flag else ""
        return (f"<a href='/admin/explorer/rider/{quote(sid or '')}'>{fl}<b>{html.escape(r.display_name or sid or '?')}</b></a>"
                f"<div class=mut style='font-size:11px'><code>{sid_e}</code></div>")

    fhtml = "".join(
        f"<tr>"
        f"<td><a href='/admin/explorer/trip/{quote(t.trip_uuid)}'><code>{t.trip_uuid[:8]}</code></a></td>"
        f"<td>{_frider(t.rider_store_id)}</td>"
        f"<td class=mut style='white-space:nowrap'>{_fmt_dt(t.start_utc or t.created_at)}</td>"
        f"<td>{round(t.distance_km or 0,1)} km</td>"
        f"<td>{round(t.max_speed or 0,1)} km/h</td>"
        f"<td>{html.escape(', '.join(t.flag_reasons or []))}</td><td style='white-space:nowrap'>"
        f"<form method=get action='/admin/explorer/trip/{quote(t.trip_uuid)}' style='display:inline-flex;margin-right:6px'><button class='ghost mini'>{_IC['search']} view</button></form>"
        f"<form method=post action=/admin/trip/{t.trip_uuid}/approve style='display:inline-flex;margin-right:6px'><button class=mini>{_IC['check']} approve</button></form>"
        f"<form method=post action=/admin/trip/{t.trip_uuid}/reject style='display:inline-flex'><button class='mini danger'>{_IC['x']} reject</button></form>"
        f"</td></tr>" for t in flagged) or "<tr><td colspan=7 class=mut>nothing flagged, queue is clear</td></tr>"

    riders = db.query(Rider).order_by(desc(Rider.created_at)).limit(30).all()
    bset = set(settings.banned(db))          # fetched once; reused per row (no N+1)
    rhtml = "".join(
        f"<tr class=clk onclick=\"location='/admin/explorer/rider/{html.escape(r.store_id)}'\">"
        f"<td><code>{html.escape(r.store_id)}</code></td><td>{html.escape(r.display_name or '')}</td>"
        f"<td>{html.escape(r.flag or '')}</td><td>{html.escape(r.platform or '')}</td>"
        f"<td>{_rider_badges(db, r, bset)}</td></tr>"
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
    <p class=sub>Site banner: {'<span class="b test">ON</span>' if cur_test else '<span class="b live">OFF</span>'} &nbsp;{'a red banner is showing across the public site' if cur_test else 'clean public site, no banner'} &nbsp;·&nbsp; <a href="/admin/appearance">change in Appearance</a></p>
    <div class=card><div class=kpi>{kpis}</div></div>
    <div class=card>
      <h2>Quick actions</h2>
      <div class=qa>
        <a class=btn href="/admin/explorer">{_IC['search']} Explore riders &amp; trips</a>
        <a class=btn href="/admin/ingest">{_IC['pulse']} Ingest &amp; validation</a>
        <a class=btn href="/admin/appearance">{_IC['sliders']} Appearance</a>
        <a class=btn href="/admin/datasets">{_IC['db']} Datasets &amp; backups</a>
        <a class=btn href="/admin/system">{_IC['pulse']} System</a>
      </div>
    </div>
    <div class=card>
      <h2>Flagged trips, review queue</h2>
      <p class=hint>Trips held back by plausibility checks. Click a row's <b>view</b> (or the id) to see the full trip and its GPS track, then approve to count it toward leaderboards or reject to drop it.</p>
      <div class=scrollbox><table><tr><th>id</th><th>rider</th><th>when</th><th>distance</th><th>top speed</th><th>reasons</th><th>action</th></tr>{fhtml}</table></div>
    </div>
    <div class=card>
      <h2>Riders <span class=mut>· newest 30</span> <a href="/admin/explorer" class=mut style="float:right;font-size:12px">all riders →</a></h2>
      <div class=scrollbox><table><tr><th>store id</th><th>name</th><th>flag</th><th>platform</th><th>status</th></tr>{rhtml}</table></div>
    </div>
    <div class=card>
      <h2>Recent trips <span class=mut>· newest 30</span> <a href="/admin/explorer/trips" class=mut style="float:right;font-size:12px">all trips →</a></h2>
      <div class=scrollbox><table><tr><th>id</th><th>rider</th><th>km</th><th>country</th><th>status</th></tr>{thtml}</table></div>
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
def approve_trip(trip_uuid: str, request: Request, background_tasks: BackgroundTasks,
                 db: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    t = db.get(Trip, trip_uuid)
    if t and t.validation_status == "flagged":
        t.validation_status = "validated"
        t.flag_reasons = None
        db.commit()
        Aggregator(db).apply(t)   # now counts toward leaderboards
        from services import telegram      # first-ride announce if this approval is their 1st
        background_tasks.add_task(telegram.notify_first_ride, t.rider_store_id)
        background_tasks.add_task(telegram.check_records)   # approval may create a new #1
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
# is collected, this just surfaces what's in the active dataset for moderation.

def _fmt_dt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "—"


def _num(v, dec=1):
    return "—" if v is None else (round(v, dec) if dec else int(v))


def _field(k: str, v, hi: bool = False) -> str:
    return f'<div class="f{" hi" if hi else ""}"><div class=k>{html.escape(k)}</div><div class=v>{v}</div></div>'


def _rider_badges(db: Session, r: Rider, banned: set | None = None) -> str:
    # pass `banned` (a set of banned store_ids) to avoid a per-row query in lists
    is_banned = (r.store_id in banned) if banned is not None else settings.is_banned(db, r.store_id)
    parts = ['<span class="badge rejected">deleted</span>' if r.deleted_at
             else '<span class="badge validated">active</span>']
    if is_banned:
        parts.append('<span class="badge rejected">banned</span>')
    return " ".join(parts)


def _rider_row(db: Session, r: Rider, rs, banned: set | None = None) -> str:
    km = _num(rs.total_km if rs else 0, 1)
    n = int((rs.trip_count if rs else 0) or 0)
    return f"""<tr class=clk onclick="location='/admin/explorer/rider/{html.escape(r.store_id)}'">
      <td><code>{html.escape((r.store_id or '')[:12])}…</code></td>
      <td>{html.escape(r.display_name or '')}</td><td>{html.escape(r.flag or '')}</td>
      <td>{n}</td><td>{km} km</td><td>{_rider_badges(db, r, banned)}</td></tr>"""


def _trip_row(t: Trip) -> str:
    fr = t.max_freespin
    frs = f' <span class="chip flagged" title="freespin">⟳{fr}</span>' if fr else ""
    return f"""<tr class=clk onclick="location='/admin/explorer/trip/{html.escape(t.trip_uuid)}'">
      <td><code>{html.escape(t.trip_uuid[:8])}</code></td><td>{_fmt_dt(t.start_utc)}</td>
      <td>{_num(t.distance_km)}</td><td>{_num(t.max_speed)}{frs}</td>
      <td>{html.escape(t.country or '')}</td>
      <td><span class="badge {t.validation_status or 'pending'}">{html.escape(t.validation_status or 'pending')}</span></td></tr>"""


PAGE_SIZE = 50


def _pglink(label: str, url: str, enabled: bool) -> str:
    return f'<a class="{"" if enabled else "off"}" href="{url if enabled else "#"}">{label}</a>'


def _pager(base: str, page: int, has_next: bool, qs: str = "") -> str:
    pre = f"{base}?{qs}{'&' if qs else ''}"
    return (f'<div class=pager>{_pglink("← prev", pre + f"page={page-1}", page > 1)}'
            f'<span class=cur>page {page}</span>'
            f'{_pglink("next →", pre + f"page={page+1}", has_next)}</div>')


def _explorer_html(db: Session, q: str = "", page: int = 1) -> str:
    q = (q or "").strip()
    page = max(1, page)
    base = db.query(Rider, RiderStat).outerjoin(RiderStat, RiderStat.store_id == Rider.store_id)
    cnt = db.query(func.count(Rider.store_id))
    if q:
        like = f"%{q}%"
        cond = Rider.store_id.ilike(like) | Rider.display_name.ilike(like)
        base = base.filter(cond)
        cnt = cnt.filter(cond)
    total = cnt.scalar() or 0
    rows = (base.order_by(desc(Rider.created_at))
            .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1).all())
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    bn = settings.banned(db)                 # fetched once; reused per row (no N+1)
    bset = set(bn)
    body = "".join(_rider_row(db, r, rs, bset) for r, rs in rows) or \
        f"<tr><td colspan=6 class=mut>no riders{' match' if q else ''}</td></tr>"
    banned_note = (f'<p class=hint>{len(bn)} rider(s) currently banned, excluded from public stats.</p>'
                   if bn else "")
    qs = ("q=" + quote(q)) if q else ""
    lo = (page - 1) * PAGE_SIZE + (1 if rows else 0)
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
      <p class=hint>{total} rider(s){' matching' if q else ''} · showing {lo}–{lo + len(rows) - 1 if rows else 0}</p>
      {_pager("/admin/explorer", page, has_next, qs)}
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
      <table><tr><th>id</th><th>start (UTC)</th><th>km</th><th>max km/h</th><th>country</th><th>status</th></tr>{thtml}</table></div>
    <div class=card style="border-color:rgba(255,107,107,.4)">
      <h2 style="color:#ff9d9d">Danger zone</h2>
      <p class=hint>Permanently delete this rider and ALL their data, trips, GPS tracks, raw uploads, wheels and stats. Irreversible.
      (A rider closing their own account in the app keeps their portal presence; only this removes it.)</p>
      <form class=banbar method=post action="/admin/rider/{html.escape(store_id)}/delete"
            onsubmit="return confirm('Permanently delete this rider and all their data? This cannot be undone.')">
        <input type=text name=confirm placeholder="type the rider's name to confirm">
        <button class=danger>{_IC['ban']} Delete rider &amp; all data</button>
      </form>
    </div>"""
    return _admin_shell(inner, active="/admin/explorer")


def _trips_html(db: Session, status: str = "", country: str = "", store: str = "",
                q: str = "", page: int = 1) -> str:
    page = max(1, page)
    query = db.query(Trip)
    cnt = db.query(func.count(Trip.trip_uuid))
    if status:
        query = query.filter(Trip.validation_status == status); cnt = cnt.filter(Trip.validation_status == status)
    if country:
        cc = country.strip().upper()
        query = query.filter(Trip.country == cc); cnt = cnt.filter(Trip.country == cc)
    if store:
        query = query.filter(Trip.rider_store_id == store.strip()); cnt = cnt.filter(Trip.rider_store_id == store.strip())
    if q:
        query = query.filter(Trip.trip_uuid.ilike(f"{q.strip()}%")); cnt = cnt.filter(Trip.trip_uuid.ilike(f"{q.strip()}%"))
    total = cnt.scalar() or 0
    trips = (query.order_by(desc(Trip.start_utc))
             .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1).all())
    has_next = len(trips) > PAGE_SIZE
    trips = trips[:PAGE_SIZE]
    opts = "".join(f'<option value="{s}"{" selected" if status == s else ""}>{s or "any status"}</option>'
                   for s in ("", "validated", "flagged", "rejected"))
    body = "".join(_trip_row(t) for t in trips) or "<tr><td colspan=6 class=mut>no trips match</td></tr>"
    parts = [(k, v) for k, v in (("status", status), ("country", country), ("store", store), ("q", q)) if v]
    qs = "&".join(f"{k}={quote(str(v))}" for k, v in parts)
    lo = (page - 1) * PAGE_SIZE + (1 if trips else 0)
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
      <p class=hint>{total} trip(s) · showing {lo}–{lo + len(trips) - 1 if trips else 0}, newest first. ⟳ marks a freespin spike.</p>
      {_pager("/admin/explorer/trips", page, has_next, qs)}
    </div>"""
    return _admin_shell(inner, active="/admin/explorer")


_TRIP_MAP_TMPL = """
    <link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css"/>
    <script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
    <div class=card><h2>Route <span class=mut>· admin view · exact GPS path</span></h2>
      <div id=tmap style="height:360px;border-radius:10px;overflow:hidden;border:1px solid #26345e"></div>
      <p class=hint>Green = start, red = end. Full downsampled GPS path (admin only, public maps obfuscate rider locations).</p></div>
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
def explorer_page(request: Request, db: Session = Depends(get_db), q: str = "", page: int = 1):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    return HTMLResponse(_explorer_html(db, q, page))


@admin_router.get("/explorer/trips", response_class=HTMLResponse)
def explorer_trips(request: Request, db: Session = Depends(get_db),
                   status: str = "", country: str = "", store: str = "", q: str = "", page: int = 1):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    return HTMLResponse(_trips_html(db, status, country, store, q, page))


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
    """The trip's downsampled GPS path as GeoJSON (admin only, exact coords)."""
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
    audit.log("ban", f"rider={store_id} reason={(reason or '').strip()[:80]}")
    return RedirectResponse("/admin/explorer/rider/" + quote(store_id) + "?msg=" +
                            quote("rider banned, removed from public stats"), status_code=303)


@admin_router.post("/rider/{store_id}/unban")
def unban_rider(store_id: str, request: Request, db: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    from services.aggregator import rebuild_all
    settings.unban(db, store_id)
    rebuild_all(db)   # restore their trips to public stats
    audit.log("unban", f"rider={store_id}")
    return RedirectResponse("/admin/explorer/rider/" + quote(store_id) + "?msg=" +
                            quote("ban lifted, rider restored to public stats"), status_code=303)


@admin_router.post("/rider/{store_id}/delete")
def delete_rider(store_id: str, request: Request, db: Session = Depends(get_db), confirm: str = Form("")):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    r = db.get(Rider, store_id)
    if r is None:
        return RedirectResponse("/admin/explorer?msg=" + quote("rider not found"), status_code=303)
    expected = (r.display_name or store_id).strip()
    if (confirm or "").strip() not in (expected, store_id):
        return RedirectResponse("/admin/explorer/rider/" + quote(store_id) + "?msg=" +
                                quote("name did not match, not deleted"), status_code=303)
    from services.identity import purge_rider
    purge_rider(db, store_id)
    audit.log("delete_rider", f"rider={store_id} name={expected[:60]}")
    return RedirectResponse("/admin/explorer?msg=" + quote(f"permanently deleted “{expected}” and all their data"),
                            status_code=303)


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
        live = " <span class=mut>· live</span>" if d.get("live") else ""
        # the active dataset can't be deleted (the live DB runs off it), keep the same controls,
        # just disable the input + button on that row
        dis = " disabled" if is_active else ""
        del_cell = (f'<form method=post action="/admin/datasets/delete"><input type=hidden name=slug value="{d["slug"]}">'
                    f'<input name=confirm placeholder="type name"{dis}><button class=danger{dis}>delete</button></form>')
        rows += (
            f'<tr class="{"active" if is_active else ""}">'
            f'<td>{nm}{" • active" if is_active else ""}</td>'
            f'<td>{riders}{live}</td><td>{trips}</td><td>{_fmt_size(d["size"])}</td>'
            f'<td>{d.get("created","")}</td><td>{html.escape(d.get("origin",""))}</td>'
            f'<td class=acts>'
            f'<form method=get action="/admin/datasets/export/{d["slug"]}"><button class=ghost>download</button></form>'
            f'<form method=post action="/admin/datasets/switch"><input type=hidden name=slug value="{d["slug"]}">'
            f'<input name=confirm placeholder="type name"><button>switch</button></form>'
            f'<form method=post action="/admin/datasets/rename"><input type=hidden name=slug value="{d["slug"]}">'
            f'<input name=new_name placeholder="new name"><button>rename</button></form>'
            f'{del_cell}'
            f'</td></tr>')
    if not rows:
        rows = '<tr><td colspan=7 class=mut>no saved datasets yet</td></tr>'

    inner = f"""
    {banner}
    <h1>Data &amp; backups</h1>
    <p class=sub>Save, swap, import and back up the whole database as portable snapshots, and set how long
    raw uploads are kept. The site banner is a <a href="/admin/appearance">site setting</a> now.</p>
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
    <div class=card>
      <h2>Active dataset</h2>
      <p>{c['riders']} riders · {c['trips']} trips · {c['validated']} validated · {c['flagged']} flagged</p>
      <form method=post action="/admin/datasets/save" class=inline>
        <input name=name placeholder="snapshot name" required><input name=note placeholder="note (optional)">
        <button>Save current as snapshot</button>
      </form>
    </div>
    <h2>Saved datasets</h2>
    <table><tr><th>name</th><th>riders</th><th>trips</th><th>size</th><th>created (UTC)</th><th>origin</th><th>actions</th></tr>{rows}</table>
    <p class=mut>Switching or deleting requires typing the dataset's exact name. A switch auto-backs-up the
    current dataset (unless it's empty), then reconnects instantly, no restart, no downtime.</p>
    {_retention_card(db)}
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
        audit.log("dataset_new", f"name={name} (created + activated)")
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
        return _redir(err="confirmation name did not match, nothing deleted")
    try:
        datasets.delete(slug)
    except datasets.DatasetError as e:
        return _redir(err=str(e))
    audit.log("dataset_delete", f"name={entry['name']}")
    return _redir(msg=f"deleted “{entry['name']}”")


@admin_router.post("/datasets/switch")
def datasets_switch(request: Request, slug: str = Form(...), confirm: str = Form("")):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    entry = datasets._get_entry(slug)
    if not entry:
        return _redir(err="unknown dataset")
    if (confirm or "").strip() != entry["name"]:
        return _redir(err="confirmation name did not match, no switch")
    try:
        datasets.switch_to(slug, reload_app=_reload_app)
    except datasets.DatasetError as e:
        return _redir(err=str(e))
    audit.log("dataset_switch", f"to={entry['name']}")
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

# Live "plain-English" helpers under each threshold/calibration input. Keyed by the
# bare param key; each maps a numeric value to an easy-to-grasp sentence.
_PIPELINE_CALC_JS = """
    <script>
    (function(){
      function mph(v){return (v*0.621371).toFixed(0)+' mph';}
      var CALC = {
        max_kmh:function(v){return mph(v)+', wheel-speed ceiling';},
        max_g:function(v){return (v*9.81).toFixed(0)+' m/s², '+v+'× gravity';},
        teleport_kmh:function(v){return (v/3.6).toFixed(0)+' m/s between GPS fixes';},
        teleport_max_jumps:function(v){return v+' noisy GPS jumps tolerated before flagging';},
        dist_tolerance:function(v){return Math.round(v*100)+'% odometer-vs-GPS disagreement allowed';},
        mismatch_min_km:function(v){return 'only judged on rides longer than '+v+' km';},
        unverified_dist_km:function(v){return 'flag a GPS-less ride longer than '+v+' km';},
        max_accel:function(v){return '0–100 km/h in '+(100/v).toFixed(1)+'s · 0–50 in '+(50/v).toFixed(1)+'s';},
        sustain_secs:function(v){return 'power / current / g-force must hold '+v+'s to count';},
        freespin_margin:function(v){return 'a spike must beat realistic speed by '+v+' km/h to be a freespin';},
        accel_target_kmh:function(v){return 'launch metric measures 0 → '+v+' km/h';},
        accel_min_s:function(v){return 'launches under '+v+'s are treated as sensor noise';},
        accel_max_s:function(v){return 'only count a launch reaching target within '+v+'s';},
        sustain_accel_lo_s:function(v){return 'sustained acceleration held at least '+v+'s';},
        sustain_accel_hi_s:function(v){return '…measured over at most '+v+'s';},
        sag_window_s:function(v){return 'compare voltage to its peak over the last '+v+'s';},
        ascent_hysteresis_m:function(v){return 'ignore climbs / dips under '+v+' m';},
        odo_max_step_km:function(v){return 'reject odometer jumps over '+v+' km ('+Math.round(v*1000)+' m) per reading';},
        range_min_battery_pct:function(v){return 'only estimate full-charge range after at least '+v+'% battery used';},
        rider_create_per_ip:function(v){return v>0?('≈ 1 new account every '+(60/v).toFixed(1)+' min from one IP'):'unlimited';},
        trip_per_rider:function(v){return v>0?('≈ 1 upload every '+(60/v).toFixed(1)+' min per rider'):'unlimited';},
        trip_per_ip:function(v){return v>0?('≈ 1 upload every '+(60/v).toFixed(2)+' min from one IP'):'unlimited';}
      };
      function upd(inp){
        var f=CALC[inp.dataset.k]; if(!f) return;
        var s=document.querySelector('.calc[data-for="'+inp.dataset.k+'"]'); if(!s) return;
        var v=parseFloat(inp.value);
        try{ s.textContent=(isFinite(v))?('→ '+f(v)):''; }catch(e){ s.textContent=''; }
      }
      document.querySelectorAll('input[data-k]').forEach(function(inp){upd(inp);inp.addEventListener('input',function(){upd(inp);});});
    })();
    </script>"""


def _pipeline_html(db: Session, msg: str = "") -> str:
    total = db.query(func.count(Trip.trip_uuid)).scalar() or 0
    allow = settings.ingest_allow(db)
    by_status = dict(db.query(Trip.validation_status, func.count(Trip.trip_uuid))
                     .group_by(Trip.validation_status).all())
    chips = "".join(
        f'<span class="chip {html.escape(str(s or "pending"))}">{html.escape(str(s or "pending"))}: {n}</span>'
        for s, n in sorted(by_status.items(), key=lambda kv: -kv[1])) or '<span class=mut>no trips yet</span>'

    since = utcnow() - timedelta(days=13)
    daily = dict(db.query(func.date(Trip.created_at), func.count(Trip.trip_uuid))
                 .filter(Trip.created_at >= since).group_by(func.date(Trip.created_at)).all())
    today = utcnow().date()
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
    onames = dict(db.query(Rider.store_id, Rider.display_name)        # one query, not 2 per row
                  .filter(Rider.store_id.in_([sid for sid, _ in offenders])).all()) if offenders else {}
    ohtml = "".join(
        f'<tr><td><code>{html.escape(sid or "")}</code></td>'
        f'<td>{html.escape(onames.get(sid) or "")}</td>'
        f'<td>{n}</td></tr>'
        for sid, n in offenders) or '<tr><td colspan=3 class=mut>none</td></tr>'

    # --- anti-fraud rules, each shown WITH its own tunable thresholds ---
    disabled = settings.pipeline_disabled(db)
    thr = settings.get_thresholds(db)
    thrmap = {key: (lbl, kind, lo, hi)
              for key, lbl, _mk, _ca, kind, lo, hi in settings.PIPELINE_THRESHOLDS}

    def _thr_input(key):
        lbl, kind, lo, hi = thrmap[key]
        return (f'<label class=thr>{html.escape(lbl)}'
                f'<input type=number name="thr_{key}" data-k="{key}" value="{thr[key]}" '
                f'step="{"1" if kind == "int" else "any"}" min="{lo}" max="{hi}">'
                f'<span class=calc data-for="{key}"></span></label>')

    rule_blocks = ""
    for k, lbl, rdesc, *rest in settings.PIPELINE_RULES:
        params = rest[0] if rest else []
        on = k not in disabled
        pin = ("".join(_thr_input(p) for p in params) if params
               else '<span class=nopar>no tunable parameters, on/off only</span>')
        rule_blocks += (
            f'<div class="rblock{"" if on else " off"}">'
            f'<label class=rhead><input type=checkbox name=rule value="{k}"{" checked" if on else ""}>'
            f'<span class=tw><span class=kl>{html.escape(lbl)}</span>'
            f'<span class=kd>{html.escape(rdesc)}</span></span></label>'
            f'<div class=rparams>{pin}</div></div>')
    cal = settings.get_calibration(db)
    cal_inputs = "".join(
        f'<label class=thr>{html.escape(lbl)}'
        f'<input type=number name="cal_{key}" data-k="{key}" value="{cal[key]}" '
        f'step="{"1" if kind == "int" else "any"}" min="{lo}" max="{hi}">'
        f'<span class=calc data-for="{key}"></span></label>'
        for key, lbl, _mk, _ca, kind, lo, hi in settings.CALIBRATION)
    rl = settings.get_rate_limits(db)
    rl_inputs = "".join(
        f'<label class=thr>{html.escape(lbl)}'
        f'<input type=number name="rl_{key}" data-k="{key}" value="{rl[key]}" step="1" min="{lo}" max="{hi}">'
        f'<span class=calc data-for="{key}"></span></label>'
        for key, lbl, _mk, _ca, _kind, lo, hi in settings.RATE_LIMITS)
    rules_card = f"""
    <div class=card>
      <h2>Anti-fraud rules <span class=mut>· what we check on every upload</span></h2>
      <p class=hint>Each rule can be turned off independently, unticking <b>skips that check entirely</b>
      (its thresholds below then do nothing). Two rules are pure on/off (no parameters); the rest expose the
      exact limits they use. Rules run at ingest, so changes apply to <b>new</b> uploads.</p>
      <form method=post action="/admin/pipeline/rules">
        {rule_blocks}
        <h2 style="margin-top:18px">Telemetry calibration <span class=mut>· physics limits used to summarize every trip</span></h2>
        <p class=hint>The acceleration cap defines the <b>realistic</b> top speed and what counts as a <b>freespin</b>
        spike; the sustained window is how long power / current / g-force must hold to count as a record.
        <b>Applies to new uploads only</b>, already-ingested trips keep their stored values.</p>
        <div class=calgrid>{cal_inputs}</div>
        <h2 style="margin-top:18px">Rate limits <span class=mut>· flood protection (per hour; 0 = off)</span></h2>
        <p class=hint>New accounts are capped <b>per IP</b> (pre-account there's no other signal, note shared
        carriers/VPNs share an IP, so keep it generous). Uploads are capped <b>per rider</b> and <b>per IP</b>.
        Over the cap returns HTTP 429.</p>
        <div class=calgrid>{rl_inputs}</div>
        <button style="margin-top:12px">{_IC['check']} Save rules, thresholds, calibration &amp; limits</button>
      </form>
    </div>""" + _PIPELINE_CALC_JS

    from services.reprocess import raw_available_count
    raw_n = raw_available_count(db)
    reprocess_card = f"""
    <div class=card>
      <h2>Re-process with current calibration</h2>
      <p class=hint>Calibration changes only affect <b>new</b> uploads. This recomputes speed / freespin /
      g-force / sag / acceleration for the <b>{raw_n}</b> trip(s) whose raw file is still on disk
      (within the retention window), using the calibration above, then rebuilds the leaderboards.
      Validation status is left unchanged; trips whose raw was already evicted can't be redone.</p>
      <form method=post action="/admin/pipeline/reprocess"
            onsubmit="return confirm('Re-summarize {raw_n} trip(s) from their raw upload with the current calibration?')">
        <button class=ghost{' disabled' if not raw_n else ''}>↻ Re-process {raw_n} trip(s)</button>
      </form>
    </div>"""

    banner = f'<div class="flash ok">{html.escape(msg)}</div>' if msg else ""
    inner = f"""
    {banner}
    <h1>Ingest pipeline</h1>
    <p class=sub>How uploads are flowing in and how validation is treating them.</p>
    {rules_card}
    {reprocess_card}
    <div class=card>
      <h2>Status, {total} trips total</h2>
      <p>{chips}</p>
      <p class=mut>Attestation: <b>{html.escape(config.ATTESTATION_MODE)}</b>
      ({'accepting all uploads, no verification' if config.ATTESTATION_MODE == 'stub' else 'requires an attestation token, presence only, not yet cryptographically verified'})
      · package <b>{html.escape(config.ANDROID_PACKAGE)}</b></p>
      <form method=post action="/admin/allowlist" style="margin-top:12px">
        <label class=toggle style="display:inline-flex"><input type=checkbox name=enabled value=1{' checked' if allow['enabled'] else ''}> Restrict uploads to an allowlist</label>
        <p class=hint style="margin:8px 0 4px">Off = any registered rider can upload (use during the open test period).
        On = only the store_ids below (others get 403). Currently <b>{'ON' if allow['enabled'] else 'OFF'}</b>.</p>
        <input name=ids value="{html.escape(', '.join(allow['ids']))}" placeholder="store_id, store_id, …" style="width:min(520px,100%)">
        <div style="margin-top:10px"><button>{_IC['check']} Save upload restriction</button></div>
      </form>
      <form method=post action="/admin/rebuild" style="margin-top:6px"
            onsubmit="return confirm('Recompute all leaderboards &amp; records from validated trips?')">
        <button class="ghost mini">↻ Rebuild stats</button></form>
    </div>
    <div class=card>
      <h2>Ingest, last 14 days</h2>
      {bars}
    </div>
    <div class=card>
      <h2>Repeat offenders <span class=mut>· riders by flagged/rejected trips</span></h2>
      <table><tr><th>store id</th><th>name</th><th>flagged/rejected</th></tr>{ohtml}</table>
    </div>
    <h2>Recent uploads (newest 60)</h2>
    <table><tr><th>created (UTC)</th><th>rider</th><th>km</th><th>country</th><th>status</th><th>flag/reject reasons</th></tr>{rrows}</table>
    """
    return _ds_page(inner, "/admin/ingest")


def _wheel_quality_card(db: Session) -> str:
    cat = settings.wheel_catalog(db)
    metrics = settings.WHEEL_METRICS
    prim = {m: settings.WHEEL_METRIC_FIELDS[m][0] for m in metrics}
    ncol = len(metrics) + 1
    if not cat:
        body = "<p class=mut>No wheels reported yet.</p>"
    else:
        rows = []
        for e in cat:
            rule = e.get("rule") or {}
            sel = set(rule.get("metrics") or [])
            cut = rule.get("max_app_version") or ""
            has_rule = bool(sel)
            editing = not has_rule                 # applied rule -> starts locked; no rule -> editable
            dis = "" if editing else " disabled"
            vers = ", ".join(f"{html.escape(v)} ({n})" for v, n in sorted(e["versions"].items()))
            seenv = [v for v in e["versions"] if v != "?"]
            if cut and cut not in seenv:                       # keep a saved cutoff visible
                seenv.append(cut)
            cutopts = '<option value="">(whole model, all versions)</option>' + "".join(
                f'<option value="{html.escape(v)}"{" selected" if v == cut else ""}>{html.escape(v)}</option>'
                for v in sorted(seenv, key=settings._ver_tuple))
            hdr = "<th class=wql></th>" + "".join(f'<th title="{prim[m]}">{m}</th>' for m in metrics)
            checkrow = "<td class=wql>invalid</td>" + "".join(
                f'<td><input type=checkbox name=metrics value="{m}"{" checked" if m in sel else ""}{dis}></td>'
                for m in metrics)
            cls = "wqrow" + (" wqactive" if has_rule else " wqedit-on wqnorule")
            hint = ("pick a version below → loads min · avg · max for trips ≤ that version" if editing
                    else "press Edit to change this rule and load its value ranges")
            summary = (('<span class=wqon>● ignoring ' + html.escape(', '.join(sorted(sel)))
                        + (f' · app ≤ {html.escape(cut)}' if cut else ' · whole model') + '</span>')
                       if has_rule else '')
            rows.append(f"""
            <form method=post action="/admin/wheel-quality" class="{cls}" data-om="{html.escape(','.join(sorted(sel)))}" data-oc="{html.escape(cut)}">
              <input type=hidden name=brand value="{html.escape(e['brand'])}">
              <input type=hidden name=model value="{html.escape(e['model'])}">
              <div class=wqhead><b>{html.escape(e['brand'])} · {html.escape(e['model'])}</b>
                <span class=mut>{e['riders']} riders · {e['trips']} trips · app {vers}</span></div>
              <div class=wqtwrap><table class=wqt>
                <tr>{hdr}</tr>
                <tr class=wqck>{checkrow}</tr>
                <tbody class=wqstats><tr><td colspan={ncol} class=wqhint>{hint}</td></tr></tbody>
              </table></div>
              <div class=wqfoot>
                <label>invalid for app_version ≤ <select name=cutoff class=wqsel{dis}>{cutopts}</select></label>
                <button type=button class="mini wqedit">✎ Edit rule</button>
                <button class="mini wqsave">{_IC['check']} Save &amp; rebuild</button>
                <button type=button class="mini ghost wqundo">↶ Undo</button>
                {summary}
              </div>
            </form>""")
        body = "".join(rows)
    return f"""
    <style>
    .wqrow{{border:1px solid #26345e;border-radius:10px;padding:11px 13px;margin:8px 0;background:rgba(0,0,0,.15)}}
    .wqrow.wqactive{{border-color:rgba(255,170,80,.55);background:rgba(255,170,80,.06)}}
    .wqhead{{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:8px}}
    .wqtwrap{{overflow-x:auto;margin:2px 0 10px}}
    .wqt{{border-collapse:collapse;font-size:11px}}
    .wqt th,.wqt td{{border:1px solid #1e2a4d;padding:3px 8px;text-align:center;white-space:nowrap}}
    .wqt th{{color:#9fb2d8;font-weight:600}}
    .wqt .wql{{text-align:left;color:#8aa0c8;position:sticky;left:0;background:#0c1430}}
    .wqck input{{accent-color:#2ea8ff;cursor:pointer}}
    .wqt td.wqhint{{text-align:left;color:#6b7ba5;position:static;font-style:italic}}
    .wqfoot{{display:flex;align-items:center;gap:12px;flex-wrap:wrap;font-size:11.5px;color:#8aa0c8}}
    .wqsel{{background:#0b1124;border:1px solid #26345e;color:#e9eefb;padding:4px 7px;border-radius:7px;font-size:11.5px}}
    .wqsel:disabled{{opacity:.6;cursor:default}}
    .wqck input:disabled{{cursor:default}}
    .wqon{{color:#ffb04a}}
    .wqsave,.wqundo{{display:none}}
    .wqedit-on .wqedit{{display:none}}
    .wqedit-on .wqsave{{display:inline-flex}}
    .wqedit-on:not(.wqnorule) .wqundo{{display:inline-flex}}
    </style>
    <div class=card>
      <h2>Wheel data quality <span class=mut>· ignore bad channels per model</span></h2>
      <p class=hint>If a model reports a bad channel (e.g. wrong voltage), tick those metric columns to drop
      them from leaderboards and records for that model. <b>app_version ≤</b> limits it to old builds (blank =
      all). Voltage and power are linked (power = volts × amps). Saving rebuilds stats.</p>
      {body}
    </div>
    <script>
    function wqRanges(f){{
      var s=f.querySelector('.wqsel'), tb=f.querySelector('tbody.wqstats');
      var brand=f.querySelector('input[name=brand]').value, model=f.querySelector('input[name=model]').value;
      tb.innerHTML='<tr><td colspan={ncol} class=wqhint>loading…</td></tr>';
      fetch('/admin/wheels/ranges?brand='+encodeURIComponent(brand)+'&model='+encodeURIComponent(model)+'&cutoff='+encodeURIComponent(s.value))
        .then(function(r){{return r.ok?r.text():Promise.reject();}})
        .then(function(h){{tb.innerHTML=h;}})
        .catch(function(){{tb.innerHTML='<tr><td colspan={ncol} class=wqhint>failed to load</td></tr>';}});
    }}
    function wqEdit(f,on){{
      f.classList.toggle('wqedit-on',on);
      f.querySelectorAll('input[name=metrics],select[name=cutoff]').forEach(function(el){{el.disabled=!on;}});
    }}
    document.addEventListener('change', function(ev){{
      var s=ev.target.closest('.wqsel'); if(!s||s.disabled) return;   // selecting a version (re)loads ranges
      wqRanges(s.closest('.wqrow'));
    }});
    document.addEventListener('click', function(ev){{
      var ed=ev.target.closest('.wqedit');
      if(ed){{ var f=ed.closest('.wqrow'); wqEdit(f,true); wqRanges(f); return; }}   // unlock + load ranges
      var un=ev.target.closest('.wqundo');
      if(un){{ var f=un.closest('.wqrow');                                          // revert to saved state
        var om=(f.dataset.om||'').split(',').filter(Boolean);
        f.querySelectorAll('input[name=metrics]').forEach(function(el){{el.checked=om.indexOf(el.value)>=0;}});
        f.querySelector('select[name=cutoff]').value=f.dataset.oc||'';
        f.querySelector('tbody.wqstats').innerHTML='<tr><td colspan={ncol} class=wqhint>press Edit to change this rule and load its value ranges</td></tr>';
        wqEdit(f,false);
      }}
    }});
    </script>"""


def _name_fix_card(db: Session) -> str:
    rules = settings.get_name_rules(db)
    cat = settings.wheel_catalog(db)
    brands = sorted({e["brand"] for e in cat})
    models = sorted({e["model"] for e in cat})
    versions = sorted({v for e in cat for v in e["versions"] if v != "?"}, key=settings._ver_tuple)
    bopts = "".join(f"<option>{html.escape(b)}</option>" for b in brands)
    mopts = "".join(f"<option>{html.escape(m)}</option>" for m in models)
    vopts = "".join(f"<option>{html.escape(v)}</option>" for v in versions)

    if rules:
        rl = []
        for i, r in enumerate(rules):
            mt = " · ".join(filter(None, [
                f"brand={html.escape(r['m_brand'])}" if r.get("m_brand") else "",
                f"model={html.escape(r['m_model'])}" if r.get("m_model") else "",
                f"app ≤ {html.escape(r['max_app_version'])}" if r.get("max_app_version") else ""])) or "any wheel"
            st = " · ".join(filter(None, [
                f"brand → <b>{html.escape(r['set_brand'])}</b>" if r.get("set_brand") else "",
                f"model → <b>{html.escape(r['set_model'])}</b>" if r.get("set_model") else ""]))
            rl.append(f'<div class=nfrule><span>{mt} &nbsp;⇒&nbsp; {st}</span>'
                      f'<form method=post action="/admin/wheels/name-rule/remove" style="margin:0">'
                      f'<input type=hidden name=idx value="{i}"><button class="mini ghost">remove</button></form></div>')
        rules_html = "".join(rl)
    else:
        rules_html = "<p class=mut>No name fixes yet.</p>"

    changes = settings.name_fix_changes(db, rules)
    if changes:
        ex = "".join(f"<li>{html.escape(c['brand'])} · {html.escape(c['model'])} → "
                     f"<b>{html.escape(c['new_brand'])} · {html.escape(c['new_model'])}</b></li>" for c in changes[:12])
        more = f"<li class=mut>…and {len(changes) - 12} more</li>" if len(changes) > 12 else ""
        pending = (f'<div class=nfpend><p><b>{len(changes)}</b> existing wheel(s) would change:</p><ul>{ex}{more}</ul>'
                   f'<form method=post action="/admin/wheels/name-apply" '
                   f"onsubmit=\"return confirm('Snapshot the dataset and rewrite {len(changes)} wheel name(s)?')\">"
                   f'<button class=go>{_IC["check"]} Apply to existing + snapshot</button></form></div>')
    else:
        pending = "<p class=mut>No existing wheels need changing (new uploads are fixed automatically).</p>"

    return f"""
    <style>
    .nfrule{{display:flex;justify-content:space-between;gap:10px;align-items:center;border:1px solid #26345e;border-radius:8px;padding:7px 11px;margin:6px 0;font-size:12px}}
    .nfadd{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:10px;font-size:12px}}
    .nfadd select,.nfadd input{{background:#0b1124;border:1px solid #26345e;color:#e9eefb;padding:5px 7px;border-radius:7px;font-size:12px}}
    .nfnew{{display:none}}
    .nfpend{{border:1px solid rgba(255,170,80,.4);background:rgba(255,170,80,.06);border-radius:9px;padding:10px 12px;margin-top:10px}}
    .nfpend ul{{margin:4px 0 8px 18px}}
    </style>
    <div class=card>
      <h2>Brand &amp; model fixes <span class=mut>· rename mislabeled wheels</span></h2>
      <p class=hint>Rename wheels the app mislabels (e.g. Leaperkim/Veteran reported as KingSong). Match the
      reported values, set the correct ones. <b>app ≤</b> limits it to old builds (blank = always).
      <b>Apply</b> rewrites existing wheels (the dataset is snapshotted first, so you can revert).</p>
      {rules_html}
      <form method=post action="/admin/wheels/name-rule/add" class=nfadd>
        <span>If</span>
        <select name=m_brand><option value="">(any brand)</option>{bopts}</select>
        <select name=m_model><option value="">(any model)</option>{mopts}</select>
        <select name=max_app_version><option value="">(all versions)</option>{vopts}</select>
        <span><b>⇒</b> set</span>
        <select name=set_brand_sel class=nfsel><option value="">keep brand</option>{bopts}<option value="__new__">new brand…</option></select>
        <input name=set_brand_new class=nfnew placeholder="new brand">
        <select name=set_model_sel class=nfsel><option value="">keep model</option>{mopts}<option value="__new__">new model…</option></select>
        <input name=set_model_new class=nfnew placeholder="new model">
        <button class=mini>{_IC['check']} Add fix</button>
      </form>
      {pending}
    </div>
    <script>
    document.addEventListener('change', function(ev){{
      var s=ev.target.closest('.nfsel'); if(!s) return;
      var inp=s.nextElementSibling;
      if(inp && inp.classList.contains('nfnew')){{ inp.style.display = (s.value==='__new__') ? 'inline-block' : 'none'; }}
    }});
    </script>"""


def _wheels_html(db: Session, msg: str = "") -> str:
    banner = f'<div class="flash ok">{html.escape(msg)}</div>' if msg else ""
    inner = f"""
    {banner}
    <h1>Wheels &amp; data quality</h1>
    <p class=sub>Every brand/model that has reported data. Fix wrong <b>names</b> (brand/model) below, or
    ignore bad <b>numbers</b> (metrics) per model further down.</p>
    {_name_fix_card(db)}
    {_wheel_quality_card(db)}"""
    return _ds_page(inner, "/admin/wheels")


@admin_router.get("/ingest", response_class=HTMLResponse)
def ingest_page(request: Request, db: Session = Depends(get_db), msg: str = ""):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    return HTMLResponse(_pipeline_html(db, msg))


@admin_router.get("/wheels", response_class=HTMLResponse)
def wheels_page(request: Request, db: Session = Depends(get_db), msg: str = ""):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    return HTMLResponse(_wheels_html(db, msg))


def _wheel_ranges_fragment(stats: dict, metrics) -> str:
    """min/avg/max rows aligned to the metric columns (filled into the card table on demand)."""
    def n(x):
        try:
            return ("%.2f" % float(x)).rstrip("0").rstrip(".")
        except Exception:
            return "—"

    def srow(label, key):
        return (f"<tr><td class=wql>{label}</td>"
                + "".join(f"<td>{n(stats.get(m, {}).get(key))}</td>" for m in metrics) + "</tr>")
    return srow("min", "min") + srow("avg", "avg") + srow("max", "max")


@admin_router.get("/wheels/ranges", response_class=HTMLResponse)
def wheels_ranges(request: Request, brand: str = "", model: str = "", cutoff: str = "",
                  db: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return HTMLResponse("", status_code=401)
    stats = settings.wheel_metric_stats(db, brand, model, cutoff.strip() or None)
    return HTMLResponse(_wheel_ranges_fragment(stats, settings.WHEEL_METRICS))


@admin_router.post("/wheels/name-rule/add")
def wheels_name_rule_add(request: Request, db: Session = Depends(get_db),
                         m_brand: str = Form(""), m_model: str = Form(""), max_app_version: str = Form(""),
                         set_brand_sel: str = Form(""), set_brand_new: str = Form(""),
                         set_model_sel: str = Form(""), set_model_new: str = Form("")):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    resolve = lambda sel, new: (new.strip() if sel == "__new__" else sel.strip())
    sb, sm = resolve(set_brand_sel, set_brand_new), resolve(set_model_sel, set_model_new)
    if not (sb or sm):
        return RedirectResponse("/admin/wheels?msg=" + quote("nothing to set, rule ignored"), status_code=303)
    rules = settings.get_name_rules(db)
    rules.append({"m_brand": m_brand.strip(), "m_model": m_model.strip(),
                  "max_app_version": max_app_version.strip(), "set_brand": sb, "set_model": sm})
    settings.set_name_rules(db, rules)
    audit.log("name_rule_add", f"match[{m_brand}/{m_model} <= {max_app_version}] set[{sb}/{sm}]")
    return RedirectResponse("/admin/wheels?msg=" + quote("name fix added, review & Apply"), status_code=303)


@admin_router.post("/wheels/name-rule/remove")
def wheels_name_rule_remove(request: Request, idx: int = Form(-1), db: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    rules = settings.get_name_rules(db)
    if 0 <= idx < len(rules):
        rules.pop(idx)
        settings.set_name_rules(db, rules)
    return RedirectResponse("/admin/wheels?msg=" + quote("name fix removed"), status_code=303)


@admin_router.post("/wheels/name-apply")
def wheels_name_apply(request: Request, db: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    try:                                            # safety net: snapshot before a destructive rewrite
        from services import datasets
        datasets.save_current(datasets._timestamped("pre-namefix"),
                              note="auto backup before brand/model fixes", origin="pre-edit")
    except Exception:
        pass
    changes = settings.apply_name_fixes(db)
    audit.log("name_fix_apply", f"{len(changes)} wheels")
    return RedirectResponse("/admin/wheels?msg=" + quote(f"renamed {len(changes)} wheel(s), snapshot saved"),
                            status_code=303)


@admin_router.post("/wheel-quality")
def wheel_quality_save(request: Request, db: Session = Depends(get_db),
                       brand: str = Form(...), model: str = Form(...),
                       cutoff: str = Form(""), metrics: list[str] = Form([])):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    mets = [m for m in metrics if m in settings.WHEEL_METRIC_FIELDS]
    rules = [r for r in settings.get_wheel_rules(db)            # replace this model's rule
             if not (r.get("brand") == brand and r.get("model") == model)]
    if mets:
        rules.append({"brand": brand, "model": model,
                      "max_app_version": cutoff.strip() or None, "metrics": mets})
    settings.set_wheel_rules(db, rules)
    from services.aggregator import rebuild_all
    n = rebuild_all(db)                                          # recompute rider boards + records
    audit.log("wheel_quality_save", f"{brand}/{model} metrics={','.join(mets) or 'none'} cutoff={cutoff or '*'}")
    note = (f"{brand} {model}: " + (f"ignoring {', '.join(mets)}" if mets else "rule cleared")
            + (f" (app ≤ {cutoff})" if (mets and cutoff.strip()) else "") + f", rebuilt {n} trips")
    return RedirectResponse("/admin/wheels?msg=" + quote(note), status_code=303)


@admin_router.get("/pipeline")           # back-compat: old bookmark -> Ingest
def pipeline_redirect():
    return RedirectResponse("/admin/ingest", status_code=307)


@admin_router.post("/rebuild")
def admin_rebuild(request: Request, db: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    from services.aggregator import rebuild_all
    n = rebuild_all(db)
    audit.log("rebuild_stats", f"{n} validated trips")
    return RedirectResponse("/admin/ingest?msg=" + quote(f"rebuilt stats from {n} validated trips"),
                            status_code=303)


@admin_router.post("/pipeline/reprocess")
def pipeline_reprocess(request: Request, db: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    from services.reprocess import reprocess_with_calibration
    r = reprocess_with_calibration(db)
    audit.log("reprocess", f"reprocessed={r['reprocessed']} failed={r['failed']} of {r['available']} with raw")
    return RedirectResponse("/admin/ingest?msg=" + quote(
        f"re-processed {r['reprocessed']} trip(s) with current calibration"
        + (f" ({r['failed']} failed)" if r['failed'] else "")), status_code=303)


@admin_router.post("/pipeline/rules")
async def pipeline_rules_save(request: Request, db: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    form = await request.form()
    settings.set_pipeline_enabled(db, form.getlist("rule"))
    settings.set_thresholds(db, {key: form.get("thr_" + key) for key, *_ in settings.PIPELINE_THRESHOLDS})
    settings.set_calibration(db, {key: form.get("cal_" + key) for key, *_ in settings.CALIBRATION})
    settings.set_rate_limits(db, {key: form.get("rl_" + key) for key, *_ in settings.RATE_LIMITS})
    off = len(settings.pipeline_disabled(db))
    audit.log("rules_save", f"{len(settings.PIPELINE_RULES) - off}/{len(settings.PIPELINE_RULES)} rules active")
    note = f"rules saved, {len(settings.PIPELINE_RULES) - off}/{len(settings.PIPELINE_RULES)} active"
    return RedirectResponse("/admin/ingest?msg=" + quote(note), status_code=303)


@admin_router.post("/allowlist")
def allowlist_save(request: Request, db: Session = Depends(get_db),
                   enabled: str = Form(""), ids: str = Form("")):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    id_list = [s.strip() for s in ids.replace("\n", ",").replace(";", ",").split(",") if s.strip()]
    settings.set_ingest_allow(db, bool(enabled), id_list)
    state = f"on ({len(id_list)} id{'s' if len(id_list) != 1 else ''})" if enabled else "off, open to all"
    audit.log("allowlist", state)
    return RedirectResponse("/admin/ingest?msg=" + quote("allowlist " + state), status_code=303)


# --- metric / section show-hide toggles ---

_METRICS_JS = """
    <script>
    (function(){
      function offc(cb){var r=cb.closest('.krow');if(r){r.classList.toggle('off',!cb.checked);}}
      document.querySelectorAll('.mnode').forEach(function(node){
        var p=node.querySelector('.psel');
        var kids=node.querySelectorAll('.kids input[type=checkbox]');
        function sync(){var on=0;kids.forEach(function(c){if(c.checked)on++;});
          if(p){p.checked=(on===kids.length&&kids.length>0);p.indeterminate=(on>0&&on<kids.length);}}
        kids.forEach(function(c){c.addEventListener('change',function(){offc(c);sync();});});
        if(p)p.addEventListener('change',function(){     // header ticks / unticks every metric below it
          kids.forEach(function(c){c.checked=p.checked;offc(c);});p.indeterminate=false;});
        sync();
      });
      // drag to reorder metrics within a section; the hidden .ordfield carries the order on save
      document.querySelectorAll('.mnode').forEach(function(node){
        var box=node.querySelector('.kids'), ordf=node.querySelector('.ordfield');
        if(!box||!ordf)return;
        function serialize(){ordf.value=[].map.call(box.querySelectorAll('.krow'),function(r){return r.dataset.k;}).join(',');}
        var drag=null;
        box.querySelectorAll('.krow').forEach(function(row){
          row.addEventListener('dragstart',function(e){drag=row;row.classList.add('dragging');e.dataTransfer.effectAllowed='move';});
          row.addEventListener('dragend',function(){if(drag)drag.classList.remove('dragging');drag=null;serialize();});
        });
        box.addEventListener('dragover',function(e){
          e.preventDefault();if(!drag)return;
          var rows=box.querySelectorAll('.krow:not(.dragging)'),after=null;
          for(var i=0;i<rows.length;i++){var b=rows[i].getBoundingClientRect();if(e.clientY<b.top+b.height/2){after=rows[i];break;}}
          if(after)box.insertBefore(drag,after);else box.appendChild(drag);
        });
      });
    })();
    </script>"""


def _metrics_section(db: Session) -> str:
    h = settings.get_hidden(db)
    g = h["groups"]
    order = settings.get_metric_order(db)
    secmeta = {k: (lbl, desc) for k, lbl, desc in settings.METRIC_SECTIONS}
    # section_key -> (child form field, items, that section's own hidden list, order key)
    kids_of = {
        "riders": ("show_board", settings.METRIC_BOARDS, h["boards"], "boards"),
        "countries": ("show_gcountries", settings.METRIC_GROUPS, g["countries"], "gcountries"),
        "wheels": ("show_gwheels", settings.METRIC_GROUPS, g["wheels"], "gwheels"),
        "brands": ("show_gbrands", settings.METRIC_GROUPS, g["brands"], "gbrands"),
        "records": ("show_record", settings.METRIC_RECORDS, h["records"], "records"),
        "tech": ("show_app", settings.METRIC_APP, h["app"], "app"),
    }

    def krow(field, k, label, desc, hidden):
        on = k not in hidden
        return (f'<div class="krow{"" if on else " off"}" draggable="true" data-k="{k}">'
                f'<span class=grip aria-hidden="true">{_IC["grip"]}</span>'
                f'<label class=ktoggle><input type=checkbox name={field} value="{k}"{" checked" if on else ""}>'
                f'<span class=tw><span class=kl>{html.escape(label)}</span>'
                f'<span class=kd>{html.escape(desc)}</span></span></label></div>')

    def node(sec_key):
        lbl, desc = secmeta[sec_key]
        field, items, hidden, okey = kids_of[sec_key]
        items = settings.order_items(items, order.get(okey))     # admin's saved drag order
        shown = sum(1 for k, *_ in items if k not in hidden)
        allon = shown == len(items)
        body = "".join(krow(field, k, l, d, hidden) for k, l, d in items)
        cnt = f'<span class=cnt>{shown}/{len(items)} on</span>'
        # the header checkbox is a UI master-toggle only (no name -> not submitted); a section
        # is hidden purely because all its metrics are off.
        return (f'<div class=mnode>'
                f'<label class=mhead><input type=checkbox class=psel data-parent="{sec_key}"{" checked" if allon else ""}>'
                f'<span class=tw><span class=t>{html.escape(lbl)}</span>'
                f'<span class=d>{html.escape(desc)}</span></span>{cnt}</label>'
                f'<div class=kids>{body}</div>'
                f'<input type=hidden name="order_{okey}" class=ordfield value="{",".join(k for k, *_ in items)}"></div>')

    tree = "".join(node(k) for k, *_ in settings.METRIC_SECTIONS)
    return f"""
    <div class=card>
      <h2>Metric visibility</h2>
      <p class=hint>Untick to hide a metric from the public site. Hidden metrics are still processed, just not shown.</p>
      <form method=post action="/admin/metrics/save">
        <div class=mtree2>{tree}</div>
        <button>{_IC['check']} Save visibility</button>
      </form>
    </div>
    {_METRICS_JS}"""


@admin_router.get("/metrics")            # back-compat: old bookmark -> Appearance
def metrics_redirect():
    return RedirectResponse("/admin/appearance", status_code=307)


@admin_router.post("/metrics/save")
def metrics_save(request: Request, db: Session = Depends(get_db),
                 show_board: list[str] = Form([]), show_app: list[str] = Form([]),
                 show_record: list[str] = Form([]),
                 show_gcountries: list[str] = Form([]), show_gwheels: list[str] = Form([]),
                 show_gbrands: list[str] = Form([]),
                 order_boards: str = Form(""), order_gcountries: str = Form(""),
                 order_gwheels: str = Form(""), order_gbrands: str = Form(""),
                 order_records: str = Form(""), order_app: str = Form("")):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    settings.set_metric_order(db, {                       # admin's drag-to-reorder, applied on the public site
        "boards": order_boards.split(","), "gcountries": order_gcountries.split(","),
        "gwheels": order_gwheels.split(","), "gbrands": order_gbrands.split(","),
        "records": order_records.split(","), "app": order_app.split(",")})
    gk = [k for k, *_ in settings.METRIC_GROUPS]
    hidden_boards = [k for k, *_ in settings.METRIC_BOARDS if k not in show_board]
    hidden_app = [k for k, *_ in settings.METRIC_APP if k not in show_app]
    hidden_records = [k for k, *_ in settings.METRIC_RECORDS if k not in show_record]
    groups = {"countries": [k for k in gk if k not in show_gcountries],
              "wheels": [k for k in gk if k not in show_gwheels],
              "brands": [k for k in gk if k not in show_gbrands]}
    settings.set_hidden(db, boards=hidden_boards, app=hidden_app, records=hidden_records, groups=groups)
    settings.mark_boards_shown(db, show_board)   # default-off newcomers stay off until ticked once
    audit.log("metrics_save", "hidden boards=%d app=%d rec=%d grp c/w/b=%d/%d/%d" % (
        len(hidden_boards), len(hidden_app), len(hidden_records),
        len(groups["countries"]), len(groups["wheels"]), len(groups["brands"])))
    return RedirectResponse("/admin/appearance?msg=" + quote("visibility saved, live now"),
                            status_code=303)


def _score_cfg_from_form(dist_exp, speed_div, hours_div, speed_on, hours_on) -> dict:
    """Clamp raw form values into a champions() config (shared by preview + save)."""
    def cf(v, d, lo, hi):
        try:
            return max(lo, min(hi, float(v)))
        except (TypeError, ValueError):
            return d
    return {"dist_exp": cf(dist_exp, 1.0, 0.1, 3.0), "speed_on": bool(speed_on),
            "speed_div": cf(speed_div, 100.0, 1.0, 100000.0), "hours_on": bool(hours_on),
            "hours_div": cf(hours_div, 10.0, 0.1, 100000.0)}


@admin_router.post("/score/preview")
def score_preview(request: Request, db: Session = Depends(get_db),
                  dist_exp: str = Form("1"), speed_div: str = Form("100"), hours_div: str = Form("10"),
                  speed_on: str = Form(None), hours_on: str = Form(None)):
    if not _is_authenticated(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from services import stats
    cfg = _score_cfg_from_form(dist_exp, speed_div, hours_div, speed_on, hours_on)
    return JSONResponse(stats.champions(db, cfg))   # computed live, NOT saved


@admin_router.post("/score/save")
def score_save(request: Request, db: Session = Depends(get_db),
               dist_exp: str = Form("1"), speed_div: str = Form("100"), hours_div: str = Form("10"),
               speed_on: str = Form(None), hours_on: str = Form(None)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    cfg = _score_cfg_from_form(dist_exp, speed_div, hours_div, speed_on, hours_on)
    settings.set_score_config(db, cfg["dist_exp"], cfg["speed_on"], cfg["speed_div"],
                              cfg["hours_on"], cfg["hours_div"])
    audit.log("score_save", f"exp={cfg['dist_exp']:g} speed={cfg['speed_div']:g}/{cfg['speed_on']} "
                            f"hours={cfg['hours_div']:g}/{cfg['hours_on']}")
    return RedirectResponse("/admin/appearance?msg=" + quote("EUC Planet Score saved, live now"),
                            status_code=303)


_SCORE_JS = """
<script>
(function(){
  var pv=document.getElementById('scoreprev'), out=document.getElementById('scoreout'), form=document.getElementById('scoreform');
  if(!pv||!form)return;
  function cell(label,c){return c?('<tr><td>'+label+'</td><td>'+(c.name||c.store_id||'?')+'</td><td><b>'+c.score+'</b></td><td>'+c.km+' km</td><td>'+c.hours+' h</td><td>'+c.top_speed+' km/h</td></tr>'):('<tr><td>'+label+'</td><td colspan=5 class=mut>no rides in window</td></tr>');}
  pv.onclick=function(){
    out.innerHTML='<span class=mut>computing preview…</span>';
    fetch('/admin/score/preview',{method:'POST',body:new URLSearchParams(new FormData(form)),credentials:'same-origin'})
      .then(function(r){return r.ok?r.json():Promise.reject();})
      .then(function(d){
        out.innerHTML='<p class=hint style="margin:0 0 6px"><code>'+d.formula+'</code></p>'
          +'<table><tr><th>Window</th><th>Champion</th><th>Score</th><th>Dist</th><th>Hours</th><th>Top</th></tr>'
          +cell('Day',d.day)+cell('Week',d.week)+cell('Month',d.month)+'</table>'
          +'<p class=hint style="margin:6px 0 0">Preview only — not saved.</p>';
      }).catch(function(){out.innerHTML='<span class=mut>preview failed</span>';});
  };
  // preset formulas: fill the form then auto-preview (Save still required to apply)
  var PRE={classic:{dist_exp:1,speed_div:100,hours_div:10,speed_on:true,hours_on:true},
           spicy:{dist_exp:1.3,speed_div:50,hours_div:5,speed_on:true,hours_on:true}};
  form.querySelectorAll('[data-preset]').forEach(function(b){b.onclick=function(){
    var p=PRE[b.dataset.preset]; if(!p)return;
    form.dist_exp.value=p.dist_exp; form.speed_div.value=p.speed_div; form.hours_div.value=p.hours_div;
    form.speed_on.checked=p.speed_on; form.hours_on.checked=p.hours_on;
    pv.click();
  };});
})();
</script>"""


def _score_card(db: Session) -> str:
    c = settings.get_score_config(db)
    ck = lambda v: " checked" if v else ""
    nin = "background:#0b1124;border:1px solid #26345e;color:#e9eefb;padding:7px 9px;border-radius:8px;width:90px"
    return f"""
    <div class=card>
      <h2>EUC Planet Score <span class=mut>· the champions formula</span></h2>
      <p class=hint>How the day / week / month champions are ranked. Distance is the base raised to the
      exponent (&gt;1 rewards big rides harder); each factor adds a boost where a <b>lower divisor = spicier</b>.
      Hit Preview to see who would win before you apply.</p>
      <form method=post action="/admin/score/save" id=scoreform>
        <div style="display:flex;gap:18px;flex-wrap:wrap;align-items:flex-end">
          <label>Distance exponent<br><input type=number step=any min=0.1 max=3 name=dist_exp value="{c['dist_exp']:g}" style="{nin}"></label>
          <label>Top-speed divisor<br><input type=number step=any min=1 name=speed_div value="{c['speed_div']:g}" style="{nin}"></label>
          <label>Hours divisor<br><input type=number step=any min=0.1 name=hours_div value="{c['hours_div']:g}" style="{nin}"></label>
        </div>
        <div style="margin-top:12px">
          <label class=toggle style="display:inline-flex;margin-right:18px"><input type=checkbox name=speed_on value=1{ck(c['speed_on'])}> include top speed</label>
          <label class=toggle style="display:inline-flex"><input type=checkbox name=hours_on value=1{ck(c['hours_on'])}> include hours (real ride time)</label>
        </div>
        <div style="margin-top:12px"><span class=hint style="margin-right:8px">Presets:</span>
          <button type=button class="ghost mini" data-preset="classic">Classic</button>
          <button type=button class="ghost mini" data-preset="spicy" style="margin-left:6px">Spicy</button>
        </div>
        <div class=qa style="margin-top:14px">
          <button type=button class=ghost id=scoreprev>{_IC['search']} Preview (no save)</button>
          <button>{_IC['check']} Save formula</button>
        </div>
      </form>
      <div id=scoreout style="margin-top:12px"></div>
    </div>
    {_SCORE_JS}"""


# --- metric audit: 3-agent adversarial review of how each metric is calculated ---
_AUDIT_LEVELS = {0: ("Solid", "#13a05a"), 1: ("Minor", "#2ea8ff"),
                 2: ("Caution", "#f59e0b"), 3: ("High risk", "#ff5b6e")}


def _audit_html() -> str:
    import importlib
    from web import metric_audit_data
    importlib.reload(metric_audit_data)                 # pick up regenerated data without a restart
    data = metric_audit_data.AUDIT or {}
    metrics = sorted(data.get("metrics", []), key=lambda m: (-int(m.get("level", 0)), m.get("name", "")))
    gen = data.get("generated")
    if not metrics:
        body = ('<div class=card><p class=hint>No audit yet. Run the <code>metric-audit</code> workflow '
                'to generate the review.</p></div>')
    else:
        cards = []
        for m in metrics:
            lvl = int(m.get("level", 0))
            lbl, col = _AUDIT_LEVELS.get(lvl, _AUDIT_LEVELS[0])
            issues = "".join(f"<li>{html.escape(str(i))}</li>" for i in (m.get("issues") or []))
            fixes = "".join(f"<li>{html.escape(str(f))}</li>" for f in (m.get("fixes") or []) if f)
            how = html.escape(str((m.get("hows") or [""])[0]))
            cards.append(f"""
            <div class=card style="border-left:4px solid {col}">
              <h2 style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">{html.escape(str(m.get('name', m.get('id', ''))))}
                <span style="background:{col}22;color:{col};border:1px solid {col}66;border-radius:20px;padding:2px 11px;font-size:12px;font-weight:600">{lbl}</span>
                <span class=mut style="font-size:12px;font-weight:400">cheat {m.get('cheat', 0)}/3 · wrong {m.get('wrong', 0)}/3 · {m.get('reviews', 0)} reviews</span></h2>
              <p class=hint style="margin:2px 0 8px">{how}</p>
              {('<p class=hint style="margin:0 0 3px"><b>Issues raised</b></p><ul style="margin:0 0 8px;padding-left:18px">' + issues + '</ul>') if issues else ''}
              {('<p class=hint style="margin:0 0 3px"><b>Suggested fixes</b></p><ul style="margin:0;padding-left:18px">' + fixes + '</ul>') if fixes else ''}
            </div>""")
        body = "".join(cards)
    cnt = {}
    for m in metrics:
        cnt[int(m.get("level", 0))] = cnt.get(int(m.get("level", 0)), 0) + 1
    summ = " &nbsp;·&nbsp; ".join(
        f'<span style="color:{_AUDIT_LEVELS[l][1]}">{cnt.get(l, 0)} {_AUDIT_LEVELS[l][0].lower()}</span>'
        for l in (3, 2, 1, 0))
    inner = f"""
    <h1>Metric audit</h1>
    <p class=sub>Independent 3-agent adversarial review of how each metric is calculated and how it could be
    cheated or be misleading. Worst risk first.{(' Generated ' + html.escape(str(gen)) + '.') if gen else ''}</p>
    <div class=card><div style="font-size:13px">{summ}</div></div>
    {body}"""
    return _admin_shell(inner, active="/admin/audit")


@admin_router.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request):
    if not _is_authenticated(request):
        return HTMLResponse(_login_html())
    return HTMLResponse(_audit_html())


# --- appearance: everything visitors see (metrics, heatmap, look, banner) ---

def _appearance_html(db: Session, msg: str = "") -> str:
    b = settings.get_behaviour(db)
    tm = settings.get_test_mode()
    banner = f'<div class="flash ok">{html.escape(msg)}</div>' if msg else ""
    sel = "background:#0b1124;border:1px solid #26345e;color:#e9eefb;padding:8px 10px;border-radius:9px"
    styles = "".join(
        f'<option value="{s}"{" selected" if b["map_style"] == s else ""}>{s}</option>'
        for s in settings.MAP_STYLES)
    intensity_opts = "".join(
        f'<option value="{i}"{" selected" if b["glitch_intensity"] == i else ""}>{lbl}</option>'
        for i, lbl in ((1, "1, subtle"), (2, "2, light"), (3, "3, medium"),
                       (4, "4, strong"), (5, "5, heavy")))
    ck = lambda v: " checked" if v else ""
    inner = f"""
    {banner}
    <h1>Public site</h1>
    <p class=sub>Everything visitors see, which metrics show, the activity heatmap, the look of the map,
    the intro and the site banner. Changes apply on the next page load.</p>
    {_metrics_section(db)}
    {_heatmap_card(db)}
    {_score_card(db)}
    <form method=post action="/admin/settings/save">
      <div class=card>
        <h2>Site banner</h2>
        <p class=hint>Shows a big red banner across the whole public site, use it to flag a testing / in-development phase. Site-wide, independent of the active dataset.</p>
        <label class=toggle style="display:inline-flex"><input type=checkbox name=test_enabled value=1{ck(tm['enabled'])}> Show the red banner on the site</label>
        <p class=hint style="margin:12px 0 4px">Banner text (anything you like, e.g. TEST DATA, BETA, SHAKEDOWN):</p>
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
      <button>{_IC['check']} Save look &amp; banner</button>
    </form>
    """
    return _ds_page(inner, "/admin/appearance")


@admin_router.get("/appearance", response_class=HTMLResponse)
def appearance_page(request: Request, db: Session = Depends(get_db), msg: str = ""):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    return HTMLResponse(_appearance_html(db, msg))


@admin_router.get("/settings")           # back-compat: old bookmark -> Appearance
def settings_redirect():
    return RedirectResponse("/admin/appearance", status_code=307)


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
    audit.log("settings_save", f"banner={'on' if test_enabled else 'off'} map={map_style}")
    return RedirectResponse("/admin/appearance?msg=" + quote("appearance saved, live on next page load"),
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


def _sandbox_card() -> str:
    """QA sandbox toggle + the magic store_id table to hand to the testing agent."""
    ck = " checked" if settings.sandbox_enabled() else ""
    rows = "".join(f'<tr><td><code>{sid}</code></td><td>{html.escape(desc)}</td></tr>'
                   for sid, _st, _pl, desc in sandbox.CASES)
    return f"""
    <div class=card>
      <h2>Sandbox test responses <span class=mut>· for Android QA</span></h2>
      <p class=hint>When ON, set a rider's <code>store_id</code> to one of these magic values to get that exact
      response from <code>POST /api/v1/riders</code> and <code>POST /api/v1/trips</code>, like Stripe test cards.
      They never collide with real riders (those are UUIDs). <b>Turn OFF for production.</b> Copy this table for the testing agent:</p>
      <form method=post action="/admin/sandbox">
        <label class=toggle style="display:inline-flex"><input type=checkbox name=sandbox_enabled value=1{ck}> Enable simulated users</label>
        <table style="margin-top:12px"><tr><th>store_id</th><th>response</th></tr>{rows}</table>
        <button style="margin-top:10px">{_IC['check']} Save sandbox</button>
      </form>
    </div>"""


def _audit_card() -> str:
    """Inline tail of the admin audit log (flat file, survives dataset switches)."""
    lines = audit.tail(400)
    body = html.escape("\n".join(lines)) if lines else "no admin actions logged yet"
    return f"""
    <div class=card>
      <h2>Audit log</h2>
      <p class=hint>Append-only record of admin actions, read from <code>data/audit.log</code>, a flat file,
      not stored in any dataset, so it survives dataset switches. Newest first; last 400 lines.</p>
      <pre class=j style="max-height:420px">{body}</pre>
    </div>"""


def _health_card() -> str:
    """Inline tail of the ops log: one line per ingest + periodic health snapshots."""
    from services import health
    lines = health.tail(300)
    body = html.escape("\n".join(lines)) if lines else "no activity logged yet"
    return f"""
    <div class=card>
      <h2>Activity &amp; health log</h2>
      <p class=hint>Live feed from <code>data/health.log</code>: one <code>ingest</code> line per upload
      (outcome · rider · trip · distance · size · time · any flag reasons) plus a periodic
      <code>health</code> snapshot (riders / trips / km / mem% / disk% / load). Flat file, bounded,
      survives dataset switches. Newest first; last 300 lines.</p>
      <pre class=j style="max-height:420px">{body}</pre>
    </div>"""


def _telegram_card() -> str:
    from services import telegram
    cfg = telegram.get_config()
    ck = lambda b: " checked" if b else ""
    tok = "set ✓" if cfg.get("token") else "not set"
    esc = lambda v: html.escape(str(v))
    return f"""
    <div class=card>
      <h2>Telegram bot</h2>
      <p class=hint>Posts to your Telegram group/topic when a new rider joins, when a rider logs their
      first ride, and once a day as a summary. The token is stored server-side in
      <code>data/telegram.json</code> (gitignored, never in the dataset or its exports). Leave the token
      blank to keep the current one. <b>Send test</b> works even while disabled.</p>
      <form method=post action="/admin/telegram">
        <p><label><input type=checkbox name=enabled{ck(cfg['enabled'])}> <b>Enabled</b>, master switch (off = nothing auto-posts)</label></p>
        <div class=calgrid>
          <label class=thr>Bot token <span class=mut>· currently {tok}</span>
            <input type=password name=token placeholder="leave blank to keep" autocomplete=off></label>
          <label class=thr>Chat ID
            <input name=chat_id value="{esc(cfg['chat_id'])}" placeholder="-100..."></label>
          <label class=thr>Topic / thread ID <span class=mut>· optional (forum topics)</span>
            <input name=thread_id value="{esc(cfg['thread_id'])}" placeholder="(none)"></label>
          <label class=thr>Link URL
            <input name=link_url value="{esc(cfg['link_url'])}"></label>
        </div>
        <p style="margin-top:10px">Events: &nbsp;
          <label><input type=checkbox name=new_rider{ck(cfg['new_rider'])}> new rider</label> &nbsp;
          <label><input type=checkbox name=first_ride{ck(cfg['first_ride'])}> first ride</label> &nbsp;
          <label><input type=checkbox name=summary_enabled{ck(cfg['summary_enabled'])}> daily summary</label>
        </p>
        <p style="margin-top:8px"><b>First place takeover</b>, announce when #1 changes: &nbsp;
          <label><input type=checkbox name=tk_rider{ck(cfg['tk_rider'])}> rider</label> &nbsp;
          <label><input type=checkbox name=tk_country{ck(cfg['tk_country'])}> country</label> &nbsp;
          <label><input type=checkbox name=tk_wheel{ck(cfg['tk_wheel'])}> wheel</label> &nbsp;
          <label><input type=checkbox name=tk_brand{ck(cfg['tk_brand'])}> brand</label>
        </p>
        <p class=hint style="margin:-4px 0 0">Rider takeovers fire only for <b>visible</b> leaderboards; group (country/wheel/brand) takeovers are by total distance. Only a different #1 fires.</p>
        <div class=calgrid>
          <label class=thr>Daily summary time <span class=mut>· HH:MM</span>
            <input name=summary_time value="{esc(cfg['summary_time'])}" placeholder="08:00"></label>
          <label class=thr>Timezone
            <input name=summary_tz value="{esc(cfg['summary_tz'])}" placeholder="Europe/Oslo"></label>
        </div>
        <button style="margin-top:12px">{_IC['check']} Save Telegram settings</button>
      </form>
      <form method=post action="/admin/telegram/test" style="margin-top:10px">
        <button class=ghost>Send test message</button>
        <span class=hint>Posts a test line to the configured chat/topic right now.</span>
      </form>
    </div>"""


def _retention_card(db: Session) -> str:
    r = settings.get_retention(db)
    return f"""
    <div class=card>
      <h2>Data retention</h2>
      <p class=hint>Only the original uploaded files (raw blobs) are ever evicted, trip summaries, GPS tracks,
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


def _system_html(db: Session, msg: str = "") -> str:
    banner = f'<div class="flash ok">{html.escape(msg)}</div>' if msg else ""
    inner = f"""
    {banner}
    <h1>System</h1>
    <p class=sub>Server resources, the logs (ingest/health + admin audit), and the QA sandbox.</p>
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
    {_health_card()}
    {_audit_card()}
    {_sandbox_card()}"""
    return _ds_page(inner, "/admin/system")


def _telegram_html(msg: str = "") -> str:
    banner = f'<div class="flash ok">{html.escape(msg)}</div>' if msg else ""
    inner = f"""
    {banner}
    <h1>Telegram</h1>
    <p class=sub>Announce new riders, first rides, leaderboard takeovers and a daily summary to your Telegram group.</p>
    {_telegram_card()}"""
    return _ds_page(inner, "/admin/telegram")


def _heatmap_card(db: Session) -> str:
    hm = settings.get_heatmap(db)
    rsel = lambda v: " selected" if hm["route_mode"] == v else ""
    return f"""
    <style>
    .hmcard .thr{{position:relative}}
    .hmcard .thr small{{display:block;margin-top:5px;font-size:10.5px;color:#8aa0c8;font-weight:400;line-height:1.4;letter-spacing:.2px}}
    .hmcard .calc{{color:#5fd0a0;font-size:10.5px}}
    </style>
    <div class="card hmcard">
      <h2>Heatmap</h2>
      <p class=hint>How rides are drawn on the map. The mental model: each ride is snapped onto grid
      <b>squares</b>; a glow's <b>size grows as you zoom in</b>, and its <b>brightness grows with how many
      different riders</b> used that square. Per-dataset. <b>Cell size</b> &amp; <b>route mode</b> are baked
      into the data, after changing them hit <a href="/admin/ingest">Rebuild stats</a>. Everything else is
      live on the next page load.</p>
      <form method=post action="/admin/system/heatmap">
        <h2 style="font-size:12.5px;margin-top:6px">Grid &amp; privacy <span class=mut>· first two need a Rebuild</span></h2>
        <div class=calgrid>
          <label class=thr>Cell size (degrees)
            <input type=number step=0.005 min=0.005 max=5 name=cell_size data-k=hm_cell value="{hm['cell_size']}">
            <span class=calc data-for=hm_cell></span>
            <small>Grid resolution, width of each square. Smaller = finer detail but more squares. Needs a Rebuild.</small></label>
          <label class=thr>Route mode
            <select name=route_mode style="background:#0b1124;border:1px solid #26345e;color:#e9eefb;padding:7px;border-radius:8px">
              <option value=route{rsel('route')}>whole route (corridors light up)</option>
              <option value=start{rsel('start')}>start point only</option>
            </select>
            <small>Whole route lights every square a ride crosses; start-only lights where rides began. Needs a Rebuild.</small></label>
          <label class=thr>Privacy floor (riders/square)
            <input type=number step=1 min=1 max=1000 name=floor data-k=hm_floor value="{hm['floor']}">
            <span class=calc data-for=hm_floor></span>
            <small>Hide a square until this many <b>different</b> riders have used it. 2+ protects a lone rider's area; 1 shows every square.</small></label>
        </div>
        <h2 style="font-size:12.5px;margin-top:14px">Look <span class=mut>· live, no rebuild</span></h2>
        <div class=calgrid>
          <label class=thr>Glow size (base px)
            <input type=number step=1 min=4 max=400 name=radius value="{hm['radius']}">
            <small>Size of each glow at city zoom. It expands automatically as you zoom in. Bigger = fatter blobs.</small></label>
          <label class=thr>Zoom growth
            <input type=number step=0.05 min=0.2 max=2 name=zoom_growth value="{hm['zoom_growth']}">
            <small>How fast the glow expands as you zoom in. 1.0 = doubles each zoom level (sticks to the ground); lower = grows slower; higher = explodes faster.</small></label>
          <label class=thr>Brightness
            <input type=number step=0.1 min=0.1 max=10 name=intensity value="{hm['intensity']}">
            <small>Overall glow strength. Higher = hotter / whiter everywhere.</small></label>
          <label class=thr>Lone-rider brightness (0–1)
            <input type=number step=0.05 min=0 max=1 name=glow_floor value="{hm['glow_floor']}">
            <small>How visible a square with just 1 rider looks. Lower = quiet areas fade out; higher = even a single rider glows. Busy squares always outshine quiet ones.</small></label>
          <label class=thr>Opacity (0–1)
            <input type=number step=0.01 min=0 max=1 name=opacity value="{hm['opacity']}">
            <small>See-through-ness of the whole heat layer. 0 = invisible, 1 = solid (hides more of the map underneath).</small></label>
        </div>
        <button style="margin-top:12px">{_IC['check']} Save heatmap</button>
      </form>
    </div>
    <script>
    (function(){{
      var T={{hm_cell:function(v){{return '→ '+(v*111).toFixed(1)+' km N–S · ~'+(v*111*0.35).toFixed(1)+' km E–W at 70°N';}},
              hm_floor:function(v){{return '→ a square appears only once '+v+' different riders have ridden through it';}}}};
      function u(i){{var f=T[i.dataset.k];if(!f)return;var s=document.querySelector('.calc[data-for="'+i.dataset.k+'"]');if(s){{var x=parseFloat(i.value);s.textContent=isFinite(x)?f(x):'';}}}}
      document.querySelectorAll('input[data-k^="hm_"]').forEach(function(i){{u(i);i.addEventListener('input',function(){{u(i);}});}});
    }})();
    </script>"""


@admin_router.get("/system", response_class=HTMLResponse)
def system_page(request: Request, db: Session = Depends(get_db), msg: str = ""):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    return HTMLResponse(_system_html(db, msg))


@admin_router.get("/activity")
def activity_page():
    # tab retired: heatmap moved to Public site, logs to System
    return RedirectResponse("/admin/appearance", status_code=307)


@admin_router.get("/telegram", response_class=HTMLResponse)
def telegram_page(request: Request, msg: str = ""):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    return HTMLResponse(_telegram_html(msg))


@admin_router.get("/system/resources", response_class=HTMLResponse)
def system_resources(request: Request):
    """Just the resource bars, polled by the System page for live autorefresh."""
    if not _is_authenticated(request):
        return HTMLResponse("", status_code=401)
    return HTMLResponse(_resources_html())


@admin_router.get("/audit")              # back-compat: audit folded into System
def audit_redirect():
    return RedirectResponse("/admin/system", status_code=307)


@admin_router.post("/system/save")
def system_save(request: Request, db: Session = Depends(get_db),
                ret_days: int = Form(30), ret_floor_gb: float = Form(10.0),
                ret_interval_s: int = Form(3600)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    settings.set_retention(db, ret_days, ret_floor_gb, ret_interval_s)
    audit.log("retention_save", f"days={ret_days} floor_gb={ret_floor_gb} interval_s={ret_interval_s}")
    return RedirectResponse("/admin/datasets?msg=" + quote("retention saved"), status_code=303)


@admin_router.post("/system/heatmap")
def heatmap_save(request: Request, db: Session = Depends(get_db),
                 cell_size: float = Form(0.025), route_mode: str = Form("route"),
                 floor: int = Form(2), radius: int = Form(60),
                 zoom_growth: float = Form(1.0), intensity: float = Form(1.0),
                 glow_floor: float = Form(0.45), opacity: float = Form(0.62)):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    before = settings.get_heatmap(db)
    settings.set_heatmap(db, cell_size, route_mode, floor, radius, intensity, opacity,
                         zoom_growth=zoom_growth, glow_floor=glow_floor)
    after = settings.get_heatmap(db)
    audit.log("heatmap_save", f"cell={after['cell_size']} mode={after['route_mode']} floor={after['floor']}")
    # changing the baked knobs needs a rebuild to take effect
    needs_rebuild = (before["cell_size"] != after["cell_size"] or before["route_mode"] != after["route_mode"])
    note = ("heatmap saved, cell size / route mode changed: hit Rebuild stats to re-bake the cells"
            if needs_rebuild else "heatmap saved, live now")
    return RedirectResponse("/admin/appearance?msg=" + quote(note), status_code=303)


@admin_router.post("/sandbox")
def sandbox_save(request: Request, db: Session = Depends(get_db),
                 sandbox_enabled: str = Form("")):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    settings.set_sandbox(bool(sandbox_enabled))
    audit.log("sandbox_save", f"sandbox={'on' if sandbox_enabled else 'off'}")
    state = "enabled" if sandbox_enabled else "disabled"
    return RedirectResponse("/admin/system?msg=" + quote(f"sandbox {state}"), status_code=303)


@admin_router.post("/telegram")
def telegram_save(request: Request,
                  enabled: str = Form(""), token: str = Form(""), chat_id: str = Form(""),
                  thread_id: str = Form(""), link_url: str = Form("https://eucstats.ried.no"),
                  new_rider: str = Form(""), first_ride: str = Form(""),
                  tk_rider: str = Form(""), tk_country: str = Form(""),
                  tk_wheel: str = Form(""), tk_brand: str = Form(""),
                  summary_enabled: str = Form(""), summary_time: str = Form("08:00"),
                  summary_tz: str = Form("Europe/Oslo")):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    from services import telegram
    fields = dict(enabled=bool(enabled), chat_id=chat_id.strip(), thread_id=thread_id.strip(),
                  link_url=(link_url.strip() or "https://eucstats.ried.no"),
                  new_rider=bool(new_rider), first_ride=bool(first_ride),
                  tk_rider=bool(tk_rider), tk_country=bool(tk_country),
                  tk_wheel=bool(tk_wheel), tk_brand=bool(tk_brand),
                  summary_enabled=bool(summary_enabled),
                  summary_time=(summary_time.strip() or "08:00"),
                  summary_tz=(summary_tz.strip() or "Europe/Oslo"))
    if token.strip():
        fields["token"] = token.strip()          # only overwrite when a new token is supplied
    telegram.update_config(**fields)
    audit.log("telegram_save", f"enabled={bool(enabled)} chat={chat_id} thread={thread_id}")  # never log the token
    return RedirectResponse("/admin/telegram?msg=" + quote("Telegram settings saved"), status_code=303)


@admin_router.post("/telegram/test")
def telegram_test(request: Request):
    if not _is_authenticated(request):
        return RedirectResponse("/admin", status_code=303)
    from services import telegram
    ok, detail = telegram.send_message("✅ EUC Stats, Telegram test message. The bot is wired up correctly.")
    audit.log("telegram_test", "ok" if ok else f"fail: {detail}")
    msg = "Telegram test sent ✓" if ok else f"Telegram test failed: {detail}"
    return RedirectResponse("/admin/telegram?msg=" + quote(msg), status_code=303)
