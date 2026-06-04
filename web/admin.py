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
    <p><a href="/admin/datasets">→ Datasets &amp; backups</a></p>
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


# --- dataset & snapshot manager ---

_DS_STYLE = """<style>
body{font-family:system-ui,-apple-system,sans-serif;max-width:1000px;margin:2rem auto;padding:0 1rem;color:#1b2030}
h1{margin:.2rem 0 1rem}h2{font-size:1.05rem;margin:.2rem 0 .6rem}
a{color:#1d6fe0}
.card{border:1px solid #d8deea;border-radius:10px;padding:14px 16px;margin:0 0 16px;background:#fafbff}
.inline{display:inline-flex;gap:6px;flex-wrap:wrap;align-items:center;margin:6px 12px 6px 0}
input,button{font:inherit;padding:6px 9px;border-radius:7px;border:1px solid #c3cad8}
button{background:#1d6fe0;color:#fff;border-color:#1d6fe0;cursor:pointer}
button.danger{background:#c23b3b;border-color:#c23b3b}button.go{background:#13864a;border-color:#13864a}
a.btn{display:inline-block;padding:5px 8px;border:1px solid #c3cad8;border-radius:7px;text-decoration:none;color:#1b2030}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border-bottom:1px solid #e7ebf3;padding:7px 8px;text-align:left;vertical-align:top}
tr.active{background:#eaf4ff}
.acts form{display:inline-flex;gap:4px;margin:2px 0}.acts input{width:92px}
.b{font-size:11px;font-weight:700;padding:2px 7px;border-radius:20px}
.b.test{background:#ffe2e2;color:#b11}.b.live{background:#dcf5e6;color:#0a7a3e}
.mut{color:#8a93b2}
.flash{padding:9px 12px;border-radius:8px;margin:0 0 12px}
.flash.ok{background:#e6f6ec;color:#0a7a3e}.flash.err{background:#fdeaea;color:#b11}
</style>"""

def _ds_page(inner: str) -> str:
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>eucstats datasets</title>" + _DS_STYLE + "</head><body>" + inner + "</body></html>")


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
    <p><a href="/admin">← back to admin</a></p>
    {banner}
    <h1>Datasets &amp; backups</h1>
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
    current dataset, then restarts the service (~a few seconds of downtime).</p>
    """
    return _ds_page(inner)


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
